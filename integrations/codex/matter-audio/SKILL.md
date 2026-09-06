---
name: matter-audio
description: Use SonicMatter or ScoreMatter to inspect, crop, and adjust existing local audio, or prepare candidates from registered paper recordings. Use for local sound-effect/BGM authoring with these projects. Model generation, persistent editing sessions and game integration are separate capabilities.
---

# Matter audio authoring

Use the configured product CLI through `scripts/run_audio.py`. The sibling
`config.local.json` supplies the local interpreter, product checkout and audio
workspace. Keep that machine-local file separate from shared skill source.

Choose `--product sonic` for registered recording/Foley work, or `--product score`
for existing BGM. Use the user's selected product and asset; if neither is clear,
inspect the current project context before choosing. Query `capabilities` for
the installed operation schemas before creating requests.

```text
python <skill-dir>/scripts/run_audio.py --product score -- capabilities --json
python <skill-dir>/scripts/run_audio.py --product score -- assets import <wav> --request-id <id> --json
python <skill-dir>/scripts/run_audio.py --product score -- inspect <asset-id> --json
python <skill-dir>/scripts/run_audio.py --product score -- action execute --request <request.json> --json
```

Requests contain `schema: matter-action/v1`, `request_id`, `operation`, an `inputs`
list of exact asset IDs, and typed `parameters`. `gain/v1` takes `db` and optional
`clip` (default `reject`). `trim/v1` takes either frame or second start/end fields,
with an exclusive end. `action resolve` returns actual frames, multiplier and a
resolution digest; execute can bind it with `--expected-resolution-digest`.

For Sonic, use `catalog list`, then `catalog decode <registered-id> --request-id
<id>`. Choose the returned audio asset from `playback`/its role; the registration
and compressed source outputs are provenance snapshots. Sonic also exposes
`sonic.recording_condition/v1`: start frame, frame count, fade-in/out frames and
Q15 gain. It preserves the existing Sonic processing algorithm. See the selected
product's `docs/shared-audio.md` when preparing that operation.

Explain the intended preservation, change, operation and listening focus briefly.
Use actual returned asset IDs, frame counts, digests and measurements. Present
the verified local `playback` WAV using an absolute-path audio embed, and distinguish
measured changes from listening judgments. These operations use zero audio-model
calls; returning audio does not mean the language model has heard it.

Reuse a request ID when querying or retrying the same action. A different request
under that ID conflicts. On timeout or `recovery_pending`, query `action show`
and report its actual state; do not start a new request to hide a retry.

M1 has immutable snapshots and parent relationships, but no shared session,
selection/feedback persistence, PCM region locks or automatic recovery. Do not
claim these future operations worked. A local candidate and an imported asset
do not by themselves establish user acceptance or consumer distribution rights.
