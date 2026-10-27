# Advisory audio diagnostics

`quality/v1` scans a PCM16 source for intervals worth reviewing. It creates a
source-bound findings report and **no audio output**. It does not repair audio,
select a version, score quality, or establish listening acceptance. A quiet
passage, musical accent, or sample near full scale can be intentional.

The processing profile is `pcm16-quality-advisory/v1`. Its three content presets
are explicit policies, not automatic speech or music recognition:

| `profile` | Minimum low-level duration | Adjacent RMS change | Low-level message |
| --- | --- | --- | --- |
| `generic` (default) | 1,000 ms | 12 dB | Review the intended pause. |
| `music` | 2,000 ms | 18 dB | A note that the interval may be a musical rest. |
| `speech` | 500 ms | 9 dB | Review the intended pause. |

These are review heuristics, not production standards or acceptance thresholds.
The lower speech threshold can surface gaps for inspection; it cannot identify
missing words. Music receives a longer pause threshold and a larger level-change
threshold, but its legitimate dynamics can still produce advisories.

## Request and Python interface

For an immediate read-only report, use the shared CLI in any product:

```sh
matter-audio --workspace /absolute/workspace quality ASSET_ID --profile music --max-warnings 64
```

`score-matter audio` and `python -m tools.authoring` expose the same command.
The result binds the report to the asset ID and digest without publishing a
new result. To persist findings, use the action form below.

Use an ordinary resolved/executed action with one imported asset:

```json
{
  "schema": "matter-action/v1",
  "request_id": "review-take-1",
  "operation": "quality/v1",
  "inputs": ["<asset-id>"],
  "parameters": {
    "profile": "music",
    "start_frame": 0,
    "end_frame": 441000,
    "window_frames": 2205,
    "max_scan_frames": 4000000,
    "max_warnings": 64
  }
}
```

Replace the asset placeholder and use an end frame within that asset. All
parameters are optional; omitted start/end cover the source, and the default
window is `max(1, sample_rate // 20)` frames (50 ms at common rates).
The schema rejects unknown keys, unsupported presets, booleans/floats in integer
fields, empty ranges and ranges beyond the source. `window_frames` is 1–192,000;
`max_scan_frames` is 1–33,554,432 (default 4,000,000); `max_warnings` is 1–128
(default 64). A smaller window changes the meaning of a level-change comparison.

Python callers with an already validated `PCM` can call
`matter_audio_core.quality.scan(pcm, parameters)`. `resolve(parameters, pcm)`
validates the request and freezes defaults without scanning the samples.
`quality_operation()` supplies the same analysis for an operation registry.
The action result's finding includes `source_asset_id`; its resolution binds the
source digest. Standalone `scan` returns measurements, without an asset identity.

## What the intervals mean

- `clipping_risk`: contiguous frames where at least one channel has a sample
  magnitude of 32,760 or greater. Evidence gives the peak, sample count and
  zero-based affected channel indices. A rail-adjacent sample does not prove
  clipping. This rule does not measure inter-sample peaks or true peak.
- `sustained_silence`: contiguous frames where **every** channel has sample
  magnitude at most 32, sustained for the preset duration. This is thresholded
  low level, not necessarily digital silence. Duration thresholds round upward
  to whole frames. Opposite-phase stereo and a silent channel beside an active
  channel are not mistaken for silence by downmixing.
- `relative_level_jump`: an adjacent pair of nonoverlapping windows differs by
  at least the preset dB threshold. RMS combines squared samples across channels.
  Both windows must have RMS at least 128 PCM units, avoiding ratios against
  silence. The interval spans both windows; `boundary_frame` identifies their
  join and the evidence gives both exact ranges and the signed dB change.
  This is RMS, not LUFS or perceived loudness. The final short window uses its
  actual sample count and can also participate in a comparison.

All coordinates are half-open **source frame** intervals, including when the
scan starts at a nonzero frame. Windows start at the requested start, not at an
implicit global grid. Runs can cross window boundaries. `touches_scan_boundary`
means the event may extend beyond the observed scope; it does not claim an exact
onset or ending outside that scope.

## Bounded scans and incomplete reports

The scanner reads only the prefix allowed by `max_scan_frames` within the
requested range. This limits diagnostic work; the enclosing action's source
integrity checks still verify the complete imported asset.
The report has `requested_range`, `scanned_range`,
`scanned_frames`, `unscanned_frames`, `windows_scanned` and `level_comparisons`.
Cancellation checkpoints run before/after scanning, at each window start and
at least every 4,096 scanned frames inside a window. Cancellation propagates;
it is not converted into a successful partial report.

`warnings` is capped independently of the frame budget. Scanning continues after
that cap so `warning_counts` includes omitted detections and `warnings_omitted`
states how many were omitted. The first completed detections are retained, then
sorted by range for display; this is not a severity ranking or a guarantee that
the earliest-starting long event is retained. Up to 128 warnings keep report
memory bounded even for rapidly alternating input.

`truncated` is true when `reasons` contains `scan_frame_budget`, `warning_limit`,
or both. A report can scan every requested frame and still omit warnings.
Request another explicit interval or a larger allowed budget to inspect more;
scanning adjacent ranges separately can split events and window comparisons.
No warnings, especially in a truncated report, does not mean the audio is good.

The operation declares identity mapping and no writes, so existing PCM protection
references remain usable. It does not change the source, protected regions,
selection, or any existing numerical processing profile.
