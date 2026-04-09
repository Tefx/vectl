# Orchestration Plane Runtime Worktree Lifecycle

**Status:** Proposed  
**Architecture authority:** `docs/ORCHESTRATION-PLANE-ARCHITECTURE.md`  
**Interface authority:** `docs/ORCHESTRATION-PLANE-INTERFACES.md`  
**Related:** `docs/ORCHESTRATION-PLANE-RUNNER-BACKEND.md`, `docs/ORCHESTRATION-PLANE-ISOLATION-SEMANTICS.md`, `docs/ORCHESTRATION-PLANE-RESOLUTION-CONTRACT.md`, `docs/ADR-worktree-support.md`

---

## 1. Purpose

This document defines the missing runtime lifecycle for the orchestration plane.

The current runtime implementation is only a mechanical shell around workspace
bookkeeping. The target runtime must instead own the full lifecycle of:

- standard `git worktree` isolation
- agent execution tracking
- reconcile/merge back into the integration context
- conflict preservation and escalation when reconcile cannot close mechanically

---

## 2. Core decision

`runtime` is the orchestration plane's:

> **worktree + execution + reconcile lifecycle owner**

It is not merely a directory-preparation helper.

It owns the lifecycle of:

1. isolated execution environment creation via standard `git worktree`
2. execution tracking for agent work running inside that worktree
3. result reconciliation back into the authoritative integration context
4. conflict detection, preservation, and escalation to resolver when mechanical
   closure fails

### 2.1 Implementation gap from current code

Current code status is materially below the target described here.

Notable current/runtime gaps include:

- ordinary directory creation instead of standard `git worktree`
- no durable worktree binding metadata beyond path identity
- no true execution lifecycle tracking beyond execution ID allocation
- no reconcile lifecycle
- no conflict state exposed through runtime snapshot

This document defines the target implementation shape rather than describing the
current code as already conformant.

---

## 3. Scope

### In scope

- creating and removing standard git worktrees
- scratch-branch lifecycle for isolated execution
- tracking agent execution inside a worktree
- collecting execution results
- reconciling worktree output back to the target branch
- detecting merge conflicts and preserving conflict artifacts
- enforcing cleanup rules
- exposing runtime state to `control` and `resolver`

### Out of scope

- plan-aware dispatch decisions
- blocked-case reasoning policy
- semantic interpretation of success/failure beyond runtime facts
- direct core authority ownership
- prompt design for agents

---

## 4. Ownership boundary

### runtime owns

- `git worktree` lifecycle
- execution lifecycle inside the worktree
- reconcile lifecycle back to target branch
- worktree/execution/reconcile metadata
- mechanical conflict detection
- cleanup and preservation rules

### runtime does not own

- deciding whether work should dispatch
- deciding whether blocked cases should escalate
- claim/complete semantics
- direct core mutation ownership

---

## 5. Hard rules

### Rule 1 — execution isolation uses standard git worktree

Runtime must use standard `git worktree` flows for isolated execution.

It must not substitute ad hoc directory creation for real worktree isolation.

### Rule 2 — resolver always runs on the main worktree

Resolver operates in the main worktree / integration context, not in the step's
linked execution worktree.

### Rule 3 — claim belongs to normal flow only

`claim` is a normal-flow action. Resolver must not claim new work.

### Rule 4 — complete requires successful reconcile

`complete` must only occur after reconcile returns:

- `merged`, or
- `noop`

Execution success by itself is not sufficient to complete a step.

### Rule 5 — resolver mutation uses approved vectl tool facade

When resolver must perform core mutations for exception repair, it must do so
through the approved vectl tool facade.

Resolver does not own claim/complete semantics and must not bypass the approved
authority surfaces.

### Rule 6 — unresolved runtime problems must surface to the user

If resolver cannot close the problem, the system must surface an explicit user /
operator notification path. It must not silently collapse into ordinary `wait`.

---

## 6. Lifecycle phases

Runtime owns three distinct phases.

### 6.1 Prepare

Prepare creates the isolated execution context.

Actions:

1. resolve target branch/ref
2. record target head commit at prepare time
3. create a scratch branch for the step
4. create a linked worktree using standard git worktree commands
5. persist worktree binding metadata

Output:

- `WorktreeBinding`

### 6.2 Execute

Execute runs agent work in the prepared worktree.

Actions:

1. launch or resume execution via the shared runner backend
2. track execution handle, session, timestamps, and artifacts
3. poll until terminal execution result or runtime-visible stall/error

Output:

- `ExecutionResult | None`

### 6.3 Reconcile

Reconcile integrates worktree results back to the target branch.

Actions:

1. validate worktree integrity before merge
2. validate integration context before merge
3. apply protected-path rules
4. perform standard git reconcile/merge flow
5. emit one of:
   - `merged`
   - `noop`
   - `merge_conflict`
   - `aborted`

Output:

- `ReconcileResult`

### 6.4 Lifecycle transition model

Recommended lifecycle transitions:

| From | Event | To | Owner |
|---|---|---|---|
| `prepared` | execution launch succeeds | `executing` | runtime |
| `executing` | terminal execution result | `pending_reconcile` | runtime |
| `pending_reconcile` | reconcile starts | `reconciling` | runtime |
| `reconciling` | clean integrate | `merged` | runtime |
| `reconciling` | no effective diff | `noop` | runtime |
| `reconciling` | merge conflict | `merge_conflict` | runtime |
| `reconciling` | mechanical reconcile failure | `aborted` | runtime |
| `merged` or `noop` | approved complete | terminal step completion | normal flow via authority surface |
| `merge_conflict` or `aborted` | resolve path entered | blocked/unresolved handling | control -> resolver |

---

## 7. Worktree model

### 7.1 Every execution runs in a worktree

Runtime should treat the worktree as the standard execution environment.

The runtime should not maintain parallel semantics where some executions use a
 true git worktree and others use a plain directory. That dual model increases
state ambiguity and complicates recovery.

### 7.1.1 Isolation-mode effect on runtime behavior

Runtime behavior by isolation mode:

| IsolationMode | Worktree behavior | Session reuse implications |
|---|---|---|
| `default` | standard worktree execution | runtime does not decide reuse; roster/control semantics still apply |
| `workspace` | fresh worktree required | session reuse may still be allowed if it does not violate step semantics |
| `independent` | fresh worktree required | runtime preserves freshness; resolver/control/roster must not lower independent semantics |

### 7.2 Worktree binding metadata

Recommended minimum binding shape:

```python
@dataclass(frozen=True)
class WorktreeBinding:
    workspace_id: str
    step_id: str
    worktree_path: Path
    scratch_branch: str
    target_ref: str
    target_head_at_prepare: str
    isolation: IsolationMode
```

### 7.3 Integration context

The target integration context is the main worktree / authoritative branch
context.

Linked worktrees provide isolated execution space, not an alternative authority
surface.

This follows `docs/ADR-worktree-support.md`:

- linked worktrees isolate code
- plan/state authority remains tied to the main worktree / canonical plan path

---

## 8. Execution tracking model

Runtime must track agent execution as first-class lifecycle state.

Recommended minimum execution shape:

```python
@dataclass(frozen=True)
class AgentExecutionState:
    execution_id: str
    step_id: str
    workspace_id: str
    runner: str
    runner_handle: RunnerHandle
    session_id: str | None
    status: Literal[
        "starting",
        "running",
        "stall",
        "success",
        "fail",
        "transport_error",
        "cancelled",
    ]
    started_at: float
    last_update_at: float
    artifact_refs: tuple[str, ...]
```

### 8.1 Why runtime must own execution tracking

Runtime is the lifecycle owner of mechanical execution facts.

These facts must not be scattered across:

- `control`
- `resolver`
- ad hoc run-store-only metadata

---

## 9. Reconcile model

Reconcile is distinct from execution completion.

### 9.1 Execution success is not completion

If an agent finishes successfully inside the worktree but the worktree result has
not been integrated back to the target branch, the step is not complete.

The lifecycle order is:

1. prepare
2. execute
3. collect terminal execution result
4. reconcile
5. if reconcile is `merged` or `noop`, then normal flow may complete the step

### 9.2 Reconcile result model

Recommended minimum shape:

```python
@dataclass(frozen=True)
class ReconcileResult:
    execution_id: str
    workspace_id: str
    status: Literal["merged", "noop", "merge_conflict", "aborted"]
    summary: str
    conflict_files: tuple[str, ...] = ()
    artifact_refs: tuple[str, ...] = ()
```

### 9.3 Reconcile must be serialized

Concurrent execution is acceptable.

Concurrent reconcile into the same target branch is not.

Runtime must therefore own an integration/reconcile lock so that merge-back to a
single target branch occurs serially.

---

## 10. Protected-path rules

Runtime must not blindly integrate every changed path from a worktree.

### Initial protected path

- `plan.yaml`

Rationale:

- per `ADR-worktree-support`, plan authority remains tied to the main worktree
- agent worktrees are not allowed to redefine authoritative plan structure

### Rule

Changes to protected paths must not be silently merged as step output.

The runtime must either:

1. restore protected paths to target state before reconcile while leaving an
   artifact/evidence trail, or
2. stop and surface the problem as unresolved if safe restoration cannot be done
   mechanically

Protected-path handling is a runtime concern because it is part of safe
reconcile mechanics.

---

## 11. Integrity checks

Runtime must validate both the worktree and the integration context.

### 11.1 Worktree integrity preflight

Before reconcile, runtime must check at minimum:

- worktree path still exists
- scratch branch still exists
- HEAD is not in an unexpected detached/diverged state
- git metadata is intact enough to continue

If these checks fail, runtime must not guess. It must produce a runtime-visible
failure that can escalate to resolver/operator handling.

### 11.2 Integration-context preflight

Before reconcile, runtime must check at minimum that the main integration
context is not already mid-operation in a way that makes reconcile unsafe,
including:

- active merge state
- active rebase state
- active cherry-pick state
- conflicting local mutation that would invalidate a clean integrate step

If the integration context is not safe, runtime must not attempt reconcile.

---

## 12. Conflict handling

### 12.1 runtime is responsible for detection, not semantic resolution

Runtime detects conflict and preserves evidence.

Runtime does not own semantic blocker reasoning.

### 12.2 runtime output when reconcile cannot close mechanically

When reconcile cannot close mechanically, runtime must emit:

- `status = merge_conflict`
- conflict file list
- artifact refs sufficient for resolver investigation

### 12.3 control behavior

`control` should not grow a large merge-conflict taxonomy.

It only needs to recognize that normal flow did not close and route to
`resolve`.

### 12.4 resolver behavior

Resolver operates in the main worktree and uses the approved vectl tool facade
for exception repair where needed.

Resolver may attempt to close the conflict, or return explicit
operator/user-required output when it cannot safely do so.

### 12.5 Resolver access to conflict evidence

Resolver should not guess conflict state from ad hoc filesystem discovery.

Runtime must preserve conflict evidence and expose it through controlled
artifacts/surfaces, including at minimum:

- conflicted file list
- reconcile summary
- artifact refs for conflict evidence
- worktree binding metadata sufficient to understand source execution context

Resolver consumes this preserved evidence from the main-worktree context and, if
needed, performs exception repair through the approved vectl tool facade.

---

## 13. Cleanup rules

Cleanup policy must not override preservation requirements.

Runtime must not delete a worktree simply because cleanup policy says
`on-success` or `always` when any of the following remain true:

- execution is still active
- reconcile is unresolved
- merge conflict is unresolved
- evidence/artifacts required for resolver or operator handling have not been preserved

Cleanup is therefore conditional on lifecycle state, not just config intent.

---

## 14. Runtime snapshot contract

The current `RuntimeSnapshot` is too thin.

The refreshed runtime snapshot should expose enough state for `control` and
`resolver` to understand whether runtime is:

- actively executing
- stalled
- actively reconciling
- blocked on merge conflict

Recommended minimum shape:

```python
@dataclass(frozen=True)
class RuntimeSnapshot:
    active_workspaces: tuple[str, ...]
    active_executions: tuple[str, ...]
    stalled_executions: tuple[str, ...]
    pending_reconciles: tuple[str, ...]
    active_reconciles: tuple[str, ...]
    conflicted_reconciles: tuple[str, ...]
```

This keeps `control` simple while still exposing whether normal flow is blocked
by runtime facts.

---

## 15. Relationship to normal flow and resolver

### normal flow

Normal flow remains:

1. `control` decides dispatch
2. approved claim occurs through authority surface
3. runtime prepares worktree
4. runtime starts execution
5. runtime collects terminal result
6. runtime reconciles
7. if reconcile is `merged` or `noop`, approved complete may occur

This means `complete` is never emitted directly from execution success alone.

### abnormal flow

Abnormal flow remains:

1. runtime detects non-closure (execution failure, reconcile failure, conflict)
2. `control` routes to `resolve`
3. resolver acts in the main worktree using approved vectl tool facade for
   exception repair where needed
4. if resolver cannot finish safely, the system must notify the user/operator

### 15.1 Sequence responsibility summary

The intended responsibility chain is:

1. normal flow performs `claim`
2. runtime performs prepare/execute/reconcile
3. only after reconcile returns `merged` or `noop` may normal flow perform `complete`
4. resolver never claims new work
5. resolver only performs exception-repair-class mutation through approved vectl surfaces

---

## 16. Interaction with runner backend

This document assumes the shared runner backend defined in:

- `docs/ORCHESTRATION-PLANE-RUNNER-BACKEND.md`

Boundary:

- runner backend owns launch/resume/poll/cancel mechanics
- runtime owns lifecycle and state around those mechanics

Runtime therefore depends on the runner backend but is not replaced by it.

---

## 17. Required implementation surfaces

Required code updates will likely include:

- `src/vectl/orchestration/runtime.py`
- `src/vectl/orchestration/contracts.py`
- `src/vectl/orchestration/interfaces.py`
- `src/vectl/orchestration/control.py` (only for runtime snapshot consumption, not new merge taxonomy)
- `src/vectl/orchestration/resolver.py` (only for conflict-handling integration)
- `src/vectl/orch_app.py`
- run/continuity artifact writers where durable lifecycle metadata is persisted

---

## 18. Verification guidance

Implementation should prove at minimum:

1. runtime creates real git worktrees rather than plain directories
2. runtime tracks agent execution lifecycle explicitly
3. execution success does not complete a step before reconcile
4. reconcile is serialized per target branch
5. protected paths are not silently integrated
6. merge conflict is preserved as runtime state with artifacts
7. resolver is invoked from unresolved reconcile/conflict paths rather than normal flow
8. cleanup does not destroy unresolved evidence

Suggested test classes:

- worktree lifecycle tests
- execution lifecycle tests with fake runner backend
- reconcile success/noop tests
- merge-conflict preservation tests
- cleanup-preservation tests
- control/runtime integration tests for resolve routing

---

## 19. Final recommendation

The orchestration-plane runtime should be implemented as a unified lifecycle
owner for:

- `git worktree` isolation
- agent execution tracking
- reconcile/merge back to the integration context

This is the only design that keeps the architecture coherent while preserving
the agreed boundaries:

- claim remains normal flow
- complete only occurs after reconcile closure
- resolver remains first-class for unresolved blocker handling
- resolver acts in the main worktree through approved vectl tool surfaces

This closes the runtime gap without collapsing all orchestration into one large
always-on agent controller.
