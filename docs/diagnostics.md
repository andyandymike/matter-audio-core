# Read-only backend diagnostics

```sh
matter-audio doctor --json
```

`doctor` needs no workspace. It reads local runtime information and any product
provider's static checks. It does not create files, connect to the network,
download dependencies, launch subprocesses, decode audio or run a model. Passing
`--workspace` does not open, validate or repair that directory.

The `matter-diagnostics/v1` report contains Python, platform and core versions,
the supported PCM16 WAV limits, a separate core report, and optional backend
reports. `status: succeeded` means the report was produced; inspect
`summary.status` and each backend's `availability` for problems. Missing an
optional decoder or model does not make the core unavailable.

Each check has a category (`configuration`, `dependency`, `file` or `device`),
a status (`ok`, `warning`, `error` or `unknown`), a structured reason, and an
optional remedy. Remedies are instructions only; doctor never applies them.
An unavailable prerequisite makes its backend unavailable. Unknown or warning
prerequisites leave availability unknown. Device observations do not determine
static availability.

`configured` means the provider found its configuration. `available` means its
static prerequisites passed. Neither establishes that a native library can load,
that inference will succeed, or that audio will sound suitable. `loaded` and
`execution_device` remain `unknown`; a configured GPU is not evidence that a model
ran on it. This command has no inference or device-probing mode.

## Product providers

Product packages own their optional runtime checks. The public Python helpers
build and validate the shared shape without importing those runtimes:

```python
from matter_audio_core.diagnostics import backend_report, build_report, check

checks = [check("decoder", "dependency", "error", "decoder_missing",
                "The optional decoder is not installed.",
                remedy="Install the product's pinned authoring requirements.")]
provider_reports = [backend_report("product.decoder", configured=True,
                                   optional=True, checks=checks)]
report = build_report("product", provider_reports)
```

Pass a zero-argument provider returning a list of backend reports to
`cli.run(diagnostic_extra=provider)`. It runs only for `doctor`. Providers must
remain read-only and bounded: inspect distribution metadata, configuration
presence, file sizes or small driver/manifest files; do not hash whole model
weights, invoke health endpoints, load optional binaries, repair caches or spawn
processes. A missing optional installation should produce checks, not an import
failure.

Messages and remedies must be static public text. Never include raw exception
messages, environment/configuration values, API keys or local paths. `details`
accepts bounded versions, identifiers, hashes, counts and flags; paths, URLs,
secret-related fields and nested configuration dumps are rejected. These helpers
validate report structure; trusted product providers remain responsible for the
truth and privacy of their observations. Hostname, environment variables and
full filesystem paths are not part of the core report.

The report contains no persistent model inventory, unload operation, audio
quality score, licence approval or permission to download or generate audio.
