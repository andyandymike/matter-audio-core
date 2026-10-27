# Optional VoiceStudio transcription client

An independently implemented MIT client for a separately deployed VoiceStudio
HTTP service. It contains no VoiceStudio implementation, model runtime or
weights. Installing Matter Audio Core alone does not install or activate it.

The client uploads an explicitly selected, snapshotted PCM16 WAV only on
`transcribe execute`. It saves a source-bound transcript and, when segment
timings exist, SRT/VTT assets in the same Core, ScoreMatter or SonicMatter
workspace. It never selects audio, writes listening feedback or changes WAVs.

From the Matter Audio Core checkout, install Core and then this package into
the intended Python environment:

```sh
python -m pip install .
python -m pip install ./integrations/voicestudio
matter-voicestudio capabilities
matter-voicestudio --config /absolute/path/service.local.json doctor
```

Copy `config.example.json` to a local configuration and enter the endpoint,
model identifier accepted by your deployed service, and exact service release
or commit. The example's model is an API alias, not a weight fingerprint. Set
the named environment variable for authentication, or omit `api_key_env` only
for a service that intentionally needs no token. Secrets never belong in JSON
configuration or requests. HTTP is restricted to literal loopback addresses or
`localhost`; other endpoints require HTTPS. Redirects and system proxies are
not used. Configuration and doctor do not contact the server or download models.

Use `--product core` (default), `score`, or `sonic` to match the existing
workspace owner. Import audio with that product's normal tools first. For
Sonic, retain its registered-recording import workflow and workspace boundary.

Create a request with a real asset ID:

```json
{
  "schema": "matter-transcription-request/v1",
  "request_id": "dialogue-transcription-001",
  "asset_id": "<existing-audio-asset-id>",
  "timestamps": "word",
  "language": "en"
}
```

`timestamps` is `word` (default) or `segment`; omit `language` for service
autodetection. Only these documented fields are accepted. No LLM text cleanup,
speaker identification, translation, model installation or voice generation
is requested by this client.

```sh
matter-voicestudio --config service.local.json --workspace /absolute/workspace transcribe resolve --request request.json
matter-voicestudio --config service.local.json --workspace /absolute/workspace transcribe execute --request request.json --expected-resolution-digest <digest.hex-from-resolve>
matter-voicestudio --workspace /absolute/workspace transcribe show dialogue-transcription-001
matter-voicestudio --workspace /absolute/workspace transcribe export dialogue-transcription-001 --format srt --output /absolute/output/dialogue.srt
```

Preview binds the exact source digest, effective parameters, endpoint and
declared service identity. Execution requires that preview digest. Completed
identical requests replay their saved result without contacting the service;
changed requests under the same ID fail. JSON, SRT and VTT exports use the
saved asset bytes, refuse existing destinations and cannot write into the
managed workspace. Output parents must already exist.

## Evidence and failure boundaries

- The implementation targets the documented `/v1/audio/transcriptions` REST
  interface with `verbose_json`. It is not coupled to internal Python modules.
- Model and service identities in configuration are declarations. A returned
  `model` field is recorded separately; neither proves the actual checkpoint.
- Timestamps are estimates. Frame conversion is explicit, with original
  seconds retained. Missing word/segment timings produce warnings, never
  fabricated timing. SRT/VTT are only published when segments contain usable
  nonempty cues with positive duration after millisecond conversion.
  If all returned segments are unusable, `no_usable_subtitle_cues` preserves
  the transcript and explains why no subtitle asset was saved.
- Word alignment may silently fall back inside the service. Precision remains
  unverified; use the original audio to review transcript and edit boundaries.
- HTTP responses and normalized transcripts are limited to 1 MiB. Inputs retain Core's 64 MiB PCM16 WAV
  bound. Use shorter source assets for oversized transcripts.
  A normalized document exceeding that limit fails with `transcript_too_large`
  before publishing any output assets.
- There are no automatic retries. After dispatch, `audio_model_calls` is
  **null** because this API cannot attest server inference counts. A received
  response records one HTTP request, not one proven model invocation.
- HTTP errors, malformed responses and timeouts leave a durable failed receipt
  without partial transcript assets. Repeating that ID returns that receipt.
  A killed client can leave `recovery_pending` plus dispatch evidence. The
  service may still be processing; check it before explicitly making a new
  request. This adapter cannot confirm server cancellation or recover an
  unreturned result through an unavailable server job ID.
- Credentials and server error bodies are not persisted or echoed. Transcript
  text itself is private project data and is intentionally stored in the chosen
  workspace. No transcript or audio is bundled with the package.

Validation uses synthetic audio and a loopback HTTP fixture, including fresh
CLI processes, conflicts, redirects, bounded failures and exact subtitle
exports. It is not an installed VoiceStudio/model interoperability or listening
test. Run the tests without any configured model:

```sh
python -m unittest discover -s integrations/voicestudio/tests -v
```

## Licensing

This client is MIT, as stated in its `LICENSE`. VoiceStudio remains a separate
AGPL-3.0-only application; model weights and audio inputs retain their own
terms. An HTTP boundary is an architectural choice, not a blanket licensing
exemption. No upstream application code, tests or assets were copied into this
implementation. The service's public protocol is described in its
[API documentation](https://github.com/debpalash/VoiceStudio/blob/main/docs/production-private-api.md).
