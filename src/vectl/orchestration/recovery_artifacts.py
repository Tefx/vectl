"""Recovery fallback artifact persistence helpers.

Authority: docs/RFC-opencode-orchestration-runner.md sections 10.3, 10.4.
This module owns recovery artifact file I/O while preserving the public helper
return shapes re-exported from ``recovery_fallback``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal, TypeVar, cast

from typing_extensions import TypeAliasType

from vectl.orchestration.contracts import RecoveryAttempt, RecoveryContinuity

_T = TypeVar("_T")
_E = TypeVar("_E", bound=Exception)

# Guard-facing shell adapter alias; callers still receive the direct Path/DTO
# compatibility values documented by the recovery fallback public API.
Result = TypeAliasType("Result", Any, type_params=(_T, _E))

_RECOVERY_DIR_NAME: str = "recovery"
_CONTINUITY_FILENAME: str = "continuity.json"
_RESUME_ATTEMPT_FILENAME: str = "resume_attempt.json"
_RECOVER_ATTEMPT_FILENAME: str = "recover_attempt.json"


def persist_recovery_continuity(
    *,
    run_root: Path,
    continuity: RecoveryContinuity,
) -> Result[Path, OSError]:
    """Persist recovery path truth label at ``recovery/continuity.json``."""
    recovery_dir = run_root / _RECOVERY_DIR_NAME
    recovery_dir.mkdir(parents=True, exist_ok=True)
    continuity_path = recovery_dir / _CONTINUITY_FILENAME

    payload: dict[str, object] = {
        "recovered_via": continuity.recovered_via,
        "run_id": continuity.run_id,
        "step_id": continuity.step_id,
        "agent_id": continuity.agent_id,
        "runner": continuity.runner,
        "session_id": continuity.session_id,
        "timestamp": continuity.timestamp,
    }
    continuity_path.write_text(json.dumps(payload, sort_keys=True) + "\n", encoding="utf-8")
    return continuity_path


def persist_recovery_attempt(
    *,
    run_root: Path,
    attempt: RecoveryAttempt,
) -> Result[Path, OSError]:
    """Persist individual resume or recover attempt record."""
    recovery_dir = run_root / _RECOVERY_DIR_NAME
    recovery_dir.mkdir(parents=True, exist_ok=True)
    filename = _RESUME_ATTEMPT_FILENAME if attempt.attempt_kind == "resume" else _RECOVER_ATTEMPT_FILENAME
    attempt_path = recovery_dir / filename
    payload: dict[str, object] = {
        "attempt_kind": attempt.attempt_kind,
        "native_validation_ok": attempt.native_validation_ok,
        "native_validation_failure_reason": attempt.native_validation_failure_reason,
        "fallback_relaunch_used": attempt.fallback_relaunch_used,
        "resulting_run_id": attempt.resulting_run_id,
        "resulting_session_id": attempt.resulting_session_id,
    }
    attempt_path.write_text(json.dumps(payload, sort_keys=True) + "\n", encoding="utf-8")
    return attempt_path


def persist_recovery_fallback_result(
    *,
    run_root: Path,
    result: Any,
) -> Result[dict[str, Path], OSError]:
    """Persist all recovery artifacts for a fallback result."""
    persisted: dict[str, Path] = {}
    if result.continuity is not None:
        persisted["continuity"] = persist_recovery_continuity(run_root=run_root, continuity=result.continuity)
    if result.resume_attempt is not None:
        persisted["resume_attempt"] = persist_recovery_attempt(run_root=run_root, attempt=result.resume_attempt)
    if result.recover_attempt is not None:
        persisted["recover_attempt"] = persist_recovery_attempt(run_root=run_root, attempt=result.recover_attempt)
    return persisted


def read_recovery_continuity(*, run_root: Path) -> Result[RecoveryContinuity | None, OSError]:
    """Read a previously persisted recovery continuity record."""
    continuity_path = run_root / _RECOVERY_DIR_NAME / _CONTINUITY_FILENAME
    if not continuity_path.exists():
        return None
    try:
        data = json.loads(continuity_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    recovered_via_raw = data.get("recovered_via", "")
    if recovered_via_raw not in ("native_session_resume", "fresh_relaunch"):
        return None
    return RecoveryContinuity(
        recovered_via=recovered_via_raw,
        run_id=str(data.get("run_id", "")),
        step_id=str(data.get("step_id", "")),
        agent_id=str(data.get("agent_id", "")),
        runner=str(data.get("runner", "")),
        session_id=data.get("session_id"),
        timestamp=str(data.get("timestamp", "")),
    )


def read_recovery_attempt(
    *,
    run_root: Path,
    attempt_kind: Literal["resume", "recover"],
) -> Result[RecoveryAttempt | None, OSError]:
    """Read a previously persisted recovery attempt record."""
    filename = _RESUME_ATTEMPT_FILENAME if attempt_kind == "resume" else _RECOVER_ATTEMPT_FILENAME
    attempt_path = run_root / _RECOVERY_DIR_NAME / filename
    if not attempt_path.exists():
        return None
    try:
        data = json.loads(attempt_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    return RecoveryAttempt(
        attempt_kind=cast(Literal["resume", "recover"], data.get("attempt_kind", attempt_kind)),
        native_validation_ok=bool(data.get("native_validation_ok", False)),
        native_validation_failure_reason=str(data.get("native_validation_failure_reason", "")),
        fallback_relaunch_used=bool(data.get("fallback_relaunch_used", False)),
        resulting_run_id=str(data.get("resulting_run_id", "")),
        resulting_session_id=data.get("resulting_session_id"),
    )


__all__ = [
    "persist_recovery_attempt",
    "persist_recovery_continuity",
    "persist_recovery_fallback_result",
    "read_recovery_attempt",
    "read_recovery_continuity",
]
