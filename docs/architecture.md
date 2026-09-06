# Architecture and roadmap

The core turns explicit local audio requests into verified, immutable results.
It is independent of the host language model and of product-specific generation
or recording pipelines.

## Components

| Module | Responsibility |
| --- | --- |
| `contracts` | Strict JSON parsing, schemas, canonical JSON and digests |
| `media` | PCM16 validation, encoding, levels, Q24 gain and slicing |
| `artifacts` | Stable input reads, product-scoped workspaces, complete groups and request claims |
| `actions` | Versioned registry, parameter resolution and operation execution |
| `cli` | Shared argument parsing, JSON responses and product extension hooks |
| `session_contracts` | Strict versioned session and feedback requests |
| `session_db` | SQLite ownership, schema migrations and atomic transactions |
| `sessions` | Version selection, branching, feedback and bounded resume context |
| `job_contracts` / `jobs` | Frozen job requests, attempts, recovery, guarded selection and partial batches |
| `execution` | Local worker locks and cooperative CPU checkpoints |

An adapter can add an `Operation` to a `Registry` and pass it to `cli.run` or
`ActionService`. It owns any decoding, rights registration or model dependency.
The built-in registry exposes `inspect/v1`, `gain/v1` and `trim/v1` without
loading an adapter. Inspect its parameter schemas through `capabilities`.

## Request lifecycle

1. Import a supported WAV to snapshot the input and obtain an asset ID.
2. Resolve a typed action against that snapshot. The resolution binds the
   operation profile, source hash and effective parameters into a digest.
3. Execute, optionally providing the preview digest. An exclusive request claim
   binds the request ID to its identity and result group.
4. Prepare and verify a complete group, then publish its directory without
   replacing an existing destination.
5. Return output asset IDs, measurements and playback paths. Completed retries
   read the original group; conflicting reuse of the request ID fails.

A request may finish with no audio output, such as a recorded inspection or a
failed gain operation. A claim without a complete group is `recovery_pending`;
the current implementation does not reclaim it automatically.

## Workspace

```text
workspace/
  workspace.json       # Schema and owning product
  sessions.sqlite3     # Optional, authoritative authoring state from core 0.2
  objects/<group-id>/  # Manifest, digest and complete output inventory
  requests/<id>/       # Immutable request claim
  .staging/            # Unpublished work owned by the store
  .job-locks/          # Stable OS-lock files; never delete while clients may run
```

Use a separate workspace for each product. Managed files are implementation
data; access them through the API/CLI and use returned playback paths for
listening. The storage format is early-stage and is not a general-purpose media
database or a security boundary. See [processing rules](pcm16-profile.md) and
[security assumptions](../SECURITY.md).

## Roadmap

The first increment (M1) contains the snapshot store, versioned deterministic
operations and CLI extension points. Core 0.2 implements the first M2 increment:
SQLite sessions, selection/history, branches, attributed feedback and context
queries. Audio actions create candidates; an explicit, revision-guarded selection
records the chosen output. See [persistent sessions](sessions.md).

Core 0.3 adds managed jobs and batch items in SQLite schema 2. A worker holds a
local OS lock across execution and result registration. Immutable audio publication
precedes the short registration/selection transaction. Recovery verifies already
published groups without rerunning the operation. Retries create separate attempts
only after a worker has stopped. See [jobs and recovery](jobs.md).

Remaining increments are:

- Protected PCM regions, generic fades and product-specific candidate preparation.
- A small listening interface and explicit selected-version export.
- Optional model-based editing and additional host transports.

These items are planned scope, not available commands or a release schedule.
BornAgent integration is deferred; Codex can use the local CLI entry points.
