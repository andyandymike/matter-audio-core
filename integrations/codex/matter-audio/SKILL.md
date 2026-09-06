---
name: matter-audio
description: Use Matter Audio Core, SonicMatter or ScoreMatter for local sound-effect/BGM authoring, audio inspection, gain, trimming, persistent version selection and feedback. Use registered paper recordings through SonicMatter. Model generation and game integration are separate capabilities.
---

# Matter audio authoring

Use the configured product CLI through `scripts/run_audio.py`. The sibling
`config.local.json` supplies the local interpreter, product checkout and audio
workspace. Keep that machine-local file separate from shared skill source.

Choose `--product core` for standalone WAV authoring, `--product sonic` for
registered recording/Foley work, or `--product score` for ScoreMatter BGM.
Use the user's selected product and asset; if neither is clear,
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

For continued work, check the installed CLI's `sessions` capability, then read
[session requests](references/sessions.md). Core 0.2 adds durable selection,
branching and attributed feedback; an older product installation may not expose
them. Read `context show <session-id>` when resuming. Select a completed output
explicitly using the observed revision; an audio action does not change the
session's selection on its own.

Store the user's actual feedback verbatim with `source: agent_relay`, bound to
the revision they evaluated. Use `source: agent` for the agent's own notes or
hypotheses. User observations and measurements are separate evidence. Never
invent a listening verdict. A local candidate and an imported asset do not by
themselves establish acceptance or consumer distribution rights.

PCM region locks, generic fades and automatic audio job recovery remain future
work. Session persistence does not imply that those operations are available.
