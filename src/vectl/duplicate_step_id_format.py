"""Canonical duplicate-step-id diagnostic and recommendation formatting.

Shared formatter boundary consumed by both CLI and MCP shells.
Source: docs/ARCHITECTURAL-DEMOLITION-REMEDIATION-PLAN.md DEM-003.
"""

from __future__ import annotations

from vectl.core import analyze_duplicate_step_ids, duplicate_step_id_recommendation_for_target
from vectl.models import DuplicateStepIdRepairRecommendation, Plan


def format_duplicate_step_id_diagnostics(plan: Plan) -> list[str]:
    """Return plain-text diagnostic lines for duplicate step IDs.

    Returns an empty list when there are no conflicts.
    Each line describes one conflict in a stable format usable by both
    Rich (CLI) and Markdown (MCP) presentation layers.
    """
    diagnostics = analyze_duplicate_step_ids(plan)
    if not diagnostics.conflicts:
        return []

    lines: list[str] = []
    for conflict in diagnostics.conflicts:
        phases = ", ".join(conflict.phase_ids)
        lines.append(
            f"WARN: duplicate step ID '{conflict.step_id}' appears "
            f"{conflict.occurrences} time(s) across phases: {phases}"
        )
    return lines


def format_duplicate_step_id_recommendation(
    recommendation: DuplicateStepIdRepairRecommendation,
) -> list[str]:
    """Return plain-text recommendation lines from a repair recommendation."""
    phase_list = ", ".join(duplicate.phase for duplicate in recommendation.duplicates)
    return [
        f"type={recommendation.type}; step_id={recommendation.step_id}; duplicates={phase_list}",
        f"resolution.explicit_phase: {recommendation.resolution_path.explicit_phase}",
        f"resolution.auto_migrate_flag: {recommendation.resolution_path.auto_migrate_flag}",
        f"resolution.migration_tool: {recommendation.resolution_path.migration_tool}",
    ]


def get_duplicate_step_id_recommendation(
    plan: Plan, step_id: str
) -> DuplicateStepIdRepairRecommendation | None:
    """Return the repair recommendation for a specific step ID, if any."""
    diagnostics = analyze_duplicate_step_ids(plan)
    return duplicate_step_id_recommendation_for_target(diagnostics, step_id)
