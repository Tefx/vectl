# Orchestration Plane Isolation Semantics

> Authoritative semantics for tasks that require explicit execution isolation.

**Status:** Target semantics  
**Architecture authority:** `docs/ORCHESTRATION-PLANE-ARCHITECTURE.md`  
**Interface authority:** `docs/ORCHESTRATION-PLANE-INTERFACES.md`

---

## 1. Purpose

Some tasks require more than ordinary execution. Examples include:

- independent review
- audit
- black-box verification

For these tasks, the orchestration plane must not guess whether a warm session,
prior reasoning context, or a reused workspace is acceptable. That is a task
semantic, not a runtime optimization.

This document resolves that open question.

---

## 2. Decision

Add a single explicit step-level isolation field to authoritative task semantics.

Recommended target schema:

```python
class IsolationMode(str, Enum):
    DEFAULT = "default"
    WORKSPACE = "workspace"
    INDEPENDENT = "independent"


class Step(BaseModel):
    ...
    isolation: IsolationMode = IsolationMode.DEFAULT
```

YAML example:

```yaml
steps:
  - id: review.api
    name: Independent API review
    isolation: independent
```

This is intentionally a **single** authoritative field.

It is preferred over introducing separate architecture-level concepts such as:

- `continuity_group`
- task-level `session_reuse`
- multi-field reuse preference matrices

---

## 3. Semantics of the Field

## 3.1 `default`

No explicit isolation requirement.

Implications:

- `runtime` may use normal workspace behavior
- `roster` may opportunistically reuse a compatible resource if available
- no correctness guarantee of fresh workspace or fresh reasoning context is
  implied

`default` is the ordinary case.

## 3.2 `workspace`

Fresh workspace isolation is required.

Implications:

- `runtime` must provide a fresh isolated workspace/worktree for this step
- `roster` may still reuse a compatible agent/session if that does not violate
  the task semantics

Use this when filesystem/workspace isolation is required, but fresh reasoning
context is not strictly required.

## 3.3 `independent`

Fresh workspace **and** fresh reasoning context are required.

Implications:

- `runtime` must provide a fresh isolated workspace/worktree
- `roster` must not reuse a prior warm agent/session for this step
- `resolver` and `control` must preserve this requirement when arranging any
  follow-on or delegated work for this step

Use this for:

- independent review
- audit
- black-box verification
- any task whose validity depends on not inheriting prior task context

---

## 4. Why This Does Not Reintroduce `session_reuse`

This design does **not** make session reuse a first-class architecture concept.

It makes only one thing authoritative:

> whether the task requires explicit isolation.

The architecture still does **not** expose a positive reuse preference like:

- `reuse_session: true`
- `continuity_group`
- `reuse_window`

Those remain banned because they are optimization concepts, not correctness
boundaries.

This field is different because it expresses a negative correctness constraint:

> **do not reuse when isolation is required**.

---

## 5. Ownership by Component

## 5.1 Core / planner ownership

The field belongs in authoritative task semantics.

Why:

- planners can set it intentionally
- control can consume it deterministically
- runtime and roster can enforce it mechanically
- resolver does not need to guess it

## 5.2 `control`

`control` must read the step's `isolation` value when deciding how work may be
dispatched.

## 5.3 `roster`

`roster` enforces the reuse side of the semantic:

- `default` -> reuse allowed opportunistically
- `workspace` -> reuse allowed, but only with fresh workspace support
- `independent` -> no prior warm resource may satisfy the step

## 5.4 `runtime`

`runtime` enforces the workspace side of the semantic:

- `default` -> ordinary runtime behavior
- `workspace` -> fresh workspace required
- `independent` -> fresh workspace required

## 5.5 `resolver`

`resolver` must not lower authoritative isolation requirements when resolving a
case. If it arranges work for a step, it must preserve the step's `isolation`
constraint.

---

## 6. Defaults and Migration

### Default

Existing steps default to:

```text
isolation = default
```

### Migration implication

No existing plan schema needs immediate bulk rewrite. Steps only need explicit
annotation when they truly require stronger isolation.

---

## 7. Legacy Context

The legacy system already has partial notions related to isolation:

- worktree isolation mechanics
- `COLD_CONTEXT` judgment for gate/freeze dispatch in legacy driver docs
- legacy isolation-focused tests and docs

Those remain valuable reference material, but they do not replace the need for a
single authoritative step-level isolation semantic.

---

## 8. Non-Goals

- introducing continuity grouping
- exposing positive reuse preferences to planners
- defining a large execution-policy matrix
- deciding every future environment/security constraint now

---

## 9. Future Extension Boundary

If future needs emerge (for example read-only execution, stricter sandboxing, or
network policy), this design may later grow into a richer execution-constraints
object.

That is **not** needed now. The architecture should start with the single field
above unless real requirements prove otherwise.

---

## 10. Summary

The target authoritative isolation semantic is:

- a single step-level field: `isolation`
- values:
  - `default`
  - `workspace`
  - `independent`

This is sufficient to capture the real correctness boundary without reviving
unnecessary continuity/reuse abstractions.
