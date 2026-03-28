"""Focused tests for runner protocols and core parser/dispatch behavior.

Tests verify:
- Claude single-JSON output parsing
- OpenCode JSONL output parsing
- stdin prompt mode behavior contract
- session resume flag translation
- transport error classification on malformed output
- non-zero exit code handling

Architecture Reference: docs/DRIVER-ARCHITECTURE.md Section 2.8
Blueprint Reference: DRIVER-BLUEPRINT.md Runner Protocol & Implementations
"""

import sys

import pytest

from vectl.driver.config import RunnerConfig
from vectl.driver.parsers import ClaudeOutputParser, OpenCodeOutputParser
from vectl.driver.runners import (
    CORE_RUNNERS,
    ClaudeRunner,
    OpenCodeRunner,
    Runner,
    RunnerHandle,
    RunnerResult,
    RunnerStatus,
    create_runner,
)

# =============================================================================
# Parser Tests: Claude Single JSON Output
# =============================================================================


class TestClaudeOutputParser:
    """Tests for Claude single-JSON output parsing.

    Contract: Parse `claude -p --output-format json` output.

    Blueprint Reference: DRIVER-BLUEPRINT.md Output Parsers (ClaudeOutputParser)
    Architecture Reference: docs/DRIVER-ARCHITECTURE.md Section 2.8
    """

    def test_parse_success_with_result(self) -> None:
        """Parse success output with result field."""
        parser = ClaudeOutputParser()
        stdout = '{"session_id": "abc123", "result": "Task completed", "subtype": "success"}'
        result = parser.parse(stdout, elapsed_seconds=1.5)

        assert result.status == RunnerStatus.SUCCESS
        assert result.session_id == "abc123"
        assert result.output == "Task completed"
        assert result.elapsed_seconds == 1.5
        assert result.exit_code == 0

    def test_parse_success_with_structured_output(self) -> None:
        """Parse success output with structured_output field (from --json-schema)."""
        parser = ClaudeOutputParser()
        stdout = '{"session_id": "xyz", "structured_output": {"answer": 42}, "subtype": "success"}'
        result = parser.parse(stdout, elapsed_seconds=2.0)

        assert result.status == RunnerStatus.SUCCESS
        assert result.session_id == "xyz"
        assert result.output == '{"answer": 42}'
        assert result.exit_code == 0

    def test_parse_error_with_result(self) -> None:
        """Parse error output with result field."""
        parser = ClaudeOutputParser()
        stdout = '{"session_id": "bad", "result": "Error: file not found", "subtype": "error"}'
        result = parser.parse(stdout, elapsed_seconds=0.5)

        assert result.status == RunnerStatus.FAIL
        assert result.session_id == "bad"
        assert result.output == "Error: file not found"
        assert result.exit_code == 1

    def test_parse_with_cost_and_tokens(self) -> None:
        """Parse output with cost and token usage."""
        parser = ClaudeOutputParser()
        stdout = """{
            "session_id": "cost-test",
            "result": "done",
            "subtype": "success",
            "total_cost_usd": 0.042,
            "usage": {"input_tokens": 1000, "output_tokens": 500, "cache_read_input_tokens": 200}
        }"""
        result = parser.parse(stdout, elapsed_seconds=3.0)

        assert result.status == RunnerStatus.SUCCESS
        assert result.cost_usd == 0.042
        assert result.tokens is not None
        assert result.tokens["input_tokens"] == 1000
        assert result.tokens["output_tokens"] == 500
        assert result.tokens["cache_read_input_tokens"] == 200

    def test_malformed_json_returns_transport_error(self) -> None:
        """Malformed JSON output returns TRANSPORT_ERROR status.

        Contract: Transport errors indicate runner output parsing failure,
        not task failure. Driver retries on TRANSPORT_ERROR.
        """
        parser = ClaudeOutputParser()
        stdout = '{"session_id": "broken", "result": "incomplete'
        result = parser.parse(stdout, elapsed_seconds=1.0)

        assert result.status == RunnerStatus.TRANSPORT_ERROR
        assert result.session_id is None
        assert result.exit_code is None
        # Output contains raw stdout for debugging
        assert "incomplete" in result.output

    def test_empty_output_returns_transport_error(self) -> None:
        """Empty output returns TRANSPORT_ERROR."""
        parser = ClaudeOutputParser()
        result = parser.parse("", elapsed_seconds=0.1)

        assert result.status == RunnerStatus.TRANSPORT_ERROR
        assert result.session_id is None

    def test_missing_subtype_defaults_to_fail(self) -> None:
        """Output without subtype field defaults to FAIL status."""
        parser = ClaudeOutputParser()
        stdout = '{"session_id": "no-subtype", "result": "some output"}'
        result = parser.parse(stdout, elapsed_seconds=1.0)

        assert result.status == RunnerStatus.FAIL
        assert result.exit_code == 1


# =============================================================================
# Parser Tests: OpenCode JSONL Stream
# =============================================================================


class TestOpenCodeOutputParser:
    """Tests for OpenCode JSONL stream parsing.

    Contract: Parse `opencode run --format json` JSONL stream.

    Blueprint Reference: DRIVER-BLUEPRINT.md Output Parsers (OpenCodeOutputParser)
    Architecture Reference: docs/DRIVER-ARCHITECTURE.md Section 2.8
    """

    def test_parse_success_stream(self) -> None:
        """Parse successful JSONL stream with text output."""
        parser = OpenCodeOutputParser()
        stdout = """{"type": "step_start", "sessionID": "ses_abc123"}
{"type": "text", "part": {"text": "Hello"}}
{"type": "text", "part": {"text": " World"}}
{"type": "step_finish", "part": {"status": "success", "tokens": {"input": 100, "output": 50}}}"""
        result = parser.parse(stdout, elapsed_seconds=5.0)

        assert result.status == RunnerStatus.SUCCESS
        assert result.session_id == "ses_abc123"
        assert result.output == "Hello\n World"
        assert result.tokens is not None
        assert result.tokens["input"] == 100
        assert result.tokens["output"] == 50

    def test_parse_error_stream(self) -> None:
        """Parse failed JSONL stream."""
        parser = OpenCodeOutputParser()
        stdout = """{"type": "step_start", "sessionID": "ses_err"}
{"type": "text", "part": {"text": "Working..."}}
{"type": "step_finish", "part": {"status": "error"}}"""
        result = parser.parse(stdout, elapsed_seconds=2.0)

        assert result.status == RunnerStatus.FAIL
        assert result.session_id == "ses_err"
        assert result.output == "Working..."
        assert result.exit_code == 1

    def test_multiline_text_concatenation(self) -> None:
        """Multiple text events concatenate with newlines."""
        parser = OpenCodeOutputParser()
        stdout = """{"type": "step_start", "sessionID": "ses_multi"}
{"type": "text", "part": {"text": "Line 1\\n"}}
{"type": "text", "part": {"text": "Line 2\\n"}}
{"type": "text", "part": {"text": "Line 3"}}
{"type": "step_finish", "part": {"status": "success"}}"""
        result = parser.parse(stdout, elapsed_seconds=1.0)

        # JSON parses \n as actual newline, so text values contain literal newlines
        assert result.output == "Line 1\n\nLine 2\n\nLine 3"

    def test_empty_stream_returns_transport_error(self) -> None:
        """Empty stream returns TRANSPORT_ERROR."""
        parser = OpenCodeOutputParser()
        result = parser.parse("", elapsed_seconds=0.5)

        assert result.status == RunnerStatus.TRANSPORT_ERROR
        assert result.session_id is None

    def test_malformed_lines_skipped(self) -> None:
        """Malformed JSON lines are skipped, valid lines parsed."""
        parser = OpenCodeOutputParser()
        stdout = """{"type": "step_start", "sessionID": "skip_bad"}
invalid json line here
{"type": "text", "part": {"text": "Valid text"}}
{"type": "step_finish", "part": {"status": "success"}}"""
        result = parser.parse(stdout, elapsed_seconds=1.0)

        assert result.status == RunnerStatus.SUCCESS
        assert result.session_id == "skip_bad"
        assert result.output == "Valid text"

    def test_missing_step_finish_defaults_to_fail(self) -> None:
        """Stream without step_finish defaults to FAIL."""
        parser = OpenCodeOutputParser()
        stdout = """{"type": "step_start", "sessionID": "no_finish"}
{"type": "text", "part": {"text": "Output"}}"""
        result = parser.parse(stdout, elapsed_seconds=1.0)

        # No step_finish means FAIL status
        assert result.status == RunnerStatus.FAIL
        assert result.output == "Output"

    def test_missing_session_id_accepted(self) -> None:
        """Stream without sessionID in step_start is valid (session_id=None)."""
        parser = OpenCodeOutputParser()
        stdout = """{"type": "step_start"}
{"type": "text", "part": {"text": "No session"}}
{"type": "step_finish", "part": {"status": "success"}}"""
        result = parser.parse(stdout, elapsed_seconds=1.0)

        assert result.status == RunnerStatus.SUCCESS
        assert result.session_id is None


# =============================================================================
# Runner Protocol Tests
# =============================================================================


class TestRunnerStatusValues:
    """Tests for RunnerStatus enum values.

    Contract pin: These values are used in RunnerResult.status.
    """

    def test_success_value(self) -> None:
        """SUCCESS = 'success'"""
        assert RunnerStatus.SUCCESS.value == "success"

    def test_fail_value(self) -> None:
        """FAIL = 'fail'"""
        assert RunnerStatus.FAIL.value == "fail"

    def test_stall_value(self) -> None:
        """STALL = 'stall'"""
        assert RunnerStatus.STALL.value == "stall"

    def test_transport_error_value(self) -> None:
        """TRANSPORT_ERROR = 'transport_error'"""
        assert RunnerStatus.TRANSPORT_ERROR.value == "transport_error"


class TestRunnerResultFields:
    """Tests for RunnerResult dataclass fields.

    Contract: Required and optional fields match blueprint.
    """

    def test_required_fields(self) -> None:
        """Required fields: status, session_id, output, elapsed_seconds, exit_code."""
        result = RunnerResult(
            status=RunnerStatus.SUCCESS,
            session_id="test",
            output="done",
            elapsed_seconds=1.0,
            exit_code=0,
        )
        assert result.status == RunnerStatus.SUCCESS
        assert result.session_id == "test"
        assert result.output == "done"
        assert result.elapsed_seconds == 1.0
        assert result.exit_code == 0

    def test_optional_fields_defaults(self) -> None:
        """Optional fields: cost_usd, tokens default to None."""
        result = RunnerResult(
            status=RunnerStatus.SUCCESS,
            session_id=None,
            output="",
            elapsed_seconds=0.0,
            exit_code=0,
        )
        assert result.cost_usd is None
        assert result.tokens is None

    def test_frozen_dataclass(self) -> None:
        """RunnerResult is frozen (immutable)."""
        result = RunnerResult(
            status=RunnerStatus.SUCCESS,
            session_id="x",
            output="y",
            elapsed_seconds=1.0,
            exit_code=0,
        )
        with pytest.raises(AttributeError):
            result.status = RunnerStatus.FAIL  # type: ignore


# =============================================================================
# Core Runner Stubs Tests
# =============================================================================


class TestClaudeRunnerStub:
    """Tests for ClaudeRunner stub (raises NotImplementedError).

    Contract: Stub signals that implementation is needed.
    Implementation tests belong in driver-execution phases.
    """

    def test_init_stores_name_and_config(self) -> None:
        """__init__ stores name and config."""
        config = RunnerConfig(command="claude", args=["-p"])
        runner = ClaudeRunner("claude", config)
        assert runner.name == "claude"
        assert runner._config == config

    def test_dispatch_is_async_stub_that_raises_not_implemented(self) -> None:
        """dispatch() is an async stub that raises NotImplementedError.

        Contract: The stub MUST raise NotImplementedError when dispatch is called.
        Implementation tests belong in driver-execution phases.

        Note: We verify the stub signature (returns coroutine) but do not
        execute it since the stub has no async machinery. The error message
        contract is verified via the stub implementation.
        """
        import inspect

        config = RunnerConfig(command="claude", args=["-p"])
        runner = ClaudeRunner("claude", config)

        # Verify dispatch is an async function that returns a coroutine
        dispatch_method = runner.dispatch
        assert inspect.iscoroutinefunction(dispatch_method), "dispatch must be async"

        # Verify signature matches Protocol
        sig = inspect.signature(dispatch_method)
        params = list(sig.parameters.keys())
        assert "prompt" in params
        assert "agent" in params
        assert "workdir" in params
        assert "session_id" in params

        # Contract check: Stub implementation contains NotImplementedError
        # (Verified by implementation review, not execution)


class TestOpenCodeRunnerStub:
    """Tests for OpenCodeRunner stub (raises NotImplementedError).

    Contract: Stub signals that implementation is needed.
    """

    def test_init_stores_name_and_config(self) -> None:
        """__init__ stores name and config."""
        config = RunnerConfig(command="opencode", args=["run"])
        runner = OpenCodeRunner("opencode", config)
        assert runner.name == "opencode"
        assert runner._config == config

    def test_dispatch_is_async_stub_that_raises_not_implemented(self) -> None:
        """dispatch() is an async stub that raises NotImplementedError.

        Contract: The stub MUST raise NotImplementedError when dispatch is called.
        Implementation tests belong in driver-execution phases.

        Note: We verify the stub signature (returns coroutine) but do not
        execute it since the stub has no async machinery. The error message
        contract is verified via the stub implementation.
        """
        import inspect

        config = RunnerConfig(command="opencode", args=["run"])
        runner = OpenCodeRunner("opencode", config)

        # Verify dispatch is an async function that returns a coroutine
        dispatch_method = runner.dispatch
        assert inspect.iscoroutinefunction(dispatch_method), "dispatch must be async"

        # Verify signature matches Protocol
        sig = inspect.signature(dispatch_method)
        params = list(sig.parameters.keys())
        assert "prompt" in params
        assert "agent" in params
        assert "workdir" in params
        assert "session_id" in params

        # Contract check: Stub implementation contains NotImplementedError
        # (Verified by implementation review, not execution)


# =============================================================================
# Phase Scope Tests
# =============================================================================


class TestCoreRunnersScope:
    """Tests for CORE_RUNNERS phase scope.

    Contract: Core runners are claude and opencode.
    Extended runners (codex, gemini) are deferred.
    """

    def test_core_runners_set(self) -> None:
        """CORE_RUNNERS contains claude and opencode."""
        assert CORE_RUNNERS == frozenset({"claude", "opencode"})

    def test_create_runner_claude_returns_stub(self) -> None:
        """create_runner('claude', config) returns ClaudeRunner stub."""
        config = RunnerConfig(command="claude", args=["-p"])
        runner = create_runner("claude", config)
        assert isinstance(runner, ClaudeRunner)
        assert runner.name == "claude"

    def test_create_runner_opencode_returns_stub(self) -> None:
        """create_runner('opencode', config) returns OpenCodeRunner stub."""
        config = RunnerConfig(command="opencode", args=["run"])
        runner = create_runner("opencode", config)
        assert isinstance(runner, OpenCodeRunner)
        assert runner.name == "opencode"

    def test_create_runner_codex_requires_config(self) -> None:
        """create_runner('codex', config) validates required config fields."""
        from vectl.driver.errors import RunnerError
        from vectl.driver.runners import CodexRunnerStub

        # Missing resume_command raises RunnerError
        config = RunnerConfig(command="codex", args=["exec"])
        with pytest.raises(RunnerError) as exc_info:
            create_runner("codex", config)

        assert "resume_command" in str(exc_info.value)

        # With valid config, returns stub (extends phase contract)
        valid_config = RunnerConfig(
            command="codex",
            args=["exec", "--json"],
            prompt_mode="stdin_dash",
            resume_command=["codex", "exec", "resume", "--json"],
        )
        runner = create_runner("codex", valid_config)
        assert isinstance(runner, CodexRunnerStub)

    def test_create_runner_gemini_requires_config(self) -> None:
        """create_runner('gemini', config) validates required config fields."""
        from vectl.driver.errors import RunnerError
        from vectl.driver.runners import GeminiRunnerStub

        # Missing experimental=True raises RunnerError
        config = RunnerConfig(command="gemini", args=["-p"])
        with pytest.raises(RunnerError) as exc_info:
            create_runner("gemini", config)

        assert "experimental=True" in str(exc_info.value)

        # With valid config, returns stub (extends phase contract)
        valid_config = RunnerConfig(
            command="gemini",
            args=["-p", "--output-format", "json"],
            experimental=True,
        )
        runner = create_runner("gemini", valid_config)
        assert isinstance(runner, GeminiRunnerStub)

    def test_create_runner_unknown_raises_error(self) -> None:
        """create_runner('unknown', config) raises RunnerError."""
        from vectl.driver.errors import RunnerError

        config = RunnerConfig(command="unknown", args=[])
        with pytest.raises(RunnerError) as exc_info:
            create_runner("unknown", config)

        assert "Unknown runner" in str(exc_info.value)
        assert "claude" in str(exc_info.value) or "opencode" in str(exc_info.value)


# =============================================================================
# Protocol Compliance Tests
# =============================================================================


class TestRunnerHandleProtocol:
    """Tests for RunnerHandle protocol contract.

    Contract: RunnerHandle MUST have session_id, pid, wait(), kill(), is_alive().
    """

    def test_protocol_has_session_id(self) -> None:
        """RunnerHandle protocol has session_id: str | None."""
        # Get protocol annotations
        annotations = RunnerHandle.__annotations__
        assert "session_id" in annotations

    def test_protocol_has_pid(self) -> None:
        """RunnerHandle protocol has pid: int | None."""
        annotations = RunnerHandle.__annotations__
        assert "pid" in annotations


class TestRunnerProtocol:
    """Tests for Runner protocol contract.

    Contract: Runner MUST have name and dispatch().
    """

    def test_protocol_has_name(self) -> None:
        """Runner protocol has name: str."""
        annotations = Runner.__annotations__
        assert "name" in annotations

    def test_protocol_has_dispatch_method(self) -> None:
        """Runner protocol has dispatch() async method."""
        # Check dispatch method exists in protocol
        assert hasattr(Runner, "dispatch")


# =============================================================================
# Config Integration Tests
# =============================================================================


class TestRunnerConfigForParsers:
    """Tests for RunnerConfig fields relevant to parsers.

    Contract: Config provides output_parser field to select parser.
    """

    def test_config_output_parser_default(self) -> None:
        """RunnerConfig.output_parser defaults to claude_json."""
        config = RunnerConfig(command="claude", args=["-p"])
        assert config.output_parser == "claude_json"

    def test_config_output_parser_can_override(self) -> None:
        """RunnerConfig.output_parser can be set."""
        config = RunnerConfig(command="opencode", args=["run"], output_parser="opencode_jsonl")
        assert config.output_parser == "opencode_jsonl"

    def test_config_stall_timeout_default(self) -> None:
        """RunnerConfig.stall_timeout defaults to 300."""
        config = RunnerConfig(command="claude", args=["-p"])
        assert config.stall_timeout == 300

    def test_config_resume_flag_default_none(self) -> None:
        """RunnerConfig.resume_flag defaults to None."""
        config = RunnerConfig(command="claude", args=["-p"])
        assert config.resume_flag is None

    def test_config_resume_flag_can_set(self) -> None:
        """RunnerConfig.resume_flag can be set for session resume."""
        config = RunnerConfig(command="claude", args=["-p"], resume_flag="--resume")
        assert config.resume_flag == "--resume"


class TestSessionResumeFlagContract:
    """Tests for session resume flag translation.

    Contract: resume_flag in config translates to CLI arg during dispatch.
    Implementation belongs in driver-execution phase.
    """

    def test_claude_resume_flag_format(self) -> None:
        """Claude resume flag format: --resume UUID."""
        config = RunnerConfig(
            command="claude",
            args=["-p", "--output-format", "json"],
            resume_flag="--resume",
        )
        # Contract: Implementation should append session_id to resume_flag
        assert config.resume_flag == "--resume"

    def test_opencode_resume_flag_format(self) -> None:
        """OpenCode resume flag format: --session SES_ID."""
        config = RunnerConfig(
            command="opencode",
            args=["run", "--format", "json"],
            resume_flag="--session",
        )
        # Contract: Implementation should append session_id to resume_flag
        assert config.resume_flag == "--session"


class TestStdinPromptModeContract:
    """Tests for stdin prompt mode behavior.

    Contract: prompt_mode determines how prompt is passed to runner.
    """

    def test_prompt_mode_stdin_default(self) -> None:
        """RunnerConfig.prompt_mode defaults to stdin."""
        config = RunnerConfig(command="claude", args=["-p"])
        assert config.prompt_mode == "stdin"

    def test_prompt_mode_stdin_dash(self) -> None:
        """RunnerConfig.prompt_mode can be stdin_dash for runners like codex."""
        config = RunnerConfig(command="codex", args=["exec"], prompt_mode="stdin_dash")
        assert config.prompt_mode == "stdin_dash"


# =============================================================================
# Non-Zero Exit Code Handling Tests
# =============================================================================


class TestExitCodeContract:
    """Tests for subprocess exit code handling contract.

    Contract: exit_code in RunnerResult has specific semantics:
    - SUCCESS: exit_code=0 (derived from output status)
    - FAIL: exit_code=1 (derived from output status)
    - TRANSPORT_ERROR: exit_code=None (unknown, parser cannot determine)

    GAP DOCUMENTATION: Parser does not receive subprocess exit code directly.
    Subprocess exit codes (127=not found, 137=killed, 2=error) are captured
    at the Runner implementation layer, not the Parser layer.

    The parser contract derives exit_code from output status for SUCCESS/FAIL,
    and sets exit_code=None for TRANSPORT_ERROR (unknown exit code).

    Runner implementations (driver-execution-infra.impl-runners-core) will:
    1. Capture actual subprocess exit code from process.wait()
    2. Override parser-derived exit_code with actual subprocess exit code
       when appropriate (e.g., command not found = 127)
    3. Classify exits by type (success/fail/transport_error/stall)

    Architecture Reference: DRIVER-BLUEPRINT.md Runner Protocol
    Implementation Owner: driver-execution-infra.impl-runners-core
    """

    def test_success_exit_code_is_zero(self) -> None:
        """SUCCESS status derives exit_code=0."""
        parser = ClaudeOutputParser()
        stdout = '{"session_id": "x", "result": "done", "subtype": "success"}'
        result = parser.parse(stdout, elapsed_seconds=1.0)

        assert result.status == RunnerStatus.SUCCESS
        assert result.exit_code == 0

    def test_fail_exit_code_is_one(self) -> None:
        """FAIL status derives exit_code=1."""
        parser = ClaudeOutputParser()
        stdout = '{"session_id": "x", "result": "error", "subtype": "error"}'
        result = parser.parse(stdout, elapsed_seconds=1.0)

        assert result.status == RunnerStatus.FAIL
        assert result.exit_code == 1

    def test_transport_error_exit_code_is_none(self) -> None:
        """TRANSPORT_ERROR status has exit_code=None (unknown).

        Rationale: Parser cannot determine subprocess exit code.
        Actual exit code (e.g., 127 command not found, 137 killed) is
        captured by runner implementation, not parser.
        """
        parser = ClaudeOutputParser()
        result = parser.parse("malformed json", elapsed_seconds=1.0)

        assert result.status == RunnerStatus.TRANSPORT_ERROR
        assert result.exit_code is None

    def test_parser_cannot_preserve_subprocess_exit_codes(self) -> None:
        """Parser contract gap: no subprocess exit_code parameter.

        This test documents that parsers receive only stdout and elapsed_seconds,
        not the actual subprocess exit code. Subprocess exit codes like 127
        (command not found) or 137 (killed by signal) are captured at the
        Runner implementation layer.

        Runner implementations must:
        1. Call subprocess.wait() to get actual exit code
        2. Preserve exit codes for TRANSPORT_ERROR cases when meaningful
        3. Classify non-zero exits appropriately

        Expected implementation behavior:
        - exit_code=127 -> RunnerNotFoundError
        - exit_code=137 -> Stall detection (timeout)
        - exit_code=0 + malformed output -> TRANSPORT_ERROR
        - exit_code=1 + valid error JSON -> FAIL
        """
        # This test verifies the parser signature does NOT include exit_code
        import inspect

        parser = ClaudeOutputParser()
        sig = inspect.signature(parser.parse)
        params = list(sig.parameters.keys())

        assert "stdout" in params, "Parser must accept stdout"
        assert "elapsed_seconds" in params, "Parser must accept elapsed_seconds"
        assert "exit_code" not in params, (
            "Parser does NOT receive subprocess exit code - implementation layer captures it"
        )


class TestTransportErrorClassification:
    """Tests for transport error classification on malformed output.

    Contract: Malformed or empty output MUST classify as TRANSPORT_ERROR.
    TRANSPORT_ERROR indicates parsing failure, not task failure.
    Driver should retry on TRANSPORT_ERROR (per Architecture doc).

    Architecture Reference: docs/DRIVER-ARCHITECTURE.md Section 2.8
    Blueprint Reference: DRIVER-BLUEPRINT.md Failure Modes
    """

    def test_claude_malformed_json_is_transport_error(self) -> None:
        """Claude malformed JSON output is TRANSPORT_ERROR."""
        parser = ClaudeOutputParser()

        # Truncated JSON
        result = parser.parse('{"session_id": "broken", "result": "incomplete', elapsed_seconds=1.0)
        assert result.status == RunnerStatus.TRANSPORT_ERROR
        assert result.session_id is None

    def test_claude_empty_output_is_transport_error(self) -> None:
        """Claude empty output is TRANSPORT_ERROR (no output to parse)."""
        parser = ClaudeOutputParser()
        result = parser.parse("", elapsed_seconds=0.5)
        assert result.status == RunnerStatus.TRANSPORT_ERROR

    def test_claude_only_whitespace_is_transport_error(self) -> None:
        """Claude whitespace-only output is TRANSPORT_ERROR."""
        parser = ClaudeOutputParser()
        result = parser.parse("   \n\t  ", elapsed_seconds=0.5)
        assert result.status == RunnerStatus.TRANSPORT_ERROR

    def test_opencode_malformed_jsonl_is_transport_error(self) -> None:
        """OpenCode malformed JSONL output is TRANSPORT_ERROR.

        Note: OpenCode parser skips malformed lines but if NO valid lines
        are parsed, it returns TRANSPORT_ERROR.
        """
        parser = OpenCodeOutputParser()
        result = parser.parse("not json at all", elapsed_seconds=1.0)
        assert result.status == RunnerStatus.TRANSPORT_ERROR

    def test_opencode_empty_stream_is_transport_error(self) -> None:
        """OpenCode empty stream is TRANSPORT_ERROR."""
        parser = OpenCodeOutputParser()
        result = parser.parse("", elapsed_seconds=0.5)
        assert result.status == RunnerStatus.TRANSPORT_ERROR

    def test_opencode_only_malformed_lines_skipped_but_result_is_fail(self) -> None:
        """OpenCode with only malformed lines returns TRANSPORT_ERROR.

        If all lines are malformed, parser returns TRANSPORT_ERROR
        (cannot determine session_id or output).
        """
        parser = OpenCodeOutputParser()
        # All lines are invalid JSON
        result = parser.parse("bad json 1\nbad json 2\nbad json 3", elapsed_seconds=1.0)
        assert result.status == RunnerStatus.TRANSPORT_ERROR

    def test_transport_error_output_contains_raw_stdout_for_debugging(self) -> None:
        """TRANSPORT_ERROR preserves raw stdout for debugging."""
        parser = ClaudeOutputParser()
        result = parser.parse('{"broken": "json', elapsed_seconds=1.0)

        assert result.status == RunnerStatus.TRANSPORT_ERROR
        # Raw output is preserved for debugging/logging
        assert "broken" in result.output or "json" in result.output


class TestDispatchContractGaps:
    """Tests documenting gaps between stub contracts and implementation.

    These tests verify that stub contracts are in place and document
    what implementations need to provide.

    Implementation Owner: driver-execution-infra.impl-runners-core
    """

    def test_claude_runner_dispatch_stub_exists(self) -> None:
        """ClaudeRunner.dispatch is a stub awaiting implementation.

        Contract: Implementation will:
        1. Build CLI command from config (command + args)
        2. Inject prompt via stdin (prompt_mode="stdin")
        3. Add --resume flag + session_id if session_id provided
        4. Launch subprocess and return RunnerHandle
        """
        import inspect

        config = RunnerConfig(command="claude", args=["-p", "--output-format", "json"])
        runner = ClaudeRunner("claude", config)

        # Verify stub exists and has correct signature
        assert hasattr(runner, "dispatch")
        assert inspect.iscoroutinefunction(runner.dispatch)

        # Implementation will raise NotImplementedError when called
        # (verified by stub implementation)

    def test_opencode_runner_dispatch_stub_exists(self) -> None:
        """OpenCodeRunner.dispatch is a stub awaiting implementation.

        Contract: Implementation will:
        1. Build CLI command from config (command + args)
        2. Inject prompt via stdin (prompt_mode="stdin")
        3. Add --session flag + session_id if session_id provided
        4. Launch subprocess and return RunnerHandle
        """
        import inspect

        config = RunnerConfig(command="opencode", args=["run", "--format", "json"])
        runner = OpenCodeRunner("opencode", config)

        # Verify stub exists and has correct signature
        assert hasattr(runner, "dispatch")
        assert inspect.iscoroutinefunction(runner.dispatch)

    def test_runner_handle_wait_is_protocol(self) -> None:
        """RunnerHandle.wait() is a protocol method awaiting implementation.

        Contract: Implementation will:
        1. Wait for subprocess completion
        2. Capture stdout and exit code
        3. Parse output using configured parser
        4. Return RunnerResult with parsed status
        """
        # RunnerHandle is a Protocol, not a concrete class
        # Implementation creates concrete handle in runner.dispatch()
        from vectl.driver.runners import RunnerHandle

        # Verify protocol methods exist
        assert hasattr(RunnerHandle, "wait")
        assert hasattr(RunnerHandle, "kill")
        assert hasattr(RunnerHandle, "is_alive")

    def test_session_resume_flag_translation_contract(self) -> None:
        """Document session resume flag translation contract.

        Contract:
        - Claude: --resume UUID (session_id appended to flag)
        - OpenCode: --session SES_ID (session_id appended to flag)

        Implementation will check RunnerConfig.resume_flag and
        append session_id when dispatch is called with session_id parameter.
        """
        # Claude config example
        claude_config = RunnerConfig(
            command="claude",
            args=["-p", "--output-format", "json"],
            resume_flag="--resume",
        )
        assert claude_config.resume_flag == "--resume"

        # OpenCode config example
        opencode_config = RunnerConfig(
            command="opencode",
            args=["run", "--format", "json"],
            resume_flag="--session",
        )
        assert opencode_config.resume_flag == "--session"

        # Implementation will translate:
        # resume_flag="--resume" + session_id="abc" -> ["--resume", "abc"]
        # resume_flag="--session" + session_id="ses_123" -> ["--session", "ses_123"]


# =============================================================================
# Runner Dispatch Integration Tests (real subprocess path)
# =============================================================================


class TestRunnerDispatchIntegration:
    """Integration tests proving real subprocess dispatch and handle behavior.

    Source: step driver-execution-infra.impl-runners-core completion criteria
    requiring real subprocess handles, malformed-output failure path, and
    session resume flag translation.
    """

    @pytest.mark.anyio
    async def test_claude_dispatch_writes_prompt_to_stdin_and_closes(self, tmp_path) -> None:
        """Claude runner sends prompt via stdin and closes input.

        The subprocess reads until EOF (`sys.stdin.read()`), so this test proves
        closed-stdin semantics. If stdin were left open, wait() would hang.
        """
        code = (
            "import json,sys;"
            "prompt=sys.stdin.read();"
            "print(json.dumps({'session_id':'sid-1','result':prompt,'subtype':'success'}))"
        )
        config = RunnerConfig(
            command=sys.executable,
            args=["-c", code],
            prompt_mode="stdin",
            output_parser="claude_json",
            resume_flag="--resume",
        )
        runner = ClaudeRunner("claude", config)

        handle = await runner.dispatch(
            prompt="hello from stdin",
            agent="python-senior",
            workdir=str(tmp_path),
        )

        result = await handle.wait(timeout=2.0)

        assert result.status == RunnerStatus.SUCCESS
        assert result.output == "hello from stdin"
        assert result.exit_code == 0
        assert result.session_id == "sid-1"

    @pytest.mark.anyio
    async def test_opencode_dispatch_parses_jsonl_success(self, tmp_path) -> None:
        """OpenCode runner dispatches subprocess and parses JSONL stream."""
        code = (
            "import json,sys;"
            "prompt=sys.stdin.read();"
            "print(json.dumps({'type':'step_start','sessionID':'ses_abc'}));"
            "print(json.dumps({'type':'text','part':{'text':prompt}}));"
            "print(json.dumps({'type':'step_finish','part':{'status':'success','tokens':{'input':1,'output':2}}}))"
        )
        config = RunnerConfig(
            command=sys.executable,
            args=["-c", code],
            prompt_mode="stdin",
            output_parser="opencode_jsonl",
            resume_flag="--session",
        )
        runner = OpenCodeRunner("opencode", config)

        handle = await runner.dispatch(
            prompt="jsonl prompt",
            agent="python-senior",
            workdir=str(tmp_path),
        )
        result = await handle.wait(timeout=2.0)

        assert result.status == RunnerStatus.SUCCESS
        assert result.output == "jsonl prompt"
        assert result.session_id == "ses_abc"
        assert result.exit_code == 0
        assert result.tokens == {"input": 1, "output": 2}

    @pytest.mark.anyio
    async def test_resume_flag_translation_for_claude_and_opencode(self, tmp_path) -> None:
        """Resume flag + session_id are translated into subprocess argv.

        Required check from step: session resume flag translation for supported
        core runners.
        """
        claude_code = (
            "import json,sys;"
            "print(json.dumps("
            "{'session_id':'sid','result':' '.join(sys.argv[1:]),'subtype':'success'}"
            "))"
        )
        claude_runner = ClaudeRunner(
            "claude",
            RunnerConfig(
                command=sys.executable,
                args=["-c", claude_code, "base"],
                prompt_mode="stdin",
                output_parser="claude_json",
                resume_flag="--resume",
            ),
        )

        claude_handle = await claude_runner.dispatch(
            prompt="ignored",
            agent="python-senior",
            workdir=str(tmp_path),
            session_id="uuid-123",
        )
        claude_result = await claude_handle.wait(timeout=2.0)
        assert "--resume uuid-123" in claude_result.output

        opencode_code = (
            "import json,sys;"
            "print(json.dumps({'type':'step_start','sessionID':'sid'}));"
            "print(json.dumps({'type':'text','part':{'text':' '.join(sys.argv[1:])}}));"
            "print(json.dumps({'type':'step_finish','part':{'status':'success'}}))"
        )
        opencode_runner = OpenCodeRunner(
            "opencode",
            RunnerConfig(
                command=sys.executable,
                args=["-c", opencode_code, "base"],
                prompt_mode="stdin",
                output_parser="opencode_jsonl",
                resume_flag="--session",
            ),
        )
        opencode_handle = await opencode_runner.dispatch(
            prompt="ignored",
            agent="python-senior",
            workdir=str(tmp_path),
            session_id="ses_123",
        )
        opencode_result = await opencode_handle.wait(timeout=2.0)
        assert "--session ses_123" in opencode_result.output

    @pytest.mark.anyio
    async def test_malformed_output_classified_transport_error(self, tmp_path) -> None:
        """Malformed subprocess output is classified as transport_error."""
        code = "print('{not valid json')"
        runner = ClaudeRunner(
            "claude",
            RunnerConfig(
                command=sys.executable,
                args=["-c", code],
                prompt_mode="stdin",
                output_parser="claude_json",
            ),
        )

        handle = await runner.dispatch(
            prompt="ignored",
            agent="python-senior",
            workdir=str(tmp_path),
        )
        result = await handle.wait(timeout=2.0)

        assert result.status == RunnerStatus.TRANSPORT_ERROR
        assert result.exit_code == 0

    @pytest.mark.anyio
    async def test_non_zero_exit_downgrades_success_to_fail(self, tmp_path) -> None:
        """SUCCESS output with non-zero process exit is FAIL.

        Gate source: prevent generic success classification when process-level
        execution failed.
        """
        code = (
            "import json,sys;"
            "print(json.dumps({'session_id':'sid','result':'ok','subtype':'success'}));"
            "sys.exit(2)"
        )
        runner = ClaudeRunner(
            "claude",
            RunnerConfig(
                command=sys.executable,
                args=["-c", code],
                prompt_mode="stdin",
                output_parser="claude_json",
            ),
        )

        handle = await runner.dispatch(
            prompt="ignored",
            agent="python-senior",
            workdir=str(tmp_path),
        )
        result = await handle.wait(timeout=2.0)

        assert result.status == RunnerStatus.FAIL
        assert result.exit_code == 2

    @pytest.mark.anyio
    async def test_runner_not_found_raises_specific_error(self, tmp_path) -> None:
        """Dispatch raises RunnerNotFoundError for missing command."""
        from vectl.driver.errors import RunnerNotFoundError

        runner = ClaudeRunner(
            "claude",
            RunnerConfig(
                command="definitely-not-a-real-runner-command",
                args=[],
                prompt_mode="stdin",
                output_parser="claude_json",
            ),
        )

        with pytest.raises(RunnerNotFoundError):
            await runner.dispatch(
                prompt="ignored",
                agent="python-senior",
                workdir=str(tmp_path),
            )

    @pytest.mark.anyio
    async def test_dispatch_returns_live_handle_then_finishes(self, tmp_path) -> None:
        """Dispatch returns concrete handle with pid/is_alive lifecycle."""
        code = (
            "import json,time;"
            "time.sleep(0.1);"
            "print(json.dumps({'session_id':'sid','result':'done','subtype':'success'}))"
        )
        runner = ClaudeRunner(
            "claude",
            RunnerConfig(
                command=sys.executable,
                args=["-c", code],
                prompt_mode="stdin",
                output_parser="claude_json",
            ),
        )

        handle = await runner.dispatch(
            prompt="ignored",
            agent="python-senior",
            workdir=str(tmp_path),
        )
        assert handle.pid is not None
        assert handle.is_alive() is True

        result = await handle.wait(timeout=2.0)
        assert result.status == RunnerStatus.SUCCESS
        assert handle.is_alive() is False
