# Production authoring with core 0.6

Query the installed product capabilities first. Continue using registered Sonic
catalog decoding for recordings and the existing Score audio workspace for BGM.
The features below operate on existing PCM with zero audio-model calls.

- `analyze <asset-id>` returns RMS/peak/DC, activity, sampled spectral centroid,
  stereo correlation and endpoint/seam measurements. `analyze/v1` records the
  same result through action/job execution.
- `normalize/v1`: `target_rms_dbfs`, optional `peak_ceiling_dbfs` (default -1)
  and `max_boost_db` (default 12). Use explicit variant actions or managed batches.
  Report capped targets and silence. This is RMS matching, not LUFS or compression.
- `loop/v1`: `start_frame`, `end_frame`, `crossfade_frames`, optional `curve`
  (`linear` default or `equal_power`) and `clip`. F overlap frames shorten the
  output period by F. The retained timeline starts at source `start+F`; removed
  or blended locks conflict. Report output loop coordinates and measured seam
  differences without asserting musical continuity.
- `scene/v1`: explicit `duration_frames`, named `tracks` and timed `events`.
  Tracks/events take dB and fades; events bind `input_index`, a source range,
  `offset_frame`, optional `repeat` and `interval_frames`. Re-render the same
  original recipe when changing a track. All events must fit. Arbitrary scene
  arrangements cannot project an existing input PCM lock; keep that policy intact
  and use a separate unprotected authoring session when appropriate to the task.

Create packages with `cue-set create --request <json>`:

```json
{
  "schema": "matter-cue-set/v1", "set_id": "ui-v1", "name": "UI paper",
  "cues": [{"key": "switch", "name": "Switch", "selected_variant": "soft",
    "variants": [{"key": "soft", "asset_id": "<asset-id>",
      "selection": {"session_id": "switch", "revision": 3}}]}]
}
```

Only provide a selection reference read from real session state. Loop variants
may include `loop: {begin_frame,end_frame}` using exact end-exclusive output
coordinates. A new set ID with `supersedes` revises the package; prior sets stay
immutable. Use `cue-set list/show` after restarting. `cue-set export` accepts
`schema: matter-cue-export/v1`, `request_id`, `set_id` and `variants: selected|all`.
The returned directory contains exact WAV files and their manifest. Use
`cue-set export-show <request-id>` after an uncertain response before retrying.
Package delivery does not approve source rights or insert a game catalog entry.

`library create` accepts `schema: matter-library/v1`, `library_id`, `name` and
`entries: [{asset_id,name,tags}]`. Use names/tags supported by catalog data or
explicit task context, not invented listening impressions. `library search`
accepts `schema: matter-library-search/v1`, `library_id`, optional `text`, `tags`,
duration/RMS bounds, channels, sample rate and `similar_to` (an indexed asset).
Results explain numeric distance contributions and support pagination. This is
local literal metadata and measured-feature search, with no semantic embedding.

No new backend, weight download, stem separation, tempo/chord inference, paid API
or automatic listening verdict is part of this workflow. Defer listening when
the user requests it. Full schemas are available from product capabilities;
the core's `docs/production.md` and `examples/production_workflow.py` give details.
