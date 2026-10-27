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

Only execution uploads audio to the chosen endpoint. Preview, local doctor,
completed-result replay and export are offline. Secrets are read from a named
environment variable. No model is installed or selected remotely. Outputs are
JSON and optional SRT/VTT, not modified audio, feedback, or a new selection.

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
