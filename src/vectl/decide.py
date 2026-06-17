"""Decision logic for vectl automation/dispatch advice."""

from __future__ import annotations

import time
from typing import Literal, cast

from vectl.core import auto_unlock_phases, get_next_steps
from vectl.decision_state import DecideState
from vectl.io import load_plan_definition
from vectl.models import Action, CompletedResult, DecideOutput, Decision, PhaseStatus, Plan, RunningTask, StepStatus
from vectl.plan_path import resolve_plan_path

REUSE_TTL: int = 300
ESCALATION_THRESHOLD: int = 3
_legacy_state: DecideState = DecideState()
DECIDE_RUNTIME_CONTEXT_BOUNDARY: str = (
    "RuntimeContext rollout does not move decide-local ownership into handlers; "
    "decide() remains the mutation boundary for DecideState passed explicitly."
)
_Status = Literal["dispatch", "wait", "blocked", "done"]
_Reason = Literal[
    "dispatch_available", "waiting_on_running", "capacity_full", "no_executable_steps", "repeated_failures"
]


def _resolve_state(state: DecideState | None) -> DecideState:
    return state if state is not None else DecideState()


def should_reuse_session(step_id: str, parent_step_id: str | None, state: DecideState | None = None) -> tuple[bool, str | None]:
    """Return whether ``step_id`` can reuse its single parent's session token."""
    _ = step_id
    if parent_step_id is None:
        return (False, None)
    token = _resolve_state(state).reusable_session(parent_step_id=parent_step_id, now=time.time(), reuse_ttl=REUSE_TTL)
    return (token is not None, token)


def _reuse_runner_for_parent(parent_step_id: str | None, state: DecideState) -> str | None:
    """Return authoritative reuse runner provenance, with legacy token-only fallback."""
    if parent_step_id is None:
        return None
    runner = state.reusable_session_runner(parent_step_id=parent_step_id, now=time.time(), reuse_ttl=REUSE_TTL)
    if runner is not None:
        return runner
    token = state.reusable_session(parent_step_id=parent_step_id, now=time.time(), reuse_ttl=REUSE_TTL)
    return "task" if token is not None else None


def _compute_status(plan: Plan, running_count: int, max_parallelism: int) -> tuple[_Status, _Reason, str]:
    claimable = get_next_steps(plan)
    capacity = max_parallelism - running_count
    if capacity > 0 and claimable:
        return ("dispatch", "dispatch_available", "Executable work available and capacity open.")
    if capacity <= 0:
        return ("wait", "capacity_full", "Parallelism limit reached, waiting for running tasks.")
    if running_count > 0:
        return ("wait", "waiting_on_running", "Work in flight, waiting for completion.")
    return ("done", "no_executable_steps", "No executable steps available.")


def _plan_step_ids(plan: Plan) -> set[str]:
    return {step.id for phase in plan.phases for step in phase.steps}


def _filtered_map(raw: object, step_ids: set[str]) -> dict[str, object]:
    source = raw if isinstance(raw, dict) else {}
    return {step_id: value for step_id, value in source.items() if step_id in step_ids}


def _state_from_advisor(advisor_state: dict[str, object] | None, step_ids: set[str]) -> DecideState:
    if advisor_state is None:
        return DecideState()
    pending = {
        step_id: reason for step_id, reason in _filtered_map(advisor_state.get("pending_escalations", {}), step_ids).items()
        if reason == "repeated_failures"
    }
    return DecideState(
        completion_times=cast(dict[str, float], _filtered_map(advisor_state.get("completion_times", {}), step_ids)),
        session_registry=cast(dict[str, str], _filtered_map(advisor_state.get("session_registry", {}), step_ids)),
        session_runner_registry=cast(dict[str, str], _filtered_map(advisor_state.get("session_runner_registry", {}), step_ids)),
        failure_counts=cast(dict[str, int], _filtered_map(advisor_state.get("failure_counts", {}), step_ids)),
        pending_escalations=cast(dict[str, str], pending),
    )


def _apply_advisory_completions(plan: Plan, completion_times: dict[str, float]) -> None:
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
    if completion_times:
        auto_unlock_phases(plan)


def _record_success(actions: list[Action], log: list[Decision], state: DecideState, result: CompletedResult) -> None:
    actions.append(Action(action="complete", step_id=result.step_id, evidence=result.output_summary))
    preview = result.output_summary[:100] if result.output_summary else "no output"
    log.append(Decision(decision="COMPLETE", step_id=result.step_id, why=f"Step completed successfully with output: {preview}"))
    state.reset_failure(step_id=result.step_id)
    state.clear_pending_escalation(step_id=result.step_id)


def _record_expected_red(actions: list[Action], log: list[Decision], state: DecideState, result: CompletedResult) -> None:
    actions.append(Action(action="complete", step_id=result.step_id, evidence=result.output_summary))
    log.append(Decision(decision="COMPLETE", step_id=result.step_id, why="Expected-red: red outcome demonstrates intended gap"))
    state.reset_failure(step_id=result.step_id)
    state.clear_pending_escalation(step_id=result.step_id)


def _record_failure(actions: list[Action], log: list[Decision], state: DecideState, result: CompletedResult) -> bool:
    count = state.register_failure(step_id=result.step_id)
    if count < ESCALATION_THRESHOLD:
        log.append(Decision(decision="WAIT", step_id=result.step_id, why=f"Step failed ({count} attempts), waiting for retry"))
        return False
    state.mark_pending_escalation(step_id=result.step_id, reason_code="repeated_failures")
    actions.append(Action(action="escalate", step_id=result.step_id, reason=f"Step failed {count} times consecutively", context=result.output_summary))
    log.append(Decision(decision="ESCALATE", step_id=result.step_id, why=f"Escalating after {count} consecutive failures"))
    state.reset_failure(step_id=result.step_id)
    return True


def _process_completed(plan: Plan, completed: list[CompletedResult] | None, state: DecideState) -> tuple[list[Action], list[Decision], bool]:
    actions: list[Action] = []
    log: list[Decision] = []
    repeated = False
    for result in completed or []:
        state.completion_times[result.step_id] = time.time()
        state.session_runner_registry[result.step_id] = result.runner
        if result.status == "SUCCESS":
            _record_success(actions, log, state, result)
        elif result.status == "FAIL" and (found := plan.find_step(result.step_id)) and found[1].verify == "expected_red":
            _record_expected_red(actions, log, state, result)
        elif result.status == "FAIL":
            repeated = _record_failure(actions, log, state, result) or repeated
    return actions, log, repeated


def _apply_complete_actions(plan: Plan, actions: list[Action]) -> None:
    for action in actions:
        if action.action != "complete" or not action.step_id:
            continue
        found = plan.find_step(action.step_id)
        if found is None:
            continue
        phase, step = found
        step.status = StepStatus.DONE
        if all(s.status in (StepStatus.DONE, StepStatus.SKIPPED) for s in phase.steps):
            phase.status = PhaseStatus.DONE
            auto_unlock_phases(plan)


def _append_dispatch_actions(plan: Plan, state: DecideState, capacity: int, actions: list[Action], log: list[Decision]) -> None:
    for step in get_next_steps(plan)[:capacity]:
        parent = step.depends_on[0] if len(step.depends_on) == 1 else None
        should_reuse, token = should_reuse_session(step.id, parent, state=state)
        actions.append(Action(
            action="claim_and_dispatch", step_id=step.id, agent=step.agent, task_id=None,
            reuse_token=token if should_reuse else None,
            reuse_runner=_reuse_runner_for_parent(parent, state) if should_reuse and token else None,
            step_description=step.description, step_verification=step.verification,
            step_refs=step.refs if step.refs else None,
        ))
        decision = "SESSION_REUSE" if should_reuse and token else "SESSION_FRESH"
        why = f"Reusing prior runner token {token} from parent {parent}" if should_reuse and token else ("No session to reuse" if parent is None else "No eligible session for reuse")
        log.append(Decision(decision=decision, step_id=step.id, why=why))


def _next_state(state: DecideState, step_ids: set[str]) -> dict[str, object]:
    return {
        "completion_times": {k: v for k, v in state.completion_times.items() if k in step_ids},
        "session_registry": {k: v for k, v in state.session_registry.items() if k in step_ids},
        "session_runner_registry": {k: v for k, v in state.session_runner_registry.items() if k in step_ids},
        "failure_counts": {k: v for k, v in state.failure_counts.items() if k in step_ids},
        "pending_escalations": {k: v for k, v in state.pending_escalations.items() if k in step_ids},
    }


def _final_status(plan: Plan, running_count: int, max_parallelism: int, repeated_failure: bool, state: DecideState) -> tuple[_Status, _Reason, str]:
    escalation = repeated_failure or any(count >= ESCALATION_THRESHOLD for count in state.failure_counts.values()) or bool(state.pending_escalations)
    if escalation:
        return ("blocked", "repeated_failures", "Repeated failures require attention/escalation handling.")
    return _compute_status(plan, running_count, max_parallelism)


def decide(running_tasks: list[RunningTask], completed_results: list[CompletedResult] | None, max_parallelism: int, advisor_state: dict[str, object] | None = None) -> DecideOutput:
    """Return deterministic automation/dispatch advisor actions and replacement state."""
    plan, _ = load_plan_definition(resolve_plan_path())
    step_ids = _plan_step_ids(plan)
    state = _state_from_advisor(advisor_state, step_ids)
    _apply_advisory_completions(plan, state.completion_times)

    actions, decision_log, repeated = _process_completed(plan, completed_results, state)
    _apply_complete_actions(plan, actions)
    running_count = len(running_tasks)
    capacity = max_parallelism - running_count
    _append_dispatch_actions(plan, state, capacity, actions, decision_log)
    if capacity <= 0 and running_count > 0:
        actions.append(Action(action="wait", reason="At max parallelism, waiting for running subagents to complete"))
    status, reason_code, message = _final_status(plan, running_count, max_parallelism, repeated, state)
    return DecideOutput(
        status=status, reason_code=reason_code, message=message, actions=actions,
        next_state=_next_state(state, step_ids),
        policy={"reuse_ttl_s": REUSE_TTL, "escalation_threshold": ESCALATION_THRESHOLD},
        decision_log=decision_log,
    )
