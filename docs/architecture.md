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
| `execution` / `processes` | Worker locks, CPU checkpoints, owned backend trees and attempt evidence |
| `composition` | Multi-input layering, bounded replacement and measured write ranges |
| `audition` / `audition_server` | Persistent comparisons and a scoped loopback browser page |
| `delivery` | Exact selected-version WAV exports and immutable receipts |

An adapter can add an `Operation` to a `Registry` and pass it to `cli.run` or
`ActionService`. It owns any decoding, rights registration or model dependency.
The built-in registry exposes `inspect/v1`, `gain/v1`, `trim/v1`, `fade/v1`,
`mix/v1` and `splice/v1` without
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
  requests/<id>/       # Immutable request claim and optional execution journal
  exports/<id>/        # Exact selected WAV and immutable revision receipt
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

Core 0.4 adds immutable PCM lock policies on revisions (SQLite schema 3).
`regions.py` projects exact ranges through recorded identity/slice lineage;
`fades.py` implements a separate Q24 fade profile. Operation adapters optionally
declare mappings and write ranges. Protected resolution checks those declarations,
execution verifies actual output PCM, and selection verifies the chosen lineage.
See [protected edits](regions.md) for constraints, restore semantics and boundaries.

Core 0.5 completes the M2 comparison/export interface (SQLite schema 4) and adds
shared M3 layering, splice reports and owned model-process execution. Product
adapters own their domain integration: ScoreMatter supplies local SA3 inpainting;
SonicMatter supplies registered recording inputs for layering. Engineering checks,
real backend checks and human listening acceptance are separate evidence.
See [comparison](audition.md), [composition](composition.md) and
[model adapter contracts](model-adapters.md).

Remaining product work includes:

- Human listening and application/consumer acceptance of chosen audio.
- Additional host transports, model backends and optional game integration.

Additional transports/backends are planned scope, not available commands or a release schedule.
BornAgent integration is deferred; Codex can use the local CLI entry points.
