# Managed jobs and batches

Use this route for recoverable execution of registered local operations. Query
`capabilities.jobs` first; older installations may not expose it. Existing core
0.2 workspaces need `session migrate` once after clients have stopped. Resume
with `context show <session>` and `job list --session <session>`.

`job submit --request <json>` takes:

```json
{
  "schema": "matter-job-submit/v1",
  "request_id": "submit-edit",
  "job_id": "edit",
  "session_id": "<existing-session>",
  "action": {
    "operation": "gain/v1",
    "inputs": ["<exact-asset-id>"],
    "parameters": {"db": -3}
  }
}
```

Then use `job run edit` and `job show edit`. Run is synchronous; it can be
queried or cancelled from another process. Jobs normally create candidates.
Only when selecting on completion is intended, include
`selection: {"expected_revision": <observed-head>, "output_index": 0}`.
Read the returned selection status: a successful late result may be retained
without selection because the session has advanced.

After interruption, use `job recover <id>`. A live worker returns `job_busy`.
Recovery verifies and registers complete results without executing audio; an
unfinished stopped attempt becomes `interrupted`. Never remove claims or lock
files to force recovery. Never execute a generated attempt ID separately.

To retry a confirmed failed/interrupted attempt, use `job retry --request <json>`:

```json
{
  "schema": "matter-job-retry/v1",
  "request_id": "retry-edit-1",
  "job_id": "edit",
  "expected_attempt": 1
}
```

Then run the job. Keep the same mutation request ID for an uncertain reply and
query its receipt with `session request <request-id>`. A retry creates a new
attempt but keeps the logical action unchanged. Explain deterministic failures;
do not keep retrying clipping or changed profiles. Changed parameters need a
new job. Retry only with a concrete reason, and report persistent failure.

Cancellation uses the same fields with `schema: matter-job-cancel/v1`, through
`job cancel --request <json>`. Treat `cancel_requested` as pending until `job show`
confirms the worker stopped. A result completed before cancellation was observed
can still succeed. A cancelled attempt is retried only when explicitly intended.

For up to 100 independent candidates, `batch submit --request <json>` accepts
`schema: matter-batch-submit/v1`, `request_id`, `batch_id`, `session_id`, and
`items: [{"job_id": "A", "action": <action-object>}, ...]`. Batch items create
candidates without selection. Use `batch run`, `batch show`, and `batch recover`
with the batch ID. They do not automatically retry failed items.

`batch retry` takes `schema: matter-batch-retry/v1`, `request_id`, `batch_id`,
and `items: [{"job_id": "B", "expected_attempt": 1}, ...]`. List only observed
failed/interrupted members; never include successful ones. Run the batch again
after retry registration. Human listening feedback remains separate from these
execution states.

Direct `action execute` remains available, with legacy incomplete claims still
reporting `recovery_pending`. Do not describe it as a managed job. Core 0.4 jobs
freeze the session's constraint policy automatically. Changed policies require a
new request; read [protected editing](regions.md). Model process cancellation and
game integration remain separate capabilities.
