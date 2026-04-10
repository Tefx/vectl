# Orchestration Plane Orchestration App Routing

**Status:** Proposed  
**Architecture authority:** `docs/ORCHESTRATION-PLANE-ARCHITECTURE.md`, `docs/ADR-orchestration-role-profile-config-and-resolver-cleanup.md`, `docs/ORCHESTRATION-PLANE-LIVE-AUTHORITY-CONTRACT-LOCK.md`  
**Interface authority:** `docs/ORCHESTRATION-PLANE-INTERFACES.md`  
**Related:** `docs/ORCHESTRATION-PLANE-RUNNER-BACKEND.md`, `docs/ORCHESTRATION-PLANE-RUNTIME-WORKTREE-LIFECYCLE.md`, `docs/ORCHESTRATION-PLANE-DISPATCH-AND-PROMPT-POLICY.md`, `docs/ORCHESTRATION-PLANE-RESOLVER-COORDINATION.md`

---

## 1. Purpose

This document defines the orchestration app routing contract: how the
orchestration plane actually sequences normal flow, resolution flow, dispatch,
runtime execution, reconcile, completion, and user/operator notification.

The purpose of this document is to make explicit what no single component owns:

- `control` only decides
- `runtime` only executes and reconciles
- `resolver` only coordinates blocked-case handling

The orchestration app ties these together.

---

## 2. Core decision

The orchestration app is the orchestration plane's:

> **main loop and routing coordinator**

It is responsible for sequencing component interactions.

It is not a replacement for:

- `control`
- `runtime`
- `resolver`
- core authority surfaces

Instead, it orchestrates them in the correct order.

The live-path checkpoints for dispatch and resolution routing are locked by
`docs/ORCHESTRATION-PLANE-LIVE-AUTHORITY-CONTRACT-LOCK.md`.

---

## 3. Ownership boundary

### orchestration app owns

- refreshing snapshots in the correct order
- dispatch sequencing
- invoking approved claim/complete surfaces in the correct place
- turning structured review/gate failures into explicit `ResolutionCase`s
- routing into resolver when normal flow does not close
- routing `operator_required` into explicit user/operator notification

### orchestration app does not own

- plan-aware decision semantics (`control`)
- prompt policy (`PromptRegistry` / dispatch layer)
- execution mechanics (`runtime` / runner backend)
- blocked-case reasoning (`resolver`)
- authority semantics of claim/complete/defer (`vectl core`)

---

## 4. Hard rules

### Rule 1 — claim is part of normal dispatch flow

When normal flow dispatches a step, claim occurs before runtime execution starts.

### Rule 2 — complete requires reconcile closure

`complete` must only occur after runtime reconcile returns:

- `merged`, or
- `noop`

Execution success alone is insufficient.

### Rule 3 — review/gate failure must be explicit

Non-pass structured review/gate output must be normalized into explicit
`ResolutionCase`s.

### Rule 4 — `operator_required` must notify the user/operator

`operator_required` must not be silently treated as ordinary wait.

### Rule 5 — resolver delegation uses the shared dispatch/runtime substrate

Resolver-spawned planner/coder/reviewer work is still executed through the same
dispatch/prompt/runtime substrate, but with `source_kind="resolution_subtask"`.

---

## 5. Normal flow routing

### 5.1 Snapshot refresh

At the start of each orchestration loop, the orchestration app refreshes:

- core snapshot
- roster snapshot
- runtime snapshot

These snapshots are passed to `control.evaluate(...)`.

### 5.2 Dispatch path

If `control.evaluate(...)` returns `dispatch`:

1. orchestration app performs approved `claim` through `core_adapter` or an
   equivalent approved authority surface
2. dispatch coordinator loads authoritative step data
3. dispatch coordinator resolves a config-backed role/profile
4. dispatch coordinator builds `DispatchSpec`
5. `PromptRegistry` renders prompt content
6. runtime prepares execution context
7. runtime starts execution

### 5.3 Wait path

If `control.evaluate(...)` returns `wait`:

1. orchestration app polls/collects runtime execution state
2. if work remains in flight, loop continues waiting
3. if a terminal execution result arrives, orchestration app routes that result
   according to the rules below

### 5.4 Done path

If `control.evaluate(...)` returns `done`:

- the orchestration app may finalize the run and stop

---

## 6. Execution result routing

### 6.1 Successful execution result

If runtime returns terminal execution success:

1. orchestration app immediately invokes `runtime.reconcile(...)` as the next
   routing action after terminal execution success
2. if reconcile returns `merged` or `noop`, approved `complete` may occur
3. if reconcile returns `merge_conflict` or `aborted`, create explicit
   `ResolutionCase`

### 6.2 Failed/stalled/transport execution result

If runtime returns:

- `fail`
- `stall`
- `transport_error`

the orchestration app must route that non-closure explicitly rather than
pretending the step is complete.

Recommended behavior:

- create explicit `ResolutionCase`, or
- remain in wait only if the runtime contract explicitly says the execution is
  still safely in progress

### 6.3 Reconcile result gate for completion

The orchestration app is the layer that enforces the rule:

> execution success does not complete a step unless reconcile closes with
> `merged` or `noop`

---

## 7. Structured review/gate routing

### 7.1 Parse location

Structured review/gate results are parsed in the orchestration app result
normalization path.

They are not parsed by `control`.

### 7.2 Review normalization

If a role with `output_contract="structured_review_result"` returns:

- `pass` -> continue normal flow
- `needs_fix` -> create explicit `ResolutionCase`
- `needs_replan` -> create explicit `ResolutionCase`
- `operator_required` -> create explicit `ResolutionCase`

### 7.3 Parse failure

If structured review parsing fails:

- do not complete the step
- create explicit `ResolutionCase` with `case_source="review_failed"` describing
  the contract violation
- route through resolver rather than guessing from prose

---

## 8. Resolution flow routing

### 8.1 Entering resolve

If control returns `resolve`, the orchestration app must ensure a current
explicit `ResolutionCase` exists and invoke resolver with it.

### 8.2 Resolver outcomes

When resolver returns `ResolutionReport`:

- `unblocked` -> refresh snapshots and return to normal control evaluation
- `waiting` -> continue waiting path
- `operator_required` -> notify user/operator explicitly
- `halt` -> stop orchestration

### 8.3 No silent downgrade of `operator_required`

`operator_required` must not be collapsed into ordinary `wait` without explicit
user/operator notification state.

---

## 9. Resolver subagent routing

Resolver may delegate to specialized subagents such as planner, coder, or
reviewer.

### 9.1 Common path

Resolver-spawned subagent work uses:

- `DispatchSpec(source_kind="resolution_subtask")`
- role/profile lookup
- prompt rendering
- runtime execution in the execution context required by the selected role profile

### 9.2 Planner example

For a `needs_replan` case:

1. resolver decides to delegate to planner
2. dispatch coordinator builds `DispatchSpec` with:
   - `source_kind="resolution_subtask"`
   - planner role profile loaded from orchestration config
   - `main_worktree` execution context
3. planner runs
4. resolver consumes structured planner output
5. approved vectl tool facade applies any authoritative mutation required; the
   planner itself never edits `plan.yaml`

### 9.3 Role authority reminder

The routing layer must preserve this split:

- plan data references role IDs
- orchestration config defines role profiles
- orchestration core only loads, validates, and consumes

---

## 10. User/operator notification routing

### 10.1 Ownership

User/operator notification is owned by the orchestration app / operator-facing
surface, not by `control` or `runtime`.

### 10.2 Trigger conditions

Notification must occur when:

- resolver returns `operator_required`
- policy requires explicit human decision before continuing

### 10.3 Notification contract

At minimum, orchestration app should preserve:

- notification reason
- operator-facing summary
- evidence refs
- current resolution case ID

This creates a stable pause point rather than an implicit hidden wait.

---

## 11. Main worktree vs linked worktree routing

The orchestration app decides execution context based on `RoleProfile`, not by
hardcoding one role as the only main-worktree actor.

### Typical linked-worktree roles

- coder-family normal step execution

### Typical main-worktree roles

- resolver itself
- planner-family roles
- reviewer-family roles
- conflict-resolution helpers

---

## 12. Internal routing helpers

The orchestration app may internally factor routing into helpers such as:

- dispatch coordinator
- result normalizer
- operator notification handler

These are orchestration-app-owned logic layers, not new top-level architecture
components.

### 12.1 Dispatch coordinator output

The dispatch coordinator returns a `DispatchSpec`.

That `DispatchSpec` is then:

1. rendered by `PromptRegistry`
2. converted into the runtime-facing execution input
3. passed to runtime start/execution flow by the orchestration app

### 12.2 Notification handler

The operator notification handler may be modeled as an orchestration-app-owned
internal helper.

Recommended minimal protocol:

```python
class NotificationHandler(Protocol):
    def notify_operator(
        self,
        *,
        case_id: str,
        summary: str,
        evidence_refs: tuple[str, ...],
        operator_message: str | None,
    ) -> None: ...
```

---

## 13. Anti-patterns

The orchestration app routing design must avoid all of the following:

- letting `control` directly own claim/complete sequencing
- letting runtime decide prompt policy or review semantics
- completing a step directly from execution success before reconcile closure
- guessing review results from prose
- silently swallowing `operator_required` into ordinary wait
- spawning resolver work through an entirely separate execution stack

---

## 14. Required implementation surfaces

Likely implementation updates/additions include:

- `src/vectl/orch_app.py`
- dispatch coordination helpers
- result normalization helpers
- operator notification / receipt helpers
- wiring between control, core adapter, runtime, resolver, and prompt policy

---

## 15. Verification guidance

Implementation should prove at minimum:

1. `dispatch` path performs claim before runtime start
2. successful execution does not complete before reconcile closure
3. reconcile `merged|noop` is the only normal path to complete
4. non-pass review output becomes explicit `ResolutionCase`
5. parse failure of structured review output also becomes explicit `ResolutionCase`
6. resolver-spawned planner/coder/reviewer work goes through shared dispatch/runtime substrate
7. `operator_required` leads to explicit user/operator notification state

Suggested test classes:

- orch app routing tests
- execution-success-but-no-complete-before-reconcile tests
- review normalization tests
- parse-failure-to-resolution-case tests
- resolver delegation routing tests
- operator notification path tests

---

## 16. Final recommendation

The orchestration app should be treated as the orchestration plane's routing
coordinator.

It is the layer that correctly sequences:

- claim
- dispatch construction
- prompt rendering
- runtime execution
- reconcile
- complete
- resolve
- operator notification

This keeps the architecture coherent:

- `control` remains thin and deterministic
- `runtime` remains mechanical
- `resolver` remains the blocked-case coordinator
- user/operator escalation remains explicit and auditable

without forcing any single component to absorb the whole system.

Historical `plan.yaml` remains untouched and is not terminology authority for
new orchestration-plane contracts.
