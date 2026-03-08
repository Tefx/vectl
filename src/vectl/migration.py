"""Split-state to unified-state migration helpers.

Source: plan step `unified-state-migration.implement-migration-function`
and docs/ADR-unified-state.md migration edge-case requirements.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from vectl.io import load_plan_definition, save_plan
from vectl.models import Clipboard, Phase, PlanIOError, Step

_LOGGER = logging.getLogger(__name__)

_STEP_MIGRATION_FIELDS: tuple[str, ...] = (
    "status",
    "claimed_by",
    "claimed_at",
    "done_at",
    "evidence",
    "skipped_reason",
    "rejection_reason",
    "rejection_history",
    "affinity_override",
    "affinity_override_by",
    "affinity_override_at",
)

_PHASE_MIGRATION_FIELDS: tuple[str, ...] = (
    "status",
    "evidence",
)

_STEP_OVERWRITE_WARNING_DEFAULTS: dict[str, Any] = {
    "status": "pending",
    "evidence": None,
    "done_at": None,
}


def _normalize_step_field_value(field: str, value: Any) -> Any:
    if field == "status" and hasattr(value, "value"):
        return value.value
    return value


@dataclass(frozen=True)
class MigrationResult:
    migrated_steps: int
    migrated_phases: int
    skipped_orphans: list[str]
    warnings: list[str]
    already_migrated: bool


def resolve_state_path(plan_path: Path) -> Path:
    """Resolve legacy split-state path for a plan.

    Source: docs/ADR-worktree-support.md (`resolve_state_path` used git
    common-dir for split-state) and step requirement to handle old companions.
    """
    try:
        git_result = subprocess.run(
            ["git", "rev-parse", "--git-common-dir"],
            capture_output=True,
            text=True,
            cwd=plan_path.parent,
        )
    except OSError:
        return plan_path.parent / ".vectl" / "state.json"

    if git_result.returncode != 0:
        return plan_path.parent / ".vectl" / "state.json"

    git_common_dir = Path(git_result.stdout.strip())
    if not git_common_dir.is_absolute():
        git_common_dir = plan_path.parent / git_common_dir
    return git_common_dir / "vectl" / "state.json"


def _load_state_payload(path: Path) -> dict[str, Any]:
    """Load legacy state JSON payload.

    Source: step note requires self-contained state reader logic.
    """
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise PlanIOError(f"Invalid JSON in legacy state file {path}: {exc}") from exc
    except OSError as exc:
        raise PlanIOError(f"Failed reading legacy state file {path}: {exc}") from exc

    if not isinstance(raw, dict):
        raise PlanIOError(f"Legacy state file must be a JSON object, got {type(raw).__name__}")
    return raw


def _merge_step(step: Step, step_state: dict[str, Any]) -> Step:
    merged = step.model_dump(mode="python")
    for field in _STEP_MIGRATION_FIELDS:
        if field in step_state:
            default_value = _STEP_OVERWRITE_WARNING_DEFAULTS.get(field)
            if field in _STEP_OVERWRITE_WARNING_DEFAULTS:
                inline_value = _normalize_step_field_value(field, getattr(step, field))
                state_value = _normalize_step_field_value(field, step_state[field])
                if inline_value != default_value and state_value != inline_value:
                    _LOGGER.warning(
                        "state.json overwriting inline plan.yaml step value: "
                        "step_id=%s field=%s plan_value=%r state_value=%r",
                        step.id,
                        field,
                        inline_value,
                        state_value,
                    )
            merged[field] = step_state[field]
    return Step.model_validate(merged)


def _merge_phase(phase: Phase, phase_state: dict[str, Any]) -> Phase:
    merged = phase.model_dump(mode="python")
    for field in _PHASE_MIGRATION_FIELDS:
        if field in phase_state:
            merged[field] = phase_state[field]
    return Phase.model_validate(merged)


def migrate_from_split_state(plan_path: Path) -> MigrationResult:
    """Migrate legacy split-state runtime data into plan.yaml.

    Source: plan step `unified-state-migration.implement-migration-function`.
    """
    state_path = resolve_state_path(plan_path)
    migrated_path = state_path.with_suffix(".json.migrated")

    if not state_path.exists():
        if migrated_path.exists():
            return MigrationResult(0, 0, [], [], True)
        return MigrationResult(0, 0, [], [], False)

    if migrated_path.exists():
        return MigrationResult(0, 0, [], [], True)

    state_payload = _load_state_payload(state_path)
    steps_state = state_payload.get("steps", {})
    phases_state = state_payload.get("phases", {})
    clipboard_state = state_payload.get("clipboard")

    if not isinstance(steps_state, dict):
        raise PlanIOError("Legacy state field 'steps' must be a JSON object")
    if not isinstance(phases_state, dict):
        raise PlanIOError("Legacy state field 'phases' must be a JSON object")

    if not steps_state and not phases_state and clipboard_state is None:
        state_path.unlink()
        return MigrationResult(0, 0, [], [], False)

    plan, _ = load_plan_definition(plan_path)

    phase_index: dict[str, int] = {phase.id: i for i, phase in enumerate(plan.phases)}
    step_index: dict[str, tuple[int, int]] = {}
    for phase_i, phase in enumerate(plan.phases):
        for step_i, step in enumerate(phase.steps):
            step_index[step.id] = (phase_i, step_i)

    skipped_orphans: list[str] = []
    warnings: list[str] = []
    migrated_steps = 0
    migrated_phases = 0

    for step_id, raw_step_state in steps_state.items():
        if not isinstance(raw_step_state, dict):
            raise PlanIOError(f"Legacy state for step '{step_id}' must be a JSON object")
        loc = step_index.get(step_id)
        if loc is None:
            message = f"Orphan step in state.json not present in plan.yaml: {step_id}"
            skipped_orphans.append(step_id)
            warnings.append(message)
            _LOGGER.warning(message)
            continue

        phase_i, step_i = loc
        plan.phases[phase_i].steps[step_i] = _merge_step(
            plan.phases[phase_i].steps[step_i], raw_step_state
        )
        migrated_steps += 1

    for phase_id, raw_phase_state in phases_state.items():
        if not isinstance(raw_phase_state, dict):
            raise PlanIOError(f"Legacy state for phase '{phase_id}' must be a JSON object")
        phase_pos = phase_index.get(phase_id)
        if phase_pos is None:
            message = f"Orphan phase in state.json not present in plan.yaml: {phase_id}"
            skipped_orphans.append(phase_id)
            warnings.append(message)
            _LOGGER.warning(message)
            continue

        plan.phases[phase_pos] = _merge_phase(plan.phases[phase_pos], raw_phase_state)
        migrated_phases += 1

    if clipboard_state is not None:
        if not isinstance(clipboard_state, dict):
            raise PlanIOError("Legacy state field 'clipboard' must be a JSON object")
        plan.clipboard = Clipboard.model_validate(clipboard_state)

    save_plan(
        plan,
        plan_path,
        commit_message="[vectl] migrate: merge state into plan",
    )

    try:
        os.replace(state_path, migrated_path)
    except OSError as exc:
        raise PlanIOError(
            f"Saved migrated plan but failed to rename {state_path} -> {migrated_path}: {exc}"
        ) from exc

    return MigrationResult(
        migrated_steps=migrated_steps,
        migrated_phases=migrated_phases,
        skipped_orphans=skipped_orphans,
        warnings=warnings,
        already_migrated=False,
    )
