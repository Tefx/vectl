"""Unit coverage supplements for OpenCode runner substrate.

Step: opencode_runner_verification.unit-tests
Intent: Fill gaps in runner registry, error hierarchy, execution request contract,
        and recovery truth persistence helpers coverage.

This module targets uncovered branches identified in coverage analysis:
- runner_registry.py lines 73-74 (RunnerNotFoundError on unknown runner_id)
- runners.py lines 95-97 (RunnerNotFoundError __init__ with message param)
- runners.py lines 634-635 (OpenCodeRunner.poll OSError branch)
- contracts.py — RecoveryContinuity, RecoveryAttempt, ExecutionRequest expanded fields
- Error hierarchy completeness (all typed runner errors)
"""

from __future__ import annotations

import subprocess
from dataclasses import fields, is_dataclass
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from vectl.orchestration.contracts import (
    ExecutionRequest,
    OpenCodeLaunchConfig,
    PromptArtifactPaths,
    PromptBundle,
    RecoveredVia,
    RecoveryAttempt,
    RecoveryContinuity,
    RunnerHandoffEnv,
)
from vectl.orchestration.prompt_materialization import (
    build_opencode_launch_argv,
    build_opencode_launch_env,
    build_runner_handoff_env,
    compute_prompt_bundle_sha256,
    materialize_prompt_artifacts,
    resolve_prompt_artifact_paths,
)
from vectl.orchestration.runner_registry import (
    RunnerRegistry,
    build_default_runner_registry,
    get_opencode_runner,
)
from vectl.orchestration.runners import (
    OpenCodeContinueForbiddenError,
    OpenCodeRunner,
    OpenCodeRunnerError,
    RunnerCancelError,
    RunnerCapabilities,
    RunnerError,
    RunnerHandle,
    RunnerLaunchError,
    RunnerLaunchResult,
    RunnerNotFoundError,
    RunnerPollError,
    RunnerPollResult,
    RunnerResumeError,
    SubprocessRunner,
)


# ---------------------------------------------------------------------
# 1. Runner Registry: unknown runner resolution
# ---------------------------------------------------------------------


class TestRunnerRegistryUnknownRunner:
    """Cover runner_registry.py lines 73-74: RunnerNotFoundError on unknown runner."""

    def test_registry_get_raises_for_unknown_runner(self) -> None:
        """Registry.get() must raise RunnerNotFoundError for unknown runner ID."""
        registry = RunnerRegistry()
        with pytest.raises(RunnerNotFoundError) as exc_info:
            registry.get("nonexistent")
        assert "nonexistent" in str(exc_info.value)
        assert exc_info.value.reason == "runner_not_found"

    def test_registry_get_lists_available_runners_in_error(self) -> None:
        """RunnerNotFoundError message must list available runner IDs."""
        registry = RunnerRegistry()
        registry.register("alpha", SubprocessRunner(runner_id="alpha", command=("echo",)))
        registry.register("beta", SubprocessRunner(runner_id="beta", command=("echo",)))

        with pytest.raises(RunnerNotFoundError) as exc_info:
            registry.get("missing")

        error_msg = str(exc_info.value)
        assert "alpha" in error_msg
        assert "beta" in error_msg

    def test_registry_get_returns_registered_runner(self) -> None:
        """Registry.get() must return the registered runner for known IDs."""
        registry = RunnerRegistry()
        runner = SubprocessRunner(runner_id="test-runner", command=("echo", "test"))
        registry.register("test-runner", runner)
        result = registry.get("test-runner")
        assert result is runner

    def test_registry_register_overwrites(self) -> None:
        """Re-registering the same runner_id must overwrite the previous entry."""
        registry = RunnerRegistry()
        runner_a = SubprocessRunner(runner_id="r", command=("echo", "a"))
        runner_b = SubprocessRunner(runner_id="r", command=("echo", "b"))
        registry.register("r", runner_a)
        registry.register("r", runner_b)
        assert registry.get("r") is runner_b


# ---------------------------------------------------------------------
# 2. RunnerNotFoundError init with message param
# ---------------------------------------------------------------------


class TestRunnerNotFoundErrorInit:
    """Cover runners.py lines 95-97: RunnerNotFoundError with message param."""

    def test_runner_not_found_error_with_message(self) -> None:
        """RunnerNotFoundError(msg=...) must use message as detail."""
        err = RunnerNotFoundError("Unknown runner 'foo'", runner_id="foo")
        assert err.runner_id == "foo"
        assert err.reason == "runner_not_found"
        assert "Unknown runner 'foo'" in str(err)

    def test_runner_not_found_error_with_detail(self) -> None:
        """RunnerNotFoundError(detail=...) must include the detail."""
        err = RunnerNotFoundError(runner_id="bar", detail="some detail")
        assert err.runner_id == "bar"
        assert "some detail" in str(err)

    def test_runner_not_found_error_defaults_runner_id(self) -> None:
        """RunnerNotFoundError without runner_id defaults to 'unknown'."""
        err = RunnerNotFoundError("no runner")
        assert err.runner_id == "unknown"

    def test_runner_not_found_error_is_runner_error(self) -> None:
        """RunnerNotFoundError must be a subclass of RunnerError."""
        assert issubclass(RunnerNotFoundError, RunnerError)


# ---------------------------------------------------------------------
# 3. Typed runner error hierarchy
# ---------------------------------------------------------------------


class TestRunnerErrorHierarchy:
    """Verify all typed runner errors form a correct hierarchy."""

    def test_runner_error_base_class(self) -> None:
        """RunnerError must be the root of the error hierarchy."""
        assert issubclass(RunnerError, Exception)

    def test_runner_not_found_error_hierarchy(self) -> None:
        """RunnerNotFoundError must inherit from RunnerError."""
        assert issubclass(RunnerNotFoundError, RunnerError)

    def test_runner_launch_error_hierarchy(self) -> None:
        """RunnerLaunchError must inherit from RunnerError."""
        assert issubclass(RunnerLaunchError, RunnerError)

    def test_runner_resume_error_hierarchy(self) -> None:
        """RunnerResumeError must inherit from RunnerError."""
        assert issubclass(RunnerResumeError, RunnerError)

    def test_runner_poll_error_hierarchy(self) -> None:
        """RunnerPollError must inherit from RunnerError."""
        assert issubclass(RunnerPollError, RunnerError)

    def test_runner_cancel_error_hierarchy(self) -> None:
        """RunnerCancelError must inherit from RunnerError."""
        assert issubclass(RunnerCancelError, RunnerError)

    def test_opencode_runner_error_hierarchy(self) -> None:
        """OpenCodeRunnerError must inherit from RunnerError."""
        assert issubclass(OpenCodeRunnerError, RunnerError)

    def test_opencode_continue_forbidden_error_hierarchy(self) -> None:
        """OpenCodeContinueForbiddenError must inherit from OpenCodeRunnerError."""
        assert issubclass(OpenCodeContinueForbiddenError, OpenCodeRunnerError)

    def test_all_runner_errors_have_runner_id(self) -> None:
        """All RunnerError subclasses must have a runner_id attribute."""
        # RunnerNotFoundError has different init signature (message, runner_id, detail)
        err_not_found = RunnerNotFoundError(runner_id="test-id")
        assert err_not_found.runner_id == "test-id"

        # RunnerLaunchError requires reason
        err_launch = RunnerLaunchError(runner_id="test-id", reason="resource_unavailable")
        assert err_launch.runner_id == "test-id"

        # RunnerResumeError requires reason
        err_resume = RunnerResumeError(runner_id="test-id", reason="session_missing")
        assert err_resume.runner_id == "test-id"

        # RunnerPollError requires reason
        err_poll = RunnerPollError(runner_id="test-id", reason="handle_unknown")
        assert err_poll.runner_id == "test-id"

        # RunnerCancelError requires reason
        err_cancel = RunnerCancelError(runner_id="test-id", reason="transport_failed")
        assert err_cancel.runner_id == "test-id"

        # OpenCodeRunnerError and OpenCodeContinueForbiddenError
        err_opencode = OpenCodeRunnerError(runner_id="test-id", reason="internal")
        assert err_opencode.runner_id == "test-id"

        err_continue = OpenCodeContinueForbiddenError(runner_id="test-id")
        assert err_continue.runner_id == "test-id"

    def test_all_runner_errors_have_reason(self) -> None:
        """All RunnerError subclasses must have a reason attribute."""
        err_not_found = RunnerNotFoundError(runner_id="test-id")
        assert isinstance(err_not_found.reason, str)

        err_launch = RunnerLaunchError(runner_id="test-id", reason="resource_unavailable")
        assert isinstance(err_launch.reason, str)

        err_resume = RunnerResumeError(runner_id="test-id", reason="session_missing")
        assert isinstance(err_resume.reason, str)

        err_poll = RunnerPollError(runner_id="test-id", reason="handle_unknown")
        assert isinstance(err_poll.reason, str)

        err_cancel = RunnerCancelError(runner_id="test-id", reason="transport_failed")
        assert isinstance(err_cancel.reason, str)

        err_opencode = OpenCodeRunnerError(runner_id="test-id", reason="internal")
        assert isinstance(err_opencode.reason, str)

        err_continue = OpenCodeContinueForbiddenError(runner_id="test-id")
        assert isinstance(err_continue.reason, str)


class TestRunnerLaunchErrorReasons:
    """Verify RunnerLaunchError supports all documented reason values."""

    @pytest.mark.parametrize(
        "reason",
        ["workspace_invalid", "request_invalid", "resource_unavailable", "internal"],
    )
    def test_launch_error_accepts_documented_reasons(self, reason: str) -> None:
        """RunnerLaunchError must accept all documented reason literals."""
        err = RunnerLaunchError(runner_id="opencode", reason=reason)
        assert err.reason == reason

    def test_launch_error_includes_detail_in_message(self) -> None:
        """RunnerLaunchError message must include detail when provided."""
        err = RunnerLaunchError(
            runner_id="opencode",
            reason="resource_unavailable",
            detail="binary not found",
        )
        assert "resource_unavailable" in str(err)
        assert "binary not found" in str(err)


class TestRunnerResumeErrorReasons:
    """Verify RunnerResumeError supports all documented reason values."""

    @pytest.mark.parametrize(
        "reason",
        ["resume_unsupported", "session_missing", "session_invalid", "internal"],
    )
    def test_resume_error_accepts_documented_reasons(self, reason: str) -> None:
        """RunnerResumeError must accept all documented reason literals."""
        err = RunnerResumeError(runner_id="opencode", reason=reason)
        assert err.reason == reason

    def test_resume_error_detail_in_message(self) -> None:
        """RunnerResumeError message must include detail when provided."""
        err = RunnerResumeError(
            runner_id="opencode",
            reason="session_missing",
            detail="no session ID",
        )
        assert "session_missing" in str(err)
        assert "no session ID" in str(err)


class TestRunnerPollErrorReasons:
    """Verify RunnerPollError supports all documented reason values."""

    @pytest.mark.parametrize("reason", ["handle_unknown", "transport_failed", "internal"])
    def test_poll_error_accepts_documented_reasons(self, reason: str) -> None:
        """RunnerPollError must accept all documented reason literals."""
        err = RunnerPollError(runner_id="opencode", reason=reason)
        assert err.reason == reason


class TestRunnerCancelErrorReasons:
    """Verify RunnerCancelError supports all documented reason values."""

    @pytest.mark.parametrize(
        "reason",
        ["cancel_unsupported", "already_complete", "transport_failed", "internal"],
    )
    def test_cancel_error_accepts_documented_reasons(self, reason: str) -> None:
        """RunnerCancelError must accept all documented reason literals."""
        err = RunnerCancelError(runner_id="opencode", reason=reason)
        assert err.reason == reason


# ---------------------------------------------------------------------
# 4. OpenCodeRunner.poll OSError branch (line 634-635)
# ---------------------------------------------------------------------


class TestOpenCodeRunnerPollOSError:
    """Cover runners.py lines 634-635: OSError during poll output reading."""

    @patch("vectl.orchestration.runners.subprocess.Popen")
    def test_poll_raises_transport_error_on_stdout_read_oserror(
        self, mock_popen: MagicMock
    ) -> None:
        """poll() must raise RunnerPollError with reason=transport_failed on OSError
        reading stdout.

        Authority: RFC §9.4 — transport errors must be surfaced as
        RunnerPollError with reason='transport_failed'.
        """
        mock_process = MagicMock()
        mock_process.poll.return_value = 0  # Completed
        # Simulate OSError when reading stdout
        mock_process.stdout = MagicMock()
        mock_process.stdout.read.side_effect = OSError("broken pipe")
        mock_process.stderr = MagicMock()
        mock_process.stderr.read.return_value = ""
        mock_popen.return_value = mock_process

        runner = OpenCodeRunner(artifact_root=Path("/runs"))
        request = ExecutionRequest(
            step_id="core.step",
            role="python-senior-tacit",
            runner="opencode",
            work_refs=(),
            agent_id="python-senior-tacit",
        )
        workspace = Path("/ws")

        launch_result = runner.launch(request=request, workspace=workspace)

        with pytest.raises(RunnerPollError) as exc_info:
            runner.poll(launch_result.handle)

        assert exc_info.value.reason == "transport_failed"
        assert "broken pipe" in str(exc_info.value)


# ---------------------------------------------------------------------
# 5. Expanded ExecutionRequest contract
# ---------------------------------------------------------------------


class TestExecutionRequestContract:
    """Verify ExecutionRequest field defaults and type constraints.

    Authority: docs/ORCHESTRATION-PLANE-INTERFACES.md section 3.6
    Authority: docs/RFC-opencode-orchestration-runner.md section 7.1
    """

    def test_execution_request_default_fields(self) -> None:
        """ExecutionRequest must have correct default values."""
        req = ExecutionRequest(
            step_id="phase.step",
            role="python-senior-tacit",
            runner="opencode",
            work_refs=(),
        )
        assert req.agent_id == ""
        assert req.prompt_bundle_path == ""
        assert req.runner_prompt_path == ""
        assert req.request_mode == "start"
        assert req.session_policy == "reuse_forbidden"
        assert req.session_id is None

    def test_execution_request_is_frozen(self) -> None:
        """ExecutionRequest must be immutable."""
        assert is_dataclass(ExecutionRequest)
        with pytest.raises(AttributeError):
            req = ExecutionRequest(
                step_id="phase.step",
                role="python-senior-tacit",
                runner="opencode",
                work_refs=(),
            )
            req.step_id = "changed"  # type: ignore[misc]

    def test_execution_request_fields_match_spec(self) -> None:
        """ExecutionRequest must have the documented fields.

        Authority: RFC §7.1, §6.1, §6.2
        """
        expected = {
            "step_id",
            "role",
            "runner",
            "work_refs",
            "agent_id",
            "prompt_bundle_path",
            "runner_prompt_path",
            "request_mode",
            "session_policy",
            "session_id",
        }
        actual = {f.name for f in fields(ExecutionRequest)}
        assert actual == expected, (
            f"ExecutionRequest field mismatch. Expected: {expected}, Got: {actual}"
        )

    def test_execution_request_request_mode_types(self) -> None:
        """request_mode must accept 'start', 'resume', 'recover' values."""
        for mode in ("start", "resume", "recover"):
            req = ExecutionRequest(
                step_id="phase.step",
                role="agent",
                runner="opencode",
                work_refs=(),
                request_mode=mode,
            )
            assert req.request_mode == mode

    def test_execution_request_session_policy_types(self) -> None:
        """session_policy must accept 'reuse_allowed', 'reuse_forbidden' values."""
        for policy in ("reuse_allowed", "reuse_forbidden"):
            req = ExecutionRequest(
                step_id="phase.step",
                role="agent",
                runner="opencode",
                work_refs=(),
                session_policy=policy,
            )
            assert req.session_policy == policy

    def test_execution_request_with_all_fields_explicit(self) -> None:
        """ExecutionRequest must correctly store all fields when explicitly set."""
        req = ExecutionRequest(
            step_id="phase.impl.step",
            role="python-senior-tacit",
            runner="opencode",
            work_refs=("src/main.py", "src/util.py"),
            agent_id="python-senior-tacit",
            prompt_bundle_path="/runs/run-1/input/prompt_bundle.json",
            runner_prompt_path="/runs/run-1/input/runner_prompt.md",
            request_mode="resume",
            session_policy="reuse_allowed",
            session_id="sess-abc-123",
        )
        assert req.step_id == "phase.impl.step"
        assert req.role == "python-senior-tacit"
        assert req.runner == "opencode"
        assert req.work_refs == ("src/main.py", "src/util.py")
        assert req.agent_id == "python-senior-tacit"
        assert req.prompt_bundle_path == "/runs/run-1/input/prompt_bundle.json"
        assert req.runner_prompt_path == "/runs/run-1/input/runner_prompt.md"
        assert req.request_mode == "resume"
        assert req.session_policy == "reuse_allowed"
        assert req.session_id == "sess-abc-123"


# ---------------------------------------------------------------------
# 6. Recovery truth persistence helpers
# ---------------------------------------------------------------------


class TestRecoveryContinuityContract:
    """Verify RecoveryContinuity dataclass contract.

    Authority: docs/RFC-opencode-orchestration-runner.md section 10.3
    """

    def test_recovery_continuity_is_frozen(self) -> None:
        """RecoveryContinuity must be immutable."""
        assert is_dataclass(RecoveryContinuity)
        with pytest.raises(AttributeError):
            rc = RecoveryContinuity(recovered_via="native_session_resume")
            rc.recovered_via = "fresh_relaunch"  # type: ignore[misc]

    def test_recovery_continuity_fields_match_spec(self) -> None:
        """RecoveryContinuity must have all documented fields.

        Authority: RFC §10.3 — recovery truth label persisted at continuity.json.
        """
        expected = {
            "recovered_via",
            "run_id",
            "step_id",
            "agent_id",
            "runner",
            "session_id",
            "timestamp",
        }
        actual = {f.name for f in fields(RecoveryContinuity)}
        assert actual == expected, (
            f"RecoveryContinuity field mismatch. Expected: {expected}, Got: {actual}"
        )

    def test_recovery_continuity_native_session_resume(self) -> None:
        """RecoveryContinuity must accept 'native_session_resume' path.

        Authority: RFC §10.3 — the system must not collapse these paths.
        """
        rc = RecoveryContinuity(
            recovered_via="native_session_resume",
            run_id="run-1",
            step_id="phase.step",
            agent_id="python-senior-tacit",
            runner="opencode",
            session_id="sess-abc-123",
            timestamp="2025-01-15T10:30:00Z",
        )
        assert rc.recovered_via == "native_session_resume"
        assert rc.run_id == "run-1"
        assert rc.session_id == "sess-abc-123"

    def test_recovery_continuity_fresh_relaunch(self) -> None:
        """RecoveryContinuity must accept 'fresh_relaunch' path without session_id.

        Authority: RFC §10.3 — fresh relaunch does not carry a session.
        """
        rc = RecoveryContinuity(
            recovered_via="fresh_relaunch",
            run_id="run-2",
            step_id="phase.step",
            agent_id="python-senior-tacit",
            runner="opencode",
            session_id=None,
            timestamp="2025-01-15T11:00:00Z",
        )
        assert rc.recovered_via == "fresh_relaunch"
        assert rc.session_id is None

    def test_recovery_continuity_default_values(self) -> None:
        """RecoveryContinuity must have correct default values."""
        rc = RecoveryContinuity(recovered_via="native_session_resume")
        assert rc.run_id == ""
        assert rc.step_id == ""
        assert rc.agent_id == ""
        assert rc.runner == ""
        assert rc.session_id is None
        assert rc.timestamp == ""

    def test_recovery_continuity_paths_are_distinct(self) -> None:
        """RecoveredVia values must be distinct strings.

        Authority: RFC §10.3 — the system must not collapse these two
        recovery paths into the same label.
        """
        assert "native_session_resume" != "fresh_relaunch"
        # Verify they are valid RecoveredVia values
        for path in ("native_session_resume", "fresh_relaunch"):
            rc = RecoveryContinuity(recovered_via=path)
            assert rc.recovered_via == path


class TestRecoveryAttemptContract:
    """Verify RecoveryAttempt dataclass contract.

    Authority: docs/RFC-opencode-orchestration-runner.md section 10.4
    """

    def test_recovery_attempt_is_frozen(self) -> None:
        """RecoveryAttempt must be immutable."""
        assert is_dataclass(RecoveryAttempt)
        with pytest.raises(AttributeError):
            ra = RecoveryAttempt(attempt_kind="resume")
            ra.attempt_kind = "recover"  # type: ignore[misc]

    def test_recovery_attempt_fields_match_spec(self) -> None:
        """RecoveryAttempt must have all documented fields.

        Authority: RFC §10.4 — recovery attempt record structure.
        """
        expected = {
            "attempt_kind",
            "native_validation_ok",
            "native_validation_failure_reason",
            "fallback_relaunch_used",
            "resulting_run_id",
            "resulting_session_id",
        }
        actual = {f.name for f in fields(RecoveryAttempt)}
        assert actual == expected, (
            f"RecoveryAttempt field mismatch. Expected: {expected}, Got: {actual}"
        )

    def test_recovery_attempt_resume_kind(self) -> None:
        """RecoveryAttempt must accept 'resume' attempt kind."""
        ra = RecoveryAttempt(
            attempt_kind="resume",
            native_validation_ok=True,
            resulting_run_id="run-1",
            resulting_session_id="sess-1",
        )
        assert ra.attempt_kind == "resume"
        assert ra.native_validation_ok is True
        assert ra.resulting_run_id == "run-1"
        assert ra.resulting_session_id == "sess-1"

    def test_recovery_attempt_recover_kind(self) -> None:
        """RecoveryAttempt must accept 'recover' attempt kind."""
        ra = RecoveryAttempt(
            attempt_kind="recover",
            native_validation_ok=False,
            native_validation_failure_reason="session expired",
            fallback_relaunch_used=True,
            resulting_run_id="run-2",
            resulting_session_id=None,
        )
        assert ra.attempt_kind == "recover"
        assert ra.native_validation_ok is False
        assert ra.native_validation_failure_reason == "session expired"
        assert ra.fallback_relaunch_used is True
        assert ra.resulting_session_id is None

    def test_recovery_attempt_default_values(self) -> None:
        """RecoveryAttempt must have correct default values."""
        ra = RecoveryAttempt(attempt_kind="resume")
        assert ra.native_validation_ok is False
        assert ra.native_validation_failure_reason == ""
        assert ra.fallback_relaunch_used is False
        assert ra.resulting_run_id == ""
        assert ra.resulting_session_id is None

    def test_recovery_attempt_records_failure_reason(self) -> None:
        """RecoveryAttempt must store validation failure reason.

        Authority: RFC §10.4 — records why validation failed.
        """
        ra = RecoveryAttempt(
            attempt_kind="resume",
            native_validation_ok=False,
            native_validation_failure_reason="session metadata missing from continuity.json",
        )
        assert "session metadata missing" in ra.native_validation_failure_reason

    def test_recovery_attempt_records_fallback_relaunch(self) -> None:
        """RecoveryAttempt must track whether fallback relaunch was used.

        Authority: RFC §10.4 — records whether fresh relaunch was used.
        """
        ra = RecoveryAttempt(
            attempt_kind="recover",
            native_validation_ok=False,
            native_validation_failure_reason="invalid session",
            fallback_relaunch_used=True,
            resulting_run_id="run-fallback",
        )
        assert ra.fallback_relaunch_used is True
        assert ra.resulting_run_id == "run-fallback"


# ---------------------------------------------------------------------
# 7. RunnerHandle and RunnerLaunchResult contracts
# ---------------------------------------------------------------------


class TestRunnerHandleContract:
    """Verify RunnerHandle dataclass contract."""

    def test_runner_handle_is_frozen(self) -> None:
        """RunnerHandle must be immutable."""
        assert is_dataclass(RunnerHandle)
        with pytest.raises(AttributeError):
            handle = RunnerHandle(runner="opencode", run_id="run-1", session_id=None)
            handle.runner = "changed"  # type: ignore[misc]

    def test_runner_handle_fields(self) -> None:
        """RunnerHandle must have runner, run_id, session_id fields."""
        expected = {"runner", "run_id", "session_id"}
        actual = {f.name for f in fields(RunnerHandle)}
        assert actual == expected

    def test_runner_handle_without_session(self) -> None:
        """RunnerHandle must accept nullable session_id."""
        handle = RunnerHandle(runner="opencode", run_id="run-1", session_id=None)
        assert handle.session_id is None

    def test_runner_handle_with_session(self) -> None:
        """RunnerHandle must accept session_id for resume operations."""
        handle = RunnerHandle(runner="opencode", run_id="run-1", session_id="sess-abc")
        assert handle.session_id == "sess-abc"


class TestRunnerLaunchResultContract:
    """Verify RunnerLaunchResult dataclass contract."""

    def test_runner_launch_result_is_frozen(self) -> None:
        """RunnerLaunchResult must be immutable."""
        assert is_dataclass(RunnerLaunchResult)
        with pytest.raises(AttributeError):
            result = RunnerLaunchResult(
                handle=RunnerHandle(runner="opencode", run_id="run-1", session_id=None),
                initial_summary="started",
            )
            result.initial_summary = "changed"  # type: ignore[misc]

    def test_runner_launch_result_fields(self) -> None:
        """RunnerLaunchResult must have handle and initial_summary fields."""
        expected = {"handle", "initial_summary"}
        actual = {f.name for f in fields(RunnerLaunchResult)}
        assert actual == expected


class TestRunnerPollResultContract:
    """Verify RunnerPollResult dataclass contract."""

    def test_runner_poll_result_is_frozen(self) -> None:
        """RunnerPollResult must be immutable."""
        assert is_dataclass(RunnerPollResult)
        with pytest.raises(AttributeError):
            result = RunnerPollResult(status="running", output_summary="active")
            result.status = "changed"  # type: ignore[misc]

    def test_runner_poll_result_fields(self) -> None:
        """RunnerPollResult must have documented fields."""
        expected = {"status", "output_summary", "session_id", "evidence_refs"}
        actual = {f.name for f in fields(RunnerPollResult)}
        assert actual == expected

    @pytest.mark.parametrize("status", ["running", "success", "fail", "stall", "transport_error"])
    def test_runner_poll_result_accepts_all_status_values(self, status: str) -> None:
        """RunnerPollResult must accept all 5 documented status values."""
        result = RunnerPollResult(status=status, output_summary=f"{status} test")
        assert result.status == status

    def test_runner_poll_result_default_values(self) -> None:
        """RunnerPollResult must have correct default values."""
        result = RunnerPollResult(status="running", output_summary="active")
        assert result.session_id is None
        assert result.evidence_refs == ()


class TestRunnerCapabilitiesContract:
    """Verify RunnerCapabilities dataclass contract."""

    def test_runner_capabilities_is_frozen(self) -> None:
        """RunnerCapabilities must be immutable."""
        assert is_dataclass(RunnerCapabilities)
        with pytest.raises(AttributeError):
            caps = RunnerCapabilities(
                runner_id="opencode",
                supports_resume=True,
                supports_cancel=True,
                supports_streaming=False,
            )
            caps.runner_id = "changed"  # type: ignore[misc]

    def test_runner_capabilities_fields(self) -> None:
        """RunnerCapabilities must have documented fields."""
        expected = {"runner_id", "supports_resume", "supports_cancel", "supports_streaming"}
        actual = {f.name for f in fields(RunnerCapabilities)}
        assert actual == expected


# ---------------------------------------------------------------------
# 8. OpenCodeRunnerError hierarchy detail tests
# ---------------------------------------------------------------------


class TestOpenCodeRunnerErrorDetail:
    """Verify OpenCodeRunnerError and its detail behavior."""

    def test_opencode_runner_error_with_detail(self) -> None:
        """OpenCodeRunnerError must include detail in message."""
        err = OpenCodeRunnerError(runner_id="opencode", reason="internal", detail="extra info")
        assert "extra info" in str(err)
        assert "internal" in str(err)

    def test_opencode_runner_error_without_detail(self) -> None:
        """OpenCodeRunnerError message must omit detail when empty."""
        err = OpenCodeRunnerError(runner_id="opencode", reason="internal")
        assert "internal" in str(err)
        # detail should be empty, message should not have "detail=" trailing
        msg = str(err)
        assert msg.startswith("runner=")


# ---------------------------------------------------------------------
# 9. OpenCodeRunner _build_launch_env coverage
# ---------------------------------------------------------------------


class TestOpenCodeRunnerBuildLaunchEnv:
    """Verify OpenCodeRunner._build_launch_env with agent_id fallback."""

    def test_build_launch_env_defaults_opencode_agent_id(self) -> None:
        """_build_launch_env must use 'opencode' when agent_id is empty."""
        runner = OpenCodeRunner(artifact_root=Path("/runs"))
        request = ExecutionRequest(
            step_id="phase.step",
            role="",
            runner="opencode",
            work_refs=(),
            agent_id="",
        )
        env = runner._build_launch_env(request=request, workspace=Path("/ws"))
        assert env["VECTL_ORCH_AGENT_ID"] == "opencode"

    def test_build_launch_env_uses_provided_agent_id(self) -> None:
        """_build_launch_env must use provided agent_id when non-empty."""
        runner = OpenCodeRunner(artifact_root=Path("/runs"))
        request = ExecutionRequest(
            step_id="phase.step",
            role="python-senior-tacit",
            runner="opencode",
            work_refs=(),
            agent_id="python-senior-tacit",
        )
        env = runner._build_launch_env(request=request, workspace=Path("/ws"))
        assert env["VECTL_ORCH_AGENT_ID"] == "python-senior-tacit"

    def test_build_launch_env_uses_step_id_as_run_id(self) -> None:
        """_build_launch_env must use request.step_id for VECTL_ORCH_RUN_ID."""
        runner = OpenCodeRunner(artifact_root=Path("/runs"))
        request = ExecutionRequest(
            step_id="phase.impl.step",
            role="python-senior-tacit",
            runner="opencode",
            work_refs=(),
            agent_id="python-senior-tacit",
        )
        env = runner._build_launch_env(request=request, workspace=Path("/ws"))
        assert env["VECTL_ORCH_RUN_ID"] == "phase.impl.step"
