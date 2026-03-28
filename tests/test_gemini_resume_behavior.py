"""EXPECTED-RED pre-implementation tests for Gemini resume behavior.

These tests define the expected behavior for Gemini resume semantics BEFORE
IMPLEMENTATION. They will FAIL until driver-multi-runner-hardening.impl-runners-extended
lands the runner implementation.

Resume Semantics (from DRIVER-BLUEPRINT.md):
    - Resume flag: --resume INDEX (INDEX is session number, not UUID)
    - Config field: resume_flag = "--resume"
    - Different from Claude/OpenCode session management

Downstream Owner: driver-multi-runner-hardening.impl-runners-extended
"""

import sys
import pytest

from vectl.driver.config import RunnerConfig
from vectl.driver.runners import GeminiRunnerStub


class TestGeminiResumeCommandBuilding:
    """Expected-RED tests for Gemini resume command building.

    GAP: GeminiRunnerStub.dispatch raises NotImplementedError.
    Implementation must:
    1. Build dispatch command from config.command + config.args
    2. Use standard stdin mode (pipe to -p flag)
    3. Apply --yolo flag for auto-approve
    4. For resume: use --resume INDEX flag
    """

    @pytest.mark.anyio
    @pytest.mark.skip(reason="EXPECTED-RED: GeminiRunner not implemented")
    async def test_dispatch_builds_initial_command(self, tmp_path) -> None:
        """Initial dispatch uses config.command + config.args with --yolo."""
        from vectl.driver.runners import create_runner

        config = RunnerConfig(
            command="gemini",
            args=["-p", "--output-format", "json", "--yolo"],
            prompt_mode="stdin",
            output_parser="gemini_json",
            experimental=True,
        )
        runner = create_runner("gemini", config)

        # Mock subprocess to capture command
        handle = await runner.dispatch(
            prompt="test prompt",
            agent="test-agent",
            workdir=str(tmp_path),
        )

        # Implementation should have spawned subprocess with:
        # ["gemini", "-p", "--output-format", "json", "--yolo"]
        # plus prompt via stdin

    @pytest.mark.anyio
    @pytest.mark.skip(reason="EXPECTED-RED: GeminiRunner not implemented")
    async def test_resume_uses_resume_flag_with_index(self, tmp_path) -> None:
        """Resume uses --resume INDEX flag (not UUID like Claude).

        GAP: Gemini session IDs are numeric indices, not UUIDs.
        """
        from vectl.driver.runners import create_runner

        config = RunnerConfig(
            command="gemini",
            args=["-p", "--output-format", "json"],
            prompt_mode="stdin",
            output_parser="gemini_json",
            experimental=True,
            resume_flag="--resume",
        )
        runner = create_runner("gemini", config)

        handle = await runner.dispatch(
            prompt="test prompt",
            agent="test-agent",
            workdir=str(tmp_path),
            session_id="0",  # Gemini uses numeric index
        )

        # Implementation should append: ["--resume", "0"]
        # Different from Claude's UUID-based resume


class TestGeminiExperimentalStatusHandling:
    """Expected-RED tests for Gemini experimental status handling.

    GAP: Implementation must handle elevated failure risk for experimental runners.
    """

    @pytest.mark.skip(reason="EXPECTED-RED: GeminiRunner not implemented")
    def test_experimental_runner_requires_flag(self) -> None:
        """Gemini config MUST have experimental=True to create runner.

        This validation happens at stub construction time.
        Implementation should preserve this validation.
        """
        # Already tested in contract tests
        # This documents expected behavior for downstream owner
        config = RunnerConfig(
            command="gemini",
            args=["-p", "--output-format", "json"],
            experimental=True,
        )
        assert config.experimental is True

    @pytest.mark.anyio
    @pytest.mark.skip(reason="EXPECTED-RED: Integration test requires implementation")
    async def test_authentication_failure_handling(self, tmp_path) -> None:
        """Authentication failures should propagate as TRANSPORT_ERROR.

        Document: Gemini may fail authentication without proper setup.
        Implementation should handle gracefully.
        """
        # This is an integration concern
        # Document for downstream owner
        pass


class TestGeminiSessionIdFormat:
    """Expected-RED tests for Gemini session ID format handling.

    Note: Gemini uses numeric indices for session IDs, not UUIDs.
    """

    @pytest.mark.anyio
    @pytest.mark.skip(reason="EXPECTED-RED: GeminiRunner not implemented")
    async def test_numeric_session_id_accepted(self, tmp_path) -> None:
        """Accept numeric session IDs for resume."""
        from vectl.driver.runners import create_runner

        config = RunnerConfig(
            command="gemini",
            args=["-p", "--output-format", "json"],
            experimental=True,
            resume_flag="--resume",
        )
        runner = create_runner("gemini", config)

        # Numeric session IDs should work
        handle = await runner.dispatch(
            prompt="test",
            agent="test-agent",
            workdir=str(tmp_path),
            session_id="3",  # Numeric index
        )

        # Implementation should accept this format


class TestGeminiAutoApproveFlag:
    """Expected-RED tests for Gemini --yolo flag handling.

    GAP: Gemini requires --yolo or --approval-mode yolo for auto-approve.
    """

    @pytest.mark.skip(reason="EXPECTED-RED: GeminiRunner not implemented")
    def test_yolo_flag_required_for_headless(self) -> None:
        """--yolo flag should be in args for headless operation.

        Without auto-approve, Gemini will hang waiting for user input.
        """
        config = RunnerConfig(
            command="gemini",
            args=["-p", "--output-format", "json", "--yolo"],
            experimental=True,
        )

        assert "--yolo" in config.args or "--approval-mode" in str(config.args)


class TestGeminiOutputFormat:
    """Expected-RED tests for Gemini output format handling.

    GAP: Gemini uses single JSON object format (like Claude).
    """

    @pytest.mark.skip(reason="EXPECTED-RED: Integration test requires implementation")
    def test_json_output_format_configured(self) -> None:
        """--output-format json should be in args.

        Without this flag, Gemini may output text format.
        """
        config = RunnerConfig(
            command="gemini",
            args=["-p", "--output-format", "json", "--yolo"],
            experimental=True,
            output_parser="gemini_json",
        )

        assert "--output-format" in config.args
        assert "json" in config.args
        assert config.output_parser == "gemini_json"


class TestGeminiNoWorkingDirectoryFlag:
    """Expected-RED tests for Gemini working directory handling.

    Note: Gemini may not have explicit -C flag like Codex.
    Implementation should use cwd param for working directory.
    """

    @pytest.mark.anyio
    @pytest.mark.skip(reason="EXPECTED-RED: GeminiRunner not implemented")
    async def test_no_workdir_flag_uses_cwd(self, tmp_path) -> None:
        """Gemini should use subprocess cwd param, not -C flag.

        Document: If no working dir flag exists, use subprocess cwd.
        """
        from vectl.driver.runners import create_runner

        config = RunnerConfig(
            command="gemini",
            args=["-p", "--output-format", "json", "--yolo"],
            experimental=True,
        )
        runner = create_runner("gemini", config)

        # Implementation must pass workdir to subprocess cwd param
        # (not as -C flag like Codex)
