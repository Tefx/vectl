# Orchestration CLI Quick Start

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

> 注意：`stop` 会排队写入 `control.stop` 请求，不是 CLI 同步立即终止。

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

做 recovery：

```bash
uv run vectl orch recover RUN_ID
```

只做 recovery 诊断：

```bash
uv run vectl orch recover RUN_ID --dry-run --json
```

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

默认就是：

```bash
uv run vectl orch status --latest
```

### JSON

适合集成脚本：

```bash
uv run vectl orch status --latest --json
```

### JSONL

适合事件流：

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
