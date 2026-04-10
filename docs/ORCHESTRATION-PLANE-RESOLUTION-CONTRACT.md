# Orchestration Plane Resolution Contract

> Contract for how `control` invokes `resolver`, and how `resolver` returns the
> system to normal orchestration flow.

**Status:** Target contract  
**Architecture authority:** `docs/ORCHESTRATION-PLANE-ARCHITECTURE.md`, `docs/ADR-orchestration-role-profile-config-and-resolver-cleanup.md`  
**Interface authority:** `docs/ORCHESTRATION-PLANE-INTERFACES.md`

---

## 1. Purpose

This document resolves a central design gap in the orchestration plane:

> When normal orchestration flow is not closed, how does `control` invoke
> `resolver`, and what exactly must come back?

The answer in this architecture is:

- `control` decides **when** resolution is needed,
- `resolver` decides **how** to investigate and unblock,
- `resolver` may use approved tools and official authority surfaces,
- and `control` regains control by re-reading state after resolution.

The only default resolver agents are `blocked-case-coordinator` and
`blocked-case-coordinator-tacit`.

This is intentionally **not** a pure directive-only design. The `resolver`
exists to reason and act where rules do not close the case. But it still does
not become a permanent authority.

---

## 2. Core Principle

`control` owns orchestration flow. `resolver` owns blocked-case reasoning.

The contract between them is:

```text
control detects unresolved state
  -> control creates ResolutionCase
  -> resolver investigates/acts via allowed tools
  -> resolver returns ResolutionReport
  -> control refreshes core/roster/runtime state
  -> control resumes normal evaluate() flow
```

The system should continue from refreshed state, not from a hypothetical plan of
what the resolver "intended".

---

## 3. Invocation Contract

## 3.1 When `control` must invoke `resolver`

`control` invokes `resolver` when normal orchestration flow is not closed.

Examples:

- no normal dispatch path is valid, but the system is not done
- blocked or ambiguous state needs reasoning
- a blocker requires broader investigation than current deterministic logic
- current typed/local rules cannot determine a safe next step

`control` should **not** invoke `resolver` for ordinary dispatch/wait/done flow.

## 3.2 What `control` passes in

`control` invokes `resolver` with a `ResolutionCase`.

Recommended shape:

```python
@dataclass(frozen=True)
class ResolutionCase:
    case_id: str
    case_source: Literal[
        "runtime_failure",
        "merge_conflict",
        "review_failed",
        "continuity_block",
        "authority_ambiguity",
        "unknown",
    ]
    reason: str
    summary: str | None
    core: CoreSnapshot
    roster: RosterSnapshot
    runtime: RuntimeSnapshot
    blocked_step_ids: tuple[str, ...] = ()
    artifact_refs: tuple[str, ...] = ()
```

Interpretation:

- `case_id` identifies one explicit resolution case
- `case_source` identifies where the case came from without creating a large blocker taxonomy
- `reason` is the minimal machine-readable explanation of why normal flow did
  not close
- `summary` is an optional bounded human-readable summary
- `core`, `roster`, and `runtime` provide the current state basis for reasoning
- `blocked_step_ids` and `artifact_refs` provide optional coordination context and preserved evidence

The case should be a snapshot of current known facts, not a speculative action
plan.

---

## 4. Resolver Powers and Limits

## 4.0 Authorization model

Resolver tool use follows an explicit allowlist model.

Recommended shape:

- a small static allowlist of allowed tool families/surfaces owned by
  orchestration-plane configuration
- optional narrowing by deployment/configuration
- deny-by-default for anything not explicitly allowed

This document does **not** freeze the exact tool list. It freezes the model:

> `resolver` may use only explicitly allowed surfaces.

That keeps the role agentic without making its authority boundary implicit.

## 4.1 Allowed powers

`resolver` may:

- inspect allowed read surfaces
- use approved vectl/core tool surfaces
- use approved orchestration-plane surfaces
- perform official authority mutations **through existing authoritative tools or
  interfaces**, when those are necessary to unblock the system

This means `resolver` is allowed to be effective, not merely advisory.

Typical allowlist categories may include:

- official vectl/core operations
- orchestration-plane runtime/roster inspection or execution surfaces
- planner/coder/reviewer/agent dispatch surfaces exposed by the orchestration plane

The exact list remains implementation policy, but the allowlist model itself is
architecturally fixed.

## 4.2 Forbidden behaviors

`resolver` must not:

- become the long-lived orchestration controller
- silently redefine authority boundaries
- mutate plan files or internal state by bypassing official tools/interfaces
- smuggle hidden orchestration logic into `roster` or `runtime`

---

## 5. Return Contract

## 5.1 `ResolutionReport`

`resolver` returns a bounded report, not a permanent authority transfer.

Recommended shape:

```python
@dataclass(frozen=True)
class ResolutionReport:
    status: Literal["unblocked", "waiting", "operator_required", "halt"]
    summary: str
    evidence_refs: tuple[str, ...] = ()
    operator_message: str | None = None
```

### Status meanings

- **`unblocked`**: resolver believes the system can return to normal
  orchestration flow; `control` must refresh state and reevaluate
- **`waiting`**: no immediate dispatch is appropriate, but the system is not a
  terminal failure; `control` should refresh and continue its normal wait path
- **`operator_required`**: automated resolution should stop until operator input
  is provided
- **`halt`**: resolver concluded that orchestration should stop

## 5.2 Why report, not directive-only

This architecture does **not** require the resolver to return a complete action
program for `control` to replay.

Why:

- the resolver may already have acted through official surfaces while unblocking
- forcing everything into an invented directive language would overconstrain the
  agentic role too early
- the correct post-resolution behavior is to refresh state and resume normal
  orchestration from reality

---

## 6. Control Obligations After Resolution

After receiving a `ResolutionReport`, `control` must:

1. re-read the relevant authoritative and orchestration-plane state
2. not trust stale pre-resolution assumptions
3. resume normal `evaluate()` logic from refreshed state

This rule is mandatory.

If the resolver mutated state via official surfaces, continuing from stale state
would produce incorrect orchestration behavior.

---

## 7. Relationship to Deterministic Local Surfaces

This contract does **not** require a rigid global catalog of which problems are
"closed by deterministic local rules" versus "resolver-handled".

The rule is simpler:

- if current deterministic / typed local logic closes the case, use it
- if it does not, `control` may invoke `resolver`

This avoids overdesigning a frozen semantic routing taxonomy too early.

---

## 8. Machine-Readable Requirement

The resolver's returned `ResolutionReport` must be machine-readable if it is
automated.

That does **not** mean the internal reasoning must be rigidly encoded. It means
the final surface back to `control` must be parseable and bounded.

---

## 9. Interaction with `runtime` and `roster`

`resolver` may use `runtime` and `roster` through allowed surfaces while
resolving, but must not absorb their ownership.

- `roster` still owns reusable resource bookkeeping
- `runtime` still owns mechanical execution chores
- `control` still owns orchestration flow

---

## 10. Error Contract

- If `resolver` cannot close the case, it must return `operator_required` or
  `halt`, not false certainty.
- If tooling fails during resolution, the report must surface that failure in
  `summary` / `evidence_refs`.
- If resolution requires plan mutation, it must occur through the approved vectl
  facade; direct `plan.yaml` editing is forbidden.

---

## 11. Historical terminology rule

Historical `plan.yaml` contents and earlier doc vocabulary remain legacy context
only. They are not authoritative for target resolver naming or contract terms.

---

## 12. Consequences

### Gains

- keeps `resolver` agentic enough to be useful
- avoids inventing a premature heavy directive language
- keeps `control` as the orchestration owner
- allows state-changing unblock actions while still preserving official
  authority boundaries

### Costs

- requires post-resolution resnapshot/re-evaluation discipline
- requires machine-readable reporting even when internal reasoning is open-ended

---

## 13. Summary

The `control ↔ resolver` contract is:

- `control` decides when the system is unresolved
- `resolver` investigates and may act through official surfaces
- `resolver` returns a bounded `ResolutionReport`
- `control` refreshes state and resumes normal orchestration flow

That is the target contract for blocker resolution in the orchestration plane.
