"""EXPECTED-RED pre-implementation tests for shared runner protocol compatibility.

These tests verify that extended runners (Codex, Gemini) are compatible
with the shared Runner and RunnerHandle protocols. They define expected
behavior BEFORE implementation and will FAIL until
driver-multi-runner-hardening.impl-runners-extended lands.

Protocol compatibility ensures:
- All runners share the same dispatch() signature
- All handles share the same wait()/kill()/is_alive() interface
- Result types are consistent across all runners

Downstream Owner: driver-multi-runner-hardening.impl-runners-extended
"""

import sys
import pytest
from typing import Protocol, get_type_hints

from vectl.driver.runners import (
    Runner,
    RunnerHandle,
    RunnerResult,
    RunnerStatus,
)


class TestRunnerProtocolCompatibility:
    """Expected-RED tests for Runner protocol compatibility.

    GAP: Extended runners must implement the same Runner protocol.
    """

    @pytest.mark.skip(reason="EXPECTED-RED: Extended runners not implemented")
    def test_codex_implements_runner_protocol(self) -> None:
        """CodexRunner must satisfy the Runner protocol."""
        from vectl.driver.runners import create_runner
        from vectl.driver.config import RunnerConfig

        config = RunnerConfig(
            command="codex",
            args=["exec", "--json"],
            prompt_mode="stdin_dash",
            output_parser="codex_jsonl",
            resume_command=["codex", "exec", "resume", "--json"],
        )
        runner = create_runner("codex", config)

        # Must have 'name' attribute
        assert hasattr(runner, "name")
        assert runner.name == "codex"

        # Must have async dispatch() method with correct signature
        assert hasattr(runner, "dispatch")
        import inspect

        assert inspect.iscoroutinefunction(runner.dispatch)

        # Signature must match protocol
        sig = inspect.signature(runner.dispatch)
        params = list(sig.parameters.keys())
        assert "prompt" in params
        assert "agent" in params
        assert "workdir" in params
        assert "session_id" in params

    @pytest.mark.skip(reason="EXPECTED-RED: Extended runners not implemented")
    def test_gemini_implements_runner_protocol(self) -> None:
        """GeminiRunner must satisfy the Runner protocol."""
        from vectl.driver.runners import create_runner
        from vectl.driver.config import RunnerConfig

        config = RunnerConfig(
            command="gemini",
            args=["-p", "--output-format", "json", "--yolo"],
            prompt_mode="stdin",
            output_parser="gemini_json",
            experimental=True,
        )
        runner = create_runner("gemini", config)

        # Must have 'name' attribute
        assert hasattr(runner, "name")
        assert runner.name == "gemini"

        # Must have async dispatch() method
        import inspect

        assert inspect.iscoroutinefunction(runner.dispatch)


class TestRunnerHandleProtocolCompatibility:
    """Expected-RED tests for RunnerHandle protocol compatibility.

    GAP: Extended runner handles must implement the same RunnerHandle protocol.
    """

    @pytest.mark.anyio
    @pytest.mark.skip(reason="EXPECTED-RED: Extended runners not implemented")
    async def test_codex_handle_is_runner_handle(self, tmp_path) -> None:
        """CodexRunner.dispatch() returns a RunnerHandle-compatible object."""
        from vectl.driver.runners import create_runner
        from vectl.driver.config import RunnerConfig

        config = RunnerConfig(
            command=sys.executable,  # Use python for mock
            args=[
                "-c",
                "import json,sys; print(json.dumps({'thread_id':'x','items':[{'text':'y'}]}))",
            ],
            prompt_mode="stdin_dash",
            output_parser="codex_jsonl",
            resume_command=["python", "-c", "mock"],
        )
        runner = create_runner("codex", config)

        handle = await runner.dispatch(
            prompt="test",
            agent="test-agent",
            workdir=str(tmp_path),
        )

        # Must satisfy RunnerHandle protocol
        assert hasattr(handle, "session_id")
        assert hasattr(handle, "pid")
        assert hasattr(handle, "wait")
        assert hasattr(handle, "kill")
        assert hasattr(handle, "is_alive")

        # Must be async methods
        import inspect

        assert inspect.iscoroutinefunction(handle.wait)
        assert inspect.iscoroutinefunction(handle.kill)

    @pytest.mark.anyio
    @pytest.mark.skip(reason="EXPECTED-RED: Extended runners not implemented")
    async def test_gemini_handle_is_runner_handle(self, tmp_path) -> None:
        """GeminiRunner.dispatch() returns a RunnerHandle-compatible object."""
        from vectl.driver.runners import create_runner
        from vectl.driver.config import RunnerConfig

        config = RunnerConfig(
            command=sys.executable,  # Use python for mock
            args=[
                "-c",
                "import json,sys; print(json.dumps({'session_id':'x','result':'y','subtype':'success'}))",
            ],
            prompt_mode="stdin",
            output_parser="gemini_json",
            experimental=True,
        )
        runner = create_runner("gemini", config)

        handle = await runner.dispatch(
            prompt="test",
            agent="test-agent",
            workdir=str(tmp_path),
        )

        # Must satisfy RunnerHandle protocol
        assert hasattr(handle, "session_id")
        assert hasattr(handle, "pid")
        assert hasattr(handle, "wait")
        assert hasattr(handle, "kill")
        assert hasattr(handle, "is_alive")


class TestRunnerResultConsistency:
    """Expected-RED tests for RunnerResult consistency across all runners.

    All runners must return the same RunnerResult type.
    """

    @pytest.mark.anyio
    @pytest.mark.skip(reason="EXPECTED-RED: Extended runners not implemented")
    async def test_codex_result_is_runner_result(self, tmp_path) -> None:
        """CodexRunner returns RunnerResult from handle.wait()."""
        from vectl.driver.runners import create_runner
        from vectl.driver.config import RunnerConfig

        config = RunnerConfig(
            command=sys.executable,
            args=[
                "-c",
                "import json,sys; print(json.dumps({'thread_id':'sid','items':[{'text':'out'}]}))",
            ],
            prompt_mode="stdin_dash",
            output_parser="codex_jsonl",
            resume_command=["python", "-c", "mock"],
        )
        runner = create_runner("codex", config)

        handle = await runner.dispatch(
            prompt="test",
            agent="test-agent",
            workdir=str(tmp_path),
        )
        result = await handle.wait(timeout=5.0)

        # Must be RunnerResult type
        assert isinstance(result, RunnerResult)
        assert hasattr(result, "status")
        assert hasattr(result, "session_id")
        assert hasattr(result, "output")
        assert hasattr(result, "elapsed_seconds")
        assert hasattr(result, "exit_code")
        assert hasattr(result, "cost_usd")
        assert hasattr(result, "tokens")

        # Status must be RunnerStatus enum
        assert isinstance(result.status, RunnerStatus)

    @pytest.mark.anyio
    @pytest.mark.skip(reason="EXPECTED-RED: Extended runners not implemented")
    async def test_gemini_result_is_runner_result(self, tmp_path) -> None:
        """GeminiRunner returns RunnerResult from handle.wait()."""
        from vectl.driver.runners import create_runner
        from vectl.driver.config import RunnerConfig

        config = RunnerConfig(
            command=sys.executable,
            args=[
                "-c",
                "import json,sys; print(json.dumps({'session_id':'sid','result':'out','subtype':'success'}))",
            ],
            prompt_mode="stdin",
            output_parser="gemini_json",
            experimental=True,
        )
        runner = create_runner("gemini", config)

        handle = await runner.dispatch(
            prompt="test",
            agent="test-agent",
            workdir=str(tmp_path),
        )
        result = await handle.wait(timeout=5.0)

        # Must be RunnerResult type
        assert isinstance(result, RunnerResult)


class TestFactoryCompatibility:
    """Tests for create_runner factory compatibility with extended runners."""

    def test_create_runner_returns_same_protocol(self) -> None:
        """create_runner returns Runner protocol for all runner types."""
        from vectl.driver.runners import create_runner, ClaudeRunner, OpenCodeRunner
        from vectl.driver.config import RunnerConfig

        # Core runners - already implemented
        claude_config = RunnerConfig(command="claude", args=["-p"])
        claude_runner = create_runner("claude", claude_config)
        assert isinstance(claude_runner, ClaudeRunner)  # type: ignore

        opencode_config = RunnerConfig(command="opencode", args=["run"])
        opencode_runner = create_runner("opencode", opencode_config)
        assert isinstance(opencode_runner, OpenCodeRunner)  # type: ignore

        # Extended runners return stubs with NotImplementedError (documented)
        # Implementation will change this to return actual runners


class TestConfigFieldCompatibility:
    """Tests for RunnerConfig field compatibility across all runners.

    Extended runners may require additional config fields but must
    still work with existing fields.
    """

    def test_core_runner_fields_work_for_extended(self) -> None:
        """Core RunnerConfig fields work for extended runners."""
        from vectl.driver.config import RunnerConfig

        # Fields shared by all runners
        config = RunnerConfig(
            command="codex",
            args=["exec", "--json"],
            prompt_mode="stdin_dash",
            stall_timeout=600,
            resume_flag=None,  # Codex uses resume_command instead
            session_id_regex="^[0-9a-f]{8}-",
            output_parser="codex_jsonl",
            persist_session=True,
        )

        # All these fields should exist
        assert config.command == "codex"
        assert config.args == ["exec", "--json"]
        assert config.prompt_mode == "stdin_dash"
        assert config.stall_timeout == 600
        assert config.session_id_regex == "^[0-9a-f]{8}-"
        assert config.output_parser == "codex_jsonl"
        assert config.persist_session is True

    def test_extended_runner_optional_fields(self) -> None:
        """Extended runners have optional config fields for their semantics."""
        from vectl.driver.config import RunnerConfig

        # Codex: resume_command (optional, required by stub validation)
        codex_config = RunnerConfig(
            command="codex",
            args=["exec"],
            resume_command=["codex", "exec", "resume"],
        )
        assert codex_config.resume_command == ["codex", "exec", "resume"]

        # Gemini: experimental (required by stub validation)
        gemini_config = RunnerConfig(
            command="gemini",
            args=["-p"],
            experimental=True,
        )
        assert gemini_config.experimental is True


class TestFallbackCompatibility:
    """Expected-RED tests for fallback chain compatibility.

    GAP: Extended runners must work in fallback chain with core runners.
    """

    @pytest.mark.skip(reason="EXPECTED-RED: Driver loop integration not tested here")
    def test_codex_fallback_to_opencode(self) -> None:
        """Document: Codex can fallback to opencode on failure.

        This is handled by the driver loop (loop.py), not runners.
        Document the expected behavior for downstream owner.
        """
        # Fallback chain: codex -> opencode
        # Implementation in driver loop, not runner contract
        pass

    @pytest.mark.skip(reason="EXPECTED-RED: Driver loop integration not tested here")
    def test_gemini_fallback_to_opencode(self) -> None:
        """Document: Gemini can fallback to opencode on failure.

        GAP: Experimental runners have elevated failure risk.
        Driver loop should implement 2-failure fallback.
        """
        # Fallback chain: gemini -> opencode (after 2 failures)
        # session reuse may not work for experimental runners
        pass

    def test_experimental_flag_for_metrics(self) -> None:
        """experimental flag is available for metrics and logging."""
        from vectl.driver.config import RunnerConfig

        config = RunnerConfig(
            command="gemini",
            args=["-p"],
            experimental=True,
        )

        # This flag can be used by:
        # - Driver loop for elevated failure risk handling
        # - Metrics for tagging experimental runner usage
        # - Logging for warning about experimental status
        assert config.experimental is True
