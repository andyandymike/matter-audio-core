# Optional Codex integration

The core CLI works directly from a Codex workspace. For example, after installing
this package in the workspace environment, ask Codex to run
`python -m matter_audio_core capabilities --json`, then use an explicit audio
workspace to import and transform a user-selected WAV.

The bundled [matter-audio skill](../integrations/codex/matter-audio/SKILL.md)
provides a separate launcher for compatible SonicMatter and ScoreMatter product
checkouts. It forwards structured CLI arguments; it does not contain either
product, a model runtime or an audio service connection.

## Configure the product launcher

1. Install a compatible product checkout with its authoring entry point:
   `python -m score_matter audio` for ScoreMatter or
   `python -m tools.authoring` from a SonicMatter checkout. Install this core
   wheel into that product's Python environment. Confirm its `capabilities`
   command succeeds before configuring the launcher.
2. Copy the `integrations/codex/matter-audio/` folder into the skills directory
   used by your Codex installation, or invoke its script directly from this
   checkout while developing.
3. Copy `config.example.json` to `config.local.json` next to `SKILL.md`. Replace
   the example values with absolute paths to the product interpreter, checkout
   and a dedicated audio workspace. Configure only the products you use.
   `config.local.json` is ignored by Git and excluded from packages.
4. Run the launcher with the Python command from your environment:

```sh
python integrations/codex/matter-audio/scripts/run_audio.py --product score -- capabilities --json
```

Use the installed skill's path instead if you copied it. `--config` selects a
different configuration file and `--workspace` overrides the configured audio
workspace. On Windows, JSON paths can use `C:/projects/...` or escaped
backslashes. The example paths are placeholders, not automatic discovery.

For SonicMatter, an in-repository workspace must satisfy that product's
authoring safeguards (under its `artifacts` tree with `.gdignore`); an explicit
workspace outside the product checkout is another option. Use separate
workspaces for the two products.

The skill returns verified playback paths and measured changes. Persistent
editing sessions, listening feedback, region locks and automatic recovery are
not implemented in this version. Audio generation remains a separate product
capability; installing the core does not enable it.
