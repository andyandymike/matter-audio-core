"""Versioned linear-amplitude fades with Q24 factors and PCM16 rounding."""

from array import array
from decimal import Decimal, ROUND_HALF_UP

from .contracts import object_schema
from .errors import AudioError
from .execution import checkpoint
from .media import PCM, Q24, sample_bytes

FADE_PROFILE = "pcm16-fade-linear-q24/v1"
FRAME_COUNT = {"type": "integer", "minimum": 0, "maximum": 230400000}
SECONDS = {"type": "number", "minimum": 0, "maximum": 86400}
FADE_SCHEMA = {"oneOf": [
    {**object_schema({"fade_in_frames": FRAME_COUNT, "fade_out_frames": FRAME_COUNT,
                      "curve": {"const": "linear"}}, []),
     "anyOf": [{"required": ["fade_in_frames"]}, {"required": ["fade_out_frames"]}]},
    {**object_schema({"fade_in_seconds": SECONDS, "fade_out_seconds": SECONDS,
                      "curve": {"const": "linear"}}, []),
     "anyOf": [{"required": ["fade_in_seconds"]}, {"required": ["fade_out_seconds"]}]},
]}


def seconds_to_frames(value, rate):
    return int((Decimal(str(value)) * rate).to_integral_value(rounding=ROUND_HALF_UP))


def resolve_fade(parameters, pcm):
    if "fade_in_frames" in parameters or "fade_out_frames" in parameters:
        incoming, outgoing = parameters.get("fade_in_frames", 0), parameters.get("fade_out_frames", 0)
    else:
        incoming = seconds_to_frames(parameters.get("fade_in_seconds", 0), pcm.sample_rate)
        outgoing = seconds_to_frames(parameters.get("fade_out_seconds", 0), pcm.sample_rate)
    if incoming + outgoing > pcm.frames:
        raise AudioError("fade_overlap", "Fade ranges must fit the source without overlap")
    return {"fade_in_frames": incoming, "fade_out_frames": outgoing, "curve": "linear",
            "fade_in_seconds": incoming / pcm.sample_rate, "fade_out_seconds": outgoing / pcm.sample_rate}


def fade_writes(parameters, pcm):
    incoming, outgoing = parameters["fade_in_frames"], parameters["fade_out_frames"]
    # The unity endpoint does not write a changed sample.
    return ([{"start_frame": 0, "end_frame": max(1, incoming - 1)}] if incoming else []) + (
        [{"start_frame": pcm.frames - max(1, outgoing - 1), "end_frame": pcm.frames}] if outgoing else [])


def identity_mapping(parameters, pcm):
    return {"kind": "identity", "frame_count": pcm.frames}


def fade(parameters, pcm):
    incoming, outgoing = parameters["fade_in_frames"], parameters["fade_out_frames"]
    if incoming + outgoing > pcm.frames or min(incoming, outgoing) < 0:
        raise AudioError("fade_overlap", "Fade ranges must fit the source without overlap")
    if not incoming and not outgoing:
        return pcm, {"time_mapping": identity_mapping(parameters, pcm), "write_ranges": []}
    values = pcm.samples()
    output = array("h", values)
    for start, count, reverse in ((0, incoming, False), (pcm.frames - outgoing, outgoing, True)):
        for index in range(count):
            if index % 4096 == 0:
                checkpoint()
            numerator = count - 1 - index if reverse else index
            factor = 0 if count == 1 else (Q24 * numerator + (count - 1) // 2) // (count - 1)
            for channel in range(pcm.channels):
                offset = (start + index) * pcm.channels + channel
                product = values[offset] * factor
                magnitude = (abs(product) + Q24 // 2) // Q24
                output[offset] = -magnitude if product < 0 else magnitude
    return PCM(sample_bytes(output), pcm.sample_rate, pcm.channels), {
        "time_mapping": identity_mapping(parameters, pcm), "write_ranges": fade_writes(parameters, pcm)}
