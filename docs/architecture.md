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
  objects/<group-id>/  # Manifest, digest and complete output inventory
  requests/<id>/       # Immutable request claim
  .staging/            # Unpublished work owned by the store
```

Use a separate workspace for each product. Managed files are implementation
data; access them through the API/CLI and use returned playback paths for
listening. The storage format is early-stage and is not a general-purpose media
database or a security boundary. See [processing rules](pcm16-profile.md) and
[security assumptions](../SECURITY.md).

## Roadmap

The first increment (M1) contains the snapshot store, versioned deterministic
operations and CLI extension points. Potential later increments are:

- Session/revision storage, candidate selection and persistent listening feedback.
- Explicit recovery procedures for interrupted requests.
- Protected PCM regions, candidate batches and a small listening interface.
- Optional model-based editing and additional host transports.

These items are planned scope, not available commands or a release schedule.
BornAgent integration is deferred; Codex can use the local CLI entry points.
