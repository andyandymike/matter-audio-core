# Matter Audio Core

[English](README.md)

供工具和编程代理使用的本地音频制作核心：导入 WAV、检查电平、调整增益、
按帧或秒裁剪，并为每次结果保留来源、摘要和处理参数。CLI 返回 JSON 和可播放的 WAV 路径。

核心可独立使用，也可供 SonicMatter 和 ScoreMatter 共用。确定性操作无需 GPU、
模型权重或 API key。[Codex 入口](docs/codex.md)通过配置好的产品 CLI 调用。

当前版本为 **0.3.0，早期开发阶段**。面向 Windows / Linux、带标准库 SQLite 的 Python 3.10+。
持久会话已加入托管任务、显式中断恢复、批次失败项重试与 CPU 协作取消。
macOS 的结果发布和生成式编辑尚未实现。

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

输入仅支持非空、最大 64 MiB、8–192 kHz 的单/双声道 PCM16 WAV。
其他格式需要显式解码适配器。

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
0.2 数据库升级需先执行 `session migrate`，已有音频和会话记录保持原样。
具体请求、取消确认与兼容边界见 [任务说明](docs/jobs.md)。

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe -m build
.\.venv\Scripts\python.exe tools/check_distribution.py dist
```

贡献与检查流程见 [CONTRIBUTING.md](CONTRIBUTING.md)。使用 [MIT 许可证](LICENSE)；
用户导入音频的版权与分发权利仍由各自素材许可决定。
