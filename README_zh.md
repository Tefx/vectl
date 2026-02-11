# vectl — 为 AI Agent 设计的 DAG 强制任务列表

[English](README.md) | [**Read the Introduction (tefx.one)**](https://tefx.one/posts/vectl-intro/)

TODO.md 没法说"不"，vectl 可以。

[![PyPI](https://img.shields.io/pypi/v/vectl)](https://pypi.org/project/vectl/)

```bash
uvx vectl --help
```

## 为什么需要 vectl？

| 被动式 Markdown 计划 | vectl |
| :--- | :--- |
| ❌ **Token 爆炸**：Agent 每次调用都重读整个计划（包括已完成步骤） | ✅ `next` 只返回当前可执行的步骤 |
| ❌ **状态漂移**：多个 Agent 编辑同一文件 → 相互覆盖，状态过时 | ✅ CAS 安全原子写入 — 冲突必报错，绝不静默覆盖 |
| ❌ **无序执行**：Agent 随意挑选任务 → 跳过依赖，重复工作 | ✅ DAG 强制顺序 — 被阻塞的步骤对 Agent 不可见 |
| ❌ **无法验证**："完成" = 打个勾，没有任何证据 | ✅ 完成时强制要求提供证据 (Evidence) |
| ❌ **上下文污染**：已完成步骤永久驻留，稀释注意力 | ✅ Agent 只关注当下 |

## 核心理念

1. **主动门控 (Active Gating)**：强制执行 DAG 依赖，禁止跳过阶段或步骤。
2. **上下文效率 (Context Efficiency)**：Agent 只看到下一步要做什么，大幅节省 Token。
3. **原子状态 (Atomic State)**：基于 CAS (Compare-And-Swap) 的文件操作，杜绝并发冲突。
4. **行动暗示 (Affordance)**：每个命令的输出都包含"下一步做什么"的提示。
5. **极简调用**: 最常用的工作流 (`认领 → 干活 → 完成`) 只需要最少的工具调用。

## 快速开始

### 1. 初始化

```bash
uvx vectl init --project my-project
```

这会创建 `plan.yaml` 并自动配置 `AGENTS.md`（如果需要）。

### 2. 连接 Agent

<details>
<summary>⚡ Claude Desktop / Cursor</summary>

```json
{
  "mcpServers": {
    "vectl": {
      "command": "uvx",
      "args": ["vectl", "mcp"],
      "env": { "VECTL_PLAN_PATH": "/absolute/path/to/plan.yaml" }
    }
  }
}
```
</details>

<details>
<summary>⚡ OpenCode</summary>

添加到 `opencode.jsonc`：

```jsonc
{
  "mcp": {
    "vectl": {
      "type": "local",
      "command": ["uvx", "vectl", "mcp"],
      "environment": { "VECTL_PLAN_PATH": "/absolute/path/to/plan.yaml" }
    }
  }
}
```
详见 [OpenCode MCP 文档](https://opencode.ai/docs/mcp-servers/)。
</details>

<details>
<summary>⌨️ 仅使用 CLI (无 MCP)</summary>

无需配置 — Agent 直接调用 `uvx vectl ...`。

> **注**：`uvx vectl init` (步骤 1) 已经自动创建或更新了 `AGENTS.md`。
> 只有跳过 `init` 时才需要手动添加以下内容。

<details>
<summary>📋 AGENTS.md 模板 (点击展开)</summary>

```md
## Plan Tracking (vectl)

vectl tracks this repo's implementation plan as a structured `plan.yaml`:
what to do next, who claimed it, and what counts as done (with verification evidence).

Full guide: `uvx vectl guide`
Quick view: `uvx vectl status`

### CLI vs MCP
- Source of truth: `plan.yaml` (channel-agnostic).
- If MCP is available (IDE / Claude host), prefer MCP tools for plan operations.
- Otherwise use CLI (`uvx vectl ...`).
- Evidence requirements are identical across CLI and MCP.

### Rules
- One claimed step at a time.
- Evidence is mandatory when completing (commands run + outputs + gaps).
- Spec uncertainty: leave `# SPEC QUESTION: ...` in code, do not guess.
```
</details>
</details>

### 3. 迁移（可选）

如果你已经有 Markdown、Issue 或电子表格形式的计划，告诉你的 Agent：

```
阅读迁移指南（通过 `uvx vectl guide --on migration` 或 MCP 工具 `vectl_guide`）。
将现有的计划迁移到 plan.yaml。
如果可用，优先使用 MCP 工具 (`vectl_mutate`, `vectl_guide`)，否则使用 CLI。
```

### 4. 工作流

```bash
# 定位: 我们在哪？
uvx vectl status                    # 查看整体进度

# 挑选: 能做什么？
uvx vectl next                      # 查看可认领步骤

# 认领: 我做这个。
uvx vectl claim <step-id> --agent me  # 锁定步骤，获取完整 spec

# 干活: (写代码，跑测试...)

# 完成: 搞定了。
uvx vectl complete <step-id> --evidence "commit abc123, pytest passed"

# 重复: 下一步解锁了什么？
uvx vectl next                      # 查看新解锁的步骤
```

每个命令的输出都会提示下一步操作：

```
$ uvx vectl complete auth.user-model -e "commit abc: model + tests"

Completed: auth.user-model

Next available:
  ○ pending  auth.session-token — Session Token  (auth)
  ○ pending  auth.permissions — Permission Model  (auth)

→ vectl claim <id> --agent <name>
→ vectl show <id>
```

完整 33 条命令（计划变更、Review、管理）：`uvx vectl --help` 或 `uvx vectl guide`。

### 人类监管

```bash
uvx vectl render                    # 导出为 Markdown
uvx vectl diff                      # 查看自上次提交以来的变更
uvx vectl log --last 5              # 查看最近的计划变更记录
```

## 数据模型 (`plan.yaml`)

```yaml
version: 1
project: my-project
phases:
  - id: auth
    name: Auth Module
    depends_on: [core]
    steps:
      - id: auth.user-model
        name: User Model
        status: claimed
        claimed_by: engineer-1
```

完整 schema、ID 规则和排序语义：[docs/DESIGN.md](docs/DESIGN.md).

## 技术细节

架构、CAS 安全机制和测试覆盖率：[docs/DESIGN.md](docs/DESIGN.md).
