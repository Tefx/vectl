# orch_removal_scope_matrix Evidence

Step: `orch_removal_scope_matrix`  
Spec link: `user-request:2026-06-17#remove-built-in-orchestrator-runtime`  
Mode: investigation/read-only proof artifact; no product source/docs/tests were modified.

## refs Read Confirmation

| ref | read status | key passage / insight |
|---|---:|---|
| `tools/vectl/README.md` | NOT READ (ENOENT) | Required path is absent in this worktree (`tools/` does not exist). Fallback read: root `README.md`, which says vectl is a `plan.yaml` control plane, exposes MCP tools including `vectl_decide`, and warns not to hand-edit `plan.yaml`. |
| `user-request:2026-06-17#remove-built-in-orchestrator-runtime` | PROVIDED CONTEXT | Prompt decision: remove built-in `vectl orch` plus orchestration runtime/control/drive/resolver/runner/worktree features; preserve `vectl_decide`; delete `Step.isolation` and `IsolationMode`. |
| `src/vectl/decide.py` | READ | Computes `claim_and_dispatch`/`complete`/`wait`/`escalate` advice and returns `DecideOutput`; preserves caller-owned `advisor_state`/`next_state`. |
| `src/vectl/decision_state.py` | READ | `DecideState` owns completion/session/failure/pending escalation memory; wording still references RuntimeContext/DriverState and should be replaced with caller-owned advisor wording. |
| `src/vectl/model_outputs.py` | READ | `RunningTask`, `CompletedResult`, `Action`, `DecideOutput` define the `vectl_decide` DTO contract; `task_id` is execution identity, reuse uses `reuse_token`/`reuse_runner`. |
| `src/vectl/models.py` | READ | Contains `IsolationMode` enum and `Step.isolation: IsolationMode = IsolationMode.DEFAULT` with authority comments to the isolation doc; direct ISOLATION-REMOVE targets. |
| `src/vectl/cli.py` | READ | Imports `vectl.cli_orchestration*`, creates `orch_app`, and registers `orch run/resume/recover/drive/...`; direct CLI cleanup target. |
| `src/vectl/mcp_server.py` | READ | Registers `vectl_decide` as a core MCP tool and has no `vectl_orch` MCP tool; preserve registration while cleaning wording if needed. |
| `docs/RFC-vectl-decide-advisor-refresh.md` | READ | RFC says `vectl_decide` remains part of vectl, is an advisor not authority, does not mutate plan state, and caller owns state. Preserve. |
| `docs/ORCHESTRATION-PLANE-ISOLATION-SEMANTICS.md` | READ | Authorizes `IsolationMode`/`Step.isolation`; now a deletion target because user explicitly removed that schema. |
| `CONSTITUTION.md` | NOT READ (ENOENT) | No repository constitution exists at worktree root. |

<demolition_warrant>
1. Scale Anchor: Current repo feature set is a single Python package/CLI/MCP control plane; the built-in orchestration runtime is in-repo code, tests, and docs, not a separate hard production service.
2. Dumbest Baseline: Keep `plan.yaml` authority plus existing vectl core/MCP/CLI commands, and expose one deterministic `vectl_decide(running_tasks, completed_results, advisor_state, max_parallelism)` advisor for external callers. No internal runner registry, control loop, run store, resolver, worktree lifecycle, or `vectl orch` CLI is needed.
3. Falsification (The Kill Switch): Deleting built-in orchestration mechanisms does not break a current hard requirement because the user explicitly requested removal. Deleting `vectl_decide` would break DECIDE-PRESERVE, so it is protected.
4. Minimum Remnant: Preserve `src/vectl/decide.py`, `src/vectl/decision_state.py`, `src/vectl/model_outputs.py`, the `vectl_decide` MCP surface, decide tests, and `docs/RFC-vectl-decide-advisor-refresh.md`.
</demolition_warrant>

## Requirement-to-Evidence Matrix

| requirement_id | source_ref + key passage | target_state | evidence_class | required_probe_or_artifact | status |
|---|---|---|---|---|---|
| ORCH-REMOVE-SCOPE | User request removes built-in `vectl orch`; `src/vectl/cli.py` proves `orch` group; `src/vectl/orchestration/` proves runtime/control/drive/resolver/runner code. | All orchestration-owned source/docs/tests assigned to delete or cleanup owners. | artifact_review | Deletion matrix below names source dirs/files, tests, repro/live smoke, docs, and residual grep discoveries. | OWNED |
| DECIDE-PRESERVE | Decide RFC: `vectl_decide remains part of vectl`; advisor is non-authority and caller-owned. | Decide surfaces remain supported; final probes fail if required paths disappear. | artifact_review | Preservation matrix and path-presence command below. | OWNED |
| ISOLATION-REMOVE | User request removes `Step.isolation`/`IsolationMode`; `models.py`, `io.py`, tests, and isolation doc currently expose them. | Core model/IO/tests/docs no longer expose isolation schema or deleted-doc references. | artifact_review | Affected-file map below. | OWNED |
| FINAL-GREP | User request requires final forbidden-term closure. | Closure gate covers product/docs/tests and exact deletion paths while excluding plan history. | artifact_review | Exact grep scope, absence list, preserved-path presence list, allowed exceptions below. | OWNED |

## deletion_targets_confirmed

### Source deletion targets — owner: `orch_source_delete`

- `src/vectl/orchestration/` — runtime/control/drive/resolver/runner/run-store/roster/recovery/continuity package.
- `src/vectl/core/orchestration/` — orchestration-only core helpers/config/projections/routing.
- `src/vectl/shell/orchestration/` — orchestration prompt artifact shell helper.
- `src/vectl/orch_app.py` — built-in orchestration app facade.
- `src/vectl/cli_orchestration.py`
- `src/vectl/cli_orchestration_common.py`
- `src/vectl/cli_orchestration_drive_commands.py`
- `src/vectl/cli_orchestration_drive_helpers.py`
- `src/vectl/cli_orchestration_inspect_commands.py`
- `src/vectl/cli_orchestration_run_commands.py`
- `src/vectl/cli_orchestration_runtime_helpers.py`

### Source cleanup targets — owner: `orch_surface_cleanup`

- `src/vectl/cli.py` — remove `vectl.cli_orchestration*` imports, `orch_app` Typer group/sub-typers, and all `orch_*` command registrations; preserve normal vectl commands and `mcp`.
- `src/vectl/cli_plan_common.py` — remove stale `vectl.orch_app` / `vectl.orchestration.recovery` imports and duplicate `orch_app` Typer group block; preserve plan CLI helpers.
- `pyproject.toml` — optional cleanup if package-data or marker text still references removed driver/orchestration package; do not remove core dependencies unless tests prove unused.

### Docs deletion targets — owner: `orch_doc_delete`

Exact doc deletion set:

- `docs/ORCHESTRATION-*.md` including `_zh` variants.
- `docs/ORCHESTRATION-PLANE-ISOLATION-SEMANTICS.md` (explicit ISOLATION-REMOVE target; covered by previous glob but named separately by requirement).
- `docs/RFC-orch-drive.md`
- `docs/RFC-opencode-orchestration-runner.md`
- `docs/ADR-orchestration-*.md`
- `docs/ORCH-*.md`
- `docs/RFC-role-profile-overrides.md`
- `docs/invar_guard_remediation/orch_app_driver_budget_closure_receipt.md`
- Archive proof artifacts under `docs/archive/`: `consumer_boundary_conformance_report.yaml`, `consumer_boundary_conformance_verify.py`, `independent_verification_report.md`, `orch_workflow_validation.yaml`, `verification_report.yaml`, and `docs/archive/legacy-proof/`.

### Test deletion targets — owner: `orch_test_delete`

Exact required deletions:

- `tests/test_runtime_context_vertical_slice.py`
- `tests/test_runtime_lifecycle.py`
- `tests/test_extended_runner_protocol.py`
- `tests/test_orch_cli_surface.py`
- `tests/repro/test_repair_continuity_black_box.py`
- `tests/repro/test_orch_*.py`
- `tests/repro/orch_liveness_probe.py`
- `tests/repro/liveness_drive.py`
- `tests/live_smoke/` (all current files are runner/orchestration live smoke: codex/opencode dispatch, drive autonomy, resolver, long runtime, orchestrator e2e, helpers)

Additional orchestration-owned test dirs/fixtures:

- `tests/orchestration/`
- `tests/fixtures/orchestration/`
- `tests/fixtures/helpers/extended_runner_gaps.py`

Residual orchestration-dependent tests discovered by grep outside the initial list:

- `tests/test_codex_parser_behavior.py`
- `tests/test_codex_resume_behavior.py`
- `tests/test_gemini_parser_behavior.py`
- `tests/test_gemini_resume_behavior.py`
- `tests/repro/vertical_slice_probe.py`
- `tests/test_cli.py::TestDriveCLI` only — remove this class/section; preserve unrelated CLI tests.

## exact_test_deletion_targets_confirmed

Confirmed present or matched by filesystem probe:

```text
tests/test_runtime_context_vertical_slice.py
tests/test_runtime_lifecycle.py
tests/test_extended_runner_protocol.py
tests/test_orch_cli_surface.py
tests/repro/test_repair_continuity_black_box.py
tests/repro/test_orch_cli_case_lifecycle.py
tests/repro/test_orch_cli_concurrency.py
tests/repro/test_orch_cli_control_consumption.py
tests/repro/test_orch_cli_full_flow.py
tests/repro/test_orch_cli_prune_config.py
tests/repro/test_orch_cli_recovery_faults.py
tests/repro/test_orch_cli_recovery_live.py
tests/repro/test_orch_cli_recovery_red.py
tests/repro/test_orch_cli_runner_faults.py
tests/repro/test_orch_cli_selector_safety.py
tests/repro/test_orch_liveness.py
tests/repro/test_orch_observability_red.py
tests/repro/orch_liveness_probe.py
tests/repro/liveness_drive.py
tests/live_smoke/
```

## exact_doc_deletion_targets_confirmed

Confirmed by filesystem probe:

```text
docs/ADR-orchestration-plane-reset.md
docs/ADR-orchestration-role-agent-prompt-separation.md
docs/ADR-orchestration-role-profile-config-and-resolver-cleanup.md
docs/ORCH-OPERATOR-CUTOVER-VALIDATION-MIGRATION-STATE-AUDIT.md
docs/ORCHESTRATION-CLI-QUICKSTART.md
docs/ORCHESTRATION-CLI-QUICKSTART_zh.md
docs/ORCHESTRATION-PLANE-ARCHITECTURE.md
docs/ORCHESTRATION-PLANE-CLI-CONFIG-OBSERVABILITY-DESIGN.md
docs/ORCHESTRATION-PLANE-CLI-CONFIG-OBSERVABILITY-DESIGN_zh.md
docs/ORCHESTRATION-PLANE-DISPATCH-AND-PROMPT-POLICY.md
docs/ORCHESTRATION-PLANE-IMPLEMENTATION-DESIGN.md
docs/ORCHESTRATION-PLANE-INTERFACES.md
docs/ORCHESTRATION-PLANE-ISOLATION-SEMANTICS.md
docs/ORCHESTRATION-PLANE-LIVE-AUTHORITY-CONTRACT-LOCK.md
docs/ORCHESTRATION-PLANE-OPERATOR-CONFLICT-RECOVERY.md
docs/ORCHESTRATION-PLANE-ORCH-APP-ROUTING.md
docs/ORCHESTRATION-PLANE-RESOLUTION-CONTRACT.md
docs/ORCHESTRATION-PLANE-RESOLVER-COORDINATION.md
docs/ORCHESTRATION-PLANE-RUNNER-BACKEND.md
docs/ORCHESTRATION-PLANE-RUNTIME-WORKTREE-LIFECYCLE.md
docs/RFC-opencode-orchestration-runner.md
docs/RFC-orch-drive.md
docs/RFC-role-profile-overrides.md
docs/archive/
docs/invar_guard_remediation/orch_app_driver_budget_closure_receipt.md
```

## active_cleanup_targets_confirmed

- `tests/conftest.py` remains present, but remove the active `DRIVER_YAML_SPEC["orchestration"]` block and driver/orchestration fixture tokens tied only to deleted runtime tests. Preserve `pytest_collection_modifyitems` and generic expected-red metadata validation if still used by remaining tests.
- `tests/test_cli.py` remains present, but remove `TestDriveCLI` assertions that `orch` is the supported replacement for old `drive`.
- `src/vectl/cli.py` and `src/vectl/cli_plan_common.py` remain present, but remove active `orch` command wiring.

## preservation_targets_confirmed

Required paths must remain present:

- `src/vectl/decide.py`
- `src/vectl/decision_state.py`
- `src/vectl/model_outputs.py`
- `tests/test_decide.py`
- `tests/test_decide_state_isolation.py`
- `tests/test_mcp.py` (preserve `TestVectlDecide`; remove only unrelated orch assertions if introduced later)
- `docs/RFC-vectl-decide-advisor-refresh.md`

Expected wording replacements while preserving behavior:

- Replace “orchestration advisor” / “orchestrator should” in decide-facing docstrings with “caller-owned automation/dispatch advisor” / “external caller may”.
- Remove RuntimeContext/DriverState references from `DECIDE_RUNTIME_CONTEXT_BOUNDARY` and `DECIDE_STATE_RUNTIME_BOUNDARY`; use caller-owned `advisor_state` / `next_state` language.
- Preserve structured fields: `running_tasks`, `completed_results`, `advisor_state`, `max_parallelism`, `status`, `reason_code`, `actions`, `next_state`, `policy`, `decision_log`, `reuse_token`, `reuse_runner`.
- Keep decide RFC path; if edited, update terminology without deleting the non-goals and caller-owned-state contract.

Required path-presence gate:

```bash
set -euo pipefail
for p in \
  src/vectl/decide.py \
  src/vectl/decision_state.py \
  src/vectl/model_outputs.py \
  tests/test_decide.py \
  tests/test_decide_state_isolation.py \
  tests/test_mcp.py \
  docs/RFC-vectl-decide-advisor-refresh.md; do
  test -f "$p"
done
```

## ISOLATION-REMOVE affected-file map

Owner: `isolation_schema_cleanup`.

- `src/vectl/models.py`: delete `IsolationMode`; delete `Step.isolation`; delete authority comments referencing `docs/ORCHESTRATION-PLANE-ISOLATION-SEMANTICS.md`.
- `src/vectl/io.py`: remove `field_name == "isolation"` cleanup logic and all isolation doc comments.
- `tests/test_io.py`: delete `IsolationMode` import and `TestIsolationCleanup` cases.
- `tests/test_models.py`: delete `IsolationMode` import and `test_isolation_explicit_*` / default isolation assertions.
- `docs/ORCHESTRATION-PLANE-ISOLATION-SEMANTICS.md`: delete file.
- Lowercase IO residue to remove: YAML key `isolation`, refs like `isolation=workspace`, and deleted-doc path references.

## non_goals

- Do not remove `vectl_decide` or its DTOs/tests/RFC.
- Do not edit `plan.yaml` directly.
- Do not inspect/mutate/claim/complete/defer/checkpoint plan state in this worker.
- Do not manually delete registered `.vectl` worktrees.
- Do not remove generic git/linked-worktree plan resolution unless a later phase proves it is solely orchestration-owned; delete only orchestration runtime worktree lifecycle code/docs.

## FINAL-GREP closure gate

Run after downstream deletion/cleanup, from repo root. Scope excludes plan/orchestrator history and virtual/build caches, but covers product source/docs/tests and exact deletion-target paths.

```bash
set -euo pipefail

# 1. Required preserved files must exist.
for p in \
  src/vectl/decide.py \
  src/vectl/decision_state.py \
  src/vectl/model_outputs.py \
  tests/test_decide.py \
  tests/test_decide_state_isolation.py \
  tests/test_mcp.py \
  docs/RFC-vectl-decide-advisor-refresh.md; do
  test -f "$p"
done

# 2. Exact deletion targets must be absent.
for p in \
  src/vectl/orchestration \
  src/vectl/core/orchestration \
  src/vectl/shell/orchestration \
  src/vectl/orch_app.py \
  src/vectl/cli_orchestration.py \
  src/vectl/cli_orchestration_common.py \
  src/vectl/cli_orchestration_drive_commands.py \
  src/vectl/cli_orchestration_drive_helpers.py \
  src/vectl/cli_orchestration_inspect_commands.py \
  src/vectl/cli_orchestration_run_commands.py \
  src/vectl/cli_orchestration_runtime_helpers.py \
  tests/orchestration \
  tests/fixtures/orchestration \
  tests/live_smoke \
  docs/ORCHESTRATION-PLANE-ISOLATION-SEMANTICS.md; do
  test ! -e "$p"
done

# 3. Forbidden active runtime terms must be absent from source/tests/docs.
rg -n --hidden --glob '!plan.yaml' --glob '!.vectl/**' --glob '!.git/**' --glob '!.venv/**' \
  --glob '!docs/RFC-vectl-decide-advisor-refresh.md' \
  --glob '!src/vectl/decide.py' --glob '!src/vectl/decision_state.py' --glob '!src/vectl/model_outputs.py' \
  --glob '!tests/test_decide.py' --glob '!tests/test_decide_state_isolation.py' --glob '!tests/test_mcp.py' \
  'vectl orch|\borch_app\b|cli_orchestration|from vectl\.orchestration|import vectl\.orchestration|from vectl\.orch_app|IsolationMode|\bisolation\s*:|ORCHESTRATION-PLANE-ISOLATION-SEMANTICS|RFC-orch-drive|RFC-opencode-orchestration-runner|RFC-role-profile-overrides|RuntimeContext|DriverState|\bDRIVER_YAML_SPEC\b|driver\.yaml' \
  src tests docs README.md README_zh.md pyproject.toml && exit 1 || true
```

Allowed exception policy:

- `vectl_decide` preserved files may retain the word “orchestration” only when describing external/caller-owned advisor semantics, not built-in runtime authority.
- Generic words like `driver` in `src/vectl/merge_driver.py` and git merge-driver docs are not orchestration runtime residue.
- Generic linked-worktree plan resolution may remain if required by vectl core/worktree isolation harness; orchestration runtime worktree lifecycle references must be gone.

## dependency map for downstream phases

1. `orch_source_delete` must run before final import/test cleanup; deleting source packages will expose all stale imports.
2. `orch_surface_cleanup` depends on source deletion list and removes CLI command wiring in `cli.py`/`cli_plan_common.py`.
3. `orch_test_delete` can run in parallel with doc deletion, but before final pytest selection/gate.
4. `isolation_schema_cleanup` must run before model/io tests are updated.
5. `decide_preserve_wording` should run after runtime wording deletion so decide docs/tests remain clearly caller-owned.
6. `final_grep_gate` runs last and includes path presence/absence probes plus scoped forbidden-term grep.

## Verification

Commands run in isolated worktree:

```text
git status --short --branch -> ## vectl/step-orch_removal_scope_matrix
find/rg probes -> listed source/docs/tests targets above
preservation path presence -> all required decide paths PRESENT
isolation residue probe -> current hits in models.py/io.py/tests/test_io.py/tests/test_models.py/isolation doc, mapped above
```

No product-code mutation evidence: only `.agent/evidence/orch_removal_scope_matrix.md` is intended to change.

## Programmatic Handoff JSON

```json
{
  "deletion_targets_confirmed": true,
  "preservation_targets_confirmed": true,
  "isolation_remove_map_confirmed": true,
  "final_grep_scope_confirmed": true,
  "behavior_preserved": ["vectl_decide caller-owned advisor contract", "core plan/MCP/CLI surfaces outside orch"],
  "deletion_migration_path": ["delete orchestration packages/docs/tests", "clean CLI wiring", "remove Step.isolation/IsolationMode", "preserve and reword decide", "run final grep/path gates"],
  "validation_plan": ["path presence/absence probes", "scoped forbidden-term rg", "targeted decide tests", "full guard when downstream code changes exist"],
  "rollback_fallback": "Revert downstream deletion commits; this investigation artifact has no product-code changes."
}
```
