"""Contracts for projection artifact payload builders.

Decision row: ``src/vectl/orchestration/projections.py`` structural Core extraction.

>>> latest_payload({"run_id": "run-1", "status": "running", "event_count": 1})
Traceback (most recent call last):
...
NotImplementedError: contract stub: latest_payload

>>> metrics_payload({"run_id": "run-1", "event_count": -1})  # doctest: +ELLIPSIS
Traceback (most recent call last):
...
deal.PreContractError: expected...
"""

from collections.abc import Mapping

from deal import post, pre


@pre(lambda state_view: isinstance(state_view, Mapping) and bool(str(state_view.get("run_id", "")).strip()))
@post(lambda result: isinstance(result, Mapping) and {"run_id", "status"}.issubset(result.keys()))
def latest_payload(state_view: Mapping[str, object]) -> Mapping[str, object]:
    """Build JSON-compatible latest projection payload.
    
    >>> latest_payload({"run_id": "run-1", "status": "running", "event_count": 1})
    Traceback (most recent call last):
    ...
    NotImplementedError: contract stub: latest_payload
    """
    raise NotImplementedError("contract stub: latest_payload")


@pre(lambda state_view: isinstance(state_view, Mapping) and bool(str(state_view.get("run_id", "")).strip()))
@post(lambda result: isinstance(result, Mapping) and {"run_id", "event_count"}.issubset(result.keys()))
def summary_payload(state_view: Mapping[str, object]) -> Mapping[str, object]:
    """Build JSON-compatible summary projection payload.
    
    >>> summary_payload({"run_id": "run-1", "event_count": 1})
    Traceback (most recent call last):
    ...
    NotImplementedError: contract stub: summary_payload
    """
    raise NotImplementedError("contract stub: summary_payload")


@pre(lambda state_view: isinstance(state_view, Mapping) and bool(str(state_view.get("run_id", "")).strip()) and int(state_view.get("event_count", 0)) >= 0)
@post(lambda result: isinstance(result, Mapping) and int(result.get("event_count", 0)) >= 0)
def metrics_payload(state_view: Mapping[str, object]) -> Mapping[str, object]:
    """Build JSON-compatible non-negative metrics projection payload.
    
    >>> metrics_payload({"run_id": "run-1", "event_count": 1})
    Traceback (most recent call last):
    ...
    NotImplementedError: contract stub: metrics_payload
    """
    raise NotImplementedError("contract stub: metrics_payload")
