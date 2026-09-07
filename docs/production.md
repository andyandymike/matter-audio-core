# Cue packages, loops, scene timelines and local search

Core 0.6 adds four deterministic operations and two immutable document services.
They reuse existing PCM assets, sessions, managed jobs and the local comparison
page. No additional model, provider, download or Python dependency is required.
Run `python examples/production_workflow.py` for a complete example across fresh
CLI processes, including two scene revisions and byte-checked package delivery.

## Level matching and descriptors

`analyze <asset-id>` measures PCM directly. `analyze/v1` provides the same
descriptor profile through the action/job API. Results include peak/RMS/DC,
crest factor, per-channel zero crossings aggregated by sample count, activity
above a fixed 32-PCM-unit threshold, active bounds, peak frame and stereo
correlation. Silence has null dBFS/centroid, not a fabricated finite floor.

Spectral centroid uses power from at most 16 uniformly placed 2048-frame Hann
windows with a radix-2 FFT. Channels contribute separate power spectra, avoiding
opposite-phase cancellation. Short inputs are zero padded. These are sampled
spectral measurements, not a full spectrogram or instrument classifier.
Floating descriptors support ranking; exact PCM protection still uses hashes.

`normalize/v1` accepts `target_rms_dbfs` (-60..-3), optional
`peak_ceiling_dbfs` (-12..0, default -1) and `max_boost_db` (0..24, default 12).
It chooses the smallest Q24 coefficient allowed by the target, boost cap and
integer peak ceiling, then uses the existing gain arithmetic. It preserves
silence and reports when the target is limited. This is whole-clip RMS matching,
not LUFS normalization, compression or a promise of equal perceived loudness.
Use separate actions or the existing managed batch API for several variants.
Global level changes respect existing PCM locks and normally conflict with them.

## Construct a loop

```json
{
  "schema": "matter-action/v1", "request_id": "ambience-loop-1",
  "operation": "loop/v1", "inputs": ["<asset-id>"],
  "parameters": {
    "start_frame": 0, "end_frame": 441000,
    "crossfade_frames": 4410, "curve": "linear", "clip": "reject"
  }
}
```

The source range is `[start,end)`. For overlap F, output starts at source
`start+F`, copies through `end-F`, and blends the final F source frames with the
first F frames. Its period is **end-start-F**, which is shorter than the source
range. The final blend ends on source `start+F-1`, immediately before the first
output frame in the original timeline. Musical bar length is not inferred.

F may be zero (an exact slice) or at least two frames, no more than half the
range. Linear weights sum to Q24; `equal_power` uses rounded sine/cosine Q24
weights and can increase correlated signal peaks. Sample sums round once, ties
away from zero. Overflow defaults to rejection; explicit saturation counts it.
Interior PCM locks can map through the retained slice; removed locks and locks
inside the blended tail are rejected. No lock is silently shortened.

The report includes source/output endpoint jumps, derivative differences and
short head/tail RMS measurements, plus end-exclusive loop coordinates. Small
numbers do not prove an inaudible seam. Exported WAVs remain ordinary PCM16;
loop coordinates are sidecar metadata, and engine-specific packaging is separate.

## Render a scene

`scene/v1` starts from silence with an explicit `duration_frames`. Inputs share
rate and channels; there is no implicit resampling. Up to 16 input references
can be reused by 128 named event definitions, expanded to at most 1024 events.
Output and combined input WAV bytes must each fit within 64 MiB.

```json
{
  "schema": "matter-action/v1", "request_id": "scene-1",
  "operation": "scene/v1", "inputs": ["<loop-asset>", "<click-asset>"],
  "parameters": {
    "duration_frames": 132300,
    "tracks": [{"name": "music", "db": -9}, {"name": "sfx", "db": -3}],
    "events": [
      {"event_id": "bed", "input_index": 0, "track": "music",
       "source_start_frame": 0, "source_end_frame": 44100,
       "offset_frame": 0, "repeat": 3},
      {"event_id": "click", "input_index": 1, "track": "sfx",
       "source_start_frame": 0, "source_end_frame": 4410,
       "offset_frame": 22050, "repeat": 2, "interval_frames": 44100}
    ]
  }
}
```

Events and tracks support `db`, `fade_in_frames` and `fade_out_frames`. Track
fades use the whole scene timeline; event fades apply separately to each repeat.
An event's optional interval defaults to its source length. Overlap can be
requested explicitly; out-of-bounds events are rejected instead of truncated.
Every input and track must be used, and IDs must be distinct. `master_db` and
`clip` are optional, defaulting to 0 and `reject`.

Event, track and master dB are summed before Q24 gain quantization. Event and
track linear fades contribute two more Q24 factors. All samples accumulate in
Q72 and round once. Editing a track means rendering the original recipe again;
other event intervals remain exact when their contributors are unchanged.

A scene has an arrangement mapping, not the first input's timeline. Existing
source PCM locks cannot be projected through arbitrary repeats/reordering and
are rejected. A completed scene can become the anchor of a new protected session.
This is offline rendering, not a realtime mixer or game audio runtime.

## Save cue sets and export packages

`cue-set create --request <json>` saves an immutable `matter-cue-set/v1` request:
`set_id`, `name`, optional `supersedes`, and `cues`. Each cue contains `key`,
`name`, `selected_variant`, and `variants`. Each variant contains `key` and
`asset_id`, with optional `loop: {begin_frame,end_frame}` and
`selection: {session_id,revision}`. A selection must identify that exact saved
asset; later session changes do not alter the package. Loop bounds must fit.

Cue/variant keys use lowercase ASCII letters, digits, hyphens and underscores,
starting with a letter. Labels can use other languages. Limits are 64 cues,
8 variants per cue, 128 total variants and 64 MiB of referenced WAV contents.
Snapshots include measured RMS/peak and a package RMS spread, which describes
level consistency only. To revise selections, create another set ID and name
the earlier ID in `supersedes`; earlier sets remain readable.

`cue-set list` and `cue-set show <set-id>` recover saved packages.
`cue-set export --request <json>` accepts `schema: matter-cue-export/v1`,
`request_id`, `set_id` and `variants: selected|all`. Files use
`<cue-key>__<variant-key>.wav`; ambiguous filename collisions are rejected.
The returned directory includes a manifest binding source digests, saved
selection references and loop metadata. WAV bytes exactly match their assets.
`cue-set export-show <request-id>` checks every exported file again.

Sets, libraries and deliveries use atomic, no-replace directories under the
product workspace. An existing ID with the same request replays its receipt;
different parameters conflict. Incomplete directories are never advertised as
complete delivery. Existing local-filesystem/trusted-writer support boundaries
apply. No SQLite schema migration is needed for these immutable documents.

## Search an explicit local library

`library create --request <json>` takes `schema: matter-library/v1`, `library_id`,
`name` and `entries: [{asset_id,name,tags}]`. Up to 128 distinct existing WAV
assets, totaling at most 64 MiB, are measured and bound to immutable digests.
For SonicMatter, decode registered catalog recordings through its product CLI
first. Indexing does not grant source eligibility or distribution rights.

`library search --request <json>` takes `schema: matter-library-search/v1` and
`library_id`. Optional fields: `text`, `tags`, `min_seconds`, `max_seconds`,
`min_rms_dbfs`, `max_rms_dbfs`, `channels`, `sample_rate_hz`, `similar_to`,
`offset` and `limit` (1..50). Text tokens match explicit names/tags ignoring case;
all tokens/tags are required. RMS filters exclude silent assets.

`similar_to` must be an indexed asset. Ranking uses the mean of six clamped
normalized absolute differences: log2 duration / 4 octaves, RMS / 60 dB,
crest / 30 dB, zero-crossing rate / 1, active fraction / 1 and spectral centroid
/ 10000 Hz. Missing spectral/crest values use zero and silence uses -120 dB
only inside this distance calculation. Every contribution is returned. Ties
sort by asset ID. The reference itself is included if it passes the filters.
No embeddings or semantic audio understanding are implied by this score.

Use `library list` and `library show <id>` after restarting. A changed library
uses a new ID. Queries verify source snapshots before using cached measurements.
Human listening, musical structure, stem separation, additional backends and
game integration remain separate from these engineering capabilities.
