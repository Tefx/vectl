"""Git merge driver for plan.yaml step/phase-level merges.

Source: docs/ADR-unified-state.md (Multi-user merge strategy, Phase 3).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Sequence, TypeVar

import yaml

from vectl.io import load_plan_definition
from vectl.models import Phase, Plan, PlanIOError, Step

_T = TypeVar("_T")


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
    payload: Callable[[_T], Any] | None = None,
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


def merge_plans(base_path: str, ours_path: str, theirs_path: str) -> int:
    """Merge plan YAML files for git merge-driver integration.

    Source: docs/ADR-unified-state.md + plan step
    ``unified-state-merge-driver.implement-merge-driver``.

    Returns:
        0 on successful auto-merge, 1 on conflict/error.
    """

    ours_file = Path(ours_path)
    theirs_file = Path(theirs_path)

    try:
        ours_raw = ours_file.read_text(encoding="utf-8")
        theirs_raw = theirs_file.read_text(encoding="utf-8")
    except OSError:
        return 1

    try:
        base_plan, _ = load_plan_definition(base_path)
        ours_plan, _ = load_plan_definition(ours_path)
        theirs_plan, _ = load_plan_definition(theirs_path)
    except PlanIOError:
        return 1

    base_steps, base_step_phase, base_step_order = _build_step_index(base_plan)
    ours_steps, ours_step_phase, ours_step_order = _build_step_index(ours_plan)
    theirs_steps, theirs_step_phase, theirs_step_order = _build_step_index(theirs_plan)

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
            try:
                _write_conflict_markers(ours_file, ours_raw, theirs_raw)
            except OSError:
                return 1
            return 1
        if merged_phase is not None:
            merged_phases_by_id[phase_id] = merged_phase.model_copy(update={"steps": []}, deep=True)

    merged_steps: dict[str, Step] = {}
    merged_step_phase: dict[str, str] = {}
    merged_step_order = _ordered_union(base_step_order, ours_step_order, theirs_step_order)
    for step_id in merged_step_order:
        merged_step, conflict = _three_way(
            base_steps.get(step_id),
            ours_steps.get(step_id),
            theirs_steps.get(step_id),
            payload=_step_payload,
        )
        if conflict:
            try:
                _write_conflict_markers(ours_file, ours_raw, theirs_raw)
            except OSError:
                return 1
            return 1
        if merged_step is None:
            continue

        merged_phase_id, phase_conflict = _three_way(
            base_step_phase.get(step_id),
            ours_step_phase.get(step_id),
            theirs_step_phase.get(step_id),
        )
        if phase_conflict or merged_phase_id is None:
            try:
                _write_conflict_markers(ours_file, ours_raw, theirs_raw)
            except OSError:
                return 1
            return 1
        if merged_phase_id not in merged_phases_by_id:
            try:
                _write_conflict_markers(ours_file, ours_raw, theirs_raw)
            except OSError:
                return 1
            return 1

        merged_steps[step_id] = merged_step.model_copy(deep=True)
        merged_step_phase[step_id] = merged_phase_id

    base_phase_steps = {phase.id: [step.id for step in phase.steps] for phase in base_plan.phases}
    ours_phase_steps = {phase.id: [step.id for step in phase.steps] for phase in ours_plan.phases}
    theirs_phase_steps = {
        phase.id: [step.id for step in phase.steps] for phase in theirs_plan.phases
    }

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
            try:
                _write_conflict_markers(ours_file, ours_raw, theirs_raw)
            except OSError:
                return 1
            return 1
        phase.steps.append(merged_steps[step_id])

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
        return 1

    return 0
