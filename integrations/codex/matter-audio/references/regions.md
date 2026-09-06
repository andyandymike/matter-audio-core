# Preserve exact audio while editing

Use core 0.4 or later and check the installed operation schemas. Run commands
through the configured product launcher with its existing audio workspace.
Older databases return `session_migration_required`: stop active clients, preserve
a database backup when rollback matters, and run `session migrate` as part of
the authorized upgrade. All clients sharing that workspace need the new core.

Read `context show <session-id>` for the selected asset, current revision,
`constraints.policy` and `constraints.mapped_regions`. Explain the audio to
preserve, the change and the listening focus. If the user's range cannot be
inferred from their instruction or current context, clarify it before setting
a lock; do not invent an exact range for a subjective phrase.

Set locks with `constraints set --request <file>`:

```json
{
  "schema": "matter-constraints-set/v1",
  "request_id": "protect-intro",
  "session_id": "bgm-main",
  "expected_revision": 2,
  "regions": [{"start_seconds": 0, "end_seconds": 15}]
}
```

Use actual session/revision values. Each region accepts seconds or frames, with
an exclusive end. At most 16 nonempty, sorted, disjoint ranges may be inside the
selected audio. The response creates a new revision and records actual frames,
anchor asset and digests. Setting a policy replaces the whole list;
`regions: []` explicitly clears it. Do not clear/re-anchor locks to bypass a
rejected edit. Setting, replacing or clearing follows the user's preservation
intent; existing authorization does not need another confirmation.

For a continued edit, prefer [managed jobs](jobs.md): submission binds the current
policy, and input must match the selected asset when locks exist. Use the returned
current revision for any requested automatic selection. Generic `fade/v1` accepts
`fade_in_frames`/`fade_out_frames` or `_seconds` lengths; omitted lengths are zero,
units cannot mix, and lengths cannot overlap. `curve` only accepts `linear`.
Zero frames copy, one frame silences, and longer fades include 0/1 endpoints.
It uses its own Q24 profile; do not describe it as Sonic's fused Q15 operation.

Direct actions can also include:

```json
"protection": {"session_id": "bgm-main", "revision": 3}
```

Preview with `action resolve`, inspect effective frames, mapped ranges and write
ranges, then bind execution with `--expected-resolution-digest`. A protected
prefix permits tail trim/fade only outside its samples. Nonunity whole-file gain
will conflict. Unsupported product mappings are explicitly unavailable; choose
a compatible shared operation when it meets the user's intent.

`constraint_violation` rejects protected deletion/write or altered output PCM.
`constraint_conflict` means the policy changed: read current context and reconcile
the edit before submitting a new request. Never rerun unprotected to hide these
errors. Inspect a completed direct request with `action show`; policy changes can
prevent executing the old request again. Evidence proves the recorded policy,
while later selection rechecks the current policy and revision.

Ordinary selection retains the same anchor and checks the candidate's lineage
and protected samples. Leading trim can move inner regions; use mapped coordinates
for the next edit. Restore brings back both historical audio and historical
constraints, including clearing later locks when restoring an unlocked revision.
State this effect when carrying out a restore. Branches copy the source policy.

Present the actual returned playback WAV via an absolute-path audio embed.
Report `protection.status: verified` as exact PCM evidence only. Record the user's
actual listening feedback with its revision; never invent acceptance from hashes
or measurements. Two successful edits still need audition for subjective quality.
