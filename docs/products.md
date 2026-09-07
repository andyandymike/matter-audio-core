# SonicMatter and ScoreMatter

Both product CLIs share the core operation registry, request schemas and
workspace model. Each adds its own source or backend adapter.

| Product | Entry point | Product-specific capability |
| --- | --- | --- |
| Matter Audio Core | `python -m matter_audio_core` | Standalone PCM16 WAV snapshots and shared authoring. |
| SonicMatter | `python -m tools.authoring` | Registered CC0 recordings and the existing fused Q15 profile. |
| ScoreMatter | `python -m score_matter audio` | BGM editing and configured local SA3 inpainting. |

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

SonicMatter obtains sources through `catalog list` and `catalog decode`; its CLI
does not expose arbitrary imports. Inside its repository, workspaces belong below
`artifacts/`, with the tracked `artifacts/.gdignore` marker present. The core,
Python and decoder do not enter Godot exports.

ScoreMatter imports WAV snapshots and can register `score.sa3_inpaint/v1` when
its local runtime is configured. Shared PCM operations remain usable without
that runtime. The core does not download model weights or call a hosted provider.
Read [local model adapters](model-adapters.md) before using a model operation.

Use the same core version in clients of a shared workspace. Product integration
does not establish source rights, listening acceptance or game integration.
