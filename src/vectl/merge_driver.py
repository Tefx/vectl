"""Git merge driver for plan.yaml step/phase-level merges.

Source: docs/ADR-unified-state.md (Multi-user merge strategy, Phase 3).
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TypeVar

import yaml
from returns.result import Failure, Result, Success

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


# @shell_orchestration: Merge-driver file keeps pure ordering helper colocated with status-code shell boundary.
def _ordered_union(*sequences: Sequence[str]) -> Result[list[str], str]:
    ordered: list[str] = []
    seen: set[str] = set()
    for sequence in sequences:
        for item in sequence:
            if item not in seen:
                seen.add(item)
                ordered.append(item)
    return Success(ordered)


# @shell_orchestration: Merge-driver file keeps pure serialization helper colocated with conflict orchestration.
def _step_payload(step: Step) -> Result[dict[str, Any], str]:
    return Success(step.model_dump(mode="json", exclude_none=False, exclude_defaults=False))


# @shell_orchestration: Merge-driver file keeps pure serialization helper colocated with conflict orchestration.
def _phase_payload(phase: Phase) -> Result[dict[str, Any], str]:
    return Success(
        phase.model_dump(
            mode="json", exclude_none=False, exclude_defaults=False, exclude={"steps"}
        )
    )


# @shell_complexity: Branches encode standard three-way merge cases and must preserve conflict behavior.
# @shell_orchestration: Three-way decision stays near merge-driver I/O to preserve conflict/status semantics.
def _three_way(
    base: _T | None,
    ours: _T | None,
    theirs: _T | None,
    payload: Callable[[Any], Result[Any, str]] | None = None,
) -> Result[tuple[_T | None, bool], str]:
    def normalize(value: _T | None) -> Result[Any, str]:
        if value is None:
            return Success(None)
        if payload is None:
            return Success(value)
        return payload(value)

    base_result = normalize(base)
    ours_result = normalize(ours)
    theirs_result = normalize(theirs)
    if isinstance(base_result, Failure):
        return Failure(base_result.failure())
    if isinstance(ours_result, Failure):
        return Failure(ours_result.failure())
    if isinstance(theirs_result, Failure):
        return Failure(theirs_result.failure())

    base_n = base_result.unwrap()
    ours_n = ours_result.unwrap()
    theirs_n = theirs_result.unwrap()

    if ours_n == theirs_n:
        return Success((ours, False))
    if base_n == ours_n:
        return Success((theirs, False))
    if base_n == theirs_n:
        return Success((ours, False))
    return Success((ours, True))


# @shell_orchestration: Private writer is intentionally tiny so caller owns Git status-code mapping.
def _write_conflict_markers(ours_path: Path, ours_raw: str, theirs_raw: str) -> Result[None, str]:
    conflict_text = f"<<<<<<< ours\n{ours_raw}\n=======\n{theirs_raw}\n>>>>>>> theirs\n"
    try:
        ours_path.write_text(conflict_text, encoding="utf-8")
    except OSError as exc:
        return Failure(str(exc))
    return Success(None)


# @shell_orchestration: Merge-driver file keeps pure indexing helper colocated with conflict orchestration.
def _build_step_index(plan: Plan) -> Result[tuple[dict[str, Step], dict[str, str], list[str]], str]:
    by_id: dict[str, Step] = {}
    phase_by_step: dict[str, str] = {}
    order: list[str] = []
    for phase in plan.phases:
        for step in phase.steps:
            by_id[step.id] = step
            phase_by_step[step.id] = phase.id
            order.append(step.id)
    return Success((by_id, phase_by_step, order))


# @shell_complexity: Branches mirror field-level three-way merge outcomes including deletions and same-field conflicts.
# @shell_orchestration: Field merge stays colocated with status-code merge driver to preserve conflict semantics.
def _three_way_mapping(
    base: dict[str, Any] | None,
    ours: dict[str, Any] | None,
    theirs: dict[str, Any] | None,
) -> Result[tuple[dict[str, Any] | None, bool], str]:
    if ours == theirs:
        return Success((ours, False))
    if base == ours:
        return Success((theirs, False))
    if base == theirs:
        return Success((ours, False))
    if ours is None or theirs is None:
        return Success((ours, True))

    base_map = {} if base is None else base
    merged: dict[str, Any] = {}
    field_order_result = _ordered_union(list(base_map), list(ours), list(theirs))
    if isinstance(field_order_result, Failure):
        return Failure(field_order_result.failure())
    field_order = field_order_result.unwrap()

    for field in field_order:
        base_value = base_map.get(field, _MISSING)
        ours_value = ours.get(field, _MISSING)
        theirs_value = theirs.get(field, _MISSING)

        ours_changed = ours_value != base_value
        theirs_changed = theirs_value != base_value

        if ours_changed and theirs_changed and ours_value != theirs_value:
            return Success((ours, True))

        merged_value = base_value
        if ours_changed:
            merged_value = ours_value
        elif theirs_changed:
            merged_value = theirs_value

        if merged_value is not _MISSING:
            merged[field] = merged_value

    return Success((merged, False))


# @shell_orchestration: Adapter keeps phase movement represented in merge-driver field conflict logic.
def _step_state_fields(step: Step | None, phase_id: str | None) -> Result[dict[str, Any] | None, str]:
    if step is None:
        return Success(None)
    payload_result = _step_payload(step)
    if isinstance(payload_result, Failure):
        return Failure(payload_result.failure())
    payload = payload_result.unwrap()
    payload["_phase_id"] = phase_id
    return Success(payload)


# @shell_orchestration: Conflict path writes markers but always returns Git-compatible conflict status.
def _conflict(ours_file: Path, ours_raw: str, theirs_raw: str) -> Result[int, str]:
    _write_conflict_markers(ours_file, ours_raw, theirs_raw)
    return Success(1)


# @shell_orchestration: Loads merge-driver file inputs while preserving missing/invalid input semantics for caller.
def _load_merge_inputs(base_path: str, ours_path: str, theirs_path: str) -> Result[_MergeInputs, str]:
    ours_file = Path(ours_path)
    theirs_file = Path(theirs_path)

    try:
        ours_raw = ours_file.read_text(encoding="utf-8")
        theirs_raw = theirs_file.read_text(encoding="utf-8")
    except OSError as exc:
        return Failure(str(exc))

    try:
        base_plan, _ = load_plan_definition(base_path)
        ours_plan, _ = load_plan_definition(ours_path)
        theirs_plan, _ = load_plan_definition(theirs_path)
    except PlanIOError as exc:
        return Failure(str(exc))

    return Success(_MergeInputs(ours_file, ours_raw, theirs_raw, base_plan, ours_plan, theirs_plan))


# @shell_orchestration: Aggregate indexes remain colocated with merge-driver conflict orchestration.
def _build_step_indexes(base_plan: Plan, ours_plan: Plan, theirs_plan: Plan) -> Result[_StepIndexes, str]:
    base_result = _build_step_index(base_plan)
    ours_result = _build_step_index(ours_plan)
    theirs_result = _build_step_index(theirs_plan)
    if isinstance(base_result, Failure):
        return Failure(base_result.failure())
    if isinstance(ours_result, Failure):
        return Failure(ours_result.failure())
    if isinstance(theirs_result, Failure):
        return Failure(theirs_result.failure())
    base_steps, base_step_phase, base_step_order = base_result.unwrap()
    ours_steps, ours_step_phase, ours_step_order = ours_result.unwrap()
    theirs_steps, theirs_step_phase, theirs_step_order = theirs_result.unwrap()
    return Success(_StepIndexes(
        base_steps,
        base_step_phase,
        base_step_order,
        ours_steps,
        ours_step_phase,
        ours_step_order,
        theirs_steps,
        theirs_step_phase,
        theirs_step_order,
    ))


# @shell_complexity: Branches preserve phase add/delete/modify conflict handling in one ordered merge pass.
# @shell_orchestration: Phase shell merge returns structured conflict signal for Git status-code caller.
def _merge_phase_shells(
    base_plan: Plan, ours_plan: Plan, theirs_plan: Plan
) -> Result[tuple[dict[str, Phase], list[str]], str]:
    base_phase_index = {phase.id: phase for phase in base_plan.phases}
    ours_phase_index = {phase.id: phase for phase in ours_plan.phases}
    theirs_phase_index = {phase.id: phase for phase in theirs_plan.phases}
    base_phase_order = [phase.id for phase in base_plan.phases]
    ours_phase_order = [phase.id for phase in ours_plan.phases]
    theirs_phase_order = [phase.id for phase in theirs_plan.phases]

    merged_phases_by_id: dict[str, Phase] = {}
    phase_order_result = _ordered_union(base_phase_order, ours_phase_order, theirs_phase_order)
    if isinstance(phase_order_result, Failure):
        return Failure(phase_order_result.failure())
    merged_phase_order = phase_order_result.unwrap()
    for phase_id in merged_phase_order:
        merge_result = _three_way(
            base_phase_index.get(phase_id),
            ours_phase_index.get(phase_id),
            theirs_phase_index.get(phase_id),
            payload=_phase_payload,
        )
        if isinstance(merge_result, Failure):
            return Failure(merge_result.failure())
        merged_phase, conflict = merge_result.unwrap()
        if conflict:
            return Failure("phase conflict")
        if merged_phase is not None:
            merged_phases_by_id[phase_id] = merged_phase.model_copy(update={"steps": []}, deep=True)
    return Success((merged_phases_by_id, merged_phase_order))


# @shell_complexity: Branches preserve step deletion, movement, validation, and conflict semantics in one merge pass.
# @shell_orchestration: Step record merge preserves conflict signaling for Git status-code caller.
def _merge_step_records(
    indexes: _StepIndexes, merged_phases_by_id: dict[str, Phase]
) -> Result[tuple[dict[str, Step], dict[str, str]], str]:
    merged_steps: dict[str, Step] = {}
    merged_step_phase: dict[str, str] = {}
    step_order_result = _ordered_union(
        indexes.base_step_order, indexes.ours_step_order, indexes.theirs_step_order
    )
    if isinstance(step_order_result, Failure):
        return Failure(step_order_result.failure())
    merged_step_order = step_order_result.unwrap()
    for step_id in merged_step_order:
        base_fields = _step_state_fields(
            indexes.base_steps.get(step_id), indexes.base_step_phase.get(step_id)
        )
        ours_fields = _step_state_fields(
            indexes.ours_steps.get(step_id), indexes.ours_step_phase.get(step_id)
        )
        theirs_fields = _step_state_fields(
            indexes.theirs_steps.get(step_id), indexes.theirs_step_phase.get(step_id)
        )
        if isinstance(base_fields, Failure):
            return Failure(base_fields.failure())
        if isinstance(ours_fields, Failure):
            return Failure(ours_fields.failure())
        if isinstance(theirs_fields, Failure):
            return Failure(theirs_fields.failure())
        merge_result = _three_way_mapping(
            base_fields.unwrap(), ours_fields.unwrap(), theirs_fields.unwrap()
        )
        if isinstance(merge_result, Failure):
            return Failure(merge_result.failure())
        merged_step_fields, conflict = merge_result.unwrap()
        if conflict:
            return Failure("step conflict")
        if merged_step_fields is None:
            continue

        merged_phase_id = merged_step_fields.pop("_phase_id", None)
        if merged_phase_id is None or merged_phase_id not in merged_phases_by_id:
            return Failure("merged step references missing phase")

        try:
            merged_step = Step.model_validate(merged_step_fields)
        except ValueError as exc:
            return Failure(str(exc))

        merged_steps[step_id] = merged_step.model_copy(deep=True)
        merged_step_phase[step_id] = merged_phase_id
    return Success((merged_steps, merged_step_phase))


# @shell_complexity: Branches preserve phase order, duplicate-attachment prevention, and fallback append semantics.
# @shell_orchestration: Attachment pass mutates merged phase shells immediately before merge-driver write boundary.
def _attach_merged_steps(
    base_plan: Plan,
    ours_plan: Plan,
    theirs_plan: Plan,
    merged_phases_by_id: dict[str, Phase],
    merged_phase_order: list[str],
    merged_steps: dict[str, Step],
    merged_step_phase: dict[str, str],
) -> Result[bool, str]:
    base_phase_steps = {phase.id: [step.id for step in phase.steps] for phase in base_plan.phases}
    ours_phase_steps = {phase.id: [step.id for step in phase.steps] for phase in ours_plan.phases}
    theirs_phase_steps = {phase.id: [step.id for step in phase.steps] for phase in theirs_plan.phases}

    attached: set[str] = set()
    for phase_id in merged_phase_order:
        phase = merged_phases_by_id.get(phase_id)
        if phase is None:
            continue
        phase_order_result = _ordered_union(
            base_phase_steps.get(phase_id, []),
            ours_phase_steps.get(phase_id, []),
            theirs_phase_steps.get(phase_id, []),
        )
        if isinstance(phase_order_result, Failure):
            return Failure(phase_order_result.failure())
        phase_order = phase_order_result.unwrap()
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
            return Success(False)
        phase.steps.append(merged_steps[step_id])
    return Success(True)


# @shell_orchestration: Final writer maps serialization and file errors into merge-driver status handling.
def _write_merged_plan(
    ours_file: Path,
    ours_plan: Plan,
    merged_phases_by_id: dict[str, Phase],
    merged_phase_order: list[str],
) -> Result[bool, str]:
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
    except (OSError, ValueError, TypeError) as exc:
        return Failure(str(exc))
    return Success(True)


# @invar:allow shell_result: Public merge-driver API must return Git-compatible integer exit status.
# @shell_complexity: Entry-point branches preserve conflict-marker writes, input errors, write errors, and success status.
# @shell_orchestration: Public entry point coordinates I/O helpers for Git's integer merge-driver contract.
def merge_plans(base_path: str, ours_path: str, theirs_path: str) -> int:
    """Merge plan YAML files for git merge-driver integration.

    Source: docs/ADR-unified-state.md + plan step
    ``unified-state-merge-driver.implement-merge-driver``.

    Returns:
        0 on successful auto-merge, 1 on conflict/error.
    """

    inputs_result = _load_merge_inputs(base_path, ours_path, theirs_path)
    if isinstance(inputs_result, Failure):
        return 1
    inputs = inputs_result.unwrap()

    phase_merge_result = _merge_phase_shells(inputs.base_plan, inputs.ours_plan, inputs.theirs_plan)
    if isinstance(phase_merge_result, Failure):
        return _conflict(inputs.ours_file, inputs.ours_raw, inputs.theirs_raw).unwrap()
    merged_phases_by_id, merged_phase_order = phase_merge_result.unwrap()

    indexes_result = _build_step_indexes(inputs.base_plan, inputs.ours_plan, inputs.theirs_plan)
    if isinstance(indexes_result, Failure):
        return _conflict(inputs.ours_file, inputs.ours_raw, inputs.theirs_raw).unwrap()
    indexes = indexes_result.unwrap()
    step_merge_result = _merge_step_records(indexes, merged_phases_by_id)
    if isinstance(step_merge_result, Failure):
        return _conflict(inputs.ours_file, inputs.ours_raw, inputs.theirs_raw).unwrap()
    merged_steps, merged_step_phase = step_merge_result.unwrap()

    attached_result = _attach_merged_steps(
        inputs.base_plan,
        inputs.ours_plan,
        inputs.theirs_plan,
        merged_phases_by_id,
        merged_phase_order,
        merged_steps,
        merged_step_phase,
    )
    if isinstance(attached_result, Failure):
        return _conflict(inputs.ours_file, inputs.ours_raw, inputs.theirs_raw).unwrap()
    attached = attached_result.unwrap()
    if not attached:
        return _conflict(inputs.ours_file, inputs.ours_raw, inputs.theirs_raw).unwrap()

    write_result = _write_merged_plan(
        inputs.ours_file, inputs.ours_plan, merged_phases_by_id, merged_phase_order
    )
    if isinstance(write_result, Failure) or not write_result.unwrap():
        return 1
    return 0
