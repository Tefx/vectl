# Orchestration Plane Interfaces

> Shared boundary types and component contracts for the target orchestration
> plane.

**Status:** Interface authority  
**Architecture authority:** `docs/ORCHESTRATION-PLANE-ARCHITECTURE.md`  
**Related docs:** `docs/ORCHESTRATION-PLANE-IMPLEMENTATION-DESIGN.md`, `docs/ORCHESTRATION-PLANE-RESOLUTION-CONTRACT.md`, `docs/RFC-opencode-orchestration-runner.md`, `docs/RFC-orch-drive.md`

---

## 1. Anchor

This document defines the stable orchestration-plane boundary types and
component interfaces.

If another orchestration document implies a contradictory type or ownership
boundary, this document wins unless an RFC explicitly supersedes it.

This document defines:

- shared types used across orchestration components
- component protocol boundaries
- interaction boundaries and ownership rules

It does **not** define CLI UX, storage implementation details, or runner-specific
behavior beyond the required shared contracts.

---

## 2. Shared Boundary Rules

### Rule A — Core owns authority

`vectl core` remains the owner of plan graph, lifecycle, claims, and authoritative
step/phase state.

### Rule B — Control is plan-aware; roster is not

Only `control` may decide what work should happen next for a plan. `roster` may
answer resource questions, but it must not reinterpret plan state.

### Rule C — Runtime is mechanical only

`runtime` may prepare worktrees, launch runners, collect results, and reconcile
workspaces, but it must not decide frontier ordering, barrier policy, or
planner/resolver transitions.

### Rule D — Resolver reasons; it does not become authority

`resolver` may investigate unresolved cases and return bounded machine-readable
results. It must not silently mutate plan authority or become the permanent
orchestration controller.

### Rule E — Tasks are self-contained

Task continuity should be carried by plan structure, descriptions, refs,
artifacts, and durable runner state — not by implicit hidden session memory
contracts.

### Rule F — Session reuse is not a planning concept

Session reuse may exist as an internal optimization or runner contract, but it
must not leak into public plan semantics.

### Rule G — Live authority beats cached convenience

If a dispatch, prompt, roster, or resolver decision depends on authority held by
core state, role-profile state, or current resolution state, implementations
must consume the live surface at the routing seam rather than silently relying
on a precomputed shortcut.

### Rule H — Drive is a session model, not a fifth component

Drive state may exist as a durable orchestration-session record, but it does not
create a new top-level architecture component alongside `control`, `roster`,
`runtime`, or `resolver`.

Drive records coordinate those components; they do not replace component
ownership boundaries.

---

## 3. Shared Types

These are the stable cross-component data shapes. They should live in one shared
contract module rather than being redefined locally.

### 3.1 `CoreSnapshot`

Authoritative plan-aware view from `vectl core`.

```python
@dataclass(frozen=True)
class CoreSnapshot:
    plan_complete: bool
    claimable_step_ids: tuple[str, ...]
    in_progress_step_ids: tuple[str, ...]
    blocked_step_ids: tuple[str, ...]
```

### 3.2 `RosterSnapshot`

Reusable resource/session view.

```python
@dataclass(frozen=True)
class RosterSnapshot:
    active_roles: tuple[str, ...]
    available_agents: tuple[str, ...]
    reusable_session_ids: tuple[str, ...]
```

### 3.3 `RuntimeSnapshot`

Mechanical execution view.

```python
@dataclass(frozen=True)
class RuntimeSnapshot:
    active_workspaces: tuple[str, ...]
    active_executions: tuple[str, ...]
    stalled_executions: tuple[str, ...]
```

### 3.4 Drive Scheduling Types

Drive is a durable orchestration session object. It is **not** a fifth
architecture component. It is the composition-level state that records how the
orchestration plane is currently coordinating `control`, `roster`, `runtime`,
and `resolver` for one plan.

#### Type aliases

```python
DriveStatus: TypeAlias = Literal[
    "running",
    "paused",
    "resolving",
    "replanning",
    "blocked_operator",
    "recovering",
    "completed",
    "halted",
    "failed_unrecoverable",
    "stopped",
]

ChildRunKind: TypeAlias = Literal["step", "resolver", "planner"]

ChildRunStatus: TypeAlias = Literal[
    "pending",
    "running",
    "success",
    "fail",
    "stall",
    "transport_error",
    "cancelled",
]

BarrierReason: TypeAlias = Literal[
    "runtime_failure",
    "merge_conflict",
    "review_failed",
    "planner_needed",
    "recovery_gate",
    "operator_pause",
]
```

#### `DriveRecord`

```python
@dataclass(frozen=True)
class DriveRecord:
    drive_id: str
    plan_path: str
    status: DriveStatus
    started_at: float
    updated_at: float
    finished_at: float | None = None
    agent: str = ""
    max_parallelism: int = 4
    active_child_run_ids: tuple[str, ...] = ()
    frontier_step_ids: tuple[str, ...] = ()
    blocked_case_ids: tuple[str, ...] = ()
    barrier: DriveBarrier | None = None
    operator_pause_state: Literal["active", "paused"] = "active"
    summary: str = ""
```

`DriveRecord.barrier` is the only authoritative barrier field. Barrier state must
not be duplicated as parallel top-level scalar fields.

`max_parallelism` constraints:

- minimum `1`
- default `4`
- hard maximum `32`

`active drive` is any `DriveRecord` whose status is not one of:

- `completed`
- `halted`
- `failed_unrecoverable`
- `stopped`

#### `ChildRunRef`

```python
@dataclass(frozen=True)
class ChildRunRef:
    run_id: str
    drive_id: str
    kind: ChildRunKind
    step_id: str = ""
    case_id: str = ""
    planner_request_id: str = ""
    status: ChildRunStatus = "pending"
    workspace: str = ""
    runner: str = ""
    session_id: str | None = None
    artifact_root: str = ""
```

#### `DriveBarrier`

```python
@dataclass(frozen=True)
class DriveBarrier:
    reason: BarrierReason
    entered_at: float
    case_ids: tuple[str, ...] = ()
    pending_resolver_run_id: str | None = None
    pending_planner_run_id: str | None = None
    active_child_run_ids_at_entry: tuple[str, ...] = ()
```

#### `PlannerRequest`

```python
@dataclass(frozen=True)
class PlannerRequest:
    reason: str
    affected_steps: tuple[str, ...]
    evidence_refs: tuple[str, ...] = ()
    constraints: tuple[str, ...] = ()
```

#### `StepChanges`

```python
@dataclass(frozen=True)
class StepChanges:
    name: str | None = None
    description: str | None = None
    depends_on: tuple[str, ...] | None = None
    refs: tuple[str, ...] | None = None
    verification: str | None = None
    agent: str | None = None
```

#### `PhaseChanges`

```python
@dataclass(frozen=True)
class PhaseChanges:
    name: str | None = None
    depends_on: tuple[str, ...] | None = None
    context: str | None = None
```

#### `PlannerMutation`

```python
@dataclass(frozen=True)
class PlannerMutation:
    action: Literal[
        "add-step",
        "edit-step",
        "remove-step",
        "move-step",
        "add-phase",
        "edit-phase",
        "skip-step",
        "complete-phase",
    ]
    arguments: Mapping[str, object]
    reason: str
    safety_notes: tuple[str, ...] = ()
```

For edit-style mutations, `arguments["changes"]` must conform to:

- `StepChanges` for `edit-step`
- `PhaseChanges` for `edit-phase`

#### `PlannerMutationBundle`

```python
@dataclass(frozen=True)
class PlannerMutationBundle:
    status: Literal["applyable", "operator_required", "halt"]
    summary: str
    mutations: tuple[PlannerMutation, ...]
    affected_steps: tuple[str, ...] = ()
    evidence_refs: tuple[str, ...] = ()
    safety_notes: tuple[str, ...] = ()
```

### 3.5 `ControlDecision`

Plan-aware next-step output from `control`.

```python
@dataclass(frozen=True)
class ControlDecision:
    kind: Literal["dispatch_batch", "resolve", "replan", "wait", "done", "halt"]
    reason: str
    step_ids: tuple[str, ...] = ()
    role_bindings: Mapping[str, str] = field(default_factory=dict)  # step_id -> role_id
    case_ids: tuple[str, ...] = ()
    planner_request: PlannerRequest | None = None
    capacity_used: int = 0
    capacity_remaining: int = 0
    barrier_required: bool = False
```

Interpretation:

- `dispatch_batch`: normal work may proceed for the listed frontier steps
- `resolve`: normal flow is not closed; invoke `resolver`
- `replan`: normal flow requires planner-owned authoritative mutation
- `wait`: system is not done, but should not dispatch new work now
- `done`: orchestration plane sees no further work
- `halt`: orchestration plane has reached a terminal stop condition

### 3.6 `WorkLease`

Claimed reusable resource from `roster`.

```python
@dataclass(frozen=True)
class WorkLease:
    role: str
    runner: str
    agent_id: str
    session_id: str | None
```

### 3.7 `ExecutionRequest`

Mechanical execution request into `runtime`.

Authority: docs/RFC-opencode-orchestration-runner.md sections 6.1, 6.2, 7.1

```python
@dataclass(frozen=True)
class ExecutionRequest:
    step_id: str
    role: str
    runner: str
    work_refs: tuple[str, ...]
    agent_id: str = ""
    prompt_bundle_path: str = ""
    runner_prompt_path: str = ""
    request_mode: Literal["start", "resume", "recover"] = "start"
    session_policy: Literal["reuse_allowed", "reuse_forbidden"] = "reuse_forbidden"
    session_id: str | None = None
```

### 3.8 `ExecutionResult`

Mechanical execution result returned from `runtime`.

```python
@dataclass(frozen=True)
class ExecutionResult:
    step_id: str
    status: Literal["success", "fail", "stall", "transport_error"]
    output_summary: str
    session_id: str | None = None
```

### 3.9 `ReconcileResult`

Mechanical reconcile result returned from `runtime` after a child run has reached
terminal execution.

```python
@dataclass(frozen=True)
class ReconcileResult:
    status: Literal["merged", "noop", "merge_conflict", "aborted"]
    summary: str
    artifact_refs: tuple[str, ...] = ()
```

### 3.10 `ReviewGateResult`

Bounded output from the post-execution review gate.

```python
@dataclass(frozen=True)
class ReviewGateResult:
    status: Literal["pass", "needs_fix", "needs_replan", "operator_required"]
    summary: str
    evidence_refs: tuple[str, ...] = ()
    planner_request: PlannerRequest | None = None
```

### 3.11 `ResolutionCase`

Problem handed from `control` to `resolver` when normal flow does not close.

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
    drive: DriveRecord | None = None
    blocked_step_ids: tuple[str, ...] = ()
    artifact_refs: tuple[str, ...] = ()
```

### 3.12 `ResolutionReport`

What `resolver` returns to `control`.

```python
@dataclass(frozen=True)
class ResolutionReport:
    status: Literal["unblocked", "waiting", "operator_required", "halt"]
    summary: str
    evidence_refs: tuple[str, ...] = ()
    operator_message: str | None = None
    planner_request: PlannerRequest | None = None
```

### 3.13 Recovery Types

Recovery truth types defined in `contracts.py`.

Authority: docs/RFC-opencode-orchestration-runner.md section 10

#### Type Aliases

```python
RequestMode: TypeAlias = Literal["start", "resume", "recover"]
SessionPolicy: TypeAlias = Literal["reuse_allowed", "reuse_forbidden"]
RecoveredVia: TypeAlias = Literal["native_session_resume", "fresh_relaunch"]
```

#### `RecoveryContinuity`

```python
@dataclass(frozen=True)
class RecoveryContinuity:
    recovered_via: RecoveredVia
    run_id: str = ""
    step_id: str = ""
    agent_id: str = ""
    runner: str = ""
    session_id: str | None = None
    timestamp: str = ""
```

#### `RecoveryAttempt`

```python
@dataclass(frozen=True)
class RecoveryAttempt:
    request_mode: RequestMode
    session_policy: SessionPolicy
    continuity: RecoveryContinuity | None = None
    session_id: str | None = None
    run_id: str = ""
    step_id: str = ""
```

---

#### `RecoveryReport`

```python
@dataclass(frozen=True)
class RecoveryReport:
    drive_id: str
    status: Literal["recovered", "partially_recovered", "unrecoverable"]
    recovered_child_run_ids: tuple[str, ...] = ()
    failed_child_run_ids: tuple[str, ...] = ()
    conflict_resolutions: tuple[str, ...] = ()
    barrier_after: DriveBarrier | None = None
    summary: str = ""
```

#### `PlannerInvocation`

```python
@dataclass(frozen=True)
class PlannerInvocation:
    plan_path: str
    affected_step_ids: tuple[str, ...]
    current_frontier: tuple[str, ...]
    barrier_reason: BarrierReason
    case_ids: tuple[str, ...] = ()
    evidence_refs: tuple[str, ...] = ()
```

## 4. Component Interfaces

## 4.1 `control`

### Responsibility

Plan-aware orchestration flow and drive-level scheduling decisions.

### Public Interface

```python
class Control(Protocol):
    def evaluate(
        self,
        core: CoreSnapshot,
        roster: RosterSnapshot,
        runtime: RuntimeSnapshot,
        drive: DriveRecord | None,
        barrier: DriveBarrier | None,
        open_case_ids: tuple[str, ...],
        recovery_gate_blocked: bool,
    ) -> ControlDecision: ...

    def apply_resolution(
        self,
        report: ResolutionReport,
        core: CoreSnapshot,
        roster: RosterSnapshot,
        runtime: RuntimeSnapshot,
        drive: DriveRecord,
        barrier: DriveBarrier,
    ) -> ControlDecision: ...

    def apply_planner_result(
        self,
        bundle: PlannerMutationBundle,
        core: CoreSnapshot,
        roster: RosterSnapshot,
        runtime: RuntimeSnapshot,
        drive: DriveRecord,
        barrier: DriveBarrier,
    ) -> ControlDecision: ...
```

### Owns

- plan-aware flow decisions
- drive-level frontier scheduling
- blocked/unresolved determination
- when to invoke `resolver`
- when to invoke planner-owned replan work
- when to dispatch work using available resources

### Does Not Own

- authoritative lifecycle mutation semantics
- reusable session internals
- workspace/runner chores
- long-running reasoning itself

### Dependency Direction

`control` may depend on:

- core views/adapters
- `roster` snapshot and claim/release surface
- `runtime` snapshot and execution surface
- `resolver` invocation surface
- drive/barrier state records

Nothing else in the orchestration plane should import plan-aware decisions back
from `control` as hidden authority.

### Error Contract

- If state is insufficient to close normal flow, return `kind="resolve"`; do not
  guess.
- If authoritative plan mutation is required, return `kind="replan"`; do not
  encode plan edits in prose.
- If authority conflict exists, surface it via `reason`; do not silently correct
  core truth.

---

## 4.2 `roster`

### Responsibility

Reusable resource registry.

### Public Interface

```python
class Roster(Protocol):
    def snapshot(self) -> RosterSnapshot: ...

    def claim(self, role: str) -> WorkLease | None: ...

    def release(self, lease: WorkLease) -> None: ...

    def register(
        self,
        lease: WorkLease,
        expires_at: float,
    ) -> None: ...
```

### Owns

- reusable agent/session metadata
- TTL / reuse-window bookkeeping
- compatibility/capability matching for resource reuse

### Does Not Own

- plan semantics
- blocked-state meaning
- next-step orchestration decisions

### Dependency Direction

`roster` may depend on time, runner/resource metadata, and internal persistence.

`roster` must not depend on plan/core semantics to decide its own behavior.

### Error Contract

- Returning `None` from `claim()` means no compatible reusable resource is
  currently available.
- It must not reinterpret `None` as a plan blockage.

---

## 4.3 `runtime`

### Responsibility

Mechanical execution chores.

### Public Interface

```python
class Runtime(Protocol):
    def snapshot(self) -> RuntimeSnapshot: ...

    def prepare(self, request: ExecutionRequest) -> str: ...

    def start(self, request: ExecutionRequest, workspace: str) -> str: ...

    def collect(self, execution_id: str) -> ExecutionResult | None: ...

    def begin_reconcile(self, execution_id: str) -> ReconcileResult | None: ...

    def cleanup(self, workspace: str) -> None: ...
```

Batch dispatch is a scheduler concern, not a separate runtime authority. The
scheduler forms a batch by issuing repeated `prepare()` / `start()` calls in a
stable order and persisting each admitted child run individually.

Identity rule:

- the string returned by `start()` is both the runtime `execution_id` and the
  authoritative `ChildRunRef.run_id`

### Owns

- worktree/workspace preparation and cleanup
- runner startup / resume / shutdown helpers
- mechanical execution handles and collection
- reconcile mechanics per child run

### Does Not Own

- plan-aware dispatch decisions
- blocked-state reasoning
- authority mutation semantics
- frontier ordering or batch selection

### Dependency Direction

`runtime` may depend on worktree, runner, and environment helpers.

It must not decide what work should happen next.

### Error Contract

- Mechanical failures are returned as execution outcomes or surfaced explicitly.
- `runtime` must not convert mechanical failure into orchestration policy.
- If a start inside a scheduler batch fails, `runtime` reports only the failed
  start; the scheduler decides how the already-started siblings affect the drive.
- If `begin_reconcile()` returns `None`, that means reconcile has not yet reached
  a terminal disposition and the caller must poll again rather than treating it
  as `noop` or as an error by default.

---

## 4.4 `resolver`

### Responsibility

Reasoning over blocked or unresolved cases.

### Public Interface

```python
class Resolver(Protocol):
    def resolve(self, case: ResolutionCase) -> ResolutionReport: ...
```

### Owns

- blocker investigation
- use of allowed tools to find an unblock path
- returning a bounded outcome to `control`
- optionally requesting planner-owned authoritative mutation via `planner_request`

### Does Not Own

- permanent plan/control authority
- reusable resource bookkeeping
- mechanical startup/dispatch chores
- direct plan mutation

### Dependency Direction

`resolver` may depend on allowed tool surfaces and read-only views needed to
resolve the case.

It should not silently become the orchestration plane's permanent controller.

### Error Contract

- If the case cannot be closed safely, return `operator_required` rather than
  fabricating false certainty.
- If resolver reasoning concludes that authoritative mutation is required, it must
  request planner work through `planner_request` rather than returning free-form
  mutation prose.

---

## 4.5 `review_gate` (shared support)

### Responsibility

Bounded post-execution review normalization.

### Public Interface

```python
class ReviewGate(Protocol):
    def evaluate(
        self,
        step_id: str,
        execution_result: ExecutionResult,
        artifact_refs: tuple[str, ...] = (),
    ) -> ReviewGateResult: ...
```

### Owns

- parsing or validating machine-readable review outputs
- reducing post-execution review into one bounded `ReviewGateResult`

### Does Not Own

- frontier scheduling
- plan mutation
- resolver reasoning
- runtime mechanics

### Error Contract

- malformed review output must become `needs_fix` or `operator_required`; it must
  not silently pass
- review gate must not directly mutate authoritative plan state

---

## 5. Interaction Boundaries

### 5.1 `control` ↔ `roster`

- `control` asks: do we have a compatible reusable resource?
- `roster` answers resource questions only.
- `roster` does not decide whether work should exist.

### 5.2 `control` ↔ `runtime`

- `control` decides whether work should be executed.
- `runtime` performs mechanical preparation/execution/collection.
- `runtime` does not decide frontier width, batch ordering, or barrier policy.

### 5.3 `control` ↔ `resolver`

- `control` invokes `resolver` only when normal flow is not closed.
- `resolver` returns an outcome, not a new permanent authority.

### 5.4 `resolver` ↔ `roster` / `runtime`

- `resolver` may inspect or request information through allowed surfaces.
- It does not absorb ownership of those components.

### 5.5 drive ↔ control / runtime / resolver

- drive records are durable orchestration-session state, not a fifth component
- `control` reads drive state and emits the next scheduler decision
- `runtime` owns child execution mechanics inside drive-assigned work
- `resolver` and planner child runs operate under drive barrier policy
- drive-level control surfaces act on the orchestration session, not on an
  arbitrary child run

---

## 6. Ownership Matrix

| State / Concern | Owner |
|---|---|
| plan graph / lifecycle / claims | `vectl core` |
| drive session state / active frontier / barrier state | orchestration composition layer (`driver.py` / `orch_app.py`) |
| blocked vs continue vs dispatch vs done | `control` |
| reusable session/agent registry | `roster` |
| workspaces / runner handles / mechanical execution | `runtime` |
| unresolved-case reasoning | `resolver` |
| authoritative plan mutation from planner output | vectl facade over `vectl core` |

---

## 7. Explicitly Rejected Interface Ideas

The following should not appear in target orchestration-plane interfaces unless a
future architecture decision explicitly reverses this:

- `continuity_group`
- task-level `session_reuse` flags
- planner-visible continuity grouping concepts
- plan-aware methods on `roster`
- hidden runtime-control ownership inside `runtime`
- planner output as free-form prose mutations

---

## 8. Open Interface Question

No open interface question remains at the contract layer for drive orchestration.

Implementation still needs to realize these interfaces, but the target boundary
is intentionally frozen by `docs/RFC-orch-drive.md`.

---

## 9. Summary

The target interface boundary is:

- `control` decides orchestration flow, including batch frontier scheduling
- `roster` manages reusable resources and leases
- `runtime` performs mechanical execution and reconcile chores
- `resolver` handles blocked/unresolved reasoning and may request planner-owned mutation
- drive state is a durable orchestration session, not a fifth architecture component
- authoritative plan mutation still flows through vectl facade over `vectl core`

That is the interface baseline implementation must preserve.
