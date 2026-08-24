"""Pure scoped-review projection and gate-integrity closure logic."""

from __future__ import annotations

from invar_runtime import Contract, post, pre

from vectl.core.diagnostic_formatting import ReviewScope
from vectl.core_plan_validation import (
    _active_dependency_step_keys,
    _execution_active_phase_ids,
    _gate_integrated_step_ids,
    _phase_dependency_closure,
)
from vectl.models import Phase, Plan, PlanError, PlanValidationIssue, Step

ReviewStepKey = tuple[str, str]


@pre(
    Contract(
        lambda plan, scope=None, phase_id=None: isinstance(plan, Plan)
        and len(plan.phases) >= 0
        and (scope is None or scope in {"global", "phase", "active_dependency_closure"})
        and (phase_id is None or isinstance(phase_id, str)),
        "a Plan with an ordered phase collection is required",
    )
)
@post(Contract(lambda result: isinstance(result, set), "selected phase IDs are a set"))
def review_phase_ids(
    plan: Plan, scope: ReviewScope, phase_id: str | None
) -> set[str]:
    """Select phases using established active/dependency-closure semantics.

    >>> plan = Plan(project="p", phases=[Phase(id="phase", name="Phase")])
    >>> review_phase_ids(plan, "global", None)
    {'phase'}
    """
    if scope not in {"global", "phase", "active_dependency_closure"}:
        raise ValueError(f"Unknown review scope: {scope}")
    if scope == "phase" and not phase_id:
        raise PlanError("scope='phase' requires phase_id.")
    if scope == "active_dependency_closure" and phase_id is not None:
        raise PlanError("phase_id cannot be combined with scope='active_dependency_closure'.")
    if scope == "global":
        return {phase.id for phase in plan.phases}
    if scope == "phase":
        assert phase_id is not None
        if plan.find_phase(phase_id) is None:
            raise PlanError(f"Phase '{phase_id}' not found.")
        return {phase_id}

    active_phase_ids = _execution_active_phase_ids(plan)
    selected = _phase_dependency_closure(plan, active_phase_ids)
    selected.update(
        selected_phase_id
        for selected_phase_id, _ in _active_dependency_step_keys(plan)
    )
    return selected


@pre(
    Contract(
        lambda plan, scope=None, phase_id=None: isinstance(plan, Plan)
        and len(plan.phases) >= 0
        and (scope is None or scope in {"global", "phase", "active_dependency_closure"})
        and (phase_id is None or isinstance(phase_id, str)),
        "a Plan with an ordered phase collection is required",
    )
)
@post(
    Contract(
        lambda result: result is None or isinstance(result, set),
        "selected review step keys are a set or global None",
    )
)
def review_step_keys(
    plan: Plan, scope: ReviewScope, phase_id: str | None
) -> set[ReviewStepKey] | None:
    """Select step-level render closure for one review scope.

    >>> plan = Plan(project="p", phases=[Phase(id="phase", name="Phase")])
    >>> review_step_keys(plan, "phase", "phase")
    set()
    """
    selected_phase_ids = review_phase_ids(plan, scope, phase_id)
    if scope == "global":
        return None
    if scope == "phase":
        assert phase_id is not None
        return {
            (phase.id, step.id)
            for phase in plan.phases
            if phase.id in selected_phase_ids
            for step in phase.steps
        }
    return _active_dependency_step_keys(plan)


@pre(
    Contract(
        lambda plan, selected_phase_ids=None, selected_step_keys=None: isinstance(plan, Plan)
        and (
            selected_phase_ids is None
            or (
                isinstance(selected_phase_ids, set)
                and all(isinstance(phase_id, str) for phase_id in selected_phase_ids)
            )
        )
        and (
            selected_step_keys is None
            or (
                isinstance(selected_step_keys, set)
                and all(
                    isinstance(key, tuple)
                    and len(key) == 2
                    and all(isinstance(component, str) for component in key)
                    for key in selected_step_keys
                )
            )
        ),
        "a Plan and scoped phase/step selections are required",
    )
)
@post(Contract(lambda result: isinstance(result, Plan), "scoped review result is a Plan"))
def scoped_review_plan(
    plan: Plan,
    selected_phase_ids: set[str],
    selected_step_keys: set[ReviewStepKey] | None = None,
) -> Plan:
    """Project rendering phases/steps while retaining dependency shell edges.

    >>> dependency = Phase(id="dependency", name="Dependency", depends_on=["missing"])
    >>> target = Phase(id="target", name="Target", depends_on=["dependency"])
    >>> projected = scoped_review_plan(
    ...     Plan(project="p", phases=[dependency, target]), {"target"}
    ... )
    >>> projected.find_phase("dependency").depends_on  # type: ignore[union-attr]
    ['missing']
    """
    phase_by_id = {phase.id: phase for phase in plan.phases}
    required_phase_ids = set(selected_phase_ids)
    pending = list(selected_phase_ids)
    while pending:
        phase = phase_by_id.get(pending.pop())
        if phase is None:
            continue
        for dependency in phase.depends_on:
            if dependency not in required_phase_ids and dependency in phase_by_id:
                required_phase_ids.add(dependency)
                pending.append(dependency)

    phases: list[Phase] = []
    for phase in plan.phases:
        if phase.id not in required_phase_ids:
            continue
        scoped_phase = phase.model_copy(deep=True)
        if phase.id not in selected_phase_ids:
            scoped_phase.steps = []
        elif selected_step_keys is not None:
            scoped_phase.steps = [
                step
                for step in scoped_phase.steps
                if (phase.id, step.id) in selected_step_keys
            ]
        phases.append(scoped_phase)

    scoped_plan = plan.model_copy(deep=True)
    scoped_plan.phases = phases
    return scoped_plan


@pre(
    Contract(
        lambda plan, phase_id=None: isinstance(plan, Plan)
        and (phase_id is None or isinstance(phase_id, str)),
        "phase gate validation requires a Plan with ordered phases",
    )
)
@post(
    Contract(
        lambda result: isinstance(result, set)
        and all(
            isinstance(key, tuple)
            and len(key) == 2
            and all(isinstance(component, str) for component in key)
            for key in result
        ),
        "phase gate validation returns phase/step keys",
    )
)
def phase_gate_validation_step_keys(plan: Plan, phase_id: str) -> set[ReviewStepKey]:
    """Include a phase, integrated candidates, and both dependency closures.

    >>> phase_gate_validation_step_keys(
    ...     Plan(project="p", phases=[Phase(id="target", name="Target")]), "target"
    ... )
    set()
    """
    phase_by_id = {phase.id: phase for phase in plan.phases}
    base_phase_ids = _phase_dependency_closure(plan, {phase_id})
    keys = {
        (phase.id, step.id)
        for phase in plan.phases
        if phase.id in base_phase_ids
        for step in phase.steps
    }
    all_step_keys = {
        (phase.id, step.id) for phase in plan.phases for step in phase.steps
    }
    target_phase = phase_by_id.get(phase_id)
    if target_phase is None:
        return keys
    if "<vectl_gate_scope>" in target_phase.gate:
        return all_step_keys

    def add_step_dependencies(integrated_phase: Phase, root: Step) -> None:
        step_by_id = {step.id: step for step in integrated_phase.steps}
        pending = [root]
        while pending:
            step = pending.pop()
            key = (integrated_phase.id, step.id)
            if key in keys:
                continue
            keys.add(key)
            pending.extend(
                step_by_id[dependency]
                for dependency in step.depends_on
                if dependency in step_by_id
            )

    for gate_step in target_phase.steps:
        integrated_step_ids = _gate_integrated_step_ids(gate_step.description)
        if integrated_step_ids is None:
            return all_step_keys
        for integrated_step_id in integrated_step_ids:
            matches = plan.find_step_matches(integrated_step_id)
            if not matches:
                return all_step_keys
            for integrated_phase, integrated_step in matches:
                add_step_dependencies(integrated_phase, integrated_step)
                dependency_phase_ids = _phase_dependency_closure(
                    plan, {integrated_phase.id}
                ) - {integrated_phase.id}
                keys.update(
                    (dependency_phase.id, step.id)
                    for dependency_phase in plan.phases
                    if dependency_phase.id in dependency_phase_ids
                    for step in dependency_phase.steps
                )
    return keys


@pre(
    Contract(
        lambda plan, gate_phase_ids=None: isinstance(plan, Plan)
        and (
            gate_phase_ids is None
            or (
                isinstance(gate_phase_ids, set)
                and all(isinstance(phase_id, str) for phase_id in gate_phase_ids)
            )
        ),
        "gate ambiguity review requires a Plan and phase-ID set",
    )
)
@post(
    Contract(
        lambda result: isinstance(result, list)
        and all(isinstance(issue, PlanValidationIssue) for issue in result),
        "gate ambiguity review returns validation issues",
    )
)
def gate_integration_ambiguity_issues(
    plan: Plan, gate_phase_ids: set[str]
) -> list[PlanValidationIssue]:
    """Block selectors with distinct exact and qualified interpretations.

    >>> gate_scope = ('<vectl_gate_scope>{"schema_version":"vectl.gate.scope.v2",'
    ...               '"integrated_steps":["target.review"]}</vectl_gate_scope>')
    >>> plan = Plan(project="p", phases=[
    ...     Phase(id="other", name="Other", steps=[Step(id="target.review", name="Exact")]),
    ...     Phase(id="target", name="Target", steps=[
    ...         Step(id="review", name="Qualified"),
    ...         Step(id="gate", name="Gate", description=gate_scope),
    ...     ]),
    ... ])
    >>> len(gate_integration_ambiguity_issues(plan, {"target"}))
    1
    """
    issues: list[PlanValidationIssue] = []
    seen_selectors: set[tuple[str, str]] = set()
    for gate_phase in plan.phases:
        if gate_phase.id not in gate_phase_ids:
            continue
        for gate_step in gate_phase.steps:
            integrated_step_ids = _gate_integrated_step_ids(gate_step.description)
            if integrated_step_ids is None:
                continue
            for selector in integrated_step_ids:
                selector_key = (gate_phase.id, selector)
                if selector_key in seen_selectors or "." not in selector:
                    continue
                seen_selectors.add(selector_key)
                phase_prefix, _, step_suffix = selector.partition(".")
                exact = [
                    (phase, step)
                    for phase in plan.phases
                    for step in phase.steps
                    if step.id == selector
                ]
                qualified = [
                    (phase, step)
                    for phase in plan.phases
                    if phase.id == phase_prefix
                    for step in phase.steps
                    if step.id == step_suffix
                ]
                candidates = plan.find_step_matches(selector)
                if not exact or not qualified or len(candidates) < 2:
                    continue
                rendered_candidates = ", ".join(
                    f"{phase.id}/{step.id}" for phase, step in candidates
                )
                issues.append(
                    PlanValidationIssue(
                        f"Ambiguous gate integration selector '{selector}' resolves to "
                        f"distinct exact/qualified steps: {rendered_candidates}"
                    )
                )
    return issues


@pre(
    Contract(
        lambda plan, scope=None, phase_id=None: isinstance(plan, Plan)
        and (scope is None or scope in {"global", "phase", "active_dependency_closure"})
        and (phase_id is None or isinstance(phase_id, str)),
        "gate phase selection requires a Plan and valid review scope",
    )
)
@post(
    Contract(
        lambda result: isinstance(result, set)
        and all(isinstance(phase_id, str) for phase_id in result),
        "gate phase selection returns phase IDs",
    )
)
def review_gate_phase_ids(
    plan: Plan, scope: ReviewScope, phase_id: str | None
) -> set[str]:
    """Return phases whose gate selectors affect one review scope.

    >>> review_gate_phase_ids(
    ...     Plan(project="p", phases=[Phase(id="p", name="P")]), "global", None
    ... )
    {'p'}
    """
    if scope == "global":
        return {phase.id for phase in plan.phases}
    if scope == "phase":
        return {phase_id} if phase_id is not None else set()
    return _execution_active_phase_ids(plan)


@pre(
    Contract(
        lambda plan, scope=None, phase_id=None: isinstance(plan, Plan)
        and (scope is None or scope in {"global", "phase", "active_dependency_closure"})
        and (phase_id is None or isinstance(phase_id, str)),
        "a Plan and valid review scope are required",
    )
)
@post(Contract(lambda result: isinstance(result, Plan), "validation closure is a Plan"))
def review_validation_plan(
    plan: Plan, scope: ReviewScope, phase_id: str | None
) -> Plan:
    """Build validation closure independently from render projection.

    >>> dependency = Phase(id="dependency", name="Dependency", depends_on=["missing"])
    >>> target = Phase(id="target", name="Target", depends_on=["dependency"])
    >>> closure = review_validation_plan(
    ...     Plan(project="p", phases=[dependency, target]), "phase", "target"
    ... )
    >>> [phase.id for phase in closure.phases]
    ['dependency', 'target']
    """
    if scope == "global":
        return plan
    selected_phase_ids = review_phase_ids(plan, scope, phase_id)
    selected_step_keys = review_step_keys(plan, scope, phase_id)
    if scope == "phase":
        assert phase_id is not None
        selected_step_keys = phase_gate_validation_step_keys(plan, phase_id)
        selected_phase_ids = _phase_dependency_closure(plan, selected_phase_ids)
        selected_phase_ids.update(
            key_phase_id for key_phase_id, _ in selected_step_keys
        )
    return scoped_review_plan(plan, selected_phase_ids, selected_step_keys)
