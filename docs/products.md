# SonicMatter and ScoreMatter

Both product CLIs share the core operation registry, request schemas and
workspace model. Each adds its own source or backend adapter.

| Product | Entry point | Product-specific capability |
| --- | --- | --- |
| Matter Audio Core | `python -m matter_audio_core` | Standalone PCM16 WAV snapshots and shared authoring. |
| SonicMatter | `python -m tools.authoring` | Recording catalogs, declared project recordings and the existing fused Q15 profile. |
| ScoreMatter | `python -m score_matter audio` | BGM candidates, named musical sections, arrangements and configured local SA3 inpainting. |

## Install through the product

Use each product's pinned requirements and installation instructions:

- [SonicMatter shared authoring](https://andyandymike.github.io/sonic-matter/shared-audio/)
  uses `requirements-authoring.txt` and the miniaudio decoder.
- [ScoreMatter shared audio](https://andyandymike.github.io/score-matter/shared-audio/)
  uses `requirements-audio.txt` and the optional `audio` extra.

The current adapters require core 0.6.0 and build from its reviewed immutable
commit. Run the product's `tools/check_shared_audio.py` to verify installation,
adapter behavior and exact exports without invoking an audio model.

## Keep the product boundary

SonicMatter obtains catalog sources through `catalog list` and `catalog decode`.
Project PCM16 WAV recordings use `recordings list/import` with an explicit
manifest containing source identity and purpose-specific rights evidence; its
CLI does not expose unrestricted `assets import`. Inside its repository, workspaces belong below
`artifacts/`, with the tracked `artifacts/.gdignore` marker present. The core,
Python and decoder do not enter Godot exports.

ScoreMatter imports WAV snapshots and can register `score.sa3_inpaint/v1` when
its local runtime is configured. Shared PCM operations remain usable without
that runtime. The core does not download model weights or call a hosted provider.
Read [local model adapters](model-adapters.md) before using a model operation.

Score's musical authoring layer registers existing candidates, names source
regions, replaces equal-length sections and arranges explicit repetitions.
Versioned arrangement requests can add linear transitions at selected occurrence
boundaries. An explicit `music annotate-arrangement` request marks the completed
output's full occurrences or unmixed bodies for subsequent editing. These
product-level plans use Core operations and retain their source identities;
they do not infer a global beat grid or inherit old PCM locks. See the
[Codex workflow](codex.md) and the ScoreMatter guide for exact request schemas.

Each product keeps its own workspace. To use a completed Score WAV as a Sonic
recording source, explicitly export it and register it with the required Sonic
manifest and rights evidence. Asset IDs, music annotations, sessions and locks
do not transfer with the WAV. Export and registration do not grant new rights.

Use the same core version in clients of a shared workspace. Product integration
does not establish source rights, listening acceptance or game integration.
