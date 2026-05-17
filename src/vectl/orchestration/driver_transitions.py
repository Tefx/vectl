"""Drive status transition and barrier-status helpers.

Authority: docs/RFC-orch-drive.md sections 8.2.1, 10.3, 10.4, 15.3.
"""

from __future__ import annotations

import uuid

from vectl.orchestration.contracts import BarrierReason, DriveStatus

DRIVE_TRANSITIONS: dict[tuple[DriveStatus, DriveStatus], str] = {
    # (from_status, to_status): reason_description
    ("running", "paused"): "operator pause",
    ("running", "resolving"): "barrier entered for non-closure",
    ("running", "replanning"): "barrier entered for replan",
    ("running", "recovering"): "recovery requested",
    ("running", "completed"): "all phases closed",
    ("running", "halted"): "hard halt decision",
    ("running", "failed_unrecoverable"): "unrecoverable invariant break",
    ("running", "blocked_operator"): "runtime retry limit reached",
    ("running", "stopped"): "operator stop",
    ("paused", "running"): "operator unpause",
    ("paused", "stopped"): "operator stop",
    ("paused", "recovering"): "recovery requested",
    ("resolving", "running"): "resolver returns unblocked and barrier clears",
    ("resolving", "resolving"): "resolver returns waiting",
    ("resolving", "blocked_operator"): "resolver returns operator_required",
    ("resolving", "halted"): "resolver returns halt",
    ("resolving", "replanning"): "resolver requests planner",
    ("replanning", "running"): "planner bundle applied and barrier clears",
    ("replanning", "blocked_operator"): "planner requests operator intervention",
    ("replanning", "halted"): "planner emits halt-worthy fatal result",
    ("replanning", "failed_unrecoverable"): "planner/adapter invariant break",
    ("blocked_operator", "running"): "operator resumes with barrier cleared",
    ("blocked_operator", "stopped"): "operator stop",
    ("blocked_operator", "recovering"): "recovery requested",
    ("recovering", "running"): "state restored and no barrier remains",
    ("recovering", "resolving"): "restored state requires resolver",
    ("recovering", "replanning"): "restored state requires planner",
    ("recovering", "blocked_operator"): "operator attention required",
    ("recovering", "failed_unrecoverable"): "unrecoverable corruption detected",
}
"""Canonical drive status transition table.

Authority: docs/RFC-orch-drive.md section 8.2.1

Any transition not in this table is invalid and MUST raise.
"""

TERMINAL_DRIVE_STATUSES: frozenset[DriveStatus] = frozenset(
    {"completed", "halted", "failed_unrecoverable", "stopped"}
)
"""Drive statuses that are terminal (no further transitions valid).

Authority: docs/RFC-orch-drive.md section 10.3
"""

DRIVE_DEFAULTS = {
    "max_parallelism": 4,
    "collect_poll_interval_seconds": 0.25,
    "resolver_child_run_timeout_seconds": 300,
    "planner_child_run_timeout_seconds": 300,
    "barrier_stabilization_wait_seconds": 120,
    "child_run_heartbeat_stale_threshold_seconds": 90,
}
"""Default configuration values for drive execution.

Authority: docs/RFC-orch-drive.md section 10.4
"""

_MAX_STALE_CHILD_FAILURES_PER_STEP = 3
"""Maximum stale child-run failures for one step before operator stop."""


# ---------------------------------------------------------------------
# Transition validation
# ---------------------------------------------------------------------


class InvalidDriveTransitionError(RuntimeError):
    """Raised when a drive status transition is not in the valid table.

    Authority: docs/RFC-orch-drive.md section 8.2.1
    """

    def __init__(
        self,
        *,
        from_status: DriveStatus,
        to_status: DriveStatus,
    ) -> None:
        self.from_status = from_status
        self.to_status = to_status
        super().__init__(f"invalid drive status transition: {from_status} -> {to_status}")


def validate_drive_transition(
    from_status: DriveStatus,
    to_status: DriveStatus,
) -> str:
    """Validate a drive status transition against the canonical table.

    Authority: docs/RFC-orch-drive.md section 8.2.1

    Args:
        from_status: Current drive status.
        to_status: Target drive status.

    Returns:
        The reason description for the valid transition.

    Raises:
        InvalidDriveTransitionError: If the transition is not valid.
    """
    key = (from_status, to_status)
    if key in DRIVE_TRANSITIONS:
        return DRIVE_TRANSITIONS[key]
    raise InvalidDriveTransitionError(from_status=from_status, to_status=to_status)


def _generate_drive_id() -> str:
    """Generate a unique drive identifier.

    Returns:
        A string of the form ``drv_<uuid4_hex>``.
    """
    return f"drv_{uuid.uuid4().hex}"


def _merge_ids(*groups: tuple[str, ...]) -> tuple[str, ...]:
    """Merge identifier tuples while preserving first-seen order."""

    merged: list[str] = []
    seen: set[str] = set()
    for group in groups:
        for value in group:
            if value in seen:
                continue
            seen.add(value)
            merged.append(value)
    return tuple(merged)


def _barrier_status(reason: BarrierReason) -> DriveStatus:
    """Map a barrier reason to the appropriate drive status for barrier re-entry.

    Authority: RFC-orch-drive.md section 8.2.1 (transition table)
    Authority: RFC-orch-drive.md section 15.3 (recovery order)

    When a barrier must be re-entered during resume or recovery, the
    target status is determined by the barrier reason. This function
    centralizes that mapping so both ``resume_drive`` and ``recover_drive``
    use the same authoritative mapping.

    Args:
        reason: The barrier reason to map.

    Returns:
        The drive status appropriate for re-entry with this barrier reason.
    """
    if reason == "operator_pause":
        return "blocked_operator"
    if reason == "planner_needed":
        return "replanning"
    # runtime_failure, merge_conflict, review_failed, recovery_gate
    # all map to "resolving" per the transition table.
    return "resolving"
