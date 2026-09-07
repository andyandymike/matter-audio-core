"""Bounded PCM16 layering and window replacement; no implicit format conversion."""

from array import array

from .contracts import digest, object_schema
from .errors import AudioError
from .execution import checkpoint
from .fades import FRAME_COUNT
from .media import PCM, Q24, gain_multiplier, sample_bytes

MIX_PROFILE = "pcm16-layer-sum-q48/v1"
SPLICE_PROFILE = "pcm16-splice-linear-q24/v1"
DB = {"type": "number", "minimum": -60, "maximum": 24}
LAYER = object_schema({"input_index": {"type": "integer", "minimum": 1, "maximum": 15},
    "source_start_frame": FRAME_COUNT, "source_end_frame": FRAME_COUNT, "offset_frame": FRAME_COUNT,
    "db": DB, "fade_in_frames": FRAME_COUNT, "fade_out_frames": FRAME_COUNT},
    ["input_index", "source_start_frame", "source_end_frame", "offset_frame"])
MIX_SCHEMA = object_schema({"layers": {"type": "array", "minItems": 1, "maxItems": 15, "items": LAYER},
    "base_db": DB, "clip": {"enum": ["reject", "saturate"]}}, ["layers"])
SPLICE_SCHEMA = object_schema({"start_frame": FRAME_COUNT, "end_frame": FRAME_COUNT,
    "replacement_start_frame": FRAME_COUNT, "transition_frames": FRAME_COUNT}, ["start_frame", "end_frame"])


def compatible(pcms):
    if any((pcm.sample_rate, pcm.channels) != (pcms[0].sample_rate, pcms[0].channels) for pcm in pcms):
        raise AudioError("format_mismatch", "Inputs must have identical sample rate and channels; decode explicitly")


def rounded(product, scale):
    value = (abs(product) + scale // 2) // scale
    return -value if product < 0 else value


def ramp(index, count):
    return 0 if count == 1 else (Q24 * index + (count - 1) // 2) // (count - 1)


def merge_ranges(ranges):
    merged = []
    for region in sorted(ranges, key=lambda r: r["start_frame"]):
        if merged and region["start_frame"] <= merged[-1]["end_frame"]:
            merged[-1]["end_frame"] = max(merged[-1]["end_frame"], region["end_frame"])
        else:
            merged.append(dict(region))
    return merged


def observed_changes(before, after, allowed_write):
    compatible([before, after])
    if before.frames != after.frames:
        raise AudioError("invalid_mapping", "Equality report requires an unchanged timeline")
    allowed = merge_ranges(allowed_write)
    if any(not 0 <= r["start_frame"] < r["end_frame"] <= before.frames for r in allowed):
        raise AudioError("invalid_range", "Write bounds are outside the source")
    old, new = before.samples(), after.samples()
    count = frames = outside = outside_frames = region_index = range_count = 0
    ranges, run_start = [], None
    for frame in range(before.frames):
        if frame % 4096 == 0:
            checkpoint()
        while region_index < len(allowed) and frame >= allowed[region_index]["end_frame"]:
            region_index += 1
        inside = region_index < len(allowed) and frame >= allowed[region_index]["start_frame"]
        changed = sum(old[frame * before.channels + c] != new[frame * before.channels + c] for c in range(before.channels))
        count += changed
        frames += bool(changed)
        outside += changed if not inside else 0
        outside_frames += bool(changed) and not inside
        if changed and run_start is None:
            run_start = frame
        if run_start is not None and (not changed or frame == before.frames - 1):
            end = frame + 1 if changed else frame
            range_count += 1
            if len(ranges) < 64:
                ranges.append({"start_frame": run_start, "end_frame": end})
            run_start = None
    return {"method": "pcm16-equality/v1", "allowed_write": allowed,
        "changed_sample_count": count, "changed_frame_count": frames,
        "outside_changed_sample_count": outside, "outside_changed_frame_count": outside_frames,
        "changed_ranges": ranges, "changed_range_count": range_count, "ranges_truncated": range_count > len(ranges),
        "source_pcm_digest": digest(before.payload), "output_pcm_digest": digest(after.payload)}


def resolve_mix(parameters, pcms):
    compatible(pcms)
    if sorted(layer["input_index"] for layer in parameters["layers"]) != list(range(1, len(pcms))):
        raise AudioError("invalid_layers", "Each additional input must be used once; repeat an input reference to reuse a clip")
    layers = []
    for layer in parameters["layers"]:
        source = pcms[layer["input_index"]]
        start, end, offset = layer["source_start_frame"], layer["source_end_frame"], layer["offset_frame"]
        incoming, outgoing = layer.get("fade_in_frames", 0), layer.get("fade_out_frames", 0)
        if not 0 <= start < end <= source.frames or not 0 <= offset < offset + end - start <= pcms[0].frames:
            raise AudioError("invalid_range", "Layer source and destination ranges must fit their inputs")
        if incoming + outgoing > end - start:
            raise AudioError("fade_overlap", "Layer fades must fit without overlap")
        layers.append({**layer, "db": layer.get("db", 0), "gain_q24": gain_multiplier(layer.get("db", 0)),
                       "fade_in_frames": incoming, "fade_out_frames": outgoing})
    return {"layers": layers, "base_db": parameters.get("base_db", 0),
        "base_gain_q24": gain_multiplier(parameters.get("base_db", 0)), "clip": parameters.get("clip", "reject")}


def mix_writes(parameters, pcms):
    if parameters["base_gain_q24"] != Q24:
        return [{"start_frame": 0, "end_frame": pcms[0].frames}]
    return merge_ranges([{"start_frame": layer["offset_frame"],
        "end_frame": layer["offset_frame"] + layer["source_end_frame"] - layer["source_start_frame"]} for layer in parameters["layers"]])


def mix(parameters, pcms):
    compatible(pcms)
    base = pcms[0]
    inputs = [pcm.samples() for pcm in pcms]
    output, overflow = array("h"), 0
    for block in range(0, base.frames, 4096):
        checkpoint()
        end = min(base.frames, block + 4096)
        sums = [v * parameters["base_gain_q24"] * Q24 for v in inputs[0][block * base.channels:end * base.channels]]
        for layer in parameters["layers"]:
            offset = layer["offset_frame"]
            length = layer["source_end_frame"] - layer["source_start_frame"]
            incoming, outgoing = layer["fade_in_frames"], layer["fade_out_frames"]
            for frame in range(max(block, offset), min(end, offset + length)):
                index = frame - offset
                factor = ramp(index, incoming) if index < incoming else ramp(length - 1 - index, outgoing) if index >= length - outgoing else Q24
                for channel in range(base.channels):
                    sample = inputs[layer["input_index"]][(layer["source_start_frame"] + index) * base.channels + channel]
                    sums[(frame - block) * base.channels + channel] += sample * layer["gain_q24"] * factor
        for product in sums:
            value = rounded(product, Q24 * Q24)
            overflow += not -32768 <= value <= 32767
            output.append(max(-32768, min(32767, value)))
    if overflow and parameters["clip"] == "reject":
        raise AudioError("clipping_rejected", "Layer sum would overflow PCM16", details={"overflow_sample_count": overflow})
    pcm = PCM(sample_bytes(output), base.sample_rate, base.channels)
    return pcm, {"time_mapping": {"kind": "identity", "frame_count": base.frames}, "overflow_sample_count": overflow,
        "write_ranges": mix_writes(parameters, pcms), "observed_changes": observed_changes(base, pcm, mix_writes(parameters, pcms))}


def resolve_splice(parameters, pcms):
    compatible(pcms)
    start, end = parameters["start_frame"], parameters["end_frame"]
    replacement_start = parameters.get("replacement_start_frame", start)
    transition = parameters.get("transition_frames", 0)
    if not 0 <= start < end <= pcms[0].frames or not 0 <= replacement_start < replacement_start + end - start <= pcms[1].frames:
        raise AudioError("invalid_range", "Replacement and target windows must fit their inputs")
    if transition * 2 > end - start:
        raise AudioError("fade_overlap", "Transitions must fit inside the write window without overlap")
    return {"start_frame": start, "end_frame": end, "replacement_start_frame": replacement_start, "transition_frames": transition}


def splice(parameters, pcms):
    compatible(pcms)
    base, replacement = pcms
    start, end, source_start, transition = (parameters[k] for k in ("start_frame", "end_frame", "replacement_start_frame", "transition_frames"))
    old, new = base.samples(), replacement.samples()
    output = array("h", old)
    for frame in range(start, end):
        if (frame - start) % 4096 == 0:
            checkpoint()
        weight = ramp(frame - start, transition) if frame - start < transition else ramp(end - 1 - frame, transition) if end - frame <= transition else Q24
        for channel in range(base.channels):
            target = frame * base.channels + channel
            value = new[(source_start + frame - start) * base.channels + channel]
            output[target] = rounded(old[target] * (Q24 - weight) + value * weight, Q24)
    pcm = PCM(sample_bytes(output), base.sample_rate, base.channels)
    allowed = [{"start_frame": start, "end_frame": end}]
    transitions = ([{"start_frame": start, "end_frame": start + transition},
                    {"start_frame": end - transition, "end_frame": end}] if transition else [])
    boundaries = [{"frame": frame, "max_channel_jump_pcm16": max(abs(output[frame * base.channels + c] - output[(frame - 1) * base.channels + c]) for c in range(base.channels))}
                  for frame in (start, end) if 0 < frame < base.frames]
    return pcm, {"time_mapping": {"kind": "identity", "frame_count": base.frames},
        "write_ranges": allowed, "transition": transitions, "boundary_measurements": boundaries,
        "observed_changes": observed_changes(base, pcm, allowed)}


def composition_operations():
    from .actions import Operation
    mapping = lambda p, pcms: {"kind": "identity", "frame_count": pcms[0].frames}
    return [Operation("mix/v1", MIX_SCHEMA, resolve_mix, mix, MIX_PROFILE,
                      mapping=mapping, writes=mix_writes, input_count=(2, 16)),
            Operation("splice/v1", SPLICE_SCHEMA, resolve_splice, splice, SPLICE_PROFILE,
                      mapping=mapping, writes=lambda p, pcms: [{"start_frame": p["start_frame"], "end_frame": p["end_frame"]}],
                      input_count=(2, 2))]
