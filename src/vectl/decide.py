"""Decision logic for vectl orchestration.

This module provides the vectl_decide algorithm for:
- Session reuse decisions
- Continuation state computation
- Deterministic action dispatching

Contract Phase: This module contains only stubs (NotImplementedError).
Implementation will be provided in decide-impl phase.
"""

from __future__ import annotations

from vectl.core import get_next_steps  # noqa: F401
from vectl.io import load_plan_definition  # noqa: F401
from vectl.models import (
    Action,  # noqa: F401
    CompletedResult,
    DecideOutput,
    Decision,  # noqa: F401
    Plan,
    RunningTask,
)

# ---------------------------------------------------------------------------
# Module-level State
# ---------------------------------------------------------------------------

# Time-to-live for session reuse eligibility (in seconds)
REUSE_TTL: int = 300

# Tracks completion times for session reuse logic
_completion_times: dict[str, float] = {}

# Maps step IDs to their session IDs for cross-agent handoff
_session_registry: dict[str, str] = {}


# ---------------------------------------------------------------------------
# Function Stubs
# ---------------------------------------------------------------------------


def should_reuse_session(step_id: str, parent_step_id: str | None) -> tuple[bool, str | None]:
    """Determine if a session can be reused for a step.

    Args:
        step_id: The step ID being considered for dispatch.
        parent_step_id: The parent step ID (for multi-phase handoff).

    Returns:
        Tuple of (should_reuse, session_id_or_None):
        - (True, session_id) if reuse is recommended
        - (False, None) if fresh session is required

    Raises:
        NotImplementedError: This is a contract stub.
    """
    raise NotImplementedError(
        "should_reuse_session is not implemented. "
        "Implementation will be provided in decide-impl phase."
    )


def compute_continuation(
    plan: Plan, running_count: int, max_parallelism: int
) -> tuple[bool, str | None]:
    """Compute whether the orchestrator should continue dispatching.

    Args:
        plan: The current plan state.
        running_count: Number of currently running tasks.
        max_parallelism: Maximum allowed parallel dispatches.

    Returns:
        Tuple of (should_continue, halt_reason):
        - (True, None) if orchestration should continue
        - (False, "NO_EXECUTABLE_STEPS") if no claimable steps
        - (False, "MAX_PARALLELISM_REACHED") if at capacity

    Raises:
        NotImplementedError: This is a contract stub.
    """
    raise NotImplementedError(
        "compute_continuation is not implemented. "
        "Implementation will be provided in decide-impl phase."
    )


def decide(
    running_tasks: list[RunningTask],
    completed_results: list[CompletedResult] | None,
    max_parallelism: int,
) -> DecideOutput:
    """Main decision function for vectl orchestration.

    Analyzes running tasks and completed results to determine
    what actions the orchestrator should take next.

    Args:
        running_tasks: Currently running tasks (in-flight work).
        completed_results: Tasks that have completed since last decision.
        max_parallelism: Maximum allowed parallel dispatches.

    Returns:
        DecideOutput with actions to execute and continuation state.

    Raises:
        NotImplementedError: This is a contract stub.
    """
    raise NotImplementedError(
        "decide is not implemented. Implementation will be provided in decide-impl phase."
    )
