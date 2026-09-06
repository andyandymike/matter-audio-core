# Managed audio jobs and batches

Core 0.3 adds explicit job recovery, cooperative CPU cancellation and independently
retried batch items. It executes existing registered local operations. It does
not add a model, background daemon, GPU scheduler or new audio transform.

Try a complete example with an installed checkout:

```sh
python examples/jobs_workflow.py
```

The example uses four gain settings, injects one transient output-write failure
and one abrupt process exit after publication, then recovers and retries. Its
report verifies that successful items retain their results and attempt numbers.
An optional `--input <existing.wav>` uses an existing PCM16 WAV. Fault injection
belongs to the example subprocess only; it is not a production CLI option.

## Upgrade an existing workspace

Existing core 0.2 databases need schema version 2. First stop active clients and
keep a backup if rollback to an older installation matters, then run:

```sh
matter-audio --workspace <workspace> session migrate
```

Migration adds job/batch tables in one SQLite transaction. It does not change
existing audio, selections, feedback or receipts. Queries never migrate; they
return `session_migration_required` for an older database. Older core binaries
reject the upgraded database. Newly created sessions use the current schema.

## Submit, run and inspect

A session must already exist. Save this request as `submit.json`, substituting
the real asset ID:

```json
{
  "schema": "matter-job-submit/v1",
  "request_id": "submit-quieter",
  "job_id": "quieter",
  "session_id": "bgm-main",
  "action": {
    "operation": "gain/v1",
    "inputs": ["<asset-id>"],
    "parameters": {"db": -3}
  }
}
```

```sh
matter-audio --workspace <workspace> job submit --request submit.json
matter-audio --workspace <workspace> job run quieter
matter-audio --workspace <workspace> job show quieter
matter-audio --workspace <workspace> job list --session bgm-main
```

Submission resolves and freezes the input digests, operation profile and effective
parameters, then atomically queues the job. `run` executes synchronously in that
CLI process. Another process may inspect or cancel it. Repeated `run` on a
terminal job returns its state without executing. Failed jobs require an explicit
retry; repeated `run` does not retry them.

`job show` includes the current result, verified playback paths, current error
and attempt history with action request IDs. Use `action show <attempt-request-id>`
to inspect a published attempt. Attempt request IDs are allocated by the core;
do not execute them separately through `action execute`. `job show` and `job list`
accept `--offset` and `--limit` (1–100). Job lists and resume context put unfinished
work first, then sort by job ID. `context show` includes the first 20 jobs and a
continuation offset.

Submission/retry/cancel `request_id` values share the session mutation receipt
namespace. Use `session request <request-id>` after an uncertain mutation reply.
Exact retries return the original receipt even if the job has since advanced;
read `job show` for current state. Reusing an ID for different data conflicts.
Logical job IDs, batch IDs and generated audio attempt IDs have separate purposes.
All request schemas are available in `capabilities.jobs.mutation_schemas`.

## Recovery and retries

```sh
matter-audio --workspace <workspace> job recover quieter
```

Recovery first acquires the same local operating-system file lock held by the
worker. A live owner returns `job_busy`, without changing state or executing
audio. After a process exits, the OS releases its lock; no PID expiry, stale-lock
deletion or lease timeout authorizes another worker.

For a stopped worker:

| Stored state | Recovery behavior |
| --- | --- |
| Complete published result, registration missing | Verify the complete inventory and request binding, then register it without running the operation |
| No complete result | Mark the attempt `interrupted`, or `cancelled` when cancellation was requested; retain the old claim |
| Damaged complete result | Report the integrity error; keep recovery unresolved |
| Queued or terminal job | Return its existing state |

A retry starts a new, numbered attempt with a new audio request ID. Prior claims,
results and errors remain. Parameters cannot change under an existing job; changed
parameters require a new job. Changed input digests, profiles or resolved values
also conflict. A deterministic error such as clipping will fail again until a
new job changes the parameters.

```json
{
  "schema": "matter-job-retry/v1",
  "request_id": "retry-quieter-1",
  "job_id": "quieter",
  "expected_attempt": 1
}
```

Run `job retry --request <file>` followed by `job run quieter`. Retry accepts only
confirmed `failed`, `interrupted` or `cancelled` jobs and checks `expected_attempt`.
It cannot reclaim a running job. Recovery itself never reruns an operation.

## Cancellation and selection races

```json
{
  "schema": "matter-job-cancel/v1",
  "request_id": "cancel-quieter-1",
  "job_id": "quieter",
  "expected_attempt": 1
}
```

Submit through `job cancel --request <file>`. Queued jobs can be marked cancelled
immediately. Running jobs remain `cancel_requested` until the worker acknowledges
the request. Core gain and level inspection check between sample chunks; every
operation also checks at execution/publication boundaries. Product extensions
without internal checkpoints can stop only at those boundaries. Blocking I/O and
external process trees have no bounded cancellation latency here. Ctrl+C in a
managed worker also reconciles its current result before reporting a final state.

If a complete result wins a late cancellation race, the job can finish successfully;
its requested automatic selection is suppressed when cancellation is already
recorded. Cancellation does not delete complete candidates. Stale cancellation
requests cannot cancel a later attempt.

By default jobs only create candidates. A single job may explicitly request:

```json
"selection": {"expected_revision": 3, "output_index": 0}
```

Selection and result registration commit together. If the session advances while
the job runs, the result remains successful but `job.selection` records
`not_applied` / `revision_conflict`; it never overwrites the newer selection.
Select the candidate later with a fresh session revision if desired.

## Partial batches

`batch submit` accepts `matter-batch-submit/v1` with `request_id`, `batch_id`,
`session_id` and 1–100 `items`. Each item contains `job_id` and the same `action`
object used above. All items are registered atomically. Batch items do not
automatically select their outputs.

```sh
matter-audio --workspace <workspace> batch submit --request batch.json
matter-audio --workspace <workspace> batch run four
matter-audio --workspace <workspace> batch show four
matter-audio --workspace <workspace> batch recover four
```

`batch run` executes queued items sequentially, continues after a recorded item
failure and leaves completed items unchanged. `batch recover` reconciles stopped
workers and reports live ones in `busy_jobs`. Neither command retries failures.

```json
{
  "schema": "matter-batch-retry/v1",
  "request_id": "retry-failed-B",
  "batch_id": "four",
  "items": [{"job_id": "B", "expected_attempt": 1}]
}
```

Run `batch retry --request <file>` and then `batch run four`. Retry lists only
failed/interrupted members with their observed attempts. Successful members,
duplicate IDs and members of another batch are rejected atomically. Cancelled
items require an explicit individual `job retry`.

CLI exit code 2 signals `failed`, `interrupted`, `cancelled` or `partial_failure`,
as well as structured errors. Nonterminal states are query results, not proof of
completion. Inspect the JSON state and item counts.

## Boundaries

Only jobs created through the managed API are recoverable here. Existing direct
`action execute` requests retain their 0.1/0.2 behavior; incomplete legacy claims
are never reclaimed. Incomplete staging data is not published or reused by job
recovery. No automatic cleanup is performed.

Workspace locks must remain local, on a supported Windows/Linux filesystem.
Never remove `.job-locks` files while a client may be running. The implementation
uses Python's [Windows locking](https://docs.python.org/3/library/msvcrt.html#msvcrt.locking)
and [Unix flock](https://docs.python.org/3/library/fcntl.html#fcntl.flock) interfaces.
Network/synchronized workspaces, hostile writers and power-loss durability are
outside the verified boundary. Process-exit recovery does not prove these cases.
Workflow success is separate from human listening acceptance.
