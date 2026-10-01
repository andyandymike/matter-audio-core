---
name: matter-audio
description: Create and refine BGM with configured local ScoreMatter generation, or use Matter Audio Core, SonicMatter and ScoreMatter for audio editing, loops, scene timelines, cue sets, comparison and export. Preserve exact versions, project-scoped musical intent and listening feedback. Game integration is separate.
---

# Matter audio authoring

Use the configured product CLI through `scripts/run_audio.py`. The sibling
`config.local.json` supplies the local interpreter and product checkout. Keep that
machine-local file separate from shared skill source. Resolve the script from this
skill's installed directory, independently of the consuming project.

Audio state defaults to `<project-root>/artifacts/matter-audio/<product>`. The
project root is the current Git root, or the current working directory outside
Git; use `--project-root <absolute-project-dir>` when the intended project differs.
Resolve this before launching: the configured product checkout is a tool location,
not the consuming project's state directory. `--dry-run` prints the resolved
workspace and command without creating files or calling the product.

Use `--workspace <absolute-path>` for an existing session/asset workspace or a
user-selected output location. Do not infer that assets or feedback transfer to a
new workspace. A legacy `workspace` in machine config is used only with explicit
`--use-configured-workspace`; it is never a silent cross-project default.
In a Godot project keep generated audio in an ignored artifact tree. SonicMatter's
own checkout requires `artifacts/.gdignore`; preserve that product's boundary.

The launcher resolves relative `assets import` paths, `--request` files and
`--ready-file` outputs against the caller's working directory before entering
the tool checkout. It also resolves Sonic `recordings list/import --manifest`
and Score `candidate register --audio/--generation-record/--intent` file options.
Paths stored inside request JSON still follow that operation's
schema; use explicit absolute file paths where the schema accepts them.

For BGM direction, prompt/seed comparisons, musical revisions or listening feedback,
read [BGM authoring](references/bgm-authoring.md). Resolve the consuming project
first and read only its optional `.agents/skill-context/matter-audio.md`; paths
in that adapter resolve from the project root. Without an adapter, use the
current brief and exact selected audio. Keep style memories, cue choices and
feedback in that project, never in the shared skill or another project's records.

Text-to-audio uses the separate [ScoreMatter generation route](references/score-generation.md)
through `scripts/run_bgm.py`. It launches the installed product's native
`generate` command; it is not a shared audio action or job. The helper keeps
both WAVs and generation records with the consuming project, verifies the local
runtime without loading weights using `--check`, and offers a child-free `--dry-run`.
Reading this skill or preparing comparisons does not start generation.

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

For the user's own Sonic PCM16 WAV recordings, check `capabilities` for
`recordings list/import`, then use an explicit project manifest with source,
hash, size and purpose-specific rights declarations. Import requires declared
local-preview permission and evidence; never invent permission or treat it as
independent rights verification. The imported WAV retains its original bytes.
For an existing Score candidate, use `candidate register --audio <wav>` with
optional `--generation-record <record.json>` and `--intent <intent.json>` to
retain provenance and project intent. Use the product's published schemas.
Registration runs no model and does not establish listening approval, select a
session version or grant publication rights. Choose its returned audio asset ID
explicitly in the existing session flow.

For bar/beat ranges or named musical sections, check the Score product's music
capabilities and read [music coordinates and sections](references/music-coordinates.md).
Music annotations bind one exact audio asset. Use explicit timing declarations;
a requested BPM in a generation prompt is not evidence of the generated audio's
beat grid. Planning saves a reviewable immutable plan; executing it is a separate
step and does not select the resulting audio. Report actual frame ranges and any
overlap-induced reduction in loop period. New audio needs its own annotations.
For named-section replacement, bind the base and replacement annotations and
check their complete resolved frame lengths, sample rates and channels. Use the
installed splice plan schema; equal bar counts alone do not establish equal
duration. Keep transitions inside the editable region and preserve base locks.
For a new sequential structure, use `music arrange` with ordered named regions
and explicit integer repetitions, then inspect and execute its saved plan.
Report the actual total length and each occurrence's output range. This creates
a new timeline; it does not transfer old musical grids, annotations or PCM locks.
Do not bypass a required preservation policy through arrangement: protection
parameters are unsupported, and source sessions must remain unchanged.
When `score-music-arrange/v2` is available, request linear crossfades at explicit
occurrence boundaries. Report the overlap ranges and shortened total duration;
unlisted boundaries remain hard cuts. A new `music annotate-arrangement` request
can mark selected occurrences on the completed output. Choose `full` (including
mixed transition samples) or `body` (excluding both overlaps) explicitly. These
new marks use unknown timing, retain provenance, and can feed subsequent musical
plans; they do not create locks or select the result. Follow the music reference
for a complete arrange, mark, protect, edit, select and export workflow.

Explain the intended preservation, change, operation and listening focus briefly.
Use actual returned asset IDs, frame counts, digests and measurements. Present
the verified local `playback` WAV using an absolute-path audio embed when listening
is requested, and distinguish measured changes from listening judgments. Respect
a request to defer listening. Built-in PCM and registered-recording operations use
zero audio-model calls; returning audio does not mean the language model has heard it.

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

For interrupted work or batches, check `capabilities.jobs` and read
[managed jobs](references/jobs.md). Core 0.3 adds explicit recovery, independently
retried batch items and cooperative CPU cancellation. Prefer managed jobs when
the task needs these guarantees; direct audio actions keep their legacy behavior.

For requests to preserve an intro or transient while editing the rest, check
`sessions.pcm_region_protection` and read [protected editing](references/regions.md).
Core 0.4 adds PCM locks and generic `fade/v1`. Read current context before each
continued edit; use managed jobs to bind the session's policy automatically.
Keep the selected input, resolved frames and actual preservation evidence in view.

Core 0.5 adds a local comparison page, exact saved-version exports, `mix/v1` and
`splice/v1`. Read [comparison, layers and local models](references/comparison-and-layers.md).
Use `context show` or `audition list --session <id>` to recover comparison IDs.
An available ScoreMatter `score.sa3_inpaint/v1` operation is a local model call;
check its configured availability and the user's scope before executing. Report
actual launches, cancellation/failure, timing and any uncertainty from job evidence.
Do not equate model-mask preservation with exact final PCM, silently install
weights, call a paid API or manufacture a listening verdict.

Core 0.6 adds cue/variant packages, overlap loops, finite scene timelines, RMS
matching and local feature search. Read [production authoring](references/production.md).
Use explicit existing assets and recipes; confirm actual operation schemas from
the configured product. Text tags are authored metadata, numeric distance is not
semantic understanding, and measured seam quality does not establish listening
acceptance. Keep source eligibility and saved selection references in deliveries.
