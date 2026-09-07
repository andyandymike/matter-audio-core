"""Level matching, overlap loops and a bounded event timeline for existing PCM."""

import math
from array import array

from .analysis import PROFILE as ANALYSIS_PROFILE, describe, dbfs
from .composition import DB, compatible, ramp, rounded
from .contracts import object_schema
from .errors import AudioError
from .execution import checkpoint
from .fades import FRAME_COUNT, identity_mapping
from .media import MAX_AUDIO_BYTES, PCM, Q24, gain, gain_multiplier, sample_bytes, trim

NORMALIZE_SCHEMA = object_schema({"target_rms_dbfs": {"type": "number", "minimum": -60, "maximum": -3},
    "peak_ceiling_dbfs": {"type": "number", "minimum": -12, "maximum": 0},
    "max_boost_db": {"type": "number", "minimum": 0, "maximum": 24}}, ["target_rms_dbfs"])
LOOP_SCHEMA = object_schema({"start_frame": FRAME_COUNT, "end_frame": FRAME_COUNT,
    "crossfade_frames": FRAME_COUNT, "curve": {"enum": ["linear", "equal_power"]},
    "clip": {"enum": ["reject", "saturate"]}}, ["start_frame", "end_frame", "crossfade_frames"])
KEY = {"type": "string", "pattern": "^[a-z][a-z0-9_-]{0,47}$"}
TRACK_SCHEMA = object_schema({"name": KEY, "db": DB, "fade_in_frames": FRAME_COUNT,
                             "fade_out_frames": FRAME_COUNT}, ["name"])
EVENT_SCHEMA = object_schema({"event_id": KEY, "input_index": {"type": "integer", "minimum": 0, "maximum": 15},
    "track": KEY, "source_start_frame": FRAME_COUNT, "source_end_frame": FRAME_COUNT,
    "offset_frame": FRAME_COUNT, "db": DB, "fade_in_frames": FRAME_COUNT, "fade_out_frames": FRAME_COUNT,
    "repeat": {"type": "integer", "minimum": 1, "maximum": 64}, "interval_frames": FRAME_COUNT},
    ["event_id", "input_index", "track", "source_start_frame", "source_end_frame", "offset_frame"])
SCENE_SCHEMA = object_schema({"duration_frames": FRAME_COUNT,
    "tracks": {"type": "array", "minItems": 1, "maxItems": 16, "items": TRACK_SCHEMA},
    "events": {"type": "array", "minItems": 1, "maxItems": 128, "items": EVENT_SCHEMA},
    "master_db": DB, "clip": {"enum": ["reject", "saturate"]}}, ["duration_frames", "tracks", "events"])


def resolve_normalize(parameters, pcm):
    ceiling = parameters.get("peak_ceiling_dbfs", -1)
    if parameters["target_rms_dbfs"] > ceiling:
        raise AudioError("invalid_level_target", "RMS target cannot exceed the peak ceiling")
    values = pcm.samples()
    # Use bounded blocks so managed cancellation remains responsive.
    squares = peak = 0
    for start in range(0, len(values), 8192):
        checkpoint()
        block = values[start:start + 8192]
        squares += sum(value * value for value in block)
        peak = max(peak, max(map(abs, block)))
    rms = math.sqrt(squares / len(values))
    target = 32768 * gain_multiplier(parameters["target_rms_dbfs"]) / Q24
    ceiling_pcm = min(32767, math.floor(32768 * gain_multiplier(ceiling) / Q24))
    desired = round(target / rms * Q24) if rms else Q24
    coefficient = min(desired, gain_multiplier(parameters.get("max_boost_db", 12)),
                      ceiling_pcm * Q24 // peak if peak else Q24)
    return {**parameters, "peak_ceiling_dbfs": ceiling, "max_boost_db": parameters.get("max_boost_db", 12),
        "gain_q24": coefficient, "applied_db": 20 * math.log10(coefficient / Q24) if coefficient else None,
        "source_rms_dbfs": dbfs(rms), "limited": coefficient < desired, "silent": not bool(peak)}


def normalize(parameters, pcm):
    output, clipped = gain(pcm, parameters["gain_q24"], "reject")
    return output, {"time_mapping": identity_mapping(parameters, pcm), "overflow_sample_count": clipped,
        "normalization": parameters, "analysis": describe(output)}


def resolve_loop(parameters, pcm):
    start, end, fade = (parameters[key] for key in ("start_frame", "end_frame", "crossfade_frames"))
    if not 0 <= start < end <= pcm.frames or fade * 2 > end - start or fade == 1:
        raise AudioError("invalid_loop", "Loop range must fit; crossfade is zero or at least two frames, at most half the range")
    return {**parameters, "curve": parameters.get("curve", "linear"), "clip": parameters.get("clip", "reject"),
        "output_frames": end - start - fade, "output_source_start_frame": start + fade}


def loop_mapping(parameters, pcm):
    return {"kind": "slice", "source_start_frame": parameters["output_source_start_frame"],
            "output_start_frame": 0, "frame_count": parameters["output_frames"]}


def loop_writes(parameters, pcm):
    return [{"start_frame": parameters["end_frame"] - parameters["crossfade_frames"],
             "end_frame": parameters["end_frame"]}] if parameters["crossfade_frames"] else []


def make_loop(parameters, pcm):
    start, end, fade = (parameters[key] for key in ("start_frame", "end_frame", "crossfade_frames"))
    source = pcm.samples()
    output = array("h", source[(start + fade) * pcm.channels:end * pcm.channels])
    overflow = 0
    for i in range(fade):
        if i % 4096 == 0:
            checkpoint()
        incoming = ramp(i, fade)
        outgoing = Q24 - incoming
        if parameters["curve"] == "equal_power":
            incoming = round(math.sin(math.pi / 2 * i / (fade - 1)) * Q24)
            outgoing = round(math.cos(math.pi / 2 * i / (fade - 1)) * Q24)
        for c in range(pcm.channels):
            value = rounded(source[(end - fade + i) * pcm.channels + c] * outgoing +
                            source[(start + i) * pcm.channels + c] * incoming, Q24)
            overflow += not -32768 <= value <= 32767
            output[(len(output) // pcm.channels - fade + i) * pcm.channels + c] = max(-32768, min(32767, value))
    if overflow and parameters["clip"] == "reject":
        raise AudioError("clipping_rejected", "Loop overlap would overflow PCM16", details={"overflow_sample_count": overflow})
    result = PCM(sample_bytes(output), pcm.sample_rate, pcm.channels)
    return result, {"time_mapping": loop_mapping(parameters, pcm), "write_ranges": loop_writes(parameters, pcm),
        "overflow_sample_count": overflow, "loop": {"begin_frame": 0, "end_frame": result.frames,
            "crossfade_frames": fade, "curve": parameters["curve"], "period_frames_removed": fade},
        "source_seam": describe(trim(pcm, start, end))["seam"], "output_analysis": describe(result),
        "listening_acceptance": "not_evaluated"}


def resolve_scene(parameters, pcms):
    compatible(pcms)
    duration = parameters["duration_frames"]
    if duration < 1 or duration * pcms[0].channels * 2 + 44 > MAX_AUDIO_BYTES:
        raise AudioError("output_limit", "Scene must contain audio and fit within 64 MiB")
    tracks = {}
    for track in parameters["tracks"]:
        if track["name"] in tracks:
            raise AudioError("duplicate_track", "Track names must be distinct")
        incoming, outgoing = track.get("fade_in_frames", 0), track.get("fade_out_frames", 0)
        if incoming + outgoing > duration:
            raise AudioError("fade_overlap", "Track fades overlap")
        tracks[track["name"]] = {**track, "db": track.get("db", 0), "fade_in_frames": incoming, "fade_out_frames": outgoing}
    events, ids, used, used_tracks = [], set(), set(), set()
    for event in parameters["events"]:
        index, start, end, offset = (event[k] for k in ("input_index", "source_start_frame", "source_end_frame", "offset_frame"))
        if event["event_id"] in ids or event["track"] not in tracks or index >= len(pcms):
            raise AudioError("invalid_event", "Events need distinct IDs and registered tracks/inputs")
        if not 0 <= start < end <= pcms[index].frames:
            raise AudioError("invalid_range", "Event source range must fit")
        length = end - start
        incoming, outgoing = event.get("fade_in_frames", 0), event.get("fade_out_frames", 0)
        interval, repeats = event.get("interval_frames", length), event.get("repeat", 1)
        if incoming + outgoing > length or interval < 1 or offset + (repeats - 1) * interval + length > duration:
            raise AudioError("invalid_range", "Event repetitions and fades must fit; overlap between repetitions is explicit")
        track = tracks[event["track"]]
        for repeat in range(repeats):
            events.append({**event, "repeat_index": repeat, "offset_frame": offset + repeat * interval,
                "fade_in_frames": incoming, "fade_out_frames": outgoing,
                "gain_q24": gain_multiplier(event.get("db", 0) + track["db"] + parameters.get("master_db", 0))})
        if len(events) > 1024:
            raise AudioError("event_limit", "At most 1024 expanded events")
        ids.add(event["event_id"])
        used.add(index)
        used_tracks.add(event["track"])
    if used != set(range(len(pcms))) or used_tracks != set(tracks):
        raise AudioError("unused_input", "Every input and track must be used by an event")
    return {"duration_frames": duration, "tracks": tracks, "events": events,
            "master_db": parameters.get("master_db", 0), "clip": parameters.get("clip", "reject")}


def envelope(index, length, incoming, outgoing):
    return ramp(index, incoming) if index < incoming else ramp(length - 1 - index, outgoing) if index >= length - outgoing else Q24


def render_scene(parameters, pcms):
    duration, channels = parameters["duration_frames"], pcms[0].channels
    inputs, output, overflow = [pcm.samples() for pcm in pcms], array("h"), 0
    for block in range(0, duration, 4096):
        checkpoint()
        stop = min(duration, block + 4096)
        sums = [0] * ((stop - block) * channels)
        for event in parameters["events"]:
            offset, length = event["offset_frame"], event["source_end_frame"] - event["source_start_frame"]
            track = parameters["tracks"][event["track"]]
            for frame in range(max(offset, block), min(offset + length, stop)):
                factor = (event["gain_q24"] * envelope(frame - offset, length, event["fade_in_frames"], event["fade_out_frames"]) *
                          envelope(frame, duration, track["fade_in_frames"], track["fade_out_frames"]))
                for c in range(channels):
                    sums[(frame - block) * channels + c] += inputs[event["input_index"]][(event["source_start_frame"] + frame - offset) * channels + c] * factor
        for product in sums:
            value = rounded(product, Q24 ** 3)
            overflow += not -32768 <= value <= 32767
            output.append(max(-32768, min(32767, value)))
    if overflow and parameters["clip"] == "reject":
        raise AudioError("clipping_rejected", "Scene mix would overflow PCM16", details={"overflow_sample_count": overflow})
    pcm = PCM(sample_bytes(output), pcms[0].sample_rate, channels)
    return pcm, {"time_mapping": {"kind": "arrangement", "frame_count": duration},
        "overflow_sample_count": overflow, "event_count": len(parameters["events"]),
        "tracks": list(parameters["tracks"]), "analysis": describe(pcm)}


def arrangement_operations():
    from .actions import Operation
    return [Operation("analyze/v1", object_schema({}), lambda p, pcm: {},
                lambda p, pcm: (None, describe(pcm)), ANALYSIS_PROFILE, mapping=identity_mapping, writes=lambda p, pcm: []),
        Operation("normalize/v1", NORMALIZE_SCHEMA, resolve_normalize, normalize, "pcm16-rms-peak-gain-q24/v1",
            mapping=identity_mapping, writes=lambda p, pcm: [] if p["gain_q24"] == Q24 else [{"start_frame": 0, "end_frame": pcm.frames}]),
        Operation("loop/v1", LOOP_SCHEMA, resolve_loop, make_loop, "pcm16-loop-overlap-q24/v1",
            mapping=loop_mapping, writes=loop_writes),
        Operation("scene/v1", SCENE_SCHEMA, resolve_scene, render_scene, "pcm16-event-sum-q72/v1", input_count=(1, 16))]
