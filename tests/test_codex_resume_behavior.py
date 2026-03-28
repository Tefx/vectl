"""EXPECTED-RED pre-implementation tests for Codex resume behavior.

These tests define the expected behavior for Codex resume semantics BEFORE
IMPLEMENTATION. They will FAIL until driver-multi-runner-hardening.impl-runners-extended
lands the runner implementation.

Resume Semantics (from DRIVER-BLUEPRINT.md):
    - Resume command: `codex exec resume <UUID> --json`
    - Uses DIFFERENT command structure than initial dispatch
    - Config field: resume_command replaces dispatch command for resume

Downstream Owner: driver-multi-runner-hardening.impl-runners-extended
"""

import sys
import pytest

from vectl.driver.config import RunnerConfig
from vectl.driver.runners import CodexRunnerStub


class TestCodexResumeCommandBuilding:
    """Expected-RED tests for Codex resume command building.

    GAP: CodexRunnerStub.dispatch raises NotImplementedError.
    Implementation must:
    1. Build dispatch command from config.command + config.args
    2. Use stdin_dash mode ("-" argument for stdin)
    3. For resume: use config.resume_command instead of dispatch command
    """

    @pytest.mark.anyio
    @pytest.mark.skip(reason="EXPECTED-RED: CodexRunner not implemented")
    async def test_dispatch_builds_initial_command(self, tmp_path) -> None:
        """Initial dispatch uses config.command + config.args."""
        from vectl.driver.runners import create_runner

        config = RunnerConfig(
            command="codex",
            args=["exec", "--json", "-C", "{workdir}"],
            prompt_mode="stdin_dash",
            output_parser="codex_jsonl",
            resume_command=["codex", "exec", "resume", "--json"],
        )
        runner = create_runner("codex", config)

        # Mock subprocess to capture command
        handle = await runner.dispatch(
            prompt="test prompt",
            agent="test-agent",
            workdir=str(tmp_path),
        )

        # Implementation should have spawned subprocess with:
        # ["codex", "exec", "--json", "-C", str(tmp_path), "-"]
        # We can't verify this directly without subprocess inspection
        # This test documents expected behavior

    @pytest.mark.anyio
    @pytest.mark.skip(reason="EXPECTED-RED: CodexRunner not implemented")
    async def test_resume_uses_resume_command(self, tmp_path) -> None:
        """Resume uses config.resume_command instead of dispatch command.

        GAP: Resume semantics differ from core runners. Codex uses a
        separate resume command structure, not a flag.
        """
        from vectl.driver.runners import create_runner

        config = RunnerConfig(
            command="codex",
            args=["exec", "--json", "-C", "{workdir}"],
            prompt_mode="stdin_dash",
            output_parser="codex_jsonl",
            resume_command=["codex", "exec", "resume", "--json"],
        )
        runner = create_runner("codex", config)

        handle = await runner.dispatch(
            prompt="test prompt",
            agent="test-agent",
            workdir=str(tmp_path),
            session_id="abc-123-def",  # Resume existing session
        )

        # Implementation should use resume_command:
        # ["codex", "exec", "resume", "abc-123-def", "--json", "-"]
        # NOT: ["codex", "exec", "--json", ...]


class TestCodexSessionIdExtraction:
    """Expected-RED tests for Codex session ID extraction from output.

    GAP: CodexRunnerStub must extract session_id from thread.started event.
    Integration tests require real subprocess; these define expected behavior.
    """

    @pytest.mark.anyio
    @pytest.mark.skip(reason="EXPECTED-RED: CodexRunner not implemented")
    async def test_session_id_from_thread_started_event(self, tmp_path) -> None:
        """Session ID extracted from thread.started event after dispatch.

        The handle.session_id should be populated from the first
        thread.started event in the JSONL output.
        """
        from vectl.driver.runners import create_runner

        config = RunnerConfig(
            command="codex",
            args=["exec", "--json"],
            prompt_mode="stdin_dash",
            output_parser="codex_jsonl",
            resume_command=["codex", "exec", "resume", "--json"],
        )
        runner = create_runner("codex", config)

        # This requires integration test with real subprocess
        # Unit test would mock subprocess output


class TestCodexStdinDashMode:
    """Expected-RED tests for Codex stdin_dash mode handling.

    GAP: Codex uses "-" argument for stdin input, not piped stdin.
    """

    @pytest.mark.skip(reason="EXPECTED-RED: CodexRunner not implemented")
    def test_config_stdin_dash_mode(self) -> None:
        """Codex config requires prompt_mode='stdin_dash'.

        This configures the runner to append "-" to args for stdin input.
        """
        config = RunnerConfig(
            command="codex",
            args=["exec", "--json"],
            prompt_mode="stdin_dash",
            resume_command=["codex", "exec", "resume", "--json"],
        )

        assert config.prompt_mode == "stdin_dash"
        # Implementation must: argv.append("-") when prompt_mode == "stdin_dash"


class TestCodexWorkingDirectoryFlag:
    """Expected-RED tests for Codex working directory handling.

    GAP: Codex uses -C flag for working directory, not cwd param.
    """

    @pytest.mark.skip(reason="EXPECTED-RED: CodexRunner not implemented")
    def test_config_working_dir_flag(self) -> None:
        """Codex uses -C flag in args template for working directory.

        Config should include -C {workdir} in args list.
        """
        config = RunnerConfig(
            command="codex",
            args=["exec", "--json", "-C", "{workdir}"],
            prompt_mode="stdin_dash",
            resume_command=["codex", "exec", "resume", "--json"],
        )

        assert "{workdir}" in config.args
        # Implementation must replace {workdir} with actual path
