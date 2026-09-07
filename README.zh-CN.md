# Matter Audio Core

[文档站](https://andyandymike.github.io/matter-audio-core/) · [中文概览](https://andyandymike.github.io/matter-audio-core/zh/)

[English](README.md)

供工具和编程代理使用的本地音频制作核心：导入 WAV、检查电平、调整增益、
按帧或秒裁剪、淡入淡出、分层混音和窗口拼接，并为每次结果保留来源、摘要和处理参数。
CLI 返回 JSON 和 WAV 路径，本地比较页可保存选择、反馈并导出所选版本。

核心可独立使用，也可供 SonicMatter 和 ScoreMatter 共用。确定性操作无需 GPU、
模型权重或 API key。[Codex 入口](docs/codex.md)通过配置好的产品 CLI 调用。

当前版本为 **0.6.0，早期开发阶段**。面向 Windows / Linux、带标准库 SQLite 的 Python 3.10+。
持久会话已加入托管任务、显式中断恢复、批次失败项重试与 CPU 协作取消。
PCM 区域锁支持在连续裁剪和淡入淡出中逐帧保留指定片段。
可选产品适配器可接入本地模型编辑，支持进程树取消和调用记录；核心包不包含模型与权重。
macOS 的结果发布尚未实现。

0.6 新增[成套制作能力](docs/production.md)：音频套装与变体、批量精确导出、RMS/峰值电平匹配、
首尾交叉淡化循环、带重复事件和分层音量的场景时间轴，以及本地特征索引与检索。
运行 `python examples/production_workflow.py` 可跨独立 CLI 进程验证完整流程，音频模型调用为 0。
检索只使用显式名称/标签和实际测量，循环接缝指标不代表已经通过听感验收。

## 快速运行

目前从源码安装，尚未发布到 PyPI。以下是 Windows PowerShell 命令，无需激活虚拟环境：

```powershell
git clone https://github.com/andyandymike/matter-audio-core.git
cd matter-audio-core
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install .
.\.venv\Scripts\python.exe -m matter_audio_core capabilities --json
.\.venv\Scripts\python.exe examples/quickstart.py
```

Linux 环境创建 `.venv` 后使用 `source .venv/bin/activate`，再运行
`python -m pip install .` 和 `python examples/quickstart.py`。

示例会在新的 `.local/demo/` 子目录生成低电平测试信号，实际执行导入、-3 dB 增益、
裁剪和重复请求检查，并返回试听路径；不需要下载音频或其他项目。

## 能力与边界

| 操作 | 参数 | 结果 |
| --- | --- | --- |
| `inspect/v1` | 可选窗口帧数、分页位置和数量 | 峰值、RMS、分声道和分页电平 |
| `gain/v1` | `db`，可选 `clip` 策略 | 按明确 Q24 规则处理的 PCM16 WAV |
| `trim/v1` | 帧或秒的起止坐标 | 精确裁剪；结束位置不包含在内 |
| `fade/v1` | 帧或秒的淡入/淡出长度 | 线性振幅 Q24 淡化，其余 PCM 精确保留 |
| `mix/v1` | 基底、各层输入、窗口、位置、增益和淡化 | 固定长度混音；可从原配方独立修改一层 |
| `splice/v1` | 基底与替换素材、目标窗口、源起点、过渡长度 | 仅改写指定窗口，过渡也包含在窗口内 |

输入仅支持非空、最大 64 MiB、8–192 kHz 的单/双声道 PCM16 WAV。
其他格式需要显式解码适配器。多输入动作要求格式相同，所有输入 WAV 总计不超过 64 MiB。

每个 workspace 保存不可变快照与完整结果。同一请求 ID 重复提交相同动作会返回原结果；
同 ID 修改参数会报冲突。`recovery_pending` 表示动作正在运行或曾中断，需要查询状态，
当前不会自动恢复。文件处理成功和电平测量不等于人工听音验收。

完整命令及 JSON 请求示例见 [英文说明](README.md#work-with-an-existing-wav)，
实现约定见 [PCM16 profile](docs/pcm16-profile.md)，后续范围见
[架构与路线图](docs/architecture.md)。

## 开发与许可

继续制作的完整示例：

```powershell
.\.venv\Scripts\python.exe examples/session_workflow.py
```

流程为导入 A、制作并选择 B、保存代理备注、重新启动进程读取状态、回退 A、从 B 建立分支。
每次调用都使用新进程，验证状态不依赖聊天内存。可加 `--input <现有.wav>` 使用自己的音频。
示例备注明确标为代理来源，不冒充用户试听结论。

`session create / select / branch` 保存制作状态，`feedback add` 绑定具体修订，
`context show <session-id>` 返回当前选择、历史、相关反馈和测量。
选择时必须提供预期修订，回退也会新增记录。完整 JSON 请求见 [会话说明](docs/sessions.md)。

运行 `python examples/jobs_workflow.py` 可验证四候选制作中的一次写入失败与一次进程崩溃：
恢复已有完整结果，只重试失败候选，其他成功结果保持不变。
`job submit / run / show / recover / cancel / retry` 和
`batch submit / run / show / recover / retry` 提供对应操作。
旧数据库升级到 0.5 的 schema 4 需先执行 `session migrate`，已有音频和历史内容保持原样。
具体请求、取消确认与兼容边界见 [任务说明](docs/jobs.md)。

运行 `python examples/regions_workflow.py` 可制作四个尾部包络不同的候选，选择 B 后锁定前缀，
再连续裁剪尾部、淡出尾部。每一步使用新 CLI 进程，验证保护区 PCM 完全相同、越界请求失败、
回退和分支保留当时的规则。长 BGM 可加 `--input <现有.wav> --protect-seconds 15`。

`constraints set` 把保护区绑定到当前所选资产并新增修订。托管任务自动绑定约束，
直接动作可显式绑定会话修订。普通选择不能丢弃锁定片段；恢复历史修订会同时恢复音频和当时的约束，
包括恢复到未设锁的历史状态。请求和算法见 [保护区与淡化说明](docs/regions.md)。

运行 `python examples/composition_workflow.py` 可验证独立调节一层、其余区域保持不变、
比较集合持久化与精确导出。`audition create / show / serve` 准备并打开本地比较页；
页面提供片段播放、重复、循环、A/B 切换和仅影响预览的音量匹配。
`export create / show` 保存并验证所选 WAV，不重新编码。
详见[比较与导出](docs/audition.md)、[分层与拼接](docs/composition.md)。

兼容的 ScoreMatter checkout 可注册 `score.sa3_inpaint/v1`，使用其已有 SA3 本地运行环境。
模型负责提出候选，共用拼接负责最终 PCM 写入边界；原始候选、实际改动、调用次数、耗时和失败
分开记录。先查询产品能力，具体依赖和限制见[模型适配说明](docs/model-adapters.md)。
当前工程验证不代替人工试听，也不包含 BornAgent 或游戏集成。

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe -m build
.\.venv\Scripts\python.exe tools/check_distribution.py dist
```

贡献与检查流程见 [CONTRIBUTING.md](CONTRIBUTING.md)。使用 [MIT 许可证](LICENSE)；
用户导入音频的版权与分发权利仍由各自素材许可决定。
