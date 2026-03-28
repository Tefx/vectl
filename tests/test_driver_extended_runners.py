"""Contract tests for extended runners (CodexRunner, GeminiRunner).

These tests verify:
- EXTENDED_RUNNERS constant contains codex and gemini
- create_runner returns stubs for extended runners
- Stubs raise NotImplementedError for dispatch methods
- Stub initializers validate required config fields
- Resume semantics are documented in stub docstrings
- Parser semantics are documented in stub docstrings
- Experimental status handling for Gemini
- Fallback interaction documentation

Architecture Reference: docs/DRIVER-ARCHITECTURE.md Section 2.8
Blueprint Reference: DRIVER-BLUEPRINT.md Runner Protocol & Implementations
Blueprint Reference: DRIVER-BLUEPRINT.md Verified CLI Capabilities table
"""

import pytest

from vectl.driver.config import RunnerConfig
from vectl.driver.runners import (
    CORE_RUNNERS,
    EXTENDED_RUNNERS,
    CodexRunnerStub,
    GeminiRunnerStub,
    create_runner,
)
from vectl.driver.errors import RunnerError


# =============================================================================
# Extended Runners Scope Tests
# =============================================================================


class TestExtendedRunnersScope:
    """Tests for EXTENDED_RUNNERS phase scope.

    Contract: Extended runners are codex and gemini (contract-only in this phase).
    """

    def test_extended_runners_set(self) -> None:
        """EXTENDED_RUNNERS contains codex and gemini."""
        assert EXTENDED_RUNNERS == frozenset({"codex", "gemini"})

    def test_core_runners_unchanged(self) -> None:
        """CORE_RUNNERS remain claude and opencode only."""
        assert CORE_RUNNERS == frozenset({"claude", "opencode"})
        assert CORE_RUNNERS.isdisjoint(EXTENDED_RUNNERS)


# =============================================================================
# Codex Runner Stub Tests
# =============================================================================


class TestCodexRunnerStub:
    """Tests for CodexRunnerStub contract.

    Contract: Stub signals that implementation is deferred.
    """

    def test_init_requires_resume_command(self) -> None:
        """CodexRunnerStub requires resume_command in config.

        Resume Semantics (Blueprint):
            - Codex uses separate command: `codex exec resume <UUID>`
            - Config field: resume_command (list[str]) replaces dispatch command
        """
        # Missing resume_command raises RunnerError
        config = RunnerConfig(command="codex", args=["exec", "--json"])
        with pytest.raises(RunnerError) as exc_info:
            CodexRunnerStub("codex", config)
        assert "requires resume_command" in str(exc_info.value)

    def test_init_stores_name_and_config(self) -> None:
        """__init__ stores name and config when resume_command provided."""
        config = RunnerConfig(
            command="codex",
            args=["exec", "--json"],
            resume_command=["codex", "exec", "resume", "--json"],
        )
        stub = CodexRunnerStub("codex", config)
        assert stub.name == "codex"
        assert stub._config == config

    def test_dispatch_is_async_stub_that_raises_not_implemented(self) -> None:
        """dispatch() is an async stub that raises NotImplementedError.

        Contract: Stubs MUST raise NotImplementedError when dispatch is called.
        """
        import inspect

        config = RunnerConfig(
            command="codex",
            args=["exec", "--json"],
            prompt_mode="stdin_dash",
            resume_command=["codex", "exec", "resume", "--json"],
        )
        stub = CodexRunnerStub("codex", config)

        # Verify dispatch is an async function that returns a coroutine
        dispatch_method = stub.dispatch
        assert inspect.iscoroutinefunction(dispatch_method), "dispatch must be async"

        # Verify signature matches Protocol
        sig = inspect.signature(dispatch_method)
        params = list(sig.parameters.keys())
        assert "prompt" in params
        assert "agent" in params
        assert "workdir" in params
        assert "session_id" in params

    @pytest.mark.anyio
    async def test_dispatch_raises_not_implemented_error(self) -> None:
        """Calling dispatch raises NotImplementedError with phase reference."""
        config = RunnerConfig(
            command="codex",
            args=["exec", "--json"],
            prompt_mode="stdin_dash",
            resume_command=["codex", "exec", "resume", "--json"],
        )
        stub = CodexRunnerStub("codex", config)

        with pytest.raises(NotImplementedError) as exc_info:
            await stub.dispatch(prompt="test", agent="test-agent", workdir="/tmp")

        assert "STUB" in str(exc_info.value)
        assert "driver-multi-runner-hardening.execution" in str(exc_info.value)


class TestCodexResumeSemantics:
    """Tests documenting Codex resume semantics contract.

    Resume Semantics (Blueprint Section: Verified CLI Capabilities):
        - Resume command: `codex exec resume <UUID> --json`
        - Uses DIFFERENT command structure than initial dispatch
        - Session ID regex: ^[0-9a-f]{8}-
        - Config field: resume_command replaces dispatch command for resume

    Parser Semantics (Blueprint Section: Output Parsers):
        - Format: JSONL stream (thread.started -> item.completed -> turn.completed)
        - Session ID extraction: thread.started event
        - Status detection: presence of item.completed events
        - Token usage: turn.completed.usage
    """

    def test_resume_command_required_in_config(self) -> None:
        """Codex config MUST include resume_command field.

        This is different from core runners which use resume_flag.
        """
        config = RunnerConfig(
            command="codex",
            args=["exec", "--json"],
            prompt_mode="stdin_dash",
            resume_command=["codex", "exec", "resume", "--json"],
            session_id_regex="^[0-9a-f]{8}-",
        )
        assert config.resume_command is not None
        assert config.resume_command == ["codex", "exec", "resume", "--json"]

    def test_stdin_dash_mode_for_codex(self) -> None:
        """Codex uses stdin_dash mode with "-" argument for stdin input."""
        config = RunnerConfig(
            command="codex",
            args=["exec", "--json", "-C", "{workdir}"],
            prompt_mode="stdin_dash",
            resume_command=["codex", "exec", "resume", "--json"],
        )
        assert config.prompt_mode == "stdin_dash"


# =============================================================================
# Gemini Runner Stub Tests
# =============================================================================


class TestGeminiRunnerStub:
    """Tests for GeminiRunnerStub contract.

    Contract: Stub signals that implementation is deferred.
    """

    def test_init_requires_experimental_flag(self) -> None:
        """GeminiRunnerStub requires experimental=True in config.

        Experimental Status (Blueprint):
            - EXPERIMENTAL: true (auth issues, not fully verified)
            - Elevated failure risk
            - May require additional authentication setup
        """
        # Missing experimental flag raises RunnerError
        config = RunnerConfig(command="gemini", args=["-p", "--output-format", "json"])
        with pytest.raises(RunnerError) as exc_info:
            GeminiRunnerStub("gemini", config)
        assert "experimental=True" in str(exc_info.value)

    def test_init_stores_name_and_config_when_experimental(self) -> None:
        """__init__ stores name and config when experimental=True."""
        config = RunnerConfig(
            command="gemini",
            args=["-p", "--output-format", "json", "--yolo"],
            experimental=True,
        )
        stub = GeminiRunnerStub("gemini", config)
        assert stub.name == "gemini"
        assert stub._config == config

    def test_dispatch_is_async_stub_that_raises_not_implemented(self) -> None:
        """dispatch() is an async stub that raises NotImplementedError.

        Contract: Stubs MUST raise NotImplementedError when dispatch is called.
        """
        import inspect

        config = RunnerConfig(
            command="gemini",
            args=["-p", "--output-format", "json", "--yolo"],
            experimental=True,
        )
        stub = GeminiRunnerStub("gemini", config)

        # Verify dispatch is an async function
        dispatch_method = stub.dispatch
        assert inspect.iscoroutinefunction(dispatch_method), "dispatch must be async"

        # Verify signature matches Protocol
        sig = inspect.signature(dispatch_method)
        params = list(sig.parameters.keys())
        assert "prompt" in params
        assert "agent" in params
        assert "workdir" in params
        assert "session_id" in params

    @pytest.mark.anyio
    async def test_dispatch_raises_not_implemented_error(self) -> None:
        """Calling dispatch raises NotImplementedError with phase reference."""
        config = RunnerConfig(
            command="gemini",
            args=["-p", "--output-format", "json", "--yolo"],
            experimental=True,
        )
        stub = GeminiRunnerStub("gemini", config)

        with pytest.raises(NotImplementedError) as exc_info:
            await stub.dispatch(prompt="test", agent="test-agent", workdir="/tmp")

        assert "STUB" in str(exc_info.value)
        assert "driver-multi-runner-hardening.execution" in str(exc_info.value)


class TestGeminiExperimentalStatus:
    """Tests documenting Gemini experimental status handling.

    Experimental Status (Blueprint Section: Verified CLI Capabilities):
        - EXPERIMENTAL: true
        - Auth issues, not fully verified
        - Elevated failure risk
        - May require additional authentication setup
        - Cost tracking may be unavailable

    Known Issues:
        - Authentication may fail without proper setup
        - Cost tracking may not be available in output
        - Session resume semantics not fully verified
    """

    def test_experimental_must_be_true(self) -> None:
        """Gemini config MUST have experimental=True."""
        config = RunnerConfig(
            command="gemini",
            args=["-p", "--output-format", "json"],
            experimental=True,
        )
        assert config.experimental is True

    def test_yolo_approval_mode(self) -> None:
        """Gemini uses --yolo or --approval-mode yolo for auto-approve."""
        config = RunnerConfig(
            command="gemini",
            args=["-p", "--output-format", "json", "--yolo"],
            experimental=True,
        )
        # Contract: Implementation should use --yolo or --approval-mode yolo
        assert "--yolo" in config.args or "--approval-mode" in str(config.args)


class TestGeminiResumeSemantics:
    """Tests documenting Gemini resume semantics contract.

    Resume Semantics (Blueprint Section: Verified CLI Capabilities):
        - Resume flag: --resume INDEX (INDEX is session number, not UUID)
        - Config field: resume_flag = "--resume"
        - Different from Claude/OpenCode session management

    Parser Semantics (Blueprint Section: Output Parsers):
        - Format: Single JSON object (like Claude)
        - Session ID: session_id field (may be different format)
        - Status: subtype field (success/error)
        - Tokens: usage field
        - Cost: unverified (may be unavailable)
    """

    def test_resume_flag_format(self) -> None:
        """Gemini resume uses --resume INDEX flag."""
        config = RunnerConfig(
            command="gemini",
            args=["-p", "--output-format", "json"],
            experimental=True,
            resume_flag="--resume",
        )
        assert config.resume_flag == "--resume"

    def test_gemini_parser_semantics_like_claude(self) -> None:
        """Gemini output format is single JSON like Claude.

        Note: session_id format may differ; cost tracking unverified.
        """
        # This is documentation in config - no code change needed
        config = RunnerConfig(
            command="gemini",
            args=["-p", "--output-format", "json"],
            experimental=True,
            output_parser="gemini_json",
        )
        assert config.output_parser == "gemini_json"


# =============================================================================
# Create Runner Factory Tests
# =============================================================================


class TestCreateRunnerExtended:
    """Tests for create_runner factory with extended runners."""

    def test_create_runner_codex_returns_stub(self) -> None:
        """create_runner('codex', config) returns CodexRunnerStub (contract-only)."""
        config = RunnerConfig(
            command="codex",
            args=["exec", "--json"],
            prompt_mode="stdin_dash",
            resume_command=["codex", "exec", "resume", "--json"],
        )
        runner = create_runner("codex", config)
        assert isinstance(runner, CodexRunnerStub)
        assert runner.name == "codex"

    def test_create_runner_gemini_returns_stub(self) -> None:
        """create_runner('gemini', config) returns GeminiRunnerStub (contract-only)."""
        config = RunnerConfig(
            command="gemini",
            args=["-p", "--output-format", "json"],
            experimental=True,
        )
        runner = create_runner("gemini", config)
        assert isinstance(runner, GeminiRunnerStub)
        assert runner.name == "gemini"

    def test_create_runner_core_unchanged(self) -> None:
        """Core runners still return full implementations."""
        from vectl.driver.runners import ClaudeRunner, OpenCodeRunner

        claude_config = RunnerConfig(command="claude", args=["-p"])
        claude_runner = create_runner("claude", claude_config)
        assert isinstance(claude_runner, ClaudeRunner)

        opencode_config = RunnerConfig(command="opencode", args=["run"])
        opencode_runner = create_runner("opencode", opencode_config)
        assert isinstance(opencode_runner, OpenCodeRunner)


# =============================================================================
# Fallback Interactions Documentation Tests
# =============================================================================


class TestFallbackInteractions:
    """Tests documenting fallback interactions for extended runners.

    Fallback interactions (Blueprint Section: Configuration Schema):
        - Experimental runners have elevated failure risk
        - Fallback to core runner (opencode) after 2 consecutive failures
        - Session reuse may not work for experimental runners
        - Cost tracking may be unavailable for Gemini

    These are documented behaviors; implementation in driver execution phase.
    """

    def test_experimental_runner_elevated_failure_risk(self) -> None:
        """Experimental runners should have elevated failure risk handling.

        Contract: Driver should fallback to core runner after consecutive failures.
        """
        # This is handled in loop.py, not in runner contract
        # Config can track experimental status for logging/metrics
        config = RunnerConfig(
            command="gemini",
            args=["-p", "--output-format", "json"],
            experimental=True,
        )
        assert config.experimental is True

    def test_session_reuse_may_not_work_for_experimental(self) -> None:
        """Session reuse for experimental runners is not guaranteed.

        Contract: Driver should gracefully handle session resume failures.
        """
        # Session handling is in session.py
        # Experimental runners may have session issues
        config = RunnerConfig(
            command="gemini",
            args=["-p", "--output-format", "json"],
            experimental=True,
        )
        # No session persistence guarantee for experimental
        assert config.persist_session  # Default True, but not guaranteed


# =============================================================================
# Extended Parser Stub Tests
# =============================================================================


class TestCodexOutputParserStub:
    """Tests for CodexOutputParser stub contract.

    Parser Semantics (Blueprint Section: Output Parsers):
        - Format: JSONL stream (thread.started -> item.completed -> turn.completed)
        - Session ID: thread.started event -> thread_id field
        - Status: SUCCESS if item.completed events exist, FAIL otherwise
        - Output: Concatenated text from all item.completed events
        - Tokens: turn.completed.usage
    """

    def test_parse_raises_not_implemented(self) -> None:
        """CodexOutputParser.parse raises NotImplementedError."""
        from vectl.driver.parsers import CodexOutputParser

        parser = CodexOutputParser()
        with pytest.raises(NotImplementedError) as exc_info:
            parser.parse('{"type": "thread.started"}', 1.0)

        assert "STUB" in str(exc_info.value)
        assert "driver-multi-runner-hardening.execution" in str(exc_info.value)


class TestGeminiOutputParserStub:
    """Tests for GeminiOutputParser stub contract.

    Parser Semantics (Blueprint Section: Output Parsers):
        - Format: Single JSON object (like Claude)
        - Session ID: session_id field
        - Status: subtype field (success/error)
        - Output: result field
        - Tokens: usage field
        - Cost: unverified (may be unavailable)
    """

    def test_parse_raises_not_implemented(self) -> None:
        """GeminiOutputParser.parse raises NotImplementedError."""
        from vectl.driver.parsers import GeminiOutputParser

        parser = GeminiOutputParser()
        with pytest.raises(NotImplementedError) as exc_info:
            parser.parse('{"session_id": "x", "result": "y"}', 1.0)

        assert "STUB" in str(exc_info.value)
        assert "driver-multi-runner-hardening.execution" in str(exc_info.value)


# =============================================================================
# Config Extensions for Extended Runners
# =============================================================================


class TestRunnerConfigExtensions:
    """Tests for RunnerConfig fields required by extended runners."""

    def test_resume_command_field_exists(self) -> None:
        """RunnerConfig has resume_command field for Codex resume semantics."""
        config = RunnerConfig(
            command="codex",
            args=["exec"],
            resume_command=["codex", "exec", "resume"],
        )
        assert config.resume_command == ["codex", "exec", "resume"]

    def test_resume_command_defaults_to_none(self) -> None:
        """RunnerConfig.resume_command defaults to None for core runners."""
        config = RunnerConfig(command="claude", args=["-p"])
        assert config.resume_command is None

    def test_experimental_field_exists(self) -> None:
        """RunnerConfig has experimental field for Gemini runner status."""
        config = RunnerConfig(command="gemini", args=["-p"], experimental=True)
        assert config.experimental is True

    def test_experimental_defaults_to_false(self) -> None:
        """RunnerConfig.experimental defaults to False for core runners."""
        config = RunnerConfig(command="claude", args=["-p"])
        assert config.experimental is False
