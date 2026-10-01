# Local ScoreMatter Generation

Use `scripts/run_bgm.py` from this installed skill for one new text-to-music candidate. It calls the configured ScoreMatter `generate` CLI. Existing WAV edits, sessions, comparisons and loops continue through `run_audio.py --product score`; the shared inpainting operation is not a text-to-audio generator.

## Machine Configuration And Project Output

The skill's machine-local `config.local.json` supplies `score.python`, `score.root` and `score.sa3_runtime_root` as existing absolute paths. The runtime root identifies the SA3 TFLite directory containing its own Python environment, script and model components. `--runtime-root` can explicitly override it. Do not infer the runtime from another project, download weights, or substitute a different model when the configured route is missing.

The configured ScoreMatter version must support native `generate --record-root`. `--check` validates that support, the actual imported product location, the generation arguments, and required local component file presence/nonzero size. It imports only lightweight product code and does not load weights or generate audio. File metadata does not prove model integrity, successful inference or music quality.

Default output is `<project-root>/artifacts/matter-audio/score/generation/<unique-run>/candidate.wav`, with generation records in its sibling `records/` directory. Project root follows `run_audio.py`: explicit `--project-root`, otherwise the caller's Git root or cwd. `--out` selects a new WAV and `--record-root` selects its record directory; relative values resolve from the caller's cwd before entering the product checkout. No legacy shared workspace is inherited. Existing output paths are rejected rather than overwritten.

## Prepare, Then Execute The Requested Work

```text
python <skill-dir>/scripts/run_bgm.py --check
python <skill-dir>/scripts/run_bgm.py --project-root <absolute-project> --prompt "<musical intent>" --seed 31415 --seconds 32 --out <new-absolute-wav> --dry-run
python <skill-dir>/scripts/run_bgm.py --project-root <absolute-project> --prompt "<musical intent>" --seed 31415 --seconds 32 --out <new-absolute-wav>
```

`--dry-run` only prints resolved paths and argv; it starts no child and creates no state. Its `runtime_checked: false` is intentional. `--check` performs the no-generation product preflight; provide the same prompt/options to check a specific planned invocation. Executing without these switches performs the preflight and then one native generation attempt. There is no automatic playback, batch multiplication, hidden retry, model download or session import.

The wrapper forwards optional `--seconds`, `--seed`, `--steps`, `--threads`, `--cfg`, `--apg` and `--negative-prompt`. Unspecified values use the installed product defaults. Inspect `python -m score_matter generate --help` in that configured interpreter for its current interface. The current native route is SA3 Medium / SAME-L / fp32; this is that backend's implementation boundary, not a universal musical preset. A request for another model needs a supported route, not an invented flag. The project adapter may preserve a narrower chosen recipe, which must still be checked against the live route.

For controlled comparisons, pass an explicit shared seed and the same settings across direction prompts; freeze the exact prompt when comparing seeds. An omitted seed remains a backend-selected value, and a preflight preview seed is not a generated seed. The product's actual generation record owns the resulting seed, prompt, settings, model route, timing, output hash and media facts. It validates the WAV structure and publishes without replacement; this does not establish musical acceptance or every audio-quality metric.

## Failure And Continued Work

Keep `--timeout-seconds` within the task's compute allowance. The product times out its model process; the host allows additional time for cleanup and recording. A host timeout leaves the outcome uncertain: inspect the exact planned WAV, sibling records and process state before retrying. This direct route has no shared `action show` or `job show` ID. A successful WAV with a record warning should be preserved and its missing evidence repaired without generating it again.

Model execution remains offline and runs only for the requested candidate set. Model components and tool caches remain machine resources; project output and prompt records stay at the resolved project locations. Old records in the product checkout are not moved automatically. Never delete weights as a side effect of skill use.

After successful generation, inspect the exact WAV and return its playable path. Import it into a chosen shared audio workspace only when that continued editing/comparison work is requested, using the returned asset IDs thereafter. Keep raw output intact and create separately named derivatives. Follow [BGM authoring](bgm-authoring.md) for musical comparison, scoped feedback and loop audition; game playback still needs its real consumer and listening evidence.

When the installed product exposes `candidate register`, preserve the generation
record alongside the audio in that workspace:

```text
python <skill-dir>/scripts/run_audio.py --product score -- candidate register --audio <candidate.wav> --generation-record <record.json> --intent <intent.json> --request-id <registration-id>
```

The record and intent arguments are optional. Use the actual returned record
path; if native generation reported a record warning, retain the WAV and omit
the missing record rather than inventing one or generating again. The small
`score-music-intent/v1` schema is documented in ScoreMatter's `docs/shared-audio.md`;
it records direction, not user listening feedback or a musical guarantee.
Registration checks the record's output hash and media facts, preserving the
original WAV and supplied JSON bytes. It does not verify past inference or the
model components. Use the returned audio asset ID for explicit session creation
or selection with the current revision. Query `action show <registration-id>` to
retrieve a completed registration without its original input files. Generation
and registration are separate operations: registration errors never require
regeneration, and `recovery_pending` is not permission to create a new attempt.
