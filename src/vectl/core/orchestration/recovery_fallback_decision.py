"""Contracts for pure recovery fallback decision extraction.

Decision row: ``src/vectl/orchestration/recovery_fallback.py`` structural Core extraction.

>>> decide_recovery_fallback({"valid": True, "session_id": "sess-1"}, "2026-01-01T00:00:00Z")
Traceback (most recent call last):
...
NotImplementedError: contract stub: decide_recovery_fallback

>>> decide_recovery_fallback({"valid": False}, "")  # doctest: +ELLIPSIS
Traceback (most recent call last):
...
deal.PreContractError: expected...
"""

from collections.abc import Mapping

from deal import post, pre


@pre(lambda validation_summary, timestamp: isinstance(validation_summary, Mapping) and "valid" in validation_summary and bool(timestamp.strip()))
@post(lambda result: isinstance(result, Mapping) and sum(1 for key in ("native_resume", "fresh_relaunch", "failure") if result.get(key)) == 1 and bool(result.get("recovered_via")))
def decide_recovery_fallback(
    validation_summary: Mapping[str, object],
    timestamp: str,
) -> Mapping[str, object]:
    """Choose one recovery fallback shape while preserving recovered_via truth labels.
    
    >>> decide_recovery_fallback({"valid": True, "session_id": "sess-1"}, "2026-01-01T00:00:00Z")
    Traceback (most recent call last):
    ...
    NotImplementedError: contract stub: decide_recovery_fallback
    """
    raise NotImplementedError("contract stub: decide_recovery_fallback")
