# Post-Salvage Residual Classification Register

Step: `invar_guard_remediation.fix-full-guard-escape-budget-blockers-batched`

## refs Read Confirmation

- `INVAR.md` — Read. Key passage: Core code must have `@pre`, `@post`, and a doctest before implementation; Shell applies to file/network/env/time/random/subprocess I/O; escape hatches must be rare and justified. This step is evidence/classification only and therefore does not add Core functions or new hatches.
- `AGENTS.md` — Read. Key passage: `plan.yaml` is vectl-owned and must not be directly edited; evidence is mandatory; planner/final-gate evidence must distinguish phase-local proof from repo-wide readiness. This step avoids plan/vectl mutation and produces only this evidence artifact.

## Provenance

- main_salvage_commit: `1618486`
- branch_provenance_commit: `49aa644b897c3ff08ba39d74a5da036e1fe6bfe2` (recorded as provenance only)
- current_worktree_head: `6f8997ff3c6d8279be7e7e868ba9a9821740acfc`
- current_branch: `vectl/step-invar_guard_remediation.fix-full-guard-escape-budget-blockers-batched`
- salvage ancestry check: `git merge-base --is-ancestor 1618486 HEAD` exited successfully, so the current worktree contains the salvaged main commit.
- targeted_regression_baseline: `190 passed in 13.71s` (post-salvage baseline from assigned step; not re-run in this classification step)

## BEFORE Guard Excerpts

Command run from isolated worktree:

```text
uvx invar-tools guard --all
```

Current summary:

```text
status: failed
summary.files_checked: 100
summary.errors: 2
summary.warnings: 48
summary.infos: 7
escape_hatches.count: 18
escape_hatches.by_rule:
  shell_complexity: 1
  entry_point_too_thick: 2
  file_size: 9
  dead_param: 6
escape_hatches.gating.status: exceeded
escape_hatches.gating.budget.used: 40
escape_hatches.gating.budget.limit: 15
project errors:
  - Project rule budget file_size: 9/5
  - Escape hatch weighted budget: 40/15
doctest.passed: true
crosshair.status: skipped (prior failures)
property_tests.status: skipped (prior failures)
```

Current residual detail rows:

```text
docs/archive/consumer_boundary_conformance_verify.py:231 shell_complexity — archived conformance verifier is a linear shell script
src/vectl/plan_path.py:182 entry_point_too_thick — git-common-dir fallback must remain a single public resolver
src/vectl/mcp_core_tools.py:1 file_size — MCP core tool names and schemas are public compatibility surfaces
src/vectl/orch_app.py:1 file_size — documented composition root and public CLI routing facade
src/vectl/orch_app.py:4579 dead_param — force retained for public CLI/app prune compatibility
src/vectl/core_plan_mutations.py:1 file_size — lifecycle/add/edit/remove/clipboard/agents-md/recovery APIs co-located for existing imports
src/vectl/cli_orchestration.py:6 file_size — public Typer registration surface
src/vectl/cli_orchestration.py:1673 dead_param — drive_id retained as public Typer positional compatibility placeholder
src/vectl/cli_plan.py:1 file_size — legacy Typer plan-management surface
src/vectl/migration.py:64 entry_point_too_thick — legacy split-state path fallback must remain public
src/vectl/orchestration/config.py:1 file_size — public config model/loader compatibility surface
src/vectl/orchestration/control.py:190 dead_param — roster retained for Control interface compatibility and future scheduling
src/vectl/orchestration/runtime.py:1 file_size — worktree lifecycle/runner handle/child-run/reconcile runtime compatibility surface
src/vectl/orchestration/runtime.py:202 dead_param — step_id retained as keyword-compatible lifecycle protocol parameter
src/vectl/orchestration/continuity_protocols.py:176 dead_param — Quarantine contract preserves public parameter names
src/vectl/orchestration/continuity_protocols.py:199 dead_param — Quarantine contract preserves public parameter names
src/vectl/orchestration/driver.py:1 file_size — active-drive state machine/barriers/resume/recovery surface
src/vectl/orchestration/run_store.py:1 file_size — run and drive JSONL persistence import compatibility surface
```

No fresh-current delta was observed from the assigned post-salvage residual list for file-size hatches: the guard still reports `file_size 9/5` and weighted `40/15`.

## Classification Register

| file_path | current_hatch_kind | current_counter_contribution | classification | assigned_residual_step | compatibility_smoke_required | runtime_or_surface_smoke_required | dead_param_or_entrypoint_owner | decision_basis |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `src/vectl/mcp_core_tools.py` | `file_size` | file_size +1; weighted +3 | `public_schema_facade` | `invar_guard_remediation.split-mcp-and-cli-public-surfaces` | yes: MCP tool names/schema registration/import compatibility | yes: MCP tool discovery or schema smoke | n/a | Guard detail row says MCP core tool names/schemas are public compatibility surfaces; file header documents 15 public MCP tools and structured return contracts. |
| `src/vectl/orch_app.py` | `file_size` | file_size +1; weighted +3 | `composition_root_facade` | `invar_guard_remediation.split-orch-app-and-core-plan-compat-surfaces` | yes: public `OrchestrationApp`, `AppConfig`, `OrchestrationResult`, `build_orchestration_app` imports | yes: CLI routing/app factory smoke | n/a | File header identifies this module as orchestration application composition root wiring control/roster/runtime/resolver/core_adapter and exposing typed API for `vectl orch`. |
| `src/vectl/core_plan_mutations.py` | `file_size` | file_size +1; weighted +3 | `compatibility_api_facade` | `invar_guard_remediation.split-orch-app-and-core-plan-compat-surfaces` | yes: existing `vectl.core`/CLI/MCP mutation imports | yes: representative lifecycle/add/edit/remove/clipboard/recovery surface smoke | n/a | File header and guard reason state lifecycle, add/edit/remove, clipboard, agents-md, and recovery APIs are co-located for existing CLI/MCP imports. |
| `src/vectl/cli_orchestration.py` | `file_size` | file_size +1; weighted +3 | `cli_registration_facade` | `invar_guard_remediation.split-mcp-and-cli-public-surfaces` | yes: Typer command registration/backward command names | yes: `vectl orch --help` and representative subcommand help smoke | owns its local `dead_param` at line 1673 | Header/guard reason says orchestration CLI preserves one public Typer registration surface; imports Typer/Rich and delegates to orchestration app boundaries. |
| `src/vectl/cli_plan.py` | `file_size` | file_size +1; weighted +3 | `cli_registration_facade` | `invar_guard_remediation.split-mcp-and-cli-public-surfaces` | yes: legacy Typer plan command registration | yes: `vectl --help` / plan-management command help smoke | n/a | Header/guard reason says legacy Typer plan-management surface must preserve public command registration. |
| `src/vectl/orchestration/config.py` | `file_size` | file_size +1; weighted +3 | `config_runtime_compatibility` | `invar_guard_remediation.split-orchestration-config-runtime-persistence-surfaces` | yes: config model/loader/freeze import compatibility | yes: load/freeze/validate config smoke | n/a | File header lists public `OrchestrationConfig`, `ResolverToolAllowlist`, `freeze_config`, `load_orchestration_config`; guard reason says splitting would affect import/snapshot semantics. |
| `src/vectl/orchestration/runtime.py` | `file_size` | file_size +1; weighted +3 | `config_runtime_compatibility` | `invar_guard_remediation.split-orchestration-config-runtime-persistence-surfaces` | yes: runtime public API/worktree lifecycle imports | yes: worktree lifecycle/reconcile glue smoke with safe temporary paths | owns its local `dead_param` at line 202 | Header ties runtime to worktree lifecycle and execution/reconcile authority docs; guard reason says runtime keeps worktree lifecycle, runner handle tracking, child-run bookkeeping, and reconcile glue in one public compatibility surface. |
| `src/vectl/orchestration/driver.py` | `file_size` | file_size +1; weighted +3 | `state_machine_runtime` | `invar_guard_remediation.split-orchestration-driver-state-machine-surface` | yes: drive result types/`DriveDriver` imports | yes: drive loop/resume/recovery state-machine smoke | owns any driver `entry_point_too_thick`, `dead_param`, or state-machine pressure | File header says it provides drive-level orchestration loop, barrier management, `DriveDriver`, status transition validation, admission and parallelism validation. |
| `src/vectl/orchestration/run_store.py` | `file_size` | file_size +1; weighted +3 | `persistence_compatibility` | `invar_guard_remediation.split-orchestration-config-runtime-persistence-surfaces` | yes: `RunRecord`, `CasesIndex`, `RunRegistry`, `latest_run`, `DriveStore` imports | yes: JSONL run/drive persistence round-trip smoke | n/a | File header says run registry/cases index/latest lookup interfaces and guard reason says run and drive JSONL persistence remain co-located to preserve public run-store import compatibility. |

## Weighted Contributor Ownership Register

All current escape-hatch contributors from the fresh guard output are assigned below; none are dismissed as pre-existing, out-of-scope, or deferred without owner.

| contributor | rule | owner_step | owner_basis |
| --- | --- | --- | --- |
| `docs/archive/consumer_boundary_conformance_verify.py:231` | `shell_complexity` | `invar_guard_remediation.split-orch-app-and-core-plan-compat-surfaces` | Archived verifier explicitly checks orch_app recovery/consumer boundary conformance; downstream owner must either keep archival justification or split/remove in a compatibility-safe way. |
| `src/vectl/plan_path.py:182` | `entry_point_too_thick` | `invar_guard_remediation.split-mcp-and-cli-public-surfaces` | Claims/plan path resolver is a shared public entrypoint used by CLI/MCP surfaces; smoke should include plan/claims path resolution compatibility. |
| `src/vectl/mcp_core_tools.py:1` | `file_size` | `invar_guard_remediation.split-mcp-and-cli-public-surfaces` | MCP public schema facade. |
| `src/vectl/orch_app.py:1` | `file_size` | `invar_guard_remediation.split-orch-app-and-core-plan-compat-surfaces` | Orchestration composition root facade. |
| `src/vectl/orch_app.py:4579` | `dead_param` | `invar_guard_remediation.split-orch-app-and-core-plan-compat-surfaces` | `force` is public CLI/app prune compatibility and belongs with orch_app split. |
| `src/vectl/core_plan_mutations.py:1` | `file_size` | `invar_guard_remediation.split-orch-app-and-core-plan-compat-surfaces` | Core mutation compatibility API facade. |
| `src/vectl/cli_orchestration.py:6` | `file_size` | `invar_guard_remediation.split-mcp-and-cli-public-surfaces` | Public orchestration Typer registration surface. |
| `src/vectl/cli_orchestration.py:1673` | `dead_param` | `invar_guard_remediation.split-mcp-and-cli-public-surfaces` | `drive_id` is a public Typer positional compatibility placeholder. |
| `src/vectl/cli_plan.py:1` | `file_size` | `invar_guard_remediation.split-mcp-and-cli-public-surfaces` | Legacy Typer plan-management surface. |
| `src/vectl/migration.py:64` | `entry_point_too_thick` | `invar_guard_remediation.split-orch-app-and-core-plan-compat-surfaces` | Legacy split-state migration helper is plan-management compatibility adjacent to core mutation/lifecycle APIs. |
| `src/vectl/orchestration/config.py:1` | `file_size` | `invar_guard_remediation.split-orchestration-config-runtime-persistence-surfaces` | Config model/loader compatibility surface. |
| `src/vectl/orchestration/control.py:190` | `dead_param` | `invar_guard_remediation.split-orchestration-driver-state-machine-surface` | Control decision evaluation is part of drive state-machine scheduling pressure; roster-compatible parameter affects driver/control integration. |
| `src/vectl/orchestration/runtime.py:1` | `file_size` | `invar_guard_remediation.split-orchestration-config-runtime-persistence-surfaces` | Runtime/worktree lifecycle/reconcile compatibility surface. |
| `src/vectl/orchestration/runtime.py:202` | `dead_param` | `invar_guard_remediation.split-orchestration-config-runtime-persistence-surfaces` | Runtime lifecycle protocol compatibility parameter. |
| `src/vectl/orchestration/continuity_protocols.py:176` | `dead_param` | `invar_guard_remediation.split-orchestration-config-runtime-persistence-surfaces` | Recovery/continuity protocol compatibility should be handled with runtime/persistence compatibility surfaces. |
| `src/vectl/orchestration/continuity_protocols.py:199` | `dead_param` | `invar_guard_remediation.split-orchestration-config-runtime-persistence-surfaces` | Recovery/continuity protocol compatibility should be handled with runtime/persistence compatibility surfaces. |
| `src/vectl/orchestration/driver.py:1` | `file_size` | `invar_guard_remediation.split-orchestration-driver-state-machine-surface` | Active-drive state machine/barriers/resume/recovery surface. |
| `src/vectl/orchestration/run_store.py:1` | `file_size` | `invar_guard_remediation.split-orchestration-config-runtime-persistence-surfaces` | Run/drive JSONL persistence compatibility surface. |

## Ownership Closure

- [x] every current residual has a focused owner
- [x] no residual is dismissed as pre-existing/out-of-scope
- [x] downstream retest/gate remains dependent on residual owners
- [x] no product-code edits, budget lowering, suppressions, ignored files, or escape-hatch policy widening were performed

Downstream gate dependency statement: no downstream retest/final guard gate can proceed as authoritative until all focused residual implementation steps listed above complete and the cold retest step runs against the merged result.
