"""Mutable decide-side orchestration state container.

This module is the sole runtime owner for the decide-side mutable memory used by
session reuse and repeated-failure escalation decisions.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Final

_MIN_TIMESTAMP: Final[float] = 0.0


DECIDE_STATE_RUNTIME_BOUNDARY: Final[str] = (
    "DecideState is decide-local mutable memory. RuntimeContext may carry DriverState, "
    "but handlers must not create a second mutable decide-memory surface outside "
    "DriverState.decide_state -> vectl.decide(state=...)."
)


@dataclass
class DecideState:
    """Mutable state for ``vectl.decide`` decision memory.

    Attributes:
        completion_times: Step completion timestamp by step ID, used for
            session-reuse TTL checks.
        session_registry: Completed step -> runner-specific reuse token mapping
            for parent session reuse decisions.
        failure_counts: Consecutive failure counts by step ID for escalation
            threshold decisions.
    """

    completion_times: dict[str, float] = field(default_factory=dict)
    session_registry: dict[str, str] = field(default_factory=dict)
    failure_counts: dict[str, int] = field(default_factory=dict)

    def record_completion(self, *, step_id: str, task_id: str, completed_at: float) -> None:
        """Record completion metadata for session reuse checks.

        Args:
            step_id: Completed step identifier.
            task_id: Runner-specific reuse token associated with the completed
                step in the current decide contract.
            completed_at: Completion timestamp from ``time.time()``.

        Raises:
            ValueError: If ``completed_at`` is negative.
        """
        if completed_at < _MIN_TIMESTAMP:
            raise ValueError(f"completed_at must be >= {_MIN_TIMESTAMP}, got {completed_at}")

        self.completion_times[step_id] = completed_at
        self.session_registry[step_id] = task_id

    def reusable_session(self, *, parent_step_id: str, now: float, reuse_ttl: int) -> str | None:
        """Return reusable runner token for ``parent_step_id`` if still eligible.

        Args:
            parent_step_id: Parent step candidate for reuse.
            now: Current timestamp from ``time.time()``.
            reuse_ttl: Reuse eligibility window in seconds.

        Returns:
            Runner-specific reuse token when parent completion is within TTL and
            has a registered entry, otherwise ``None``.
        """
        completed_at = self.completion_times.get(parent_step_id)
        if completed_at is None:
            return None
        if now - completed_at > reuse_ttl:
            return None
        return self.session_registry.get(parent_step_id)

    def register_failure(self, *, step_id: str) -> int:
        """Increment and return consecutive failure count for ``step_id``.

        Args:
            step_id: Step identifier.

        Returns:
            Updated consecutive failure count.
        """
        count = self.failure_counts.get(step_id, 0) + 1
        self.failure_counts[step_id] = count
        return count

    def reset_failure(self, *, step_id: str) -> None:
        """Reset consecutive failure count for ``step_id``.

        Args:
            step_id: Step identifier.
        """
        self.failure_counts.pop(step_id, None)


__all__ = ["DECIDE_STATE_RUNTIME_BOUNDARY", "DecideState"]
