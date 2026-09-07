# Your first local edit

Matter Audio Core imports an immutable source snapshot and publishes each edit
as a new audio asset. You can use the CLI directly or through a coding agent.

## Install

Use Python 3.10+ on Windows or Linux. Install from this repository; the core is
currently distributed as source and locally built wheels, not through PyPI.

```sh
git clone https://github.com/andyandymike/matter-audio-core.git
cd matter-audio-core
python -m venv .venv
```

=== "Windows PowerShell"

    ```powershell
    .venv\Scripts\Activate.ps1
    ```

=== "Linux"

    ```sh
    source .venv/bin/activate
    ```

Then install and run the synthetic example:

```sh
python -m pip install .
matter-audio capabilities --json
python examples/quickstart.py
```

The example creates a quiet synthetic PCM signal in a new `.local/demo/`
workspace, imports it, reduces its gain by 3 dB, trims it, and verifies that
repeating the same request returns the original result. It neither invokes a
model nor starts playback. Its output includes verified local WAV paths.

## Bring an existing WAV

Input must be uncompressed signed PCM16 WAV: mono or stereo, 8–192 kHz, nonempty
and no larger than 64 MiB. Other formats require a product decoder adapter.

```sh
matter-audio --workspace .local/my-audio assets import source.wav --request-id source-001 --json
```

Put the returned audio asset ID in `gain.json`:

```json
{
  "schema": "matter-action/v1",
  "request_id": "gain-001",
  "operation": "gain/v1",
  "inputs": ["ACTUAL_ASSET_ID"],
  "parameters": {"db": -3}
}
```

Resolve the effective parameters, execute, then query the saved result:

```sh
matter-audio --workspace .local/my-audio action resolve --request gain.json --json
matter-audio --workspace .local/my-audio action execute --request gain.json --json
matter-audio --workspace .local/my-audio action show gain-001 --json
```

Use the same request ID for a retry. A new ID means a new operation. Inspect
pending work before retrying, and keep managed workspace files immutable.
The returned `playback` paths identify audio you can audition when ready.

## Continue the workflow

- [Sessions](sessions.md) save selections, branches and attributed feedback.
- [Protected regions](regions.md) keep specified PCM samples unchanged.
- [Jobs](jobs.md) provide durable progress, cancellation and explicit recovery.
- [Comparison and export](audition.md) support saved versions and exact delivery.
- [Production authoring](production.md) adds loops, scenes, cue sets and search.
- [Product adapters](products.md) add registered recordings or configured local models.

For reproducible integration, use a reviewed commit and keep its built wheel.
Audio rights and listening acceptance remain separate from successful execution.
