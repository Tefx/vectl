# Orchestration Plane Architecture

> Full target architecture for the orchestration subsystem that depends on
> `vectl core`.

**Status:** Target architecture  
**Authority:** `docs/ADR-orchestration-role-profile-config-and-resolver-cleanup.md`, `docs/ADR-orchestration-role-agent-prompt-separation.md`, `docs/ORCHESTRATION-PLANE-LIVE-AUTHORITY-CONTRACT-LOCK.md`  
**Scope:** Full target design, not an implementation slice  
**Related docs:** `docs/ORCHESTRATION-PLANE-INTERFACES.md`, `docs/ORCHESTRATION-PLANE-RESOLUTION-CONTRACT.md`, `docs/ORCHESTRATION-PLANE-ISOLATION-SEMANTICS.md`, `docs/ORCHESTRATION-PLANE-IMPLEMENTATION-DESIGN.md`, `docs/RFC-orch-drive.md`

---

## 1. Purpose

The orchestration plane exists to turn:

- `vectl core` authority and state,
- reusable agent/session resources,
- mechanical runtime capabilities,
- and complex blocker-resolution reasoning,

into a coherent orchestration system.

`vectl core` is not part of the orchestration plane. It is an external authority
dependency that the orchestration plane consumes.

---

## 2. Top-Level Structure

```text
vectl core
orchestration plane
  - control
  - roster
  - runtime
  - resolver
```

### 2.1 Layer Relationship

- **`vectl core`** owns plan, lifecycle, claims, and existing authoritative
  decision/query surfaces.
- **`orchestration plane`** owns orchestration behavior over that authority.

The orchestration plane is complete only when all four of its internal
components cooperate. None of them individually substitutes for the whole plane.

---

### 2.2 Drive session model

The target orchestration surface introduces a durable **drive session** for full
plan execution. A drive session is **not** a fifth top-level architecture
component. It is the session-level coordination record that composes the four
actual components:

- `control` evaluates the next scheduler decision
- `roster` provides reusable resource facts
- `runtime` executes and reconciles child runs
- `resolver` handles blocked or unresolved cases

The drive session owns orchestration-session state such as active child runs,
frontier, barrier state, and aggregate observability. It does not replace
component ownership boundaries.

---

## 3. Design Goals

### G1. Preserve core authority

The orchestration plane must consume `vectl core`; it must not re-own or fork
plan/lifecycle/claims authority.

### G2. Separate plan-aware control from resource mechanics

Plan-aware decision flow, resource/session reuse, and mechanical runtime chores
must remain distinct concerns.

### G3. Keep tasks self-contained

Task continuity should normally be carried by decomposition, descriptions, refs,
and artifacts — not by implicit session memory contracts.

### G4. Allow agentic flexibility where rules do not close the case

The architecture must allow a reasoning role (`resolver`) to unblock cases that
are not closed by normal orchestration flow.

### G5. Minimize architecture-level concepts

If a concept is merely an optimization and not a correctness boundary, it should
stay out of the architectural surface unless proven necessary.

### G6. Keep role vocabulary configuration-owned

Plan data may reference role IDs, but ordinary role-profile definitions belong to
orchestration configuration rather than orchestration-core code.

---

## 4. Non-Goals

- Replacing `vectl core` authority
- Introducing continuity abstractions such as `continuity_group`
- Promoting session reuse to a first-class task/planning concept
- Hiding plan-aware control inside a resource registry

---

## 5. Component Model

## 5.1 `control`

### Responsibility

`control` is the plan-aware control component of the orchestration plane.

It owns:

- reading core state together with orchestration-plane state
- determining whether work can continue normally
- determining whether the system is blocked or otherwise unresolved
- deciding when to dispatch work using available resources
- deciding when to invoke `resolver`

### Non-responsibility

It does **not** own:

- plan/lifecycle/claims authority
- mechanical runtime chores
- resource reuse bookkeeping internals
- long-lived open-ended reasoning

### Why it exists

Without `control`, plan-aware decisions would leak into `roster` or `runtime`
and recreate a hidden scheduler/control brain under another name.

### Relation to `vectl_decide`

`vectl_decide` is the existing deterministic-decision reference surface from the
legacy system. The landed `control` implementation (`src/vectl/orchestration/control.py`)
exposes a new deterministic decision surface that subsumes `vectl_decide`'s
responsibilities within the orchestration plane.

The `PlanAwareControl` class in the orchestration package is the authoritative
implementation of the `control` component contract.

---

## 5.2 `roster`

### Responsibility

`roster` is the reusable agent/session resource registry.

It owns:

- registration of reusable agents/sessions
- TTL / reuse-window bookkeeping after task completion
- capability / runner / session metadata
- claim / release of compatible reusable resources
- visibility into reusable resource state

### Non-responsibility

It does **not** own:

- plan semantics
- blocked-state interpretation
- next-step orchestration decisions

### Boundary rule

`roster` knows resources, not plans.

If `roster` begins interpreting plan semantics or deciding whether work is
blocked, the architecture collapses back into a renamed legacy-control blob.

### Note on reuse

Session/agent reuse is allowed as an internal `roster` optimization where
useful. It is **not** a first-class architecture concept and should not be
required for correctness.

---

## 5.3 `runtime`

### Responsibility

`runtime` handles mechanical non-core chores required by agents.

It owns:

- worktree/workspace preparation
- cleanup / merge support
- runner startup / resume / shutdown helpers
- other purely mechanical environment support needed by agents

### Non-responsibility

It does **not** own:

- plan-aware control
- blocker reasoning
- authority mutations outside existing core boundaries

### Why it exists

Mechanical execution concerns have different failure, retry, and audit semantics
than plan-aware control or reasoning. Keeping them separate prevents unnecessary
token use and keeps the system auditable.

---

## 5.4 `resolver`

### Responsibility

`resolver` is the reasoning role used when normal orchestration flow is blocked
or unresolved.

It owns:

- investigating blockers
- using vectl, runtime, and other allowed tools to determine how to unblock
- returning control decisions or actions back into the orchestration plane

### Non-responsibility

It does **not** own:

- permanent system authority
- mechanical startup/dispatch mechanics
- replacing the entire orchestration plane

### Why it exists

Not all blocker cases are closed by static rules. `resolver` is the place where
the system permits broader reasoning without collapsing all orchestration into an
always-on agentic surface.

---

## 6. Interaction Model

## 6.1 Normal Flow
```text
core state + drive session state
  -> control reads core + orchestration-plane state
  -> control determines claimable frontier and dispatchable work batch
  -> control asks roster for compatible reusable resources
  -> runtime prepares execution environments and starts child runs
  -> child runs reconcile/merge/complete through authoritative surfaces
  -> control reevaluates until the drive reaches a terminal state
```

Key properties:

- normal flow does not require `resolver`
- normal flow may dispatch in parallel up to the drive's capacity
- the drive session is the durable loop owner, but `control` remains the
  plan-aware decision authority
## 6.2 Blocked / Unresolved Flow
```text
core state + drive session state
  -> control determines normal flow is not closed
  -> drive enters a barrier and stops new frontier admission
  -> active child runs stabilize to a terminal set
  -> control invokes resolver or planner as required
  -> resolver/planner return bounded machine-readable outcomes
  -> control applies those outcomes using roster/runtime/core/facade surfaces
  -> drive either resumes normal scheduling, blocks for operator action, or halts
```

Key properties:

- `resolver` is a reasoning role inside the orchestration plane, not the whole plane itself
- planner is an authoritative mutation producer inside the orchestration loop, but final mutation still flows through vectl facade
- barrier semantics prevent plan mutation and fresh dispatch from racing each other
## 7. Authority and Ownership Matrix
| Concern | Owner |
|--------|-------|
| Plan graph, lifecycle, claims, authoritative state | `vectl core` |
| Role references on plan-side work items | authoritative plan data |
| Ordinary role-profile definitions | orchestration configuration |
| Plan-aware orchestration flow and scheduler decisions | `control` |
| Drive session state (frontier, barrier, active child runs, aggregate progress) | orchestration composition layer built from the four plane components |
| Reusable agent/session resource registry | `roster` |
| Mechanical worktree/runner/environment chores | `runtime` |
| Complex blocker investigation and unblock reasoning | `resolver` |
| Authoritative plan mutation from planner output | vectl facade over `vectl core` |

## 8. Task Semantics

### 8.1 Self-contained task rule

Tasks should be treated as self-contained.

If task B depends on information from task A, that information should normally
be carried by:

- decomposition,
- task description,
- refs,
- or explicit produced artifacts.

The architecture should not introduce continuity abstractions to compensate for
underspecified tasks.

### 8.2 Banned continuity abstractions

The following are rejected from the target architecture:

- `continuity_group`
- `session_reuse` as a task/planner/core concept

`session_reuse` may exist only as an internal `roster` optimization.

### 8.3 Role authority split

The target architecture freezes the role boundary as:

- plan/task data references `role_id`
- orchestration configuration defines the role profile for that `role_id`
- orchestration core only loads, validates, and consumes those profiles

This prevents plan documents from becoming an accidental second source of truth
for prompt, runner, mutation, or execution policy semantics.

---

## 9. Isolation Semantics

Some tasks may require explicit isolation semantics, for example:

- independent review
- audit
- black-box verification

These are different from continuity/reuse optimizations. They appear to be real
task semantics.

### Architectural position

If explicit isolation is required, that requirement belongs in authoritative task
semantics (likely core/plan-level execution constraints), not in `resolver`
guesswork and not in `roster` heuristics.

The exact target semantic is defined in:

- `docs/ORCHESTRATION-PLANE-ISOLATION-SEMANTICS.md`

---

## 10. Naming

The orchestration-plane component names are fixed for this design round:

- `control`
- `roster`
- `runtime`
- `resolver`

These should be preferred over earlier placeholders such as `servant` and
`pool`, and over overloaded internal names such as an inner `orchestrator`
component inside the orchestration plane.

### 10.1 Removed target terms

Legacy resolver aliases, legacy review vocabulary, and deprecated dispatch-shape
fields are removed from the target architecture. Historical appearances are
non-authoritative; see
`docs/ADR-orchestration-role-profile-config-and-resolver-cleanup.md`.

### 10.2 Default resolver agents

The only default resolver agents are:

- `blocked-case-coordinator`
- `blocked-case-coordinator-tacit`

Additional resolver-like roles require explicit configuration and must not be
treated as built-in defaults.

These two defaults share one resolver contract (`ResolutionCase` ->
`ResolutionReport`) and one policy family (`main_worktree`,
`vectl_facade_only`, no ordinary claim semantics), but they are distinct agent
identities and must remain independently selectable at runtime.

### 10.3 Historical plan non-authority

Historical `plan.yaml` content remains untouched execution history. It does not
define target orchestration-plane terminology or override this architecture.

---

## 11. Trade-offs

### Gains

- clear separation between authority, control, resources, mechanics, and
  reasoning
- fewer architecture-level concepts
- less risk of hiding control logic inside resource/session machinery
- preserves room for agentic flexibility without making it the whole system

### Costs

- introduces an explicit four-part orchestration vocabulary that implementers
  must respect
- requires configuration hygiene so role-profile drift does not move into code

---

## 12. Remaining Open Questions

- whether packaged default configuration should ship these role profiles in a
  checked-in config file or equivalent config resource
- whether non-default resolver specializations will ever justify distinct
  configured role IDs beyond the two frozen defaults

---

## 13. Summary

The target system is:

- `vectl core` as authority
- `orchestration plane` as a separate subsystem over core
- within that plane:
  - `control` for plan-aware orchestration flow
  - `roster` for reusable resources
  - `runtime` for mechanical chores
  - `resolver` for unresolved blocker reasoning

And the supporting authority split is:

- plan references roles
- config defines role profiles
- orchestration core only loads, validates, and consumes

Within config-defined role profiles:

- `role_id` is the orchestration-facing identity
- `agent_id` is the concrete runtime agent/persona identity
- `prompt_family` is the shared contract family, not a command to collapse
  every role in that family to one concrete prompt

This is the full target architecture for the next design phase.
