# Persistent authoring sessions

Core 0.2 adds session/revision storage and attributed feedback. Existing 0.1
asset workspaces remain readable; no audio is regenerated or rewritten to adopt
an existing asset. The SQLite database is created only when a session is created.

Try the complete workflow from an installed checkout:

```sh
python examples/session_workflow.py
```

The example uses a synthetic signal unless `--input <existing.wav>` is supplied.
Every CLI call starts a fresh process. It imports A, makes and selects a quieter
B, saves an explicitly attributed agent note, reopens the session, rejects a
stale selection, restores A, and branches from B. It does not invent user
feedback or listening acceptance. Outputs stay under `.local/sessions-demo/`.

## Commands and requests

Use the same explicit `--workspace` and product that own the audio assets.
Run `capabilities` to retrieve `sessions.mutation_schemas` for the installed
version. Mutations accept UTF-8 JSON through `--request`; unknown fields,
nonfinite values and incorrectly typed revisions are rejected.

| Command | Purpose |
| --- | --- |
| `session create --request <file>` | Start a named session, optionally selecting an existing asset |
| `session select --request <file>` | Select an asset or restore a historical revision |
| `session branch --request <file>` | Start a new session from an explicit historical revision |
| `session show <session-id>` | Current selection, verified playback path and newest revisions |
| `session list` | Discover saved sessions |
| `session request <request-id>` | Retrieve a committed state-mutation receipt |
| `feedback add --request <file>` | Attach verbatim feedback to an exact selected revision |
| `feedback list <session-id>` | Read attributed feedback, optionally filtered by `--revision` |
| `context show <session-id>` | Current selection, history, feedback, measurements and capabilities |

`session show`, `session list` and `feedback list` accept `--offset` (default 0)
and `--limit` (default 50, maximum 100). Follow `next_offset` for another page.
History is ordered by descending revision, feedback newest first, and the
session list by session ID. `context show` accepts `--history-limit` (default
10) and `--feedback-limit` (default 20), each limited to 100.

Create a session after importing a WAV:

```json
{
  "schema": "matter-session-create/v1",
  "request_id": "create-bgm",
  "session_id": "bgm-main",
  "name": "Opening BGM",
  "asset_id": "<actual imported asset ID>"
}
```

Save that as `create.json`, then run:

```sh
matter-audio --workspace .local/example session create --request create.json
matter-audio --workspace .local/example context show bgm-main
```

Omitting `asset_id` creates an empty revision 1. Feedback requires a revision
with selected audio. `session_id` and `request_id` are safe identifiers up to
96 characters, starting with a letter/digit and otherwise using letters,
digits, underscores, hyphens or periods.

## Select, restore and branch

Run an existing audio action to produce B, then select its returned asset ID:

```json
{
  "schema": "matter-session-select/v1",
  "request_id": "select-b",
  "session_id": "bgm-main",
  "expected_revision": 1,
  "asset_id": "<actual output asset ID>"
}
```

Executing this through `session select` appends revision 2. `expected_revision`
must equal the current head. A conflict returns `revision_conflict` with the
actual head and commits no selection, history or receipt. Read fresh context
and reconcile the intended change; do not blindly replace the expected value.
Audio actions remain independent and never select their output automatically.

To restore revision 1, use a new request ID, the current expected revision and
`"from_revision": 1` in place of `asset_id`. A restore appends a new revision
with the current head as its parent and records `restored_from_revision`.
It does not delete history or rewrite audio. Restoring an empty revision clears
the current selection through the same explicit history operation.

Create an independent branch with `session branch`:

```json
{
  "schema": "matter-session-branch/v1",
  "request_id": "branch-b",
  "session_id": "bgm-alternative",
  "name": "Alternative from B",
  "from_session": "bgm-main",
  "from_revision": 2
}
```

The new session starts at revision 1 with the source selection and an `origin`
reference. Its source session keeps its current head. Feedback stays attached
to its original session/revision; it is not copied or relabelled as a new
observation. `origin_feedback` in context references feedback on the immediate
branch-source revision, with its original identifiers. Earlier branch ancestry
can be inspected through the origin references explicitly.

## Feedback and context

```json
{
  "schema": "matter-feedback/v1",
  "request_id": "feedback-b",
  "session_id": "bgm-main",
  "revision": 2,
  "source": "agent_relay",
  "text": "<the user's actual words>",
  "listening_context": "<known conditions, or omit this field>"
}
```

Submit with `feedback add`. Text is preserved verbatim, including whitespace
and Unicode, with a 4000-character limit. Optional `listening_context` allows
2000 characters. Feedback targets a specific revision, including a historical
one, and includes its asset ID, media facts and digest. Adding feedback does not
move the selected revision.

| Source | Recorded kind | Meaning |
| --- | --- | --- |
| `agent_relay` | `user_observation` | Actual user words relayed by an agent |
| `user_cli` | `user_observation` | Direct user entry through a CLI |
| `user_ui` | `user_observation` | Direct user entry through an interface |
| `agent` | `agent_hypothesis` | The agent's own note or hypothesis |

Source attribution records the caller's declaration; it does not authenticate
the speaker or establish listening acceptance. Automated examples use agent
notes, and tests use explicitly synthetic feedback fixtures.

`context show` reads selection and feedback from one database snapshot, then
verifies the selected immutable asset and measures it. `feedback` contains
recent session feedback; `current_feedback` retrieves feedback on the currently
selected asset even if that feedback belongs to an older revision restored
later. Each item keeps its exact revision and attribution. Measurements remain
separate from feedback. Additional pages are available through the query CLI.
Region constraints explicitly report unavailable. Core 0.3 adds managed jobs to
resume context, with unfinished jobs first and a continuation query. See [jobs](jobs.md).

## Persistence and retries

The workspace contains `sessions.sqlite3` alongside the existing asset store.
It is the authority for session heads, immutable revisions, feedback and
state-mutation receipts. JSON responses are views/receipts, not a second editable
current-state file. Asset bytes and their immutable manifests stay in the
existing `objects`/`requests` storage.

Schema version 1 is initialized transactionally using an application ID and
`PRAGMA user_version`. Known future migrations use the same transaction path;
newer or unrelated schemas are rejected without adoption. Queries do not create
a missing database, perform migrations or append application records. SQLite
may roll back an interrupted, uncommitted transaction when reopening the file,
so the workspace must remain writable even for queries. Connections are closed
after each call; write locks wait at most five seconds before `session_busy`.

Each successful state mutation and its receipt commit in one transaction. An
identical retry returns the original receipt even after the session has advanced.
Changing a committed request's parameters returns `request_conflict`. A failed
validation/transaction has no committed receipt and no partial state. Session
mutation IDs share one workspace-wide namespace separate from audio action IDs.
After a timeout, use `session request` for state mutations and `action show` for
audio actions. Use `context show` to learn the current state, since a replayed
receipt describes the original mutation, not the current head.

The original session capability was the first M2 increment. Core 0.3 adds job
registration/recovery and batch management through an explicit schema-2 migration;
run `session migrate` on an existing 0.2 database before using the new core.
PCM region locks, fades, a comparison UI and selected-version export remain later
work. SQLite transaction rollback alone does not recover an unfinished audio
action or establish full power-loss durability.
