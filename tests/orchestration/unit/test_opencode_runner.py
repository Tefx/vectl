"""Runner-level tests for OpenCodeRunner: launch, resume, poll, cancel behavior.

Authority: docs/RFC-opencode-orchestration-runner.md sections 9, 15

These tests prove that the OpenCodeRunner implementation matches the frozen
OpenCode CLI contract:
    - Start mode builds the correct one-shot argv
    - Resume mode builds the correct session argv with --session
    - --continue is never used (forbidden per RFC §15.2)
    - Capabilities declare resume and cancel support
    - Typed errors are raised for invalid inputs
    - Poll processes correctly (running, success, fail, stall, transport_error)
    - Cancel sends terminate and preserves session metadata

Step: opencode_runner_impl.dedicated-runner
Intent: runner_level_tests
"""

from __future__ import annotations

import json
import sqlite3
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from vectl.orchestration.contracts import (
    ExecutionRequest,
    OpenCodeLaunchConfig,
    PromptBundle,
)
from vectl.orchestration.prompt_materialization import (
    build_opencode_launch_argv,
    materialize_prompt_artifacts,
    resolve_prompt_artifact_paths,
)
from vectl.orchestration.runner_registry import (
    build_default_runner_registry,
    get_opencode_runner,
)
from vectl.orchestration.runners import (
    OpenCodeContinueForbiddenError,
    OpenCodeRunner,
    RunnerCancelError,
    RunnerError,
    RunnerHandle,
    RunnerLaunchError,
    RunnerLaunchResult,
    RunnerPollError,
    RunnerResumeError,
    SubprocessRunner,
)

# ---------------------------------------------------------------------
# 1. OpenCodeRunner registration contract
# ---------------------------------------------------------------------


class TestOpenCodeRunnerRegistration:
    """Verify the opencode runner is registered as OpenCodeRunner, not SubprocessRunner."""

    def test_opencode_runner_is_registered_in_default_registry(self) -> None:
        """The default registry must register 'opencode' as OpenCodeRunner."""
        registry = build_default_runner_registry()
        runner = registry.get("opencode")
        assert isinstance(runner, OpenCodeRunner), (
            f"Expected OpenCodeRunner for 'opencode', got {type(runner).__name__}"
        )

    def test_opencode_runner_is_not_subprocess_runner(self) -> None:
        """The 'opencode' runner must NOT be SubprocessRunner.

        Authority: RFC §4.1 — OpenCode must use a dedicated runner adapter.
        """
        registry = build_default_runner_registry()
        runner = registry.get("opencode")
        assert not isinstance(runner, SubprocessRunner), (
            "opencode runner must be OpenCodeRunner, not bare SubprocessRunner"
        )

    def test_other_runners_remain_subprocess(self) -> None:
        """Other runners (claude, codex, test) remain SubprocessRunner."""
        registry = build_default_runner_registry()
        for runner_id in ("claude", "codex", "test"):
            runner = registry.get(runner_id)
            assert isinstance(runner, SubprocessRunner), (
                f"runner '{runner_id}' should be SubprocessRunner, got {type(runner).__name__}"
            )

    def test_get_opencode_runner_returns_opencode_runner(self) -> None:
        """get_opencode_runner() must return an OpenCodeRunner instance."""
        runner = get_opencode_runner()
        assert isinstance(runner, OpenCodeRunner)

    def test_get_opencode_runner_with_custom_artifact_root(self) -> None:
        """get_opencode_runner() must accept custom artifact_root."""
        custom_root = Path("/custom/runs")
        runner = get_opencode_runner(artifact_root=custom_root)
        assert isinstance(runner, OpenCodeRunner)
        assert runner.artifact_root == custom_root

    def test_get_opencode_runner_with_custom_config(self) -> None:
        """get_opencode_runner() must accept custom OpenCodeLaunchConfig."""
        custom_config = OpenCodeLaunchConfig()
        runner = get_opencode_runner(config=custom_config)
        assert isinstance(runner, OpenCodeRunner)
        assert runner.config is custom_config


# ---------------------------------------------------------------------
# 2. OpenCodeRunner capabilities
# ---------------------------------------------------------------------


class TestOpenCodeRunnerCapabilities:
    """Verify OpenCodeRunner capabilities match RFC §9.3, §9.5."""

    def test_capabilities_supports_resume(self) -> None:
        """OpenCode supports resume via --session (RFC §9.3)."""
        runner = OpenCodeRunner()
        caps = runner.capabilities()
        assert caps.supports_resume is True, "OpenCodeRunner must support resume (via --session)"

    def test_capabilities_supports_cancel(self) -> None:
        """OpenCode supports cancel (RFC §9.5)."""
        runner = OpenCodeRunner()
        caps = runner.capabilities()
        assert caps.supports_cancel is True, "OpenCodeRunner must support cancel"

    def test_capabilities_does_not_support_streaming(self) -> None:
        """OpenCode does not support streaming (not in RFC scope)."""
        runner = OpenCodeRunner()
        caps = runner.capabilities()
        assert caps.supports_streaming is False, "OpenCodeRunner does not support streaming"

    def test_capabilities_runner_id_is_opencode(self) -> None:
        """The runner_id in capabilities must be 'opencode'."""
        runner = OpenCodeRunner()
        caps = runner.capabilities()
        assert caps.runner_id == "opencode"

    def test_default_runner_id_is_opencode(self) -> None:
        """Default runner_id must be 'opencode'."""
        runner = OpenCodeRunner()
        assert runner.runner_id == "opencode"


# ---------------------------------------------------------------------
# 3. Frozen launch argv contract (start mode)
# ---------------------------------------------------------------------


class TestOpenCodeRunnerLaunchArgv:
    """Verify OpenCodeRunner builds the frozen launch argv from the RFC contract."""

    def _make_request(
        self,
        *,
        step_id: str = "core.step",
        agent_id: str = "python-senior-tacit",
        session_id: str | None = None,
        request_mode: str = "start",
        prompt_bundle_path: str = "",
        runner_prompt_path: str = "",
    ) -> ExecutionRequest:
        return ExecutionRequest(
            step_id=step_id,
            role=agent_id,
            runner="opencode",
            work_refs=(),
            agent_id=agent_id,
            prompt_bundle_path=prompt_bundle_path,
            runner_prompt_path=runner_prompt_path,
            request_mode=request_mode,
            session_id=session_id,
        )

    def test_start_mode_argv_matches_rfc_contract(self) -> None:
        """Start mode argv must match RFC §9.2 exactly.

        Authority: RFC §9.2 — the one-shot launch contract::

            opencode run --format json --dir <workspace> --agent <agent_id>
                --file .vectl/orch/runner_prompt.md
                "Read the attached runner prompt file..."
        """
        runner = OpenCodeRunner()
        request = self._make_request()
        workspace = Path("/ws")

        argv = runner._build_launch_argv(request=request, workspace=workspace, session_id=None)

        assert argv[0] == "opencode"
        assert argv[1] == "run"
        assert "--format" in argv
        format_idx = argv.index("--format")
        assert argv[format_idx + 1] == "json"
        assert "--dir" in argv
        dir_idx = argv.index("--dir")
        assert argv[dir_idx + 1] == "/ws"
        assert "--agent" in argv
        agent_idx = argv.index("--agent")
        assert argv[agent_idx + 1] == "python-senior-tacit"
        assert "--file" in argv
        file_idx = argv.index("--file")
        assert argv[file_idx + 1] == ".vectl/orch/runner_prompt.md"
        # No --session in start mode
        assert "--session" not in argv
        # Last arg is the start bootstrap message
        assert argv[-1] == OpenCodeLaunchConfig().bootstrap_start

    def test_resume_mode_argv_includes_session(self) -> None:
        """Resume mode argv must include --session (RFC §9.3).

        Authority: RFC §9.3 — the session continuation contract::

            opencode run --format json --dir <workspace>
                --session <session_id> --agent <agent_id>
                --file .vectl/orch/runner_prompt.md
                "Continue this session..."
        """
        runner = OpenCodeRunner()
        request = self._make_request(session_id="sess-abc-123")
        workspace = Path("/ws")

        argv = runner._build_launch_argv(
            request=request, workspace=workspace, session_id="sess-abc-123"
        )

        assert "--session" in argv
        session_idx = argv.index("--session")
        assert argv[session_idx + 1] == "sess-abc-123"
        assert "--file" in argv
        assert ".vectl/orch/runner_prompt.md" in argv
        # Last arg is the resume bootstrap message
        assert argv[-1] == OpenCodeLaunchConfig().bootstrap_resume

    def test_continue_flag_never_appears(self) -> None:
        """--continue must NEVER appear in any argv (RFC §15.2).

        Authority: RFC §15.2 — '--continue' targets 'last session' and
        is therefore non-deterministic for machine recovery.
        --session <session_id> is the required deterministic surface.
        """
        runner = OpenCodeRunner()
        request_start = self._make_request()
        request_resume = self._make_request(session_id="sess-1")

        workspace = Path("/ws")

        argv_start = runner._build_launch_argv(
            request=request_start, workspace=workspace, session_id=None
        )
        argv_resume = runner._build_launch_argv(
            request=request_resume, workspace=workspace, session_id="sess-1"
        )

        assert "--continue" not in argv_start, "--continue must not appear in start mode"
        assert "--continue" not in argv_resume, "--continue must not appear in resume mode"

    def test_launch_argv_delegates_to_frozen_contract(self) -> None:
        """_build_launch_argv must delegate to build_opencode_launch_argv."""
        runner = OpenCodeRunner()
        request = self._make_request()
        workspace = Path("/ws")

        # Our _build_launch_argv should produce the same output as the
        # frozen contract function for the same inputs
        runner_argv = runner._build_launch_argv(
            request=request, workspace=workspace, session_id=None
        )
        contract_argv = build_opencode_launch_argv(
            workspace=workspace,
            agent_id="python-senior-tacit",
            session_id=None,
        )

        assert runner_argv == contract_argv, (
            f"OpenCodeRunner._build_launch_argv must match frozen contract. "
            f"Got: {runner_argv}, Expected: {contract_argv}"
        )

    def test_launch_argv_with_session_delegates_correctly(self) -> None:
        """_build_launch_argv for resume must match frozen contract with session."""
        runner = OpenCodeRunner()
        request = self._make_request(session_id="sess-xyz")
        workspace = Path("/ws")

        runner_argv = runner._build_launch_argv(
            request=request, workspace=workspace, session_id="sess-xyz"
        )
        contract_argv = build_opencode_launch_argv(
            workspace=workspace,
            agent_id="python-senior-tacit",
            session_id="sess-xyz",
        )

        assert runner_argv == contract_argv


# ---------------------------------------------------------------------
# 4. Launch environment contract
# ---------------------------------------------------------------------


class TestOpenCodeRunnerLaunchEnv:
    """Verify OpenCodeRunner constructs the required VECTL_ORCH_* env vars."""

    def _make_request(
        self,
        *,
        step_id: str = "core.step",
        agent_id: str = "python-senior-tacit",
    ) -> ExecutionRequest:
        return ExecutionRequest(
            step_id=step_id,
            role=agent_id,
            runner="opencode",
            work_refs=(),
            agent_id=agent_id,
        )

    def test_launch_env_includes_all_handoff_vars(self) -> None:
        """Launch env must include all 5 VECTL_ORCH_* variables (RFC §8.5)."""
        runner = OpenCodeRunner(artifact_root=Path("/runs"))
        request = self._make_request()
        workspace = Path("/ws")

        env = runner._build_launch_env(request=request, workspace=workspace)

        for required_key in (
            "VECTL_ORCH_RUN_ID",
            "VECTL_ORCH_STEP_ID",
            "VECTL_ORCH_AGENT_ID",
            "VECTL_ORCH_PROMPT_PATH",
            "VECTL_ORCH_PROMPT_BUNDLE_PATH",
        ):
            assert required_key in env, f"Missing required env var: {required_key}"

    def test_launch_env_step_id_used_as_run_id(self) -> None:
        """VECTL_ORCH_RUN_ID must use request.step_id."""
        runner = OpenCodeRunner(artifact_root=Path("/runs"))
        request = self._make_request(step_id="phase.impl.step")
        workspace = Path("/ws")

        env = runner._build_launch_env(request=request, workspace=workspace)
        assert env["VECTL_ORCH_RUN_ID"] == "phase.impl.step"

    def test_launch_env_uses_run_id_from_prompt_bundle_path(self) -> None:
        """When present, run_id is derived from prompt bundle artifact path."""
        runner = OpenCodeRunner(artifact_root=Path("/runs"))
        request = ExecutionRequest(
            step_id="phase.impl.step",
            role="python-senior-tacit",
            runner="opencode",
            work_refs=(),
            agent_id="python-senior-tacit",
            prompt_bundle_path="/runs/01TESTRUN/input/prompt_bundle.json",
            runner_prompt_path="/runs/01TESTRUN/input/runner_prompt.md",
        )
        workspace = Path("/ws")

        env = runner._build_launch_env(request=request, workspace=workspace)
        assert env["VECTL_ORCH_RUN_ID"] == "01TESTRUN"
        assert env["VECTL_ORCH_PROMPT_BUNDLE_PATH"] == "/runs/01TESTRUN/input/prompt_bundle.json"

    def test_launch_env_work_refs_run_id_overrides_prompt_path(self) -> None:
        """work_refs run_id takes precedence when both sources exist."""
        runner = OpenCodeRunner(artifact_root=Path("/runs"))
        request = ExecutionRequest(
            step_id="phase.impl.step",
            role="python-senior-tacit",
            runner="opencode",
            work_refs=("run_id=01WORKREF",),
            agent_id="python-senior-tacit",
            prompt_bundle_path="/runs/01PATH/input/prompt_bundle.json",
            runner_prompt_path="/runs/01PATH/input/runner_prompt.md",
        )
        workspace = Path("/ws")

        env = runner._build_launch_env(request=request, workspace=workspace)
        assert env["VECTL_ORCH_RUN_ID"] == "01WORKREF"

    def test_launch_env_agent_id_correct(self) -> None:
        """VECTL_ORCH_AGENT_ID must use request.agent_id."""
        runner = OpenCodeRunner(artifact_root=Path("/runs"))
        request = self._make_request(agent_id="my-agent")
        workspace = Path("/ws")

        env = runner._build_launch_env(request=request, workspace=workspace)
        assert env["VECTL_ORCH_AGENT_ID"] == "my-agent"


# ---------------------------------------------------------------------
# 5. Launch behavior (with mock subprocess)
# ---------------------------------------------------------------------


class TestOpenCodeRunnerLaunch:
    """Verify OpenCodeRunner.launch() spawns a subprocess with correct argv and env."""

    def _make_request(
        self,
        *,
        step_id: str = "core.step",
        agent_id: str = "python-senior-tacit",
        session_id: str | None = None,
        request_mode: str = "start",
    ) -> ExecutionRequest:
        return ExecutionRequest(
            step_id=step_id,
            role=agent_id,
            runner="opencode",
            work_refs=(),
            agent_id=agent_id,
            request_mode=request_mode,
            session_id=session_id,
        )

    @patch("vectl.orchestration.runners.subprocess.Popen")
    def test_launch_spawns_subprocess_with_correct_argv(self, mock_popen: MagicMock) -> None:
        """launch() must spawn a subprocess matching the frozen command contract."""
        # Popen returns a mock process that appears "running"
        mock_process = MagicMock()
        mock_process.poll.return_value = None
        mock_popen.return_value = mock_process

        runner = OpenCodeRunner(artifact_root=Path("/runs"))
        request = self._make_request()
        workspace = Path("/ws")

        result = runner.launch(request=request, workspace=workspace)

        assert isinstance(result, RunnerLaunchResult)
        assert result.handle.runner == "opencode"
        assert result.handle.session_id is None  # start mode has no session
        mock_popen.assert_called_once()

        # Verify Popen was called with the correct argv
        call_args = mock_popen.call_args
        argv = list(call_args[0][0])  # First positional arg is the command list
        assert argv[0] == "opencode"
        assert "run" in argv
        assert "--format" in argv
        assert "json" in argv
        assert "--dir" in argv
        assert "--agent" in argv
        assert "--file" in argv
        assert "--session" not in argv  # start mode

    @patch("vectl.orchestration.runners.subprocess.Popen")
    def test_launch_uses_correct_working_directory(self, mock_popen: MagicMock) -> None:
        """launch() must set cwd to the workspace path."""
        mock_process = MagicMock()
        mock_process.poll.return_value = None
        mock_popen.return_value = mock_process

        runner = OpenCodeRunner(artifact_root=Path("/runs"))
        request = self._make_request()
        workspace = Path("/my/workspace")

        runner.launch(request=request, workspace=workspace)

        call_args = mock_popen.call_args
        assert call_args[1]["cwd"] == "/my/workspace"

    @patch("vectl.orchestration.runners.subprocess.Popen")
    def test_launch_sets_stdin_devnull(self, mock_popen: MagicMock) -> None:
        """launch() must set stdin=DEVNULL to prevent TUI interaction."""
        mock_process = MagicMock()
        mock_process.poll.return_value = None
        mock_popen.return_value = mock_process

        runner = OpenCodeRunner(artifact_root=Path("/runs"))
        request = self._make_request()
        workspace = Path("/ws")

        runner.launch(request=request, workspace=workspace)

        call_args = mock_popen.call_args
        assert call_args[1]["stdin"] == subprocess.DEVNULL

    @patch("vectl.orchestration.runners.subprocess.Popen")
    def test_launch_captures_stdout_stderr(self, mock_popen: MagicMock) -> None:
        """launch() must capture stdout and stderr for evidence refs."""
        mock_process = MagicMock()
        mock_process.poll.return_value = None
        mock_popen.return_value = mock_process

        runner = OpenCodeRunner(artifact_root=Path("/runs"))
        request = self._make_request()
        workspace = Path("/ws")

        runner.launch(request=request, workspace=workspace)

        call_args = mock_popen.call_args
        assert call_args[1]["stdout"] == subprocess.PIPE
        assert call_args[1]["stderr"] == subprocess.PIPE
        assert call_args[1]["text"] is True
        assert call_args[1]["encoding"] == "utf-8"
        assert call_args[1]["errors"] == "replace"

    @patch("vectl.orchestration.runners.subprocess.Popen")
    def test_launch_raises_on_oserror(self, mock_popen: MagicMock) -> None:
        """launch() must raise RunnerLaunchError on OSError."""
        mock_popen.side_effect = OSError("command not found: opencode")

        runner = OpenCodeRunner(artifact_root=Path("/runs"))
        request = self._make_request()
        workspace = Path("/ws")

        with pytest.raises(RunnerLaunchError) as exc_info:
            runner.launch(request=request, workspace=workspace)
        assert exc_info.value.reason == "resource_unavailable"

    @patch("vectl.orchestration.runners.subprocess.Popen")
    def test_launch_env_includes_handoff_vars(self, mock_popen: MagicMock) -> None:
        """launch() must pass VECTL_ORCH_* env vars to subprocess."""
        mock_process = MagicMock()
        mock_process.poll.return_value = None
        mock_popen.return_value = mock_process

        runner = OpenCodeRunner(artifact_root=Path("/runs"))
        request = self._make_request(step_id="core.step", agent_id="my-agent")
        workspace = Path("/ws")

        runner.launch(request=request, workspace=workspace)

        call_args = mock_popen.call_args
        env = call_args[1]["env"]
        assert "VECTL_ORCH_RUN_ID" in env
        assert "VECTL_ORCH_STEP_ID" in env
        assert "VECTL_ORCH_AGENT_ID" in env
        assert "VECTL_ORCH_PROMPT_PATH" in env
        assert "VECTL_ORCH_PROMPT_BUNDLE_PATH" in env


# ---------------------------------------------------------------------
# 6. Resume behavior
# ---------------------------------------------------------------------


class TestOpenCodeRunnerResume:
    """Verify OpenCodeRunner.resume() uses --session for deterministic continuation."""

    def _make_request(
        self,
        *,
        step_id: str = "phase.step",
        agent_id: str = "python-senior-tacit",
        session_id: str = "sess-abc-123",
    ) -> ExecutionRequest:
        return ExecutionRequest(
            step_id=step_id,
            role=agent_id,
            runner="opencode",
            work_refs=(),
            agent_id=agent_id,
            request_mode="resume",
            session_id=session_id,
        )

    @patch("vectl.orchestration.runners.subprocess.Popen")
    def test_resume_spawns_subprocess_with_session(self, mock_popen: MagicMock) -> None:
        """resume() must include --session <session_id> in argv (RFC §9.3)."""
        mock_process = MagicMock()
        mock_process.poll.return_value = None
        mock_popen.return_value = mock_process

        runner = OpenCodeRunner(artifact_root=Path("/runs"))
        request = self._make_request(session_id="mysession-789")
        workspace = Path("/ws")

        result = runner.resume(request=request, workspace=workspace)

        assert isinstance(result, RunnerLaunchResult)
        assert result.handle.session_id == "mysession-789"

        call_args = mock_popen.call_args
        argv = list(call_args[0][0])
        assert "--session" in argv
        session_idx = argv.index("--session")
        assert argv[session_idx + 1] == "mysession-789"

    @patch("vectl.orchestration.runners.subprocess.Popen")
    def test_resume_does_not_use_continue(self, mock_popen: MagicMock) -> None:
        """resume() must NOT use --continue (RFC §15.2)."""
        mock_process = MagicMock()
        mock_process.poll.return_value = None
        mock_popen.return_value = mock_process

        runner = OpenCodeRunner(artifact_root=Path("/runs"))
        request = self._make_request()
        workspace = Path("/ws")

        runner.resume(request=request, workspace=workspace)

        call_args = mock_popen.call_args
        argv = list(call_args[0][0])
        assert "--continue" not in argv, "--continue is forbidden in orchestration (RFC §15.2)"

    @patch("vectl.orchestration.runners.subprocess.Popen")
    def test_resume_uses_resume_bootstrap_message(self, mock_popen: MagicMock) -> None:
        """resume() must use the frozen resume bootstrap message."""
        mock_process = MagicMock()
        mock_process.poll.return_value = None
        mock_popen.return_value = mock_process

        runner = OpenCodeRunner(artifact_root=Path("/runs"))
        request = self._make_request()
        workspace = Path("/ws")

        runner.resume(request=request, workspace=workspace)

        call_args = mock_popen.call_args
        argv = list(call_args[0][0])
        assert argv[-1] == OpenCodeLaunchConfig().bootstrap_resume

    def test_resume_raises_on_missing_session_id(self) -> None:
        """resume() must raise RunnerResumeError when session_id is missing."""
        runner = OpenCodeRunner(artifact_root=Path("/runs"))
        request = ExecutionRequest(
            step_id="phase.step",
            role="python-senior-tacit",
            runner="opencode",
            work_refs=(),
            agent_id="python-senior-tacit",
            request_mode="resume",
            session_id=None,  # Missing session_id
        )
        workspace = Path("/ws")

        with pytest.raises(RunnerResumeError) as exc_info:
            runner.resume(request=request, workspace=workspace)
        assert exc_info.value.reason == "session_missing"
        assert "--continue is forbidden" in str(exc_info.value)

    def test_resume_raises_on_empty_session_id(self) -> None:
        """resume() must raise RunnerResumeError when session_id is empty."""
        runner = OpenCodeRunner(artifact_root=Path("/runs"))
        request = ExecutionRequest(
            step_id="phase.step",
            role="python-senior-tacit",
            runner="opencode",
            work_refs=(),
            agent_id="python-senior-tacit",
            request_mode="resume",
            session_id="",  # Empty session_id
        )
        workspace = Path("/ws")

        with pytest.raises(RunnerResumeError) as exc_info:
            runner.resume(request=request, workspace=workspace)
        assert exc_info.value.reason == "session_missing"

    @patch("vectl.orchestration.runners.subprocess.Popen")
    def test_resume_raises_on_oserror(self, mock_popen: MagicMock) -> None:
        """resume() must raise RunnerLaunchError on OSError."""
        mock_popen.side_effect = OSError("command not found: opencode")

        runner = OpenCodeRunner(artifact_root=Path("/runs"))
        request = self._make_request()
        workspace = Path("/ws")

        with pytest.raises(RunnerLaunchError) as exc_info:
            runner.resume(request=request, workspace=workspace)
        assert exc_info.value.reason == "resource_unavailable"


# ---------------------------------------------------------------------
# 7. Poll behavior
# ---------------------------------------------------------------------


class TestOpenCodeRunnerPoll:
    """Verify OpenCodeRunner.poll() correctly handles subprocess states."""

    def _write_opencode_text_part(self, data_home: Path, session_id: str, text: str) -> None:
        db_dir = data_home / "opencode"
        db_dir.mkdir(parents=True)
        connection = sqlite3.connect(db_dir / "opencode.db")
        try:
            connection.execute(
                "create table part ("
                "id text primary key, message_id text, session_id text, "
                "time_created integer, time_updated integer, data text)"
            )
            connection.execute(
                "insert into part values (?, ?, ?, ?, ?, ?)",
                (
                    "prt-final",
                    "msg-final",
                    session_id,
                    1,
                    1,
                    json.dumps({"type": "text", "text": text}),
                ),
            )
            connection.commit()
        finally:
            connection.close()

    def _make_request(
        self,
        *,
        step_id: str = "core.step",
        agent_id: str = "python-senior-tacit",
    ) -> ExecutionRequest:
        return ExecutionRequest(
            step_id=step_id,
            role=agent_id,
            runner="opencode",
            work_refs=(),
            agent_id=agent_id,
        )

    @patch("vectl.orchestration.runners.subprocess.Popen")
    def test_poll_returns_running_while_process_active(self, mock_popen: MagicMock) -> None:
        """poll() must return 'running' while the process is still active."""
        mock_process = MagicMock()
        mock_process.poll.return_value = None  # Still running
        mock_process.wait.side_effect = subprocess.TimeoutExpired(cmd="opencode", timeout=0.01)
        mock_popen.return_value = mock_process

        runner = OpenCodeRunner(artifact_root=Path("/runs"))
        request = self._make_request()
        workspace = Path("/ws")

        launch_result = runner.launch(request=request, workspace=workspace)
        poll_result = runner.poll(launch_result.handle)

        assert poll_result.status == "running"
        assert (
            "OpenCode" in poll_result.output_summary or "still active" in poll_result.output_summary
        )

    @patch("vectl.orchestration.runners.subprocess.Popen")
    def test_poll_returns_success_on_exit_0(self, mock_popen: MagicMock) -> None:
        """poll() must return 'success' when OpenCode exits with code 0."""
        mock_process = MagicMock()
        mock_process.poll.return_value = 0  # Completed with exit code 0
        mock_process.stdout = MagicMock()
        mock_process.stdout.read.return_value = "Task completed"
        mock_process.stderr = MagicMock()
        mock_process.stderr.read.return_value = ""
        mock_popen.return_value = mock_process

        runner = OpenCodeRunner(artifact_root=Path("/runs"))
        request = self._make_request()
        workspace = Path("/ws")

        launch_result = runner.launch(request=request, workspace=workspace)
        poll_result = runner.poll(launch_result.handle)

        assert poll_result.status == "success"
        assert (
            "exit 0" in poll_result.output_summary
            or "completed successfully" in poll_result.output_summary
        )

    @patch("vectl.orchestration.runners.subprocess.Popen")
    def test_poll_summarizes_opencode_json_event_text(
        self, mock_popen: MagicMock
    ) -> None:
        """poll() extracts assistant text from OpenCode JSON event stdout."""
        mock_process = MagicMock()
        mock_process.poll.return_value = 0
        event = {
            "type": "part",
            "sessionID": "ses_123",
            "timestamp": "2026-04-26T00:00:00Z",
            "part": {
                "type": "text",
                "text": '{"status":"unblocked","summary":"ok"}',
            },
        }
        mock_process.stdout = MagicMock()
        mock_process.stdout.read.return_value = json.dumps(event)
        mock_process.stderr = MagicMock()
        mock_process.stderr.read.return_value = ""
        mock_popen.return_value = mock_process

        runner = OpenCodeRunner(artifact_root=Path("/runs"))
        launch_result = runner.launch(request=self._make_request(), workspace=Path("/ws"))
        poll_result = runner.poll(launch_result.handle)

        assert poll_result.status == "success"
        assert 'stdout={"status":"unblocked","summary":"ok"}' in (
            poll_result.output_summary
        )
        assert "sessionID" not in poll_result.output_summary

    @patch("vectl.orchestration.runners.subprocess.Popen")
    def test_poll_prefers_final_opencode_text_event(
        self, mock_popen: MagicMock
    ) -> None:
        """poll() keeps final answer text instead of earlier progress text."""
        mock_process = MagicMock()
        mock_process.poll.return_value = 0
        progress_event = {
            "type": "text",
            "part": {"type": "text", "text": "I will inspect the case first."},
        }
        final_event = {
            "type": "text",
            "part": {
                "type": "text",
                "text": '{"status":"unblocked","summary":"done"}',
            },
        }
        mock_process.stdout = MagicMock()
        mock_process.stdout.read.return_value = "\n".join(
            [json.dumps(progress_event), json.dumps(final_event)]
        )
        mock_process.stderr = MagicMock()
        mock_process.stderr.read.return_value = ""
        mock_popen.return_value = mock_process

        runner = OpenCodeRunner(artifact_root=Path("/runs"))
        launch_result = runner.launch(request=self._make_request(), workspace=Path("/ws"))
        poll_result = runner.poll(launch_result.handle)

        assert poll_result.status == "success"
        assert 'stdout={"status":"unblocked","summary":"done"}' in (
            poll_result.output_summary
        )
        assert "I will inspect" not in poll_result.output_summary

    @patch("vectl.orchestration.runners.subprocess.Popen")
    def test_poll_extracts_report_from_long_opencode_event_stream(
        self, mock_popen: MagicMock
    ) -> None:
        """poll() finds report JSON even after large tool-output events."""
        mock_process = MagicMock()
        mock_process.poll.return_value = 0
        large_tool_event = {
            "type": "tool_use",
            "part": {
                "type": "tool",
                "state": {"output": "x" * 6000},
            },
        }
        final_event = {
            "type": "text",
            "part": {
                "type": "text",
                "text": '{"status":"unblocked","summary":"after tools"}',
            },
        }
        mock_process.stdout = MagicMock()
        mock_process.stdout.read.return_value = "\n".join(
            [json.dumps(large_tool_event), json.dumps(final_event)]
        )
        mock_process.stderr = MagicMock()
        mock_process.stderr.read.return_value = ""
        mock_popen.return_value = mock_process

        runner = OpenCodeRunner(artifact_root=Path("/runs"))
        launch_result = runner.launch(request=self._make_request(), workspace=Path("/ws"))
        poll_result = runner.poll(launch_result.handle)

        assert poll_result.status == "success"
        assert 'stdout={"status":"unblocked","summary":"after tools"}' in (
            poll_result.output_summary
        )
        assert "x" * 100 not in poll_result.output_summary

    @patch("vectl.orchestration.runners.subprocess.Popen")
    def test_poll_extracts_structured_review_payload_from_event_stream(
        self, mock_popen: MagicMock
    ) -> None:
        """poll() finds StructuredReviewResult payloads as machine output."""
        mock_process = MagicMock()
        mock_process.poll.return_value = 0
        final_event = {
            "type": "message",
            "part": {
                "review_outcome": "pass",
                "summary": "review passed",
                "findings": [],
                "evidence_refs": ["review://ok"],
            },
        }
        mock_process.stdout = MagicMock()
        mock_process.stdout.read.return_value = json.dumps(final_event)
        mock_process.stderr = MagicMock()
        mock_process.stderr.read.return_value = ""
        mock_popen.return_value = mock_process

        runner = OpenCodeRunner(artifact_root=Path("/runs"))
        launch_result = runner.launch(request=self._make_request(), workspace=Path("/ws"))
        poll_result = runner.poll(launch_result.handle)

        assert poll_result.status == "success"
        assert 'stdout={"review_outcome":"pass","summary":"review passed"' in (
            poll_result.output_summary
        )
        assert "sessionID" not in poll_result.output_summary

    @patch("vectl.orchestration.runners.subprocess.Popen")
    def test_poll_falls_back_to_opencode_db_when_stdout_lacks_final_text(
        self, mock_popen: MagicMock, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """OpenCode sometimes stores final assistant text only in its session DB."""
        data_home = tmp_path / "xdg-data"
        monkeypatch.setenv("XDG_DATA_HOME", str(data_home))
        payload = {
            "review_outcome": "pass",
            "summary": "review passed from db",
            "findings": [],
            "evidence_refs": ["review://db"],
        }
        self._write_opencode_text_part(data_home, "ses_db", json.dumps(payload))

        mock_process = MagicMock()
        mock_process.poll.return_value = 0
        mock_process.stdout = MagicMock()
        mock_process.stdout.read.return_value = json.dumps(
            {"type": "step_finish", "sessionID": "ses_db", "part": {"type": "step-finish"}}
        )
        mock_process.stderr = MagicMock()
        mock_process.stderr.read.return_value = ""
        mock_popen.return_value = mock_process

        runner = OpenCodeRunner(artifact_root=Path("/runs"))
        launch_result = runner.launch(request=self._make_request(), workspace=Path("/ws"))
        poll_result = runner.poll(launch_result.handle)

        assert poll_result.status == "success"
        assert 'stdout={"review_outcome":"pass","summary":"review passed from db"' in (
            poll_result.output_summary
        )
        assert poll_result.session_id == "ses_db"

    @patch("vectl.orchestration.runners.subprocess.Popen")
    def test_poll_does_not_truncate_machine_payloads(
        self, mock_popen: MagicMock
    ) -> None:
        """Machine contract JSON must stay parseable even when it is long."""
        mock_process = MagicMock()
        mock_process.poll.return_value = 0
        payload = {
            "review_outcome": "needs_fix",
            "summary": "review found long evidence",
            "findings": ["x" * 6000],
            "evidence_refs": ["review://long"],
        }
        final_event = {"type": "message", "part": {"type": "text", "text": json.dumps(payload)}}
        mock_process.stdout = MagicMock()
        mock_process.stdout.read.return_value = json.dumps(final_event)
        mock_process.stderr = MagicMock()
        mock_process.stderr.read.return_value = ""
        mock_popen.return_value = mock_process

        runner = OpenCodeRunner(artifact_root=Path("/runs"))
        launch_result = runner.launch(request=self._make_request(), workspace=Path("/ws"))
        poll_result = runner.poll(launch_result.handle)

        _prefix, _sep, stdout_payload = poll_result.output_summary.partition("stdout=")
        parsed = json.loads(stdout_payload)
        assert parsed["review_outcome"] == "needs_fix"
        assert parsed["findings"] == ["x" * 6000]

    @patch("vectl.orchestration.runners.subprocess.Popen")
    def test_poll_omits_raw_opencode_json_when_no_final_text(
        self, mock_popen: MagicMock, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """poll() does not persist raw event JSON when OpenCode emits no final text."""
        monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "empty-xdg"))
        mock_process = MagicMock()
        mock_process.poll.return_value = 0
        event = {"type": "session.updated", "sessionID": "ses_123"}
        mock_process.stdout = MagicMock()
        mock_process.stdout.read.return_value = json.dumps(event)
        mock_process.stderr = MagicMock()
        mock_process.stderr.read.return_value = ""
        mock_popen.return_value = mock_process

        runner = OpenCodeRunner(artifact_root=Path("/runs"))
        launch_result = runner.launch(request=self._make_request(), workspace=Path("/ws"))
        poll_result = runner.poll(launch_result.handle)

        assert poll_result.status == "success"
        assert "stdout=OpenCode emitted JSON event stream without final text" in (
            poll_result.output_summary
        )
        assert "sessionID" not in poll_result.output_summary

    @patch("vectl.orchestration.runners.subprocess.Popen")
    def test_poll_ignores_status_summary_protocol_envelope(
        self, mock_popen: MagicMock, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """poll() must not mistake OpenCode envelope metadata for a report."""
        monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "empty-xdg"))
        mock_process = MagicMock()
        mock_process.poll.return_value = 0
        event = {
            "type": "message",
            "sessionID": "ses_123",
            "timestamp": "2026-04-26T00:00:00Z",
            "status": "unblocked",
            "summary": "protocol summary, not a ResolutionReport",
        }
        mock_process.stdout = MagicMock()
        mock_process.stdout.read.return_value = json.dumps(event)
        mock_process.stderr = MagicMock()
        mock_process.stderr.read.return_value = ""
        mock_popen.return_value = mock_process

        runner = OpenCodeRunner(artifact_root=Path("/runs"))
        launch_result = runner.launch(request=self._make_request(), workspace=Path("/ws"))
        poll_result = runner.poll(launch_result.handle)

        assert poll_result.status == "success"
        assert "stdout=OpenCode emitted JSON event stream without final text" in (
            poll_result.output_summary
        )
        assert "protocol summary" not in poll_result.output_summary

    @patch("vectl.orchestration.runners.subprocess.Popen")
    def test_poll_returns_fail_on_nonzero_exit(self, mock_popen: MagicMock) -> None:
        """poll() must return 'fail' when OpenCode exits with nonzero code."""
        mock_process = MagicMock()
        mock_process.poll.return_value = 1
        mock_process.stdout = MagicMock()
        mock_process.stdout.read.return_value = ""
        mock_process.stderr = MagicMock()
        mock_process.stderr.read.return_value = "error: something went wrong"
        mock_popen.return_value = mock_process

        runner = OpenCodeRunner(artifact_root=Path("/runs"))
        request = self._make_request()
        workspace = Path("/ws")

        launch_result = runner.launch(request=request, workspace=workspace)
        poll_result = runner.poll(launch_result.handle)

        assert poll_result.status == "fail"
        assert "exit code 1" in poll_result.output_summary or "failed" in poll_result.output_summary

    @patch("vectl.orchestration.runners.subprocess.Popen")
    def test_poll_returns_stall_on_signal(self, mock_popen: MagicMock) -> None:
        """poll() must return 'stall' when OpenCode is terminated by signal."""
        mock_process = MagicMock()
        mock_process.poll.return_value = -15  # SIGTERM
        mock_process.stdout = MagicMock()
        mock_process.stdout.read.return_value = ""
        mock_process.stderr = MagicMock()
        mock_process.stderr.read.return_value = ""
        mock_popen.return_value = mock_process

        runner = OpenCodeRunner(artifact_root=Path("/runs"))
        request = self._make_request()
        workspace = Path("/ws")

        launch_result = runner.launch(request=request, workspace=workspace)
        poll_result = runner.poll(launch_result.handle)

        assert poll_result.status == "stall"

    @patch("vectl.orchestration.runners.subprocess.Popen")
    def test_poll_preserves_stdout_evidence_refs(self, mock_popen: MagicMock) -> None:
        """poll() must include stdout in evidence_refs (RFC §9.4)."""
        mock_process = MagicMock()
        mock_process.poll.return_value = 0
        mock_process.stdout = MagicMock()
        mock_process.stdout.read.return_value = "A" * 100  # Some output
        mock_process.stderr = MagicMock()
        mock_process.stderr.read.return_value = ""
        mock_popen.return_value = mock_process

        runner = OpenCodeRunner(artifact_root=Path("/runs"))
        request = self._make_request()
        workspace = Path("/ws")

        launch_result = runner.launch(request=request, workspace=workspace)
        poll_result = runner.poll(launch_result.handle)

        assert poll_result.evidence_refs is not None
        assert any("stdout" in ref for ref in poll_result.evidence_refs), (
            f"poll() must include stdout in evidence_refs, got {poll_result.evidence_refs}"
        )

    @patch("vectl.orchestration.runners.subprocess.Popen")
    def test_poll_preserves_stderr_evidence_refs(self, mock_popen: MagicMock) -> None:
        """poll() must include stderr in evidence_refs on failure (RFC §9.4)."""
        mock_process = MagicMock()
        mock_process.poll.return_value = 1
        mock_process.stdout = MagicMock()
        mock_process.stdout.read.return_value = ""
        mock_process.stderr = MagicMock()
        mock_process.stderr.read.return_value = "Error: task failed"
        mock_popen.return_value = mock_process

        runner = OpenCodeRunner(artifact_root=Path("/runs"))
        request = self._make_request()
        workspace = Path("/ws")

        launch_result = runner.launch(request=request, workspace=workspace)
        poll_result = runner.poll(launch_result.handle)

        assert any("stderr" in ref for ref in poll_result.evidence_refs), (
            "poll() must include stderr in evidence_refs on failure, "
            f"got {poll_result.evidence_refs}"
        )

    def test_poll_raises_on_unknown_handle(self) -> None:
        """poll() must raise RunnerPollError for unknown handle."""
        runner = OpenCodeRunner(artifact_root=Path("/runs"))
        bad_handle = RunnerHandle(runner="opencode", run_id="nonexistent", session_id=None)

        with pytest.raises(RunnerPollError) as exc_info:
            runner.poll(bad_handle)
        assert exc_info.value.reason == "handle_unknown"

    @patch("vectl.orchestration.runners.subprocess.Popen")
    def test_poll_preserves_session_id(self, mock_popen: MagicMock) -> None:
        """poll() must preserve session_id from the handle."""
        mock_process = MagicMock()
        mock_process.poll.return_value = 0
        mock_process.stdout = MagicMock()
        mock_process.stdout.read.return_value = ""
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
            request_mode="resume",
            session_id="mysession-456",
        )
        workspace = Path("/ws")

        launch_result = runner.resume(request=request, workspace=workspace)
        poll_result = runner.poll(launch_result.handle)

        assert poll_result.session_id == "mysession-456"

    @patch("vectl.orchestration.runners.subprocess.Popen")
    def test_poll_cleans_up_handle_after_completion(self, mock_popen: MagicMock) -> None:
        """poll() must remove the process from _processes after completion."""
        mock_process = MagicMock()
        mock_process.poll.return_value = 0
        mock_process.stdout = MagicMock()
        mock_process.stdout.read.return_value = ""
        mock_process.stderr = MagicMock()
        mock_process.stderr.read.return_value = ""
        mock_popen.return_value = mock_process

        runner = OpenCodeRunner(artifact_root=Path("/runs"))
        request = self._make_request()
        workspace = Path("/ws")

        launch_result = runner.launch(request=request, workspace=workspace)
        assert launch_result.handle.run_id in runner._processes

        runner.poll(launch_result.handle)

        # After poll completes, handle should be cleaned up
        assert launch_result.handle.run_id not in runner._processes


# ---------------------------------------------------------------------
# 8. Cancel behavior
# ---------------------------------------------------------------------


class TestOpenCodeRunnerCancel:
    """Verify OpenCodeRunner.cancel() sends terminate and does not discard metadata."""

    def _make_request(
        self,
        *,
        step_id: str = "core.step",
        agent_id: str = "python-senior-tacit",
    ) -> ExecutionRequest:
        return ExecutionRequest(
            step_id=step_id,
            role=agent_id,
            runner="opencode",
            work_refs=(),
            agent_id=agent_id,
        )

    @patch("vectl.orchestration.runners.subprocess.Popen")
    def test_cancel_sends_terminate(self, mock_popen: MagicMock) -> None:
        """cancel() must call process.terminate() (RFC §9.5)."""
        mock_process = MagicMock()
        mock_process.poll.return_value = None  # Still running
        mock_process.wait.side_effect = subprocess.TimeoutExpired(cmd="opencode", timeout=0.01)
        mock_popen.return_value = mock_process

        runner = OpenCodeRunner(artifact_root=Path("/runs"))
        request = self._make_request()
        workspace = Path("/ws")

        launch_result = runner.launch(request=request, workspace=workspace)
        runner.cancel(launch_result.handle)

        mock_process.terminate.assert_called_once()

    def test_cancel_is_noop_on_unknown_handle(self) -> None:
        """cancel() must not raise on unknown handle (process already exited)."""
        runner = OpenCodeRunner(artifact_root=Path("/runs"))
        unknown_handle = RunnerHandle(runner="opencode", run_id="nonexistent", session_id=None)

        # Should not raise
        runner.cancel(unknown_handle)

    @patch("vectl.orchestration.runners.subprocess.Popen")
    def test_cancel_preserves_session_metadata(self, mock_popen: MagicMock) -> None:
        """cancel() must not silently discard session metadata needed for recovery.

        Authority: RFC §9.5 — 'Cancellation should terminate the current execution
        process, but must not silently discard the persisted session metadata
        needed for later recovery.'
        """
        mock_process = MagicMock()
        mock_process.poll.return_value = None
        mock_process.wait.side_effect = subprocess.TimeoutExpired(cmd="opencode", timeout=0.01)
        mock_popen.return_value = mock_process

        runner = OpenCodeRunner(artifact_root=Path("/runs"))
        request = ExecutionRequest(
            step_id="core.step",
            role="python-senior-tacit",
            runner="opencode",
            work_refs=(),
            agent_id="python-senior-tacit",
            request_mode="resume",
            session_id="preserved-session-789",
        )
        workspace = Path("/ws")

        launch_result = runner.resume(request=request, workspace=workspace)

        # Cancel the process
        runner.cancel(launch_result.handle)

        # The handle still has the session_id — recovery metadata is preserved
        assert launch_result.handle.session_id == "preserved-session-789"

    @patch("vectl.orchestration.runners.subprocess.Popen")
    def test_cancel_raises_on_terminate_error(self, mock_popen: MagicMock) -> None:
        """cancel() must raise RunnerCancelError if terminate fails."""
        mock_process = MagicMock()
        mock_process.poll.return_value = None
        mock_process.wait.side_effect = subprocess.TimeoutExpired(cmd="opencode", timeout=0.01)
        mock_process.terminate.side_effect = OSError("permission denied")
        mock_popen.return_value = mock_process

        runner = OpenCodeRunner(artifact_root=Path("/runs"))
        request = self._make_request()
        workspace = Path("/ws")

        launch_result = runner.launch(request=request, workspace=workspace)

        with pytest.raises(RunnerCancelError) as exc_info:
            runner.cancel(launch_result.handle)
        assert exc_info.value.reason == "transport_failed"


# ---------------------------------------------------------------------
# 9. OpenCodeContinueForbiddenError
# ---------------------------------------------------------------------


class TestOpenCodeContinueForbidden:
    """Verify the --continue forbidden error type exists and is well-formed."""

    def test_continue_forbidden_error_is_runner_error(self) -> None:
        """OpenCodeContinueForbiddenError must be a RunnerError subclass."""
        assert issubclass(OpenCodeContinueForbiddenError, RunnerError)

    def test_continue_forbidden_error_default_detail(self) -> None:
        """Default detail must mention --continue is forbidden."""
        error = OpenCodeContinueForbiddenError()
        assert "--continue" in str(error)
        assert "forbidden" in str(error).lower()

    def test_continue_forbidden_error_custom_detail(self) -> None:
        """Custom detail must be included in error message."""
        error = OpenCodeContinueForbiddenError(detail="custom reason here")
        assert "custom reason here" in str(error)

    def test_continue_forbidden_error_reason(self) -> None:
        """The reason must be 'continue_forbidden'."""
        error = OpenCodeContinueForbiddenError()
        assert error.reason == "continue_forbidden"


# ---------------------------------------------------------------------
# 10. Default agent_id fallback
# ---------------------------------------------------------------------


class TestOpenCodeRunnerDefaultAgentFallback:
    """Verify OpenCodeRunner falls back to 'opencode' when agent_id is empty."""

    def _make_request(self, agent_id: str = "") -> ExecutionRequest:
        return ExecutionRequest(
            step_id="core.step",
            role=agent_id,
            runner="opencode",
            work_refs=(),
            agent_id=agent_id,
        )

    @patch("vectl.orchestration.runners.subprocess.Popen")
    def test_launch_uses_fallback_agent_when_empty(self, mock_popen: MagicMock) -> None:
        """When agent_id is empty, launch should use 'opencode' as default."""
        mock_process = MagicMock()
        mock_process.poll.return_value = None
        mock_popen.return_value = mock_process

        runner = OpenCodeRunner(artifact_root=Path("/runs"))
        request = self._make_request(agent_id="")
        workspace = Path("/ws")

        runner.launch(request=request, workspace=workspace)

        call_args = mock_popen.call_args
        argv = list(call_args[0][0])
        agent_idx = argv.index("--agent")
        assert argv[agent_idx + 1] == "opencode", (
            "When agent_id is empty, --agent must default to 'opencode'"
        )

    def test_build_argv_uses_fallback_agent_when_empty(self) -> None:
        """_build_launch_argv must use 'opencode' as fallback when agent_id is empty."""
        runner = OpenCodeRunner(artifact_root=Path("/runs"))
        request = self._make_request(agent_id="")
        workspace = Path("/ws")

        argv = runner._build_launch_argv(request=request, workspace=workspace, session_id=None)
        agent_idx = argv.index("--agent")
        assert argv[agent_idx + 1] == "opencode"


# ---------------------------------------------------------------------
# 11. Integration: materialize → launch → env consistency
# ---------------------------------------------------------------------


class TestOpenCodeRunnerLaunchIntegration:
    """Integration tests for prompt materialization → runner launch consistency."""

    def test_materialized_prompt_paths_appear_in_launch_env(self, tmp_path: Path) -> None:
        """The launch env must reference the same paths as materialized artifacts.

        Authority: RFC §8, §9.2 — prompt artifacts are written to disk and then
        the VECTL_ORCH_PROMPT_PATH env var must point to the workspace copy.
        """
        artifact_root = tmp_path / "runs"
        workspace = tmp_path / "ws"
        artifact_root.mkdir()
        workspace.mkdir()

        # Use step_id as run_id since that's what _build_launch_env uses
        # for VECTL_ORCH_RUN_ID. This aligns with the runner's env construction.
        run_id = "phase.impl.step"
        step_id = "phase.impl.step"
        agent_id = "python-senior-tacit"

        # Materialize artifacts
        paths = resolve_prompt_artifact_paths(
            artifact_root=artifact_root,
            run_id=run_id,
            workspace=workspace,
        )
        bundle = PromptBundle(
            system_prompt="System instructions.",
            task_prompt="Integrate the runner.",
            messages=(),
        )
        materialize_prompt_artifacts(
            bundle=bundle,
            artifact_paths=paths,
            role_id=agent_id,
            agent_id=agent_id,
            runner="opencode",
        )

        # Build launch env via OpenCodeRunner
        runner = OpenCodeRunner(artifact_root=artifact_root)
        request = ExecutionRequest(
            step_id=step_id,
            role=agent_id,
            runner="opencode",
            work_refs=(),
            agent_id=agent_id,
            prompt_bundle_path=paths.prompt_bundle_path,
            runner_prompt_path=paths.runner_prompt_path,
        )

        env = runner._build_launch_env(request=request, workspace=workspace)

        # Verify the env vars point to the same materialized paths
        assert env["VECTL_ORCH_PROMPT_PATH"] == paths.workspace_prompt_path
        assert env["VECTL_ORCH_PROMPT_BUNDLE_PATH"] == paths.prompt_bundle_path

        # Verify the workspace copy exists at the expected path
        assert Path(env["VECTL_ORCH_PROMPT_PATH"]).exists()

    @patch("vectl.orchestration.runners.subprocess.Popen")
    def test_launch_argv_references_workspace_prompt(self, mock_popen: MagicMock) -> None:
        """The --file flag must reference .vectl/orch/runner_prompt.md."""
        mock_process = MagicMock()
        mock_process.poll.return_value = None
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

        runner.launch(request=request, workspace=workspace)

        call_args = mock_popen.call_args
        argv = list(call_args[0][0])
        file_idx = argv.index("--file")
        assert argv[file_idx + 1] == ".vectl/orch/runner_prompt.md", (
            f"--file must reference .vectl/orch/runner_prompt.md, got {argv[file_idx + 1]}"
        )
