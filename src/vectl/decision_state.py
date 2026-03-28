"""Mutable decide-side orchestration state container.

This module is the sole runtime owner for the decide-side mutable memory used by
session reuse and repeated-failure escalation decisions.

Short-lived migration note:
    ``vectl.decide.decide(..., state=None)`` may temporarily route to an internal
    compatibility instance, but production driver runtime MUST pass an explicit
    ``DecideState`` from ``DriverState.decide_state``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Final

_MIN_TIMESTAMP: Final[float] = 0.0


@dataclass
class DecideState:
    """Mutable state for ``vectl.decide`` decision memory.

    Attributes:
        completion_times: Step completion timestamp by step ID, used for
            session-reuse TTL checks.
        session_registry: Completed step -> session/task ID mapping for parent
            session reuse.
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
            task_id: Session/task identifier associated with the completed step.
            completed_at: Completion timestamp from ``time.time()``.

        Raises:
            ValueError: If ``completed_at`` is negative.
        """
        if completed_at < _MIN_TIMESTAMP:
            raise ValueError(f"completed_at must be >= {_MIN_TIMESTAMP}, got {completed_at}")

        self.completion_times[step_id] = completed_at
        self.session_registry[step_id] = task_id

    def reusable_session(self, *, parent_step_id: str, now: float, reuse_ttl: int) -> str | None:
        """Return reusable session/task ID for ``parent_step_id`` if still eligible.

        Args:
            parent_step_id: Parent step candidate for reuse.
            now: Current timestamp from ``time.time()``.
            reuse_ttl: Reuse eligibility window in seconds.

        Returns:
            Session/task ID when parent completion is within TTL and has a
            registered session, otherwise ``None``.
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
