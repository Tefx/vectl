"""Duplicate step-ID diagnostics and migration behavior."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from pathlib import Path
from typing import NamedTuple

from vectl import claims as _claims
from vectl import lifecycle as _lifecycle
from vectl.io import load_plan_definition, save_plan
from vectl.models import (
    AmbiguousMatchError, Clipboard, DiffResult, DuplicateStepIdApplyResult,
    DuplicateStepIdClaimConflict, DuplicateStepIdDependsOnRewrite,
    DuplicateStepIdDiagnostic, DuplicateStepIdDiagnostics, DuplicateStepIdDryRunReport,
    DuplicateStepIdGroup, DuplicateStepIdMigrationEvidence, DuplicateStepIdPhaseRef,
    DuplicateStepIdRenameEntry, DuplicateStepIdRepairRecommendation,
    DuplicateStepIdResolutionPath, DuplicateStepIdRetryEvidence, GateCheckResult,
    NoMatchError, Phase, PhaseChange, PhaseProgress, PhaseStatus, Plan, PlanError,
    PlanIOError, PlanValidationIssue, ReviewResult, SearchMatch, Step, StepChange,
    StepStatus, format_step_selector,
)
from vectl.semantics import is_step_locked as _is_step_locked_shared

def analyze_duplicate_step_ids(plan: Plan) -> DuplicateStepIdDiagnostics:
    """Analyze plan-wide duplicate step IDs and build repair guidance.

    Source:
        - conversation/duplicate-step-id-bug
        - conversation/half-automatic-step-id-migration

    Rollout contract for this step requires diagnostics-only behavior on
    read surfaces, including explicit, structured recommendations.
    """
    step_phase_counts: dict[str, dict[str, int]] = {}
    for phase in plan.phases:
        for step in phase.steps:
            phase_counts = step_phase_counts.setdefault(step.id, {})
            phase_counts[phase.id] = phase_counts.get(phase.id, 0) + 1

    conflicts: list[DuplicateStepIdDiagnostic] = []
    recommendations: list[DuplicateStepIdRepairRecommendation] = []

    for step_id in sorted(step_phase_counts):
        phase_counts = step_phase_counts[step_id]
        occurrences = sum(phase_counts.values())
        if occurrences <= 1:
            continue

        phase_ids = sorted(phase_counts)
        conflicts.append(
            DuplicateStepIdDiagnostic(
                step_id=step_id,
                phase_ids=phase_ids,
                occurrences=occurrences,
            )
        )
        recommendations.append(_build_duplicate_step_id_recommendation(step_id, phase_ids))

    return DuplicateStepIdDiagnostics(conflicts=conflicts, recommendations=recommendations)


def _build_duplicate_step_id_recommendation(
    step_id: str, phase_ids: list[str]
) -> DuplicateStepIdRepairRecommendation:
    """Build a structured repair recommendation for one duplicate step ID."""
    duplicates = [DuplicateStepIdPhaseRef(phase=phase_id) for phase_id in phase_ids]
    return DuplicateStepIdRepairRecommendation(
        type="duplicate-step-id",
        step_id=step_id,
        duplicates=duplicates,
        resolution_path=DuplicateStepIdResolutionPath(
            explicit_phase=(
                "Not available in phase B: targeted commands do not support "
                "phase-qualified step selectors"
            ),
            auto_migrate_flag=(
                "Not available in phase B: --auto-migrate is unsupported "
                "on targeted mutating commands"
            ),
            migration_tool="Use vectl migrate-step-id --dry-run, then --yes",
        ),
    )


def duplicate_step_id_recommendation_for_target(
    diagnostics: DuplicateStepIdDiagnostics, step_id: str
) -> DuplicateStepIdRepairRecommendation | None:
    """Return duplicate repair recommendation for a targeted step ID, if any."""
    for recommendation in diagnostics.recommendations:
        if recommendation.step_id == step_id:
            return recommendation
    return None


def require_unambiguous_target_step_id(plan: Plan, step_id: str, *, operation: str) -> None:
    """Block targeted writes when duplicate step IDs make target selection ambiguous.

    Source:
        - conversation/duplicate-step-id-bug
        - conversation/half-automatic-step-id-migration
    """
    diagnostics = analyze_duplicate_step_ids(plan)
    recommendation = duplicate_step_id_recommendation_for_target(diagnostics, step_id)
    if recommendation is None:
        return

    phases = ",".join(duplicate.phase for duplicate in recommendation.duplicates)
    raise PlanError(
        "duplicate_step_id_write_guard: blocked\n"
        "error_code=duplicate_step_id_ambiguous_target\n"
        "blocking_reason=duplicate_step_id_ambiguous\n"
        f"operation={operation}\n"
        f"step_id={recommendation.step_id}\n"
        f"recommendation.type={recommendation.type}\n"
        f"recommendation.duplicates={phases}\n"
        "recommendation.resolution_path.explicit_phase="
        f"{recommendation.resolution_path.explicit_phase}\n"
        "recommendation.resolution_path.auto_migrate_flag="
        f"{recommendation.resolution_path.auto_migrate_flag}\n"
        "recommendation.resolution_path.migration_tool="
        f"{recommendation.resolution_path.migration_tool}"
    )


def require_no_global_duplicate_step_id(plan: Plan, step_id: str, *, operation: str) -> None:
    """Reject writes that would introduce a global duplicate step ID."""
    phase_ids: list[str] = []
    for phase in plan.phases:
        if any(step.id == step_id for step in phase.steps):
            phase_ids.append(phase.id)

    if not phase_ids:
        return

    phases = ",".join(sorted(set(phase_ids)))
    raise PlanError(
        "duplicate_step_id_write_guard: blocked\n"
        "error_code=duplicate_step_id_write_blocked\n"
        "blocking_reason=duplicate_step_id_exists\n"
        f"operation={operation}\n"
        f"step_id={step_id}\n"
        f"phases={phases}\n"
        f"detail=Step ID '{step_id}' already exists"
    )


class _StepOccurrence(NamedTuple):
    phase_index: int
    step_index: int
    phase_id: str
    step_id: str
    claimed_by: str | None
    is_claimed: bool


def _collect_step_occurrences(
    plan: Plan,
) -> tuple[list[_StepOccurrence], dict[str, list[_StepOccurrence]]]:
    occurrences: list[_StepOccurrence] = []
    by_step_id: dict[str, list[_StepOccurrence]] = {}

    for phase_index, phase in enumerate(plan.phases):
        for step_index, step in enumerate(phase.steps):
            occurrence = _StepOccurrence(
                phase_index=phase_index,
                step_index=step_index,
                phase_id=phase.id,
                step_id=step.id,
                claimed_by=step.claimed_by,
                is_claimed=step.status == StepStatus.CLAIMED,
            )
            occurrences.append(occurrence)
            by_step_id.setdefault(step.id, []).append(occurrence)

    return occurrences, by_step_id


def _build_rename_entries(
    plan: Plan,
) -> tuple[list[DuplicateStepIdRenameEntry], dict[tuple[int, int], str]]:
    _, occurrences_by_step_id = _collect_step_occurrences(plan)
    existing_ids = {step.id for phase in plan.phases for step in phase.steps}
    assigned_ids = set(existing_ids)

    rename_entries: list[DuplicateStepIdRenameEntry] = []
    rename_by_location: dict[tuple[int, int], str] = {}

    for step_id, occurrences in occurrences_by_step_id.items():
        if len(occurrences) <= 1:
            continue

        for occurrence in occurrences[1:]:
            candidate = f"{occurrence.phase_id}.{step_id}"
            if candidate in assigned_ids:
                suffix = 2
                while f"{candidate}--dup{suffix}" in assigned_ids:
                    suffix += 1
                candidate = f"{candidate}--dup{suffix}"

            rename_by_location[(occurrence.phase_index, occurrence.step_index)] = candidate
            assigned_ids.add(candidate)
            rename_entries.append(
                DuplicateStepIdRenameEntry(
                    phase_id=occurrence.phase_id,
                    old_step_id=step_id,
                    new_step_id=candidate,
                    reason="noncanonical_duplicate",
                )
            )

    return rename_entries, rename_by_location


def _build_duplicate_groups(plan: Plan) -> list[DuplicateStepIdGroup]:
    _, occurrences_by_step_id = _collect_step_occurrences(plan)
    groups: list[DuplicateStepIdGroup] = []

    for step_id, occurrences in occurrences_by_step_id.items():
        if len(occurrences) <= 1:
            continue
        phases: list[str] = []
        for occurrence in occurrences:
            if occurrence.phase_id not in phases:
                phases.append(occurrence.phase_id)
        groups.append(
            DuplicateStepIdGroup(step_id=step_id, phases=phases, occurrences=len(occurrences))
        )

    return groups


def _build_claim_conflicts(
    plan: Plan,
    duplicate_step_ids: set[str],
    *,
    claims_path: Path | None,
    branch: str | None,
) -> list[DuplicateStepIdClaimConflict]:
    conflicts: dict[tuple[str, str], DuplicateStepIdClaimConflict] = {}

    for phase in plan.phases:
        for step in phase.steps:
            if step.id not in duplicate_step_ids:
                continue
            if step.status != StepStatus.CLAIMED:
                continue
            if step.claimed_by is None:
                continue
            key = (phase.id, step.id)
            conflicts[key] = DuplicateStepIdClaimConflict(
                phase_id=phase.id,
                step_id=step.id,
                claimed_by=step.claimed_by,
            )

    if claims_path is None:
        return [conflicts[key] for key in sorted(conflicts)]
    if not claims_path.exists():
        return [conflicts[key] for key in sorted(conflicts)]

    claims_branch = branch if branch is not None else _claims.get_current_branch()
    branch_claims = _claims.load_claims_for_branch(claims_path, claims_branch)

    for step_id in sorted(duplicate_step_ids):
        claim_entry = branch_claims.get(step_id)
        if claim_entry is None:
            continue
        for phase in plan.phases:
            for step in phase.steps:
                if step.id != step_id:
                    continue
                key = (phase.id, step.id)
                conflicts.setdefault(
                    key,
                    DuplicateStepIdClaimConflict(
                        phase_id=phase.id,
                        step_id=step_id,
                        claimed_by=claim_entry.agent,
                    ),
                )

    return [conflicts[key] for key in sorted(conflicts)]


def _migrated_plan_and_rewrites(
    plan: Plan,
    rename_by_location: dict[tuple[int, int], str],
) -> tuple[Plan, list[DuplicateStepIdDependsOnRewrite]]:
    migrated = plan.model_copy(deep=True)

    for (phase_index, step_index), new_step_id in rename_by_location.items():
        migrated.phases[phase_index].steps[step_index].id = new_step_id

    phase_resolution: dict[str, dict[str, str]] = {}
    for phase_index, phase in enumerate(plan.phases):
        resolved: dict[str, str] = {}
        for step_index, step in enumerate(phase.steps):
            if step.id in resolved:
                continue
            resolved[step.id] = rename_by_location.get((phase_index, step_index), step.id)
        phase_resolution[phase.id] = resolved

    rewrites: list[DuplicateStepIdDependsOnRewrite] = []
    for phase in migrated.phases:
        resolver = phase_resolution.get(phase.id)
        if resolver is None:
            resolver = {}
        for step in phase.steps:
            old_depends_on: list[str] = []
            for dep in step.depends_on:
                if dep is None:
                    continue
                old_depends_on.append(dep)
            if not old_depends_on:
                continue
            new_depends_on: list[str] = []
            for dep in old_depends_on:
                resolved_dep = resolver.get(dep, dep)
                if resolved_dep is None:
                    continue
                new_depends_on.append(resolved_dep)
            if old_depends_on == new_depends_on:
                continue
            step.depends_on = new_depends_on
            rewrites.append(
                DuplicateStepIdDependsOnRewrite(
                    phase_id=phase.id,
                    step_id=step.id,
                    old_depends_on=old_depends_on,
                    new_depends_on=new_depends_on,
                )
            )

    return migrated, rewrites


def build_duplicate_step_id_migration_dry_run(
    plan: Plan,
    *,
    claims_path: Path | None = None,
    branch: str | None = None,
) -> tuple[DuplicateStepIdDryRunReport, Plan]:
    """Build deterministic duplicate-ID migration dry-run output and candidate plan."""
    duplicate_groups = _build_duplicate_groups(plan)
    rename_map, rename_by_location = _build_rename_entries(plan)
    migrated_plan, depends_on_rewrites = _migrated_plan_and_rewrites(plan, rename_by_location)

    duplicate_step_ids = {group.step_id for group in duplicate_groups}
    claimed_conflicts = _build_claim_conflicts(
        plan,
        duplicate_step_ids,
        claims_path=claims_path,
        branch=branch,
    )

    affected_phase_ids = sorted(
        {entry.phase_id for entry in rename_map}
        | {rewrite.phase_id for rewrite in depends_on_rewrites}
    )

    report = DuplicateStepIdDryRunReport(
        run_mode="dry-run",
        duplicate_groups=duplicate_groups,
        rename_map=rename_map,
        depends_on_rewrites=depends_on_rewrites,
        affected_phases=affected_phase_ids,
        claimed_step_conflicts=claimed_conflicts,
        compatibility_notes=[
            "old_id_aliases_supported=false",
            "post_migration_old_ids_fail_with_guidance",
            "update_stored_selectors_using_rename_map",
        ],
        requires_manual_follow_up=bool(claimed_conflicts),
    )

    return report, migrated_plan


def build_duplicate_step_id_migration_evidence(
    *,
    command: str,
    command_args: list[str],
    run_mode: str,
    migrated: bool,
    report: DuplicateStepIdDryRunReport,
    retry: DuplicateStepIdRetryEvidence | None = None,
) -> DuplicateStepIdMigrationEvidence:
    """Build migration evidence payload from dry-run/apply engine outputs."""
    retry_evidence = retry or DuplicateStepIdRetryEvidence(
        attempted=False,
        succeeded=False,
        error=None,
    )
    return DuplicateStepIdMigrationEvidence(
        command=command,
        command_args=command_args,
        run_mode=run_mode,
        migrated=migrated,
        rename_map=report.rename_map,
        depends_on_rewrites=report.depends_on_rewrites,
        affected_phases=report.affected_phases,
        claimed_step_conflicts=report.claimed_step_conflicts,
        retry=retry_evidence,
    )


def apply_duplicate_step_id_migration(
    plan_path: Path,
    *,
    expected_hash: str | None = None,
    claims_path: Path | None = None,
    branch: str | None = None,
    command: str = "migrate-step-id",
    command_args: list[str] | None = None,
    run_mode: str = "apply",
    retry: DuplicateStepIdRetryEvidence | None = None,
) -> DuplicateStepIdApplyResult:
    """Apply duplicate-ID migration with CAS and claimed-step safeguards."""
    loaded_plan, loaded_hash = load_plan_definition(plan_path)
    cas_hash = loaded_hash if expected_hash is None else expected_hash

    report, migrated_plan = build_duplicate_step_id_migration_dry_run(
        loaded_plan,
        claims_path=claims_path,
        branch=branch,
    )
    if report.claimed_step_conflicts:
        conflict_lines = [
            f"{conflict.phase_id}.{conflict.step_id} claimed_by={conflict.claimed_by}"
            for conflict in report.claimed_step_conflicts
        ]
        details = "\n".join(conflict_lines)
        raise PlanError(
            "duplicate_step_id_migration_blocked\n"
            "blocking_reason=claimed_step_conflict\n"
            "unblock_guidance=unclaim_defer_or_reject_conflicted_steps_then_retry\n"
            f"claimed_step_conflicts={details}"
        )

    migrated = len(report.rename_map) > 0
    new_plan_hash: str | None = None
    if migrated:
        new_plan_hash = save_plan(
            migrated_plan,
            plan_path,
            expected_hash=cas_hash,
        )

    evidence = build_duplicate_step_id_migration_evidence(
        command=command,
        command_args=command_args or [],
        run_mode=run_mode,
        migrated=migrated,
        report=report,
        retry=retry,
    )

    return DuplicateStepIdApplyResult(
        report=report,
        evidence=evidence,
        migrated=migrated,
        new_plan_hash=new_plan_hash,
    )
