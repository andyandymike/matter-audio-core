# Diagnostics, saved proposals and optional transcription

## Offline diagnosis

Use `run_audio.py --product core|score|sonic -- doctor --json` to inspect the
configured environment. This does not create an audio workspace, load a model,
start a child process or contact a server. Availability checks do not prove that
a model is loaded or running on a particular device; those states remain unknown.
Report individual checks and their remedies rather than interpreting `succeeded`
as proof that every optional backend is available.

## Signal issue locations

Run `quality <asset-id> --profile generic|music|speech --json` against an exact
saved asset. The result includes frame ranges and seconds for clipping risk,
low-level regions and large adjacent-window level changes. Check scanned ranges,
truncation and omitted warnings before describing coverage. These are advisory
measurements; silence and dynamics may be intentional, especially in music.

For a durable report, create a normal `matter-action/v1` request using operation
`quality/v1`, one input asset and parameters from capabilities. This operation
changes no audio samples and makes no model calls. Never auto-trim silence or
normalize a flagged asset solely because it received a warning.

## Reassemble a saved ScoreMatter proposal

Check `product_capabilities.proposal_reassembly` in the installed ScoreMatter
entry point. Use an existing successful `score.sa3_inpaint/v1` request and its
exact `model_proposal` output. A reassembly request has schema
`score-sa3-reassembly/v1`, a new `request_id`, `source_request_id`,
`base_asset_id`, `proposal_asset_id`, and an `assembly` object containing
`transition_frames`. Optional `start_frame` and `end_frame` can narrow the
original writable range. Use the published product schema for protection.

```text
python <skill-dir>/scripts/run_audio.py --product score -- proposal plan --request reassembly.json --json
python <skill-dir>/scripts/run_audio.py --product score -- proposal show <plan-id> --json
python <skill-dir>/scripts/run_audio.py --product score -- proposal execute <plan-id> --json
```

Planning checks original evidence, identities, media and protection. Execution
uses the saved proposal and deterministic splice, with zero new model calls.
It cannot change the prompt, seed or generation settings. Inspect actual frames
and preservation evidence, then select explicitly through the existing session
workflow only when requested. Keep listening deferred when the user asks.

## Optional external transcription

The separately installed `matter-voicestudio` CLI is independent of this launcher.
Read its guide at `integrations/voicestudio/README.md` in the Core checkout and
query its capabilities. Choose the existing workspace and matching `--product`.
Use a registered speech asset; ASR is not a musical or Foley quality assessment.

The sequence is `transcribe resolve`, then `transcribe execute` with the returned
`--expected-resolution-digest`, then `transcribe show` and optional export.
Only execute sends audio to the configured service. Confirm that uploading the
selected audio is within the user's task; installation or diagnosis alone is
not a request to transmit it. Credentials live in a named environment variable.

Preserve source hashes, configured service identity, provider model declarations
and original timestamp estimates. Missing timings remain missing, and word
alignment precision remains unverified. SRT/VTT require usable segment timings.
Do not invent speakers, words or listening approval. After dispatch, remote model
call counts remain unknown. A timeout does not confirm server cancellation;
inspect the saved receipt instead of automatically creating a replacement request.

Our client is MIT. The separately deployed service and models retain their own
licenses; no upstream VoiceStudio application code is included in Core.
