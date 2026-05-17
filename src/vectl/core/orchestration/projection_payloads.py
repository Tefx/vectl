"""Projection artifact payload builders.

Decision row: ``src/vectl/orchestration/projections.py`` structural Core extraction.

>>> latest_payload({"run_id": "run-1", "status": "running", "event_count": 1})["status"]
'running'

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
    
    >>> latest_payload({"run_id": "run-1", "status": "running", "event_count": 1})["version"]
    1
    """
    return {
        "version": 1,
        "run_id": state_view.get("run_id", ""),
        "status": state_view.get("status"),
        "started_at": state_view.get("started_at"),
        "finished_at": state_view.get("finished_at"),
        "active_step_id": state_view.get("active_step_id"),
        "open_case_count": int(state_view.get("open_case_count", 0)),
        "active_execution_count": int(state_view.get("active_execution_count", 0)),
        "active_lease_count": int(state_view.get("active_lease_count", 0)),
        "last_event_seq": int(state_view.get("last_event_seq", 0)),
        "projection_health": state_view.get("projection_health", "fresh"),
        "projection_code": state_view.get("projection_code"),
        "projection_detail": state_view.get("projection_detail"),
    }


@pre(lambda state_view: isinstance(state_view, Mapping) and bool(str(state_view.get("run_id", "")).strip()))
@post(lambda result: isinstance(result, Mapping) and {"run_id", "event_count"}.issubset(result.keys()))
def summary_payload(state_view: Mapping[str, object]) -> Mapping[str, object]:
    """Build JSON-compatible summary projection payload.
    
    >>> summary_payload({"run_id": "run-1", "event_count": 1})["event_count"]
    1
    """
    return {
        "version": 1,
        "run_id": state_view.get("run_id", ""),
        "status": state_view.get("status"),
        "active_step_id": state_view.get("active_step_id"),
        "open_case_count": int(state_view.get("open_case_count", 0)),
        "active_execution_count": int(state_view.get("active_execution_count", 0)),
        "active_lease_count": int(state_view.get("active_lease_count", 0)),
        "last_event_seq": int(state_view.get("last_event_seq", 0)),
        "event_count": int(state_view.get("event_count", state_view.get("last_event_seq", 0))),
    }


@pre(lambda state_view: isinstance(state_view, Mapping) and bool(str(state_view.get("run_id", "")).strip()) and int(state_view.get("event_count", 0)) >= 0)
@post(lambda result: isinstance(result, Mapping) and int(result.get("event_count", 0)) >= 0)
def metrics_payload(state_view: Mapping[str, object]) -> Mapping[str, object]:
    """Build JSON-compatible non-negative metrics projection payload.
    
    >>> metrics_payload({"run_id": "run-1", "event_count": 1})["event_count"]
    1
    """
    return {
        "version": 1,
        "run_id": state_view.get("run_id", ""),
        "dispatch_count": int(state_view.get("dispatch_count", 0)),
        "resolution_count": int(state_view.get("resolution_count", 0)),
        "operator_required_case_count": int(state_view.get("operator_required_case_count", 0)),
        "transport_error_count": int(state_view.get("transport_error_count", 0)),
        "active_execution_peak": int(state_view.get("active_execution_peak", 0)),
        "average_step_runtime_seconds": float(state_view.get("average_step_runtime_seconds", 0.0)),
        "total_resolver_tokens": int(state_view.get("total_resolver_tokens", 0)),
        "total_estimated_cost_usd": float(state_view.get("total_estimated_cost_usd", 0.0)),
        "event_count": int(state_view.get("event_count", state_view.get("last_event_seq", 0))),
    }
