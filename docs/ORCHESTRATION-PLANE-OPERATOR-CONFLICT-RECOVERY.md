# Orchestration Plane Operator, Conflict, and Recovery Policy

**Status:** Proposed  
**Architecture authority:** `docs/ORCHESTRATION-PLANE-ARCHITECTURE.md`  
**Interface authority:** `docs/ORCHESTRATION-PLANE-INTERFACES.md`  
**Related:** `docs/ORCHESTRATION-PLANE-RUNTIME-WORKTREE-LIFECYCLE.md`, `docs/ORCHESTRATION-PLANE-RESOLVER-COORDINATION.md`, `docs/ORCHESTRATION-PLANE-ORCH-APP-ROUTING.md`, `docs/ORCHESTRATION-PLANE-RUNNER-BACKEND.md`, `docs/ADR-worktree-support.md`

---

## 1. Purpose

This document closes the remaining top-level orchestration-plane policy gaps in
three tightly related areas:

1. explicit user/operator notification and receipt handling
2. merge/reconcile conflict policy
3. recovery/continuity alignment with the new runtime model

These topics are grouped together because unresolved runtime conflict and
blocked-case escalation are not complete without durable recovery and explicit
human-facing routing.

---

## 2. Core decision

The orchestration plane must treat:

- unresolved runtime conflict,
- unresolved resolver outcomes, and
- user/operator-required situations

as **first-class durable orchestration states**, not as transient log messages or
implicit wait conditions.

---

## 3. Hard rules

### Rule 1 — `operator_required` is a durable orchestration state

It must not be downgraded into ordinary `wait`.

### Rule 2 — unresolved merge conflict is a durable runtime state

It must preserve evidence and remain available for resolver/operator handling.

### Rule 3 — recovery must reconstruct worktree, execution, and reconcile state

Recovery cannot be limited to plan-only or session-only continuity.

### Rule 4 — protected paths must never be silently integrated

Protected-path behavior must be explicit, auditable, and deterministic.

### Rule 5 — a crashed or restarted orchestration app must be able to re-enter
the correct phase of runtime/reconcile/operator handling from durable metadata

---

## 4. Operator notification contract

### 4.1 Why this is required

The architecture already requires that resolver failure to safely close a case
must notify the user/operator. That requirement is incomplete unless the system
defines:

- what gets recorded
- what gets surfaced
- how the system remains paused
- how a later operator action can resume or terminate the run

### 4.2 NotificationRecord

Recommended minimum shape:

```python
@dataclass(frozen=True)
class NotificationRecord:
    notification_id: str
    case_id: str
    run_id: str
    kind: Literal["operator_required", "halt_notice"]
    status: Literal["pending", "acknowledged", "resolved", "dismissed"]
    summary: str
    operator_message: str | None
    evidence_refs: tuple[str, ...] = ()
    created_at: float = 0.0
    updated_at: float = 0.0
```

### 4.3 Receipt / acknowledgment

If the operator-facing surface supports acknowledgment, the orchestration plane
should preserve that acknowledgment as durable metadata.

Minimum expectation:

- `pending` means notification exists and still requires action
- `acknowledged` means a human/operator has seen the case
- `resolved` means the operator path has supplied a valid resolution or unblock
- `dismissed` means the operator explicitly terminated/closed the notification path

### 4.4 Routing behavior

When resolver returns `operator_required`:

1. orch app creates a `NotificationRecord`
2. orch app enters a paused operator-wait routing state
3. orch app does not continue ordinary dispatch
4. orch app may continue read-only status/reporting surfaces

### 4.5 Resume behavior

The system may leave operator-wait only after an explicit operator action or an
explicit orchestration restart path that re-evaluates the persisted case and
notification state.

---

## 5. Conflict policy

### 5.1 Runtime owns detection and preservation

Runtime owns:

- reconcile attempt
- merge/conflict detection
- conflict artifact preservation
- reconcile status persistence

Resolver owns semantic handling after conflict is explicit.

### 5.2 Reconcile outcomes

The runtime reconcile contract should treat these as the only normal outcomes:

- `merged`
- `noop`
- `merge_conflict`
- `aborted`

`aborted` means runtime could not safely continue reconcile because of a
mechanical/runtime-side failure such as failed integrity preflight or an unsafe
integration-context condition.

### 5.3 Default reconcile strategy

The default reconcile strategy should be explicit and singular.

Recommended initial policy:

- runtime uses one standard git reconcile mode per deployment/configuration
- the strategy must not be guessed ad hoc per step
- if strategy choice is still open, it must be pinned in runtime configuration before implementation begins

This document does **not** freeze the exact git strategy command, but it does
require that the strategy be:

- explicit
- deterministic
- auditable

### 5.4 Protected paths

Initial protected-path set:

- `plan.yaml`

Policy:

- protected-path changes must not be silently integrated as normal step output
- runtime must either restore protected paths before reconcile while preserving
  evidence, or stop and surface the issue explicitly

### 5.5 Conflict artifacts

Runtime must preserve enough conflict context for resolver and operator use.

Minimum artifact set:

- conflicted file list
- reconcile summary
- worktree binding metadata
- target ref / target head at prepare time
- artifact refs to conflict evidence

### 5.6 Resolver interaction

Once conflict is explicit:

1. orch app creates/refreshes `ResolutionCase(case_source="merge_conflict")`
2. control routes to `resolve`
3. resolver investigates conflict evidence from the main worktree
4. resolver may self-handle, delegate, or escalate

---

## 6. Recovery and continuity alignment

### 6.1 Why old continuity assumptions are insufficient

With the new runtime model, continuity must cover more than:

- plan state
- session IDs

It must now also cover:

- worktree bindings
- execution handles
- reconcile state
- conflict state
- operator notification state

### 6.2 Durable runtime metadata

The orchestration plane must durably preserve, at minimum:

#### Worktree metadata
- workspace_id
- step_id
- worktree_path
- scratch_branch
- target_ref
- target_head_at_prepare

#### Execution metadata
- execution_id
- step_id
- workspace_id
- runner
- runner handle / run_id
- session_id if present
- status
- started_at / last_update_at
- artifact_refs

#### Reconcile metadata
- execution_id
- workspace_id
- reconcile status
- conflict_files
- artifact_refs

#### Operator metadata
- notification records
- linked case IDs
- operator-facing summaries/messages

Note: the enriched runtime/reconcile state required here assumes the richer
runtime snapshot and reconcile model defined in
`docs/ORCHESTRATION-PLANE-RUNTIME-WORKTREE-LIFECYCLE.md`. Current code is below
that target and must be brought up to it during implementation.

### 6.3 Recovery phases

After restart, orch app should recover in this order:

1. load authoritative core state
2. load durable orchestration metadata
3. reconstruct runtime snapshot from durable worktree/execution/reconcile state
4. reconstruct outstanding resolution cases
5. reconstruct outstanding operator notifications
6. resume normal evaluation only after these states are coherent

### 6.4 Recovery-safe invariants

The system must preserve these invariants across restart:

- active execution is not forgotten
- unresolved reconcile conflict is not lost
- operator_required state is not silently cleared
- complete is not replayed for a step whose reconcile was not `merged|noop`
- linked worktree evidence is not destroyed before recovery has classified it

Preserved evidence may remain in place or be moved to a durable quarantine
location, but it must not be silently discarded.

---

## 7. Operator, conflict, and recovery interaction

These three concerns interact tightly.

### Example path

1. runtime detects `merge_conflict`
2. orch app opens/refreshes `ResolutionCase`
3. control routes to resolve
4. resolver cannot safely close and returns `operator_required`
5. orch app emits `NotificationRecord`
6. process crashes
7. restart loads runtime conflict metadata + pending notification
8. orch app resumes in operator-wait state rather than returning to normal dispatch

9. once operator action is recorded and the case is explicitly resolved or
   unblocked, orch app may re-enter normal control evaluation

This is the minimum standard of continuity required by the new model.

---

## 8. Required implementation surfaces

Likely implementation updates/additions include:

- operator notification persistence and routing helpers
- durable runtime/reconcile metadata writers
- durable metadata readers during run/resume/recover
- run-store / continuity-artifact integration changes
- explicit paused/operator-wait run state in orch app

Likely code surfaces:

- `src/vectl/orch_app.py`
- `src/vectl/orchestration/runtime.py`
- `src/vectl/orchestration/recovery.py`
- `src/vectl/orchestration/run_store.py`
- `src/vectl/orchestration/continuity_artifacts.py`
- operator-facing interfaces/surfaces

---

## 9. Verification guidance

Implementation should prove at minimum:

1. `operator_required` creates durable notification state
2. unresolved conflict persists across process restart
3. restart reconstructs enough runtime state to prevent unsafe dispatch
4. protected-path conflict is not silently integrated
5. complete is never replayed before reconcile closure
6. pending operator notification survives restart
7. resolver-driven escalation remains explicit and auditable

Suggested test classes:

- operator notification persistence tests
- merge-conflict persistence tests
- recovery reconstruction tests
- protected-path policy tests
- restart-while-paused tests
- restart-after-conflict tests

---

## 10. Final recommendation

The orchestration plane should complete its design with one unified policy:

- unresolved runtime conflict is durable
- operator notification is durable
- recovery reconstructs both runtime and human-facing pause state

This preserves the guarantees already established elsewhere:

- runtime owns mechanical lifecycle and conflict detection
- resolver owns blocked-case coordination
- orch app owns routing and user/operator interaction

Without this layer, the rest of the architecture remains incomplete and unsafe
under restart, conflict, or explicit human escalation.
