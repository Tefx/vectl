# RFC: `vectl_decide` session ID 类型化

**状态:** 提案
**日期:** 2026-03-26
**作者:** tefx + Claude Opus 4.6

## 问题

`vectl_decide` 在 `session: "reuse"` 时返回的 `action.task_id` 是一个**无类型的不透明字符串**。编排器需要自行判断这个 ID 属于哪个 runner 命名空间：

| Runner | ID 格式 | 示例 |
|--------|---------|------|
| Task subagent (OpenCode) | `ses_xxx` | `ses_abc123def456` |
| Claude CLI | UUID | `01234567-89ab-cdef-0123-456789abcdef` |

编排器拿到 `task_id` 后，根据 `action.agent` 选择 dispatch 路径：

- `python-senior` / `frontend-engineer` → Claude CLI lane → `--resume <task_id>`
- 其他 agent → Task subagent

**但 session reuse 场景中，`task_id` 可能来自上一轮的不同 runner。** 典型失败路径：

```
Step A 首次用 Task subagent 执行 → completed_result.task_id = "ses_xxx"
    ↓
vectl_decide 将 "ses_xxx" 存入 _session_registry
    ↓
Step B (同 agent) 需要 reuse → action.task_id = "ses_xxx"
    ↓
编排器因 agent 路由将其发往 Claude CLI lane
    ↓
claude --resume "ses_xxx" → API 拒绝: "must start with ses" (UUID 格式校验失败)
```

此外，还存在反向路径：Claude CLI session UUID 被传给 Task subagent 的 resume 接口。

### 当前缓解措施

1. **编排器 prompt 规则**（概率性）："If Claude reuse metadata is invalid for Claude (task_id not UUID), do NOT call --resume"
2. **`claude-phase-stream` preflight 校验**（机械性）：非 UUID 的 `--resume` 值被静默降级为 fresh session

这两层防线能阻止 API 错误，但**每次触发都意味着一次本应成功的 session reuse 被降级为 fresh session**，浪费了已有的上下文缓存。

### 根因

`_session_registry` 和 `Action.task_id` 不携带 runner 来源信息。`vectl_decide` 在做 session reuse 决策时无法区分 ID 属于哪个命名空间，因为**这个信息在 `CompletedResult` / `RunningTask` 输入时就已经丢失了**。

## 提案

### 方案 A：输入侧标记 runner（推荐）

在 `RunningTask` 和 `CompletedResult` 中新增 `runner` 字段：

```python
class RunningTask(BaseModel):
    step_id: str
    agent: str
    task_id: str
    dispatched_at: float
    runner: Literal["claude", "task"] = "task"  # 新增，默认向后兼容

class CompletedResult(BaseModel):
    step_id: str
    task_id: str
    status: str
    output_summary: str
    runner: Literal["claude", "task"] = "task"  # 新增，默认向后兼容
```

`_session_registry` 改为存储 `(task_id, runner)` 元组：

```python
_session_registry: dict[str, tuple[str, str]] = {}  # step_id -> (task_id, runner)
```

`Action` 输出新增 `runner` 字段：

```python
class Action(BaseModel):
    action: Literal["claim_and_dispatch", "complete", "wait", "escalate"]
    step_id: str | None = None
    agent: str | None = None
    session: Literal["fresh", "reuse"] | None = None
    task_id: str | None = None
    task_runner: Literal["claude", "task"] | None = None  # 新增：task_id 的来源 runner
    # ...
```

`should_reuse_session` 同时返回 runner 信息，使编排器可以做**同 runner 命名空间校验**：

```python
def should_reuse_session(step_id, parent_step_id):
    # ...
    task_id, runner = _session_registry[parent_step_id]
    return (True, task_id, runner)
```

编排器侧的逻辑变为：

```
IF action.session == "reuse":
    IF dispatch_runner == action.task_runner:
        使用 action.task_id 做 resume
    ELSE:
        降级为 fresh session（跨 runner reuse 无意义）
```

### 方案 B：`vectl_decide` 内部做 runner-agent 匹配（备选）

`vectl_decide` 已经知道 `action.agent`，可以在内部判断目标 runner（基于 agent 名是否在 Claude lane 列表中），然后只在 runner 匹配时返回 `session: "reuse"`。

**优点**：编排器无需额外逻辑。
**缺点**：`vectl_decide` 需要硬编码或配置 Claude lane agent 列表，引入了对编排器路由策略的耦合。

### 推荐

**方案 A**。原因：

1. **关注点分离**：`vectl_decide` 只负责"是否 reuse"+ 提供完整元数据，路由决策留给编排器
2. **可扩展**：未来新增 runner 类型（如 Gemini CLI）只需扩展 `runner` 枚举，不改 decide 逻辑
3. **向后兼容**：`runner` 字段有默认值 `"task"`，现有编排器不传也不会 break

## 影响范围

| 组件 | 变更 |
|------|------|
| `models.py` | `RunningTask`, `CompletedResult`, `Action` 各加一个可选字段 |
| `decide.py` | `_session_registry` 改为存储 `(task_id, runner)` 元组 |
| `mcp_server.py` | `vectl_decide` 参数 schema 扩展（向后兼容） |
| 编排器 prompt | 新增 `action.task_runner` 的使用规则 |
| `claude-phase-stream` | preflight 校验保留作为 defense-in-depth |

## 迁移

- 所有新增字段有默认值，**MCP schema 向后兼容**
- 现有编排器不传 `runner` → 默认 `"task"` → 行为与当前一致
- 升级后的编排器开始传 `runner` → `vectl_decide` 可以做精确匹配

## 不做的事

- **不在 `vectl_decide` 中硬编码 Claude lane agent 列表** — 这是编排器的路由策略，不是 plan 状态管理器的职责
- **不移除 `claude-phase-stream` 的 preflight 校验** — defense-in-depth 原则，即使 vectl 侧修了也保留机械防线
