# Orchestration Plane Implementation Design

> Code-level design for implementing the target orchestration plane without
> prematurely deleting the current legacy package.

**Status:** Implementation design  
**Architecture authority:** `docs/ORCHESTRATION-PLANE-ARCHITECTURE.md`  
**Interface authority:** `docs/ORCHESTRATION-PLANE-INTERFACES.md`  
**Related docs:** `docs/ORCHESTRATION-PLANE-RESOLUTION-CONTRACT.md`, `docs/ORCHESTRATION-PLANE-ISOLATION-SEMANTICS.md`, `docs/ORCHESTRATION-PLANE-MIGRATION.md`

---

## 1. Purpose

This document translates the target orchestration-plane design into a code-level
implementation shape.

It answers:

1. What future package/module layout should the orchestration plane use?
2. How should shared contracts be represented in code?
3. How should legacy package responsibilities be extracted without destroying
   working behavior?

This is still design, not implementation. It defines the intended structure that
code changes should aim toward.

---

## 2. Target Package Shape

Recommended new package:

```text
src/vectl/orchestration/
    __init__.py
    contracts.py
    judgments.py
    control.py
    roster.py
    runtime.py
    resolver.py
    core_adapter.py
    config.py
    events.py
```

### Why this package

- `orchestration` matches the target architecture name
- it avoids reusing `driver` as a future concept
- it is explicit enough to coexist with the legacy package during migration

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

### Local typed judgment support

`control.py` may depend on shared typed judgment helpers/schemas from
`judgments.py` when deterministic/local typed reasoning is useful.

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

## 3.9 `judgments.py`

### Responsibility

Shared typed judgment helpers/schemas that remain useful in the target system.

### Expected contents

- typed/local judgment enums and schemas that are still valuable outside the
  legacy package
- helper contracts for local typed reasoning that `control` may call directly
- parsing/schema support that `resolver` may also reuse when appropriate

### Why this module exists

The legacy package currently mixes two different things:

- typed judgment schemas/contracts (`judgments.py`)
- actual LLM invocation glue (`judge.py`)

The target package should preserve that distinction.

### Disposition of legacy modules

- legacy `driver/judgments.py` should evolve toward `orchestration/judgments.py`
- legacy `driver/judge.py` should not remain a future architecture concept; any
  surviving invocation glue belongs under `resolver.py`

---

## 4. Code-Level Ownership Map from Legacy Package

This complements the migration doc with a stronger code-design angle.

| Legacy code | Target code shape | Implementation note |
|---|---|---|
| `driver/session.py` | `orchestration/roster.py` | strongest early extraction candidate |
| `driver/worktree.py` | `orchestration/runtime.py` | workspace mechanics |
| `driver/runners.py` | `orchestration/runtime.py` | runner mechanics |
| `driver/parsers.py` | `orchestration/runtime.py` or nearby helper | keep close to execution collection |
| `driver/observe.py` + event registry concerns | `orchestration/events.py` | shared support owned by the orchestration plane |
| `driver/loop.py` | split across `control.py` + small coordination glue | do not port wholesale |
| `driver/judgments.py` | `orchestration/judgments.py` | typed/local schema support; not a top-level architecture component |
| `driver/judge.py` | `orchestration/resolver.py` support | any surviving invocation glue belongs under blocked-case reasoning |
| `driver/config.py` | `orchestration/config.py` bridge later | avoid premature config duplication |
| `driver/types.py` | split into `contracts.py` plus owner-local internal types | do not carry over driver-shaped aggregation blindly |

---

## 5. Recommended Extraction Order

This order is not a product-scope statement. It is an implementation-design
sequence chosen to preserve behavior and test leverage.

### Step 1 — establish the new package and contracts

Create:

- `src/vectl/orchestration/__init__.py`
- `contracts.py`
- skeletal `control.py`, `roster.py`, `runtime.py`, `resolver.py`, `judgments.py`
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

### Step 5 — carve `control` out of legacy loop behavior

Implement `control` by extracting plan-aware flow logic from the legacy loop,
using `core_adapter`, `roster`, and `runtime`.

Important:

- do not wholesale copy `loop.py`
- move only the responsibility that truly belongs to `control`

### Step 6 — attach `resolver`

Implement the `control ↔ resolver` contract using the target `ResolutionCase` /
`ResolutionReport` model.

Initial implementation may wrap or reuse legacy reasoning surfaces, but the
orchestration-plane contract—not legacy module names—should define the boundary.

### Step 6a — rehome typed judgment support cleanly

Before or during resolver attachment, extract reusable typed judgment schemas and
helpers into `judgments.py` so that:

- local typed reasoning remains available to `control`
- blocked-case invocation glue remains clearly under `resolver`
- the legacy `judge.py` / `judgments.py` split is not collapsed into a new blob

### Step 7 — rehome tests by behavior

Move tests as each behavior gets a clear new owner.

---

## 6. Legacy Entrypoint Strategy

The orchestration plane should not require an immediate delete/replace cutover of
legacy entrypoints.

Recommended strategy:

- keep current legacy entrypoints while target components are extracted
- introduce new orchestration package surfaces alongside them
- only later decide how CLI/MCP/runtime entrypoints should target the new plane

This avoids conflating architecture migration with user-facing cutover.

---

## 6a. Test Organization Convention

Recommended future test layout:

```text
tests/
  orchestration/
    unit/
    integration/
  legacy/
    driver/
```

Interpretation:

- `tests/orchestration/unit/` — target component-level tests (`control`,
  `roster`, `runtime`, `resolver`, `judgments`, `core_adapter`)
- `tests/orchestration/integration/` — orchestration-plane interaction tests
- `tests/legacy/driver/` — legacy baseline tests retained during migration

Existing tests do not need immediate movement, but new target tests should start
using the future-oriented layout rather than reinforcing legacy naming.

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

---

## 9. Anti-Patterns to Avoid in Implementation

1. **Recreating `drive` under a new package name**
   - if `roster` becomes plan-aware, or `runtime` starts deciding flow, the
     architecture has collapsed

2. **Bulk renaming without responsibility change**
   - renaming `driver/loop.py` to `orchestration/control.py` is not a migration

3. **Deleting tests before equivalent owners exist**
   - this destroys behavioral leverage

4. **Encoding optimization concepts as public contracts**
   - session reuse remains an internal optimization, not a planning API

5. **Bypassing core authority through convenience helpers**
   - all authoritative mutations must still flow through official core surfaces

---

## 10. Minimum Done Condition for Entering Code Work

Code-level implementation work should begin from this document only when:

- target docs are accepted as the design baseline
- the package target `src/vectl/orchestration/` is accepted
- the four target component responsibilities remain stable
- migration is understood as responsibility extraction, not big-bang rewrite

---

## 11. Summary

The code-level design target is:

- a new `src/vectl/orchestration/` package
- shared contracts in `contracts.py`
- explicit component modules for `control`, `roster`, `runtime`, and `resolver`
- a thin `core_adapter.py`
- shared `config.py` / `events.py`
- responsibility-driven extraction from the legacy package

This is the implementation-design baseline for the next phase.
