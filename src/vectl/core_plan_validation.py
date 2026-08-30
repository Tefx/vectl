"""Deterministic structural Plan validation helpers.

Plan narrative fields such as descriptions, verification instructions, gates, and
completion evidence are preserved as text. They do not contribute validation
issues or alter validation scope.
"""

from __future__ import annotations

from typing import Any, Literal, TypeAlias

from vectl.core_duplicate_step_id import analyze_duplicate_step_ids
from vectl.models import PhaseStatus, Plan, PlanValidationIssue, StepStatus

Path: TypeAlias = Any
# Retained for one compatibility cycle. These values no longer affect validation.
CompletedEvidenceScope: TypeAlias = Literal["all", "active_dependency_closure"]


def _detect_cycle(graph: dict[str, list[str]]) -> list[str] | None:
    """Detect a cycle in a directed graph. Returns cycle path or None."""
    WHITE, GRAY, BLACK = 0, 1, 2
    color: dict[str, int] = {node: WHITE for node in graph}
    parent: dict[str, str | None] = {node: None for node in graph}

    def dfs(node: str) -> list[str] | None:
        color[node] = GRAY
        for neighbor in graph.get(node, []):
            if neighbor not in color:
                continue
            if color[neighbor] == GRAY:
                cycle = [neighbor, node]
                cur = node
                previous = parent[cur]
                while previous is not None and previous != neighbor:
                    cycle.append(previous)
                    cur = previous
                    previous = parent[cur]
                cycle.reverse()
                return cycle
            if color[neighbor] == WHITE:
                parent[neighbor] = node
                result = dfs(neighbor)
                if result:
                    return result
        color[node] = BLACK
        return None

    for node in graph:
        if color[node] == WHITE:
            result = dfs(node)
            if result:
                return result
    return None


def _duplicate_phase_id_issues(plan: Plan) -> list[PlanValidationIssue]:
    """Return deterministic Plan-wide duplicate phase-ID blockers."""
    issues: list[PlanValidationIssue] = []
    phase_ids: set[str] = set()
    for phase in plan.phases:
        if phase.id in phase_ids:
            issues.append(PlanValidationIssue(f"Duplicate phase ID: '{phase.id}'"))
        phase_ids.add(phase.id)
    return issues


def _duplicate_step_id_issues(plan: Plan) -> list[PlanValidationIssue]:
    """Return deterministic Plan-wide duplicate step-ID blockers."""
    issues: list[PlanValidationIssue] = []
    for conflict in analyze_duplicate_step_ids(plan).conflicts:
        conflict_phase_list = ", ".join(conflict.phase_ids)
        issues.append(
            PlanValidationIssue(
                f"Duplicate step ID '{conflict.step_id}' appears {conflict.occurrences} "
                f"times across phases: {conflict_phase_list}. "
                "Repair required before validation/gate checks can pass. "
                "Run `vectl migrate-step-id --dry-run` then "
                "`vectl migrate-step-id --yes`.",
            )
        )
    return issues


def _plan_identity_validation_issues(plan: Plan) -> list[PlanValidationIssue]:
    """Return global phase/step identity blockers for scoped validation."""
    return [*_duplicate_phase_id_issues(plan), *_duplicate_step_id_issues(plan)]


# @shell_complexity: Branches preserve phase/step identity, dependency, DAG,
# lifecycle consistency, and optional reference validation.
def validate_plan(
    plan: Plan,
    *,
    check_refs: bool = False,
    base_path: Path | None = None,
    include_completed_evidence_guard: bool = True,
    completed_evidence_scope: CompletedEvidenceScope = "all",
) -> list[PlanValidationIssue]:
    """Validate deterministic Plan structure and lifecycle consistency.

    ``include_completed_evidence_guard`` and ``completed_evidence_scope`` are
    accepted for Python API compatibility only. Vectl no longer infers Plan
    state from evidence, description, verification, or gate text.

    Args:
        plan: The plan to validate.
        check_refs: If True, check that files in refs[] exist on disk.
        base_path: Base path for resolving refs (defaults to cwd).
        include_completed_evidence_guard: Deprecated no-op compatibility option.
        completed_evidence_scope: Deprecated no-op compatibility option.
    """
    del include_completed_evidence_guard
    if completed_evidence_scope not in {"all", "active_dependency_closure"}:
        raise ValueError(f"Unknown completed evidence scope: {completed_evidence_scope}")

    issues = _duplicate_phase_id_issues(plan)
    phase_ids = {phase.id for phase in plan.phases}

    for phase in plan.phases:
        for dependency in phase.depends_on:
            if dependency not in phase_ids:
                issues.append(
                    PlanValidationIssue(
                        f"Phase '{phase.id}' depends on unknown phase '{dependency}'"
                    )
                )

    phase_cycle = _detect_cycle({phase.id: phase.depends_on for phase in plan.phases})
    if phase_cycle:
        issues.append(
            PlanValidationIssue(f"Phase DAG cycle detected: {' → '.join(phase_cycle)}")
        )

    for phase in plan.phases:
        step_ids = {step.id for step in phase.steps}
        for step in phase.steps:
            for dependency in step.depends_on:
                if dependency not in step_ids:
                    issues.append(
                        PlanValidationIssue(
                            f"Step '{step.id}' depends on unknown step '{dependency}' "
                            f"(phase '{phase.id}')"
                        )
                    )

        step_cycle = _detect_cycle({step.id: step.depends_on for step in phase.steps})
        if step_cycle:
            issues.append(
                PlanValidationIssue(
                    f"Step DAG cycle in phase '{phase.id}': {' → '.join(step_cycle)}"
                )
            )

        if phase.status == PhaseStatus.LOCKED:
            for step in phase.steps:
                if step.status not in (StepStatus.PENDING, StepStatus.SKIPPED):
                    issues.append(
                        PlanValidationIssue(
                            f"Step '{step.id}' has status '{step.status.value}' "
                            f"but phase '{phase.id}' is locked"
                        )
                    )

        if phase.status == PhaseStatus.DONE:
            for step in phase.steps:
                if step.status not in (StepStatus.DONE, StepStatus.SKIPPED):
                    issues.append(
                        PlanValidationIssue(
                            f"Phase '{phase.id}' is done but step '{step.id}' "
                            f"has status '{step.status.value}'"
                        )
                    )

    issues.extend(_duplicate_step_id_issues(plan))

    if check_refs and base_path is not None:
        for phase in plan.phases:
            for step in phase.steps:
                for ref in step.refs:
                    file_part = ref.split("#")[0]
                    try:
                        ref_exists = not file_part or (base_path / file_part).exists()
                    except OSError:
                        ref_exists = False
                    if not ref_exists:
                        issues.append(
                            PlanValidationIssue(
                                f"Step '{step.id}' ref '{ref}' — file not found",
                                is_warning=True,
                                is_historical=step.status
                                in (StepStatus.DONE, StepStatus.SKIPPED),
                            )
                        )

    return issues
