# Architectural demolition remediation plan

## Status

- Status: proposed remediation plan
- Scope: repository-wide architectural simplification and de-duplication
- Primary goal: remove duplicated logic, false abstraction seams, dead public surfaces, and contract theater without collapsing valid authority boundaries

## Why this document exists

Three independent reviews (`duplicate-abstraction-auditor-tacit`, `architectural-demolitionist-tacit`, and direct repository inspection) converged on the same core problem: the repo pays high cognitive cost for duplicated logic and speculative abstraction while several real authority surfaces remain valid and should be preserved.

This document is the authoritative remediation plan for fixing **all discovered issues**, not just the overlaps between reviewers.

## Remediation objectives

1. Ensure each concept has one authoritative implementation.
2. Remove pure pass-through wrappers and single-buyer protocols that do not buy real substitution value.
3. Delete dead exported surfaces.
4. Preserve valid boundaries:
   - `core` vs `cli`/`mcp_server`
   - `runtime` vs `runners`
   - config input models vs runtime contract models
5. Re-cut large files only **after** abstraction removal, never by adding new contract-only files.

## Non-goals / preserved boundaries

The remediation must **not** do the following:

1. Merge `src/vectl/core.py` and `src/vectl/cli.py` into one module.
2. Merge `src/vectl/orchestration/runtime.py` and `src/vectl/orchestration/runners.py`.
3. Flatten all `DuplicateStepId*` reporting into untyped dictionaries.
4. Introduce more protocol-only or contract-only files as an organizational tactic.
5. Change `plan.yaml` directly; any plan-state mutation remains vectl-owned.

## Issue register

Every discovered issue must be resolved by this plan.

| ID | Severity | Issue | Exact locations | Required disposition |
|---|---|---|---|---|
| DEM-001 | high | `agents-md` logic duplicated across shell and authority surfaces | `src/vectl/cli.py:2401-2485`, `src/vectl/core.py:2582-2677` | Extract shared implementation into `src/vectl/agents_md.py`; `agents_md.py` becomes the canonical owner of `AgentsTarget`, helper constants, detect, and upsert logic; `core.py` may re-export temporarily only for compatibility |
| DEM-002 | high | `_get_next_steps_with_phase` duplicated | `src/vectl/cli.py:201-242`, `src/vectl/mcp_server.py:284-327` | Extract one shared helper |
| DEM-003 | medium | duplicate step-id diagnostics formatting duplicated | `src/vectl/cli.py:155-199`, `src/vectl/mcp_server.py:245-276` | Extract one shared structure/formatter boundary |
| DEM-004 | medium | claim/affinity/conflict metadata modeled in parallel across lifecycle and MCP | `src/vectl/lifecycle.py:29-75`, `src/vectl/mcp_server.py:486-551` | Keep shared claim metadata lifecycle-owned in `src/vectl/lifecycle.py`; keep MCP transport envelope thin and consume lifecycle-owned metadata instead of redefining domain objects |
| DEM-005 | high | `Control` defined twice | `src/vectl/orchestration/interfaces.py:84-144`, `src/vectl/orchestration/control.py:111-217` | Keep one canonical contract only |
| DEM-006 | high | `Resolver` defined twice | `src/vectl/orchestration/interfaces.py:294-334`, `src/vectl/orchestration/resolver.py:45-65` | Keep one canonical contract only |
| DEM-007 | high | `LifecycleMutationPort` duplicates `CoreAdapter` authority surface | `src/vectl/orchestration/interfaces.py:337-366`, `src/vectl/orchestration/core_adapter.py:24-77` | Remove duplicate surface; retain one mutation authority seam |
| DEM-008 | medium | `RunRegistryInspectionView` is pure delegation | `src/vectl/orchestration/run_store.py:1198-1242` | Delete and query `RunRegistry` directly |
| DEM-009 | medium | `CoreStepDataAdapter` is pure delegation and forces awkward reverse dependency | `src/vectl/orchestration/dispatch_policy.py:751-772`, `src/vectl/orchestration/core_adapter.py:242-277` (`load_step_data_for_dispatch`) | Delete wrapper, relocate `StepData` to a leaf data module, and keep step-data loading on the surviving authority seam |
| DEM-010 | medium | `ToolFamilyRegistry` is OO shell around constants | `src/vectl/orchestration/tool_registry.py:123-185` | Replace class with module-level helpers/constants; update `src/vectl/orchestration/resolver_gateway.py:342-398` (`_authorize_tool_calls`) to call those helpers directly |
| DEM-011 | high | inspection read path over-factored with DTO + protocol + impl + wrapper layers | `src/vectl/orchestration/inspection_queries.py`, `src/vectl/orchestration/run_store.py` | Collapse to DTOs plus direct `RunRegistry`-backed query functions |
| DEM-012 | medium | dead DTOs exported with no production buyer | `src/vectl/orchestration/control_channel.py:717-795` (`InspectView`, `CaseView`) | Delete |
| DEM-013 | medium | dead speculative persistence seams | `src/vectl/orchestration/continuity_artifacts.py:359-489` (`ArtifactReader`, `ArtifactWriter`) | Delete |
| DEM-014 | medium | `ReviewGate` exists only as protocol surface; no real implementation | `src/vectl/orchestration/review_gate.py:24-116`, `src/vectl/orchestration/driver.py:833-851` | Resolve entirely in Wave 5: add `DefaultReviewGate` implementing `evaluate(step_id, execution_result, artifact_refs=()) -> ReviewGateResult`, wire it into the driver, then remove the protocol-only seam |
| DEM-015 | medium | `DriveDriver` protocol has no substitution value vs concrete class | `src/vectl/orchestration/driver.py:207-345`, `src/vectl/orchestration/driver.py:803+` | Remove protocol, rename concrete class to canonical class name, and retain a temporary deprecated alias only for migration |
| DEM-016 | medium | role/prompt registries exposed as public protocols despite only one in-repo path | `src/vectl/orchestration/contracts.py:321-346`, `src/vectl/orchestration/dispatch_policy.py:114-405` | Remove public protocol indirection; use concrete config-backed types |
| DEM-017 | medium | `DuplicateStepId*` nested leaf DTO family is over-factored | `src/vectl/models.py:286-497` | Preserve outer reports, collapse tiny nested leaf wrappers |
| DEM-018 | medium | giant files still carry too much behavior after years of additive splitting | `src/vectl/cli.py` (4717 lines), `src/vectl/core.py` (2761 lines), `src/vectl/mcp_server.py` (2177 lines), `src/vectl/orch_app.py` (4803 lines), `src/vectl/orchestration/run_store.py` (2418 lines), `src/vectl/orchestration/driver.py` (2245 lines) | Re-cut by behavior only after demolition; hit the Wave 7 size targets and module split map |
| DEM-019 | low | config/contracts boundary needs explicit freeze to prevent future parallel models | `src/vectl/orchestration/config.py`, `src/vectl/orchestration/contracts.py` | Clarify ownership in docs and module docstrings |

## Target end-state

After remediation:

1. Each user-visible or orchestration-visible concept is defined once.
2. The orchestration package contains fewer public seams and each surviving seam has a real buyer.
3. Read paths query stores directly instead of moving through protocol/impl/view stacks.
4. Review and drive control use real implementations, not placeholder contract surfaces.
5. Large files are reduced only after duplication and wrapper layers are removed.

## Canonical design decisions

### 1. Shell vs authority remains split

- `core.py` remains the authority layer for plan semantics.
- `cli.py` and `mcp_server.py` remain presentation/transport layers.
- Shared helper logic moves to small behavior-owning modules, not back into shells.

### 2. Runtime vs runner remains split

- `runtime.py` owns execution mechanics/orchestration lifecycle.
- `runners.py` owns runner implementations.
- No collapse is allowed here unless a separate review proves there is no real runner substitution value.

### 3. Concrete implementation beats speculative protocol

For repository-internal surfaces with only one production path, use the concrete class and test with fakes/stubs as needed. Do not publish a protocol merely because tests can mock it.

**Real buyer definition:** a consumer is a real buyer only if it (a) invokes the surface in production code paths and (b) would require meaningful refactoring if the surface were removed. Mock-only tests, protocol existence tests, and conformance tests without substantive production behavior do not qualify.

### 4. Query DTOs may survive; query protocol stacks do not

Output DTOs such as `RunsInspectView` and `DriveInspectView` may remain if they represent stable user-facing result shapes. Query protocols and pure wrapper views that only forward to `RunRegistry` must be removed.

### 5. Canonical ownership decisions fixed before execution

- `src/vectl/agents_md.py` is the canonical owner of `AgentsTarget`, agents-md snippet constants, `detect_agents_target()`, and `upsert_agents_md()`.
- `src/vectl/plan_helpers.py` is the canonical owner of `get_next_steps_with_phase()` and any other shared plan-query helpers consumed by both CLI and MCP shells.
- `src/vectl/duplicate_step_id_format.py` is the canonical owner of duplicate-step-id diagnostic and recommendation formatting helpers consumed by both CLI and MCP shells.
- `src/vectl/lifecycle.py` remains the canonical owner of shared claim/affinity/conflict domain metadata. CLI and MCP may wrap or render that data, but they must not redefine it.
- `src/vectl/orchestration/step_data.py` will be created as a leaf data module and becomes the canonical owner of the `StepData` dataclass.
- No implementation PR may change these ownership decisions without first updating this document and rerunning document/spec review.

## Execution plan

The work must be completed in the following order. Later waves may not begin until earlier wave acceptance criteria pass. Each wave must be claimed by exactly one agent at a time and mapped to explicit vectl step(s) before implementation begins. Concurrent wave execution is prohibited because the waves touch shared mutable files and shared authority surfaces.

### Wave 0 — characterization and safety rails

#### Scope

Add or tighten characterization tests for all behaviors that will be touched by demolition.

#### Required coverage

1. `agents-md` detect/upsert behavior.
2. `_get_next_steps_with_phase` ordering and filtering behavior.
3. duplicate step-id diagnostics and recommendation rendering.
4. `vectl_claim` conflict/affinity shaping across CLI and MCP surfaces.
5. inspection query outputs used by `orch_app`.
6. drive/review behavior around post-execution decision paths.

#### Acceptance criteria

1. Existing behavior is pinned before demolition begins.
2. No demolition PR may weaken or delete characterization tests without replacing them with equivalent or stronger coverage.

#### Verification

- `uv run pytest tests/ -x --tb=short` must pass before Wave 1 begins
- focused tests for touched shell/orchestration surfaces must be named in the PR evidence and must pass in addition to the full suite

### Wave 1 — deduplicate direct copies

#### Scope

Resolve DEM-001 through DEM-004.

#### Required changes

1. Add `src/vectl/agents_md.py` as the canonical home of:
   - `AgentsTarget`
   - `AGENTS_MD_*` snippet constants
   - `detect_agents_target()`
   - `upsert_agents_md()`
2. Convert `cli.py` to a thin command wrapper over shared agents-md logic.
3. Update `core.py` to import from `src/vectl/agents_md.py`; if external compatibility requires it, keep only a temporary deprecated re-export from `core.py` for one wave cycle.
4. Update all shell call sites, including MCP/init flows, to use the shared agents-md module rather than carrying local helper copies.
5. Extract `get_next_steps_with_phase()` into `src/vectl/plan_helpers.py` as its canonical owner and update CLI and MCP to import from there.
6. Extract shared duplicate-step-id diagnostic/recommendation formatting helpers into `src/vectl/duplicate_step_id_format.py` as their canonical owner and update CLI and MCP to import from there.
7. Keep claim conflict/affinity metadata lifecycle-owned in `src/vectl/lifecycle.py` and make `mcp_server.py` wrap that metadata instead of re-declaring the same domain data.

#### Acceptance criteria

1. `AgentsTarget` exists in one production location only: `src/vectl/agents_md.py`.
2. `detect_agents_target()` and `upsert_agents_md()` each have one production implementation only.
3. `_get_next_steps_with_phase` has one production implementation only.
4. MCP claim result models no longer duplicate lifecycle-owned domain metadata.
5. The import graph remains acyclic after the module extraction and any temporary re-export is explicitly documented as deprecated.

#### Verification

- `uv run pytest tests/ -x --tb=short` passes
- `uv run vectl --help` succeeds
- import graph review in the PR confirms no new cycle was introduced

### Wave 2 — delete dead exported surfaces

#### Scope

Resolve DEM-012, DEM-013, and DEM-015.

#### Required changes

1. Delete `InspectView` and `CaseView` from `control_channel.py`.
2. Delete `ArtifactReader` and `ArtifactWriter` from `continuity_artifacts.py`.
3. Delete `DriveDriver` protocol from `driver.py`.
4. Rename `ConcreteDriveDriver` to `DriveDriver` and migrate imports.
5. Retain `ConcreteDriveDriver = DriveDriver` as a deprecated alias for one wave cycle only, with removal scheduled no later than the end of Wave 5.

#### Acceptance criteria

1. Deleted symbols do not remain in `__all__` exports.
2. No production code imports any deleted symbol.
3. `DriveDriver` now refers to the real driver class rather than a protocol shell.
4. The deprecated `ConcreteDriveDriver` alias is documented as temporary, removed no later than the end of Wave 5, and is not used by new production code.

#### Verification

- `uv run pytest tests/ -x --tb=short` passes
- static import and grep checks show no production references to deleted symbols
- drive-path tests continue to pass after rename and import migration

### Wave 3 — flatten wrappers and inspection query stack

#### Scope

Resolve DEM-008 through DEM-011.

#### Required changes

1. Delete `RunInspectionBoundary` and `RunRegistryInspectionView`.
2. Delete `RunsQuery`, `CasesQuery`, `RunsQueryImpl`, and `CasesQueryImpl`.
3. Keep stable read DTOs in `src/vectl/orchestration/inspection_queries.py` as the canonical read-DTO home:
   - `RunsInspectView`
   - `InspectQuery`
   - `DriveInspectView`
   - `DriveInspectQuery`
4. Rewrite `query_runs()` and `query_cases()` to operate directly on `RunRegistry`.
5. Delete `CoreStepDataAdapter`.
6. Delete the `StepDataAdapter` protocol from `dispatch_policy.py`; after `CoreStepDataAdapter` is removed, `DispatchCoordinator` must not retain adapter indirection with no real implementor.
7. Extend `CoreAdapter` to expose `load_step_data_for_dispatch(step_id)` as part of the surviving authority seam, then replace `DispatchCoordinator.step_adapter` with `DispatchCoordinator.core_adapter: CoreAdapter` and call `core_adapter.load_step_data_for_dispatch(step_id)` directly.
8. Create `src/vectl/orchestration/step_data.py` as a leaf data module containing the `StepData` dataclass, then update `dispatch_policy.py` and `core_adapter.py` to import from it.
9. Replace `ToolFamilyRegistry` with module-level functions over canonical metadata.
10. Update all currently known `_FakeCoreAdapter` test doubles to implement the new `load_step_data_for_dispatch(step_id)` method so the surviving `CoreAdapter` seam remains testable without drift. The known locations at plan time are:
    - `tests/orchestration/test_opencode_drive_acceptance_matrix.py`
    - `tests/orchestration/unit/test_drive_control_consumption.py`
    - `tests/orchestration/unit/test_drive_recovery_semantics.py`
    - `tests/orchestration/unit/test_planner_loop_integration.py`
    - `tests/orchestration/unit/test_resolver_loop_integration.py`
    - `tests/orchestration/unit/test_driver_loop_proof.py`
    - `tests/orchestration/unit/test_control_drive.py`
    - `tests/orchestration/unit/test_control.py`
    - `tests/orchestration/integration/test_control_resolver_contract.py`
    - `tests/orchestration/integration/test_control_isolation_flow.py`

#### Acceptance criteria

1. No pure delegation wrapper remains in the inspection read path.
2. `inspection_queries.py` remains the canonical DTO home but no longer defines protocols/impls that merely repackage `RunRegistry` calls.
3. `PlanCoreAdapter` no longer imports `StepData` from `dispatch_policy`; both consumers import it from `src/vectl/orchestration/step_data.py`.
4. `ToolFamilyRegistry` class no longer exists.
5. `StepDataAdapter` no longer exists in `dispatch_policy.py`.
6. `DispatchCoordinator` no longer has a `step_adapter` field and instead uses `core_adapter: CoreAdapter` directly.
7. All `CoreAdapter` test doubles used in targeted suites implement `load_step_data_for_dispatch(step_id)` and continue to satisfy the surviving public seam.

#### Verification

- `uv run pytest tests/ -x --tb=short` passes
- query behavior used by `orch_app` remains unchanged
- resolver gateway authorization tests pass with module helper replacement
- no cycle is introduced by `StepData` relocation

### Wave 4 — unify orchestration authority surfaces

#### Scope

Resolve DEM-005, DEM-006, DEM-007, and DEM-016.

#### Required changes

1. Keep the canonical `Control` contract definition in `src/vectl/orchestration/interfaces.py` and delete the duplicate protocol definition from `src/vectl/orchestration/control.py`.
2. After deleting the duplicate `Control` protocol from `control.py`, keep `PlanAwareControl` in `src/vectl/orchestration/control.py` unchanged; preserve its import path and existing call sites.
3. Keep the canonical `Resolver` contract definition in `src/vectl/orchestration/interfaces.py` and delete the duplicate protocol definition from `src/vectl/orchestration/resolver.py`.
4. After deleting the duplicate `Resolver` protocol from `resolver.py`, keep `BoundResolver` in `src/vectl/orchestration/resolver.py` unchanged; preserve its import path and existing call sites.
5. Remove `LifecycleMutationPort` and use `CoreAdapter` as the single mutation authority seam.
6. Remove public `RoleProfileRegistry` and `PromptRegistry` protocols from `contracts.py`.
7. Treat `ConfigRoleProfileRegistry` and `ConfigPromptRegistry` as the concrete in-repo authority surfaces.
8. Migrate contract-lock tests to the surviving `CoreAdapter` public boundary rather than deleting those tests.
9. Update all remaining type annotations, constructor signatures, and field annotations that still name `RoleProfileRegistry` or `PromptRegistry` so they point to the surviving concrete surfaces.
10. Update tests to validate the concrete surfaces or duck-typed behavior rather than protocol existence.

#### Acceptance criteria

1. `Control` is defined exactly once.
2. `Resolver` is defined exactly once.
3. `LifecycleMutationPort` no longer exists and its contract-lock coverage has been migrated to `CoreAdapter` or an equivalent surviving public boundary.
4. `RoleProfileRegistry` and `PromptRegistry` are no longer public orchestration contracts.
5. `PlanAwareControl` remains importable from `src/vectl/orchestration/control.py` and all existing call sites remain unbroken.
6. `BoundResolver` remains importable from `src/vectl/orchestration/resolver.py` and all existing call sites remain unbroken.
7. No surviving production type annotation still depends on the deleted `RoleProfileRegistry` or `PromptRegistry` protocol names.

#### Verification

- `uv run pytest tests/ -x --tb=short` passes
- contract-lock tests are updated to the surviving authority seam
- dispatch and orchestration app tests pass with concrete registry types

### Wave 5 — give review flow a real implementation

#### Scope

Resolve DEM-014 atomically. This wave intentionally revisits `driver.py` after the Wave 2 `ConcreteDriveDriver -> DriveDriver` rename; all Wave 5 driver work must target the post-rename class and file state.

#### Required changes

1. Add a concrete `DefaultReviewGate` in `review_gate.py`.
2. `DefaultReviewGate` must implement the existing review contract: `evaluate(step_id, execution_result, artifact_refs=()) -> ReviewGateResult`.
3. `DefaultReviewGate.evaluate(...)` must own review normalization, including malformed-output handling; malformed review output must become `needs_fix` or `operator_required`, never silent pass.
4. `DefaultReviewGate` owns only the post-execution-to-`ReviewGateResult` normalization layer. It does not replace or absorb `normalize_review_result()` in `dispatch_policy.py`, which remains the structured-review-result-to-`ResolutionCase` normalizer for the downstream resolution path.
5. Wire `DefaultReviewGate` into `DriveDriver.__init__` as the default value for the `review_gate` parameter, replacing the current `None` default after the Wave 2 rename lands.
6. Introduce an explicit helper in `DriveDriver` named `_handle_post_execution_review(...)` (or rename-equivalent explicitly documented in the PR) and make it the canonical post-execution review hook.
7. In `DriveDriver.run_drive_loop()` or the helper it delegates to for completed child-run handling, route terminal execution facts through `_handle_post_execution_review(...)`: after terminal execution output exists and before authoritative step completion, call `self._review_gate.evaluate(step_id=..., execution_result=..., artifact_refs=...)`.
8. Remove the `ReviewGate` protocol only after `DefaultReviewGate` is wired in and covered by tests, so there is no broken intermediate state.

#### Acceptance criteria

1. Review normalization has one real implementation entry point.
2. There is no broken intermediate state in which the protocol was removed but no concrete implementation exists.
3. The repo no longer advertises review-gate behavior as contract-only.
4. `DriveDriver` contains a real review-gate invocation site between terminal execution output and authoritative completion.
5. `DriveDriver` has a named post-execution review helper (`_handle_post_execution_review(...)` or documented rename-equivalent) so future review wiring does not drift back into inline logic.

#### Verification

- `uv run pytest tests/ -x --tb=short` passes
- review-gate integration tests pass
- drive barrier/review transitions remain behaviorally identical or are improved with explicit tests
- any temporary `ConcreteDriveDriver` compatibility alias retained from Wave 2 is removed by the end of this wave

### Wave 6 — simplify duplicate-step-id domain model

#### Scope

Resolve DEM-017.

#### Required changes

1. Preserve top-level report/result objects needed by CLI/MCP outputs.
2. Collapse tiny nested wrappers such as:
   - `DuplicateStepIdPhaseRef`
   - `DuplicateStepIdResolutionPath`
   - `DuplicateStepIdRetryEvidence`
3. Inline their fields into parent result objects where doing so does not destroy output clarity.
4. Prefer shared serialization helpers over repeated hand-built mapping logic.

#### Acceptance criteria

1. Duplicate-step-id nested leaf type count is reduced by at least 3, with the named leaf wrappers `DuplicateStepIdPhaseRef`, `DuplicateStepIdResolutionPath`, and `DuplicateStepIdRetryEvidence` removed or fully inlined.
2. CLI and MCP outputs remain structured and stable.
3. Snapshot or golden-file tests prove duplicate-step-id JSON/structured outputs remain unchanged unless the PR explicitly documents an intended format change.

#### Verification

- `uv run pytest tests/ -x --tb=short` passes
- duplicate-step-id diagnostics, dry-run, and apply-result tests pass

### Wave 7 — re-cut large files by real behavior only

#### Scope

Resolve DEM-018 and DEM-019 after demolition is complete.

#### Required changes

1. Split `cli.py` into concrete behavior modules with these planned targets:
   - `src/vectl/cli_agents_md.py` — agents-md and project-bootstrap shell commands
   - `src/vectl/cli_plan.py` — plan read/mutate/list/show/claim/complete shell commands
   - `src/vectl/cli_render.py` — render/report/export-oriented shell commands
   - `src/vectl/cli_orchestration.py` — orchestration/drive/inspection shell commands
2. Split `core.py` into concrete behavior modules with these planned targets:
   - `src/vectl/core_plan_queries.py` — plan read/query helpers
   - `src/vectl/core_plan_mutations.py` — plan mutation/lifecycle entrypoints that remain core-owned
   - `src/vectl/core_duplicate_step_id.py` — duplicate-step-id analysis/migration logic that remains core-owned
3. Split `mcp_server.py` into concrete behavior modules with these planned targets:
   - `src/vectl/mcp_core_tools.py` — core MCP tools such as status/show/claim/complete/lifecycle reads
   - `src/vectl/mcp_project_tools.py` — init/project bootstrap/mutation-adjacent MCP tools
   - `src/vectl/mcp_render_tools.py` — render/report/inspection-oriented MCP tools
4. Split `orch_app.py` into concrete behavior modules with these planned targets:
   - `src/vectl/orch_app_wiring.py` — orchestration app construction and dependency wiring
   - `src/vectl/orch_app_read_surfaces.py` — read-only inspection/query surfaces exposed by the app
   - `src/vectl/orch_app_drive_ops.py` — drive start/resume/recover/operate entrypoints
5. Split `src/vectl/orchestration/run_store.py` into:
   - `src/vectl/orchestration/run_store_persistence.py` — durable read/write store mechanics
   - `src/vectl/orchestration/run_store_queries.py` — query/read helpers over persisted run data
6. Split `src/vectl/orchestration/driver.py` into:
   - `src/vectl/orchestration/driver_start_loop.py` — drive start and main execution loop behavior
   - `src/vectl/orchestration/driver_recovery.py` — recovery and resume/replay behavior
7. Mirror source-file splits in tests. New test files must follow the naming pattern `tests/.../test_<module_basename>.py` unless an existing package-level suite already owns that behavior and the PR explicitly documents the exception.
8. Add or tighten module docstrings in `config.py` and `contracts.py` to freeze boundary ownership and prevent new parallel models.

#### Acceptance criteria

1. No new file introduced in Wave 7 is a `Protocol`, abstract-base-only file, or `__init__`-only re-export module.
2. Each new file contains at least one concrete function or class with production callers.
3. File-size targets are met: `cli.py <= 2200`, `core.py <= 1800`, `mcp_server.py <= 1200`, `orch_app.py <= 2600`, `src/vectl/orchestration/run_store.py <= 1600`, and `src/vectl/orchestration/driver.py <= 1500`.
4. `config.py` and `contracts.py` each contain an explicit `owns:` section in the module docstring, and no model name appears in both ownership lists.
5. No module names beyond the planned targets above may be introduced unless this document is updated and re-reviewed before implementation begins.

#### Verification

- `uv run pytest tests/ -x --tb=short` passes
- `uv run vectl --help` succeeds
- `uv run python -c "import vectl; print('Import OK')"` succeeds
- user-facing behavior is unchanged

## Issue-to-wave coverage matrix

| Issue ID | Covered in wave |
|---|---|
| DEM-001 | Wave 1 |
| DEM-002 | Wave 1 |
| DEM-003 | Wave 1 |
| DEM-004 | Wave 1 |
| DEM-005 | Wave 4 |
| DEM-006 | Wave 4 |
| DEM-007 | Wave 4 |
| DEM-008 | Wave 3 |
| DEM-009 | Wave 3 |
| DEM-010 | Wave 3 |
| DEM-011 | Wave 3 |
| DEM-012 | Wave 2 |
| DEM-013 | Wave 2 |
| DEM-014 | Wave 5 |
| DEM-015 | Wave 2 |
| DEM-016 | Wave 4 |
| DEM-017 | Wave 6 |
| DEM-018 | Wave 7 |
| DEM-019 | Wave 7 |

## Cross-wave guardrails

These guardrails are mandatory for every remediation PR.

1. No behavior-changing demolition without characterization coverage for the touched surface.
2. No new public abstraction may be introduced unless the PR proves at least two real consumers or two real production implementations.
3. No direct `plan.yaml` edits.
4. No new reverse imports between orchestration layers.
5. No “temporary” duplicate helpers left behind after a wave lands.
6. Every deletion PR must remove exports, imports, and tests tied only to dead abstractions; contract-lock tests that still verify surviving boundaries must be migrated, not deleted.
7. Wave 4 validation must confirm that the surviving `CoreAdapter` boundary still works after Wave 3 removes inspection-query protocol layers. If that proof cannot be made cleanly, Waves 3 and 4 must be executed as a combined change.
8. Any change to the planned Wave 7 module names or file-size thresholds requires updating this document and rerunning document/spec review before code changes begin.

## Verification policy

This plan is only complete if remediation proves both local and repo-wide safety.

### Per-wave verification

Each wave must run `uv run pytest tests/ -x --tb=short` and capture the command output plus any wave-specific checks in the PR description.

### Final repo-wide readiness gate

Before declaring the demolition finished, run the final repo-wide readiness gate:

1. `uv run pytest tests/ -x --tb=short`
2. `uv run vectl --help`
3. `uv run python -c "import vectl; print('Import OK')"`
4. If the repo has adopted invar as a full-scan gate for this workstream, attach `uvx invar-tools guard --all` output as supplemental evidence.
5. Explicitly document any expected-red or intentionally deferred failure.

If a repo-wide clean pass is deferred, the deferral must name:

- why it is non-blocking
- who owns it
- issue or tracking reference
- expected resolution date
- what lifecycle/disposition prevents it from being mistaken for an ungoverned regression

## Definition of done

This remediation is complete only when all conditions below are true:

1. All issue IDs `DEM-001` through `DEM-019` are closed.
2. Deleted surfaces are absent from production code and exports.
3. Surviving abstraction seams have a real buyer.
4. No duplicated helpers remain for the targeted flows.
5. Review surfaces are backed by real implementations.
6. Query paths are direct and behavior-owned.
7. File decomposition, if performed, happens after simplification and does not reintroduce contract theater.

## Resolved pre-execution ownership decisions

1. Shared claim/affinity/conflict domain metadata remains lifecycle-owned in `src/vectl/lifecycle.py`. This plan does not authorize a new catch-all claim-domain module.
2. `StepData` moves to `src/vectl/orchestration/step_data.py`, a leaf data module with no reverse orchestration imports.
3. `AgentsTarget` remains canonical in `src/vectl/agents_md.py`; any compatibility re-export must be temporary, documented, and removed after migration.
4. `get_next_steps_with_phase()` and other shared plan-query helpers remain canonical in `src/vectl/plan_helpers.py`.
5. Shared duplicate-step-id diagnostic/recommendation formatting helpers remain canonical in `src/vectl/duplicate_step_id_format.py`.
6. There are no remaining plan-level open questions. Any future deviation from these ownership decisions requires updating this document and rerunning document/spec review before implementation starts.

## References

- `docs/ORCHESTRATION-PLANE-ARCHITECTURE.md`
- `docs/ORCHESTRATION-PLANE-IMPLEMENTATION-DESIGN.md`
- `docs/ORCHESTRATION-PLANE-INTERFACES.md`
- `docs/ORCHESTRATION-PLANE-DISPATCH-AND-PROMPT-POLICY.md`
- `docs/ORCHESTRATION-PLANE-RESOLUTION-CONTRACT.md`
- `docs/RFC-orch-drive.md`
