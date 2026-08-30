"""Pure scoped-review projection and topology-only validation closure logic."""

from __future__ import annotations

from deal import pre as deal_pre
from invar_runtime import Contract, post, pre

from vectl.core.diagnostic_formatting import ReviewScope
from vectl.models import Phase, PhaseStatus, Plan, PlanError, PlanValidationIssue, Step, StepStatus

ReviewStepKey = tuple[str, str]
_ACTIVE_WORK_STEP_STATUSES = frozenset(
    {StepStatus.PENDING, StepStatus.CLAIMED, StepStatus.REJECTED}
)


@pre(
    Contract(
        lambda plan: isinstance(plan, Plan),
        "active phase selection requires a Plan",
    )
)
@post(
    Contract(
        lambda result: isinstance(result, set) and all(isinstance(item, str) for item in result),
        "active phase selection returns phase IDs",
    )
)
def _execution_active_phase_ids(plan: Plan) -> set[str]:
    """Return phases where work can execute or be recovered.

    >>> _execution_active_phase_ids(Plan(project="p", phases=[Phase(id="p", name="P")]))
    {'p'}
    """
    done_phase_ids = {phase.id for phase in plan.phases if phase.status == PhaseStatus.DONE}
    active: set[str] = set()
    for phase in plan.phases:
        if phase.status in {PhaseStatus.PENDING, PhaseStatus.IN_PROGRESS}:
            active.add(phase.id)
        elif (
            phase.status == PhaseStatus.LOCKED
            and phase.depends_on
            and all(dependency in done_phase_ids for dependency in phase.depends_on)
        ):
            active.add(phase.id)
        elif any(step.status == StepStatus.CLAIMED for step in phase.steps):
            active.add(phase.id)
    return active


@deal_pre(
    lambda plan, root_phase_ids: isinstance(plan, Plan)
    and isinstance(root_phase_ids, set)
    and all(isinstance(phase_id, str) for phase_id in root_phase_ids)
)
@post(
    Contract(
        lambda result: isinstance(result, set) and all(isinstance(item, str) for item in result),
        "phase closure returns phase IDs",
    )
)
def _phase_dependency_closure(plan: Plan, root_phase_ids: set[str]) -> set[str]:
    """Return roots and all transitive phase dependencies.

    >>> phases = [
    ...     Phase(id="a", name="A"),
    ...     Phase(id="b", name="B", depends_on=["a"]),
    ... ]
    >>> sorted(_phase_dependency_closure(Plan(project="p", phases=phases), {"b"}))
    ['a', 'b']
    """
    phase_by_id = {phase.id: phase for phase in plan.phases}
    closure = set(root_phase_ids)
    pending = list(root_phase_ids)
    while pending:
        phase = phase_by_id.get(pending.pop())
        if phase is None:
            continue
        for dependency in phase.depends_on:
            if dependency not in closure:
                closure.add(dependency)
                pending.append(dependency)
    return closure


@pre(
    Contract(
        lambda plan: isinstance(plan, Plan),
        "active dependency selection requires a Plan",
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
        "active dependency selection returns phase/step keys",
    )
)
def _active_dependency_step_keys(plan: Plan) -> set[ReviewStepKey]:
    """Select active work and its declared dependency closure.

    Narrative fields, including legacy ``<vectl_gate_scope>`` text, do not
    expand this deterministic selection.

    >>> phase = Phase(id="p", name="P", steps=[Step(id="work", name="Work")])
    >>> plan = Plan(project="p", phases=[phase])
    >>> _active_dependency_step_keys(plan)
    {('p', 'work')}
    """
    phase_by_id = {phase.id: phase for phase in plan.phases}
    active_phase_ids = _execution_active_phase_ids(plan)
    phase_scope = _phase_dependency_closure(plan, active_phase_ids)
    keys: set[ReviewStepKey] = set()

    for phase_id in phase_scope - active_phase_ids:
        phase = phase_by_id.get(phase_id)
        if phase is not None:
            keys.update((phase.id, step.id) for step in phase.steps)

    def add_step_dependencies(phase: Phase, root: Step) -> None:
        step_by_id = {step.id: step for step in phase.steps}
        pending = [root]
        while pending:
            step = pending.pop()
            key = (phase.id, step.id)
            if key in keys:
                continue
            keys.add(key)
            pending.extend(
                step_by_id[dependency]
                for dependency in step.depends_on
                if dependency in step_by_id
            )

    for phase_id in active_phase_ids:
        phase = phase_by_id.get(phase_id)
        if phase is None:
            continue
        for step in phase.steps:
            if step.status in _ACTIVE_WORK_STEP_STATUSES:
                add_step_dependencies(phase, step)

    return keys


@pre(
    Contract(
        lambda plan, scope=None, phase_id=None: isinstance(plan, Plan)
        and (scope is None or scope in {"global", "phase", "active_dependency_closure"})
        and (phase_id is None or isinstance(phase_id, str)),
        "a Plan with an ordered phase collection is required",
    )
)
@post(Contract(lambda result: isinstance(result, set), "selected phase IDs are a set"))
def review_phase_ids(
    plan: Plan, scope: ReviewScope, phase_id: str | None
) -> set[str]:
    """Select phases through declared status and dependency topology.

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

    return _phase_dependency_closure(plan, _execution_active_phase_ids(plan))


@pre(
    Contract(
        lambda plan, scope=None, phase_id=None: isinstance(plan, Plan)
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
    """Select step-level rendering closure for one review scope.

    >>> plan = Plan(project="p", phases=[Phase(id="phase", name="Phase")])
    >>> review_step_keys(plan, "phase", "phase")
    set()
    """
    selected_phase_ids = review_phase_ids(plan, scope, phase_id)
    if scope == "global":
        return None
    if scope == "phase":
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
    >>> projected = scoped_review_plan(Plan(project="p", phases=[dependency, target]), {"target"})
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
        "phase validation requires a Plan with ordered phases",
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
        "phase validation returns phase/step keys",
    )
)
def phase_gate_validation_step_keys(plan: Plan, phase_id: str) -> set[ReviewStepKey]:
    """Return a phase and its declared phase-dependency steps.

    The historical name remains import-compatible. It no longer reads gate
    descriptions or resolves cross-phase selectors.

    >>> phase_gate_validation_step_keys(
    ...     Plan(project="p", phases=[Phase(id="target", name="Target")]), "target"
    ... )
    set()
    """
    phase_ids = _phase_dependency_closure(plan, {phase_id})
    return {
        (phase.id, step.id)
        for phase in plan.phases
        if phase.id in phase_ids
        for step in phase.steps
    }


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
    """Return no issues; legacy gate integration text is ordinary text.

    >>> gate_integration_ambiguity_issues(Plan(project="p"), set())
    []
    """
    del plan, gate_phase_ids
    return []


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
    """Return phase IDs in a review scope for legacy callers.

    >>> review_gate_phase_ids(Plan(project="p", phases=[Phase(id="p", name="P")]), "global", None)
    {'p'}
    """
    return review_phase_ids(plan, scope, phase_id)


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
    """Build a structural validation closure independently from rendering.

    >>> dependency = Phase(id="dependency", name="Dependency", depends_on=["missing"])
    >>> target = Phase(id="target", name="Target", depends_on=["dependency"])
    >>> plan = Plan(project="p", phases=[dependency, target])
    >>> closure = review_validation_plan(plan, "phase", "target")
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
        selected_phase_ids.update(key_phase_id for key_phase_id, _ in selected_step_keys)
    return scoped_review_plan(plan, selected_phase_ids, selected_step_keys)
