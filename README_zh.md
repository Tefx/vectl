# vectl — AI Agent 的执行控制面

[English](README.md) | [**介绍文章**](https://tefx.one/posts/vectl-intro/)

**约束 agent 行为，压缩 agent 开销。**

[![PyPI](https://img.shields.io/pypi/v/vectl)](https://pypi.org/project/vectl/)

```bash
uvx vectl --help
```

## 你的 Markdown 计划正在浪费 Token

一个 50 步的 markdown 计划，完成 40 步后：

- Agent 仍然**逐行重读全部 50 条**。40 条已完成的步骤是纯噪音——占 context window、消耗 attention、花你的钱。
- `vectl next` **只返回 3 条可执行的步骤**。完成的消失，被阻塞的不可见。

步骤越多，差距越大。100 步做完 90 步？Markdown 强迫 agent 读 100 行来找 10 行有用的。vectl 只给它那 10 行。

而且 Markdown 是线性的。三个 agent 同时在线？它们只能排队——因为没有任何信息告诉它们哪些步骤可以并行。
vectl 的 DAG 让并行成为可能：依赖关系是显式的，`next` 自动吐出**所有**已解锁的步骤，三个 agent 各领一个，互不冲突。

Token 浪费和无法并行只是表面症状。Markdown 的根本缺陷是**它不表达依赖关系**：

| Markdown 计划 | vectl |
| :--- | :--- |
| ❌ **每次全量读取**：不管做完多少，agent 都重读所有步骤 | ✅ 只返回可执行的步骤，完成即消失 |
| ❌ **隐式依赖**："部署 DB"写在"配置 App"前面，agent 只能猜它们有没有关系 | ✅ `depends_on: [db.deploy]` —— 显式声明，不猜 |
| ❌ **无法并行**：没有依赖信息，多 agent 只能排队或赌运气 | ✅ DAG 让并行可计算——`next` 返回所有无依赖冲突的步骤，多 agent 各领一个 |
| ❌ **人工调度**："DB 好了，你去搞 App 吧" | ✅ `next` 自动吐出所有已解锁的步骤 |
| ❌ **静默覆盖**：两个 agent 同时写同一个文件 | ✅ CAS 乐观锁 —— 冲突报错，不会静默丢失 |
| ❌ **自我宣布完成**：agent 说 "Done" 就是 Done | ✅ 必须提交证据：跑了什么命令、输出是什么、PR 在哪 |
| ❌ **对话太长就失忆**：换个会话一切从零开始 | ✅ `checkpoint` 一键生成状态快照，注入新会话即可恢复 |

> TODO.md 没法说"不"。vectl 可以。

## 控制面，不是框架

Agent 框架管 agent 怎么想。vectl 管 **agent 看到什么、什么时候看到、必须证明什么**。

| 能力 | 解决什么 | 怎么做 |
| :--- | :--- | :--- |
| **DAG 强制执行** | Agent 跳依赖、猜顺序 | 被阻塞的步骤对 agent 不可见，物理上无法领取 |
| **安全并行** | 多 agent 互踩 | `claim` 锁定 + CAS 原子写入 |
| **自动调度** | 需要人盯着分配任务 | `next` 自动计算已解锁步骤并排序；rejected 自动浮顶 |
| **Token 预算** | Agent 重读大量已完成内容 | 全链路上限：next ≤3 条、context ≤120 字符、evidence ≤900 字符 |
| **反幻觉** | Agent 说 "Fixed" 就完事了 | `evidence_template` 填空式证明：命令、输出、PR 链接 |
| **上下文压缩** | 对话超长导致 agent 失忆 | `checkpoint` 生成确定性 JSON 快照，注入新会话即刻恢复 |
| **交接便条** | 跨 host/跨会话状态丢失 | `clipboard-write/read/clear` 把短便条存进 `plan.yaml`（带 TTL） |
| **Agent 亲和** | 不同 agent 擅长不同任务 | 步骤可标记建议 agent，`next` 按亲和度排序 |

## 快速开始

### 1. 初始化

```bash
uvx vectl init --project my-project
```

创建 `plan.yaml` 并自动配置 agent 指令文件（检测到 `.claude/` 目录时写 `CLAUDE.md`，否则写 `AGENTS.md`）。

> 建议把 `plan.yaml` 和 `AGENTS.md`/`CLAUDE.md` 一起提交：前者是状态机，后者是 agent 的入口。

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
参考 [OpenCode MCP 文档](https://opencode.ai/docs/mcp-servers/)。
</details>

<details>
<summary>⌨️ 纯 CLI（不用 MCP）</summary>

不需要额外配置，agent 直接调用 `uvx vectl ...`。

> `uvx vectl init` 已自动创建/更新 agent 指令文件。
> 后续更新：`uvx vectl agents-md`（可指定 `--target claude`）。
</details>

#### Agent 指令文件

`vectl init` 和 `vectl agents-md` 用于管理 repo 里的 agent 指令文件。

这个文件就是 agent 的“入口”：它会指向 `uvx vectl guide` 的各个 topic，并写清楚规则（一次只能 claim 一个 step、complete 必须给 evidence、spec 不确定不要猜）。

```bash
uvx vectl agents-md                 # 更新 AGENTS.md / CLAUDE.md 的 vectl 区块
uvx vectl agents-md --target claude # 强制写入 CLAUDE.md
```

### 3. 迁移已有计划（可选）

如果项目已有 markdown / issue / spreadsheet 计划：

```
阅读迁移指南（`uvx vectl guide --on migration` 或 MCP 的 `vectl_guide` 工具）。
把现有计划迁移到 plan.yaml。
优先使用 MCP 工具（`vectl_mutate`、`vectl_guide`）。
```

### 4. 工作流

保持简单：

```bash
uvx vectl status                               # 定位：做到哪了？
uvx vectl next                                 # 选择：哪些能做？
uvx vectl claim <step-id> --agent <name>       # 拿到 spec + refs + evidence template
uvx vectl complete <step-id> --evidence "..."  # 提交证据（粘贴填好的模板）
```

其他内容都在 guide：

- Architect 协议：`uvx vectl guide --on planning`
- 卡住了：`uvx vectl guide --on stuck`
- Review / 校验：`uvx vectl guide --on review`
- 迁移：`uvx vectl guide --on migration`

## 交接：Clipboard（便条）vs Checkpoint（状态）

当你在不同 agent host 之间切换（Claude Code ↔ Cursor ↔ OpenCode），或多 agent 交接工作时，建议两个都用：

- **Clipboard**：短、可读的交接便条，直接存进 `plan.yaml`（带 TTL）。
- **Checkpoint**：更像“机器状态摘要”，用于注入下一次会话。

### Clipboard（交接首选）

```bash
uvx vectl clipboard-write --author agent-a --summary "改了什么" --content "怎么验证 / 看哪里"
uvx vectl clipboard-read
uvx vectl clipboard-clear
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

## 技术细节

架构、CAS 安全、测试覆盖（658 tests, Hypothesis 状态机验证）：[docs/DESIGN.md](docs/DESIGN.md)。
