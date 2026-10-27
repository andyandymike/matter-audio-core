"""Bounded, advisory PCM diagnostics; never a quality score or audio transform."""

import math
import struct

from .contracts import object_schema, validate
from .errors import AudioError
from .execution import checkpoint
from .fades import identity_mapping

PROFILE = "pcm16-quality-advisory/v1"
MAX_SCAN_FRAMES = 33554432
QUALITY_SCHEMA = object_schema({
    "profile": {"type": "string", "enum": ["generic", "music", "speech"]},
    "start_frame": {"type": "integer", "minimum": 0, "maximum": MAX_SCAN_FRAMES},
    "end_frame": {"type": "integer", "minimum": 1, "maximum": MAX_SCAN_FRAMES},
    "window_frames": {"type": "integer", "minimum": 1, "maximum": 192000},
    "max_scan_frames": {"type": "integer", "minimum": 1, "maximum": MAX_SCAN_FRAMES},
    "max_warnings": {"type": "integer", "minimum": 1, "maximum": 128},
}, [])

_POLICIES = {
    "generic": {"silence_ms": 1000, "level_jump_db": 12},
    "music": {"silence_ms": 2000, "level_jump_db": 18},
    "speech": {"silence_ms": 500, "level_jump_db": 9},
}
_NEAR_FULL_SCALE = 32760
_SILENCE_PEAK = 32
_LEVEL_FLOOR = 128


def resolve(parameters, pcm):
    """Validate the public parameters and freeze defaults, without scanning PCM."""
    validate(parameters, QUALITY_SCHEMA)
    resolved = {"profile": "generic", "start_frame": 0, "end_frame": pcm.frames,
                "window_frames": max(1, pcm.sample_rate // 20),
                "max_scan_frames": 4000000, "max_warnings": 64, **parameters}
    if not 0 <= resolved["start_frame"] < resolved["end_frame"] <= pcm.frames:
        raise AudioError("invalid_range", "Quality scan range must fit the source and be nonempty")
    return resolved


def scan(pcm, parameters):
    """Scan a bounded prefix of the requested half-open source-frame interval.

    Silence requires every channel to be quiet. Window RMS combines squared
    channel samples, so opposite-phase stereo cannot cancel into silence.
    Warning limits bound report memory, not scanning: omitted warnings are counted.
    """
    parameters = resolve(parameters, pcm)
    checkpoint(force=True)
    policy = _POLICIES[parameters["profile"]]
    start, requested_end = parameters["start_frame"], parameters["end_frame"]
    end = min(requested_end, start + parameters["max_scan_frames"])
    silence_min = (pcm.sample_rate * policy["silence_ms"] + 999) // 1000
    warnings = []
    counts = {"clipping_risk": 0, "sustained_silence": 0, "relative_level_jump": 0}
    silence_start = clip_start = None
    clip_peak = clip_samples = 0
    clip_channels = set()
    previous = None
    windows = comparisons = 0

    def emit(kind, left, right, evidence, message, severity="review"):
        counts[kind] += 1
        if len(warnings) < parameters["max_warnings"]:
            warnings.append({"kind": kind, "severity": severity,
                "start_frame": left, "end_frame": right,
                "touches_scan_boundary": left == start or right == end,
                "message": message, "evidence": evidence})

    def close_clip(right):
        nonlocal clip_start, clip_peak, clip_samples, clip_channels
        if clip_start is not None:
            emit("clipping_risk", clip_start, right,
                {"near_full_scale_sample_count": clip_samples, "peak_pcm16": clip_peak,
                 "channel_indices": sorted(clip_channels)},
                "Samples approach the PCM16 rails; inspect for clipping, which this does not prove.")
        clip_start, clip_peak, clip_samples, clip_channels = None, 0, 0, set()

    def close_silence(right):
        nonlocal silence_start
        if silence_start is not None and right - silence_start >= silence_min:
            music = parameters["profile"] == "music"
            emit("sustained_silence", silence_start, right,
                {"minimum_duration_frames": silence_min, "all_channels_peak_at_most_pcm16": _SILENCE_PEAK},
                "Sustained low level may be an intentional musical rest." if music else
                "Sustained low level may be an intentional pause; check against the intended content.",
                "note" if music else "review")
        silence_start = None

    # A view avoids decoding/copying the entire source outside the scan budget.
    payload = memoryview(pcm.payload)
    width = 2 * pcm.channels
    sample_format = "<" + "h" * pcm.channels
    for left in range(start, end, parameters["window_frames"]):
        right = min(end, left + parameters["window_frames"])
        squares = 0
        for offset, values in enumerate(struct.iter_unpack(sample_format, payload[left * width:right * width])):
            frame = left + offset
            if offset % 4096 == 0:
                checkpoint()
            peak = max(abs(value) for value in values)
            squares += sum(value * value for value in values)
            if peak <= _SILENCE_PEAK:
                if silence_start is None:
                    silence_start = frame
            else:
                close_silence(frame)
            if peak >= _NEAR_FULL_SCALE:
                if clip_start is None:
                    clip_start = frame
                clip_peak = max(clip_peak, peak)
                for channel, value in enumerate(values):
                    if abs(value) >= _NEAR_FULL_SCALE:
                        clip_samples += 1
                        clip_channels.add(channel)
            else:
                close_clip(frame)
        rms = math.sqrt(squares / ((right - left) * pcm.channels))
        if previous is not None and min(previous[2], rms) >= _LEVEL_FLOOR:
            comparisons += 1
            change_db = 20 * math.log10(rms / previous[2])
            if abs(change_db) >= policy["level_jump_db"]:
                emit("relative_level_jump", previous[0], right,
                    {"boundary_frame": left, "previous_start_frame": previous[0],
                     "previous_end_frame": previous[1], "current_start_frame": left,
                     "current_end_frame": right, "previous_rms_pcm16": previous[2],
                     "current_rms_pcm16": rms, "change_db": change_db},
                    "Adjacent window RMS levels differ; an onset, accent or edit may explain this.")
        previous = (left, right, rms)
        windows += 1
    close_clip(end)
    close_silence(end)
    checkpoint(force=True)
    detected = sum(counts.values())
    omitted = detected - len(warnings)
    reasons = []
    if end < requested_end:
        reasons.append("scan_frame_budget")
    if omitted:
        reasons.append("warning_limit")
    warnings.sort(key=lambda item: (item["start_frame"], item["end_frame"], item["kind"]))
    return {"schema": "matter-quality-report/v1", "profile": PROFILE,
        "content_profile": parameters["profile"], "advisory": True, "media": pcm.facts(),
        "parameters": parameters,
        "thresholds": {"near_full_scale_pcm16": _NEAR_FULL_SCALE,
            "silence_peak_pcm16": _SILENCE_PEAK, "silence_min_frames": silence_min,
            "level_jump_db": policy["level_jump_db"], "level_comparison_floor_rms_pcm16": _LEVEL_FLOOR},
        "requested_range": {"start_frame": start, "end_frame": requested_end},
        "scanned_range": {"start_frame": start, "end_frame": end},
        "scanned_frames": end - start, "unscanned_frames": requested_end - end,
        "windows_scanned": windows, "level_comparisons": comparisons,
        "warning_counts": counts, "warnings": warnings, "warnings_omitted": omitted,
        "truncated": bool(reasons), "reasons": reasons,
        "limitations": [
            "Advisory rules are not an audio-quality score, error verdict, or listening acceptance.",
            "Near-rail samples do not prove clipping; inter-sample peaks are not measured.",
            "Silence means every channel stays below a fixed peak threshold; rests and pauses may be intentional.",
            "Level changes compare adjacent window RMS, including a short final window; RMS is not LUFS.",
            "Scan edges may cut longer events; warnings describe only observed source frames.",
            "At the warning limit, the first completed detections are kept; counts include omitted detections.",
        ]}


def quality_operation():
    """Return the single-input analysis operation without creating audio outputs."""
    from .actions import Operation
    return Operation("quality/v1", QUALITY_SCHEMA, resolve,
        lambda parameters, pcm: (None, scan(pcm, parameters)), PROFILE,
        mapping=identity_mapping, writes=lambda parameters, pcm: [])
