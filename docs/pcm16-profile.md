# PCM16 processing profile 0.1.0

`pcm16-transform-q24/v1` accepts RIFF/WAVE format code 1, signed 16-bit PCM,
mono/stereo, 8000–192000 Hz, nonempty payload, at most 64 MiB per file. Chunk,
RIFF size, byte rate and block alignment must match actual bytes. A different
format requires an explicit decoder/import adapter.

Frames contain all simultaneous channel samples; ranges are half-open.
Second coordinates resolve with Decimal `ROUND_HALF_UP` to source frames.
Trim copies the selected PCM bytes exactly and reports its source/output mapping.

Gain supports -60..+24 dB. With a 50-digit Decimal context, the resolver computes
`gain_q24 = round_half_up(2^24 * 10^(db/20))` and binds that integer into the
resolution. Each signed sample is multiplied by this integer, divided by 2^24,
and rounded to nearest with ties away from zero. Overflow fails the action by
default; `clip: saturate` explicitly saturates to [-32768,32767] and reports the
overflow sample count. The existing Sonic fused Q15 operation is a separate
versioned product profile.

`pcm16-levels/v1` measures sample peak and RMS relative to 32768, both combined
and per channel, with paged windows. Silence has `null` dBFS. A full-scale sample
count is not a clipping detector. PCM fingerprints include sample rate and
channel count followed by interleaved little-endian PCM bytes; file digests
instead cover the entire WAV file.

Complete immutable groups contain manifest, manifest digest and audio/source
files. Readers verify the full inventory and hashes. Windows directory rename
and Linux `renameat2(RENAME_NOREPLACE)` provide complete no-replace publication.
Unsupported platforms fail rather than falling back to partially visible output.
Files are flushed before publication; full power-loss durability of filesystem
metadata is not claimed.

One exclusive claim binds each request ID to input/operation identity and its
result group. Completed retries return the original result. Conflicts fail.
An interrupted claim without a complete result reports `recovery_pending`;
M1 does not automatically reclaim or rerun it. New requests with the same audio
create distinct creative records. There is no implicit global result cache.

Core 0.4 adds the separate `pcm16-fade-linear-q24/v1` profile. Its endpoint,
rounding and unchanged-region rules are specified in [protected editing](regions.md#fade-profile).
The gain, trim, inspection and Sonic fused Q15 profiles above are unchanged.
