# Invar Guard Failure Inventory

Step: `invar_guard_remediation.collect-failure-inventory`

## refs Read Confirmation (MANDATORY)

- `INVAR.md` — read before inventory. Key passage: Core functions require `@pre` + `@post` + doctest; Shell is for file/network/env/time/randomness/subprocess I/O and Shell functions should return `Result[T, E]`.
- `AGENTS.md` — read before inventory. Key passage: `plan.yaml` is managed state and must not be edited directly; evidence is mandatory and plan interactions belong to vectl tooling/orchestrator.
- `tools/vectl/README.md` — NOT READ: file path does not exist in this isolated worktree; the `tools/` directory is absent.
- `CONSTITUTION.md` — NOT READ: no `CONSTITUTION.md` exists in this isolated worktree.

## Guard Command

Command executed from isolated worktree:

```text
uvx invar-tools guard --all
```

Output artifact/reference:

```text
docs/invar_guard_remediation/guard_all_raw.txt
docs/invar_guard_remediation/guard_all_exit_code.txt
```

Observed exit code: `1`.

Authoritative guard JSON summary in the captured output:

```text
status=failed; errors=444; warnings=105; infos=194; total_findings=743
```

Planning-probe drift noted: this run observed `444 errors, 105 warnings, 194 infos`, versus seed face `442 errors, 105 warnings, 193 infos`.

## Finding Counts

| Rule | Severity | Count |
| --- | --- | ---: |
| `shell_result` | error | 421 |
| `file_size` | error | 22 |
| `shell_complexity_debt` | error | 1 |
| `function_size` | warning | 40 |
| `dead_param` | warning | 27 |
| `shell_pure_logic` | warning | 19 |
| `dead_assign` | warning | 9 |
| `file_size_warning` | warning | 9 |
| `dead_export` | warning | 1 |
| `shell_too_complex` | info | 194 |

## File Counts

| File | Total | Rule Counts |
| --- | ---: | --- |
| `src/vectl/cli_orchestration.py` | 76 | `file_size/error:1`, `shell_result/error:35`, `shell_pure_logic/warning:4`, `shell_too_complex/info:32`, `dead_param/warning:3`, `dead_assign/warning:1` |
| `src/vectl/core_plan_mutations.py` | 49 | `file_size/error:1`, `function_size/warning:1`, `shell_result/error:33`, `shell_too_complex/info:13`, `dead_assign/warning:1` |
| `src/vectl/orchestration/run_store.py` | 45 | `file_size/error:1`, `shell_result/error:35`, `shell_pure_logic/warning:1`, `shell_too_complex/info:8` |
| `src/vectl/orchestration/config.py` | 43 | `file_size/error:1`, `function_size/warning:4`, `shell_result/error:26`, `shell_too_complex/info:11`, `dead_assign/warning:1` |
| `src/vectl/cli_plan.py` | 42 | `file_size/error:1`, `function_size/warning:3`, `shell_result/error:11`, `shell_too_complex/info:23`, `dead_param/warning:1`, `dead_assign/warning:3` |
| `src/vectl/core_plan_queries.py` | 40 | `file_size/error:1`, `function_size/warning:1`, `shell_result/error:25`, `shell_too_complex/info:13` |
| `src/vectl/orchestration/runtime.py` | 38 | `file_size/error:1`, `function_size/warning:1`, `shell_result/error:24`, `shell_pure_logic/warning:2`, `shell_too_complex/info:9`, `dead_param/warning:1` |
| `src/vectl/orch_app.py` | 37 | `file_size/error:1`, `function_size/warning:10`, `shell_result/error:13`, `shell_pure_logic/warning:1`, `shell_too_complex/info:6`, `dead_param/warning:5`, `dead_assign/warning:1` |
| `src/vectl/mcp_core_tools.py` | 34 | `file_size/error:1`, `function_size/warning:2`, `shell_result/error:19`, `shell_too_complex/info:11`, `dead_assign/warning:1` |
| `src/vectl/orchestration/projections.py` | 27 | `file_size/error:1`, `function_size/warning:1`, `shell_result/error:18`, `shell_too_complex/info:7` |
| `src/vectl/lifecycle.py` | 21 | `file_size_warning/warning:1`, `shell_result/error:13`, `shell_pure_logic/warning:2`, `shell_too_complex/info:5` |
| `src/vectl/mcp_project_tools.py` | 21 | `file_size_warning/warning:1`, `shell_result/error:15`, `shell_too_complex/info:5` |
| `src/vectl/claims.py` | 20 | `function_size/warning:1`, `shell_result/error:15`, `shell_too_complex/info:3`, `dead_export/warning:1` |
| `src/vectl/cli_render.py` | 18 | `file_size/error:1`, `shell_result/error:11`, `shell_too_complex/info:5`, `dead_assign/warning:1` |
| `src/vectl/mcp_render_tools.py` | 18 | `function_size/warning:1`, `shell_result/error:13`, `shell_too_complex/info:3`, `dead_param/warning:1` |
| `src/vectl/cli_agents_md.py` | 18 | `file_size_warning/warning:1`, `shell_result/error:13`, `shell_too_complex/info:4` |
| `src/vectl/core_duplicate_step_id.py` | 18 | `shell_result/error:11`, `shell_pure_logic/warning:2`, `shell_too_complex/info:5` |
| `src/vectl/orchestration/runners.py` | 16 | `file_size/error:1`, `shell_result/error:7`, `shell_pure_logic/warning:3`, `shell_too_complex/info:5` |
| `src/vectl/orchestration/control_channel.py` | 14 | `file_size/error:1`, `shell_result/error:9`, `shell_pure_logic/warning:1`, `shell_too_complex/info:2`, `dead_param/warning:1` |
| `src/vectl/io.py` | 13 | `shell_result/error:10`, `shell_too_complex/info:3` |
| `src/vectl/merge_driver.py` | 13 | `function_size/warning:1`, `shell_result/error:9`, `shell_too_complex/info:3` |
| `src/vectl/orchestration/recovery_fallback.py` | 13 | `file_size/error:1`, `function_size/warning:2`, `shell_result/error:7`, `shell_too_complex/info:3` |
| `src/vectl/cli.py` | 12 | `file_size_warning/warning:1`, `shell_result/error:9`, `shell_too_complex/info:2` |
| `src/vectl/orchestration/events.py` | 12 | `file_size/error:1`, `shell_result/error:6`, `shell_pure_logic/warning:3`, `shell_too_complex/info:2` |
| `src/vectl/orchestration/driver.py` | 12 | `file_size/error:1`, `function_size/warning:7`, `dead_param/warning:4` |
| `src/vectl/orchestration/inspection_queries.py` | 11 | `file_size/error:1`, `shell_result/error:8`, `shell_too_complex/info:2` |
| `src/vectl/orchestration/prompt_materialization.py` | 11 | `file_size_warning/warning:1`, `shell_result/error:8`, `shell_too_complex/info:2` |
| `src/vectl/plan_path.py` | 8 | `shell_result/error:5`, `shell_too_complex/info:3` |
| `src/vectl/migration.py` | 8 | `shell_result/error:6`, `shell_too_complex/info:2` |
| `src/vectl/orchestration/recovery.py` | 7 | `file_size/error:1`, `function_size/warning:1`, `dead_param/warning:5` |
| `src/vectl/orchestration/continuity_artifacts.py` | 6 | `file_size/error:1`, `dead_param/warning:5` |
| `src/vectl/orchestration/core_adapter.py` | 4 | `file_size_warning/warning:1`, `function_size/warning:1`, `shell_result/error:2` |
| `src/vectl/agents_md.py` | 3 | `shell_result/error:2`, `shell_too_complex/info:1` |
| `src/vectl/orchestration/control.py` | 3 | `file_size_warning/warning:1`, `function_size/warning:1`, `dead_param/warning:1` |
| `src/vectl/decide.py` | 2 | `file_size_warning/warning:1`, `function_size/warning:1` |
| `src/vectl/models.py` | 2 | `file_size/error:1`, `shell_result/error:1` |
| `src/vectl/orchestration/runner_registry.py` | 2 | `shell_result/error:2` |
| `<project>` | 1 | `shell_complexity_debt/error:1` |
| `docs/archive/consumer_boundary_conformance_verify.py` | 1 | `shell_too_complex/info:1` |
| `src/vectl/claim_guidance.py` | 1 | `function_size/warning:1` |
| `src/vectl/orchestration/contracts.py` | 1 | `file_size/error:1` |
| `src/vectl/orchestration/dispatch_policy.py` | 1 | `file_size/error:1` |
| `src/vectl/orchestration/resolver_gateway.py` | 1 | `file_size_warning/warning:1` |

## Domain Slice Map

The step prompt referred to “domain step IDs below,” but no explicit planned implementation step IDs were present in the dispatched text and plan inspection was prohibited. The table therefore uses stable inventory domain keys derived from the named file clusters in the step description and architectural remediation file. Planner should reconcile these keys with canonical phase step IDs before dispatching fixes.

| Planned Step / Domain Key | Files Owned | Rule Counts | Representative Findings |
| --- | --- | --- | --- |
| `invar_guard_remediation.cli_surfaces` | `src/vectl/cli.py`, `src/vectl/cli_agents_md.py`, `src/vectl/cli_orchestration.py`, `src/vectl/cli_plan.py`, `src/vectl/cli_render.py` | `total:166`; `shell_result:79`, `shell_too_complex:66`, `dead_assign:5`, `shell_pure_logic:4`, `dead_param:4`, `file_size:3`, `function_size:3`, `file_size_warning:2` | `src/vectl/cli_orchestration.py:None file_size File has 2517 lines`; `src/vectl/cli_orchestration.py:124 shell_result _step_icon should return Result`; `src/vectl/cli_plan.py:1271 function_size migrate_step_id_cmd has 108 code lines`; `src/vectl/cli_render.py:None file_size File has 1264 lines` |
| `invar_guard_remediation.core_plan_domains` | `src/vectl/core_plan_mutations.py`, `src/vectl/core_plan_queries.py`, `src/vectl/core_duplicate_step_id.py`, `src/vectl/lifecycle.py`, `src/vectl/claims.py` | `total:148`; `shell_result:97`, `shell_too_complex:39`, `shell_pure_logic:4`, `function_size:3`, `file_size:2`, `file_size_warning:1`, `dead_export:1`, `dead_assign:1` | `src/vectl/core_plan_mutations.py:None file_size File has 1361 lines`; `src/vectl/core_plan_mutations.py:31 shell_result claim_step should return Result`; `src/vectl/core_plan_queries.py:30 function_size validate_plan has 112 code lines`; `src/vectl/claims.py` includes `dead_export:1` |
| `invar_guard_remediation.mcp_surfaces` | `src/vectl/mcp_core_tools.py`, `src/vectl/mcp_project_tools.py`, `src/vectl/mcp_render_tools.py` | `total:73`; `shell_result:47`, `shell_too_complex:19`, `function_size:3`, `file_size:1`, `file_size_warning:1`, `dead_param:1`, `dead_assign:1` | `src/vectl/mcp_core_tools.py:None file_size File has 1338 lines`; `src/vectl/mcp_core_tools.py:485 function_size vectl_claim has 177 code lines`; `src/vectl/mcp_core_tools.py:140 shell_result _plan_path should return Result`; `src/vectl/mcp_project_tools.py` has 15 `shell_result` errors |
| `invar_guard_remediation.orch_app_split` | `src/vectl/orch_app.py` | `total:37`; `shell_result:13`, `function_size:10`, `shell_too_complex:6`, `dead_param:5`, `file_size:1`, `shell_pure_logic:1`, `dead_assign:1` | `src/vectl/orch_app.py:None file_size File has 6099 lines`; `src/vectl/orch_app.py:1199 function_size _start_runtime_execution has 107 code lines`; `src/vectl/orch_app.py:1730 function_size _collect_and_route_terminal has 217 code lines` |
| `invar_guard_remediation.run_store_split` | `src/vectl/orchestration/run_store.py` | `total:45`; `shell_result:35`, `shell_too_complex:8`, `file_size:1`, `shell_pure_logic:1` | `src/vectl/orchestration/run_store.py:None file_size File has 2487 lines`; `src/vectl/orchestration/run_store.py:193 shell_result _encode_crockford_32 should return Result`; `src/vectl/orchestration/run_store.py:209 shell_result generate_run_id should return Result` |
| `invar_guard_remediation.orchestration_runtime_driver_config` | `src/vectl/orchestration/config.py`, `src/vectl/orchestration/runtime.py`, `src/vectl/orchestration/driver.py`, `src/vectl/orchestration/runners.py`, `src/vectl/orchestration/control_channel.py`, `src/vectl/orchestration/events.py`, `src/vectl/orchestration/projections.py`, `src/vectl/orchestration/recovery.py`, `src/vectl/orchestration/recovery_fallback.py`, `src/vectl/orchestration/inspection_queries.py`, `src/vectl/orchestration/prompt_materialization.py`, `src/vectl/orchestration/continuity_artifacts.py`, `src/vectl/orchestration/contracts.py`, `src/vectl/orchestration/control.py`, `src/vectl/orchestration/core_adapter.py`, `src/vectl/orchestration/dispatch_policy.py`, `src/vectl/orchestration/resolver_gateway.py`, `src/vectl/orchestration/runner_registry.py` | `total:222`; `shell_result:117`, `shell_too_complex:43`, `function_size:18`, `dead_param:17`, `file_size:13`, `shell_pure_logic:9`, `file_size_warning:4`, `dead_assign:1` | `src/vectl/orchestration/config.py:None file_size File has 2304 lines`; `src/vectl/orchestration/runtime.py:79 shell_result worktree_create should return Result`; `src/vectl/orchestration/driver.py:1099 function_size DriveDriver.run_drive_loop has 213 code lines`; `src/vectl/orchestration/projections.py` has 18 `shell_result` errors |
| `invar_guard_remediation.support_shell_io_and_migration` | `src/vectl/agents_md.py`, `src/vectl/claim_guidance.py`, `src/vectl/decide.py`, `src/vectl/io.py`, `src/vectl/merge_driver.py`, `src/vectl/migration.py`, `src/vectl/models.py`, `src/vectl/plan_path.py` | `total:50`; `shell_result:33`, `shell_too_complex:12`, `function_size:3`, `file_size:1`, `file_size_warning:1` | `src/vectl/plan_path.py:44 shell_result _normalize_git_path should return Result`; `src/vectl/io.py` has 10 `shell_result` errors; `src/vectl/merge_driver.py` has 9 `shell_result` errors; `src/vectl/models.py` has `file_size:1` and `shell_result:1` |
| `invar_guard_remediation.residual_unknown` | `<project>`, `docs/archive/consumer_boundary_conformance_verify.py` | `total:2`; `shell_complexity_debt:1`, `shell_too_complex:1` | `<project>: shell_complexity_debt/error`; `docs/archive/consumer_boundary_conformance_verify.py:230 shell_too_complex main has 9 branches` |

## Core Contract Scan

- `**/core/**` contract/doctest gaps: none found.
- Method: glob scan found no files under a `**/core/**` directory, and the guard output contains no `missing_contract`, `missing_pre`, `missing_post`, `missing_doctest`, or `param_mismatch` rules. Files named `core_*.py` are being classified by the guard mainly as shell/file-size/function-size problems, not missing core contracts.

## Residual/Unknown Bucket

- Residual/unknown findings are not zero: `2` findings remain outside the source-domain buckets above.
- Exact residuals:
  - `<project>` — `shell_complexity_debt/error:1`.
  - `docs/archive/consumer_boundary_conformance_verify.py:230` — `shell_too_complex/info: Shell function 'main' has 9 branches (max: 3)`.
- Planner follow-up: reconcile these residual findings explicitly. If the planned implementation phase excludes docs/archive and project-level guard debt, create/assign a residual cleanup step or mark them governed exceptions.

## Replan Trigger Recommendation

- Trigger replan if canonical phase implementation steps do not include the residual/unknown bucket above.
- Trigger replan if any planned implementation step is expected to touch outside its owned file cluster. The observed failure face is broad: `orchestration_runtime_driver_config` alone covers 18 files and 222 findings, so it may need subdivision if the canonical phase step list has narrower ownership.
- Trigger replan if remediation intends to move behavior between CLI/Core/MCP/Orchestration clusters, because that would exceed a single file-cluster fix and requires dependency/order planning.

## Product Code Changes

- none.

## Test / Verification Semantic Reporting

- `step_intent`: Run authoritative `uvx invar-tools guard --all` and reduce the broad failure face into a stable remediation inventory without modifying product code.
- `expected_result`: Guard fails with known broad invar debt; inventory groups findings by rule, severity, file, and planned domain/file cluster, and identifies core contract gaps/residuals.
- `observed_result`: Guard exited `1` and emitted JSON summary `444 errors`, `105 warnings`, `194 infos`, `743 total findings`; findings are grouped above with raw output stored at `docs/invar_guard_remediation/guard_all_raw.txt`.
- `failure_alignment`: The failure is aligned with the step’s expected-red inventory purpose. Counts drift slightly upward from seed probe (`+2 errors`, `+1 info`), but same dominant rules/files remain.
- `verdict`: SUCCESS for inventory collection; guard itself is expected-red/failing and should not be interpreted as remediation pass.
- `blockers`: none for inventory. One required ref, `tools/vectl/README.md`, could not be read because it is absent from this worktree.
- `product_implementation_files_modified`: false.

## Checklist Receipt

- `[x] I have called read on each required file above` — `read` was called for `INVAR.md`, `AGENTS.md`, and `tools/vectl/README.md`; the README call returned file-not-found.
- `[x] I have identified key passages relevant to this step` — key passages are recorded in refs Read Confirmation.
- `[x] I understand the spec expectations from these refs` — applied by avoiding product code changes, avoiding vectl plan mutation, using the guard command, and reporting Result/Core contract expectations.
