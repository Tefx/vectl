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

`refs` 是辅助上下文。Vectl 会持久化引用；`validate --check-refs` 可能报告警告，但引用不会阻止领取步骤或修改 Plan。

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

vectl 通过 MCP 暴露了 20 个工具，agent 可直接调用 `vectl_status`、`vectl_claim`、`vectl_complete`、`vectl_decide` 等；`vectl_decide` 也是支持调用方自有自动化的 advisor 入口点——结构化数据进出，比解析 CLI 文本输出更可靠。

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
uvx vectl top              # 常驻终端进度面板（plan 变化时自动更新）
uvx vectl render           # 生成 Markdown 进度报告
uvx vectl dashboard --open # 可视化 Dashboard（静态 HTML，不需要 server）
```

`vectl top` 适合开在一个终端 panel 里常驻观察进度。它监听 plan 文件变化，只在内容变化时重绘，避免高频刷新带来的 CPU 和耗电浪费。为了适配普通终端窗口，它会自动折叠前面已完成的 phase，并保留最近和未完成的 phase 可见；按 `q` 或 Ctrl-C 可退出。

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

### MCP 原子批量变更

`vectl_mutate` 支持原有单项 `action` 模式，也支持非空 `requests` 列表；一次调用只能选择一种模式。批量请求按顺序作用于同一份 Plan 深拷贝，创建 phase/step 时必须提供显式 ID。任何请求错误或最终校验失败都会丢弃工作副本，并在 Plan、claims、备份和 Git 写入之前返回。成功路径只执行一次锁重算和一次带原始 CAS hash 的受准入保存，并返回有序结果、`plan_hash` 和 mutation receipt。

单项和批量模式都对候选 Plan 执行 canonical model 校验和结构校验。Canonical Pydantic 位置组件只有在当前路径预期的 `Plan`、`Phase`、`Step`、`Clipboard` 或 `RejectionEntry` schema 接受该字段或列表索引时才会保留；错误类型只有属于 Pydantic Core 内建 allowlist 时才会保留。未知位置统一写成 `<field>`，未知或自定义类型统一写成 `validation_error`；Pydantic `msg`、原始输入、context 和文档 URL 不会复制。Canonical 提取/归一化或结构校验器发生未预期异常时，系统会在保存前返回一条固定 `unexpected_validation_error`，不包含异常或候选文本。普通结构诊断继续保留可操作的标识符；控制字符和换行会转义，每条 issue 最多 512 个码点。失败结果包含 `validation_source`（`canonical_model` 或 `structural`）、完整/渲染计数、`issues_truncated` 和 `issue_values_truncated`。`max_issues=N` 最多渲染 N 行；`max_issues=None` 渲染全部行。该参数不改变候选内容、准入、CAS 或保存语义。

### 有范围的 MCP review 诊断

`vectl_review` 默认使用兼容的 `scope="global"` 完整历史路径。`scope="phase"` 要求 `phase_id`：渲染仍只投影目标 phase，校验包含目标 phase 的传递依赖。`scope="active_dependency_closure"` 包含可执行或可恢复的 phase、其传递 phase 依赖，以及 pending、claimed、rejected step 的已声明 step 依赖。范围 review 始终保留全 Plan 的重复 phase/step ID 阻断项。`description`、`verification`、`evidence`、`Phase.gate` 和历史 `<vectl_gate_scope>` 块均按普通文本展示：它们不会生成校验项，不会扩大 review 范围，也不会影响 gate 就绪状态。

当 `max_issues=N` 时，每个集合分别获得 N 行预算：L1 issue、L2 phase 行、L3 phase 标题、L3 全局 step 行、L3 phase 依赖 link、L3 step 依赖 link、L4 ref 行、L4 全局 link、gate pending step 和 downstream phase。L3 的两类依赖 link 与 L4 link 各自使用全局 N-link 预算。当前错误、当前警告、历史警告依次渲染。所有用户可控标量都会转义控制字符/换行，并在转义后限制为 160 个码点；L1 issue 消息限制为 512 个码点。完整响应还有固定 65,536 码点上限；若最后保护触发，结果只保留元数据，把所有明细 `rendered_*` 计数置零，并设置 `output_safety_truncated: true`。

每个受影响部分都输出限流前 `total_*`、最终实际保留的 `rendered_*`、计数截断标志和值截断标志；L4 另有 `ref_detail_truncated`。计数标志比较总数与实际渲染行/link；值标志只描述已渲染值的裁剪，整行省略由计数标志表达。`max_issues=None` 保留 review 的全部行、link 和值，不应用上述 160/512 标量限制或 65,536 码点总上限。

### 可选的 Plan 变更准入

Vectl 可以为所覆盖的 Plan 定义变更调用一个进程启动时冻结的本地适配器。默认行为要求下面三个变量全部不存在（`not_configured`）；如需启用，请在启动 CLI/MCP 进程前设置完整配置：

```bash
export VECTL_PLAN_ADMISSION_EXECUTABLE=/absolute/path/to/adapter
export VECTL_PLAN_ADMISSION_EXECUTABLE_SHA256=sha256:<64位小写十六进制>
export VECTL_PLAN_ADMISSION_TIMEOUT_SECONDS=30
```

部分配置或无效配置会拒绝写入。私有事务用权威 Plan 原始字节执行 CAS，只序列化一次最终候选内容，要求适配器的批准绑定 `candidate_sha256`，并原子写入完全相同的字节。MCP `vectl_mutate`（单项和批量）以及 CLI `add-step`、`add-steps`、`edit-step`、`remove-step`、`move-step`、`add-phase`、`edit-phase`、`edit-plan` 都使用该事务。成功的受覆盖变更会输出 `mutation_receipt: <JSON>`，schema 为 `vectl.plan-mutation.receipt.v1`；其中 `plan_applied` 和 `git_committed` 分开报告，因为 Git 提交仍是尽力而为。上述八个 CLI 路由成功时，先输出各命令现有的人类可读前缀，保持现有提示、详情和后续操作信息，然后只在最后输出一行完整 receipt。校验、已配置准入、CAS、持久化或 receipt 校验失败时不输出 receipt。指向当前仓库之外 Plan 的 CLI 显式路径只写 Plan，并报告 `git_attempted: false`。

适配器是同一 UID 下受信任的本地操作员配置。诊断脱敏只为普通适配器错误提供有界、尽力而为的意外泄露清理；它不提供 sandbox、凭据隔离，也不承诺遏制恶意或已被入侵的适配器。该适配器本来就能读取同一 UID 的资源并修改仓库状态。故意用控制字符拆分字段、含多个 `@` 的异常 authority、双重百分号编码、任意 Unicode 空白混淆和解析器歧义不属于此诊断合同。

原始 bytes 或解码文本超过 8192 个单位，或 Python `str` 含 surrogate 码点时，sanitizer 固定返回 `admission diagnostic unavailable`。其他输入会先捕获普通完整的 `Authorization: Bearer <payload>`、`Authorization: Basic <payload>`、`Authorization: Digest <payload>` 字段，以及常见的 `authorization`、token/password/passwd、`api_key`/`api-key`/`apikey`、credential 和 secret 赋值，再执行受保护 Plan 匹配；捕获值替换为 `<redacted>`。明显的不透明 token、精确或有区分度的 Plan 标量值、临时路径和绝对 POSIX 路径尾部也会被替换。稳定错误代码、合同字段路径和违规类别继续保留；诊断主体变成 `<step-id>`，受保护值变成 `<plan-content>`，规范化结果最多 1024 个码点。

URL 使用普通 lexical 匹配和一次百分号解码。语法正常的 userinfo，以及路径、查询或 fragment 中常见的秘密类组件，会使整个 URL 替换为 `<redacted>`。真正安全的 URL 按原样保留，即使后续文本含邮箱地址或 `@mention`。语义占位符按逆创建顺序恢复，因此安全 URL 内嵌套的 `<admission-temp>` 仍能正确恢复。创建工作区前，Vectl 会拒绝仓库内的临时父目录。工作区/私有文件失败使用 `admission private file setup or write failed`；非字符串 decision 使用 `admission verdict decision is invalid`；读取失败使用 `admission verdict file is unavailable`。初始 base/candidate 身份设置失败使用固定文本；输入缺失、不可读、被替换或不再是常规文件时返回固定且零写入的身份失败。完整边界见[准入变更 RFC](docs/RFC-admitted-plan-mutation.md)。

对于 selected-execution 检查，`tests/expected_red.py run` 会在内部证据 envelope 中记录 pytest 的真实非零 `exit_code` 和 `outcome: "red"`，随后外层适配器进程以 0 退出，让生产 Gate 解析该结果。外层 0 仅确认传输成功，测试结果仍由内部字段表达。

---

## 技术细节

架构、CAS 安全、测试覆盖（Hypothesis 状态机验证）：[docs/DESIGN.md](docs/DESIGN.md)。
