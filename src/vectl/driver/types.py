"""Driver-internal data types.

Responsibility: Define all mutable and immutable value types used internally
by the driver. Single canonical location for driver-specific data contracts.

Non-responsibility: Does NOT define vectl-core types (Plan, Step, Action, etc.).
Does NOT define config schema types (those live in config.py).

Architecture Reference: docs/DRIVER-ARCHITECTURE.md Section 2.1
Blueprint Reference: DRIVER-BLUEPRINT.md Module Map (types.py)
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Protocol

from vectl.decision_state import DecideState

if TYPE_CHECKING:
    from vectl.models import CompletedResult, RunningTask

    # Avoid circular import: RunnerHandle defined in runners.py
    from .runners import RunnerHandle


class RunnerStatus(str, Enum):
    """Outcome of a runner execution.

    Architecture: docs/DRIVER-ARCHITECTURE.md Section 2.1, RunnerStatus enum
    Blueprint: DRIVER-BLUEPRINT.md Runner Protocol & Implementations
    """

    SUCCESS = "success"
    FAIL = "fail"
    STALL = "stall"
    TRANSPORT_ERROR = "transport_error"


@dataclass
class RunnerResult:
    """Parsed output from a completed runner process.

    Architecture: docs/DRIVER-ARCHITECTURE.md Section 2.1, RunnerResult dataclass
    Blueprint: DRIVER-BLUEPRINT.md Runner Protocol & Implementations
    """

    status: RunnerStatus
    session_id: str | None
    output: str
    elapsed_seconds: float
    exit_code: int | None
    cost_usd: float | None = None
    tokens: dict[str, int] | None = None


@dataclass
class RunningEntry:
    """Bookkeeping for one in-flight runner process.

    Architecture: docs/DRIVER-ARCHITECTURE.md Section 2.1, RunningEntry dataclass
    Blueprint: DRIVER-BLUEPRINT.md Flow 1 (DriverState.running)
    """

    step_id: str
    agent: str
    runner_name: str
    handle: RunnerHandle  # Protocol reference (see runners.py)
    worktree_path: str
    dispatched_at: float  # time.monotonic()


@dataclass
class CompletedEntry:
    """A runner that has finished; pending reconciliation.

    Architecture: docs/DRIVER-ARCHITECTURE.md Section 2.1, CompletedEntry dataclass
    Blueprint: DRIVER-BLUEPRINT.md Flow 3 (reconcile input)
    """

    step_id: str
    agent: str
    runner_name: str
    result: RunnerResult
    worktree_path: str
    elapsed_seconds: float


@dataclass
class DriverState:
    """All mutable in-memory state for the driver loop.

    This is the SINGLE owner of running-task bookkeeping.
    No other module may maintain parallel running-task state.

    Architecture: docs/DRIVER-ARCHITECTURE.md Section 2.1, DriverState dataclass
    Blueprint: DRIVER-BLUEPRINT.md Flow 1 (state = DriverState())

    Invariants:
        - RunningEntry instances exist in `running` if and only if a runner
          process is alive. When a handle completes, the entry MUST be moved
          to `completed_queue` within the same loop iteration.
        - `merge_lock` is the sole serialization mechanism for git merge
          operations. All code paths that call `worktree.merge()` MUST hold
          this lock.
        - Completion authority convergence is pinned by
          ``loop.COMPLETION_AUTHORITY_A1_CONTRACT``: runtime completion/failure
          side effects are reconcile-owned. ``completed_queue`` and
          ``drain_completed()`` are legacy transitional surfaces and may be
          narrowed or removed by the implementation owner step when no longer
          required by runtime loop wiring.
    """

    running: dict[str, RunningEntry] = field(default_factory=dict)
    # step_id -> RunningEntry

    completed_queue: list[CompletedEntry] = field(default_factory=list)
    # Legacy transitional queue; runtime convergence may remove this surface.

    failure_counts: dict[str, int] = field(default_factory=dict)
    # step_id -> consecutive failure count

    failure_history: dict[str, list[str]] = field(default_factory=dict)
    # step_id -> [error_output_1, error_output_2, ...]

    runner_failures: dict[tuple[str, str], int] = field(default_factory=dict)
    # (step_id, runner_name) -> consecutive failure count for runner fallback

    agent_overrides: dict[str, str] = field(default_factory=dict)
    # step_id -> overridden agent name (from judge SWITCH_AGENT verdict)

    halt_requested: bool = False

    loop_detector: list[str] = field(default_factory=list)
    # Last N serialized decide() action signatures for loop detection

    merge_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    # Serializes all git merge operations (real lock, not LLM instruction)

    runtime_config: object | None = None
    # Loop-injected runtime config for reconcile follow-up dispatch wiring.

    runtime_runners: Mapping[str, object] | None = None
    # Loop-injected runner map for reconcile follow-up planner dispatch wiring.

    decide_state: DecideState = field(default_factory=DecideState)
    # Sole mutable owner for decide-side session reuse/failure memory.

    def as_running_tasks(self) -> list[RunningTask]:
        """Convert to vectl.models.RunningTask list for decide() input.

        Architecture: docs/DRIVER-ARCHITECTURE.md Section 2.1, DriverState methods
        Blueprint: DRIVER-BLUEPRINT.md Flow 1 (decide_result = decide(...))
        """
        from vectl.models import RunningTask

        return [
            RunningTask(
                step_id=entry.step_id,
                agent=entry.agent,
                task_id=entry.handle.session_id or "",
                dispatched_at=entry.dispatched_at,
            )
            for entry in self.running.values()
        ]

    def drain_completed(self) -> list[CompletedResult] | None:
        """Drain legacy completed_queue into vectl.models.CompletedResult list.

        Architecture: docs/DRIVER-ARCHITECTURE.md Section 2.1, DriverState methods
        Blueprint: DRIVER-BLUEPRINT.md Flow 1 (completed_results = state.drain_completed())

        Returns:
            List of CompletedResult if there are completed entries, None otherwise.

        Authority note:
            ``driver-debt-completion-authority.contract`` pins this as a legacy
            compatibility seam. Runtime completion authority has converged on
            ``wait_for_any() -> reconcile()`` in loop runtime wiring.
            This method remains for compatibility callers that still populate
            ``completed_queue`` directly (e.g. tests or transitional adapters).
        """
        from vectl.models import CompletedResult

        if not self.completed_queue:
            return None

        results = [
            CompletedResult(
                step_id=entry.step_id,
                task_id=entry.result.session_id or "",
                status="SUCCESS" if entry.result.status == RunnerStatus.SUCCESS else "FAIL",
                output_summary=entry.result.output[:500],  # Truncate for summary
            )
            for entry in self.completed_queue
        ]
        self.completed_queue.clear()
        return results

    async def wait_for_any(self) -> CompletedEntry:
        """Wait for any running handle to complete. Returns first done.

        Architecture: docs/DRIVER-ARCHITECTURE.md Section 2.1, DriverState methods
        Blueprint: DRIVER-BLUEPRINT.md Flow 1 (completed = await state.wait_for_any())

        Raises:
            RuntimeError: If no running tasks.
        """
        if not self.running:
            raise RuntimeError("No running tasks to wait for")

        # Create wait tasks for all handles
        async def wait_for_entry(step_id: str, entry: RunningEntry) -> tuple[str, CompletedEntry]:
            result = await entry.handle.wait()
            completed = CompletedEntry(
                step_id=entry.step_id,
                agent=entry.agent,
                runner_name=entry.runner_name,
                result=result,
                worktree_path=entry.worktree_path,
                elapsed_seconds=result.elapsed_seconds,
            )
            return step_id, completed

        tasks = {
            asyncio.create_task(wait_for_entry(step_id, entry)): step_id
            for step_id, entry in self.running.items()
        }

        done, _ = await asyncio.wait(tasks.keys(), return_when=asyncio.FIRST_COMPLETED)
        task = done.pop()
        step_id, completed = task.result()

        # Move from running map only.
        # Runtime completion authority is reconcile-only; this method returns the
        # completed entry directly to loop.py for immediate reconcile().
        del self.running[step_id]

        # Cancel remaining tasks
        for t in tasks:
            if t is not task:
                t.cancel()

        return completed

    def register(
        self,
        step_id: str,
        agent: str,
        runner_name: str,
        handle: RunnerHandle,
        worktree_path: str,
    ) -> None:
        """Register a newly dispatched runner.

        Architecture: docs/DRIVER-ARCHITECTURE.md Section 2.1, DriverState methods
        Blueprint: DRIVER-BLUEPRINT.md Flow 2 (state.register(...))
        """
        self.running[step_id] = RunningEntry(
            step_id=step_id,
            agent=agent,
            runner_name=runner_name,
            handle=handle,
            worktree_path=worktree_path,
            dispatched_at=time.monotonic(),
        )

    def mark_completed(self, step_id: str, status: str, output: str) -> None:
        """Move entry from running to completed_queue.

        Args:
            step_id: The step to mark completed.
            status: Runner outcome status (e.g. "success", "fail").
            output: Runner output text for evidence/error context.

        Architecture: docs/DRIVER-ARCHITECTURE.md Section 2.1, DriverState methods
        Blueprint: DRIVER-BLUEPRINT.md Flow 3 (state.mark_completed(...))

        Note:
            This is a simplified version. The full implementation would
            create a CompletedEntry from the running entry and its result.
            This method is typically called from wait_for_any().
        """
        if step_id in self.running:
            entry = self.running[step_id]
            result = RunnerResult(
                status=RunnerStatus.SUCCESS if status == "success" else RunnerStatus.FAIL,
                session_id=entry.handle.session_id,
                output=output,
                elapsed_seconds=0.0,  # Would need actual timing
                exit_code=None,
            )
            completed = CompletedEntry(
                step_id=step_id,
                agent=entry.agent,
                runner_name=entry.runner_name,
                result=result,
                worktree_path=entry.worktree_path,
                elapsed_seconds=0.0,
            )
            del self.running[step_id]
            self.completed_queue.append(completed)

    def increment_failure(self, step_id: str, runner_name: str) -> None:
        """Increment failure counters for step and runner.

        Architecture: docs/DRIVER-ARCHITECTURE.md Section 2.1, DriverState methods
        Blueprint: DRIVER-BLUEPRINT.md Flow 3 (state.increment_failure(...))
        """
        self.failure_counts[step_id] = self.failure_counts.get(step_id, 0) + 1
        self.runner_failures[(step_id, runner_name)] = (
            self.runner_failures.get((step_id, runner_name), 0) + 1
        )

    def failure_count(self, step_id: str) -> int:
        """Return consecutive failure count for a step.

        Architecture: docs/DRIVER-ARCHITECTURE.md Section 2.1, DriverState methods
        """
        return self.failure_counts.get(step_id, 0)

    def get_failure_context(self, step_id: str) -> str | None:
        """Return last failure output for prompt enrichment, or None.

        Architecture: docs/DRIVER-ARCHITECTURE.md Section 2.1, DriverState methods
        Blueprint: DRIVER-BLUEPRINT.md Flow 2 (failure_context=state.get_failure_context(...))
        """
        history = self.failure_history.get(step_id, [])
        if not history:
            return None
        return history[-1]

    def get_failure_history(self, step_id: str) -> list[str]:
        """Return all failure outputs for a step.

        Architecture: docs/DRIVER-ARCHITECTURE.md Section 2.1, DriverState methods
        Blueprint: DRIVER-BLUEPRINT.md Flow 3 (failure_history=state.get_failure_history(...))
        """
        return self.failure_history.get(step_id, [])

    def set_agent_override(self, step_id: str, agent: str) -> None:
        """Override the agent for a step (judge SWITCH_AGENT verdict).

        Architecture: docs/DRIVER-ARCHITECTURE.md Section 2.1, DriverState methods
        Blueprint: DRIVER-BLUEPRINT.md Flow 3 (state.set_agent_override(...))
        """
        self.agent_overrides[step_id] = agent

    def detect_loop(self, window: int = 10) -> bool:
        """Return True if the last `window` decide() signatures are identical.

        Architecture: docs/DRIVER-ARCHITECTURE.md Section 2.1, DriverState methods
        Blueprint: DRIVER-BLUEPRINT.md Flow 1 (if state.detect_loop(): ...)
        """
        if len(self.loop_detector) < window:
            return False
        # Check if all signatures in the window are identical
        recent = self.loop_detector[-window:]
        return len(set(recent)) == 1

    def summary(self) -> dict[str, object]:
        """Return summary dict for FINAL event.

        Architecture: docs/DRIVER-ARCHITECTURE.md Section 2.1, DriverState methods
        Blueprint: DRIVER-BLUEPRINT.md Flow 1 (observer.emit("FINAL", state.summary()))
        """
        return {
            "running_count": len(self.running),
            "completed_count": sum(
                1 for fc in self.failure_counts.values() if fc == 0
            ),  # Steps without failures
            "failure_count": len(self.failure_counts),
            "halt_requested": self.halt_requested,
        }


@dataclass
class MergeResult:
    """Outcome of a git merge operation.

    Architecture: docs/DRIVER-ARCHITECTURE.md Section 2.1, MergeResult dataclass
    Blueprint: DRIVER-BLUEPRINT.md Worktree Lifecycle (worktree.py)
    """

    status: str  # "clean" | "auto_resolved" | "conflict"
    files: list[str] = field(default_factory=list)
    trivial: bool = True


@dataclass
class SessionEntry:
    """A completed session eligible for reuse.

    Architecture: docs/DRIVER-ARCHITECTURE.md Section 2.1, SessionEntry dataclass
    Architecture: docs/DRIVER-ARCHITECTURE.md Section 2.5 (SessionPool._entries)
    Blueprint: DRIVER-BLUEPRINT.md Session Pool (session.py)
    """

    session_id: str
    runner_name: str
    agent: str
    step_id: str
    completed_at: float  # time.monotonic()


@dataclass(frozen=True)
class ReplaySafetyEnvelope:
    """Canonical replay/idempotency envelope for continuity-sensitive actions.

    Contract scope only: this type pins the minimum facts that downstream
    continuity phases must preserve when deciding whether a resumed or replayed
    action is safe to apply.
    """

    step_id: str
    attempt_key: str
    runner_name: str
    session_id: str | None
    idempotency_scope: str
    tool_call_fingerprint: str | None = None


@dataclass(frozen=True)
class ContinuityJournalEntry:
    """Minimum recovery telemetry emitted for replay-safe restart reasoning.

    This is intentionally narrower than post-bootstrap observability. It exists
    only to pin the bootstrap-minimum journal facts required by restart,
    resume, and abort handling.
    """

    step_id: str
    event_kind: str
    recorded_at: str
    attempt_key: str
    runner_name: str
    session_id: str | None
    summary: str
    replay_envelope: ReplaySafetyEnvelope


@dataclass(frozen=True)
class ContinuityLedgerEntry:
    """Durable continuity record for one step-level execution lineage.

    The ledger is the durable source of truth for resumable continuity facts.
    Downstream implementation phases own persistence mechanics; this contract
    pins the durable shape they must converge on.
    """

    step_id: str
    latest_attempt_key: str
    status: str
    runner_name: str
    last_session_id: str | None
    replay_envelope: ReplaySafetyEnvelope
    last_journal_event: ContinuityJournalEntry
    recovery_cursor: str | None = None


@dataclass(frozen=True)
class ContinuityHandoff:
    """Loop-facing continuity handoff used by restart and resume control flow.

    This type separates the durable continuity decision input from transient
    runtime process state so downstream phases do not re-derive restart policy
    from loosely structured dictionaries.
    """

    step_id: str
    resume_from_session_id: str | None
    replay_envelope: ReplaySafetyEnvelope
    ledger_entry: ContinuityLedgerEntry | None
    capability_snapshot_id: str
    reason: str


@dataclass(frozen=True)
class StartupRecoveryControllerInput:
    """Inputs required by the startup recovery controller contract."""

    orphaned_steps: tuple[str, ...]
    stale_claim_step_ids: tuple[str, ...]
    ledger_entries: tuple[ContinuityLedgerEntry, ...]
    available_capability_snapshot_ids: tuple[str, ...]


@dataclass(frozen=True)
class StartupRecoveryControllerOutput:
    """Outputs produced by the startup recovery controller contract."""

    resumable_handoffs: tuple[ContinuityHandoff, ...]
    repair_actions: tuple[str, ...]
    blocked_reasons: tuple[str, ...]


class StartupRecoveryController(Protocol):
    """Protocol for continuity-aware startup recovery planning.

    Contract only: implementation remains deferred to continuity runtime phases.
    """

    def plan_recovery(
        self, recovery_input: StartupRecoveryControllerInput
    ) -> StartupRecoveryControllerOutput:
        """Return deterministic recovery decisions from continuity inputs."""
        ...
