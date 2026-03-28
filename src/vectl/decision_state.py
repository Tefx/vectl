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
