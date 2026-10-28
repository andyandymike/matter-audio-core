# Diagnostics, saved proposals and optional transcription

## Offline diagnosis

Use `run_audio.py --product core|score|sonic -- doctor --json` to inspect the
configured environment. The launcher starts the configured product CLI; doctor
itself creates no workspace, loads no model, starts no probe subprocess and
contacts no server. Availability checks do not prove that
a model is loaded or running on a particular device; those states remain unknown.
Report individual checks and their remedies rather than interpreting `succeeded`
as proof that every optional backend is available.
The Score launcher's configured `sa3_runtime_root` selects the same runtime for
diagnosis and shared audio actions. A missing configured directory is reported
by doctor without installation. The override is local to that child process.

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

## Offline transcript-guided editing

With client 0.2.0 installed in the configured Core Python, use
`<core-python> -m matter_audio_voicestudio --workspace <project-workspace>`.
This is a separate CLI; `run_audio.py` does not route adapter subcommands. Add
`--product score|sonic` for an existing product workspace. These commands need no
service configuration and send no audio:

```text
transcribe inspect <transcription-request-id> --unit word --offset 0 --limit 50
edit plan --request <edit.json>
edit show <plan-asset-id>
edit execute <plan-asset-id> --expected-plan-digest <plan_digest>
```

Query `capabilities.editing.request_schema`. Read the Core checkout's
`docs/transcript-editing.md` for full examples. Bind the exact successful
transcription request, transcript asset and source asset. Select a one-based
entry index, inclusive index range or exact whole-entry text with its one-based
occurrence. Inspection pagination offsets are zero-based; follow `next_offset`
for more entries. Missing timings or ambiguous repeated text require a more
explicit selection, never guessed coordinates. Inspect the minimum-start to
maximum-end PCM envelope, padding and overlap warnings before executing.

Extraction uses Core trim; replacement uses duration-preserving splice with a
registered, format-compatible replacement and explicit offset/transition frames.
Neither generates replacement speech or closes a deletion gap. Include the current
session's explicit protection reference when preservation is required. Check the
new output, then select/export only as requested. The saved plan carries transcript
evidence; keep its ID with the Core execution receipt. Completed actions replay,
pending actions stay pending, and changed protection can reject a new execution.

Provider word boundaries remain unverified estimates. Do not describe an exact
PCM edit as proof of correct pronunciation or a natural speech join. Respect
deferred listening. The source distribution's `tools/evaluate_editing.py` can
prepare synthetic tasks and grade saved artifacts separately from its scripted
baseline; baseline success is not an actual LLM evaluation.
