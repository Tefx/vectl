"""Pure recovery fallback decision extraction.

Decision row: ``src/vectl/orchestration/recovery_fallback.py`` structural Core extraction.

>>> decide_recovery_fallback({"valid": True, "session_id": "sess-1"}, "2026-01-01T00:00:00Z")["recovered_via"]
'native_session_resume'

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
    
    >>> decide_recovery_fallback({"valid": False, "prompt_valid": True, "reason": "missing"}, "2026-01-01T00:00:00Z")["recovered_via"]
    'fresh_relaunch'
    """
    valid = bool(validation_summary.get("valid"))
    prompt_valid = bool(validation_summary.get("prompt_valid", False))
    reason = str(validation_summary.get("reason", ""))
    if valid:
        return {
            "native_resume": True,
            "fresh_relaunch": False,
            "failure": False,
            "recovered_via": "native_session_resume",
            "native_resume_attempted": True,
            "native_resume_succeeded": True,
            "native_resume_failure_reason": "",
            "prompt_artifacts_valid": True,
            "prompt_artifact_failure_reason": "",
            "session_id": str(validation_summary.get("session_id", "")),
            "timestamp": timestamp,
        }
    if prompt_valid:
        return {
            "native_resume": False,
            "fresh_relaunch": True,
            "failure": False,
            "recovered_via": "fresh_relaunch",
            "native_resume_attempted": True,
            "native_resume_succeeded": False,
            "native_resume_failure_reason": reason,
            "prompt_artifacts_valid": True,
            "prompt_artifact_failure_reason": "",
            "session_id": None,
            "timestamp": timestamp,
        }
    return {
        "native_resume": False,
        "fresh_relaunch": False,
        "failure": True,
        "recovered_via": "fresh_relaunch",
        "native_resume_attempted": True,
        "native_resume_succeeded": False,
        "native_resume_failure_reason": reason,
        "prompt_artifacts_valid": False,
        "prompt_artifact_failure_reason": str(validation_summary.get("prompt_reason", "")),
        "session_id": None,
        "timestamp": timestamp,
    }
