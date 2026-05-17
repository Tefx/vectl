# @invar:allow file_size: Recovery fallback persistence and decision surface remains co-located to preserve RFC recovery artifact compatibility.
"""Automatic recovery fallback semantics for OpenCode-backed orchestration.

Authority: docs/RFC-opencode-orchestration-runner.md sections 10, 11, 12

This module implements the recovery order specified in §6.3 and §10:

1. Validate persisted run/session artifacts
2. If native session continuation is possible, resume natively
3. Otherwise automatically relaunch from preserved prompt artifacts
4. Persist which path was used (recovered_via truth label)

The system must not collapse ``native_session_resume`` and ``fresh_relaunch``
into the same label. Both are valid automated recoveries, but they are not
semantically identical (§10.3).

Recovery artifacts are persisted at::

    .vectl/runs/<run_id>/recovery/continuity.json
    .vectl/runs/<run_id>/recovery/resume_attempt.json
    .vectl/runs/<run_id>/recovery/recover_attempt.json
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal, cast

from vectl.orchestration.contracts import (
    RecoveredVia,
    RecoveryAttempt,
    RecoveryContinuity,
)
from vectl.orchestration.prompt_materialization import (
    PromptArtifactValidation,
    validate_prompt_artifacts_for_recovery,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------
# Session Validation (§10.1)
# ---------------------------------------------------------------------

_SESSION_REQUIRED_FIELDS: tuple[str, ...] = (
    "runner",
    "session_id",
    "step_id",
    "agent_id",
    "workspace",
)


@dataclass(frozen=True)
class SessionValidationResult:
    """Result of validating a session.json for native resume eligibility.

    Authority: docs/RFC-opencode-orchestration-runner.md section 10.1

    Attributes:
        valid: Whether the session is eligible for native resume.
        reason: Human-readable explanation. Empty string when valid.
        session_data: Parsed session data if validation succeeded, else None.
        runner: The runner identifier from session_data, if available.
        session_id: The session identifier from session_data, if available.
        step_id: The step identifier from session_data, if available.
        agent_id: The agent identifier from session_data, if available.
    """

    valid: bool
    reason: str = ""
    session_data: dict[str, object] | None = None
    runner: str = ""
    session_id: str = ""
    step_id: str = ""
    agent_id: str = ""


# @invar:allow function_size: Native-resume validation keeps ordered RFC checks and reason strings in one compatibility surface.
# @invar:allow shell_result: Public recovery validation API returns SessionValidationResult consumed by existing recovery callers.
# @shell_complexity: Branches preserve distinct RFC validation failure reasons for session and prompt artifacts.
def validate_session_for_resume(
    *,
    run_root: Path,
) -> SessionValidationResult:
    """Validate whether a session is eligible for native resume.

    Authority: docs/RFC-opencode-orchestration-runner.md section 10.1

    Minimum session validation criteria before native resume:

    1. ``session.json`` exists
    2. ``session.json`` parses successfully
    3. ``session.json`` contains non-empty values for:
       ``runner``, ``session_id``, ``step_id``, ``agent_id``, ``workspace``
    4. ``runner == "opencode"``
    5. The authoritative prompt artifacts still exist and parse/read successfully:
       ``input/prompt_bundle.json`` and ``input/runner_prompt.md``

    Args:
        run_root: Artifact root for the run (e.g. ``.vectl/runs/<run_id>``).

    Returns:
        SessionValidationResult indicating whether native resume is possible.
    """
    session_path = run_root / "session.json"

    # Check 1: session.json must exist
    if not session_path.exists():
        return SessionValidationResult(
            valid=False,
            reason=f"session.json not found at {session_path}",
        )

    # Check 2: session.json must parse successfully
    try:
        raw = session_path.read_text(encoding="utf-8")
        session_data = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        return SessionValidationResult(
            valid=False,
            reason=f"session.json is not valid JSON at {session_path}: {exc}",
        )

    if not isinstance(session_data, dict):
        return SessionValidationResult(
            valid=False,
            reason=f"session.json must be a JSON object at {session_path}",
        )

    # Check 3: Required fields must be non-empty
    missing_fields: list[str] = []
    for field_name in _SESSION_REQUIRED_FIELDS:
        value = session_data.get(field_name)
        if not isinstance(value, str) or not value.strip():
            missing_fields.append(field_name)

    if missing_fields:
        joined = ", ".join(sorted(missing_fields))
        return SessionValidationResult(
            valid=False,
            reason=f"session.json missing or empty fields: [{joined}]",
        )

    # Extract validated fields
    runner = str(session_data.get("runner", ""))
    session_id_val = str(session_data.get("session_id", ""))
    step_id_val = str(session_data.get("step_id", ""))
    agent_id_val = str(session_data.get("agent_id", ""))

    # Check 4: runner must be "opencode" for native OpenCode resume
    if runner != "opencode":
        return SessionValidationResult(
            valid=False,
            reason=f"session.json runner={runner!r} is not 'opencode'; "
            "native resume only supported for opencode runner",
            runner=runner,
            session_id=session_id_val,
            step_id=step_id_val,
            agent_id=agent_id_val,
        )

    # Check 5: Authoritative prompt artifacts must exist and be valid
    prompt_bundle_path = run_root / "input" / "prompt_bundle.json"
    runner_prompt_path = run_root / "input" / "runner_prompt.md"

    if not prompt_bundle_path.exists():
        return SessionValidationResult(
            valid=False,
            reason=f"prompt_bundle.json not found at {prompt_bundle_path}; "
            "native resume requires authoritative prompt artifacts",
            runner=runner,
            session_id=session_id_val,
            step_id=step_id_val,
            agent_id=agent_id_val,
        )

    if not runner_prompt_path.exists():
        return SessionValidationResult(
            valid=False,
            reason=f"runner_prompt.md not found at {runner_prompt_path}; "
            "native resume requires authoritative prompt artifacts",
            runner=runner,
            session_id=session_id_val,
            step_id=step_id_val,
            agent_id=agent_id_val,
        )

    # Validate prompt bundle is readable JSON
    try:
        bundle_data = json.loads(prompt_bundle_path.read_text(encoding="utf-8"))
        if not isinstance(bundle_data, dict):
            return SessionValidationResult(
                valid=False,
                reason=f"prompt_bundle.json is not a JSON object at {prompt_bundle_path}",
                runner=runner,
                session_id=session_id_val,
                step_id=step_id_val,
                agent_id=agent_id_val,
            )
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        return SessionValidationResult(
            valid=False,
            reason=f"prompt_bundle.json is not valid JSON: {exc}",
            runner=runner,
            session_id=session_id_val,
            step_id=step_id_val,
            agent_id=agent_id_val,
        )

    # Validate runner_prompt.md is non-empty
    prompt_content = runner_prompt_path.read_text(encoding="utf-8")
    if not prompt_content.strip():
        return SessionValidationResult(
            valid=False,
            reason=f"runner_prompt.md is empty at {runner_prompt_path}",
            runner=runner,
            session_id=session_id_val,
            step_id=step_id_val,
            agent_id=agent_id_val,
        )

    return SessionValidationResult(
        valid=True,
        reason="Session and prompt artifacts are valid for native resume",
        session_data=session_data,
        runner=runner,
        session_id=session_id_val,
        step_id=step_id_val,
        agent_id=agent_id_val,
    )


# ---------------------------------------------------------------------
# Recovery Fallback Decision (§10.2, §10.3, §6.3)
# ---------------------------------------------------------------------


@dataclass(frozen=True)
class RecoveryFallbackResult:
    """Result of the recovery fallback decision process.

    Authority: docs/RFC-opencode-orchestration-runner.md sections 10.2, 10.3

    This result records which recovery path was chosen, why, and includes
    the truth label that must be persisted unaltered.

    Attributes:
        recovered_via: Truth label distinguishing native resume from fresh relaunch.
        native_resume_attempted: Whether native resume was attempted.
        native_resume_succeeded: Whether native resume validation passed.
        native_resume_failure_reason: Why native resume failed, if it did.
        prompt_artifacts_valid: Whether prompt artifacts were valid for fresh relaunch.
        prompt_artifact_failure_reason: Why prompt artifacts failed, if they did.
        prompt_validation: Full prompt artifact validation result.
        session_validation: Full session validation result, if attempted.
        continuity: The RecoveryContinuity record to persist.
        resume_attempt: The RecoveryAttempt for the resume phase, if attempted.
        recover_attempt: The RecoveryAttempt for the recovery/fallback phase.
    """

    recovered_via: RecoveredVia
    native_resume_attempted: bool
    native_resume_succeeded: bool
    native_resume_failure_reason: str
    prompt_artifacts_valid: bool
    prompt_artifact_failure_reason: str
    prompt_validation: PromptArtifactValidation | None = None
    session_validation: SessionValidationResult | None = None
    continuity: RecoveryContinuity | None = None
    resume_attempt: RecoveryAttempt | None = None
    recover_attempt: RecoveryAttempt | None = None


# @invar:allow shell_result: Public fallback API returns RecoveryFallbackResult truth labels rather than Result wrapper for compatibility.
def recover_with_fallback(
    *,
    run_id: str,
    step_id: str,
    agent_id: str,
    runner: str,
    run_root: Path,
    workspace: Path,
    timestamp: str | None = None,
) -> RecoveryFallbackResult:
    """Execute the automatic recovery fallback decision process.

    Authority: docs/RFC-opencode-orchestration-runner.md sections 10.1–10.4, §6.3

    Recovery order (§6.3):
        1. validate persisted run/session artifacts
        2. if native session continuation is possible, resume natively
        3. otherwise automatically relaunch from preserved prompt artifacts
        4. persist which path was used

    If both native session resume and prompt-based relaunch are unavailable,
    recovery must fail explicitly rather than fabricating continuity (§10.2).

    Args:
        run_id: Unique run identifier.
        step_id: Step being recovered.
        agent_id: Agent identifier.
        runner: Runner identifier.
        run_root: Artifact root for the run (e.g. ``.vectl/runs/<run_id>``).
        workspace: Absolute path to the execution workspace.
        timestamp: ISO-8601 timestamp for continuity records. Auto-generated if None.

    Returns:
        RecoveryFallbackResult with the chosen path and truth labeling.
    """
    if timestamp is None:
        timestamp = datetime.now(timezone.utc).isoformat()

    # ---------------------------------------------------------------
    # Phase 1: Try native session resume (§10.1)
    # ---------------------------------------------------------------
    session_result = validate_session_for_resume(run_root=run_root)

    resume_attempt: RecoveryAttempt | None = None
    recovery_attempt: RecoveryAttempt | None = None

    if session_result.valid:
        # Native resume is possible. Use it.
        resume_attempt = RecoveryAttempt(
            attempt_kind="resume",
            native_validation_ok=True,
            native_validation_failure_reason="",
            fallback_relaunch_used=False,
            resulting_run_id=run_id,
            resulting_session_id=session_result.session_id,
        )

        continuity = RecoveryContinuity(
            recovered_via="native_session_resume",
            run_id=run_id,
            step_id=step_id,
            agent_id=agent_id,
            runner=runner,
            session_id=session_result.session_id,
            timestamp=timestamp,
        )

        logger.info(
            "Recovery fallback: native_session_resume chosen for "
            "run_id=%s step_id=%s session_id=%s",
            run_id,
            step_id,
            session_result.session_id,
        )

        return RecoveryFallbackResult(
            recovered_via="native_session_resume",
            native_resume_attempted=True,
            native_resume_succeeded=True,
            native_resume_failure_reason="",
            prompt_artifacts_valid=True,
            prompt_artifact_failure_reason="",
            prompt_validation=None,
            session_validation=session_result,
            continuity=continuity,
            resume_attempt=resume_attempt,
            recover_attempt=None,
        )

    # ---------------------------------------------------------------
    # Phase 2: Native resume preconditions failed; try fresh relaunch (§10.2)
    # ---------------------------------------------------------------
    prompt_validation = validate_prompt_artifacts_for_recovery(
        artifact_root=run_root.parent,
        run_id=run_id,
        workspace=workspace,
    )

    if prompt_validation.valid:
        # Fresh relaunch is possible.
        resume_attempt = RecoveryAttempt(
            attempt_kind="resume",
            native_validation_ok=False,
            native_validation_failure_reason=session_result.reason,
            fallback_relaunch_used=True,
            resulting_run_id=run_id,
            resulting_session_id=None,
        )

        recovery_attempt = RecoveryAttempt(
            attempt_kind="recover",
            native_validation_ok=False,
            native_validation_failure_reason=session_result.reason,
            fallback_relaunch_used=True,
            resulting_run_id=run_id,
            resulting_session_id=None,
        )

        continuity = RecoveryContinuity(
            recovered_via="fresh_relaunch",
            run_id=run_id,
            step_id=step_id,
            agent_id=agent_id,
            runner=runner,
            session_id=None,
            timestamp=timestamp,
        )

        logger.info(
            "Recovery fallback: fresh_relaunch chosen for "
            "run_id=%s step_id=%s (native resume failed: %s)",
            run_id,
            step_id,
            session_result.reason,
        )

        return RecoveryFallbackResult(
            recovered_via="fresh_relaunch",
            native_resume_attempted=True,
            native_resume_succeeded=False,
            native_resume_failure_reason=session_result.reason,
            prompt_artifacts_valid=True,
            prompt_artifact_failure_reason="",
            prompt_validation=prompt_validation,
            session_validation=session_result,
            continuity=continuity,
            resume_attempt=resume_attempt,
            recover_attempt=recovery_attempt,
        )

    # ---------------------------------------------------------------
    # Phase 3: Both paths failed (§10.2 last paragraph)
    # Recovery must fail explicitly rather than fabricating continuity.
    # ---------------------------------------------------------------
    resume_attempt = RecoveryAttempt(
        attempt_kind="resume",
        native_validation_ok=False,
        native_validation_failure_reason=session_result.reason,
        fallback_relaunch_used=False,
        resulting_run_id=run_id,
        resulting_session_id=None,
    )

    recovery_attempt = RecoveryAttempt(
        attempt_kind="recover",
        native_validation_ok=False,
        native_validation_failure_reason=session_result.reason,
        fallback_relaunch_used=False,
        resulting_run_id=run_id,
        resulting_session_id=None,
    )

    logger.warning(
        "Recovery fallback: both paths failed for run_id=%s step_id=%s (native: %s; prompt: %s)",
        run_id,
        step_id,
        session_result.reason,
        prompt_validation.reason,
    )

    return RecoveryFallbackResult(
        recovered_via="fresh_relaunch",  # Will NOT be used for actual recovery
        native_resume_attempted=True,
        native_resume_succeeded=False,
        native_resume_failure_reason=session_result.reason,
        prompt_artifacts_valid=False,
        prompt_artifact_failure_reason=prompt_validation.reason,
        prompt_validation=prompt_validation,
        session_validation=session_result,
        continuity=None,  # No continuity when both paths fail
        resume_attempt=resume_attempt,
        recover_attempt=recovery_attempt,
    )


# ---------------------------------------------------------------------
# Recovery Artifact Persistence (§10.3, §10.4)
# ---------------------------------------------------------------------

_RECOVERY_DIR_NAME: str = "recovery"
_CONTINUITY_FILENAME: str = "continuity.json"
_RESUME_ATTEMPT_FILENAME: str = "resume_attempt.json"
_RECOVER_ATTEMPT_FILENAME: str = "recover_attempt.json"


# @invar:allow shell_result: Persistence helper historically returns the written artifact Path for recovery audit callers.
def persist_recovery_continuity(
    *,
    run_root: Path,
    continuity: RecoveryContinuity,
) -> Path:
    """Persist recovery path truth label at ``recovery/continuity.json``.

    Authority: docs/RFC-opencode-orchestration-runner.md section 10.3

    The system must not collapse ``native_session_resume`` and
    ``fresh_relaunch`` into the same label. This function writes the
    authoritative ``recovered_via`` truth so that downstream consumers
    (summaries, event payloads, audit) can distinguish the actual path.

    Args:
        run_root: Artifact root for the run (e.g. ``.vectl/runs/<run_id>``).
        continuity: The RecoveryContinuity record to persist.

    Returns:
        Path to the written continuity.json file.
    """
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

    continuity_path.write_text(
        json.dumps(payload, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    return continuity_path


# @invar:allow shell_result: Persistence helper historically returns the written attempt Path for recovery audit callers.
def persist_recovery_attempt(
    *,
    run_root: Path,
    attempt: RecoveryAttempt,
) -> Path:
    """Persist individual resume or recover attempt record.

    Authority: docs/RFC-opencode-orchestration-runner.md section 10.4

    Records whether native session validation succeeded, why it may have
    failed, whether fallback relaunch was used, and resulting identifiers.

    Args:
        run_root: Artifact root for the run (e.g. ``.vectl/runs/<run_id>``).
        attempt: The RecoveryAttempt record to persist.

    Returns:
        Path to the written attempt JSON file.
    """
    recovery_dir = run_root / _RECOVERY_DIR_NAME
    recovery_dir.mkdir(parents=True, exist_ok=True)

    filename = (
        _RESUME_ATTEMPT_FILENAME if attempt.attempt_kind == "resume" else _RECOVER_ATTEMPT_FILENAME
    )
    attempt_path = recovery_dir / filename

    payload: dict[str, object] = {
        "attempt_kind": attempt.attempt_kind,
        "native_validation_ok": attempt.native_validation_ok,
        "native_validation_failure_reason": attempt.native_validation_failure_reason,
        "fallback_relaunch_used": attempt.fallback_relaunch_used,
        "resulting_run_id": attempt.resulting_run_id,
        "resulting_session_id": attempt.resulting_session_id,
    }

    attempt_path.write_text(
        json.dumps(payload, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    return attempt_path


# @invar:allow shell_result: Aggregate persistence returns artifact-name to Path mapping consumed by recovery callers.
# @shell_orchestration: Aggregate delegates persistence I/O helpers while preserving legacy artifact mapping return shape.
def persist_recovery_fallback_result(
    *,
    run_root: Path,
    result: RecoveryFallbackResult,
) -> dict[str, Path]:
    """Persist all recovery artifacts for a fallback result.

    Authority: docs/RFC-opencode-orchestration-runner.md sections 10.3, 10.4

    Persists:
    - ``recovery/continuity.json`` (if continuity is available)
    - ``recovery/resume_attempt.json`` (if resume was attempted)
    - ``recovery/recover_attempt.json`` (always, for the recovery attempt)

    Args:
        run_root: Artifact root for the run.
        result: The RecoveryFallbackResult to persist.

    Returns:
        Dictionary mapping artifact name to file path for all persisted artifacts.
    """
    persisted: dict[str, Path] = {}

    if result.continuity is not None:
        path = persist_recovery_continuity(
            run_root=run_root,
            continuity=result.continuity,
        )
        persisted["continuity"] = path

    if result.resume_attempt is not None:
        path = persist_recovery_attempt(
            run_root=run_root,
            attempt=result.resume_attempt,
        )
        persisted["resume_attempt"] = path

    if result.recover_attempt is not None:
        path = persist_recovery_attempt(
            run_root=run_root,
            attempt=result.recover_attempt,
        )
        persisted["recover_attempt"] = path

    return persisted


# @invar:allow shell_result: Reader API intentionally uses None for missing or invalid legacy continuity artifacts.
# @shell_complexity: Branches preserve missing, unreadable, non-object, and invalid-label legacy artifact handling.
def read_recovery_continuity(
    *,
    run_root: Path,
) -> RecoveryContinuity | None:
    """Read a previously persisted recovery continuity record.

    Args:
        run_root: Artifact root for the run.

    Returns:
        RecoveryContinuity if the file exists and parses, else None.
    """
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
        session_id=data.get("session_id"),  # May be None for fresh_relaunch
        timestamp=str(data.get("timestamp", "")),
    )


# @invar:allow shell_result: Reader API intentionally uses None for missing or invalid legacy attempt artifacts.
# @shell_complexity: Branches preserve missing, unreadable, non-object, and parsed attempt compatibility handling.
def read_recovery_attempt(
    *,
    run_root: Path,
    attempt_kind: Literal["resume", "recover"],
) -> RecoveryAttempt | None:
    """Read a previously persisted recovery attempt record.

    Args:
        run_root: Artifact root for the run.
        attempt_kind: Which attempt to read ("resume" or "recover").

    Returns:
        RecoveryAttempt if the file exists and parses, else None.
    """
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
    "SessionValidationResult",
    "RecoveryFallbackResult",
    "persist_recovery_attempt",
    "persist_recovery_continuity",
    "persist_recovery_fallback_result",
    "read_recovery_attempt",
    "read_recovery_continuity",
    "recover_with_fallback",
    "validate_session_for_resume",
]
