# Matter Audio Core 简介

Matter Audio Core 是供开发工具和编程代理使用的本地音频编辑核心。导入 WAV 后，
每次编辑都生成独立资产，保留原始快照、操作参数和结果记录。

当前版本为 **0.6.0**，支持 Windows、Linux 和 Python 3.10+。核心 PCM 操作不需要
GPU、模型权重或付费音频 API。

## 从一个可运行示例开始

```sh
git clone https://github.com/andyandymike/matter-audio-core.git
cd matter-audio-core
python -m venv .venv
```

Windows PowerShell 使用 `.venv\Scripts\Activate.ps1` 激活；Linux 使用
`source .venv/bin/activate`。随后运行：

```sh
python -m pip install .
matter-audio capabilities --json
python examples/quickstart.py
```

示例生成很短的合成 PCM、导入快照、调整音量并裁剪，验证重复请求返回同一结果。
它不会播放音频，也不会调用模型。核心目前通过源码和本地构建的 wheel 分发。

## 文档入口

| 目标 | 文档 |
| --- | --- |
| 导入、检查和编辑 WAV | [快速入门](getting-started.md) |
| 保留选择、分支和反馈 | [会话](sessions.md) |
| 指定片段的 PCM 不变 | [保护区域](regions.md) |
| 中断恢复、取消和批处理 | [任务执行](jobs.md) |
| 对比版本并导出 | [对比与导出](audition.md) |
| 循环、场景、音效包和搜索 | [制作工作流](production.md) |
| 从 Codex 或两个产品进入 | [Codex](codex.md)、[产品适配](products.md) |

详细工作流文档当前以英文为主；仓库还有完整的
[中文 README](https://github.com/andyandymike/matter-audio-core/blob/main/README.zh-CN.md)。

音量测量不等于听感判断，数值特征检索不等于语义理解。试听、素材权利和实际游戏
接入分别验收；生成了文件不会自动代表这些环节通过。
