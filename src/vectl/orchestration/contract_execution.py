"""Runtime execution and recovery-attempt contracts.

Authority: docs/ORCHESTRATION-PLANE-INTERFACES.md section 3
Authority: docs/RFC-opencode-orchestration-runner.md sections 6, 7, 10
"""

from dataclasses import dataclass
from typing import Literal

from vectl.orchestration.contract_literals import RecoveredVia, RequestMode, SessionPolicy

@dataclass(frozen=True)
class ExecutionRequest:
    """
    Mechanical execution request into runtime.

    Authority: docs/ORCHESTRATION-PLANE-INTERFACES.md section 3.6
    Authority: docs/RFC-opencode-orchestration-runner.md section 7.1

    Attributes:
        step_id: Step being executed.
        role: Role performing the execution.
        runner: Runner identifier.
        work_refs: References to work artifacts.
        agent_id: Agent identifier for the execution.
        prompt_bundle_path: Path to the rendered prompt bundle artifact.
        runner_prompt_path: Path to the runner-specific prompt artifact.
        request_mode: Launch mode semantics (``start``, ``resume``, ``recover``).
            Authority: docs/RFC-opencode-orchestration-runner.md section 6.1
        session_policy: Whether an existing session may be reused.
            Authority: docs/RFC-opencode-orchestration-runner.md section 6.2
        session_id: Session to use (if any).
    """

    step_id: str
    role: str
    runner: str
    work_refs: tuple[str, ...]
    agent_id: str = ""
    prompt_bundle_path: str = ""
    runner_prompt_path: str = ""
    request_mode: RequestMode = "start"
    session_policy: SessionPolicy = "reuse_forbidden"
    session_id: str | None = None


@dataclass(frozen=True)
class ExecutionResult:
    """
    Mechanical execution result returned from runtime.

    Attributes:
        step_id: Step that was executed.
        status: One of 'success', 'fail', 'stall', 'transport_error'.
        output_summary: Human-readable summary of results.
        session_id: Session used (if any).
        operator_message: Operator/user-visible message required when runtime
            cannot close the problem mechanically. Unresolved runtime failures
            should surface this instead of silently collapsing into status only.
    """

    step_id: str
    status: Literal["success", "fail", "stall", "transport_error"]
    output_summary: str
    session_id: str | None = None
    operator_message: str | None = None


@dataclass(frozen=True)
class RecoveryContinuity:
    """Recovery path truth label persisted at ``continuity.json``.

    Authority: docs/RFC-opencode-orchestration-runner.md section 10.3

    The system must not collapse native session resume and fresh relaunch
    into the same label. This contract enforces that distinction at the
    type level.

    Attributes:
        recovered_via: Which recovery path was actually taken.
        run_id: The run identifier this continuity record belongs to.
        step_id: The step identifier this continuity record belongs to.
        agent_id: The agent identifier for the recovered execution.
        runner: The runner used for the recovered execution.
        session_id: The session identifier, if a native session was reused.
        timestamp: ISO-8601 timestamp of when recovery was recorded.
    """

    recovered_via: RecoveredVia
    run_id: str = ""
    step_id: str = ""
    agent_id: str = ""
    runner: str = ""
    session_id: str | None = None
    timestamp: str = ""


@dataclass(frozen=True)
class RecoveryAttempt:
    """Individual resume or recover attempt record.

    Authority: docs/RFC-opencode-orchestration-runner.md section 10.4

    Records whether native session validation succeeded, why it may have
    failed, whether fallback relaunch was used, and resulting identifiers.

    Attributes:
        attempt_kind: Whether this was a resume attempt or a recover attempt.
        native_validation_ok: Whether native session validation passed.
        native_validation_failure_reason: Why validation failed, if it did.
        fallback_relaunch_used: Whether a fresh relaunch was used as fallback.
        resulting_run_id: The run identifier after the attempt.
        resulting_session_id: The session identifier after the attempt, if any.
    """

    attempt_kind: Literal["resume", "recover"]
    native_validation_ok: bool = False
    native_validation_failure_reason: str = ""
    fallback_relaunch_used: bool = False
    resulting_run_id: str = ""
    resulting_session_id: str | None = None

