# vectl — AI Agent 的执行控制面

[English](README.md) | [**介绍文章**](https://tefx.one/posts/vectl-intro/)

**约束 agent 行为，而不只是建议。**

[![PyPI](https://img.shields.io/pypi/v/vectl)](https://pypi.org/project/vectl/)
[![License](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](https://opensource.org/licenses/Apache-2.0)

```bash
uvx vectl --help
```

## Markdown 管不住你的 Agent

一个 200 步的 TODO.md。你指望 agent 按顺序做、做完打勾、不要跳步骤。

实际呢？**Agent 把你的 TODO.md 当建议看，不当规则看。** 它可能跳过几个步骤直接做后面的；做完了不勾选 checkbox；或者在 1000 行的计划里迷路，开始重复做已经完成的事。你越是试图用注释、加粗、分隔线来"暗示"它应该遵守的顺序和规则，效果越差——因为 Markdown 本质上就是一段自然语言文本，agent 对它没有任何"必须执行"的义务。

核心问题：**TODO.md 对 agent 来说是建议，不是约束。你没法强制它必须 claim 一个步骤才能开始、必须提交证据才能标记完成。**

这是 Markdown 的结构性缺陷，不是 agent 笨：

- **没有强制力**：你没法阻止 agent 跳过步骤，也没法要求它提交"做完了"的证据
- **没有依赖关系**："部署 DB"写在"配置 App"前面——agent 只能猜它们有没有先后关系，猜错了也没人拦
- **多 agent 互踩**：多个 agent 同时在线，无从知道谁在做什么、哪些步骤可以并行，改同一个文件谁赢谁知道
- **完成靠自觉**：agent 说一句"Done"就算完成了，你没法要求它证明真的跑过测试、真的改对了

这些问题在计划只有 10-20 步的时候还能忍。但当项目有上百个步骤、需要多个 agent 协同的时候，TODO.md 就彻底失控了。

> TODO.md 没法说"不"。vectl 可以。

## 控制面，不是框架

Agent 框架管 agent 怎么想。vectl 管 **agent 看到什么、什么时候看到、必须证明什么**。

| 能力 | 解决什么 | 怎么做 |
| :--- | :--- | :--- |
| **DAG 强制执行** | Agent 跳依赖、猜顺序 | 被阻塞的步骤对 agent 不可见，物理上无法领取 |
| **证据机制** | Agent 说 "Fixed" 就完事了 | `evidence_template` 填空式证明：命令、输出、PR 链接 |
| **安全并行** | 多 agent 互踩 | `claim` 锁定 + CAS 原子写入 |
| **自动调度** | 需要人盯着分配任务 | `next` 自动计算已解锁步骤并排序；rejected 自动浮顶 |
| **Token 预算** | Agent 重读大量已完成内容 | 全链路上限：next ≤3 条、context ≤120 字符、evidence ≤900 字符 |
| **上下文压缩** | 对话超长导致 agent 失忆 | `checkpoint` 生成确定性 JSON 快照，注入新会话即刻恢复 |
| **交接便条** | 跨 host/跨会话状态丢失 | `clipboard-write/read/clear` 把短便条存进 `plan.yaml`（带 TTL） |
| **Agent 亲和** | 不同 agent 擅长不同任务 | 步骤可标记建议 agent，`next` 按亲和度排序 |

## 快速开始

### 1. 初始化

```bash
uvx vectl init --project my-project
```

创建 `plan.yaml`，并在 agent 指令文件（`CLAUDE.md` 或 `AGENTS.md`）里追加一段 vectl 专用 section——如果文件已存在，不会覆盖你原有的内容。

> 建议把 `plan.yaml` 和 `AGENTS.md`/`CLAUDE.md` 一起提交：前者是状态机，后者是 agent 的入口。

### 2. 连接 Agent

推荐通过 MCP 接入，获得结构化的工具调用。

<details>
<summary>⚡ MCP（推荐）</summary>

```json
{
  "mcpServers": {
    "vectl": {
      "command": "uvx",
      "args": ["vectl", "mcp"]
    }
  }
}
```

vectl 通过 MCP 暴露了 14 个工具，agent 直接调用 `vectl_status`、`vectl_claim`、`vectl_complete` 等——结构化数据进出，比解析 CLI 文本输出更可靠。

OpenCode 用户，在 `opencode.jsonc` 中添加：

```jsonc
{
  "mcp": {
    "vectl": {
      "type": "local",
      "command": ["uvx", "vectl", "mcp"]
    }
  }
}
```

参考 [OpenCode MCP 文档](https://opencode.ai/docs/mcp-servers/)。
</details>

<details>
<summary>⌨️ 纯 CLI（不用 MCP）</summary>

不需要额外配置，agent 直接调用 `uvx vectl ...`。哪里都能用，但 agent 需要自己解析文本输出。

> `uvx vectl init` 已自动创建/更新 agent 指令文件。
> 后续更新：`uvx vectl agents-md`（可指定 `--target claude`）。
</details>

#### Agent 指令文件

`vectl init` 和 `vectl agents-md` 用于管理 repo 里的 agent 指令文件。

这个文件就是 agent 的"入口"：它会指向 `uvx vectl guide` 的各个 topic，并写清楚规则（一次只能 claim 一个 step、complete 必须给 evidence、spec 不确定不要猜）。

```bash
uvx vectl agents-md                 # 更新 AGENTS.md / CLAUDE.md 的 vectl 区块
uvx vectl agents-md --target claude # 强制写入 CLAUDE.md
```

### 3. 写计划

把需求告诉 agent，它会通过 vectl 的 `mutate` 工具来生成和修改计划。**不要手写 `plan.yaml`，也不要让 agent 直接编辑**——所有修改都应该通过 vectl 工具进行，这样才能保证校验、锁状态计算和并发安全。

### 4. 迁移已有计划（可选）

如果项目已有 markdown / issue / spreadsheet 计划：

```
阅读迁移指南（`uvx vectl guide --on migration` 或 MCP 的 `vectl_guide` 工具）。
把现有计划迁移到 plan.yaml。
优先使用 MCP 工具（`vectl_mutate`、`vectl_guide`）。
```

### 5. 查看进度

作为使用者，你的日常主要是**看进度**和**做决策**：

```bash
uvx vectl render            # 生成 Markdown 进度报告
uvx vectl dashboard --open  # 可视化 Dashboard（静态 HTML，不需要 server）
```

Dashboard 里有进度概览、每个 phase 的状态、依赖关系的 DAG 图。打开浏览器就能看，不需要启动任何服务。DAG 视图会从 CDN 加载 Mermaid.js（该 tab 需要网络）。

其他内容都在 guide：

- Architect 协议：`uvx vectl guide --on planning`
- 卡住了：`uvx vectl guide --on stuck`
- Review / 校验：`uvx vectl guide --on review`
- 迁移：`uvx vectl guide --on migration`

## 交接：Clipboard（便条）vs Checkpoint（状态）

当你在不同 agent host 之间切换（Claude Code ↔ OpenCode ↔ Claude Desktop），或多 agent 交接工作时，建议两个都用：

- **Clipboard**：短、可读的交接便条，直接存进 `plan.yaml`（带 TTL）。
- **Checkpoint**：更像"机器状态摘要"，用于注入下一次会话。

### Clipboard（交接首选）

当你想在不同 agent host / 不同会话之间传递**可执行的信息**，但又不想为了这点事到处写文件时，用 clipboard。

例子：Claude Code 做了详细代码审查，发现几个小问题，想交给 OpenCode 去修。
把 review 要点塞进 clipboard，另一个 agent 读出来直接改。

```bash
uvx vectl clipboard-write \
  --author "claude-code" \
  --summary "代码审查：小修" \
  --content "
目标：src/foo.py

问题清单：
- X 改名为 Y（见 bar() 的注释）
- 补一个覆盖边界条件 Z 的测试
- 验证：uv run pytest tests/test_foo.py
"

uvx vectl clipboard-read
uvx vectl clipboard-clear
```

MCP 等价调用：

```python
vectl_clipboard(action="write", author="claude-code", summary="代码审查：小修", content="...")
vectl_clipboard(action="read")
vectl_clipboard(action="clear")
```

### Checkpoint

```bash
uvx vectl checkpoint
```

把 JSON 粘到下一次会话的 system prompt 里。

## 数据模型（`plan.yaml`）

```yaml
version: 1
project: my-project
phases:
  - id: auth
    name: Auth Module
    context: |
      所有 auth 步骤需遵循 OWASP 规范。
      测试时同时验证合法和伪造 JWT。
    depends_on: [core]
    steps:
      - id: auth.user-model
        name: User Model
        status: claimed
        claimed_by: engineer-1
```

一个 YAML 文件。在你的 git repo 里。

不需要数据库。不需要 SaaS。`git blame` 能查、PR 能 review、`git diff` 能追踪。

**Phase Context**：在 phase 上设置 `context`，可以为该 phase 下的所有步骤提供统一指导。当 agent 执行 `vectl show <step>` 或 `vectl claim` 时，phase context 会自动显示在输出中。

完整 schema 和排序语义：[docs/DESIGN.md](docs/DESIGN.md)。

## 锁一致性

锁状态由 vectl 自动维护，agent 不需要手动管理。每次写操作（`claim`、`complete`、`mutate` 等）之后，vectl 自动重新计算锁状态。当锁状态发生变化时，vectl 会输出提示信息：

```
[vectl] Lock status updated: phase-a (pending)
```

如果你在 vectl 之外直接编辑了 `plan.yaml`，运行 `uvx vectl recalc-lock` 来诊断和修复锁状态不一致。

---

## 技术细节

架构、CAS 安全、测试覆盖（Hypothesis 状态机验证）：[docs/DESIGN.md](docs/DESIGN.md)。
