# Changelog

## Unreleased

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
