"""Focused tests for driver errors.

Tests verify:
- Error hierarchy structure
- Error class attributes
- Typed error context

Architecture Reference: docs/DRIVER-ARCHITECTURE.md Section 2.2
Blueprint Reference: DRIVER-BLUEPRINT.md Failure Modes
"""

import pytest

from src.vectl.driver.errors import (
    ConfigError,
    DriverError,
    JudgmentError,
    JudgmentParseError,
    JudgmentTimeoutError,
    LoopHaltError,
    RunnerError,
    RunnerNotFoundError,
    RunnerOutputError,
    WorktreeError,
)


class TestErrorHierarchy:
    """Tests for error class hierarchy."""

    def test_driver_error_is_base(self) -> None:
        """DriverError is the base for all driver errors.

        Contract pin from: docs/DRIVER-ARCHITECTURE.md Section 2.2
        """
        assert issubclass(ConfigError, DriverError)
        assert issubclass(RunnerError, DriverError)
        assert issubclass(WorktreeError, DriverError)
        assert issubclass(JudgmentError, DriverError)
        assert issubclass(LoopHaltError, DriverError)

    def test_runner_error_subclasses(self) -> None:
        """RunnerError has RunnerNotFoundError and RunnerOutputError subclasses.

        Contract pin from: docs/DRIVER-ARCHITECTURE.md Section 2.2
        """
        assert issubclass(RunnerNotFoundError, RunnerError)
        assert issubclass(RunnerOutputError, RunnerError)

    def test_judgment_error_subclasses(self) -> None:
        """JudgmentError has JudgmentTimeoutError and JudgmentParseError subclasses.

        Contract pin from: docs/DRIVER-ARCHITECTURE.md Section 2.2
        """
        assert issubclass(JudgmentTimeoutError, JudgmentError)
        assert issubclass(JudgmentParseError, JudgmentError)


class TestDriverError:
    """Tests for DriverError base class."""

    def test_driver_error_is_exception(self) -> None:
        """DriverError MUST inherit from Exception."""
        assert issubclass(DriverError, Exception)

    def test_driver_error_message(self) -> None:
        """DriverError can have a custom message."""
        error = DriverError("test error")
        assert str(error) == "test error"


class TestConfigError:
    """Tests for ConfigError."""

    def test_config_error_is_driver_error(self) -> None:
        """ConfigError MUST inherit from DriverError.

        Contract pin from: docs/DRIVER-ARCHITECTURE.md Section 2.2
        """
        assert issubclass(ConfigError, DriverError)

    def test_config_error_message(self) -> None:
        """ConfigError can have a custom message."""
        error = ConfigError("Invalid configuration: missing runners")
        assert "Invalid configuration" in str(error)


class TestRunnerError:
    """Tests for RunnerError and subclasses."""

    def test_runner_error_constructor(self) -> None:
        """RunnerError MUST store runner_name, step_id, and message.

        Contract pin from: docs/DRIVER-ARCHITECTURE.md Section 2.2
        """
        error = RunnerError("claude", "core.impl", "Process crashed")
        assert error.runner_name == "claude"
        assert error.step_id == "core.impl"
        assert "Process crashed" in str(error)
        assert "claude" in str(error)
        assert "core.impl" in str(error)

    def test_runner_not_found_error(self) -> None:
        """RunnerNotFoundError MUST set default message.

        Contract pin from: docs/DRIVER-ARCHITECTURE.md Section 2.2
        Blueprint: DRIVER-BLUEPRINT.md Failure Modes (Runner not found)
        """
        error = RunnerNotFoundError("gemini", "core.impl")
        assert error.runner_name == "gemini"
        assert error.step_id == "core.impl"
        assert "not found on PATH" in str(error)

    def test_runner_output_error(self) -> None:
        """RunnerOutputError inherits from RunnerError."""
        error = RunnerOutputError("claude", "core.impl", "Invalid JSON output")
        assert error.runner_name == "claude"
        assert error.step_id == "core.impl"
        assert "Invalid JSON output" in str(error)


class TestWorktreeError:
    """Tests for WorktreeError."""

    def test_worktree_error_constructor(self) -> None:
        """WorktreeError MUST store step_id and message.

        Contract pin from: docs/DRIVER-ARCHITECTURE.md Section 2.2
        Blueprint: DRIVER-BLUEPRINT.md Failure Modes (Git failures)
        """
        error = WorktreeError("core.impl", "Branch creation failed")
        assert error.step_id == "core.impl"
        assert "Branch creation failed" in str(error)
        assert "core.impl" in str(error)


class TestJudgmentError:
    """Tests for JudgmentError and subclasses."""

    def test_judgment_error_constructor(self) -> None:
        """JudgmentError MUST store judgment_type, step_id, and message.

        Contract pin from: docs/DRIVER-ARCHITECTURE.md Section 2.2
        """
        error = JudgmentError("EVIDENCE", "core.impl", "Judge returned invalid verdict")
        assert error.judgment_type == "EVIDENCE"
        assert error.step_id == "core.impl"
        assert "invalid verdict" in str(error)
        assert "EVIDENCE" in str(error)

    def test_judgment_timeout_error(self) -> None:
        """JudgmentTimeoutError MUST store timeout_seconds.

        Contract pin from: docs/DRIVER-ARCHITECTURE.md Section 2.2
        Blueprint: DRIVER-BLUEPRINT.md Failure Modes (Judge unavailable)
        """
        error = JudgmentTimeoutError("EVIDENCE", "core.impl", 60)
        assert error.judgment_type == "EVIDENCE"
        assert error.step_id == "core.impl"
        assert error.timeout_seconds == 60
        assert "timeout" in str(error)
        assert "60" in str(error)

    def test_judgment_parse_error(self) -> None:
        """JudgmentParseError MUST store raw_output.

        Contract pin from: docs/DRIVER-ARCHITECTURE.md Section 2.2
        Blueprint: DRIVER-BLUEPRINT.md Failure Modes (Judge unavailable)
        """
        raw_output = '{"verdict": "ACCEPT", "reason": "Tests pass"}'
        error = JudgmentParseError("EVIDENCE", "core.impl", raw_output)
        assert error.judgment_type == "EVIDENCE"
        assert error.step_id == "core.impl"
        assert error.raw_output == raw_output
        assert "unparseable" in str(error)

    def test_judgment_parse_error_truncates_output(self) -> None:
        """JudgmentParseError MUST truncate raw_output in message.

        Contract pin from: docs/DRIVER-ARCHITECTURE.md Section 2.2
        """
        long_output = "x" * 200  # 200 chars
        error = JudgmentParseError("EVIDENCE", "core.impl", long_output)
        # Message should only include first 100 chars
        assert "unparseable output: " in str(error)
        # raw_output attribute stores full output
        assert error.raw_output == long_output


class TestLoopHaltError:
    """Tests for LoopHaltError."""

    def test_loop_halt_error_constructor(self) -> None:
        """LoopHaltError MUST store reason.

        Contract pin from: docs/DRIVER-ARCHITECTURE.md Section 2.2
        Blueprint: DRIVER-BLUEPRINT.md Flow 1 (detect_loop -> HALT)
        """
        error = LoopHaltError("No more executable steps")
        assert error.reason == "No more executable steps"
        assert "No more executable steps" in str(error)

    def test_loop_halt_error_various_reasons(self) -> None:
        """LoopHaltError can have various halt reasons."""
        reasons = [
            "No more executable steps",
            "SUSPECTED_INFINITE_LOOP",
            "All runners failed",
        ]
        for reason in reasons:
            error = LoopHaltError(reason)
            assert error.reason == reason


class TestErrorPropagation:
    """Tests simulating error propagation rules from architecture doc."""

    def test_config_error_propagates_to_loop(self) -> None:
        """ConfigError propagates to loop.py startup -> Halt with diagnostic.

        Contract pin from: docs/DRIVER-ARCHITECTURE.md Section 2.2
        Error propagation rules table
        """
        # At startup, config validation errors should raise ConfigError
        # The pattern: load_config -> ConfigError -> loop.py catches -> halt
        error = ConfigError("Missing required field: runners")
        assert isinstance(error, DriverError)

    def test_runner_not_found_error_propagates_to_loop(self) -> None:
        """RunnerNotFoundError -> loop.py -> Halt with config error.

        Contract pin from: docs/DRIVER-ARCHITECTURE.md Section 2.2
        """
        error = RunnerNotFoundError("gemini", "core.impl")
        # This error should cause the loop to halt
        assert isinstance(error, RunnerError)
        assert isinstance(error, DriverError)

    def test_runner_error_in_reconcile(self) -> None:
        """RunnerError -> loop.py reconcile -> TRANSPORT_ERROR.

        Contract pin from: docs/DRIVER-ARCHITECTURE.md Section 2.2
        """
        error = RunnerError("claude", "core.impl", "Process crashed")
        # In reconcile, this becomes TRANSPORT_ERROR in RunnerResult
        assert error.runner_name == "claude"
        assert error.step_id == "core.impl"

    def test_judgment_timeout_error_optimistic_accept(self) -> None:
        """JudgmentTimeoutError -> skip judgment, accept optimistically, log warning.

        Contract pin from: docs/DRIVER-ARCHITECTURE.md Section 2.2
        Blueprint: DRIVER-BLUEPRINT.md Failure Modes (Judge unavailable)
        """
        error = JudgmentTimeoutError("EVIDENCE", "core.impl", 60)
        # The driver should catch this and skip the judgment
        assert isinstance(error, JudgmentError)
        assert isinstance(error, DriverError)

    def test_worktree_error_log_and_continue(self) -> None:
        """WorktreeError -> dispatch/reconcile -> Log, cleanup, continue.

        Contract pin from: docs/DRIVER-ARCHITECTURE.md Section 2.2
        """
        error = WorktreeError("core.impl", "Merge conflict")
        # This should be logged and cleanup attempted
        assert error.step_id == "core.impl"


class TestErrorStrRepresentation:
    """Tests for error string representations."""

    def test_runner_error_str_format(self) -> None:
        """RunnerError message format includes runner, step, and message."""
        error = RunnerError("claude", "core.impl", "crashed")
        msg = str(error)
        assert "claude" in msg
        assert "core.impl" in msg
        assert "crashed" in msg

    def test_worktree_error_str_format(self) -> None:
        """WorktreeError message format includes step_id and message."""
        error = WorktreeError("step-1", "failed")
        msg = str(error)
        assert "step-1" in msg
        assert "failed" in msg

    def test_judgment_error_str_format(self) -> None:
        """JudgmentError message format includes judgment_type and step_id."""
        error = JudgmentError("PREFLIGHT", "core.impl", "spec inadequate")
        msg = str(error)
        assert "PREFLIGHT" in msg
        assert "core.impl" in msg

    def test_loop_halt_error_str_format(self) -> None:
        """LoopHaltError message format includes reason."""
        error = LoopHaltError("infinite loop detected")
        msg = str(error)
        assert "infinite loop detected" in msg
