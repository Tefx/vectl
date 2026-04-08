"""Decision logic for vectl orchestration.

This module provides the vectl_decide algorithm for:
- Session reuse decisions
- Top-level status/reason_code signaling
- Deterministic action dispatching
"""

from __future__ import annotations

import time
from typing import Literal

from vectl.core import auto_unlock_phases, get_next_steps
from vectl.decision_state import DecideState
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

# Escalation threshold for consecutive failures
ESCALATION_THRESHOLD: int = 3

# Legacy state object retained for compatibility references in tests/tools.
# Runtime callers should pass explicit state. For state=None we now isolate by
# returning a fresh ephemeral DecideState per call.
_legacy_state: DecideState = DecideState()

DECIDE_RUNTIME_CONTEXT_BOUNDARY: str = (
    "RuntimeContext rollout does not move decide-local ownership into handlers; "
    "decide() remains the mutation boundary for DecideState passed explicitly."
)


# ---------------------------------------------------------------------------
# Function Implementations
# ---------------------------------------------------------------------------


def _resolve_state(state: DecideState | None) -> DecideState:
    """Resolve decide state with isolated fallback semantics."""
    if state is not None:
        return state
    return DecideState()


def should_reuse_session(
    step_id: str,
    parent_step_id: str | None,
    state: DecideState | None = None,
) -> tuple[bool, str | None]:
    """Determine if a session can be reused for a step.

    Args:
        step_id: The step ID being considered for dispatch.
        parent_step_id: The parent step ID (for multi-phase handoff).

    Returns:
        Tuple of (should_reuse, reuse_token_or_None):
        - (True, reuse_token) if reuse is recommended
        - (False, None) if fresh session is required

    Notes:
        ``state`` is the sole mutable-memory container for decide-side reuse
        and failure tracking. ``None`` uses a short-lived internal migration
        fallback and is not the intended runtime path.
    """
    _ = step_id
    decision_state = _resolve_state(state)

    # No parent -> cannot reuse
    if parent_step_id is None:
        return (False, None)

    reuse_token = decision_state.reusable_session(
        parent_step_id=parent_step_id,
        now=time.time(),
        reuse_ttl=REUSE_TTL,
    )
    if reuse_token is None:
        return (False, None)
    return (True, reuse_token)


def _compute_status(
    plan: Plan, running_count: int, max_parallelism: int
) -> tuple[
    Literal["dispatch", "wait", "blocked", "done"],
    Literal[
        "dispatch_available",
        "waiting_on_running",
        "capacity_full",
        "no_executable_steps",
        "repeated_failures",
    ],
    str,
]:
    """Compute top-level status and reason code.

    RFC: docs/RFC-vectl-decide-advisor-refresh.md section 6.3

    Returns:
        Tuple of (status, reason_code, message):
        - (dispatch, dispatch_available) if work available and capacity exists
        - (wait, waiting_on_running) if running tasks exist and no dispatch
        - (wait, capacity_full) if at parallelism limit
        - (done, no_executable_steps) if no claimable steps and no running
        - (blocked, repeated_failures) if repeated failures require attention
    """
    claimable = get_next_steps(plan)
    capacity = max_parallelism - running_count

    # Can dispatch new work
    if capacity > 0 and claimable:
        return ("dispatch", "dispatch_available", "Executable work available and capacity open.")

    # At capacity
    if capacity <= 0:
        return ("wait", "capacity_full", "Parallelism limit reached, waiting for running tasks.")

    # capacity > 0, claimable is empty
    if running_count > 0:
        return ("wait", "waiting_on_running", "Work in flight, waiting for completion.")
    else:
        return ("done", "no_executable_steps", "No executable steps available.")


def _plan_step_ids(plan: Plan) -> set[str]:
    """Return all step identifiers present in ``plan``."""

    return {step.id for phase in plan.phases for step in phase.steps}


def _apply_advisory_completions(plan: Plan, completion_times: dict[str, float]) -> None:
    """Apply caller-owned completion memory as in-memory advisory completions.

    This preserves the refreshed contract where caller-owned advisor state is the
    source of truth for simulated completion visibility across decide() calls.

    Args:
        plan: In-memory plan to mutate for advisory visibility only.
        completion_times: Caller-owned completion timestamp map.
    """

    if not completion_times:
        return

    for step_id in completion_times:
        found = plan.find_step(step_id)
        if found is None:
            continue
        phase, step = found
        if step.status in (StepStatus.DONE, StepStatus.SKIPPED):
            continue
        step.status = StepStatus.DONE
        if all(s.status in (StepStatus.DONE, StepStatus.SKIPPED) for s in phase.steps):
            phase.status = PhaseStatus.DONE
    auto_unlock_phases(plan)


def decide(
    running_tasks: list[RunningTask],
    completed_results: list[CompletedResult] | None,
    max_parallelism: int,
    advisor_state: dict[str, object] | None = None,
) -> DecideOutput:
    """Main decision function for vectl orchestration.

    Analyzes running tasks and completed results to determine
    what actions the orchestrator should take next.

    RFC: docs/RFC-vectl-decide-advisor-refresh.md

    Args:
        running_tasks: Currently running tasks (in-flight work).
        completed_results: Tasks that have completed since last decision.
        max_parallelism: Maximum allowed parallel dispatches.
        advisor_state: Caller-owned advisor state. If None, a fresh ephemeral
            state is used per call (isolation mode). The caller-owned state
            contract requires callers to pass their persisted state here and
            replace it with next_state from the output.

    Returns:
        DecideOutput with:
        - status / reason_code / message: top-level control summary
        - actions: list of action payloads
        - next_state: full replacement for caller-owned advisor state
        - policy: explicit policy metadata (reuse_ttl_s, escalation_threshold)
        - decision_log: optional debug/explanatory entries
    """
    # Load plan
    plan_path = resolve_plan_path()
    plan, _ = load_plan_definition(plan_path)
    step_ids = _plan_step_ids(plan)

    # Resolve caller-owned state into internal DecideState
    if advisor_state is not None:
        raw_completion = advisor_state.get("completion_times", {})
        raw_sessions = advisor_state.get("session_registry", {})
        raw_failures = advisor_state.get("failure_counts", {})
        completion_map = raw_completion if isinstance(raw_completion, dict) else {}
        session_map = raw_sessions if isinstance(raw_sessions, dict) else {}
        failure_map = raw_failures if isinstance(raw_failures, dict) else {}

        completion_times = {
            step_id: timestamp
            for step_id, timestamp in completion_map.items()
            if step_id in step_ids
        }
        session_registry = {
            step_id: token for step_id, token in session_map.items() if step_id in step_ids
        }
        failure_counts = {
            step_id: count for step_id, count in failure_map.items() if step_id in step_ids
        }
        decision_state = DecideState(
            completion_times=completion_times,
            session_registry=session_registry,
            failure_counts=failure_counts,
        )
    else:
        decision_state = DecideState()

    _apply_advisory_completions(plan, decision_state.completion_times)

    actions: list[Action] = []
    decision_log: list[Decision] = []

    # Process completed results
    if completed_results:
        for result in completed_results:
            # Record completion time for advisory visibility.
            # Do not overwrite runner reuse tokens with execution identifiers;
            # task_id is execution identity, not a reuse handle.
            decision_state.completion_times[result.step_id] = time.time()

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
                decision_state.reset_failure(step_id=result.step_id)
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
                        decision_state.reset_failure(step_id=result.step_id)
                        continue  # skip the normal FAIL path

                # Default FAIL path: must_green or verify=None
                # Track failures for escalation
                count = decision_state.register_failure(step_id=result.step_id)

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
                    decision_state.reset_failure(step_id=result.step_id)
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
        should_reuse, reuse_token = should_reuse_session(
            step.id,
            parent_step_id,
            state=decision_state,
        )

        if should_reuse and reuse_token:
            actions.append(
                Action(
                    action="claim_and_dispatch",
                    step_id=step.id,
                    agent=step.agent,
                    task_id=None,
                    reuse_token=reuse_token,
                    reuse_runner="task",
                    step_description=step.description,
                    step_verification=step.verification,
                    step_refs=step.refs if step.refs else None,
                )
            )
            decision_log.append(
                Decision(
                    decision="SESSION_REUSE",
                    step_id=step.id,
                    why=f"Reusing prior runner token {reuse_token} from parent {parent_step_id}",
                )
            )
        else:
            actions.append(
                Action(
                    action="claim_and_dispatch",
                    step_id=step.id,
                    agent=step.agent,
                    task_id=None,
                    reuse_token=None,
                    reuse_runner=None,
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

    # Compute top-level status and reason code
    # Check for repeated failures that need attention
    escalation_pending = any(
        count >= ESCALATION_THRESHOLD for count in decision_state.failure_counts.values()
    )
    if escalation_pending:
        status: Literal["dispatch", "wait", "blocked", "done"] = "blocked"
        reason_code: Literal[
            "dispatch_available",
            "waiting_on_running",
            "capacity_full",
            "no_executable_steps",
            "repeated_failures",
        ] = "repeated_failures"
        message = "Repeated failures require attention/escalation handling."
    else:
        status, reason_code, message = _compute_status(plan, running_count, max_parallelism)

    # Build next_state as full replacement for caller-owned state
    next_state: dict[str, object] = {
        "completion_times": {
            step_id: timestamp
            for step_id, timestamp in decision_state.completion_times.items()
            if step_id in step_ids
        },
        "session_registry": {
            step_id: token
            for step_id, token in decision_state.session_registry.items()
            if step_id in step_ids
        },
        "failure_counts": {
            step_id: count
            for step_id, count in decision_state.failure_counts.items()
            if step_id in step_ids
        },
    }

    # Build explicit policy metadata
    policy: dict[str, object] = {
        "reuse_ttl_s": REUSE_TTL,
        "escalation_threshold": ESCALATION_THRESHOLD,
    }

    return DecideOutput(
        status=status,
        reason_code=reason_code,
        message=message,
        actions=actions,
        next_state=next_state,
        policy=policy,
        decision_log=decision_log,
    )
