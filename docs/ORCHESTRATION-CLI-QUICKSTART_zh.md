# Orchestration CLI 快速开始

[English](ORCHESTRATION-CLI-QUICKSTART.md)

`vectl orch` 是 `vectl` 的 orchestration 操作入口，用来启动、观察、控制、恢复一次 orchestration run。

## 1. 先确认 CLI 可用

```bash
uv run vectl orch --help
```

查看配置是否正常：

```bash
uv run vectl orch config-show
uv run vectl orch config-validate
uv run vectl orch config-tools
```

如果你想看展开后的生效配置：

```bash
uv run vectl orch config-show --effective
```

## 2. 最小启动流程

### 启动一个 run

```bash
uv run vectl orch run
```

或指定 step：

```bash
uv run vectl orch run core.ready
```

仅做校验、不真正执行：

```bash
uv run vectl orch run core.ready --dry-run --json
```

## 3. 查看运行状态

列出 run：

```bash
uv run vectl orch runs
```

查看最新 run 状态：

```bash
uv run vectl orch status --latest
```

查看指定 run：

```bash
uv run vectl orch status RUN_ID
```

> **选择器安全（硬化保证）**：`--latest` 在没有可匹配的 run 时会显式报错；`resume`、`recover`、`stop` 等变更类命令在缺少 `RUN_ID` 且没有 `--latest` 时也会显式报错，不会静默回退。

查看事件流：

```bash
uv run vectl orch events --latest
```

查看日志：

```bash
uv run vectl orch logs --latest
# 或
uv run vectl orch logs --run RUN_ID
```

查看工件：

```bash
uv run vectl orch artifacts --latest
```

查看控制动作：

```bash
uv run vectl orch actions --latest
```

## 4. 控制 run

### 暂停调度

```bash
uv run vectl orch pause RUN_ID --reason "maintenance"
```

### 恢复调度

```bash
uv run vectl orch unpause RUN_ID --reason "resume"
```

### 请求停止

```bash
uv run vectl orch stop RUN_ID --reason "stop requested"
```

> `stop` 会排队写入 `control.stop` 请求，不是 CLI 边界上的同步立即终止。

## 5. 恢复与续跑

恢复最近一次 run：

```bash
uv run vectl orch resume --latest
```

恢复指定 run：

```bash
uv run vectl orch resume RUN_ID
```

仅校验恢复路径：

```bash
uv run vectl orch resume RUN_ID --dry-run --json
```

执行 recovery：

```bash
uv run vectl orch recover RUN_ID
```

> 旧命令 `vectl repair continuity` 已退役，请统一使用 `vectl orch recover`。

只做 recovery 诊断：

```bash
uv run vectl orch recover RUN_ID --dry-run --json
```

> **Recovery 行为约束（硬化保证）**：
> - `--dry-run` 只诊断，不修改状态。
> - 非 `--dry-run` 会执行实际恢复动作。
> - 恢复遵循 **无静默删除不变性**：不会在没有显式确认语义的情况下静默删除运行数据。
> - `--latest` 在找不到 run 时会显式报错，不会静默回退或创建新 run。

## 6. 清理旧数据

预览 prune：

```bash
uv run vectl orch prune --older-than 7 --dry-run --json
```

真正 prune：

```bash
uv run vectl orch prune --older-than 7 --force
```

## 7. 常用输出格式

### 人类可读

```bash
uv run vectl orch status --latest
```

### JSON

```bash
uv run vectl orch status --latest --json
```

### JSONL

```bash
uv run vectl orch events --latest --jsonl
```

## 8. 指定计划/配置目标

多数子命令都支持：

```bash
--plan PATH
```

例如：

```bash
uv run vectl orch run resolver-phase.step1 --plan /tmp/orch_test_plan.yaml
```

## 9. 用环境变量覆盖运行目录

例如把工件和 workspace 放到临时目录：

```bash
VECTL_ORCH_RUNTIME_ARTIFACT_ROOT=/tmp/vectl_runs \
VECTL_ORCH_RUNTIME_WORKSPACE_ROOT=/tmp/vectl_workspaces \
uv run vectl orch config-show --effective --plan /tmp/orch_test_plan.yaml
```

> **已知非阻塞环境限制（Non-blocking Debt）**：
> - **临时目录中的 `uv run vectl`**：如果你直接在临时目录内执行 `uv run vectl`，而该目录缺少 `.venv` 或 `pyproject.toml`，命令解析可能失败。建议从项目根目录运行，并只用 `VECTL_ORCH_*` 重定向工件/workspace 路径。
> - **隔离 worktree 守卫**：在 isolated worktree 模式下，worktree 安全守卫会主动生效，因此行为可能与普通模式略有不同。这是预期行为，主工作树中的 `plan.yaml` 仍然是权威来源。

## 10. 最推荐的新手顺序

```bash
uv run vectl orch --help
uv run vectl orch config-show
uv run vectl orch config-validate
uv run vectl orch run --dry-run --json
uv run vectl orch runs
uv run vectl orch status --latest
uv run vectl orch events --latest
uv run vectl orch artifacts --latest
```

## 11. 常见命令速查

```bash
uv run vectl orch run [STEP_ID]
uv run vectl orch resume [RUN_ID|--latest]
uv run vectl orch recover [RUN_ID|--latest]
uv run vectl orch runs
uv run vectl orch status [RUN_ID|--latest]
uv run vectl orch events [RUN_ID|--latest]
uv run vectl orch logs [--run RUN_ID|--latest]
uv run vectl orch artifacts [RUN_ID|--latest]
uv run vectl orch actions [--run RUN_ID|--latest]
uv run vectl orch pause [RUN_ID|--latest]
uv run vectl orch unpause [RUN_ID|--latest]
uv run vectl orch stop [RUN_ID|--latest]
uv run vectl orch prune [--older-than DAYS]
uv run vectl orch config-show
uv run vectl orch config-validate
uv run vectl orch config-tools
```
