# ADR: Orchestration Plane Reset

**Status:** Proposed  
**Date:** 2026-04-03  
**Participants:** software-architect

## Context

Previous design work explored a future where the existing `src/vectl/driver/`
implementation path became the main deterministic execution surface and
gradually absorbed more orchestration responsibility. That line of design has
been reset.

The new first-principles position is:

- `vectl core` remains an independent, mature authority for plan, lifecycle,
  claims, and existing core decision/query surfaces.
- Orchestration is a **separate subsystem** that depends on core rather than
  standing beside it as a peer concept.
- The goal is not to make a permanent sovereign replacement for the current
  orchestrator. The goal is to combine:
  - plan-aware control,
  - resource/session reuse,
  - mechanical runtime chores,
  - and complex blocker resolution,
  while keeping these concerns explicitly separated.

This ADR fixes the new architectural vocabulary and bans several concepts that
were adding design churn without enough value.

The current target documentation set is:

- `docs/ORCHESTRATION-PLANE-ARCHITECTURE.md`
- `docs/ORCHESTRATION-PLANE-INTERFACES.md`
- `docs/ORCHESTRATION-PLANE-RESOLUTION-CONTRACT.md`
- `docs/ORCHESTRATION-PLANE-ISOLATION-SEMANTICS.md`
- `docs/ORCHESTRATION-PLANE-IMPLEMENTATION-DESIGN.md`
- `docs/ORCHESTRATION-PLANE-MIGRATION.md`

## Decision

Adopt the following top-level structure:

```text
vectl core
orchestration plane
  - control
  - roster
  - runtime
  - resolver
```

`vectl core` is **not** one component among these four. It is an external
authority dependency. The four components together make up the orchestration
plane.

This ADR records the reset decision and fixed vocabulary. The authoritative full
target design now lives in `docs/ORCHESTRATION-PLANE-ARCHITECTURE.md`.

## Component Definitions

### 1. `control`

Plan-aware control component.

Owns:

- reading core state together with orchestration-plane state
- deciding whether work can continue, should be dispatched, or is blocked
- triggering the `resolver` when the system is blocked or otherwise not closed
  by normal flow

Does **not** own:

- plan/lifecycle authority
- reusable session bookkeeping details
- mechanical worktree/runner chores
- long-lived agent reasoning itself

### 2. `roster`

TTL-based registry of reusable agent/session resources.

Owns:

- registration of reusable agents/sessions
- TTL / reuse-window bookkeeping
- capability / runner / session metadata
- claim / release of compatible reusable resources

Does **not** own:

- plan semantics
- blocked-state interpretation
- next-step control decisions

### 3. `runtime`

Mechanical execution helper for non-core chores.

Owns:

- worktree preparation / cleanup / merge support
- runner startup / resume / shutdown helpers
- other purely mechanical environment work needed by agents

Does **not** own:

- plan-aware control
- blocker reasoning
- authority mutations outside the existing vectl/core boundaries

### 4. `resolver`

Reasoning role used when normal orchestration flow is blocked or unresolved.

Owns:

- investigating blockers
- using vectl, runtime, and other allowed tools to determine how to unblock
- returning control decisions or actions back into the orchestration plane

Does **not** own:

- permanent system authority
- mechanical startup/dispatch mechanics (those remain outside the resolver)

## Fixed Naming

The orchestration-plane component names are fixed for this design round as:

- `control`
- `roster`
- `runtime`
- `resolver`

These are intentionally short and neutral. They should be preferred over the
earlier placeholders (`servant`, `pool`) and over overloaded names such as a
nested `orchestrator` inside the orchestration plane.

## Banned Concepts

The following concepts are explicitly rejected for the current redesign:

### 1. legacy `src/vectl/driver/` as the target future architecture

The reset does **not** continue the prior drive-centric future design. Current
`src/vectl/driver/` implementation documents remain useful as records of the
existing codebase, but they are not the target orchestration-plane architecture.

### 2. `continuity_group`

Rejected.

Reason:

- if two tasks require shared context strongly enough to need a group, they
  should usually be one task, or the required context should be made explicit in
  task description / refs / produced artifacts
- this concept adds planner and runtime drift risk without enough independent
  value

### 3. `session_reuse` as an architecture-level concept

Rejected as a first-class architecture concept.

Reason:

- session reuse is an optimization, not a correctness boundary
- it should remain an internal `roster` implementation choice where useful
- callers and planning logic should not need to reason about it as a formal
  design concept

## Allowed Simplicity Rule

Tasks should be treated as self-contained. If task B needs information from task
A, that information should usually be carried by:

- task decomposition
- task description
- refs
- explicit artifacts

not by introducing new continuity abstractions.

## Important Boundary Rule

`roster` knows resources, not plans.

This rule is critical.

`roster` may know:

- which reusable agents/sessions exist
- their TTL / reuse window
- their capability metadata
- whether they can be claimed or released

`roster` must **not** become a hidden plan-aware scheduler. If it starts owning
plan semantics or blocked-state interpretation, the architecture collapses back
into a renamed legacy-control blob.

## Open Question Preserved for Next Round

One important open problem remains intentionally open:

### Explicit isolation semantics for certain tasks

Some tasks (for example independent review/audit) may require an isolated
execution environment and must not reuse a previous agent/session. This appears
to be a real task semantic rather than an orchestration heuristic.

That requirement may justify a small explicit core/planner-level execution
constraint later. This ADR does **not** finalize that schema; it only records
that this is a real open issue and that it should not be solved by reviving
`continuity_group`.

## Consequences

### Positive

- clarifies that orchestration is a subsystem over core, not a peer of core
- fixes a simpler four-part vocabulary for the redesign
- removes speculative continuity concepts from the architecture surface
- creates cleaner separation between plan-aware control, resource reuse,
  mechanical runtime chores, and blocker reasoning

### Cost

- the design still needs a later concrete contract for how `control` triggers
  and consumes `resolver` behavior
- explicit isolation semantics are still unresolved

## Superseded Design

This ADR supersedes the prior legacy-package-centric future design direction captured in:

- `docs/ADR-drive-orchestrator-delegated-decision-model.md`

That document should be deleted rather than retained as competing future
architecture guidance.
