"""Pure planner mutation interpretation helpers.

Decision row: ``src/vectl/orchestration/core_adapter.py`` structural Core extraction.

>>> as_str_list("alpha")
['alpha']

>>> interpret_planner_mutation("", {})  # doctest: +ELLIPSIS
Traceback (most recent call last):
...
deal.PreContractError: expected...
"""

from collections.abc import Mapping, Sequence

from deal import post, pre

_SUPPORTED_ACTIONS = {
    "add-step",
    "edit-step",
    "remove-step",
    "move-step",
    "add-phase",
    "edit-phase",
    "skip-step",
    "complete-phase",
}


@pre(lambda value: value is not None)
@post(lambda result: isinstance(result, list) and all(isinstance(item, str) and item for item in result))
def as_str_list(value: str | Sequence[object]) -> list[str]:
    """Coerce scalar or sequence planner arguments to non-empty strings.
    
    >>> as_str_list("alpha")
    ['alpha']
    >>> as_str_list(["alpha", 2])
    ['alpha', '2']
    """
    raw_items: Sequence[object]
    if isinstance(value, str):
        raw_items = (value,)
    else:
        raw_items = value
    coerced: list[str] = []
    for item in raw_items:
        item_text = str(item).strip()
        if item_text:
            coerced.append(item_text)
    return coerced


@pre(lambda action, arguments: bool(action.strip()) and isinstance(arguments, Mapping) and all(isinstance(key, str) and key for key in arguments.keys()))
@post(lambda result: isinstance(result, Mapping) and bool(result.get("facade_method")) and bool(result.get("description")))
def interpret_planner_mutation(action: str, arguments: Mapping[str, object]) -> Mapping[str, object]:
    """Interpret one planner mutation into deterministic facade-call data.
    
    >>> interpret_planner_mutation("remove-step", {"step_id": "step"})["description"]
    'remove-step: step=step'
    """
    if action not in _SUPPORTED_ACTIONS:
        raise ValueError(f"Unsupported planner mutation action: {action}")

    if action == "add-step":
        phase_id = str(arguments.get("phase_id", ""))
        step_id = arguments.get("step_id")
        name = str(arguments.get("name", ""))
        return {
            "facade_method": "add_step",
            "description_template": "add-step: phase={phase_id} step={generated_id} name={name}",
            "description": f"add-step: phase={phase_id} step={step_id or ''} name={name}",
            "affected_step_id": str(step_id) if step_id is not None else "",
        }
    if action == "edit-step":
        step_id = str(arguments.get("step_id", ""))
        changes = arguments.get("changes", {})
        if not isinstance(changes, Mapping):
            raise ValueError(f"edit-step changes must be dict, got {type(changes)}")
        fields = tuple(key for key in changes.keys() if key in {"name", "description", "verification", "evidence_template", "agent", "depends_on", "refs"})
        return {
            "facade_method": "edit_step",
            "description": f"edit-step: step={step_id} fields={fields}",
            "affected_step_id": step_id,
            "fields": fields,
        }
    if action == "remove-step":
        step_id = str(arguments.get("step_id", ""))
        return {"facade_method": "remove_step", "description": f"remove-step: step={step_id}", "affected_step_id": step_id}
    if action == "move-step":
        step_id = str(arguments.get("step_id", ""))
        target_phase = str(arguments.get("target_phase", ""))
        return {"facade_method": "move_step", "description": f"move-step: step={step_id} to={target_phase}", "affected_step_id": step_id}
    if action == "add-phase":
        phase_id = arguments.get("phase_id")
        name = str(arguments.get("name", ""))
        return {
            "facade_method": "add_phase",
            "description_template": "add-phase: phase={generated_id} name={name}",
            "description": f"add-phase: phase={phase_id or ''} name={name}",
            "affected_step_id": "",
        }
    if action == "edit-phase":
        phase_id = str(arguments.get("phase_id", ""))
        changes = arguments.get("changes", {})
        if not isinstance(changes, Mapping):
            raise ValueError(f"edit-phase changes must be dict, got {type(changes)}")
        fields = tuple(key for key in changes.keys() if key in {"name", "depends_on", "context"})
        return {"facade_method": "edit_phase", "description": f"edit-phase: phase={phase_id} fields={fields}", "affected_step_id": "", "fields": fields}
    if action == "skip-step":
        step_id = str(arguments.get("step_id", ""))
        reason = str(arguments.get("reason", ""))
        return {"facade_method": "skip_step", "description": f"skip-step: step={step_id} reason={reason}", "affected_step_id": step_id}

    phase_id = str(arguments.get("phase_id", ""))
    reason = str(arguments.get("reason", "planner auto-complete"))
    return {
        "facade_method": "complete_phase",
        "description_template": "complete-phase: phase={phase_id} completed_steps={completed_ids}",
        "description": f"complete-phase: phase={phase_id} reason={reason}",
        "affected_step_id": "",
    }
