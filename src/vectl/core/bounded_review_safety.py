"""Metadata-only fallback for the bounded MCP review renderer."""

from __future__ import annotations

from collections.abc import Mapping

from invar_runtime import Contract, post, pre

from vectl.core.diagnostic_formatting import (
    _MAX_BOUNDED_REVIEW_ISSUE_CODEPOINTS,
    _MAX_BOUNDED_REVIEW_OUTPUT_CODEPOINTS,
    _MAX_BOUNDED_REVIEW_SCALAR_CODEPOINTS,
    ReviewScope,
)
from vectl.model_outputs import ReviewResult


@pre(
    Contract(
        lambda stats,
        result=None,
        scope=None,
        max_items=1: isinstance(stats, Mapping)
        and all(isinstance(key, str) for key in stats)
        and (result is None or isinstance(result, ReviewResult))
        and (scope is None or scope in {"global", "phase", "active_dependency_closure"})
        and max_items > 0,
        "safety fallback requires string-keyed counts and a positive item bound",
    )
)
@post(
    Contract(
        lambda rendered: isinstance(rendered, str)
        and len(rendered) <= _MAX_BOUNDED_REVIEW_OUTPUT_CODEPOINTS,
        "safety fallback is text within the fixed output cap",
    )
)
def render_bounded_review_safety_summary(
    stats: Mapping[str, int | bool],
    result: ReviewResult,
    scope: ReviewScope,
    max_items: int,
) -> str:
    """Return count-accurate metadata with zero surviving detail rows.

    >>> empty = ReviewResult([], [], [], {}, 0, 0)
    >>> summary = render_bounded_review_safety_summary({}, empty, "global", 1)
    >>> "output_safety_truncated: true" in summary
    True
    >>> "rendered_issue_count: 0" in summary
    True
    """
    if max_items < 1:
        raise ValueError("bounded review max_items must be positive")

    def count(key: str) -> int:
        return int(stats.get(key, 0))

    def flag(key: str) -> str:
        return str(bool(stats.get(key, False))).lower()

    def truncated(total: int) -> str:
        return str(total > 0).lower()

    total_issues = count("total_issue_count")
    total_phase_rows = count("total_phase_row_count")
    total_active_phases = count("total_active_phase_count")
    total_active_steps = count("total_active_step_count")
    total_phase_dependencies = count("total_active_phase_dependency_link_count")
    total_step_dependencies = count("total_active_dependency_link_count")
    total_refs = count("total_ref_count")
    total_ref_links = count("total_ref_link_count")
    total_pending = count("total_gate_pending_step_count")
    total_downstream = count("total_downstream_phase_count")
    summary = [
        "## Bounded Review Safety Summary",
        f"review_scope: {scope}",
        f"max_issues: {max_items}",
        f"review_scalar_codepoint_limit: {_MAX_BOUNDED_REVIEW_SCALAR_CODEPOINTS}",
        f"review_issue_codepoint_limit: {_MAX_BOUNDED_REVIEW_ISSUE_CODEPOINTS}",
        f"output_codepoint_limit: {_MAX_BOUNDED_REVIEW_OUTPUT_CODEPOINTS}",
        "output_safety_truncated: true",
        f"current_error_count: {count('current_error_count')}",
        f"current_warning_count: {count('current_warning_count')}",
        f"historical_warning_count: {count('historical_warning_count')}",
        f"total_issue_count: {total_issues}",
        "rendered_issue_count: 0",
        f"issues_truncated: {truncated(total_issues)}",
        "issue_values_truncated: false",
        f"total_phase_row_count: {total_phase_rows}",
        "rendered_phase_row_count: 0",
        f"phase_rows_truncated: {truncated(total_phase_rows)}",
        "phase_values_truncated: false",
        f"total_active_phase_count: {total_active_phases}",
        "rendered_active_phase_count: 0",
        f"active_phases_truncated: {truncated(total_active_phases)}",
        f"total_active_step_count: {total_active_steps}",
        "rendered_active_step_count: 0",
        f"active_steps_truncated: {truncated(total_active_steps)}",
        f"total_active_phase_dependency_link_count: {total_phase_dependencies}",
        "rendered_active_phase_dependency_link_count: 0",
        f"active_phase_dependency_links_truncated: {truncated(total_phase_dependencies)}",
        f"total_active_dependency_link_count: {total_step_dependencies}",
        "rendered_active_dependency_link_count: 0",
        f"active_dependency_links_truncated: {truncated(total_step_dependencies)}",
        "active_values_truncated: false",
        f"total_ref_count: {total_refs}",
        "rendered_ref_count: 0",
        f"refs_truncated: {truncated(total_refs)}",
        f"total_ref_link_count: {total_ref_links}",
        "rendered_ref_link_count: 0",
        f"ref_links_truncated: {truncated(total_ref_links)}",
        "ref_values_truncated: false",
        f"ref_detail_truncated: {truncated(total_refs + total_ref_links)}",
        f"gate_present: {flag('gate_present')}",
        f"gate_evaluated: {flag('gate_evaluated')}",
        f"gate_validation_blocked: {flag('gate_validation_blocked')}",
        f"gate_error: {flag('gate_error')}",
        f"gate_ready: {flag('gate_ready')}",
        f"total_gate_pending_step_count: {total_pending}",
        "rendered_gate_pending_step_count: 0",
        f"gate_pending_steps_truncated: {truncated(total_pending)}",
        f"total_downstream_phase_count: {total_downstream}",
        "rendered_downstream_phase_count: 0",
        f"downstream_phases_truncated: {truncated(total_downstream)}",
        "gate_values_truncated: false",
        f"overall_done_count: {result.total_done}",
        f"overall_step_count: {result.total_steps}",
    ]
    compact = "\n".join(summary)
    if len(compact) > _MAX_BOUNDED_REVIEW_OUTPUT_CODEPOINTS:
        raise ValueError("bounded review safety summary exceeded fixed cap")
    return compact
