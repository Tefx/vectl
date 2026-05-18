# orch_app / driver Budget Closure Receipt

Step: `invar_guard_remediation.fix-orch-app-driver-budget-closure`

## refs Read Confirmation

- `INVAR.md` — read. Relevant rule: Core code needs `@pre`/`@post` and doctest before implementation; Shell is for filesystem/runtime/time/subprocess effects; Shell functions should return `Result[T, E]`; escape hatches must be rare and reasoned.
- `AGENTS.md` — read. Relevant rule: `plan.yaml` is managed state and must not be edited directly; vectl CLI/MCP plan mutation is reserved to orchestration.
- `src/vectl/orch_app.py` — read. Relevant surfaces: composition-root facade, runtime start/resume/recover paths, drive foreground supervision, control consumption, and resolver/review parsing helpers.
- `src/vectl/core/orchestration/runner_payloads.py` — added. Relevant surface: contracted pure extraction for runner-output structured-review and resolver-report payload discovery formerly carried as app-shell parser escape hatches.
- `src/vectl/orchestration/driver.py` — read. Relevant surfaces: active-drive state machine, control consumption, resolver/replan continuations, resume, and recovery.
- Relevant app/driver tests — read. Relevant tests include `tests/orchestration/unit/test_orch_app.py`, `tests/orchestration/unit/test_driver_loop_proof.py`, and `tests/orchestration/unit/test_drive_foreground_progress.py`.
- Optional `tools/vectl/README.md` — attempted; file is not present in this isolated worktree.
- `CONSTITUTION.md` — checked by glob; no file is present in this isolated worktree.

## Residual Disposition Register

This slice did not add, broaden, or lower any guard escape-hatch budget controls. It removed six `src/vectl/orch_app.py` `shell_result` parser escape hatches by extracting the pure runner-output payload discovery policy into contracted Core helpers with doctests, while preserving app-level call sites. It records ownership and non-intersection for the remaining app/driver residuals so the final gate can distinguish this slice from unrelated residual families.

| Scope | Family | BEFORE | AFTER | Disposition | Evidence / Owner |
|---|---:|---:|---:|---|---|
| `src/vectl/orch_app.py` | `function_size` | `10/2` | `10/2` | `explicit_residual_owned_by_slice` | Existing hatches are narrow shell coordination paths: runtime prepare/start/collect, event emission, run-store persistence, control polling, worktree cleanup, recovery/projection replay. No broad suppression or budget lowering was introduced. |
| `src/vectl/orch_app.py` | `shell_result` | `13/2` | `7/2` | `partially_closed_with_explicit_residual_owned_by_slice` | Six pure runner-output parser hatches were closed by `src/vectl/core/orchestration/runner_payloads.py`; remaining hatches are public compatibility wrappers/factory or dispatch mappers that callers consume directly. Structural extraction for routing presenter data remains in `src/vectl/core/orchestration/routing_presenters.py`; remaining compatibility wrappers are explicitly carried forward pending a public API migration. |
| `src/vectl/orchestration/driver.py` | `function_size` | `7/2` | `7/2` | `explicit_residual_owned_by_slice` | Existing hatches are ordered state-machine shell loops coordinating drive store, control channel, resolver/planner gateways, leases, active child runs, liveness, barriers, resume, and recovery. No handler-only cleanup or semantic narrowing was performed. |
| `<project>` | `function_size` | `31/5` | `31/5` | `no_regression_from_slice` | Project-level counter is unchanged by this slice; app/driver residual ownership is recorded above while unrelated files remain owned by their respective residual families. |
| `<project>` | `shell_result` | `72/5` | `66/5` | `improved_no_regression_from_slice` | Project-level shell-result counter decreased by six due to app parser extraction; unrelated files remain owned by their respective residual families. |
| `<project>` | weighted budget | `376/15` | `358/15` | `improved_no_regression_from_slice` | Weighted budget decreased by 18 due to the six removed app parser shell-result hatches. |

## Runtime / Control / Lifecycle Preservation Proof

- Runtime launch semantics are preserved by unchanged code paths that targeted tests exercise: `_start_runtime_execution()` still validates role/prompt authority before runtime prepare, calls `prepare()` before prompt materialization, resolves the runtime worktree path, then starts the runner with populated prompt artifact paths.
- Resolver/review runner-output semantics are preserved by app tests that still invoke the `vectl.orch_app` routing path while parser logic now delegates to contracted Core helpers.
- Control consumption semantics are preserved by unchanged app and driver code paths: run-scoped pause/unpause/stop are consumed in `_collect_and_route_terminal()` and drive-scoped pause/unpause/stop are consumed before drive-loop evaluation in `_consume_drive_control()`.
- Active-drive ordering, barriers, resume, and recovery semantics are preserved by unchanged driver state-machine code paths; targeted driver tests exercise start, loop, terminal immediate return, resume restoration, recover dry-run/barrier/terminal handling, transition validation, and app delegation.

## Checklist Receipt

- BEFORE `uvx invar-tools guard --all` excerpt for `orch_app.py`, `driver.py`, and project-level budget counters is pasted from current mainline — done in final evidence.
- AFTER `uvx invar-tools guard --all` excerpt shows closure or explicit residual ownership for these families without project-level budget regression — done in final evidence and this register.
- Targeted app/driver regression tests or smoke checks pass and output is pasted — done in final evidence.
- Runtime/control/lifecycle behavior is preserved with explicit evidence — done in final evidence and preservation proof above.
- No broad suppression, budget lowering, or handler-only narrowing is used — done; no source escape hatches or guard budgets were changed.
- Residual disposition register is updated for orch_app/driver/project-level findings — done in this document.
