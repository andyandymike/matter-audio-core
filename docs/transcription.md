# Optional external transcription

The separately installed `matter-audio-voicestudio` client adds speech
transcription and estimated word/segment timings without putting a service or
model dependency in Core. Its transcript and subtitle assets use the same
immutable store as Core, ScoreMatter and SonicMatter.

Install the Core release first, then install the client from the same checkout:

```sh
python -m pip install ./integrations/voicestudio
matter-voicestudio capabilities
matter-voicestudio --config /absolute/path/service.local.json doctor
```

Use the example configuration and complete request/command walkthrough in the
[client guide](https://github.com/andyandymike/matter-audio-core/blob/main/integrations/voicestudio/README.md).
The sequence is **resolve → explicitly execute → inspect transcript → export**.
Use `--product score` or `--product sonic` when opening a product-owned workspace.
The source must already be registered through that product's authoring tools.

Only `transcribe execute` uploads audio to the chosen endpoint. Preview, local doctor,
completed-result replay and export are offline. Secrets are read from a named
environment variable. The client does not install or preload remote models. Outputs are
JSON and optional SRT/VTT. Client 0.2.0 also supports [offline transcript editing](transcript-editing.md)
using those saved assets. It never writes feedback or selects a new version automatically.

The adapter preserves the source digest, request identity, service declaration,
provider-reported model, original timing estimates, converted frame coordinates
and warning states. Missing timestamps stay missing; ASR errors are not proof
of incorrect speech and ASR is not a music or Foley quality judge.

Remote inference counts remain unknown after dispatch. HTTP timeout or local
client cancellation does not confirm server cancellation. Failed receipts and
interrupted requests are never automatically resubmitted. Service/model
interoperability and actual speech quality require separate real-service tests;
the package's default test suite uses no models.

This optional client is independently implemented under MIT. It does not
incorporate VoiceStudio application code. The independently deployed service
and its models retain their respective licenses.
