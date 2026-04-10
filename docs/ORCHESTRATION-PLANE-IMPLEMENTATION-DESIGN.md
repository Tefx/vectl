# Orchestration Plane Implementation Design

> Code-level design for implementing the target orchestration plane without
> prematurely deleting the current legacy package.

**Status:** Implementation design  
**Architecture authority:** `docs/ORCHESTRATION-PLANE-ARCHITECTURE.md`, `docs/ADR-orchestration-role-profile-config-and-resolver-cleanup.md`  
**Interface authority:** `docs/ORCHESTRATION-PLANE-INTERFACES.md`  
**Related docs:** `docs/ORCHESTRATION-PLANE-RESOLUTION-CONTRACT.md`, `docs/ORCHESTRATION-PLANE-ISOLATION-SEMANTICS.md`

---

## 1. Purpose

This document translates the target orchestration-plane design into a code-level
implementation shape.

It answers:

1. What future package/module layout should the orchestration plane use?
2. How should shared contracts be represented in code?

This is still design, not implementation. It defines the intended structure that
code changes should aim toward.

---

## 2. Target Package Shape

Current implementation package:

```text
src/vectl/orchestration/
    __init__.py
    contracts.py          # Shared boundary types (landed)
    control.py            # Plan-aware orchestration flow (landed)
    roster.py             # Reusable agent/session registry (landed)
    runtime.py            # Mechanical execution support (landed)
    resolver.py           # Blocked/unresolved case reasoning (landed)
    core_adapter.py       # Thin adapter over vectl core (landed)
    config.py             # Shared orchestration config (landed)
    dispatch_policy.py    # Dispatch spec, role-profile loading, prompt rendering
    events.py             # Canonical event envelopes (landed)
    interfaces.py         # Protocol definitions
    tool_registry.py      # Canonical tool registry
    run_store.py          # Run persistence
    projections.py        # State projection replay
    recovery.py           # Continuity/recovery
    resolver_gateway.py   # Resolver invocation gateway
    control_channel.py    # Operator control channel
    continuity_artifacts.py # Artifact management
    inspection_queries.py # Query surfaces

src/vectl/orch_app.py   # Composition root (landed)
```

### Why this package

- `orchestration` matches the target architecture name
- it clearly separates the orchestration subsystem from core

---

## 3. Module Responsibilities

## 3.1 `contracts.py`

### Responsibility

Own the shared orchestration-plane boundary types defined in
`ORCHESTRATION-PLANE-INTERFACES.md`.

Examples:

- `CoreSnapshot`
- `RosterSnapshot`
- `RuntimeSnapshot`
- `ControlDecision`
- `WorkLease`
- `ExecutionRequest`
- `ExecutionResult`
- `ResolutionCase`
- `ResolutionReport`

### Why separate module

These are cross-component contracts. They should not be hidden inside one
component module.

---

## 3.2 `control.py`

### Responsibility

Implement the plan-aware orchestration flow.

### Expected contents

- `Control` implementation
- normal-flow evaluation logic
- blocked/unresolved detection
- interaction with `roster`, `runtime`, and `resolver`
- post-resolution resnapshot/re-evaluation logic

### Must not contain

- workspace mechanics
- session registry internals
- long-running reasoning implementation

### Local typed support

`control.py` may depend on shared contracts or local helpers for deterministic
reasoning when useful.

This does **not** create a fifth architecture component. It is code support
under `control`, not a new top-level concept.

---

## 3.3 `roster.py`

### Responsibility

Implement reusable agent/session resource tracking.

### Expected contents

- `Roster` implementation
- TTL / reuse-window bookkeeping
- capability-based reusable resource matching
- claim/release/register logic

### Must not contain

- plan semantics
- blocked-state interpretation
- next-step dispatch logic

---

## 3.4 `runtime.py`

### Responsibility

Implement mechanical execution support.

### Expected contents

- workspace/worktree prep and cleanup
- runner startup / resume / shutdown helpers
- execution collection/parsing wiring

### Must not contain

- plan-aware control
- blocked reasoning
- authority ownership

---

## 3.5 `resolver.py`

### Responsibility

Implement the resolver role boundary described by
`ORCHESTRATION-PLANE-RESOLUTION-CONTRACT.md`.

### Expected contents

- `Resolver` implementation or adapter boundary
- machine-readable `ResolutionReport` parsing/validation
- allowed tool surface wiring for resolution cases
- any surviving legacy LLM-invocation glue for blocked-case reasoning

### Must not contain

- permanent orchestration flow ownership
- reusable resource bookkeeping
- general runtime mechanics

### Authorization model

`resolver.py` should consume a static allowlist-style authorization model for its
allowed tool surfaces. The implementation should be deny-by-default.

---

## 3.6 `core_adapter.py`

### Responsibility

Provide the orchestration plane with a thin adapter over `vectl core`.

### Why it exists

The orchestration plane depends on core authority but should not scatter core
reads/writes ad hoc across every component.

### Expected contents

- snapshot-building helpers from core state
- lifecycle/claim/complete/defer calls through official core surfaces
- isolation field access once core schema evolves

---

## 3.7 `config.py`

### Responsibility

Shared orchestration-plane configuration.

### Expected contents

- runtime/roster/resolver relevant config models
- compatibility bridge to legacy config only while migration is active
- resolver tool authorization allowlist/configuration shape

---

## 3.8 `events.py`

### Responsibility

Shared event/logging surface for orchestration-plane observability.

### Why separate module

Legacy `observe.py` and event registry behavior are useful across multiple future
owners; the event surface should not be treated as only one component's concern.

### Ownership clarification

`events.py` is owned by the orchestration plane as **shared support**. It is not
core-owned authority, and it is not a private submodule of one component.

---

## 3.9 `dispatch_policy.py`

### Responsibility

Own dispatch-spec construction support, config-backed role-profile loading, and
prompt-rendering coordination.

### Expected contents

- `DispatchSpec`-adjacent assembly support
- `RoleProfileRegistry` implementation backed by orchestration config
- prompt-rendering coordination shared by ordinary dispatch and resolver-spawned
  subtasks

### Why this module exists

Dispatch and role-profile policy is shared support, but it does not belong in
`control` or `runtime`.

---

## 4. Code-Level Ownership Map

| Concern | Target code shape | Implementation note |
|---|---|---|
| session/resource registry | `orchestration/roster.py` | resource reuse and TTL |
| workspace mechanics | `orchestration/runtime.py` | workspace prep and cleanup |
| runner mechanics | `orchestration/runtime.py` | runner lifecycle |
| event registry | `orchestration/events.py` | shared support owned by the orchestration plane |
| orchestration loop decisions | `control.py` + small coordination glue | plan-aware dispatch/wait/done |
| dispatch and role-profile policy | `orchestration/dispatch_policy.py` | config-backed dispatch assembly |
| blocked-case reasoning | `orchestration/resolver.py` | invocation glue under resolver |
| configuration | `orchestration/config.py` | orchestration-specific config |

---

## 5. Recommended Extraction Order

This order is not a product-scope statement. It is an implementation-design
sequence chosen to preserve behavior and test leverage.

### Step 1 — establish the new package and contracts

Create:

- `src/vectl/orchestration/__init__.py`
- `contracts.py`
- skeletal `control.py`, `roster.py`, `runtime.py`, `resolver.py`, `dispatch_policy.py`
- `core_adapter.py`

No behavior migration yet; just the explicit package boundary.

### Step 2 — extract `roster`

Move reusable session/resource logic out of the legacy package shape first.

Why first:

- narrowest responsibility
- already strongly identified in migration mapping
- easiest place to enforce "resources, not plans"

### Step 3 — extract `runtime`

Move worktree/runner mechanical support next.

Why next:

- also relatively narrow and mechanical
- behavior can be preserved with lower conceptual risk than `control`

### Step 4 — introduce `core_adapter`

Make core reads/writes explicit through one boundary.

Why before `control` rewrite:

- it keeps future `control` simpler
- it prevents new plan-aware logic from scattering across modules

### Step 5 — carve `control` out of loop behavior

Implement `control` by extracting plan-aware flow logic,
using `core_adapter`, `roster`, and `runtime`.

Important:

- move only the responsibility that truly belongs to `control`

### Step 6 — attach `resolver`

Implement the `control ↔ resolver` contract using the target `ResolutionCase` /
`ResolutionReport` model.

Initial implementation may wrap or reuse existing reasoning surfaces, but the
orchestration-plane contract should define the boundary.

### Step 7 — rehome tests by behavior

Move tests as each behavior gets a clear new owner.

**Current test layout:**

```text
tests/
  orchestration/
    unit/
      test_contracts.py
      test_control.py
      test_roster.py
      test_runtime.py
      test_resolver.py
      test_dispatch_policy.py
      test_config.py
      test_events.py
      test_core_adapter.py
      test_orch_app.py
    integration/
      test_orchestration_integration.py
```

---

## 7. Isolation Schema Impact

The isolation semantic resolved in
`ORCHESTRATION-PLANE-ISOLATION-SEMANTICS.md` has direct code-level implications.

### Step model impact

The future authoritative step model should gain:

```python
class IsolationMode(str, Enum):
    DEFAULT = "default"
    WORKSPACE = "workspace"
    INDEPENDENT = "independent"


class Step(BaseModel):
    ...
    isolation: IsolationMode = IsolationMode.DEFAULT
```

### Component impact

- `core_adapter` must expose the step's isolation semantic to `control`
- `control` must propagate isolation into work dispatch decisions
- `roster` must enforce no warm-resource reuse for `independent`
- `runtime` must enforce fresh workspace behavior for `workspace` and
  `independent`

---

## 8. Resolver Contract Impact

The resolution contract resolved in
`ORCHESTRATION-PLANE-RESOLUTION-CONTRACT.md` has direct code-level implications.

### Minimum code boundary

- `control` creates `ResolutionCase`
- `resolver` returns `ResolutionReport`
- `control` refreshes state and reevaluates

### Important consequence

Do not prematurely invent a large bespoke action language if the resolver can
use approved official surfaces and return a bounded report.

### Planner mutation rule

If planner output results in authoritative plan changes, those changes must be
applied through the approved vectl facade. Direct `plan.yaml` edits remain out
of bounds.

---

## 9. Anti-Patterns to Avoid in Implementation

1. **Recreating `drive` under a new package name**
   - if `roster` becomes plan-aware, or `runtime` starts deciding flow, the
     architecture has collapsed

2. **Bulk renaming without responsibility change**
    - simply moving code without separating concerns is not a migration

3. **Deleting tests before equivalent owners exist**
   - this destroys behavioral leverage

4. **Encoding optimization concepts as public contracts**
   - session reuse remains an internal optimization, not a planning API

5. **Bypassing core authority through convenience helpers**
    - all authoritative mutations must still flow through official core surfaces

6. **Treating legacy plan vocabulary as architecture authority**
    - historical `plan.yaml` terms must not be revived as target contracts

---

## 10. Minimum Done Condition for Entering Code Work

Code-level implementation work should begin from this document only when:

- target docs are accepted as the design baseline
- the package target `src/vectl/orchestration/` is accepted
- the four target component responsibilities remain stable

---

## 11. Summary

The code-level design target is:

- a new `src/vectl/orchestration/` package
- shared contracts in `contracts.py`
- explicit component modules for `control`, `roster`, `runtime`, and `resolver`
- a thin `core_adapter.py`
- shared `config.py` / `dispatch_policy.py` / `events.py`

This is the implementation-design baseline for the next phase.
