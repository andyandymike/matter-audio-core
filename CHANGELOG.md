# Changelog

## 0.6.0 — 2026-09-07

- Immutable cue/variant sets with revision-bound selections, sidecar loop metadata
  and atomic batch export of exact source WAV bytes.
- `normalize/v1` for bounded RMS/peak gain and `loop/v1` for explicit overlap
  construction, period accounting, region protection and seam measurements.
- `scene/v1` for finite event timelines, repeated clips, track/event fades and
  gains, single-round Q72 mixing and explicit overflow handling.
- `analyze/v1` and a local metadata/feature library with transparent numeric
  similarity contributions. No additional audio model or dependency.
- A production workflow example and numerical, snapshot, concurrency and
  delivery tests. SQLite remains at schema 4.

## 0.5.0 — 2026-09-07

- Persistent comparison sets and a loopback browser page for frame-range playback,
  repetition, looping, A/B comparison, preview RMS matching, selection and feedback.
- Exact selected-version WAV delivery with immutable, revision-bound export receipts.
- Multi-input `mix/v1`, bounded `splice/v1`, complete parent references and measured
  write-region reports. Existing single-input and Sonic fused-Q15 profiles retain
  their arithmetic.
- Optional model-adapter output/proposal contracts, owned process trees on
  Windows/Linux and durable launch/failure/cancellation evidence per attempt.
- SQLite schema 4 migration, controller/process-tree tests and an end-to-end
  layer/comparison/export example. Model weights remain outside the package.

## 0.4.0 — 2026-09-06

- Exact PCM region constraints anchored to immutable selected assets, with
  identity/slice projection through successive edits, restores and branches.
- Generic `fade/v1` with a distinct linear-amplitude Q24 profile, explicit
  endpoints, frame/second lengths and cooperative CPU cancellation.
- Resolve-time rejection of protected writes/deletions and verification of actual
  output samples before publication. Ordinary selection also verifies locks.
- Managed jobs freeze session constraints; changed policies require a new request.
  Historical jobs retain their old resolution and cannot bypass newly added locks.
- Transactional database schema 3, a four-candidate/two-edit CLI example and
  updated Codex Skill instructions for protected editing.

## 0.3.0 — 2026-09-06

- Managed local jobs with frozen resolutions, numbered attempts and explicit
  recovery of published results without repeating audio execution.
- Atomic batch submission and targeted failed-item retries; successful items
  retain their original results.
- Process-owned worker locks, acknowledged CPU cancellation and guarded selection
  that preserves candidates when a newer session selection wins.
- Explicit transactional migration from session database schema 1 to 2, job
  context queries and reproducible subprocess crash/failure examples.
- Legacy direct audio actions and PCM numeric profiles remain compatible.

## 0.2.0 — 2026-09-06

- Transactional SQLite sessions, version selection, history-preserving restores
  and independent branches from an explicit revision.
- Revision-guarded selection and idempotent state-mutation receipts, with
  queryable request IDs and rollback on interruption.
- Verbatim feedback tied to an exact revision, preserving user/agent attribution.
- Bounded context queries combining selection, history, feedback and measurements.
- A standalone core route in the Codex launcher and a session-workflow example
  that resumes through fresh CLI processes.
- Existing 0.1 audio assets and action contracts remain usable; PCM processing
  profiles are unchanged. This is the first M2 increment, not the full M2 scope.

## 0.1.0 — 2026-09-06

Initial source distribution of the shared audio authoring core.

- PCM16 WAV import with immutable snapshots, SHA-256 inventories and lineage.
- Versioned inspection, Q24 gain and frame/second trim operations.
- JSON CLI with resolve/execute/query, resolution digests, request conflict
  detection and completed-request deduplication.
- Complete result publication on Windows and Linux; interrupted requests report
  `recovery_pending` without automatic re-execution.
- Extension points for product operations and an optional Codex product launcher.
- Synthetic CLI example, unit tests, package checks and Windows/Linux CI.

This version does not include audio model generation, persistent editing
sessions, listening-feedback storage, automatic recovery or macOS publication.
