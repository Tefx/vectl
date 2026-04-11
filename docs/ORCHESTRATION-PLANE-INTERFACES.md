# Orchestration Plane Interfaces

> Interface-boundary specification for the target `orchestration plane`.

**Status:** Target interface specification  
**Architecture authority:** `docs/ORCHESTRATION-PLANE-ARCHITECTURE.md`, `docs/ADR-orchestration-role-agent-prompt-separation.md`, `docs/ORCHESTRATION-PLANE-LIVE-AUTHORITY-CONTRACT-LOCK.md`  
**Related docs:** `docs/ORCHESTRATION-PLANE-RESOLUTION-CONTRACT.md`, `docs/ORCHESTRATION-PLANE-ISOLATION-SEMANTICS.md`  
**Scope:** Full target boundaries; not an implementation slice

---

## 1. Anchor

The orchestration plane is a subsystem over `vectl core`.

```text
vectl core (authority)
orchestration plane
  - control
  - roster
  - runtime
  - resolver
```

This document exists to answer one question precisely:

> What may each orchestration-plane component know, decide, own, and return?

---

## 2. Shared Boundary Rules

### Rule A — Core owns authority

`vectl core` remains the sole authority for:

- plan graph
- lifecycle
- claims semantics
- existing authoritative decision/query surfaces

No orchestration-plane component may silently fork or shadow those truths.

### Rule B — Control is plan-aware; roster is not

`control` may interpret plan/core state.

`roster` may not.

### Rule C — Runtime is mechanical only

`runtime` may perform execution chores, but does not decide what the system
should do next.

### Rule D — Resolver reasons; it does not become authority

`resolver` may investigate blocked/unresolved cases and propose actions or
decisions, but it does not become the durable source of truth.

### Rule E — Tasks are self-contained

Task continuity should normally be carried by task decomposition, descriptions,
refs, and explicit artifacts — not by architecture-level continuity concepts.

### Rule F — Session reuse is not an interface concept

Session reuse may exist as an internal `roster` optimization. It must not become
part of the public task/planning contract unless a future architecture decision
explicitly promotes it.

### Rule G — Live authority beats cached convenience

If a dispatch, prompt, roster, or resolver decision depends on authority held by
core state, role-profile state, or current resolution state, implementations
must consume the live surface at the routing seam rather than silently relying
on a precomputed shortcut.

---

## 3. Shared Types

These are orchestration-plane shared interface types. They are intentionally
minimal and should not be expanded casually.

### 3.1 `CoreSnapshot`

Read-only view of authoritative core state needed by orchestration.

```python
@dataclass(frozen=True)
class CoreSnapshot:
    plan_complete: bool
    claimable_step_ids: tuple[str, ...]
    in_progress_step_ids: tuple[str, ...]
    blocked_step_ids: tuple[str, ...]
    unresolved_reasons: tuple[str, ...]
```

Notes:
- This is a boundary type, not a promise about exact final fields.
- `control` consumes a core view; it does not infer plan truth from `roster`.

### 3.2 `RosterSnapshot`

Read-only view of reusable resource state.

```python
@dataclass(frozen=True)
class RosterSnapshot:
    available_agents: tuple[str, ...]
    working_agents: tuple[str, ...]
    reusable_sessions: tuple[str, ...]
    exhausted_roles: tuple[str, ...]
```

### 3.3 `RuntimeSnapshot`

Read-only view of mechanical execution state.

```python
@dataclass(frozen=True)
class RuntimeSnapshot:
    active_workspaces: tuple[str, ...]
    active_executions: tuple[str, ...]
    stalled_executions: tuple[str, ...]
```

### 3.4 `ControlDecision`

Minimal next-step output from `control`.

```python
@dataclass(frozen=True)
class ControlDecision:
    kind: Literal["dispatch", "resolve", "wait", "done"]
    reason: str
    step_id: str | None = None
    role: str | None = None
```

Interpretation:
- `dispatch`: normal work may proceed
- `resolve`: normal flow is not closed; invoke `resolver`
- `wait`: system is not done, but should not dispatch new work now
- `done`: orchestration plane sees no further work

### 3.5 `WorkLease`

Claimed reusable resource from `roster`.

```python
@dataclass(frozen=True)
class WorkLease:
    role: str
    runner: str
    agent_id: str
    session_id: str | None
```

### 3.6 `ExecutionRequest`

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

- `request_mode`: Launch mode semantics. `start` begins a fresh execution;
  `resume` continues an existing session; `recover` reconstructs from durable
  artifacts, preferring native session continuation and falling back to fresh
  relaunch.
- `session_policy`: Whether orchestration may reuse an existing session.
  `reuse_forbidden` forces fresh execution semantics; `reuse_allowed` permits
  session continuation.
- `agent_id`: Agent identifier for the execution (authoritative resolution of
  role to agent).
- `prompt_bundle_path`: Path to the rendered prompt bundle artifact.
- `runner_prompt_path`: Path to the runner-specific prompt artifact.

### 3.7 `ExecutionResult`

Mechanical execution result returned from `runtime`.

```python
@dataclass(frozen=True)
class ExecutionResult:
    step_id: str
    status: Literal["success", "fail", "stall", "transport_error"]
    output_summary: str
    session_id: str | None = None
```

### 3.8 `ResolutionCase`

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
    blocked_step_ids: tuple[str, ...] = ()
    artifact_refs: tuple[str, ...] = ()
```

Important:
- `case_source` is a coarse source tag, not a large blocker taxonomy.
- `summary`, `blocked_step_ids`, and `artifact_refs` provide bounded coordination context.
- `core`, `roster`, and `runtime` remain the authoritative state basis for reasoning.

### 3.9 `ResolutionReport`

What `resolver` returns to `control`.

```python
@dataclass(frozen=True)
class ResolutionReport:
    status: Literal["unblocked", "waiting", "operator_required", "halt"]
    summary: str
    evidence_refs: tuple[str, ...] = ()
    operator_message: str | None = None
```

Important:
- `resolver` returns a bounded report to `control`.
- `control` must refresh state and resume normal evaluation from refreshed
  reality.

---

### 3.10 Recovery Types

Recovery truth types defined in `contracts.py`.

Authority: docs/RFC-opencode-orchestration-runner.md section 10

#### Type Aliases

```python
RequestMode: TypeAlias = Literal["start", "resume", "recover"]
SessionPolicy: TypeAlias = Literal["reuse_allowed", "reuse_forbidden"]
RecoveredVia: TypeAlias = Literal["native_session_resume", "fresh_relaunch"]
```

- `RequestMode`: Launch mode for execution. `start` begins fresh; `resume`
  continues an existing session; `recover` reconstructs from durable artifacts.
- `SessionPolicy`: Whether session reuse is permitted. `reuse_allowed` permits
  orchestration to provide an existing session; `reuse_forbidden` forces fresh
  execution semantics.
- `RecoveredVia`: Truth label distinguishing the two recovery paths. The system
  must not collapse these into the same label. Persisted in `continuity.json`.

#### 3.10.1 `RecoveryContinuity`

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

Persisted at `.vectl/runs/<run_id>/recovery/continuity.json`. Mirrors into
recovery attempt artifacts and human-readable summaries.

#### 3.10.2 `RecoveryAttempt`

```python
@dataclass(frozen=True)
class RecoveryAttempt:
    attempt_kind: Literal["resume", "recover"]
    native_validation_ok: bool = False
    native_validation_failure_reason: str = ""
    fallback_relaunch_used: bool = False
    resulting_run_id: str = ""
    resulting_session_id: str | None = None
```

Records whether native session validation succeeded, why it failed (if it did),
whether fallback relaunch was used, and resulting identifiers. Persisted at
`.vectl/runs/<run_id>/recovery/resume_attempt.json` or `recover_attempt.json`.

---

## 4. Component Interfaces

## 4.1 `control`

### Responsibility

Plan-aware orchestration flow.

### Public Interface

```python
class Control(Protocol):
    def evaluate(
        self,
        core: CoreSnapshot,
        roster: RosterSnapshot,
        runtime: RuntimeSnapshot,
    ) -> ControlDecision: ...

    def apply_resolution(
        self,
        report: ResolutionReport,
        core: CoreSnapshot,
        roster: RosterSnapshot,
        runtime: RuntimeSnapshot,
    ) -> ControlDecision: ...
```

### Owns

- plan-aware flow decisions
- blocked/unresolved determination
- when to invoke `resolver`
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

Nothing else in the orchestration plane should import plan-aware decisions back
from `control` as hidden authority.

### Error Contract

- If state is insufficient to close normal flow, return `kind="resolve"`; do not
  guess.
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

    def cleanup(self, workspace: str) -> None: ...
```

### Owns

- worktree/workspace preparation and cleanup
- runner startup / resume / shutdown helpers
- mechanical execution handles and collection

### Does Not Own

- plan-aware dispatch decisions
- blocked-state reasoning
- authority mutation semantics

### Dependency Direction

`runtime` may depend on worktree, runner, and environment helpers.

It must not decide what work should happen next.

### Error Contract

- Mechanical failures are returned as execution outcomes or surfaced explicitly.
- `runtime` must not convert mechanical failure into orchestration policy.

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

### Does Not Own

- permanent plan/control authority
- reusable resource bookkeeping
- mechanical startup/dispatch chores

### Dependency Direction

`resolver` may depend on allowed tool surfaces and read-only views needed to
resolve the case.

It should not silently become the orchestration plane's permanent controller.

### Error Contract

- If the case cannot be closed, return `operator_required` rather than fabricating
  false certainty.

---

## 5. Interaction Boundaries

### 5.1 `control` ↔ `roster`

- `control` asks: do we have a compatible reusable resource?
- `roster` answers resource questions only.
- `roster` does not decide whether work should exist.

### 5.2 `control` ↔ `runtime`

- `control` decides whether work should be executed.
- `runtime` performs mechanical preparation/execution/collection.

### 5.3 `control` ↔ `resolver`

- `control` invokes `resolver` only when normal flow is not closed.
- `resolver` returns an outcome, not a new permanent authority.

### 5.4 `resolver` ↔ `roster` / `runtime`

- `resolver` may inspect or request information through allowed surfaces.
- It does not absorb ownership of those components.

---

## 6. Ownership Matrix

| State / Concern | Owner |
|---|---|
| plan graph / lifecycle / claims | `vectl core` |
| blocked vs continue vs dispatch vs done | `control` |
| reusable session/agent registry | `roster` |
| workspaces / runner handles / mechanical execution | `runtime` |
| unresolved-case reasoning | `resolver` |

---

## 7. Explicitly Rejected Interface Ideas

The following should not appear in target orchestration-plane interfaces unless a
future architecture decision explicitly reverses this:

- `continuity_group`
- task-level `session_reuse` flags
- planner-visible continuity grouping concepts
- plan-aware methods on `roster`
- hidden runtime-control ownership inside `runtime`

---

## 8. Open Interface Question

The main remaining interface-level open issue is now migration-oriented rather
than conceptual: how the current legacy package mechanics should be mapped into
these target interfaces without losing behavior or test coverage.

---

## 9. Summary

The target interface boundary is simple:

- `control` decides orchestration flow
- `roster` manages reusable resources
- `runtime` performs mechanical chores
- `resolver` handles blocked/unresolved reasoning
- `vectl core` remains external authority

That is the interface baseline the next design step should preserve.
