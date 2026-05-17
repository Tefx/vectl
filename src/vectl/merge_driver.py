"""Git merge driver for plan.yaml step/phase-level merges.

Source: docs/ADR-unified-state.md (Multi-user merge strategy, Phase 3).
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TypeVar

import yaml

from vectl.io import load_plan_definition
from vectl.models import Phase, Plan, PlanIOError, Step

_T = TypeVar("_T")
_MISSING = object()


@dataclass(frozen=True)
class _MergeInputs:
    ours_file: Path
    ours_raw: str
    theirs_raw: str
    base_plan: Plan
    ours_plan: Plan
    theirs_plan: Plan


@dataclass(frozen=True)
class _StepIndexes:
    base_steps: dict[str, Step]
    base_step_phase: dict[str, str]
    base_step_order: list[str]
    ours_steps: dict[str, Step]
    ours_step_phase: dict[str, str]
    ours_step_order: list[str]
    theirs_steps: dict[str, Step]
    theirs_step_phase: dict[str, str]
    theirs_step_order: list[str]


def _ordered_union(*sequences: Sequence[str]) -> list[str]:
    ordered: list[str] = []
    seen: set[str] = set()
    for sequence in sequences:
        for item in sequence:
            if item not in seen:
                seen.add(item)
                ordered.append(item)
    return ordered


def _step_payload(step: Step) -> dict[str, Any]:
    return step.model_dump(mode="json", exclude_none=False, exclude_defaults=False)


def _phase_payload(phase: Phase) -> dict[str, Any]:
    return phase.model_dump(
        mode="json", exclude_none=False, exclude_defaults=False, exclude={"steps"}
    )


def _three_way(
    base: _T | None,
    ours: _T | None,
    theirs: _T | None,
    payload: Callable[[Any], Any] | None = None,
) -> tuple[_T | None, bool]:
    def normalize(value: _T | None) -> Any:
        if value is None:
            return None
        if payload is None:
            return value
        return payload(value)

    base_n = normalize(base)
    ours_n = normalize(ours)
    theirs_n = normalize(theirs)

    if ours_n == theirs_n:
        return ours, False
    if base_n == ours_n:
        return theirs, False
    if base_n == theirs_n:
        return ours, False
    return ours, True


def _write_conflict_markers(ours_path: Path, ours_raw: str, theirs_raw: str) -> None:
    conflict_text = f"<<<<<<< ours\n{ours_raw}\n=======\n{theirs_raw}\n>>>>>>> theirs\n"
    ours_path.write_text(conflict_text, encoding="utf-8")


def _build_step_index(plan: Plan) -> tuple[dict[str, Step], dict[str, str], list[str]]:
    by_id: dict[str, Step] = {}
    phase_by_step: dict[str, str] = {}
    order: list[str] = []
    for phase in plan.phases:
        for step in phase.steps:
            by_id[step.id] = step
            phase_by_step[step.id] = phase.id
            order.append(step.id)
    return by_id, phase_by_step, order


def _step_state_signature(
    step: Step | None, phase_id: str | None
) -> tuple[dict[str, Any] | None, str | None]:
    payload = None if step is None else _step_payload(step)
    return payload, phase_id


def _three_way_mapping(
    base: dict[str, Any] | None,
    ours: dict[str, Any] | None,
    theirs: dict[str, Any] | None,
) -> tuple[dict[str, Any] | None, bool]:
    if ours == theirs:
        return ours, False
    if base == ours:
        return theirs, False
    if base == theirs:
        return ours, False
    if ours is None or theirs is None:
        return ours, True

    base_map = {} if base is None else base
    merged: dict[str, Any] = {}
    field_order = _ordered_union(list(base_map), list(ours), list(theirs))

    for field in field_order:
        base_value = base_map.get(field, _MISSING)
        ours_value = ours.get(field, _MISSING)
        theirs_value = theirs.get(field, _MISSING)

        ours_changed = ours_value != base_value
        theirs_changed = theirs_value != base_value

        if ours_changed and theirs_changed and ours_value != theirs_value:
            return ours, True

        merged_value = base_value
        if ours_changed:
            merged_value = ours_value
        elif theirs_changed:
            merged_value = theirs_value

        if merged_value is not _MISSING:
            merged[field] = merged_value

    return merged, False


def _step_state_fields(step: Step | None, phase_id: str | None) -> dict[str, Any] | None:
    if step is None:
        return None
    payload = _step_payload(step)
    payload["_phase_id"] = phase_id
    return payload


def _conflict(ours_file: Path, ours_raw: str, theirs_raw: str) -> int:
    try:
        _write_conflict_markers(ours_file, ours_raw, theirs_raw)
    except OSError:
        return 1
    return 1


def _load_merge_inputs(base_path: str, ours_path: str, theirs_path: str) -> _MergeInputs | None:
    ours_file = Path(ours_path)
    theirs_file = Path(theirs_path)

    try:
        ours_raw = ours_file.read_text(encoding="utf-8")
        theirs_raw = theirs_file.read_text(encoding="utf-8")
    except OSError:
        return None

    try:
        base_plan, _ = load_plan_definition(base_path)
        ours_plan, _ = load_plan_definition(ours_path)
        theirs_plan, _ = load_plan_definition(theirs_path)
    except PlanIOError:
        return None

    return _MergeInputs(ours_file, ours_raw, theirs_raw, base_plan, ours_plan, theirs_plan)


def _build_step_indexes(base_plan: Plan, ours_plan: Plan, theirs_plan: Plan) -> _StepIndexes:
    base_steps, base_step_phase, base_step_order = _build_step_index(base_plan)
    ours_steps, ours_step_phase, ours_step_order = _build_step_index(ours_plan)
    theirs_steps, theirs_step_phase, theirs_step_order = _build_step_index(theirs_plan)
    return _StepIndexes(
        base_steps,
        base_step_phase,
        base_step_order,
        ours_steps,
        ours_step_phase,
        ours_step_order,
        theirs_steps,
        theirs_step_phase,
        theirs_step_order,
    )


def _merge_phase_shells(
    base_plan: Plan, ours_plan: Plan, theirs_plan: Plan
) -> tuple[dict[str, Phase], list[str]] | None:
    base_phase_index = {phase.id: phase for phase in base_plan.phases}
    ours_phase_index = {phase.id: phase for phase in ours_plan.phases}
    theirs_phase_index = {phase.id: phase for phase in theirs_plan.phases}
    base_phase_order = [phase.id for phase in base_plan.phases]
    ours_phase_order = [phase.id for phase in ours_plan.phases]
    theirs_phase_order = [phase.id for phase in theirs_plan.phases]

    merged_phases_by_id: dict[str, Phase] = {}
    merged_phase_order = _ordered_union(base_phase_order, ours_phase_order, theirs_phase_order)
    for phase_id in merged_phase_order:
        merged_phase, conflict = _three_way(
            base_phase_index.get(phase_id),
            ours_phase_index.get(phase_id),
            theirs_phase_index.get(phase_id),
            payload=_phase_payload,
        )
        if conflict:
            return None
        if merged_phase is not None:
            merged_phases_by_id[phase_id] = merged_phase.model_copy(update={"steps": []}, deep=True)
    return merged_phases_by_id, merged_phase_order


def _merge_step_records(
    indexes: _StepIndexes, merged_phases_by_id: dict[str, Phase]
) -> tuple[dict[str, Step], dict[str, str]] | None:
    merged_steps: dict[str, Step] = {}
    merged_step_phase: dict[str, str] = {}
    merged_step_order = _ordered_union(
        indexes.base_step_order, indexes.ours_step_order, indexes.theirs_step_order
    )
    for step_id in merged_step_order:
        merged_step_fields, conflict = _three_way_mapping(
            _step_state_fields(indexes.base_steps.get(step_id), indexes.base_step_phase.get(step_id)),
            _step_state_fields(indexes.ours_steps.get(step_id), indexes.ours_step_phase.get(step_id)),
            _step_state_fields(
                indexes.theirs_steps.get(step_id), indexes.theirs_step_phase.get(step_id)
            ),
        )
        if conflict:
            return None
        if merged_step_fields is None:
            continue

        merged_phase_id = merged_step_fields.pop("_phase_id", None)
        if merged_phase_id is None or merged_phase_id not in merged_phases_by_id:
            return None

        try:
            merged_step = Step.model_validate(merged_step_fields)
        except ValueError:
            return None

        merged_steps[step_id] = merged_step.model_copy(deep=True)
        merged_step_phase[step_id] = merged_phase_id
    return merged_steps, merged_step_phase


def _attach_merged_steps(
    base_plan: Plan,
    ours_plan: Plan,
    theirs_plan: Plan,
    merged_phases_by_id: dict[str, Phase],
    merged_phase_order: list[str],
    merged_steps: dict[str, Step],
    merged_step_phase: dict[str, str],
) -> bool:
    base_phase_steps = {phase.id: [step.id for step in phase.steps] for phase in base_plan.phases}
    ours_phase_steps = {phase.id: [step.id for step in phase.steps] for phase in ours_plan.phases}
    theirs_phase_steps = {phase.id: [step.id for step in phase.steps] for phase in theirs_plan.phases}

    attached: set[str] = set()
    for phase_id in merged_phase_order:
        phase = merged_phases_by_id.get(phase_id)
        if phase is None:
            continue
        phase_order = _ordered_union(
            base_phase_steps.get(phase_id, []),
            ours_phase_steps.get(phase_id, []),
            theirs_phase_steps.get(phase_id, []),
        )
        for step_id in phase_order:
            if merged_step_phase.get(step_id) != phase_id or step_id in attached:
                continue
            step = merged_steps.get(step_id)
            if step is None:
                continue
            phase.steps.append(step)
            attached.add(step_id)

    for step_id in sorted(merged_steps):
        if step_id in attached:
            continue
        phase_id = merged_step_phase[step_id]
        phase = merged_phases_by_id.get(phase_id)
        if phase is None:
            return False
        phase.steps.append(merged_steps[step_id])
    return True


def _write_merged_plan(
    ours_file: Path,
    ours_plan: Plan,
    merged_phases_by_id: dict[str, Phase],
    merged_phase_order: list[str],
) -> bool:
    try:
        ours_meta = ours_plan.model_dump(mode="json", exclude_none=False, exclude={"phases"})
        merged_plan = Plan(
            **ours_meta,
            phases=[
                merged_phases_by_id[phase_id]
                for phase_id in merged_phase_order
                if phase_id in merged_phases_by_id
            ],
        )
        merged_yaml = yaml.dump(
            merged_plan.model_dump(mode="json", exclude_none=True, exclude_defaults=False),
            default_flow_style=False,
            allow_unicode=True,
            sort_keys=False,
        )
        ours_file.write_text(merged_yaml, encoding="utf-8")
    except (OSError, ValueError, TypeError):
        return False
    return True


def merge_plans(base_path: str, ours_path: str, theirs_path: str) -> int:
    """Merge plan YAML files for git merge-driver integration.

    Source: docs/ADR-unified-state.md + plan step
    ``unified-state-merge-driver.implement-merge-driver``.

    Returns:
        0 on successful auto-merge, 1 on conflict/error.
    """

    inputs = _load_merge_inputs(base_path, ours_path, theirs_path)
    if inputs is None:
        return 1

    phase_merge = _merge_phase_shells(inputs.base_plan, inputs.ours_plan, inputs.theirs_plan)
    if phase_merge is None:
        return _conflict(inputs.ours_file, inputs.ours_raw, inputs.theirs_raw)
    merged_phases_by_id, merged_phase_order = phase_merge

    indexes = _build_step_indexes(inputs.base_plan, inputs.ours_plan, inputs.theirs_plan)
    step_merge = _merge_step_records(indexes, merged_phases_by_id)
    if step_merge is None:
        return _conflict(inputs.ours_file, inputs.ours_raw, inputs.theirs_raw)
    merged_steps, merged_step_phase = step_merge

    attached = _attach_merged_steps(
        inputs.base_plan,
        inputs.ours_plan,
        inputs.theirs_plan,
        merged_phases_by_id,
        merged_phase_order,
        merged_steps,
        merged_step_phase,
    )
    if not attached:
        return _conflict(inputs.ours_file, inputs.ours_raw, inputs.theirs_raw)

    if not _write_merged_plan(
        inputs.ours_file, inputs.ours_plan, merged_phases_by_id, merged_phase_order
    ):
        return 1
    return 0
