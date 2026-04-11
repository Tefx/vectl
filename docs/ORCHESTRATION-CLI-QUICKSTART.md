# Orchestration CLI Quick Start

[中文](ORCHESTRATION-CLI-QUICKSTART_zh.md)

`vectl orch` is the orchestration entrypoint in `vectl`. You use it to start, inspect, control, and recover orchestration runs.

## 1. Confirm the CLI works

```bash
uv run vectl orch --help
```

Check the current orchestration config:

```bash
uv run vectl orch config-show
uv run vectl orch config-validate
uv run vectl orch config-tools
```

If you want the fully expanded effective config:

```bash
uv run vectl orch config-show --effective
```

## 2. Minimal start flow

### Start a run

```bash
uv run vectl orch run
```

Or start a specific step:

```bash
uv run vectl orch run core.ready
```

Validate only, without executing:

```bash
uv run vectl orch run core.ready --dry-run --json
```

## 3. Inspect run state

List runs:

```bash
uv run vectl orch runs
```

Show the latest run status:

```bash
uv run vectl orch status --latest
```

Show a specific run:

```bash
uv run vectl orch status RUN_ID
```

> **Selector safety (hardened behavior):** if `--latest` cannot resolve a run, the command fails explicitly. Mutating commands such as `resume`, `recover`, and `stop` also fail explicitly when you omit both `RUN_ID` and `--latest`.

Show the event stream:

```bash
uv run vectl orch events --latest
```

Show logs:

```bash
uv run vectl orch logs --latest
# or
uv run vectl orch logs --run RUN_ID
```

Show artifacts:

```bash
uv run vectl orch artifacts --latest
```

Show control actions:

```bash
uv run vectl orch actions --latest
```

## 4. Control a run

### Pause dispatch

```bash
uv run vectl orch pause RUN_ID --reason "maintenance"
```

### Resume dispatch

```bash
uv run vectl orch unpause RUN_ID --reason "resume"
```

### Request stop

```bash
uv run vectl orch stop RUN_ID --reason "stop requested"
```

> `stop` queues a `control.stop` request. It is not a synchronous, immediate process kill at the CLI boundary.

## 5. Resume and recover

Resume the latest run:

```bash
uv run vectl orch resume --latest
```

Resume a specific run:

```bash
uv run vectl orch resume RUN_ID
```

Validate the resume path only:

```bash
uv run vectl orch resume RUN_ID --dry-run --json
```

Run recovery:

```bash
uv run vectl orch recover RUN_ID
```

> The legacy command `vectl repair continuity` is retired. Use `vectl orch recover` as the supported recovery surface.

Run recovery diagnostics only:

```bash
uv run vectl orch recover RUN_ID --dry-run --json
```

> **Recovery semantics (hardened behavior):**
> - `--dry-run` is diagnostic only and does not mutate state.
> - Non-`--dry-run` performs actual recovery actions.
> - Recovery follows the **no-silent-deletion invariant**: run data is never deleted silently without explicit confirmation semantics.
> - `--latest` fails explicitly if no run exists; it does not silently fall back or create a new run.

## 6. Prune old data

Preview prune:

```bash
uv run vectl orch prune --older-than 7 --dry-run --json
```

Apply prune:

```bash
uv run vectl orch prune --older-than 7 --force
```

## 7. Common output formats

### Human-readable

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

## 8. Target a specific plan/config

Most subcommands support:

```bash
--plan PATH
```

Example:

```bash
uv run vectl orch run resolver-phase.step1 --plan /tmp/orch_test_plan.yaml
```

## 9. Override runtime directories with environment variables

Example:

```bash
VECTL_ORCH_RUNTIME_ARTIFACT_ROOT=/tmp/vectl_runs \
VECTL_ORCH_RUNTIME_WORKSPACE_ROOT=/tmp/vectl_workspaces \
uv run vectl orch config-show --effective --plan /tmp/orch_test_plan.yaml
```

> **Known non-blocking environment debt:**
> - **`uv run vectl` from a temporary directory:** if you execute `uv run vectl` inside a temp directory that does not contain the expected Python project context (`.venv`, `pyproject.toml`), command resolution can fail. Prefer running from the project root and only redirect artifact/workspace paths with `VECTL_ORCH_*`.
> - **Isolated worktree guard behavior:** isolated worktree execution may differ slightly from non-isolated execution because worktree safety guards are intentionally active. The main worktree `plan.yaml` remains authoritative.

## 10. Recommended first-run order

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

## 11. Command reference cheat sheet

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
