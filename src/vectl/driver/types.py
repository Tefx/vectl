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
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING

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
    """

    running: dict[str, RunningEntry] = field(default_factory=dict)
    # step_id -> RunningEntry

    completed_queue: list[CompletedEntry] = field(default_factory=list)
    # Drained each loop iteration

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

    def as_running_tasks(self) -> list[RunningTask]:
        """Convert to vectl.models.RunningTask list for decide() input.

        Architecture: docs/DRIVER-ARCHITECTURE.md Section 2.1, DriverState methods
        Blueprint: DRIVER-BLUEPRINT.md Flow 1 (decide_result = decide(...))
        """
        raise NotImplementedError

    def drain_completed(self) -> list[CompletedResult] | None:
        """Drain completed_queue into vectl.models.CompletedResult list for decide() input.

        Architecture: docs/DRIVER-ARCHITECTURE.md Section 2.1, DriverState methods
        Blueprint: DRIVER-BLUEPRINT.md Flow 1 (completed_results = state.drain_completed())
        """
        raise NotImplementedError

    async def wait_for_any(self) -> CompletedEntry:
        """Wait for any running handle to complete. Returns first done.

        Architecture: docs/DRIVER-ARCHITECTURE.md Section 2.1, DriverState methods
        Blueprint: DRIVER-BLUEPRINT.md Flow 1 (completed = await state.wait_for_any())
        """
        raise NotImplementedError

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
        raise NotImplementedError

    def mark_completed(self, step_id: str, status: str, output: str) -> None:
        """Move entry from running to completed_queue.

        Args:
            step_id: The step to mark completed.
            status: Runner outcome status (e.g. "success", "fail").
            output: Runner output text for evidence/error context.

        Architecture: docs/DRIVER-ARCHITECTURE.md Section 2.1, DriverState methods
        Blueprint: DRIVER-BLUEPRINT.md Flow 3 (state.mark_completed(...))
        """
        raise NotImplementedError

    def increment_failure(self, step_id: str, runner_name: str) -> None:
        """Increment failure counters for step and runner.

        Architecture: docs/DRIVER-ARCHITECTURE.md Section 2.1, DriverState methods
        Blueprint: DRIVER-BLUEPRINT.md Flow 3 (state.increment_failure(...))
        """
        raise NotImplementedError

    def failure_count(self, step_id: str) -> int:
        """Return consecutive failure count for a step.

        Architecture: docs/DRIVER-ARCHITECTURE.md Section 2.1, DriverState methods
        """
        raise NotImplementedError

    def get_failure_context(self, step_id: str) -> str | None:
        """Return last failure output for prompt enrichment, or None.

        Architecture: docs/DRIVER-ARCHITECTURE.md Section 2.1, DriverState methods
        Blueprint: DRIVER-BLUEPRINT.md Flow 2 (failure_context=state.get_failure_context(...))
        """
        raise NotImplementedError

    def get_failure_history(self, step_id: str) -> list[str]:
        """Return all failure outputs for a step.

        Architecture: docs/DRIVER-ARCHITECTURE.md Section 2.1, DriverState methods
        Blueprint: DRIVER-BLUEPRINT.md Flow 3 (failure_history=state.get_failure_history(...))
        """
        raise NotImplementedError

    def set_agent_override(self, step_id: str, agent: str) -> None:
        """Override the agent for a step (judge SWITCH_AGENT verdict).

        Architecture: docs/DRIVER-ARCHITECTURE.md Section 2.1, DriverState methods
        Blueprint: DRIVER-BLUEPRINT.md Flow 3 (state.set_agent_override(...))
        """
        raise NotImplementedError

    def detect_loop(self, window: int = 10) -> bool:
        """Return True if the last `window` decide() signatures are identical.

        Architecture: docs/DRIVER-ARCHITECTURE.md Section 2.1, DriverState methods
        Blueprint: DRIVER-BLUEPRINT.md Flow 1 (if state.detect_loop(): ...)
        """
        raise NotImplementedError

    def summary(self) -> dict[str, object]:
        """Return summary dict for FINAL event.

        Architecture: docs/DRIVER-ARCHITECTURE.md Section 2.1, DriverState methods
        Blueprint: DRIVER-BLUEPRINT.md Flow 1 (observer.emit("FINAL", state.summary()))
        """
        raise NotImplementedError


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
