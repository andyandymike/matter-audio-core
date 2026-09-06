"""PCM16 operations; integer frames and explicit, independent numeric profiles."""

from __future__ import annotations

import io
import math
import struct
import sys
import wave
from array import array
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP, localcontext

from .contracts import digest
from .errors import AudioError
from .execution import checkpoint

MAX_AUDIO_BYTES = 64 * 1024 * 1024
PROFILE = "pcm16-transform-q24/v1"
Q24 = 1 << 24


@dataclass(frozen=True)
class PCM:
    payload: bytes
    sample_rate: int
    channels: int

    @property
    def frames(self) -> int:
        return len(self.payload) // (2 * self.channels)

    def samples(self) -> array:
        values = array("h")
        values.frombytes(self.payload)
        if sys.byteorder != "little":
            values.byteswap()
        return values

    def facts(self) -> dict:
        return {"container": "wav", "codec": "pcm_s16le", "sample_rate_hz": self.sample_rate,
                "channels": self.channels, "channel_layout": "mono" if self.channels == 1 else "stereo",
                "frame_count": self.frames, "duration_seconds": self.frames / self.sample_rate}


def decode_wav(data: bytes) -> PCM:
    if not 44 <= len(data) <= MAX_AUDIO_BYTES:
        raise AudioError("unsupported_audio", "WAV must be between 44 bytes and 64 MiB")
    if data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        raise AudioError("unsupported_audio", "Expected RIFF/WAVE PCM16")
    if struct.unpack_from("<I", data, 4)[0] + 8 != len(data):
        raise AudioError("invalid_audio", "RIFF size does not match actual file length")
    # Require ordinary PCM across Python 3.10/3.12, including strict chunk boundaries.
    offset, formats, payloads = 12, [], []
    while offset < len(data):
        if offset + 8 > len(data):
            raise AudioError("invalid_audio", "Truncated chunk header")
        tag, size = struct.unpack_from("<4sI", data, offset)
        start, end = offset + 8, offset + 8 + size
        if end > len(data):
            raise AudioError("invalid_audio", "Truncated chunk payload")
        if tag == b"fmt ":
            formats.append(data[start:end])
        if tag == b"data":
            payloads.append(data[start:end])
        offset = end + (size & 1)
    if offset != len(data) or len(formats) != 1 or len(payloads) != 1 or len(formats[0]) < 16:
        raise AudioError("invalid_audio", "Expected one valid format chunk and one data chunk")
    codec, channels, rate, byte_rate, align, bits = struct.unpack_from("<HHIIHH", formats[0])
    if codec != 1 or bits != 16 or channels not in (1, 2) or not 8000 <= rate <= 192000:
        raise AudioError("unsupported_audio", "Supported: PCM16 mono/stereo, 8000–192000 Hz")
    payload = payloads[0]
    if align != channels * 2 or byte_rate != rate * align or not payload or len(payload) % align:
        raise AudioError("invalid_audio", "Invalid frame alignment or byte rate")
    return PCM(payload, rate, channels)


def encode_wav(pcm: PCM) -> bytes:
    output = io.BytesIO()
    with wave.open(output, "wb") as stream:
        stream.setnchannels(pcm.channels)
        stream.setsampwidth(2)
        stream.setframerate(pcm.sample_rate)
        stream.writeframes(pcm.payload)
    return output.getvalue()


def sample_bytes(values: array) -> bytes:
    result = array("h", values)
    if sys.byteorder != "little":
        result.byteswap()
    return result.tobytes()


def gain_multiplier(db: int | float) -> int:
    with localcontext() as context:
        context.prec = 50
        multiplier = Decimal(10) ** (Decimal(str(db)) / Decimal(20))
        return int((multiplier * Q24).to_integral_value(rounding=ROUND_HALF_UP))


def gain(pcm: PCM, multiplier: int, clip: str = "reject") -> tuple[PCM, int]:
    output, clipped = array("h"), 0
    for index, sample in enumerate(pcm.samples()):
        if index % 8192 == 0:
            checkpoint()
        product = sample * multiplier
        value = (abs(product) + Q24 // 2) // Q24
        if product < 0:
            value = -value
        if not -32768 <= value <= 32767:
            clipped += 1
        output.append(max(-32768, min(32767, value)))
    if clipped and clip == "reject":
        raise AudioError("clipping_rejected", "Gain would overflow PCM16",
                         details={"overflow_sample_count": clipped})
    return PCM(sample_bytes(output), pcm.sample_rate, pcm.channels), clipped


def trim(pcm: PCM, start: int, end: int) -> PCM:
    if not 0 <= start < end <= pcm.frames:
        raise AudioError("invalid_range", "Expected 0 <= start < end <= source frames")
    width = 2 * pcm.channels
    return PCM(pcm.payload[start * width:end * width], pcm.sample_rate, pcm.channels)


def inspect(pcm: PCM, *, window_frames: int | None = None, offset: int = 0, limit: int = 128) -> dict:
    window_frames = window_frames or max(1, pcm.sample_rate // 10)
    values = pcm.samples()

    def levels(part) -> dict:
        peak, squares, full_scale = 0, 0, 0
        for index, value in enumerate(part):
            if index % 8192 == 0:
                checkpoint()
            peak = max(peak, abs(value))
            squares += value * value
            full_scale += value in (-32768, 32767)
        rms = math.sqrt(squares / len(part))
        return {"peak_pcm16": peak, "rms_pcm16": rms,
                "peak_dbfs": 20 * math.log10(peak / 32768) if peak else None,
                "rms_dbfs": 20 * math.log10(rms / 32768) if rms else None,
                "full_scale_sample_count": full_scale}

    count = (pcm.frames + window_frames - 1) // window_frames
    windows = []
    for index in range(offset, min(offset + limit, count)):
        start, end = index * window_frames, min((index + 1) * window_frames, pcm.frames)
        windows.append({"start_frame": start, "end_frame": end,
                        **levels(values[start * pcm.channels:end * pcm.channels])})
    return {"method": "pcm16-levels/v1", "kind": "measurement", "media": pcm.facts(),
            "levels": levels(values), "channel_levels": [levels(values[c::pcm.channels]) for c in range(pcm.channels)],
            "windows": windows, "window_frames": window_frames, "window_count": count,
            "next_offset": offset + len(windows) if offset + len(windows) < count else None,
            "pcm_digest": digest(struct.pack("<IH", pcm.sample_rate, pcm.channels) + pcm.payload),
            "limitations": ["Levels do not measure perceived quality or material identity.",
                            "Full-scale samples are not proof of clipping; silence dBFS is null."]}
