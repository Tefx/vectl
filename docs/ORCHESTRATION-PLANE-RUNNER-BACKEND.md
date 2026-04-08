# Orchestration Plane Runner Backend

**Status:** Proposed  
**Architecture authority:** `docs/ORCHESTRATION-PLANE-ARCHITECTURE.md`  
**Interface authority:** `docs/ORCHESTRATION-PLANE-INTERFACES.md`

---

## 1. Purpose

This document defines the missing **runner backend** for the orchestration
plane.

The current orchestration plane has:

- `ExecutionRequest.runner`
- `runtime.start()`
- `runtime.collect()`

but it does not yet have the actual execution substrate needed to launch,
resume, poll, and cancel real agent work.

This document fills that gap.

---

## 2. Problem statement

Today, the orchestration plane models execution requests but does not implement
the underlying execution backend.

Current consequences:

- `runtime.start()` allocates an execution ID but does not launch real work
- `runtime.collect()` does not collect real execution results
- `resolver` has no shared substrate for agentic blocker handling
- execution lifecycle, cancellation, and transport error handling remain
  underspecified in code

This creates a structural hole between the orchestration plane contract and the
actual execution of agent work.

---

## 3. Design goal

The runner backend is the **shared mechanical execution substrate** for the
orchestration plane.

It exists so that:

- `runtime` can execute ordinary step work
- `resolver` can execute agentic blocker-handling work when needed

The backend is intentionally shared. The system should not grow separate
execution stacks for ordinary runtime work and resolver work.

---

## 4. Scope

### In scope

- runner selection by runner ID
- launch of real execution work
- resume of resumable work
- polling/collection of execution state
- cancellation/shutdown of running work
- normalization of terminal and transport states
- a reusable substrate that both `runtime` and `resolver` can call

### Out of scope

- plan-aware orchestration decisions
- blocker reasoning policy
- authority mutation semantics
- merge conflict reasoning
- agent prompt design
- runner-specific product policy beyond what is needed for the mechanical contract

---

## 5. Ownership boundary

### Runner backend owns

- launch / resume / poll / cancel mechanics
- runner registry and adapter lookup
- execution-handle lifecycle
- mapping runner-native results into orchestration-plane execution states
- transport/mechanical error normalization

### Runner backend does not own

- whether work should be dispatched
- whether a blocked case should escalate to resolver
- authority mutations to core state
- semantic interpretation of work success/failure

---

## 6. Why one shared backend

`runtime` and `resolver` have different responsibilities, but both eventually
need the same mechanical execution substrate:

- select a runner
- launch work
- poll running work
- cancel work if needed
- interpret transport/mechanical failures

The architecture should therefore use:

> one execution substrate, multiple callers

This prevents duplicated launch/poll/cancel logic and keeps execution behavior
auditable and consistent.

---

## 7. Relationship to orchestration components

### `runtime`

`runtime` is the primary consumer of the runner backend for normal step
execution.

It remains responsible for:

- workspace/worktree preparation
- cleanup
- execution bookkeeping in orchestration-plane state

It delegates real process/agent execution mechanics to the runner backend.

### `resolver`

`resolver` is not the runner backend.

`resolver` remains the blocked/unresolved reasoning role. When resolver must use
agentic execution to investigate or unblock a case, it should reuse the same
runner backend substrate instead of introducing a separate execution system.

### `control`

`control` should not know runner internals.

It may decide that work should dispatch or that resolution is required, but it
does not launch work itself and does not reason about launch mechanics.

---

## 8. Contract

### 8.1 RunnerCapabilities

```python
@dataclass(frozen=True)
class RunnerCapabilities:
    runner_id: str
    supports_resume: bool
    supports_cancel: bool
    supports_streaming: bool
```

Purpose:

- advertises stable mechanical capabilities of a runner adapter
- allows callers to avoid assuming resume/cancel/streaming support

### 8.2 RunnerHandle

```python
@dataclass(frozen=True)
class RunnerHandle:
    runner: str
    run_id: str
    session_id: str | None
```

Purpose:

- identifies one launched execution from the runner backend's perspective
- preserves runner namespace and optional reusable session identity

### 8.3 RunnerLaunchResult

```python
@dataclass(frozen=True)
class RunnerLaunchResult:
    handle: RunnerHandle
    initial_summary: str
```

Purpose:

- returns the launched handle plus a bounded startup summary

### 8.4 RunnerPollResult

```python
@dataclass(frozen=True)
class RunnerPollResult:
    status: Literal[
        "running",
        "success",
        "fail",
        "stall",
        "transport_error",
    ]
    output_summary: str
    session_id: str | None = None
    evidence_refs: tuple[str, ...] = ()
```

Purpose:

- exposes the runner backend's normalized view of execution progress/termination

### 8.5 Runner protocol

```python
class Runner(Protocol):
    def capabilities(self) -> RunnerCapabilities: ...

    def launch(
        self,
        request: ExecutionRequest,
        workspace: Path,
    ) -> RunnerLaunchResult: ...

    def resume(
        self,
        request: ExecutionRequest,
        workspace: Path,
    ) -> RunnerLaunchResult: ...

    def poll(self, handle: RunnerHandle) -> RunnerPollResult: ...

    def cancel(self, handle: RunnerHandle) -> None: ...
```

### 8.6 Error types

The runner backend must use explicit typed errors for mechanical failures.

Recommended minimal error model:

```python
class RunnerError(Exception):
    """Base class for runner-backend mechanical failures."""


class RunnerNotFoundError(RunnerError):
    runner_id: str


class RunnerLaunchError(RunnerError):
    runner_id: str
    reason: Literal[
        "workspace_invalid",
        "request_invalid",
        "resource_unavailable",
        "internal",
    ]


class RunnerResumeError(RunnerError):
    runner_id: str
    reason: Literal[
        "resume_unsupported",
        "session_missing",
        "session_invalid",
        "internal",
    ]


class RunnerPollError(RunnerError):
    runner_id: str
    reason: Literal[
        "handle_unknown",
        "transport_failed",
        "internal",
    ]


class RunnerCancelError(RunnerError):
    runner_id: str
    reason: Literal[
        "cancel_unsupported",
        "already_complete",
        "transport_failed",
        "internal",
    ]
```

Recovery guidance:

- `RunnerNotFoundError` is non-recoverable at the caller level and should be surfaced explicitly
- `RunnerLaunchError` and `RunnerResumeError` are mechanical startup failures and should not be silently downgraded into semantic task failure
- `RunnerPollError` should map to transport/mechanical failure handling, not to business/task failure
- `RunnerCancelError` should be surfaced during cleanup/shutdown handling and recorded as runner-backend failure evidence

These exception types are mechanical/runtime facts. They do not replace the
normalized poll result states; they complement them.

---

## 9. Registry

The runner backend needs a registry that resolves `ExecutionRequest.runner` into
the corresponding runner adapter.

Recommended shape:

```python
class RunnerRegistry(Protocol):
    def get(self, runner_id: str) -> Runner: ...
```

Rules:

- unknown runner IDs must be rejected explicitly
- caller code must not guess or silently substitute a runner

---

## 10. Runtime integration

### 10.1 `runtime.start()`

`runtime.start()` should:

1. validate the workspace exists and is prepared
2. resolve the runner via the registry
3. choose `launch()` vs `resume()` based on request semantics
4. store the resulting `RunnerHandle`
5. return the orchestration-plane execution ID

`runtime.start()` should not itself implement runner-native launch logic.

### 10.2 `runtime.collect()`

`runtime.collect()` should:

1. look up the execution's `RunnerHandle`
2. call `runner.poll(handle)`
3. return `None` when status is `running`
4. map terminal poll results into `ExecutionResult`

### 10.3 `runtime.cleanup()`

`runtime.cleanup()` should:

1. cancel still-running work when required
2. release runner-associated resources if needed
3. clean the workspace/worktree

### 10.4 Polling contract

`poll()` is a **non-blocking** operation.

Rules:

- `poll()` must return promptly with either `status="running"` or a terminal normalized status
- `poll()` must not block waiting for process completion
- callers are responsible for repeated polling

Recommended runtime behavior:

- polling interval should be configurable
- default polling interval should be **1 second or greater**
- implementations should avoid busy-polling

Stall contract:

- `stall` means execution is still present or plausibly recoverable, but has exceeded the configured progress/heartbeat expectation
- stall detection threshold should be configurable by runner/runtime policy
- until a concrete implementation-specific default is chosen, this threshold should be treated as **DECISION_NEEDED** at implementation time and then pinned in code-facing config/docs

Cancellation contract:

- `cancel()` should attempt graceful termination first
- cancel timeout should be configurable by runner/runtime policy
- until a concrete implementation-specific default is chosen, this timeout should be treated as **DECISION_NEEDED** at implementation time and then pinned in code-facing config/docs

Result mapping rule:

- `status="running"` stays inside the runner-backend polling layer
- terminal statuses map into orchestration-plane `ExecutionResult`
- poll exceptions (`RunnerPollError`) are not success/fail task outcomes; they are transport/mechanical failures

---

## 11. Resolver integration

`resolver` should not introduce a second execution stack.

When resolver needs agentic execution, it should call the same runner backend
substrate through its invocation boundary.

This means:

- ordinary step execution and blocker-handling execution share launch/poll/cancel mechanics
- resolver remains the owner of blocked-case reasoning
- runner backend remains the owner of execution mechanics

---

## 12. Error model

The runner backend should distinguish:

- launch failure
- resume failure
- poll failure
- cancel failure
- execution stall
- transport/mechanical execution failure

These are mechanical/runtime facts, not plan-aware orchestration decisions.

The runner backend should normalize them into runner-independent result shapes so
that runtime and resolver do not each invent their own error taxonomies.

---

## 13. Non-goals and anti-patterns

The runner backend must not:

- absorb plan-aware orchestration
- absorb resolver reasoning policy
- become a second core authority surface
- expose runner-specific behavior as hidden global policy
- require `control` to understand backend-specific execution details

Anti-patterns to avoid:

- separate runtime and resolver execution systems
- putting launch/poll/cancel logic directly into `control`
- stuffing runner-native behavior directly into `runtime.py` without an adapter boundary

---

## 14. Required implementation surfaces

Recommended code additions:

- `src/vectl/orchestration/runners.py`
- `src/vectl/orchestration/runner_registry.py`
- one or more concrete runner adapters under `src/vectl/orchestration/`

Required code updates:

- `src/vectl/orchestration/runtime.py`
- `src/vectl/orchestration/contracts.py`
- `src/vectl/orchestration/interfaces.py`
- `src/vectl/orchestration/resolver.py` (only for substrate reuse, not to turn resolver into the backend)

---

## 15. Verification guidance

Implementation should prove:

1. `runtime.start()` launches real work through a runner adapter
2. `runtime.collect()` returns `None` while running and real terminal results when complete
3. unknown runner IDs are rejected explicitly
4. `runtime.cleanup()` can cancel active work and then clean up
5. resolver can reuse the backend substrate without owning it

Suggested test classes:

- runner registry tests
- fake-runner lifecycle tests
- runtime integration tests
- resolver invocation integration tests

---

## 16. Final recommendation

The orchestration plane should implement a **single shared runner backend** as a
mechanical execution substrate.

That substrate should be:

- owned by neither `control` nor `resolver`
- consumed by both `runtime` and `resolver`
- kept strictly separate from plan-aware orchestration and blocked-case reasoning

This closes the largest current execution gap without collapsing the
architecture back into one oversized always-on agentic controller.
