# Transcript editing and task evaluation

The optional `matter-audio-voicestudio` client 0.2.0 converts a selection in an
existing transcript into a saved Core edit plan. Core remains at 0.7.0. No
VoiceStudio service, credentials, model or network is needed for this workflow.
The transcript must already have a successful `voicestudio.transcribe/v1`
receipt in the same workspace. Arbitrary imported text is not timing evidence.

## Inspect the exact transcript

```sh
matter-voicestudio --workspace /absolute/workspace transcribe inspect dialogue-transcription-001 --unit word --offset 0 --limit 50
```

The result supplies exact transcript/source asset IDs, indexed entries, original
provider seconds, converted frames and timing warnings. Use `--unit segment` for
segment entries. Pagination offsets are zero-based; entry indices and text
occurrences are **one-based**. Follow `next_offset` until it is null when more
entries are needed. Each page accepts at most 128 entries and can shrink further
to fit the 1 MiB response bound. Read-only inspection does not initialize a missing
workspace.

Use `--product score` or `--product sonic` for an existing product-owned
workspace. Import sources and replacement WAVs through that product's established
workflow. The adapter does not bypass product registration requirements.

## Save and inspect a plan

This example extracts entries 4 through 9, including both endpoints:

```json
{
  "schema": "matter-transcript-edit/v1",
  "request_id": "dialogue-extract-001",
  "transcription_request_id": "dialogue-transcription-001",
  "transcript_asset_id": "<exact-transcript-asset-id>",
  "source_asset_id": "<exact-source-asset-id>",
  "selection": {"unit": "word", "first_index": 4, "last_index": 9},
  "edit": {"kind": "extract"}
}
```

```sh
matter-voicestudio --workspace /absolute/workspace edit plan --request edit.json
matter-voicestudio --workspace /absolute/workspace edit show <plan-asset-id>
matter-voicestudio --workspace /absolute/workspace edit execute <plan-asset-id> --expected-plan-digest <plan_digest>
```

Inspect the returned selection, PCM range, source identities, overlapping entries,
resolved Core request and protection before executing. The required plan digest
is the saved plan asset's SHA-256 hex, also returned as `plan_digest`.

Other exact selectors are `{"unit":"word","index":4}` and
`{"unit":"word","text":"again","occurrence":2}`. Text matching operates on
whole entries, normalizing Unicode NFC and whitespace while preserving case and
punctuation. It performs no fuzzy matching or sentence inference. Repeated text
requires an explicit occurrence; missing or zero-duration timings are rejected.
An index range contains at most 128 entries. Sentence extraction requires choosing
the actual corresponding word range or an existing segment.

The PCM envelope uses the minimum start and maximum end of all selected entries;
it is **end-exclusive**. Optional `"padding_frames":{"before":80,"after":80}`
extends it explicitly. Padding that exceeds the source fails rather than being
clipped. Overlap with unselected entries is reported and can include neighboring
speech. Source and transcript identities are exact: another asset with identical
bytes is still a different binding. The original receipt, source facts, transcript
parents and normalized timing coordinates must agree.

## Replace a selected span

Replace the example's edit object with:

```json
{
  "kind": "replace",
  "replacement_asset_id": "<registered-replacement-audio-id>",
  "replacement_start_frame": 0,
  "transition_frames": 64
}
```

The replacement must have the same sample rate and channel count as the source,
and enough frames from its explicit start to fill the entire selected range.
Core `splice/v1` preserves total duration and every sample outside that range.
Transitions remain inside it and must fit Core's resolved bounds. Defaults are
zero replacement offset and zero transition frames. This workflow neither
generates replacement speech nor deletes a word and closes the resulting gap.

If the session has protected regions, include its explicit `protection` reference
in the edit request using the schema returned by Core capabilities. Planning and
new execution enforce Core's current protection and selection rules. Omitting a
reference does not implicitly discover or apply session locks. Do not omit it to
work around a requested preservation constraint.

`edit show` remains readable after locks change. Before a new execution, a stale
resolution or conflicting protection fails. An already completed execution returns
its saved success or failure even if the session has since changed; a pending
execution stays pending. Reusing a plan request ID with changed intent conflicts.
There are no automatic retries or silent new request IDs.

Execution publishes a new immutable audio asset and returns the plan reference.
The saved plan retains the semantic transcript evidence, while the Core action
receipt retains its PCM operation and source parents. Keep both records. Inspect
the result with `quality` or save a `quality/v1` report, then use existing session
selection and export commands explicitly when requested.

## What this verifies

All edit coordinates remain **model estimates**, including imported provider word
timings. A valid plan proves a local source relationship and a deterministic PCM
operation. It does not prove ASR correctness, acoustic word boundaries, natural
speech, model provenance, listening approval or rights clearance. Audio-model and
network calls for inspection, planning and editing are zero.

## Evaluate complete Agent tasks

The source distribution includes a small synthetic task suite. It exercises
transcript inspection, exact selection, plan/execute, protected replacement,
durable quality reports, session selection and exact-version export, plus expected
rejections. It does not require private recordings or external inference.

From the Core checkout with Core and the optional client installed:

```sh
# Deterministic reference driver: fresh CLI processes, no LLM.
python integrations/voicestudio/tools/evaluate_editing.py --output-dir /absolute/new-baseline

# Prepare inputs and public tasks.json for an Agent to solve.
python integrations/voicestudio/tools/evaluate_editing.py --prepare-only --output-dir /absolute/new-agent-run

# After the Agent finishes, inspect existing artifacts without performing its work.
python integrations/voicestudio/tools/evaluate_editing.py --grade-existing --output-dir /absolute/new-agent-run
```

Use `--case extract_sentence` to prepare one case. Baseline and prepare require a
new output directory. Each task states the exact asset, session and required
request IDs. Keep the grading implementation out of the Agent's task context
when measuring task completion.

The grader independently checks saved plan/action/report records, PCM bytes,
preserved source/locks, session selection and export content. Artifact-only
grading cannot prove an unrecorded read-only inspection or transient refusal;
missing evidence is **unknown**, not success. It cannot infer Agent tool count or
task duration from files. Baseline CLI counts and timings describe only the
scripted driver's fresh processes. Keep those results separate from actual Agent
runs, external-model interoperability and human listening acceptance. This small
suite is a workflow regression check, not a general benchmark of language-model
audio skill.
