# Optional Codex integration

The core CLI works directly from a Codex workspace. For example, after installing
this package in the workspace environment, ask Codex to run
`python -m matter_audio_core capabilities --json`, then use an explicit audio
workspace to import and transform a user-selected WAV.

The bundled [matter-audio skill](https://github.com/andyandymike/matter-audio-core/blob/main/integrations/codex/matter-audio/SKILL.md)
provides a launcher for the standalone core and compatible SonicMatter and
ScoreMatter checkouts. It forwards structured CLI arguments; it does not contain either
product, a model runtime or an audio service connection.

## Configure the product launcher

1. Install a compatible product checkout with its authoring entry point:
   `python -m matter_audio_core` for the standalone core,
   `python -m score_matter audio` for ScoreMatter or
   `python -m tools.authoring` from a SonicMatter checkout. Install this core
   wheel into that product's Python environment. Confirm its `capabilities`
   command succeeds before configuring the launcher.
2. Copy the `integrations/codex/matter-audio/` folder into the skills directory
   used by your Codex installation, or invoke its script directly from this
   checkout while developing.
3. Copy `config.example.json` to `config.local.json` next to `SKILL.md`. Replace
   the example values with absolute paths to the product interpreter and checkout.
   Configure only the products you use.
   `config.local.json` is ignored by Git and excluded from packages.
4. Run the launcher with the Python command from your environment:

```sh
python integrations/codex/matter-audio/scripts/run_audio.py --product core -- capabilities --json
```

Use the installed skill's path instead if you copied it. `--config` selects a
different configuration file. State defaults to
`<consuming-project>/artifacts/matter-audio/<product>`; the consuming project is
the current Git root, or cwd outside Git. Use `--project-root <absolute-path>`
when launching for a different project and `--dry-run` to inspect the resolved
command without starting a product or creating files.

`--workspace <absolute-path>` selects a specific workspace, including a prior
session's state. A legacy machine-config `workspace` is honored only with
`--use-configured-workspace`; it is not an implicit shared default. No existing
assets or sessions are moved. On Windows, JSON paths can use `C:/projects/...`
or escaped backslashes. Product paths remain explicit configuration.

Relative `assets import`, `--request` and `--ready-file` CLI paths are resolved
against the caller's working directory before entering the product checkout.
The same applies to Sonic `recordings list/import --manifest` and Score
`candidate register --audio/--generation-record/--intent` in compatible products.
Paths inside request JSON follow the operation schema; use explicit absolute
paths when the schema accepts file locations.

For SonicMatter, an in-repository workspace must satisfy that product's
authoring safeguards (under its `artifacts` tree with `.gdignore`); an explicit
workspace outside the product checkout is another option. Use separate
workspaces for the two products.

## Register project inputs

Check the configured product's `capabilities` first. Compatible SonicMatter
checkouts can import project PCM16 WAV recordings from an explicit manifest;
ScoreMatter can register an existing candidate with its optional generation
record and music intent:

```sh
python <skill-dir>/scripts/run_audio.py --product sonic -- recordings list --manifest recordings.json
python <skill-dir>/scripts/run_audio.py --product sonic -- recordings import paper-01 --manifest recordings.json --request-id register-paper-01
python <skill-dir>/scripts/run_audio.py --product score -- candidate register --audio candidate.wav --generation-record candidate.generation.json --intent intent.json --request-id register-music-01
```

Use the product's `docs/shared-audio.md` for the exact manifest and intent schema.
Sonic requires explicit local-preview permission with a declared evidence reference;
this is not independent rights verification or publication approval. Score checks
the record's output hash and media facts against the supplied WAV; historical
generation declarations remain unverified. Both retain original bytes and register
immutable assets without model execution, playback or automatic session selection.
Use the returned audio asset ID in an explicit `session create` or revision-guarded
`session select`, then continue with existing editing, comparison and export commands.

Retry the same registration with the same request ID and unchanged inputs. Changed
inputs under that ID conflict. After success, `action show <request-id>` retrieves
the saved result without the original files. An interrupted claim can report
`recovery_pending`; do not hide it by registering under a new ID.

## Continue shared authoring

Compatible ScoreMatter checkouts expose asset-bound music annotations and plans.
Use the product's `capabilities` and `docs/shared-audio.md` for their strict request
schemas. The launcher resolves `music annotate --request`, `music plan --request`,
`music arrange --request` and `music annotate-arrangement --request` from the
caller's working directory:

```sh
python <skill-dir>/scripts/run_audio.py --product score -- music annotate --request annotations.json
python <skill-dir>/scripts/run_audio.py --product score -- music show ANNOTATION_ASSET_ID
python <skill-dir>/scripts/run_audio.py --product score -- music plan --request music-plan.json
python <skill-dir>/scripts/run_audio.py --product score -- music show PLAN_ASSET_ID
python <skill-dir>/scripts/run_audio.py --product score -- music execute PLAN_ASSET_ID
```

Annotations preserve the declared timing source, exact audio identity and named
regions. Fixed-tempo grids require BPM, its note unit, meter and first-beat frame;
free or unknown timing still supports seconds and frames. A generation target
does not establish the audio's actual tempo. Planning freezes the converted
ranges, rounding error and downstream request before execution. It publishes
metadata without processing audio or changing a session. Execution reuses shared
trim, loop, splice or region protection; select any resulting audio explicitly afterward.
Protection plans add to existing locks. Loop crossfade shortens the output period,
which the plan reports. An edited audio asset does not inherit old annotations.

For section replacement, a `splice` music plan names one base region and a
replacement annotation/region. Both complete regions must resolve to equal frame
counts with matching sample rates and channels; equal bar counts alone are not
enough. The plan binds both exact versions and uses Core's existing `splice/v1`.
It preserves the base duration and all PCM outside the target window. Explicit
edge transitions stay inside that window, and protection refers to the base.
There is no implicit cropping, padding or time stretching. See the
[ScoreMatter request example](https://andyandymike.github.io/score-matter/shared-audio/)
for the replacement schema; inspect the plan before executing it. Engineering
checks do not establish that the musical join sounds natural.

To assemble a new structure, `music arrange --request arrangement.json` saves a
separate plan with an ordered list of named source regions and explicit integer
repeat counts. Use `music show` to inspect it and `music execute` to render it.
The plan records each region's exact annotation/audio version and every
occurrence's output interval, with a total duration calculated from actual
frames. Core's existing `scene/v1` renders consecutive copies without implicit
padding, trimming, level changes or transitions. All sources must share a sample
rate and channel count. Different source grids do not become a global BPM map.
The result has a new timeline: old annotations and PCM locks are not inherited,
and unsupported protection/session fields are rejected. Source sessions stay
unchanged; explicitly select the candidate and create new marks or locks as
needed. The [ScoreMatter guide](https://andyandymike.github.io/score-matter/shared-audio/)
includes the request schema and capacity limits.

Compatible ScoreMatter versions also accept `score-music-arrange/v2` with
explicit linear crossfades after selected occurrences. For example, only the
second occurrence of theme A can overlap theme B; all other boundaries stay
unchanged. The plan records the overlap ranges, shortened duration and each
occurrence's full and unmixed body ranges. Transitions that exceed a source,
overlap within the same occurrence or exceed Core's event capacity are rejected
before publication. Existing v1 requests and saved plans retain their behavior.

After rendering, `music annotate-arrangement --request marks.json` creates new
named regions on that exact completed output. Choose each occurrence by segment
ID and zero-based repeat index, and explicitly choose its `full` range or `body`.
Full ranges include blended samples from neighbors; body ranges exclude both
transition overlaps and must be nonempty. New marks preserve the plan and source
mapping but declare unknown timing, without inheriting a global BPM or old locks.
They can feed the existing trim, loop, splice, arrangement and protection plans.
The command never renders missing audio or selects a session version.

For continued editing, explicitly select the arranged candidate in a new session,
mark its output regions, protect the regions to retain, and plan a named-region
replacement with that protection reference. Inspect the plan, execute it, then
select and export the chosen result using the existing revision guards. These
steps preserve source sessions and distinguish an exported candidate from
listening or in-game acceptance.

Core 0.2 adds persistent sessions and attributed feedback. Check the installed
CLI's `sessions` capability before using them; older product environments can
still have core 0.1 installed. Use `context show <session-id>` when resuming and
follow the skill's session reference for mutation schemas, revision guards and
feedback attribution. Session state belongs to its configured product/workspace;
switching launch targets does not transfer that state.

Core 0.3 adds `jobs` capabilities and a managed-jobs Skill reference. Use explicit
job recovery after interruption and retry only stopped failed items with a concrete
reason. Existing 0.2 workspaces need `session migrate`; all clients sharing one
workspace should use the same current core version.

Core 0.4 adds a protected-editing Skill reference, `constraints set/show`, and
`fade/v1`. Read current context before a continued edit. Managed jobs bind the
session policy automatically; direct protected actions bind an exact revision.
Upgrade 0.2/0.3 workspaces with `session migrate`. Restore includes the historical
policy, so restoring an unlocked revision explicitly removes later locks.

Core 0.5 adds comparison, exact export, layering/splice and optional local-model
instructions. `context show` retrieves comparison IDs; an on-demand browser page
is opened only when useful. The user can defer listening while engineering work
continues. Update the installed Skill and all product environments together.

The skill returns verified playback paths and measured changes. Model editing
requires a separately configured product adapter; installing the core does not
install weights or enable a paid provider.

## BGM creation through native ScoreMatter

The skill also carries project-scoped musical direction, fixed-seed prompt comparisons,
fixed-prompt seed refinement, listening feedback and loop-audition guidance. An optional
`<consuming-project>/.agents/skill-context/matter-audio.md` supplies that project's
music records and conventions. These records never become global skill preferences.

New text-to-music candidates use `scripts/run_bgm.py`, separate from the shared
audio-action launcher. Configure `score.sa3_runtime_root` as the absolute local
SA3 TFLite runtime directory, in addition to the existing product interpreter/root.
The installed ScoreMatter must support native `generate --record-root`; this small
optional argument directs provenance records alongside consumer output while its
omission preserves ScoreMatter's existing local-record behavior. An older checkout
without it fails preflight instead of silently writing another project's prompts
into the product repository. No model, weights or product are installed by the skill.

```sh
python <skill-dir>/scripts/run_bgm.py --check
python <skill-dir>/scripts/run_bgm.py --project-root <absolute-project> --prompt "<musical intent>" --seed 31415 --out <new-absolute-wav> --dry-run
```

The first command checks product support, validates arguments and checks runtime-file
metadata without loading weights or generating audio. The second only prints paths
and arguments; it starts no child. Omit `--dry-run` only to perform the requested
one-candidate generation. Unspecified generation settings use that installed
product's defaults. This is a native product command, not a shared audio operation
or managed job, and its availability is independent of shared SA3 inpainting.

Default WAVs go under `<project>/artifacts/matter-audio/score/generation/<unique-run>/`
and records into that run's `records/`. `--out` and `--record-root` accept explicit
locations, resolving relative values against the caller's cwd before switching to
the tool checkout. Existing output is never replaced. Legacy configured workspaces
do not choose generation output. Raw candidates and project listening decisions
remain distinct; no automatic playback, session import, game binding or acceptance
follows generation. Existing records are not relocated.

For native generation timeout or uncertain completion, inspect the exact WAV,
generation records and process state before any retry; there is no shared job ID to
query. Preserve a successful WAV even if writing its optional record failed.
