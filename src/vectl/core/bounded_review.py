"""Pure bounded renderer for the MCP multi-layer review report."""

from __future__ import annotations

from invar_runtime import Contract, post, pre

from vectl.core.bounded_review_safety import render_bounded_review_safety_summary
from vectl.core.diagnostic_formatting import (
    _MAX_BOUNDED_REVIEW_ISSUE_CODEPOINTS,
    _MAX_BOUNDED_REVIEW_OUTPUT_CODEPOINTS,
    _MAX_BOUNDED_REVIEW_SCALAR_CODEPOINTS,
    ReviewScope,
    bounded_review_value,
    format_bounded_ref_detail,
    order_review_issues,
    review_issue_category,
)
from vectl.model_outputs import GateCheckResult, ReviewResult
from vectl.models import Plan, StepStatus
from vectl.semantics import is_step_locked

_PHASE_STATUS_ICON = {
    "locked": "🔒", "pending": "○", "in_progress": "▶", "done": "✓"
}
_STEP_STATUS_ICON = {
    StepStatus.PENDING: "○", StepStatus.CLAIMED: "◉", StepStatus.DONE: "✓",
    StepStatus.SKIPPED: "⊘", StepStatus.REJECTED: "✗",
}
@pre(
    Contract(
        lambda result,
        review_target=None,
        scope=None,
        max_items=1,
        phase_id=None,
        gate_result=None,
        gate_error=None,
        validation_blocked=False: isinstance(result, ReviewResult)
        and (review_target is None or isinstance(review_target, Plan))
        and (scope is None or scope in {"global", "phase", "active_dependency_closure"})
        and max_items > 0
        and (phase_id is None or isinstance(phase_id, str))
        and (gate_result is None or isinstance(gate_result, GateCheckResult))
        and (gate_error is None or isinstance(gate_error, str))
        and isinstance(validation_blocked, bool),
        "bounded review requires structured review data and a positive row budget",
    )
)
@post(
    Contract(
        lambda rendered: isinstance(rendered, str)
        and len(rendered) <= _MAX_BOUNDED_REVIEW_OUTPUT_CODEPOINTS,
        "bounded review never exceeds the fixed output safety cap",
    )
)
def render_bounded_review(
    result: ReviewResult,
    review_target: Plan,
    scope: ReviewScope,
    max_items: int,
    phase_id: str | None = None,
    gate_result: GateCheckResult | None = None,
    gate_error: str | None = None,
    validation_blocked: bool = False,
) -> str:
    """Render every variable review collection under one shared item budget.

    Each collection has at most ``max_items`` rows; L3 step dependencies, L4
    links, gate pending steps, and downstream phases use global section budgets.
    Scalars are escaped and capped at 160 codepoints, issue messages at 512,
    and the complete response at 65,536 codepoints.

    >>> from vectl.model_outputs import ReviewResult
    >>> empty = ReviewResult([], [], [], {}, 0, 0)
    >>> report = render_bounded_review(
    ...     empty, Plan(project="p"), "global", 1
    ... )
    >>> "output_safety_truncated: false" in report
    True
    >>> len(report) <= 65_536
    True
    """
    if max_items < 1:
        raise ValueError("bounded review max_items must be positive")
    if scope not in {"global", "phase", "active_dependency_closure"}:
        raise ValueError(f"Unknown review scope: {scope}")
    def boolean(value: bool) -> str:
        return str(value).lower()

    def scalar(value: str) -> tuple[str, bool]:
        return bounded_review_value(value, _MAX_BOUNDED_REVIEW_SCALAR_CODEPOINTS)
    all_issues = order_review_issues(result.validation_issues)
    rendered_issues = all_issues[:max_items]
    categories = {
        category: [
            issue for issue in all_issues if review_issue_category(issue) == category
        ]
        for category in ("current_error", "current_warning", "historical_warning")
    }
    gate_evaluated = gate_result is not None and not validation_blocked
    gate_ready = bool(gate_evaluated and gate_result and gate_result.steps_complete)
    stats: dict[str, int | bool] = {
        "current_error_count": len(categories["current_error"]),
        "current_warning_count": len(categories["current_warning"]),
        "historical_warning_count": len(categories["historical_warning"]),
        "total_issue_count": len(all_issues),
        "total_phase_row_count": len(result.phase_progress),
        "total_active_phase_count": len(result.active_phases),
        "total_active_step_count": sum(len(phase.steps) for phase in result.active_phases),
        "total_active_phase_dependency_link_count": sum(
            len(phase.depends_on) for phase in result.active_phases
        ),
        "total_active_dependency_link_count": sum(
            len(step.depends_on)
            for phase in result.active_phases
            for step in phase.steps
        ),
        "total_ref_count": len(result.ref_index),
        "total_ref_link_count": sum(
            len(step_ids) for step_ids in result.ref_index.values()
        ),
        "gate_present": phase_id is not None,
        "gate_evaluated": gate_evaluated,
        "gate_validation_blocked": validation_blocked,
        "gate_error": gate_error is not None,
        "gate_ready": gate_ready,
        "total_gate_pending_step_count": (
            len(gate_result.pending_steps) if gate_result is not None else 0
        ),
        "total_downstream_phase_count": (
            len(gate_result.downstream_locked) if gate_result is not None else 0
        ),
    }
    parts = [
        "## Bounded Review Contract\n",
        f"review_scope: {scope}",
        f"max_issues: {max_items}",
        f"review_scalar_codepoint_limit: {_MAX_BOUNDED_REVIEW_SCALAR_CODEPOINTS}",
        f"review_issue_codepoint_limit: {_MAX_BOUNDED_REVIEW_ISSUE_CODEPOINTS}",
        f"output_codepoint_limit: {_MAX_BOUNDED_REVIEW_OUTPUT_CODEPOINTS}",
        "output_safety_truncated: false",
        "\n## L1: Validation\n",
    ]

    issue_values_truncated = False
    issue_lines: list[str] = []
    for issue in rendered_issues:
        prefix = (
            "HISTORICAL WARN"
            if review_issue_category(issue) == "historical_warning"
            else "WARN"
            if issue.is_warning
            else "ERROR"
        )
        message, value_truncated = bounded_review_value(
            issue.message, _MAX_BOUNDED_REVIEW_ISSUE_CODEPOINTS
        )
        issue_values_truncated = issue_values_truncated or value_truncated
        issue_lines.append(f"{prefix}: {message}")
    if issue_lines:
        parts.extend(issue_lines)
        parts.append(f"\n{len(result.errors)} error(s), {len(result.warnings)} warning(s)")
    else:
        parts.append("✓ Plan is valid — 0 errors, 0 warnings")
    parts.extend(
        [
            "",
            f"current_error_count: {stats['current_error_count']}",
            f"current_warning_count: {stats['current_warning_count']}",
            f"historical_warning_count: {stats['historical_warning_count']}",
            f"total_issue_count: {stats['total_issue_count']}",
            f"rendered_issue_count: {len(issue_lines)}",
            f"issues_truncated: {boolean(len(issue_lines) < len(all_issues))}",
            f"issue_values_truncated: {boolean(issue_values_truncated)}",
        ]
    )
    rendered_phase_progress = result.phase_progress[:max_items]
    phase_value_truncated = False
    phase_rows: list[str] = []
    for progress in rendered_phase_progress:
        phase_id_value, truncated = scalar(progress.phase_id)
        phase_value_truncated = phase_value_truncated or truncated
        icon = _PHASE_STATUS_ICON.get(progress.status.value, "?")
        phase_rows.append(
            f"| {icon} {progress.status.value} | {phase_id_value} | "
            f"{progress.done}/{progress.total} ({progress.pct:.0f}%) |"
        )
    parts.extend(
        [
            "\n## L2: Phase Overview\n",
            f"total_phase_row_count: {len(result.phase_progress)}",
            f"rendered_phase_row_count: {len(phase_rows)}",
            f"phase_rows_truncated: {boolean(len(phase_rows) < len(result.phase_progress))}",
            f"phase_values_truncated: {boolean(phase_value_truncated)}",
            "| Status | Phase | Progress |",
            "|--------|-------|----------|",
            *phase_rows,
            f"\n**Overall: {result.total_done}/{result.total_steps} "
            f"({result.overall_pct:.0f}%)**",
        ]
    )
    rendered_active_phases = result.active_phases[:max_items]
    active_detail: list[str] = []
    active_value_truncated = False
    rendered_active_steps = 0
    remaining_step_budget = max_items
    rendered_phase_dependency_links = 0
    remaining_phase_dependency_budget = max_items
    rendered_step_dependency_links = 0
    remaining_step_dependency_budget = max_items
    for phase in rendered_active_phases:
        safe_phase_id, id_truncated = scalar(phase.id)
        safe_phase_name, name_truncated = scalar(phase.name)
        active_value_truncated = (
            active_value_truncated or id_truncated or name_truncated
        )
        active_detail.append(
            f"### {safe_phase_id} — {safe_phase_name} ({phase.status.value})"
        )
        if phase.depends_on:
            dependencies = phase.depends_on[:remaining_phase_dependency_budget]
            safe_dependencies: list[str] = []
            for dependency in dependencies:
                safe_dependency, truncated = scalar(dependency)
                active_value_truncated = active_value_truncated or truncated
                safe_dependencies.append(safe_dependency)
            dependency_count = len(dependencies)
            rendered_phase_dependency_links += dependency_count
            remaining_phase_dependency_budget -= dependency_count
            active_detail.append(
                "Deps: "
                + (
                    ", ".join(safe_dependencies)
                    if safe_dependencies
                    else "<dependencies omitted>"
                )
            )
        if phase.gate:
            safe_gate, truncated = scalar(phase.gate)
            active_value_truncated = active_value_truncated or truncated
            active_detail.append(f"Gate: {safe_gate}")
        for step in phase.steps[:remaining_step_budget]:
            safe_step_id, id_truncated = scalar(step.id)
            safe_step_name, name_truncated = scalar(step.name)
            active_value_truncated = (
                active_value_truncated or id_truncated or name_truncated
            )
            if is_step_locked(review_target, phase, step):
                icon = "🔒"
            elif step.status == StepStatus.DONE and step.verify == "expected_red":
                icon = "✓"
            else:
                icon = _STEP_STATUS_ICON.get(step.status, "?")
            claimed = ""
            if step.claimed_by:
                safe_claimed, truncated = scalar(step.claimed_by)
                active_value_truncated = active_value_truncated or truncated
                claimed = f" @{safe_claimed}"
            suggested = ""
            if step.agent:
                safe_agent, truncated = scalar(step.agent)
                active_value_truncated = active_value_truncated or truncated
                suggested = f" suggested={safe_agent}"
            dep_info = ""
            if step.depends_on:
                dependencies = step.depends_on[:remaining_step_dependency_budget]
                safe_dependencies = []
                for dependency in dependencies:
                    safe_dependency, truncated = scalar(dependency)
                    active_value_truncated = active_value_truncated or truncated
                    safe_dependencies.append(safe_dependency)
                dependency_count = len(dependencies)
                rendered_step_dependency_links += dependency_count
                remaining_step_dependency_budget -= dependency_count
                dep_info = " deps=" + (
                    ", ".join(safe_dependencies)
                    if safe_dependencies
                    else "<dependencies omitted>"
                )
            extra = (
                " [gap reproduced]"
                if step.status == StepStatus.DONE and step.verify == "expected_red"
                else ""
            )
            active_detail.append(
                f"  {icon} {safe_step_id} — {safe_step_name}"
                f"{claimed}{suggested}{dep_info}{extra}"
            )
            rendered_active_steps += 1
            remaining_step_budget -= 1
    if not active_detail:
        active_detail.append("*No active phases.*")
    total_active_steps = int(stats["total_active_step_count"])
    total_phase_dependencies = int(stats["total_active_phase_dependency_link_count"])
    total_step_dependencies = int(stats["total_active_dependency_link_count"])
    parts.extend(
        [
            "\n## L3: Active Phases\n",
            f"total_active_phase_count: {len(result.active_phases)}",
            f"rendered_active_phase_count: {len(rendered_active_phases)}",
            "active_phases_truncated: "
            f"{boolean(len(rendered_active_phases) < len(result.active_phases))}",
            f"total_active_step_count: {total_active_steps}",
            f"rendered_active_step_count: {rendered_active_steps}",
            f"active_steps_truncated: {boolean(rendered_active_steps < total_active_steps)}",
            f"total_active_phase_dependency_link_count: {total_phase_dependencies}",
            f"rendered_active_phase_dependency_link_count: {rendered_phase_dependency_links}",
            "active_phase_dependency_links_truncated: "
            f"{boolean(rendered_phase_dependency_links < total_phase_dependencies)}",
            f"total_active_dependency_link_count: {total_step_dependencies}",
            f"rendered_active_dependency_link_count: {rendered_step_dependency_links}",
            "active_dependency_links_truncated: "
            f"{boolean(rendered_step_dependency_links < total_step_dependencies)}",
            f"active_values_truncated: {boolean(active_value_truncated)}",
            *active_detail,
        ]
    )
    ref_lines = format_bounded_ref_detail(result.ref_index, max_items)
    ref_paths = sorted(result.ref_index)[:max_items]
    rendered_ref_links = 0
    remaining_ref_link_budget = max_items
    for ref_path in ref_paths:
        rendered_count = min(len(result.ref_index[ref_path]), remaining_ref_link_budget)
        rendered_ref_links += rendered_count
        remaining_ref_link_budget -= rendered_count
    stats["rendered_ref_count"] = len(ref_paths)
    stats["rendered_ref_link_count"] = rendered_ref_links
    parts.extend(["\n## L4: Spec Coverage\n", *ref_lines])
    if phase_id is not None:
        safe_gate_phase_id, phase_id_truncated = scalar(phase_id)
        gate_values_truncated = phase_id_truncated
        gate_lines = [
            f"\n## Gate Check: {safe_gate_phase_id}\n",
            f"gate_evaluated: {boolean(gate_evaluated)}",
            f"gate_validation_blocked: {boolean(validation_blocked)}",
            f"gate_error: {boolean(gate_error is not None)}",
            f"gate_ready: {boolean(gate_ready)}",
        ]
        rendered_pending_count = 0
        rendered_downstream_count = 0
        if validation_blocked:
            total_pending = len(gate_result.pending_steps) if gate_result else 0
            total_downstream = len(gate_result.downstream_locked) if gate_result else 0
            gate_lines.extend(
                [
                    f"total_gate_pending_step_count: {total_pending}",
                    "rendered_gate_pending_step_count: 0",
                    f"gate_pending_steps_truncated: {boolean(total_pending > 0)}",
                    f"total_downstream_phase_count: {total_downstream}",
                    "rendered_downstream_phase_count: 0",
                    f"downstream_phases_truncated: {boolean(total_downstream > 0)}",
                    "✗ Gate check blocked: plan validation failed. "
                    "Fix validation errors before evaluating gate readiness.",
                ]
            )
        elif gate_result is not None:
            pending_steps = gate_result.pending_steps[:max_items]
            rendered_pending_count = len(pending_steps)
            gate_lines.extend(
                [
                    f"total_gate_pending_step_count: {len(gate_result.pending_steps)}",
                    f"rendered_gate_pending_step_count: {rendered_pending_count}",
                    "gate_pending_steps_truncated: "
                    f"{boolean(rendered_pending_count < len(gate_result.pending_steps))}",
                ]
            )
            if gate_result.steps_complete:
                gate_lines.append(
                    f"✓ Steps: {gate_result.done_count}/{gate_result.total_count} complete"
                )
            else:
                gate_lines.append(
                    f"✗ Steps: {gate_result.done_count}/{gate_result.total_count} — "
                    f"{len(gate_result.pending_steps)} remaining:"
                )
                for step in pending_steps:
                    safe_step_id, id_truncated = scalar(step.id)
                    safe_step_name, name_truncated = scalar(step.name)
                    gate_values_truncated = (
                        gate_values_truncated or id_truncated or name_truncated
                    )
                    suggested = ""
                    if step.agent:
                        safe_agent, agent_truncated = scalar(step.agent)
                        gate_values_truncated = gate_values_truncated or agent_truncated
                        suggested = f" suggested={safe_agent}"
                    icon = _STEP_STATUS_ICON.get(step.status, "?")
                    gate_lines.append(
                        f"  {icon} {safe_step_id} — {safe_step_name}{suggested}"
                    )
            if gate_result.gate_criterion:
                safe_criterion, truncated = scalar(gate_result.gate_criterion)
                gate_values_truncated = gate_values_truncated or truncated
                gate_lines.append(f"Manual gate criterion: {safe_criterion}")
            if gate_result.gate_script:
                safe_script, truncated = scalar(gate_result.gate_script)
                gate_values_truncated = gate_values_truncated or truncated
                gate_lines.append(
                    f"⚠ gate_script defined ({safe_script}) but not executable via MCP. "
                    "Use CLI `vectl gate-check`."
                )
            downstream = gate_result.downstream_locked[:max_items]
            rendered_downstream_count = len(downstream)
            safe_downstream: list[str] = []
            for downstream_phase in downstream:
                safe_phase, truncated = scalar(downstream_phase)
                gate_values_truncated = gate_values_truncated or truncated
                safe_downstream.append(safe_phase)
            gate_lines.extend(
                [
                    f"total_downstream_phase_count: {len(gate_result.downstream_locked)}",
                    f"rendered_downstream_phase_count: {rendered_downstream_count}",
                    "downstream_phases_truncated: "
                    f"{boolean(rendered_downstream_count < len(gate_result.downstream_locked))}",
                ]
            )
            if safe_downstream:
                gate_lines.append("Downstream phases:")
                gate_lines.extend(f"  {phase}" for phase in safe_downstream)
        else:
            gate_lines.extend(
                [
                    "total_gate_pending_step_count: 0",
                    "rendered_gate_pending_step_count: 0",
                    "gate_pending_steps_truncated: false",
                    "total_downstream_phase_count: 0",
                    "rendered_downstream_phase_count: 0",
                    "downstream_phases_truncated: false",
                ]
            )
            if gate_error is not None:
                safe_error, truncated = scalar(gate_error)
                gate_values_truncated = gate_values_truncated or truncated
                gate_lines.append(f"**Gate Check Error:** {safe_error}")
        gate_lines.append(f"gate_values_truncated: {boolean(gate_values_truncated)}")
        stats["rendered_gate_pending_step_count"] = rendered_pending_count
        stats["rendered_downstream_phase_count"] = rendered_downstream_count
        parts.extend(gate_lines)
    report = "\n".join(parts)
    if len(report) <= _MAX_BOUNDED_REVIEW_OUTPUT_CODEPOINTS:
        return report

    return render_bounded_review_safety_summary(stats, result, scope, max_items)
