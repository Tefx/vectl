# Orchestration Plane CLI / Config / Observability 设计说明（中文同步版）

[English Source of Truth](ORCHESTRATION-PLANE-CLI-CONFIG-OBSERVABILITY-DESIGN.md)

> 本文件是中文同步版。**英文版 `ORCHESTRATION-PLANE-CLI-CONFIG-OBSERVABILITY-DESIGN.md` 是唯一权威来源**。如有歧义，以英文版为准。

本文只同步本轮 CLI 黑盒测试硬化直接影响的契约点，便于中文读者快速确认最新行为。

---

## 1. CLI 命令面（本轮同步要点）

### 1.1 `resume [RUN_ID|--latest]`

- `--latest` 找不到可恢复 run 时，显式以 **exit code 2** 失败
- 未提供 `RUN_ID` 且未传 `--latest` 时，显式以 **exit code 3** 失败
- 不允许隐式回退，也不会偷偷创建新 run

### 1.2 `recover [RUN_ID|--latest]`

- `--dry-run` 只做诊断，不修改状态
- 非 `--dry-run` 会执行实际恢复动作
- `--latest` 找不到 run 时，显式以 **exit code 2** 失败
- 未提供 `RUN_ID` 且未传 `--latest` 时，显式以 **exit code 3** 失败
- 恢复流程遵循 **无静默删除不变性**：不会静默丢弃运行证据

### 1.3 `status [RUN_ID|--latest]`

- `status --latest` 找不到 run 时，显式以 **exit code 2** 失败
- 不再把“没有 run”静默表现成成功但空结果的选择器路径

### 1.4 `pause / unpause / stop`

- 这些命令支持 `RUN_ID` 或 `--latest`
- `stop` 在 CLI 边界上是**排队控制请求**，不是同步立即终止
- active run 的控制请求会进入 action / control channel，并由 orchestration 消费

---

## 2. Exit Code（本轮重点）

| Code | 含义 |
| --- | --- |
| 0 | Success |
| 1 | General error |
| 2 | Not found |
| 3 | Validation error |
| 4 | Recovery required |
| 5 | Internal error |
| 127 | Command not registered |

本轮黑盒测试特别强化了 2 / 3 在 selector safety 场景下的可预测性。

---

## 3. 配置与优先级（本轮同步要点）

### 3.1 配置发现顺序

仍以英文版 §8.1 为准，核心原则不变：

- explicit CLI target 优先
- 其后是环境变量
- 再后是配置文件/默认值

### 3.2 环境变量覆盖

黑盒测试确认以下 runtime root override 仍有效：

- `VECTL_ORCH_RUNTIME_ARTIFACT_ROOT`
- `VECTL_ORCH_RUNTIME_WORKSPACE_ROOT`

### 3.3 Frozen Config Snapshot

run 启动时冻结 `config.snapshot.yaml` 的契约保持有效；resume/recover 仍以 frozen snapshot 为权威输入之一。

---

## 4. Observability / Recovery（本轮同步要点）

### 4.1 Recovery hardened semantics

- `recover --dry-run`：只诊断
- `recover`：允许实际恢复动作
- `no_silent_deletion_preserved` 语义已按黑盒测试对齐
- bad-path recovery（损坏 event / projection / continuity）不会假绿通过

### 4.2 Control / Action observability

active-run control 场景下，pause / unpause / stop 请求会通过 control/action surface 暴露，不再只是“写入请求但无消费痕迹”的状态。

---

## 5. 非阻塞环境限制（中文同步）

### 5.1 Isolated Worktree Mode

- isolated worktree 下会触发 worktree safety guard
- 某些验证在隔离 worktree 内可能与主工作树执行结果不同
- 主工作树中的 `plan.yaml` 始终保持权威

### 5.2 Temporary Directory Execution

- 如果你直接在临时目录中运行 `uv run vectl`，而该目录缺少 `.venv` 或 `pyproject.toml`，命令解析可能失败
- 推荐做法：**在项目根目录执行命令，仅用环境变量重定向 artifact/workspace 路径**

### 5.3 为什么这些是 non-blocking debt

这些限制被归类为非阻塞，因为：

1. 只影响特定环境模式
2. 标准工作流不受影响
3. 有明确 workaround
4. 不破坏本轮硬化重点：**selector safety** 与 **no-silent-deletion invariant**

---

## 6. 使用建议

如果你需要完整契约、逐节定义、或实现 authority，请直接阅读英文版：

- `docs/ORCHESTRATION-PLANE-CLI-CONFIG-OBSERVABILITY-DESIGN.md`

如果你只想快速上手 CLI，请阅读：

- `docs/ORCHESTRATION-CLI-QUICKSTART.md`
- `docs/ORCHESTRATION-CLI-QUICKSTART_zh.md`
