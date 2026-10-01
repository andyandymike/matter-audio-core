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
Paths inside request JSON follow the operation schema; use explicit absolute
paths when the schema accepts file locations.

For SonicMatter, an in-repository workspace must satisfy that product's
authoring safeguards (under its `artifacts` tree with `.gdignore`); an explicit
workspace outside the product checkout is another option. Use separate
workspaces for the two products.

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
