"""Decision helpers for plan-aware orchestration control."""

from __future__ import annotations

import hashlib

from vectl.orchestration.contracts import (
    ControlDecision,
    CoreSnapshot,
    DriveRecord,
    RuntimeSnapshot,
)


def deterministic_frontier_order(step_ids: tuple[str, ...]) -> tuple[str, ...]:
    """Order claimable frontier steps deterministically."""
    return tuple(sorted(step_ids))


def count_active_step_runs(drive: DriveRecord) -> int:
    """Count active child runs considered for step capacity."""
    return len(drive.active_child_run_ids)


def synthetic_resolve_case_id(core: CoreSnapshot) -> str:
    """Generate a synthetic case identifier from unresolved core state."""

    digest = hashlib.sha256("\n".join(core.unresolved_reasons).encode("utf-8")).hexdigest()
    return f"unresolved:{digest[:16]}"


def plan_conflict_case_id(_core: CoreSnapshot) -> str:
    """Generate a synthetic case identifier for plan-complete conflicts."""
    return "plan_complete_conflict"


def blocked_steps_case_id(core: CoreSnapshot) -> str:
    """Generate a synthetic case identifier for blocked steps."""
    return f"blocked:{','.join(core.blocked_step_ids)}"


def is_runtime_idle(runtime: RuntimeSnapshot) -> bool:
    """Return whether runtime reports no active or stalled work."""
    return not (
        runtime.active_workspaces or runtime.active_executions or runtime.stalled_executions
    )


def has_active_work(core: CoreSnapshot, runtime: RuntimeSnapshot) -> bool:
    """Return whether authoritative/runtime state shows active execution."""
    return bool(
        core.in_progress_step_ids
        or runtime.active_workspaces
        or runtime.active_executions
        or runtime.stalled_executions
    )


def has_plan_complete_conflict(core: CoreSnapshot) -> bool:
    """Return True when plan-complete flag conflicts with remaining work surfaces."""
    if not core.plan_complete:
        return False
    return bool(core.claimable_step_ids or core.in_progress_step_ids or core.blocked_step_ids)


def format_unresolved_reason(core: CoreSnapshot) -> str | None:
    """Build unresolved-state explanation for ``kind='resolve'`` decisions."""
    if not core.unresolved_reasons:
        return None
    if len(core.unresolved_reasons) == 1:
        return f"Unresolved authoritative state: {core.unresolved_reasons[0]}"
    joined = "; ".join(core.unresolved_reasons)
    return f"Unresolved authoritative state: {joined}"


def validate_decision_invariants(decision: ControlDecision) -> list[str]:
    """Validate ControlDecision invariants per RFC-orch-drive.md section 9.2.1."""
    violations: list[str] = []

    if decision.kind in ("dispatch_batch", "dispatch"):
        if not decision.step_ids:
            violations.append(f"{decision.kind}: step_ids must be non-empty, got ()")
        for sid in decision.step_ids:
            if sid not in decision.role_bindings:
                violations.append(
                    f"{decision.kind}: role_bindings missing binding for step_id={sid!r}"
                )
        if decision.kind == "dispatch_batch" and decision.capacity_used <= 0:
            violations.append(
                f"dispatch_batch: capacity_used must be positive, got {decision.capacity_used}"
            )
    elif decision.kind == "resolve":
        if not decision.case_ids:
            violations.append("resolve: case_ids must be non-empty, got ()")
    elif decision.kind == "replan":
        if decision.planner_request is None:
            violations.append("replan: planner_request must be present, got None")
    elif decision.kind == "wait":
        if decision.step_ids:
            violations.append(f"wait: step_ids must be empty, got {decision.step_ids}")
        if decision.case_ids:
            violations.append(f"wait: case_ids must be empty, got {decision.case_ids}")
    elif decision.kind == "done":
        if decision.step_ids:
            violations.append(f"done: step_ids must be empty, got {decision.step_ids}")
        if decision.case_ids:
            violations.append(f"done: case_ids must be empty, got {decision.case_ids}")
    elif decision.kind == "halt" and not decision.barrier_required:
        violations.append("halt: barrier_required must be True, got False")

    return violations
