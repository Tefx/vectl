"""Step, phase, plan metadata, and checklist mutation helpers."""

from __future__ import annotations

import re
from enum import Enum

from vectl.core_duplicate_step_id import (
    require_no_global_duplicate_step_id,
    require_unambiguous_target_step_id,
)
from vectl.core_plan_queries import _detect_cycle
from vectl.models import (
    AmbiguousMatchError,
    NoMatchError,
    Phase,
    PhaseStatus,
    Plan,
    PlanError,
    Step,
    StepStatus,
    format_step_selector,
)


class _Unset(Enum):
    """Sentinel for distinguishing 'not provided' from 'set to empty string'."""

    TOKEN = "UNSET"


_SENTINEL = _Unset.TOKEN


# @shell_complexity: Branches preserve sentinel-based partial updates, ID rewrite, dependency mutation, cycle rollback, refs, and agent clearing semantics.
def edit_step(
    plan: Plan,
    step_id: str,
    *,
    name: str | _Unset = _SENTINEL,
    description: str | _Unset = _SENTINEL,
    verification: str | _Unset = _SENTINEL,
    evidence_template: str | _Unset = _SENTINEL,
    agent: str | None | _Unset = _SENTINEL,
    add_deps: list[str] | None = None,
    remove_deps: list[str] | None = None,
    depends_on: list[str] | _Unset = _SENTINEL,
    add_refs: list[str] | None = None,
    remove_refs: list[str] | None = None,
    refs: list[str] | _Unset = _SENTINEL,
    new_step_id: str | _Unset = _SENTINEL,
) -> Plan:
    """Edit an existing step's metadata."""
    require_unambiguous_target_step_id(plan, step_id, operation="edit-step")
    found = plan.find_step(step_id)
    if found is None:
        raise PlanError(f"Step '{step_id}' not found")
    phase, step = found

    if new_step_id is not _SENTINEL:
        _apply_step_id_change(plan, phase, step, str(new_step_id))

    if name is not _SENTINEL:
        step.name = str(name)
    if description is not _SENTINEL:
        step.description = str(description)
    if verification is not _SENTINEL:
        step.verification = str(verification)
    if evidence_template is not _SENTINEL:
        step.evidence_template = str(evidence_template)
    if agent is not _SENTINEL:
        step.agent = agent if agent is None else str(agent)  # type: ignore[assignment]

    _apply_step_dependency_changes(
        phase,
        step,
        depends_on=depends_on,
        add_deps=add_deps,
        remove_deps=remove_deps,
    )
    _apply_step_ref_changes(step, refs=refs, add_refs=add_refs, remove_refs=remove_refs)

    return plan


def _apply_step_id_change(plan: Plan, phase: Phase, step: Step, target_id: str) -> None:
    """Rename a step and rewrite same-phase dependency references."""
    if target_id == step.id:
        return
    require_no_global_duplicate_step_id(plan, target_id, operation="edit-step")
    old_id = step.id
    step.id = target_id
    for dep_step in phase.steps:
        dep_step.depends_on = [target_id if dep == old_id else dep for dep in dep_step.depends_on]


def _apply_step_dependency_changes(
    phase: Phase,
    step: Step,
    *,
    depends_on: list[str] | _Unset,
    add_deps: list[str] | None,
    remove_deps: list[str] | None,
) -> None:
    """Apply dependency edits with same-phase validation and cycle rollback."""
    if depends_on is _SENTINEL and not add_deps and not remove_deps:
        return

    original_depends_on = list(step.depends_on)
    existing_step_ids = {s.id for s in phase.steps}

    if depends_on is not _SENTINEL:
        new_deps = list(depends_on)  # type: ignore[arg-type]
        for dep in new_deps:
            if dep not in existing_step_ids:
                raise PlanError(f"Step depends_on '{dep}' not found in phase '{phase.id}'")
        step.depends_on = new_deps

    if add_deps:
        for dep in add_deps:
            if dep not in existing_step_ids:
                raise PlanError(f"Step depends_on '{dep}' not found in phase '{phase.id}'")
            if dep not in step.depends_on:
                step.depends_on.append(dep)

    if remove_deps:
        for dep in remove_deps:
            if dep in step.depends_on:
                step.depends_on.remove(dep)

    cycle = _detect_cycle({s.id: s.depends_on for s in phase.steps})
    if cycle:
        step.depends_on = original_depends_on
        cycle_str = " → ".join(f"{phase.id}.{s}" for s in cycle)
        raise PlanError(f"Step dependency cycle: {cycle_str}")


def _apply_step_ref_changes(
    step: Step,
    *,
    refs: list[str] | _Unset,
    add_refs: list[str] | None,
    remove_refs: list[str] | None,
) -> None:
    """Apply direct/add/remove ref edits in CLI argument precedence order."""
    if refs is not _SENTINEL:
        step.refs = list(refs)  # type: ignore[arg-type]

    if add_refs:
        for ref in add_refs:
            if ref not in step.refs:
                step.refs.append(ref)

    if remove_refs:
        for ref in remove_refs:
            if ref in step.refs:
                step.refs.remove(ref)


# @shell_complexity: Branches preserve sentinel-based phase updates, dependency replacement/add/remove, and self-dependency validation.
def edit_phase(
    plan: Plan,
    phase_id: str,
    *,
    name: str | _Unset = _SENTINEL,
    context: str | _Unset = _SENTINEL,
    gate: str | _Unset = _SENTINEL,
    depends_on: list[str] | _Unset = _SENTINEL,
    add_deps: list[str] | None = None,
    remove_deps: list[str] | None = None,
) -> Plan:
    """Edit an existing phase's metadata."""
    phase = plan.find_phase(phase_id)
    if phase is None:
        raise PlanError(f"Phase '{phase_id}' not found")

    if name is not _SENTINEL:
        phase.name = str(name)
    if context is not _SENTINEL:
        phase.context = str(context)
    if gate is not _SENTINEL:
        phase.gate = str(gate)

    if depends_on is not _SENTINEL:
        existing_phase_ids = {p.id for p in plan.phases}
        new_deps = list(depends_on)  # type: ignore[arg-type]
        for dep in new_deps:
            if dep not in existing_phase_ids:
                raise PlanError(f"Phase depends_on '{dep}' not found")
            if dep == phase_id:
                raise PlanError(f"Phase '{phase_id}' cannot depend on itself")
        phase.depends_on = new_deps

    if add_deps:
        existing_phase_ids = {p.id for p in plan.phases}
        for dep in add_deps:
            if dep not in existing_phase_ids:
                raise PlanError(f"Phase depends_on '{dep}' not found")
            if dep == phase_id:
                raise PlanError(f"Phase '{phase_id}' cannot depend on itself")
            if dep not in phase.depends_on:
                phase.depends_on.append(dep)

    if remove_deps:
        for dep in remove_deps:
            if dep in phase.depends_on:
                phase.depends_on.remove(dep)

    return plan


def edit_plan(
    plan: Plan,
    *,
    project_guidance: str | _Unset = _SENTINEL,
    strategy_ref: str | _Unset = _SENTINEL,
    context: str | _Unset = _SENTINEL,
) -> Plan:
    """Edit plan-level metadata."""
    if project_guidance is not _SENTINEL:
        plan.project_guidance = str(project_guidance)
    if strategy_ref is not _SENTINEL:
        plan.strategy_ref = str(strategy_ref)
    if context is not _SENTINEL:
        plan.context = str(context)
    return plan


# @shell_complexity: Branches preserve pending-only removal, dependent detection, force cleanup, and qualified diagnostics.
def remove_step(plan: Plan, step_id: str, *, force: bool = False) -> Plan:
    """Remove a pending step from its phase."""
    require_unambiguous_target_step_id(plan, step_id, operation="remove-step")
    found = plan.find_step(step_id)
    if found is None:
        raise PlanError(f"Step '{step_id}' not found")
    phase, step = found
    qualified_id = format_step_selector(phase.id, step.id)

    if step.status != StepStatus.PENDING:
        raise PlanError(
            f"Cannot remove step '{qualified_id}' with status "
            f"'{step.status.value}' (must be pending)"
        )

    dependents = [s for s in phase.steps if step_id in s.depends_on]
    if dependents and not force:
        dep_ids = [s.id for s in dependents]
        raise PlanError(f"Cannot remove step '{qualified_id}': step '{dep_ids[0]}' depends on it")

    if force:
        for s in phase.steps:
            if step_id in s.depends_on:
                s.depends_on.remove(step_id)

    phase.steps = [s for s in phase.steps if s.id != step_id]
    return plan


def unlock_phase(plan: Plan, phase_id: str) -> Plan:
    """Explicitly unlock a locked phase by validating all dependencies are done."""
    phase = plan.find_phase(phase_id)
    if phase is None:
        raise PlanError(f"Phase '{phase_id}' not found")

    if phase.status != PhaseStatus.LOCKED:
        raise PlanError(f"Phase '{phase_id}' is not locked (status: {phase.status.value})")

    done_phase_ids = {p.id for p in plan.phases if p.status == PhaseStatus.DONE}
    unmet = [dep for dep in phase.depends_on if dep not in done_phase_ids]
    if unmet:
        raise PlanError(f"Cannot unlock phase '{phase_id}': dependencies not done: {unmet}")

    phase.status = PhaseStatus.PENDING
    return plan


# @shell_complexity: Branches preserve pending-only moves, target validation, same-phase rejection, dependent guard, and phase-scoped dependency clearing.
def move_step(plan: Plan, step_id: str, to_phase_id: str) -> Plan:
    """Move a pending step from one phase to another."""
    require_unambiguous_target_step_id(plan, step_id, operation="move-step")
    found = plan.find_step(step_id)
    if found is None:
        raise PlanError(f"Step '{step_id}' not found")
    from_phase, step = found
    qualified_id = format_step_selector(from_phase.id, step.id)

    if step.status != StepStatus.PENDING:
        raise PlanError(
            f"Cannot move step '{qualified_id}' with status '{step.status.value}' (must be pending)"
        )

    target = plan.find_phase(to_phase_id)
    if target is None:
        raise PlanError(f"Target phase '{to_phase_id}' not found")

    if from_phase.id == to_phase_id:
        raise PlanError(f"Step '{qualified_id}' is already in phase '{to_phase_id}'")

    for s in from_phase.steps:
        if step_id in s.depends_on:
            raise PlanError(f"Cannot move step '{qualified_id}': step '{s.id}' depends on it")

    from_phase.steps = [s for s in from_phase.steps if s.id != step_id]
    step.depends_on = []
    target.steps.append(step)

    return plan


_CHECKLIST_RE = re.compile(r"^(\s*-\s*\[)([ xX])(\]\s*.+)$", re.MULTILINE)


# @shell_complexity: Branches preserve mutually optional check/append operations and existing fuzzy checklist toggling behavior.
def update_checklist(
    plan: Plan, step_id: str, *, check: str | None = None, append: str | None = None
) -> Plan:
    """Update a checklist in a step's description."""
    if check is None and append is None:
        raise PlanError("Must provide either 'check' or 'append'")

    require_unambiguous_target_step_id(plan, step_id, operation="check")
    found = plan.find_step(step_id)
    if found is None:
        raise PlanError(f"Step '{step_id}' not found")
    _, step = found

    if check is not None:
        step.description = _toggle_checklist_item(step.description, check)

    if append is not None:
        step.description = step.description.rstrip() + f"\n- [ ] {append}\n"

    return plan


# @shell_complexity: Branches preserve zero/ambiguous/single match diagnostics and toggle semantics.
def _toggle_checklist_item(description: str, keyword: str) -> str:
    """Toggle a checklist item matching keyword (case-insensitive substring)."""
    matches: list[tuple[int, re.Match[str]]] = []
    for i, match in enumerate(_CHECKLIST_RE.finditer(description)):
        item_text = match.group(3)
        if keyword.lower() in item_text.lower():
            matches.append((i, match))

    if len(matches) == 0:
        raise NoMatchError(keyword)

    if len(matches) > 1:
        candidates = [m.group(0).strip() for _, m in matches]
        raise AmbiguousMatchError(keyword, candidates)

    _, match = matches[0]
    current = match.group(2)
    new_mark = " " if current in ("x", "X") else "x"
    start, end = match.start(), match.end()
    replacement = f"{match.group(1)}{new_mark}{match.group(3)}"
    return description[:start] + replacement + description[end:]
