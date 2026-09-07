"""Measured PCM descriptors, independent of semantic or listening judgments."""

import cmath
import math

from .execution import checkpoint

PROFILE = "pcm16-descriptors/v1"


def dbfs(amplitude):
    return 20 * math.log10(amplitude / 32768) if amplitude else None


def spectrum(values):
    """Radix-2 FFT of a bounded Hann window; power bins, not a model embedding."""
    size = len(values)
    data = [complex(value * (.5 - .5 * math.cos(2 * math.pi * i / (size - 1))))
            for i, value in enumerate(values)]
    j = 0
    for i in range(1, size):
        bit = size >> 1
        while j & bit:
            j ^= bit
            bit >>= 1
        j ^= bit
        if i < j:
            data[i], data[j] = data[j], data[i]
    width = 2
    while width <= size:
        step = cmath.exp(-2j * math.pi / width)
        for start in range(0, size, width):
            factor = 1
            for k in range(width // 2):
                left, right = data[start + k], data[start + k + width // 2] * factor
                data[start + k], data[start + k + width // 2] = left + right, left - right
                factor *= step
        width *= 2
    return [abs(value) ** 2 for value in data[:size // 2 + 1]]


def describe(pcm):
    samples = pcm.samples()
    count = len(samples)
    peak = total = squares = crosses = active = 0
    first = last = peak_frame = None
    previous = [0] * pcm.channels
    for frame in range(pcm.frames):
        if frame % 4096 == 0:
            checkpoint()
        frame_peak = 0
        for channel in range(pcm.channels):
            value = samples[frame * pcm.channels + channel]
            total += value
            squares += value * value
            frame_peak = max(frame_peak, abs(value))
            if frame and ((value < 0 <= previous[channel]) or (previous[channel] < 0 <= value)):
                crosses += 1
            previous[channel] = value
        if frame_peak > peak:
            peak, peak_frame = frame_peak, frame
        if frame_peak > 32:  # fixed amplitude threshold, about -60.2 dBFS
            first = frame if first is None else first
            last, active = frame + 1, active + 1
    rms = math.sqrt(squares / count)
    size = 2048
    windows = min(16, max(1, (pcm.frames + size - 1) // size))
    starts = sorted({round(i * max(0, pcm.frames - size) / max(1, windows - 1)) for i in range(windows)})
    powers = [0.] * (size // 2 + 1)
    for start in starts:
        checkpoint()
        # Channel powers are summed so opposite-phase stereo cannot disappear.
        for channel in range(pcm.channels):
            values = [samples[frame * pcm.channels + channel] if frame < pcm.frames else 0
                      for frame in range(start, start + size)]
            powers = [a + b for a, b in zip(powers, spectrum(values))]
    power = sum(powers)
    centroid = sum(i * pcm.sample_rate / size * value for i, value in enumerate(powers)) / power if power else None
    correlation = None
    if pcm.channels == 2:
        left, right = samples[0::2], samples[1::2]
        cross = sum(a * b for a, b in zip(left, right)) - sum(left) * sum(right) / pcm.frames
        variance = (sum(a * a for a in left) - sum(left) ** 2 / pcm.frames) * (sum(b * b for b in right) - sum(right) ** 2 / pcm.frames)
        correlation = max(-1., min(1., cross / math.sqrt(variance))) if variance > 0 else None
    window = min(pcm.frames, max(1, pcm.sample_rate // 100)) * pcm.channels
    head = math.sqrt(sum(value * value for value in samples[:window]) / window)
    tail = math.sqrt(sum(value * value for value in samples[-window:]) / window)
    jump = [samples[channel] - samples[-pcm.channels + channel] for channel in range(pcm.channels)]
    slope = ([abs((samples[pcm.channels + c] - samples[c]) -
                  (samples[-pcm.channels + c] - samples[-2 * pcm.channels + c])) for c in range(pcm.channels)]
             if pcm.frames > 1 else [0] * pcm.channels)
    return {"profile": PROFILE, "media": pcm.facts(), "peak_pcm16": peak, "rms_pcm16": rms,
        "peak_dbfs": dbfs(peak), "rms_dbfs": dbfs(rms), "dc_offset_pcm16": total / count,
        "crest_db": 20 * math.log10(peak / rms) if rms else None,
        "zero_crossing_rate": crosses / max(1, (pcm.frames - 1) * pcm.channels),
        "active_frame_fraction": active / pcm.frames, "activity_threshold_pcm16": 32,
        "active_start_frame": first, "active_end_frame": last, "peak_frame": peak_frame,
        "spectral_centroid_hz": centroid, "spectral_method": {"fft_size": size, "window": "hann",
            "window_start_frames": starts, "channel_combination": "sum_power", "scope": "sampled_windows"},
        "stereo_correlation": correlation,
        "seam": {"endpoint_jump_pcm16": jump, "max_jump_pcm16": max(map(abs, jump)),
            "max_slope_difference_pcm16": max(slope), "head_rms_dbfs": dbfs(head), "tail_rms_dbfs": dbfs(tail)},
        "limitations": ["RMS is not LUFS; spectral samples are not a full spectrogram.",
            "Measurements do not identify instruments, musical structure or audible seamlessness."]}
