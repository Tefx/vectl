"""Driver error hierarchy.

Responsibility: Define all driver-specific exceptions. Provides structured error
context for each failure domain.

Non-responsibility: Does NOT re-export vectl-core exceptions (PlanError,
CASConflictError, etc.). Callers that need to catch both driver and core errors
import from both modules.

Architecture Reference: docs/DRIVER-ARCHITECTURE.md Section 2.2
Blueprint Reference: DRIVER-BLUEPRINT.md Failure Modes

Error propagation rules:
    ConfigError          -> loop.py (startup) -> Halt with diagnostic
    RunnerNotFoundError  -> loop.py -> Halt with config error
    RunnerError          -> loop.py reconcile -> TRANSPORT_ERROR, increment failure
    RunnerOutputError    -> loop.py reconcile -> TRANSPORT_ERROR, retry once
    WorktreeError        -> loop.py dispatch/reconcile -> Log, cleanup, continue
    JudgmentTimeoutError -> loop.py -> Skip judgment, accept optimistically, log warning
    JudgmentParseError   -> loop.py -> Skip judgment, accept optimistically, log warning
    LoopHaltError        -> loop.py (top-level) -> Clean shutdown
"""

from __future__ import annotations


class DriverError(Exception):
    """Base for all driver errors.

    Architecture: docs/DRIVER-ARCHITECTURE.md Section 2.2, DriverError
    """


class ConfigError(DriverError):
    """Invalid or missing driver configuration.

    Architecture: docs/DRIVER-ARCHITECTURE.md Section 2.2, ConfigError
    Blueprint: DRIVER-BLUEPRINT.md Failure Modes (Runner not found)
    """


class RunnerError(DriverError):
    """Runner dispatch or execution failure.

    Architecture: docs/DRIVER-ARCHITECTURE.md Section 2.2, RunnerError
    Blueprint: DRIVER-BLUEPRINT.md Failure Modes (Runner failures)
    """

    def __init__(self, runner_name: str, step_id: str, message: str) -> None:
        self.runner_name = runner_name
        self.step_id = step_id
        super().__init__(f"Runner {runner_name} failed on step {step_id}: {message}")


class RunnerNotFoundError(RunnerError):
    """Configured runner command not found on PATH.

    Architecture: docs/DRIVER-ARCHITECTURE.md Section 2.2, RunnerNotFoundError
    Blueprint: DRIVER-BLUEPRINT.md Failure Modes (Runner not found)
    """

    def __init__(self, runner_name: str, step_id: str) -> None:
        super().__init__(runner_name, step_id, "command not found on PATH")


class RunnerOutputError(RunnerError):
    """Runner produced unparseable output.

    Architecture: docs/DRIVER-ARCHITECTURE.md Section 2.2, RunnerOutputError
    Blueprint: DRIVER-BLUEPRINT.md Failure Modes (Malformed output)
    """


class WorktreeError(DriverError):
    """Git worktree operation failure.

    Architecture: docs/DRIVER-ARCHITECTURE.md Section 2.2, WorktreeError
    Blueprint: DRIVER-BLUEPRINT.md Failure Modes (Git failures)
    """

    def __init__(self, step_id: str, message: str) -> None:
        self.step_id = step_id
        super().__init__(f"Worktree error for step {step_id}: {message}")


class JudgmentError(DriverError):
    """Judgment agent invocation failure.

    Architecture: docs/DRIVER-ARCHITECTURE.md Section 2.2, JudgmentError
    Blueprint: DRIVER-BLUEPRINT.md Failure Modes (Judge unavailable)
    """

    def __init__(self, judgment_type: str, step_id: str, message: str) -> None:
        self.judgment_type = judgment_type
        self.step_id = step_id
        super().__init__(f"Judgment error ({judgment_type}) for step {step_id}: {message}")


class JudgmentTimeoutError(JudgmentError):
    """Judgment agent did not respond within timeout.

    Architecture: docs/DRIVER-ARCHITECTURE.md Section 2.2, JudgmentTimeoutError
    Blueprint: DRIVER-BLUEPRINT.md Failure Modes (Judge unavailable)
    """

    def __init__(self, judgment_type: str, step_id: str, timeout_seconds: int) -> None:
        super().__init__(judgment_type, step_id, f"timeout after {timeout_seconds}s")
        self.timeout_seconds = timeout_seconds


class JudgmentParseError(JudgmentError):
    """Judgment agent returned unparseable output.

    Architecture: docs/DRIVER-ARCHITECTURE.md Section 2.2, JudgmentParseError
    Blueprint: DRIVER-BLUEPRINT.md Failure Modes (Judge unavailable)
    """

    def __init__(self, judgment_type: str, step_id: str, raw_output: str) -> None:
        super().__init__(judgment_type, step_id, f"unparseable output: {raw_output[:100]}")
        self.raw_output = raw_output


class LoopHaltError(DriverError):
    """Driver loop detected a halt condition.

    Architecture: docs/DRIVER-ARCHITECTURE.md Section 2.2, LoopHaltError
    Blueprint: DRIVER-BLUEPRINT.md Flow 1 (detect_loop -> HALT)
    """

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(f"Driver halt: {reason}")
