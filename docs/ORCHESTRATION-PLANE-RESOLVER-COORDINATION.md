# Orchestration Plane Resolver Coordination

**Status:** Proposed  
**Architecture authority:** `docs/ORCHESTRATION-PLANE-ARCHITECTURE.md`, `docs/ADR-orchestration-role-profile-config-and-resolver-cleanup.md`  
**Interface authority:** `docs/ORCHESTRATION-PLANE-INTERFACES.md`  
**Related:** `docs/ORCHESTRATION-PLANE-RESOLUTION-CONTRACT.md`, `docs/ORCHESTRATION-PLANE-RUNNER-BACKEND.md`, `docs/ORCHESTRATION-PLANE-RUNTIME-WORKTREE-LIFECYCLE.md`, `docs/ORCHESTRATION-PLANE-DISPATCH-AND-PROMPT-POLICY.md`

---

## 1. Purpose

This document defines the intended resolver model for the orchestration plane.

Resolver is not just a thin adapter and not just a one-off conflict fixer. It is
the orchestration plane's blocked-case coordination role.

This document clarifies:

- why resolver exists
- what enters resolver
- what resolver may do directly
- when resolver should call specialized subagents
- how resolver interacts with runtime, control, and approved vectl mutation surfaces

---

## 2. Core decision

Resolver should be implemented as the orchestration plane's:

> **blocked-case coordinator and repair orchestrator**

The only default resolver agent IDs are `blocked-case-coordinator` and
`blocked-case-coordinator-tacit`.

Resolver is first-class and agentic, but it does not replace the entire
orchestration system.

Resolver exists only after normal flow fails to close.

---

## 3. Why resolver exists

Without resolver, the system is forced into one of two bad outcomes:

1. `control` absorbs complex blocked-case semantics and becomes dirty
2. every non-closed case is immediately escalated to the user/operator

Resolver exists so the system can maintain:

- thin deterministic normal flow
- explicit blocked-case handling
- active repair and replanning when appropriate
- user escalation only when automatic closure is not safe

---

## 4. Ownership boundary

### resolver owns

- blocked/unresolved case coordination
- deciding whether to inspect, repair, delegate, or escalate
- using approved vectl mutation surfaces for exception repair
- returning a bounded `ResolutionReport`

### resolver does not own

- normal flow dispatch
- claiming new work
- runtime worktree/execution/reconcile lifecycle
- core authority semantics
- permanent control of orchestration flow

---

## 5. Hard rules

### Rule 1 — resolver only runs after normal flow does not close

Resolver is not an always-on orchestrator. It activates only when normal flow
cannot close mechanically or policy-wise.

### Rule 2 — resolver always runs in the main worktree

Resolver itself always runs in the main worktree / integration context.

### Rule 3 — resolver does not claim new work

Claim remains part of normal flow. Resolver must not claim new steps.

### Rule 4 — resolver mutation uses approved vectl tool facade

When resolver performs exception repair, it must do so through the approved
vectl tool facade. It must not bypass authoritative surfaces.

### Rule 5 — resolver may call specialized subagents

Resolver may invoke specialized subagents such as planner, coder, or reviewer.

### Rule 6 — resolver must notify the user/operator when it cannot complete safely

`operator_required` must remain explicit. It must not silently collapse into
ordinary `wait`.

---

## 6. Input contract

Resolver consumes explicit resolution cases.

### 6.1 ResolutionCase

Recommended minimum shape:

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

    core_snapshot: CoreSnapshot
    roster_snapshot: RosterSnapshot
    runtime_snapshot: RuntimeSnapshot

    blocked_step_ids: tuple[str, ...] = ()
    artifact_refs: tuple[str, ...] = ()
```

### 6.2 Why `case_source` exists

`case_source` is not a deep taxonomy. It is a source tag so resolver can
distinguish whether the case came from:

- runtime mechanics
- merge/reconcile conflict
- structured review/gate failure
- continuity/recovery logic
- authority ambiguity

That is enough for resolver to choose an action path without inventing hundreds
of blocker subtypes.

---

## 7. Resolver action model

Resolver should reason in terms of actions, not large semantic taxonomies.

### 7.1 inspect

Resolver inspects the case, gathers more information, and returns a result
without mutation.

### 7.2 repair

Resolver performs exception-repair-class mutation through approved vectl tool
surfaces.

### 7.3 delegate

Resolver invokes a specialized subagent when the case requires more focused
planning, implementation, review, or conflict handling.

### 7.4 escalate

Resolver returns explicit `operator_required` / halt behavior when safe
automatic closure is not possible.

---

## 8. Structured review/gate failure handling

Structured review/gate outputs are one of resolver's most important inputs.

### 8.1 Review/gate outputs must be explicit

Roles that produce review/gate decisions must emit structured review results,
not prose-only conclusions.

See:

- `docs/ORCHESTRATION-PLANE-DISPATCH-AND-PROMPT-POLICY.md`

### 8.2 Review fail does not need to become a core blocked-step state

It is enough for the orchestration system to normalize non-pass review/gate
results into an explicit `ResolutionCase`.

### 8.3 Normalization rule

If structured review output is:

- `pass` -> normal flow continues
- `needs_fix` -> resolution case
- `needs_replan` -> resolution case
- `operator_required` -> resolution case

This keeps `control` simple.

---

## 9. Planner as a specialized remediation subagent

Planner should not be selected directly by ordinary control flow merely because a
review failed.

Instead, planner is typically invoked by resolver when blocked-case handling
requires remediation/retest planning.

### 9.1 Why planner belongs under resolver here

When review fails, the system often cannot deterministically choose whether the
right response is:

- a direct fix
- a retry
- deeper replanning
- a repair + retest plan mutation

That uncertainty belongs in blocked-case handling, not in thin normal-flow
control.

### 9.2 Planner output

Planner-family remediation output should also be structured.

Recommended minimum shape:

```yaml
plan_outcome: plan_ready | operator_required
summary: <string>
proposed_steps:
  - step_id: <string>
    name: <string>
    description: <string>
    verification: <string>
retest_steps:
  - step_id: <string>
    name: <string>
```

### 9.3 Planner does not own authority semantics

If planner output must become authoritative plan mutation, that mutation must be
applied through the approved vectl tool facade under resolver/orchestrator
control. Planner never edits `plan.yaml` directly.

---

## 10. Coder/reviewer subagents under resolver

Planner is not the only specialized subagent resolver may invoke.

Resolver may also delegate to:

- coder-family roles for directed repair work
- reviewer-family roles for secondary review or verification

This keeps resolver itself narrow while still making it an effective blocked-case
coordinator.

---

## 11. Merge/reconcile conflict handling

### 11.1 runtime owns detection and preservation

Runtime detects merge/reconcile conflict and preserves evidence.

### 11.2 control owns routing

Control only needs to recognize that normal flow did not close and route the
system to `resolve`.

### 11.3 resolver owns semantic conflict handling

Resolver receives a `ResolutionCase(case_source="merge_conflict", ...)` with
artifact refs and decides whether to:

- self-handle
- delegate to a planner/coder/reviewer helper selected by policy
- escalate to user/operator

---

## 12. Relationship to runtime

Runtime and resolver are adjacent but distinct.

### runtime owns

- worktree lifecycle
- execution lifecycle
- reconcile lifecycle
- conflict detection and evidence preservation

### resolver owns

- blocked-case reasoning
- blocked-case coordination
- exception repair decision making

Resolver must not absorb runtime lifecycle ownership.

---

## 13. Relationship to control

Control remains thin.

Control should not try to infer:

- whether review failure means replanning
- whether merge conflict needs planner vs coder vs operator
- whether continuity ambiguity can be self-repaired

Control only decides that normal flow did not close and the system must enter
`resolve`.

---

## 14. Relationship to dispatch/prompt policy

Resolver may invoke specialized subagents, but it should not create an entirely
separate dispatch architecture.

Resolver should reuse:

- role/profile policy
- prompt policy
- shared runner backend

What differs is the source of work:

- ordinary dispatch derives from steps
- resolver-spawned subagent work derives from resolution cases

---

## 15. Output contract

Resolver returns bounded reports.

### 15.1 ResolutionReport

Recommended minimum shape:

```python
@dataclass(frozen=True)
class ResolutionReport:
    status: Literal[
        "unblocked",
        "waiting",
        "operator_required",
        "halt",
    ]
    summary: str
    evidence_refs: tuple[str, ...] = ()
    operator_message: str | None = None
```

### 15.2 `operator_required` handling

`operator_required` must not be silently normalized into ordinary `wait` without
explicit user/operator notification behavior.

---

## 16. Anti-patterns

The resolver design must avoid all of the following:

- treating resolver as the normal-flow dispatcher
- letting resolver claim new work
- giving resolver direct authority mutation ownership outside approved vectl tool surfaces
- making control infer review semantics from prose
- creating a second independent execution stack for resolver-spawned work
- collapsing planner into resolver itself instead of treating planner as a
  specialized subagent

Historical `plan.yaml` vocabulary and legacy resolver labels are
non-authoritative for this target architecture.

---

## 17. Required implementation surfaces

Likely implementation updates/additions include:

- `src/vectl/orchestration/resolver.py`
- `src/vectl/orchestration/resolver_gateway.py`
- `src/vectl/orchestration/contracts.py`
- `src/vectl/orch_app.py`
- structured review-result normalization path
- planner/coder/reviewer delegation path under resolver handling

---

## 18. Verification guidance

Implementation should prove at minimum:

1. resolver is only entered after normal flow non-closure
2. resolver itself runs in main worktree
3. resolver never claims new work
4. resolver mutation uses approved vectl tool facade only
5. structured review failure becomes explicit `ResolutionCase`
6. resolver can invoke planner as a remediation subagent
7. merge conflict is treated as runtime detection + resolver semantic handling
8. unresolved cases can end in explicit user/operator notification

Suggested test classes:

- resolution case construction tests
- review-fail normalization tests
- resolver delegation tests
- approved tool facade enforcement tests
- operator_required notification path tests

---

## 19. Final recommendation

Resolver should be implemented as the orchestration plane's blocked-case
coordinator:

- thin enough not to absorb normal flow
- strong enough to actively coordinate repair
- able to invoke planner/coder/reviewer subagents when needed
- bound to approved vectl mutation surfaces
- explicit about user/operator escalation when automatic closure fails

This preserves the architecture's intended split:

- deterministic normal flow in `control`
- mechanical lifecycle in `runtime`
- agentic blocked-case coordination in `resolver`

without collapsing the system into one oversized always-on orchestrator.
