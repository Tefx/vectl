# Architectural demolition remediation plan

## Status

- Status: proposed remediation plan
- Scope: repository-wide architectural simplification and de-duplication
- Primary goal: remove duplicated logic, false abstraction seams, dead public surfaces, and contract theater without collapsing valid authority boundaries

## 2026 cleanup note

Built-in orchestration-plane design, runtime, proof, and recovery material has been removed from the documentation set. Historical findings that depended only on those deleted surfaces are no longer tracked in this plan. This document now retains only the non-orchestration remediation items that still describe repository architecture work.

## Why this document exists

Three independent reviews (`duplicate-abstraction-auditor-tacit`, `architectural-demolitionist-tacit`, and direct repository inspection) converged on the same core problem: the repo pays high cognitive cost for duplicated logic and speculative abstraction while several real authority surfaces remain valid and should be preserved.

This document records the remaining remediation plan for issues that are still relevant after removal of built-in orchestration-plane documentation.

## Remediation objectives

1. Ensure each concept has one authoritative implementation.
2. Remove pure pass-through wrappers and single-buyer protocols that do not buy real substitution value.
3. Delete dead exported surfaces.
4. Preserve valid boundaries:
   - `core` vs `cli`/`mcp_server`
   - config input models vs runtime contract models
5. Re-cut large files only **after** abstraction removal, never by adding new contract-only files.

## Non-goals / preserved boundaries

The remediation must **not** do the following:

1. Merge `src/vectl/core.py` and `src/vectl/cli.py` into one module.
2. Flatten all `DuplicateStepId*` reporting into untyped dictionaries.
3. Introduce more protocol-only or contract-only files as an organizational tactic.
4. Change `plan.yaml` directly; any plan-state mutation remains vectl-owned.

## Remaining issue register

| ID | Severity | Issue | Exact locations | Required disposition |
|---|---|---|---|---|
| DEM-001 | high | `agents-md` logic duplicated across shell and authority surfaces | `src/vectl/cli.py:2401-2485`, `src/vectl/core.py:2582-2677` | Extract shared implementation into `src/vectl/agents_md.py`; `agents_md.py` becomes the canonical owner of `AgentsTarget`, helper constants, detect, and upsert logic; `core.py` may re-export temporarily only for compatibility |
| DEM-002 | high | `_get_next_steps_with_phase` duplicated | `src/vectl/cli.py:201-242`, `src/vectl/mcp_server.py:284-327` | Extract one shared helper |
| DEM-003 | medium | duplicate step-id diagnostics formatting duplicated | `src/vectl/cli.py:155-199`, `src/vectl/mcp_server.py:245-276` | Extract one shared structure/formatter boundary |
| DEM-004 | medium | claim/affinity/conflict metadata modeled in parallel across lifecycle and MCP | `src/vectl/lifecycle.py:29-75`, `src/vectl/mcp_server.py:486-551` | Keep shared claim metadata lifecycle-owned in `src/vectl/lifecycle.py`; keep MCP transport envelope thin and consume lifecycle-owned metadata instead of redefining domain objects |
| DEM-017 | medium | `DuplicateStepId*` nested leaf DTO family is over-factored | `src/vectl/models.py:286-497` | Preserve outer reports, collapse tiny nested leaf wrappers |
| DEM-018 | medium | giant files still carry too much behavior after years of additive splitting | `src/vectl/cli.py`, `src/vectl/core.py`, `src/vectl/mcp_server.py` | Re-cut by behavior only after demolition; hit agreed size targets in implementation evidence |
| DEM-019 | low | config/contracts boundary needs explicit freeze to prevent future parallel models | configuration and contract model owners | Clarify ownership in docs and module docstrings |

## Target end-state

After remediation:

1. Each user-visible concept is defined once.
2. Read paths avoid protocol/implementation/view stacks that do not add authority boundaries.
3. Large files are reduced only after duplication and wrapper layers are removed.

## Canonical design decisions

### 1. Shell vs authority remains split

- `core.py` remains the authority layer for plan semantics.
- `cli.py` and `mcp_server.py` remain presentation/transport layers.
- Shared helper logic moves to small behavior-owning modules, not back into shells.

### 2. Concrete implementation beats speculative protocol

For repository-internal surfaces with only one production path, use the concrete class and test with fakes/stubs as needed. Do not publish a protocol merely because tests can mock it.

**Real buyer definition:** a consumer is a real buyer only if it (a) invokes the surface in production code paths and (b) would require meaningful refactoring if the surface were removed. Mock-only tests, protocol existence tests, and conformance tests without substantive production behavior do not qualify.

### 3. Canonical ownership decisions fixed before execution

- `src/vectl/agents_md.py` is the canonical owner of `AgentsTarget`, agents-md snippet constants, `detect_agents_target()`, and `upsert_agents_md()`.
- `src/vectl/plan_helpers.py` is the canonical owner of `get_next_steps_with_phase()` and any other shared plan-query helpers consumed by both CLI and MCP shells.
- `src/vectl/duplicate_step_id_format.py` is the canonical owner of duplicate-step-id diagnostic and recommendation formatting helpers consumed by both CLI and MCP shells.
- `src/vectl/lifecycle.py` remains the canonical owner of shared claim/affinity/conflict domain metadata. CLI and MCP may wrap or render that data, but they must not redefine it.
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
4. claim conflict/affinity shaping across CLI and MCP surfaces.

#### Acceptance criteria

1. Existing behavior is pinned before demolition begins.
2. No demolition PR may weaken or delete characterization tests without replacing them with equivalent or stronger coverage.

#### Verification

- `uv run pytest tests/ -x --tb=short` must pass before Wave 1 begins
- focused tests for touched shell surfaces must be named in the PR evidence and must pass in addition to the full suite

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

### Wave 2 — collapse duplicate-step-id DTO leaves

#### Scope

Resolve DEM-017.

#### Required changes

1. Preserve the outer duplicate-step-id report objects that define externally useful result shapes.
2. Collapse tiny nested leaf wrappers that duplicate tuple/dict structure without adding invariants.
3. Keep CLI and MCP rendering compatible with existing user-facing diagnostics.

#### Acceptance criteria

1. Public duplicate-step-id diagnostics remain stable for users.
2. The number of production DTO leaf types is reduced.
3. Tests cover at least one duplicate-step-id diagnostic flow from both CLI and MCP-facing code.

#### Verification

- `uv run pytest tests/test_cli.py tests/test_mcp_server.py -q` passes, or replacement focused paths are named with rationale
- full-suite status is recorded in evidence

### Wave 3 — behavior-based file cuts

#### Scope

Resolve DEM-018 and DEM-019 after the preceding duplicate abstractions are removed.

#### Required changes

1. Re-cut `cli.py`, `core.py`, and `mcp_server.py` by cohesive behavior, not by creating contract-only staging modules.
2. Document config/contract ownership rules in the owning module docstrings or architecture docs.
3. Preserve public command and MCP surfaces while moving implementation details.

#### Acceptance criteria

1. New modules have clear behavior ownership and at least one production buyer.
2. No new protocol-only file is introduced solely for organization.
3. Public CLI and MCP behavior remains compatible.

#### Verification

- `uv run pytest tests/ -x --tb=short` passes
- `uv run vectl --help` succeeds
- a diff review confirms new files are behavior-owned rather than contract-only

## Lifecycle rules

- Do not claim an issue complete without command evidence.
- Do not weaken characterization tests merely to make demolition easier.
- Do not mutate `plan.yaml` directly.
- If a remediation item becomes obsolete because its referenced surface has been deleted, update this plan instead of carrying stale references forward.
