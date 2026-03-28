# ADR: Driver Evolution Foundation

**Status:** Proposed
**Date:** 2026-03-28
**Participants:** software-architect

## Context

The current `vectl.driver` runtime is functional and production-ready, but its event system and decision/action execution surfaces are still shaped like an initial implementation:

1. **Event contracts are implicit** — event names are emitted as string literals from many call sites, field shapes are distributed, and schema drift is possible.
2. **Action execution is loop-local** — `loop.py` owns `if/elif` branching over action types, making new runtime and planner actions expensive to add safely.
3. **Planner dispatch is a core runtime capability** — the driver cannot function without being able to dispatch planner work for replan/remediation branches.
4. **There is no historical compatibility burden yet** — no replay tooling or external event consumers exist, so this is the cheapest point to establish stable contracts.

The near-term debt-remediation phases (DecideState isolation, completion authority convergence, CLI entrypoint unification, policy extraction) improve correctness, but they do not by themselves establish a long-lived extension boundary for future vectl evolution.

## Decision Drivers

- Preserve a stable `loop.py` control skeleton while vectl grows
- Make event logs safe for future replay/dashboard/audit tooling
- Treat planner dispatch as a first-class runtime action, not an ad hoc helper path
- Avoid over-engineering: improve contracts and boundaries without building a generic plugin platform
- Use the current no-compatibility window to set canonical formats now

## Decision

Adopt a **contract-first evolution foundation** for the driver with five structural decisions:

### 1. Versioned event envelope now

Driver events will use a canonical JSONL envelope:

```json
{
  "ts": 1712345678.1,
  "event": "FINAL",
  "version": 1,
  "data": {}
}
```

The `version` field is part of the top-level envelope, not `data`. This is effective immediately because there are no historical replay or dashboard consumers to preserve.

### 2. Event registry becomes source of truth

Introduce a driver event registry that owns, per event type:

- canonical event name
- schema version
- required fields
- optional fields
- owner module
- compatibility notes

`observe.py` remains the durable JSONL sink, but event definitions move out of scattered call sites and into a single canonical registry.

### 3. `FINAL` becomes canonical summary event

`FINAL` will represent end-of-run summary semantics, not a raw runtime snapshot.

Canonical intent for `FINAL`:

- completed/finalized work summary
- total runtime duration
- total cost/tokens when available
- optional `halt_reason` if the run terminated early

If end-of-loop runtime snapshot data is still needed later, it should be expressed as a separate event rather than overloading `FINAL`.

### 4. Loop action registry covers all loop-executed actions, including planner dispatch

Introduce a loop action registry for **all actions directly executed by the driver main loop**, not just basic runner actions.

This explicitly includes planner dispatch actions. Planner dispatch is architecturally mandatory, so it must be modeled as a first-class action type with:

- action type name
- payload contract
- registered handler
- category
- event emission expectations

This replaces the pattern where planner dispatch survives as a special-case helper outside the main action model.

### 5. Action registry is layered, not flat

To avoid registry sprawl, action definitions are categorized:

- **execution** — claim/dispatch/wait and other direct runtime execution actions
- **planner** — dispatches that invoke planner-driven remediation/replanning
- **control** — defer/reject/freeze/escalation-style control actions
- **recovery** — startup/runtime repair actions such as claims/worktree cleanup

This preserves planner support without collapsing all future actions into an unstructured string namespace.

### 5. Handlers receive a unified `RuntimeContext`

Loop action handlers will initially receive a single shared `RuntimeContext` object rather than role-specific micro-contexts.

This is an intentional migration simplification: the current need is to stabilize loop dispatch structure and move branch logic out of `loop.py`, not to optimize for maximal context isolation yet.

If the context later grows too broad, role-specific contexts may be introduced as a second-step refactor. The initial contract should prefer migration safety over premature fragmentation.

### 6. Event registry is a static declaration table

The event registry will be implemented as a static canonical declaration table, not as a runtime registration API.

Why:

- event definitions must remain reviewable and auditable in one place
- spec conformance checks should be able to compare implementation against a closed set
- allowing module-level registration too early would reintroduce implicit event growth

Controlled registration helpers can be reconsidered later only if internal extension pressure becomes real.

## Target Architecture Shape

The following module split is the intended evolution target:

```text
driver/
  loop.py                # stable orchestration skeleton
  policy.py              # pure classification / parsing helpers
  runtime_context.py     # typed execution context for handlers
  action_registry.py     # action definitions + handler registry
  events/
    types.py             # event type constants / enum
    registry.py          # canonical event schemas and versions
    emitter.py           # typed emit helpers
    sinks.py             # FileObserver and future sinks

vectl/
  decision_state.py      # decide-local mutable state carrier
  decide.py              # decision algorithm using explicit state
```

## Ownership Model

| Concern | Source of Truth | Notes |
|--------|------------------|-------|
| Runtime orchestration state | `DriverState` | Running handles, queues, merge lock |
| Decide-local state | `DecideState` | Passed explicitly to `decide()` via state parameter; driver does not write to decide-memory directly |
| Event contracts | `driver.events.registry` | Canonical schema/version owner |
| Event durability | `observe.py` / future `events.sinks` | JSONL append-only sink remains valid |
| Loop-executed action contracts | `driver.action_registry` | Includes planner actions |
| Pure policy logic | `driver.policy` | Parsing/classification only |
| Durable plan lifecycle | vectl lifecycle ops + `plan.yaml`/`claims.json` | Unchanged |

## Consequences

### Positive

- `loop.py` becomes more stable under future vectl evolution
- event logs become self-describing and replay/dashboard-friendly
- planner dispatch becomes explicit and auditable
- action additions become registry work, not loop surgery
- the current no-compatibility window is used to set a clean baseline

### Negative / Costs

- module count increases
- there is short-term migration work from direct `observer.emit(...)` and `if/elif` action handling
- event and action schemas must now be maintained deliberately

### Accepted Trade-off

We accept modest structural overhead now to avoid repeated uncontrolled growth in `loop.py` and implicit event/action contracts later.

## Implementation Guidance

Recommended rollout order:

1. complete current debt-remediation chain (DecideState, completion authority, CLI unification, policy extraction)
2. add event registry + envelope versioning
3. canonicalize `FINAL`, `DECIDE`, and `STEP_COMPLETED`
4. add typed event emit helpers
5. add `runtime_context.py`
6. add loop action registry covering existing runtime actions **and planner dispatch**
7. migrate loop branching to registry-backed handlers

## Non-Goals

- user-configurable event types
- generic plugin platform for arbitrary third-party action handlers
- replacing JSONL with a database or message bus
- remote policy loading or dynamic rule execution

## Resolved Decisions

1. **`FINAL` includes optional `halt_reason`.**
   - `HALT` remains a first-class event for stop causality in-stream.
   - `FINAL` includes optional `halt_reason` so end-of-run consumers do not need to back-scan the event stream just to understand terminal outcome.

2. **Loop action handlers receive a single `RuntimeContext` in the initial design.**
   - This minimizes migration complexity while the action-registry model is introduced.
   - Role-specific contexts are explicitly deferred.

3. **Event registry is a static declaration table.**
   - No module-driven registration API in the first version.
   - This keeps event growth explicit and reviewable.

## Open Questions

None at this ADR level. Any future changes should supersede or amend this ADR with concrete implementation pressure.

## Follow-up Work

- plan a dedicated driver-evolution phase chain after the current debt-remediation phases
- update `docs/DRIVER-ARCHITECTURE.md` to reference this ADR when the implementation starts
- add spec-verification steps for event schema conformance and planner action registration
