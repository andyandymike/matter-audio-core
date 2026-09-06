# Protected PCM editing

Core 0.4 adds exact PCM region locks and a generic fade operation. Locks are
immutable policies on session revisions, anchored to a specific selected asset.
They preserve samples and format; they do not establish listening quality.

## Set and inspect locks

Select the desired audio first. Save a request with the actual current revision:

```json
{
  "schema": "matter-constraints-set/v1",
  "request_id": "protect-intro",
  "session_id": "bgm-main",
  "expected_revision": 2,
  "regions": [{"start_seconds": 0, "end_seconds": 15}]
}
```

```sh
matter-audio --workspace <workspace> constraints set --request protect.json
matter-audio --workspace <workspace> constraints show bgm-main
matter-audio --workspace <workspace> context show bgm-main
```

Each range uses `start_frame`/`end_frame` or `start_seconds`/`end_seconds`.
Seconds resolve with Decimal half-up rounding. Ranges are half-open, nonempty,
ordered, disjoint and inside the selected audio, with at most 16 regions.
The response records the anchor digest/format, actual frames, a new constraint
ID and region digests. Each digest covers sample rate and channels (`<IH`
little-endian), followed by the region's interleaved PCM16 bytes.

Setting constraints appends a revision with the same audio and replaces the
whole policy. `regions: []` explicitly clears all locks, also appending a revision.
Identical mutation retries return the original receipt; a stale revision fails.
Use `session request <request-id>` after an uncertain reply.
`constraints show <session-id> --revision N` inspects history. Schemas are in
`capabilities.sessions.mutation_schemas.constraints`.

## Protected execution

Managed jobs and batches freeze the session policy automatically, including an
empty or absent policy. With locks present, input must be the selected audio at
that revision. Direct `matter-action/v1` requests can opt into the same check:

```json
{
  "schema": "matter-action/v1",
  "request_id": "fade-tail",
  "operation": "fade/v1",
  "inputs": ["<selected-asset-id>"],
  "parameters": {"fade_out_seconds": 0.3},
  "protection": {"session_id": "bgm-main", "revision": 3}
}
```

Use `action resolve --request <file>`, then
`action execute --request <file> --expected-resolution-digest <digest-hex>`.
Resolution returns policy, mapped ranges, write ranges and time mapping.
Deletion of locks, writes into them or an unsupported mapping fail before
publication. Inspection and unity gain preserve locks; nonunity whole-file gain
is rejected. Tail trim/fade must keep its deletions/writes outside the locks.

After processing, execution rechecks the policy and verifies actual output
format, frame count, mapping and protected PCM before adding audio to publication.
Findings record `pcm-region-sha256/v1` evidence against the frozen policy.
Changing or clearing that policy returns `constraint_conflict`; read context and
submit a new request. Query a completed direct action with `action show` if its
policy has since changed.

Policy rechecking and filesystem publication are not one transaction. A change
after the last check can leave a valid candidate for the frozen policy; later
selection always checks the current policy and expected revision. Evidence is
tied to the recorded policy, not all future session states. Late jobs under the
same policy may finish, but cannot overwrite a newer selection. Recovery can
register a published result under these same selection rules.

Direct actions without `protection` retain the standalone contract. They can
produce arbitrary candidates, but ordinary selection into a locked session
still verifies the locks. Do not resolve conflicts by silently clearing locks
or routing the same edit through an unprotected action.

## Mappings and history

Selection follows recorded `source` relationships to the anchor, checks asset
identities, projects each range and verifies the final protected samples.
Supported mappings are `identity` and exact contiguous `slice`. Leading trim
can move an inner lock: anchor `[200, 300)` becomes `[100, 200)` after removing
100 frames. Queries expose current and anchor coordinates. No stretch, resample,
mixing or ambiguous multi-parent mapping is inferred. Lineage is bounded to
128 edges; an explicit new policy can re-anchor a verified current selection.

Ordinary selection copies the policy unchanged. Restore brings back historical
audio and historical constraints, including an unlocked state when restoring a
revision made before locks existed. Branches copy the source policy and anchor.
These operations append records and never rewrite previous audio or constraints.

Product operations can opt in with `Operation.mapping` and `Operation.writes`
callbacks. Write ranges use input frame coordinates; execution observations must
match the declared mapping. Old extensions remain callable without locks and
report `pcm_region_protection: unavailable`. Sonic's fused Q15 recording operation
keeps its profile; shared trim/fade supports subsequent protected editing.

## Fade profile

`fade/v1` uses `pcm16-fade-linear-q24/v1` and preserves rate, channels and total
frames. Supply at least one of `fade_in_frames`/`fade_out_frames`, or corresponding
`_seconds` fields. Do not mix units. Omitted lengths are zero; `curve`, if supplied,
must be `linear`. The two fade lengths must fit without overlap.

For N fade frames, N=0 copies input and N=1 silences its single frame. For N>=2,
fade-in spans 0 to 1 inclusive and fade-out spans 1 to 0 inclusive. At offset i,
the Q24 factor is half-up rounded `2^24 * i / (N-1)` for fade-in, or
`2^24 * (N-1-i) / (N-1)` for fade-out. Multiply each signed sample and divide by
`2^24`, rounding nearest with ties away from zero. Channels share the factor;
unaffected PCM is copied exactly. The unity endpoint is excluded from declared
writes, so it may touch a lock. A one-frame fade does declare a write.
CPU cancellation checkpoints occur every 4096 fade frames and at action boundaries.
This is separate from Sonic's fused Q15 profile.

## Migration and verification

Stop active clients before upgrading; retain a database backup if rollback
matters. `session migrate` transactionally adds `constraints_json` in schema 3,
defaulting to JSON `null` on historical revisions. Audio, old receipts and frozen
job resolutions are not rewritten. Queries request migration without performing
it. Old core installations reject the newer schema.

Legacy jobs with no protection binding remain executable/retryable only while
the session has no nonempty locks. New jobs freeze the policy even before locks
exist, so adding one invalidates their execution and retry. Use a new request
after reconciling the edit with current constraints.

`python examples/regions_workflow.py` verifies four distinct candidate PCM
payloads, a prefix lock, two edits, rejected conflict, restore and branch through
fresh CLI processes. It accepts an existing WAV and configurable durations.
For a configured Skill, add `--launcher <run_audio.py> --product score`; for
decoded Sonic audio also use `--workspace <path> --asset-id <id>`.
Reports contain local playback paths and exact invariants. Audition separately;
the example makes no listening verdict.
