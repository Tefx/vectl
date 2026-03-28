"""Decision logic for vectl orchestration.

This module provides the vectl_decide algorithm for:
- Session reuse decisions
- Continuation state computation
- Deterministic action dispatching

Contract Phase: This module contains only stubs (NotImplementedError).
Implementation will be provided in decide-impl phase.
"""

from __future__ import annotations

import time

from vectl.core import auto_unlock_phases, get_next_steps
from vectl.io import load_plan_definition
from vectl.models import (
    Action,
    CompletedResult,
    DecideOutput,
    Decision,
    PhaseStatus,
    Plan,
    RunningTask,
    StepStatus,
)
from vectl.plan_path import resolve_plan_path

# ---------------------------------------------------------------------------
# Module-level State
# ---------------------------------------------------------------------------

# Time-to-live for session reuse eligibility (in seconds)
REUSE_TTL: int = 300

# Tracks completion times for session reuse logic
_completion_times: dict[str, float] = {}

# Maps step IDs to their session IDs for cross-agent handoff
_session_registry: dict[str, str] = {}

# Tracks failure counts per step for escalation
_failure_counts: dict[str, int] = {}


# ---------------------------------------------------------------------------
# Function Implementations
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
    """
    # No parent -> cannot reuse
    if parent_step_id is None:
        return (False, None)

    # Parent not in _completion_times -> no session to reuse
    if parent_step_id not in _completion_times:
        return (False, None)

    # Check TTL
    completion_time = _completion_times[parent_step_id]
    elapsed = time.time() - completion_time
    if elapsed > REUSE_TTL:
        return (False, None)

    # No task_id in registry -> cannot reuse
    if parent_step_id not in _session_registry:
        return (False, None)

    # Session is available for reuse
    return (True, _session_registry[parent_step_id])


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
        - (False, "WAITING_ON_RUNNING_SUBAGENTS") if waiting for running tasks
        - (False, "MAX_PARALLELISM_REACHED") if at capacity
    """
    claimable = get_next_steps(plan)
    capacity = max_parallelism - running_count

    # Can dispatch new work
    if capacity > 0 and claimable:
        return (True, None)

    # At capacity
    if capacity <= 0:
        return (False, "MAX_PARALLELISM_REACHED")

    # capacity > 0, claimable is empty
    # Have capacity but nothing to claim
    if running_count > 0:
        return (False, "WAITING_ON_RUNNING_SUBAGENTS")
    else:
        return (False, "NO_EXECUTABLE_STEPS")


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
    """
    global _completion_times, _session_registry, _failure_counts

    # Load plan
    plan_path = resolve_plan_path()
    plan, _ = load_plan_definition(plan_path)

    actions: list[Action] = []
    decision_log: list[Decision] = []

    # Process completed results
    if completed_results:
        for result in completed_results:
            # Record completion time for session reuse
            _completion_times[result.step_id] = time.time()
            _session_registry[result.step_id] = result.task_id

            if result.status == "SUCCESS":
                # Create complete action
                actions.append(
                    Action(
                        action="complete",
                        step_id=result.step_id,
                        evidence=result.output_summary,
                    )
                )
                output_preview = (
                    result.output_summary[:100] if result.output_summary else "no output"
                )
                decision_log.append(
                    Decision(
                        decision="COMPLETE",
                        step_id=result.step_id,
                        why=f"Step completed successfully with output: {output_preview}",
                    )
                )
                # Reset failure count on success
                _failure_counts.pop(result.step_id, None)
            elif result.status == "FAIL":
                # Check if this is an expected-red step (red outcome demonstrates gap)
                found = plan.find_step(result.step_id)
                if found:
                    _, step = found
                    if step.verify == "expected_red":
                        # Expected-red: FAIL result is actually a successful gap demonstration
                        actions.append(
                            Action(
                                action="complete",
                                step_id=result.step_id,
                                evidence=result.output_summary,
                            )
                        )
                        decision_log.append(
                            Decision(
                                decision="COMPLETE",
                                step_id=result.step_id,
                                why="Expected-red: red outcome demonstrates intended gap",
                            )
                        )
                        # Clear any prior failure count for this step
                        _failure_counts.pop(result.step_id, None)
                        continue  # skip the normal FAIL path

                # Default FAIL path: must_green or verify=None
                # Track failures for escalation
                count = _failure_counts.get(result.step_id, 0) + 1
                _failure_counts[result.step_id] = count

                if count >= 3:
                    # Escalate after 3 failures
                    actions.append(
                        Action(
                            action="escalate",
                            step_id=result.step_id,
                            reason=f"Step failed {count} times consecutively",
                            context=result.output_summary,
                        )
                    )
                    decision_log.append(
                        Decision(
                            decision="ESCALATE",
                            step_id=result.step_id,
                            why=f"Escalating after {count} consecutive failures",
                        )
                    )
                    # Reset count after escalation
                    _failure_counts.pop(result.step_id, None)
                else:
                    # Wait for retry (step will be re-claimable)
                    decision_log.append(
                        Decision(
                            decision="WAIT",
                            step_id=result.step_id,
                            why=f"Step failed ({count} attempts), waiting for retry",
                        )
                    )

    # Simulate completions on in-memory plan so successor tasks become visible
    # in this same decide() call, enabling parallel dispatch.
    for action in actions:
        if action.action == "complete" and action.step_id:
            found = plan.find_step(action.step_id)
            if found:
                phase, step = found
                step.status = StepStatus.DONE
                if all(s.status in (StepStatus.DONE, StepStatus.SKIPPED) for s in phase.steps):
                    phase.status = PhaseStatus.DONE
                    auto_unlock_phases(plan)

    # Get running count from tasks
    running_count = len(running_tasks)

    # Compute capacity
    capacity = max_parallelism - running_count

    # Get claimable steps
    claimable_steps = get_next_steps(plan)

    # Dispatch claim actions up to capacity
    for step in claimable_steps[:capacity]:
        # Determine parent for session reuse
        parent_step_id = None
        if len(step.depends_on) == 1:
            parent_step_id = step.depends_on[0]

        # Check session reuse
        should_reuse, task_id = should_reuse_session(step.id, parent_step_id)

        if should_reuse and task_id:
            actions.append(
                Action(
                    action="claim_and_dispatch",
                    step_id=step.id,
                    agent=step.agent,
                    session="reuse",
                    task_id=task_id,
                    step_description=step.description,
                    step_verification=step.verification,
                    step_refs=step.refs if step.refs else None,
                )
            )
            decision_log.append(
                Decision(
                    decision="SESSION_REUSE",
                    step_id=step.id,
                    why=f"Reusing session {task_id} from parent {parent_step_id}",
                )
            )
        else:
            actions.append(
                Action(
                    action="claim_and_dispatch",
                    step_id=step.id,
                    agent=step.agent,
                    session="fresh",
                    step_description=step.description,
                    step_verification=step.verification,
                    step_refs=step.refs if step.refs else None,
                )
            )
            decision_log.append(
                Decision(
                    decision="SESSION_FRESH",
                    step_id=step.id,
                    why="No session to reuse"
                    if parent_step_id is None
                    else "No eligible session for reuse",
                )
            )

    # Add wait action if at capacity
    if capacity <= 0 and running_count > 0:
        actions.append(
            Action(
                action="wait",
                reason="At max parallelism, waiting for running subagents to complete",
            )
        )

    # Compute continuation
    continuation, halt_reason = compute_continuation(plan, running_count, max_parallelism)

    return DecideOutput(
        actions=actions,
        continuation=continuation,
        halt_reason=halt_reason,
        decision_log=decision_log,
    )
