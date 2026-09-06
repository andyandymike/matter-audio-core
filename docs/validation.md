# Validation and support boundaries

## Reproducible checks

The unit suite in `tests/test_core.py` uses generated PCM samples and temporary
directories. It covers signed rounding, clipping policy, stereo frame slicing,
level windows, strict JSON/types, immutable input snapshots, tampering, request
conflicts, incomplete publication, concurrent duplicate requests, no-replace
publication and product workspace separation.

`tests/test_sessions.py` adds state persistence, restored versions, independent
branches, verbatim feedback, source attribution, pagination, request replay,
concurrent selections, schema ownership/migration rollback, and database
reopening after abrupt process exit with uncommitted writes. It also verifies
existing asset bytes are preserved and ordinary queries do not change database
bytes. Symlink checks skip when the host cannot create a test symlink.

`tests/test_jobs.py` covers live process exclusion, actual process exits after
claim/publication, registration rollback, explicit retry, partial batch failure,
acknowledged cancellation, CPU checkpoints, stale selection/cancellation guards,
frozen profiles, receipt replay, corruption and migration from schema 1.
`tests/job_worker.py` supplies bounded test-only subprocess fault/barrier fixtures.

`examples/quickstart.py` exercises the installed CLI in subprocesses. It creates
its own signal and checks gain, trim and completed-request retries. It does not
download recordings or call an audio model.

`examples/session_workflow.py` resumes a saved selection and agent note across
fresh CLI subprocesses, restores the original audio and creates a branch. The
example accepts an optional existing WAV; its note is explicitly an agent note,
not user listening feedback. `examples/jobs_workflow.py` runs four candidates
through injected I/O failure, abrupt process exit, recovery and targeted retry.
`examples/regions_workflow.py` compares four candidate PCM payloads, protects a
prefix, performs two edits and verifies restore/branch through fresh CLI processes.
CI runs all four examples against the installed wheel.

`tests/test_regions.py` covers stereo fade arithmetic/endpoints, half-up seconds,
unchanged bytes, moving inner locks, forbidden writes/deletions, unprotected
selection bypass, stale policies and changes during execution, false operation
declarations, protected job recovery and schema-2 migration with frozen legacy
job retry. These are exact technical checks, not listening judgments.

CI runs these checks against an installed wheel on Windows and Linux with
Python 3.10, 3.12 and 3.14. It builds the wheel from an sdist and checks package
contents, license metadata and the console entry point. For the current result,
see the [workflow runs](https://github.com/andyandymike/matter-audio-core/actions/workflows/tests.yml).
A configured matrix is not itself evidence that every run passed.

## Product checks

`tools/verify_consumers.py --help` describes an optional integration probe for
compatible ScoreMatter and SonicMatter checkouts. Supply each product root and
interpreter, an existing BGM WAV longer than one second, and an output directory.
Both interpreters must have the same core wheel installed. The Sonic checkout
must include its registered paper recording, decoder and frozen paper recipe.
These adapters and assets are maintained outside this repository; a checkout
without their authoring entry points cannot run the probe.

The probe compares installed core module hashes, invokes both product CLIs,
checks BGM gain/trim, compares the paper result with the recipe's expected hash,
and verifies unchanged source files. Store its outputs locally: reports include
machine paths and references to user-supplied media.

## What passing means

Passing tests demonstrates the tested sample transformations, schema handling
and filesystem behavior. It does not establish listening acceptance, subjective
quality, game integration or distribution rights for source recordings.

Publication requires Windows directory rename behavior or Linux
`renameat2(RENAME_NOREPLACE)` support in the C library and filesystem. macOS,
network/synchronized workspaces, unsupported filesystems and adversarial shared
directories are outside the supported authoring environment. Power-loss
durability and automatic request recovery are not claimed. Managed jobs provide
explicit recovery on those supported local filesystems; direct legacy actions
retain their original incomplete-claim behavior.
