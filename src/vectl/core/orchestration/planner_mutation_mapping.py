"""Contracts for pure planner mutation mapping extraction.

Decision row: ``src/vectl/orchestration/core_adapter.py`` structural Core extraction.

>>> as_str_list("alpha")
Traceback (most recent call last):
...
NotImplementedError: contract stub: as_str_list

>>> interpret_planner_mutation("", {})  # doctest: +ELLIPSIS
Traceback (most recent call last):
...
deal.PreContractError: expected...
"""

from collections.abc import Mapping, Sequence

from deal import post, pre


@pre(lambda value: value is not None)
@post(lambda result: isinstance(result, list) and all(isinstance(item, str) and item for item in result))
def as_str_list(value: str | Sequence[object]) -> list[str]:
    """Coerce scalar or sequence planner arguments to non-empty strings.
    
    >>> as_str_list("alpha")
    Traceback (most recent call last):
    ...
    NotImplementedError: contract stub: as_str_list
    """
    raise NotImplementedError("contract stub: as_str_list")


@pre(lambda action, arguments: bool(action.strip()) and isinstance(arguments, Mapping) and all(isinstance(key, str) and key for key in arguments.keys()))
@post(lambda result: isinstance(result, Mapping) and bool(result.get("facade_method")) and bool(result.get("description")))
def interpret_planner_mutation(action: str, arguments: Mapping[str, object]) -> Mapping[str, object]:
    """Interpret one planner mutation into deterministic facade-call data.
    
    >>> interpret_planner_mutation("complete", {"step_id": "step"})
    Traceback (most recent call last):
    ...
    NotImplementedError: contract stub: interpret_planner_mutation
    """
    raise NotImplementedError("contract stub: interpret_planner_mutation")
