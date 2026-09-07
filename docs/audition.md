# Comparison, feedback and delivery

Core 0.5 adds persistent comparison sets, an on-demand local browser page and
immutable export receipts. Run `session migrate` before using a schema 1–3
workspace; schema 4 adds the comparison table without changing existing audio.

## Prepare and open a comparison

Create a session and candidates first. Save this request as `compare.json`, using
actual asset IDs and the current revision returned by `context show`:

```json
{
  "schema": "matter-audition-create/v1",
  "request_id": "compare-tail-1",
  "audition_id": "tail",
  "session_id": "music",
  "expected_revision": 4,
  "name": "Tail revision",
  "reference_asset_id": "<original-asset-id>",
  "candidates": [
    {"label": "B", "asset_id": "<asset-B>"},
    {"label": "B2", "asset_id": "<asset-B2>"}
  ]
}
```

```text
matter-audio --workspace <workspace> audition create --request compare.json
matter-audio --workspace <workspace> audition show tail
matter-audio --workspace <workspace> audition list --session music
matter-audio --workspace <workspace> audition serve tail
```

The server prints a loopback URL containing an access token in its fragment. Open
the complete URL. Optional `--port` selects a port; `--ready-file <new.json>` saves
the ready result without overwriting a file. Stop the foreground service with
Ctrl+C. No service starts automatically and no audio plays on opening the page.
Product CLIs expose the same commands and product-scoped workspace.
`context show` includes saved comparison IDs when resuming a session.

The page provides raw PCM min/max waveforms, exact frame-range controls, segment
playback, four repetitions, looping and A/B switching. Playback range endpoints
are exclusive. Optional whole-file RMS matching attenuates the louder member of
the reference/candidate pair; it never changes the WAV. This is RMS matching,
not a perceptual loudness measurement. Browser decoding/output resampling is a
preview path, not the evidence used for exact PCM verification.

Clicking a candidate changes the preview. **Save current candidate** changes the
session using its observed revision. Export always uses the saved version.
Candidates that violate current PCM locks can be previewed but cannot be selected.
History restore restores both that revision's asset and its lock policy. Restoring
an earlier unlocked revision therefore also removes later locks.

Feedback is stored verbatim against the saved revision as `source: user_ui`.
Opening a page or saving a note does not prove that playback occurred. Agent
testing/notes must use the separately attributed `source: agent` API. Pending
mutations retain their request ID in browser session storage; refresh/reload
replays the same request after a lost response rather than creating a duplicate.
If browser storage is unavailable, saving fails before submitting the mutation.

## Export the exact saved version

```json
{
  "schema": "matter-export/v1",
  "request_id": "deliver-tail-4",
  "session_id": "music",
  "expected_revision": 4
}
```

Use `export create --request <file>` or the page's export button. The response
includes an absolute WAV path, selected-asset digest and complete revision
snapshot. `export show <request-id>` verifies the saved receipt and audio again.
The WAV is a byte-for-byte copy, including the original header; no re-encoding,
gain matching or normalization is applied. A later selection cannot rewrite it.
The same request replays its original delivery even after the session advances.
A new export request with a stale expected revision fails.

Deliveries live in `workspace/exports/<request-hash>/`. Publication is atomic and
does not replace an existing delivery. Browser download returns these verified
bytes. The CLI receipt remains available if the browser download is interrupted.

## Local boundary

The HTTP server binds only to `127.0.0.1`; it checks the Host, optional Origin and
API token. It exposes only this comparison, its saved session and scoped assets.
Mutation endpoints accept typed JSON, not paths or shell commands. Do not expose
the service through a proxy or treat it as a multi-user server. Local hostile
writers, power loss and durable browser storage across different server tokens
are outside this interface's guarantee.

`python examples/composition_workflow.py` creates a synthetic comparison and
verifies selection/export across fresh CLI processes without opening a browser.
