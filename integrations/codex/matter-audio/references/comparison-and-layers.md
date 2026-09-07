# Comparison, layering and local model edits

Query the configured product's capabilities before using core 0.5 commands. Old
workspaces need `session migrate` to schema 4. Keep the product/workspace fixed.

For an existing session, create a comparison with `audition create --request`:
`schema: matter-audition-create/v1`, request/audition/session IDs, expected revision,
name, reference asset ID and candidates `[{label, asset_id}]`. Use exact returned
asset IDs. Read saved sets through `audition list --session <id>`, `audition show`
or session context. `audition serve <id>` prints the complete loopback URL and
access token; keep the service local and stop only the owned server when finished.
Opening it does not play audio. Do not force playback when the user deferred it.

The page supports selected-frame playback, repeated/looped segments, A/B switching
and optional whole-file RMS matching through attenuation only. Candidate preview
is separate from saved selection. UI feedback binds the saved revision, not the
preview. Never submit an agent-generated judgment as `user_ui` feedback.
Restoring a historical version also restores its historical lock policy.

`export create --request` takes `schema: matter-export/v1`, request/session IDs
and the expected current revision. It exports the exact saved WAV bytes. Reuse
its request ID after an uncertain response; `export show <request-id>` verifies
the immutable receipt and delivery path. A later selection does not rewrite it.

`mix/v1` takes a fixed base and up to 15 layer inputs. Each additional input has
one `layers` entry with input index, source start/end frames and destination
offset. Optional per-layer db and fade lengths default to zero; base_db defaults
to zero and clipping defaults to reject. Formats must match. Resolve reports
the complete immutable recipe, every input digest and protected write ranges.

To adjust one independent layer, re-render the same immutable input recipe with
only that layer changed. Do not overlay a replacement on an already mixed WAV.
If locks still match, a direct action may bind the historical revision that
selected the recipe base; select its result using the current head revision.
Managed jobs bind the currently selected input automatically. Check that the two
renders match outside the changed layer's destination window.

`splice/v1` takes base/replacement inputs and start/end frames. Optional
replacement_start_frame defaults to start_frame; use zero for a short clip.
transition_frames defaults to zero. The two transitions stay inside the target
window, and the final timeline length is unchanged. Read `observed_changes`,
the outside-write count and protection verification before describing preservation.

ScoreMatter's optional `score.sa3_inpaint/v1` uses an existing configured local
SA3 runtime. It requires 44.1 kHz stereo PCM16, prompt, seed and an edit window;
query the live schema for settings. Prefer a managed job. Its raw model proposal
is a separate output role; select final output index 0 after successful assembly.
Inspect context_read, requested_edit, quantized model_mask, allowed_write,
transition and measured changes. Musical suitability remains unverified until
human listening. Do not reopen unrelated creative experiments to validate mechanics.

A cancellation request is pending until the worker confirms its owned process
tree stopped. Query `job show`, including every attempt's execution evidence.
Launched failed/cancelled attempts count as model calls. A null count with an
uncertain-launch count must not be reported as zero. Recovery does not rerun a
model; explicit retries need a concrete reason within the user's authorized scope.

Set the launcher's `--timeout` above the model deadline plus preflight/publication
time, for example `--timeout 900` for the default 600-second model deadline. Use a
long-lived owned background command for `audition serve`. A host timeout is an
uncertain execution state, not confirmed backend cancellation: query `job show`
and use its explicit cancellation API before deciding whether to retry.
