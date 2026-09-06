# Contributing

Bug reports and focused pull requests are welcome. For a substantial feature,
open an issue describing the intended audio operation and its observable result.
Keep discussions respectful and give enough context for others to reproduce a
problem without private recordings or access to your machine.

## Set up and check a change

Use Python 3.10+ on Windows or Linux. Create and activate a virtual environment
as described in the [README](README.md), then run:

```sh
python -m pip install -e ".[dev]"
python -m unittest discover -s tests -v
python examples/quickstart.py
python examples/session_workflow.py
python -m build
python -m twine check --strict dist/*
python tools/check_distribution.py dist
git diff --check
```

When using PowerShell without activation, replace `python` with
`.\.venv\Scripts\python.exe`. To check archives without shell glob expansion:

```powershell
$packages = (Get-ChildItem dist -File | Where-Object { $_.Name -match '\.(whl|tar\.gz)$' }).FullName
python -m twine check --strict $packages
```

CI builds an sdist and a wheel from that sdist, checks their contents and
metadata, installs the wheel, runs the unit suite and the CLI example, and
checks the installed entry point. The matrix covers Windows/Linux with Python
3.10, 3.12 and 3.14. Product adapter tests are separate; this repository's CI
does not require sibling repositories or external audio.

Session changes also need meaningful transaction, revision-conflict or restart
coverage. Test feedback is synthetic; never present it as actual user listening
evidence. Keep SQLite databases and journal files out of source and packages.

## Changes to audio behavior

Prefer small synthetic PCM fixtures with exact expected samples. Cover the
behavior affected by your change, including overflow, invalid ranges or request
conflicts where relevant. A successful export or a level metric is not evidence
of subjective audio quality.

Version operation names, schemas or processing profiles when their semantics
change. Do not silently change a recorded profile's sample rounding or time
mapping. Product-specific decoders and model integrations belong in adapters;
keep the core independent of their imports and network credentials.

Use UTF-8, LF line endings and four spaces for Python. Avoid unrelated format
changes. Include the reason for the change, validation and any limitations in
the pull request. Update the changelog for user-visible changes.

## Files and rights

Keep machine configuration, `.env` files, recordings, generated audio, model
weights and workspaces out of commits. Tests and examples generate their own
fixtures. Sanitize absolute paths from reports before sharing them.

Contributions are provided under this repository's [MIT license](LICENSE).
Contribute only code and documentation you have the right to share. Report
security issues using [SECURITY.md](SECURITY.md).
