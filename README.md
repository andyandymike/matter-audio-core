# Matter Audio Core

[![Tests](https://github.com/andyandymike/matter-audio-core/actions/workflows/tests.yml/badge.svg)](https://github.com/andyandymike/matter-audio-core/actions/workflows/tests.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

[简体中文](README.zh-CN.md)

Local audio authoring primitives for tools and coding agents: import a WAV,
inspect its levels, apply gain, trim by frames or seconds, and keep a verifiable
record of every result. Commands return JSON and paths to playable WAV files.

The core runs independently and provides shared operations for SonicMatter and
ScoreMatter adapters. It does not require a GPU, model weights, API keys or an
audio generation service. The optional [Codex integration](docs/codex.md) uses
the standalone core or configured product CLIs.

**Status:** `0.3.0`, early development. Windows and Linux, Python 3.10+ with
standard-library SQLite support. Persistent sessions now include managed jobs,
explicit crash recovery, partial batch retries and cooperative CPU cancellation.
macOS publication and generative editing are not implemented.

## Quick start

Install from this repository; this project is not currently distributed through
PyPI. Use a virtual environment:

```sh
git clone https://github.com/andyandymike/matter-audio-core.git
cd matter-audio-core
python -m venv .venv
```

Activate it with `.venv\Scripts\activate.bat` on Windows Command Prompt,
`.\.venv\Scripts\Activate.ps1` on PowerShell, or `source .venv/bin/activate`
on Linux. On Linux, your Python command may be `python3` before activation.
PowerShell users can also run `.\.venv\Scripts\python.exe` directly without
changing their execution policy.

```sh
python -m pip install .
matter-audio capabilities --json
python examples/quickstart.py
```

The example creates a quiet synthetic test signal in a new `.local/demo/`
directory, imports it, reduces gain by 3 dB, trims it, and verifies a repeated
request returns its original result. Its JSON summary includes absolute paths
to the source and output WAVs. It needs no external recordings or product
checkouts.

## Work with an existing WAV

Inputs must be uncompressed, signed PCM16 WAV, mono or stereo, 8–192 kHz,
nonempty and no larger than 64 MiB. Other formats need an explicit decoder
adapter. Use a dedicated local workspace; do not edit its managed files.

```sh
matter-audio --workspace .local/example assets import source.wav --request-id import-001
matter-audio --workspace .local/example assets list
```

Copy the returned asset ID into `gain.json`:

```json
{
  "schema": "matter-action/v1",
  "request_id": "gain-001",
  "operation": "gain/v1",
  "inputs": ["<asset-id-from-import>"],
  "parameters": {"db": -3}
}
```

```sh
matter-audio --workspace .local/example action resolve --request gain.json
matter-audio --workspace .local/example action execute --request gain.json
matter-audio --workspace .local/example action show gain-001
```

`resolve` previews exact parameters and a digest without publishing a result.
Pass that digest's `hex` value to `execute --expected-resolution-digest` to bind
execution to the preview. `playback` in the result contains absolute WAV paths.
Successful execution is not a listening-quality judgment.

| Operation | Parameters | Result |
| --- | --- | --- |
| `inspect/v1` | Optional `window_frames`, `offset`, `limit` | Peak/RMS, per-channel levels and paged windows |
| `gain/v1` | `db`, optional `clip: reject` or `saturate` | PCM16 WAV with defined Q24 rounding |
| `trim/v1` | `start_frame` + `end_frame`, or `start_seconds` + `end_seconds` | Exact slice; end is exclusive |

`matter-audio --workspace .local/example inspect <asset-id>` performs an
inspection without creating an action record. `python -m matter_audio_core`
is equivalent to `matter-audio`. Run `--help` on any command for its options.
Machine responses are one JSON object on stdout; failures return exit code 2.

## Recorded actions

- Imports snapshot source bytes; subsequent edits do not change the original.
- Results include source relationships, file hashes, media facts and profiles.
- Complete result directories are published atomically without replacing an
  existing result. Readers check the inventory and hashes.
- Reusing an identical request ID and request returns the original result.
  Changing the request under that ID fails with a conflict.
- `recovery_pending` means a request is running or was interrupted. Query it
  with `action show`; this version does not automatically rerun or recover it.

## Continue an authoring session

```sh
python examples/session_workflow.py
```

This example makes two versions, saves a selection and an agent note, resumes
through fresh CLI processes, restores the original, and branches from the
alternative. It verifies stale selections are rejected and original bytes are
preserved. Use `--input <existing.wav>` to try it with your own audio.

`session create / select / branch` save durable state in SQLite; `feedback add`
binds attributed text to a revision. `context show <session-id>` retrieves the
selected asset, history, relevant feedback and measurements. Audio actions
produce candidates; selecting one is an explicit operation guarded by the
expected revision. See [session requests and persistence](docs/sessions.md).

## Recover jobs and retry a batch

```sh
python examples/jobs_workflow.py
```

This four-candidate example injects one failed write and one process exit, then
recovers the published result and retries only the failed candidate. Successful
items retain their original results. `job submit / run / show / recover / cancel / retry`
and `batch submit / run / show / recover / retry` expose the same workflow.
Existing 0.2 databases need `session migrate` before use with 0.3.
See [job requests, cancellation and recovery](docs/jobs.md).

See [architecture and roadmap](docs/architecture.md),
[PCM16 processing rules](docs/pcm16-profile.md), and
[validation and platform boundaries](docs/validation.md).

## Development

```sh
python -m pip install -e ".[dev]"
python -m unittest discover -s tests -v
python -m build
python -m twine check --strict dist/*
python tools/check_distribution.py dist
```

The [contribution guide](CONTRIBUTING.md) describes the same checks used in CI.
Tests generate their own PCM fixtures; recordings, model weights, local
configuration and workspaces are excluded from source control and packages.

## License

[MIT](LICENSE), copyright 2026 Born Lu and contributors. The software license
does not grant rights to audio imported by users; those assets retain their
own licenses.
