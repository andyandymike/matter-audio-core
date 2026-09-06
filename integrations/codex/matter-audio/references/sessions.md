# Continued authoring with core 0.2

Query `capabilities` first. The `sessions.mutation_schemas` field is the installed
contract; only use session commands when that capability is available. Pass
requests as UTF-8 JSON files through the configured launcher, keeping the same
product and workspace that own the assets.

Create a session around an existing asset:

```json
{
  "schema": "matter-session-create/v1",
  "request_id": "create-bgm",
  "session_id": "bgm-main",
  "name": "BGM revision",
  "asset_id": "<actual imported asset ID>"
}
```

Run `session create --request <file>`. `asset_id` is optional for an initially
empty session. Resume with `context show bgm-main`: it returns the current
revision, selected asset, recent history, attributed feedback, actual
measurements and installed audio operations. `current_feedback` specifically
retrieves comments on the currently selected asset, including after a restore.
Feedback about other versions must not be relabelled as feedback on this one.

After producing a candidate with an existing audio action, select it explicitly:

```json
{
  "schema": "matter-session-select/v1",
  "request_id": "select-b",
  "session_id": "bgm-main",
  "expected_revision": 1,
  "asset_id": "<actual candidate asset ID>"
}
```

Run `session select --request <file>`. Substitute the revision observed before
the intended selection. On `revision_conflict`, read fresh context and reconcile
with the user's current intent; do not simply replace the expected revision to
force a stale selection. To restore, replace `asset_id` with `from_revision`.
Restoring creates a new revision while keeping prior history.

`session branch --request <file>` takes schema `matter-session-branch/v1`, a
request ID, a new `session_id`/`name`, and explicit `from_session`/`from_revision`.
It leaves the source session unchanged. `origin_feedback` references comments
on the branch's source revision, keeping their original session and attribution.

Record actual conversation feedback using `feedback add --request <file>`:

```json
{
  "schema": "matter-feedback/v1",
  "request_id": "feedback-b",
  "session_id": "bgm-main",
  "revision": 2,
  "source": "agent_relay",
  "text": "<verbatim user feedback>",
  "listening_context": "<known listening conditions, or omit this field>"
}
```

For the agent's own notes use `source: agent`; these remain `agent_hypothesis`.
`user_cli` and `user_ui` describe direct user entry through those interfaces.
Attribution records the declared source and does not authenticate it or prove
anyone listened. Do not invent the listening conditions or a user verdict.

Every state mutation requires its own request ID. Reuse it for an identical
retry. `session request <request-id>` retrieves the committed mutation receipt;
`context show` retrieves current state, which may have advanced since that
receipt. Session request IDs occupy a separate namespace from audio action IDs.
On a timeout, query the correct namespace before retrying.

Use `session list`, `session show`, and `feedback list` with `--offset` and
`--limit` for additional pages. Feedback can also be filtered by `--revision`.
The store is project-local SQLite; it does not depend on chat history or Codex
global memory. Region constraints and audio job recovery are not implemented.
