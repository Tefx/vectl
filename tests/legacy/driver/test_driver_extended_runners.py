"""Extended runner implementation tests (Codex + Gemini).

Covers:
- Codex/Gemini parser behavior
- Resume semantics (Codex resume_command, Gemini resume_flag)
- Shared Runner protocol compatibility
- Failure-path coverage (runner not found, malformed output, experimental gate)
"""

from __future__ import annotations

import inspect
import sys

import pytest

from vectl.driver.config import RunnerConfig
from vectl.driver.errors import RunnerError, RunnerNotFoundError
from vectl.driver.parsers import CodexOutputParser, GeminiOutputParser
from vectl.driver.runners import (
    CORE_RUNNERS,
    EXTENDED_RUNNERS,
    CodexRunner,
    GeminiRunner,
    RunnerStatus,
    create_runner,
)


class TestExtendedScope:
    def test_scope_sets(self) -> None:
        assert CORE_RUNNERS == frozenset({"claude", "opencode"})
        assert EXTENDED_RUNNERS == frozenset({"codex", "gemini"})
        assert CORE_RUNNERS.isdisjoint(EXTENDED_RUNNERS)


class TestCodexParser:
    def test_parse_success_stream(self) -> None:
        parser = CodexOutputParser()
        stdout = """{"type":"thread.started","thread_id":"th-1"}
{"type":"item.completed","item":{"text":"A"}}
{"type":"item.completed","item":{"text":"B"}}
{"type":"turn.completed","usage":{"input_tokens":10,"output_tokens":3}}"""
        result = parser.parse(stdout, elapsed_seconds=1.0)
        assert result.status == RunnerStatus.SUCCESS
        assert result.session_id == "th-1"
        assert result.output == "A\nB"
        assert result.tokens == {"input_tokens": 10, "output_tokens": 3}

    def test_parse_fail_when_no_completed_items(self) -> None:
        parser = CodexOutputParser()
        stdout = """{"type":"thread.started","thread_id":"th-1"}
{"type":"turn.completed","usage":{"input_tokens":1}}"""
        result = parser.parse(stdout, elapsed_seconds=1.0)
        assert result.status == RunnerStatus.FAIL
        assert result.session_id == "th-1"
        assert result.output == ""

    def test_parse_tolerates_malformed_line_when_valid_events_exist(self) -> None:
        parser = CodexOutputParser()
        stdout = """{"type":"thread.started","thread_id":"th-1"}
not-json
{"type":"item.completed","item":{"text":"ok"}}"""
        result = parser.parse(stdout, elapsed_seconds=0.5)
        assert result.status == RunnerStatus.SUCCESS
        assert result.session_id == "th-1"
        assert result.output == "ok"

    def test_parse_transport_error_when_only_malformed_lines(self) -> None:
        parser = CodexOutputParser()
        stdout = """not-json
still-not-json"""
        result = parser.parse(stdout, elapsed_seconds=0.5)
        assert result.status == RunnerStatus.TRANSPORT_ERROR
        assert result.session_id is None


class TestGeminiParser:
    def test_parse_success(self) -> None:
        parser = GeminiOutputParser()
        stdout = (
            """{"session_id":"2","result":"ok","subtype":"success","usage":{"input_tokens":7}}"""
        )
        result = parser.parse(stdout, elapsed_seconds=0.7)
        assert result.status == RunnerStatus.SUCCESS
        assert result.session_id == "2"
        assert result.output == "ok"
        assert result.tokens == {"input_tokens": 7}

    def test_parse_missing_subtype_defaults_fail(self) -> None:
        parser = GeminiOutputParser()
        stdout = """{"session_id":"2","result":"x"}"""
        result = parser.parse(stdout, elapsed_seconds=0.7)
        assert result.status == RunnerStatus.FAIL

    def test_parse_transport_error_on_malformed_json(self) -> None:
        parser = GeminiOutputParser()
        result = parser.parse('{"session_id":"x",', elapsed_seconds=0.2)
        assert result.status == RunnerStatus.TRANSPORT_ERROR
        assert result.session_id is None


class TestCreateRunnerExtended:
    def test_codex_create_requires_resume_command(self) -> None:
        config = RunnerConfig(command="codex", args=["exec", "--json"], output_parser="codex_jsonl")
        with pytest.raises(RunnerError):
            create_runner("codex", config)

    def test_gemini_create_requires_experimental_true(self) -> None:
        config = RunnerConfig(command="gemini", args=["-p"], output_parser="gemini_json")
        with pytest.raises(RunnerError):
            create_runner("gemini", config)

    def test_create_extended_runners(self) -> None:
        codex = create_runner(
            "codex",
            RunnerConfig(
                command="codex",
                args=["exec", "--json", "-C", "{workdir}"],
                prompt_mode="stdin_dash",
                output_parser="codex_jsonl",
                resume_command=["codex", "exec", "resume", "--json"],
            ),
        )
        gemini = create_runner(
            "gemini",
            RunnerConfig(
                command="gemini",
                args=["-p", "--output-format", "json", "--yolo"],
                output_parser="gemini_json",
                resume_flag="--resume",
                experimental=True,
            ),
        )
        assert isinstance(codex, CodexRunner)
        assert isinstance(gemini, GeminiRunner)


class TestProtocolCompatibility:
    def test_dispatch_signature_matches_protocol_shape(self) -> None:
        codex_sig = inspect.signature(CodexRunner.dispatch)
        gemini_sig = inspect.signature(GeminiRunner.dispatch)
        for sig in (codex_sig, gemini_sig):
            params = list(sig.parameters.keys())
            assert params[:4] == ["self", "prompt", "agent", "workdir"]
            assert "session_id" in params


class TestCodexRunnerIntegration:
    @pytest.mark.anyio
    async def test_dispatch_and_resume_command_path(self, tmp_path) -> None:
        # Initial command echoes argv to output and emits codex-like JSONL events.
        init_code = (
            "import json,sys;"
            "_ = sys.stdin.read();"
            "print(json.dumps({'type':'thread.started','thread_id':'th-1'}));"
            "print(json.dumps({'type':'item.completed','item':{'text':' '.join(sys.argv[1:])}}));"
            "print(json.dumps({'type':'turn.completed','usage':{'input_tokens':1}}))"
        )
        resume_code = (
            "import json,sys;"
            "_ = sys.stdin.read();"
            "print(json.dumps({'type':'thread.started','thread_id':'th-r'}));"
            "print(json.dumps({'type':'item.completed','item':{'text':' '.join(sys.argv[1:])}}));"
            "print(json.dumps({'type':'turn.completed','usage':{'input_tokens':2}}))"
        )

        runner = CodexRunner(
            "codex",
            RunnerConfig(
                command=sys.executable,
                args=["-c", init_code, "exec", "--json", "-C", "{workdir}"],
                prompt_mode="stdin_dash",
                output_parser="codex_jsonl",
                resume_command=[sys.executable, "-c", resume_code, "exec", "resume", "--json"],
            ),
        )

        first = await runner.dispatch("prompt", "agent", str(tmp_path))
        first_result = await first.wait(timeout=3.0)
        assert first_result.status == RunnerStatus.SUCCESS
        assert first_result.session_id == "th-1"
        assert "exec --json -C" in first_result.output

        resumed = await runner.dispatch("prompt", "agent", str(tmp_path), session_id="abc-123")
        resumed_result = await resumed.wait(timeout=3.0)
        assert resumed_result.status == RunnerStatus.SUCCESS
        assert resumed_result.session_id == "th-r"
        # codex resume semantics: session id inserted after `resume`
        assert "resume abc-123 --json" in resumed_result.output

    @pytest.mark.anyio
    async def test_runner_not_found_path(self, tmp_path) -> None:
        runner = CodexRunner(
            "codex",
            RunnerConfig(
                command="definitely-not-a-real-codex-command",
                args=["exec", "--json"],
                prompt_mode="stdin_dash",
                output_parser="codex_jsonl",
                resume_command=["codex", "exec", "resume", "--json"],
            ),
        )
        with pytest.raises(RunnerNotFoundError):
            await runner.dispatch("p", "a", str(tmp_path))


class TestGeminiRunnerIntegration:
    @pytest.mark.anyio
    async def test_dispatch_and_resume_flag(self, tmp_path) -> None:
        code = (
            "import json,sys;"
            "_ = sys.stdin.read();"
            "print(json.dumps({'session_id':'0','result':' '.join(sys.argv[1:]),'subtype':'success'}))"
        )
        runner = GeminiRunner(
            "gemini",
            RunnerConfig(
                command=sys.executable,
                args=["-c", code, "-p", "--output-format", "json", "--yolo"],
                output_parser="gemini_json",
                resume_flag="--resume",
                experimental=True,
            ),
        )

        first = await runner.dispatch("prompt", "agent", str(tmp_path))
        first_result = await first.wait(timeout=3.0)
        assert first_result.status == RunnerStatus.SUCCESS
        assert "--yolo" in first_result.output

        resumed = await runner.dispatch("prompt", "agent", str(tmp_path), session_id="3")
        resumed_result = await resumed.wait(timeout=3.0)
        assert resumed_result.status == RunnerStatus.SUCCESS
        assert "--resume 3" in resumed_result.output

    def test_experimental_behavior_documented_and_enforced(self) -> None:
        # Failure path coverage: non-experimental gemini config is rejected.
        with pytest.raises(RunnerError) as exc:
            GeminiRunner(
                "gemini",
                RunnerConfig(
                    command="gemini",
                    args=["-p", "--output-format", "json"],
                    output_parser="gemini_json",
                    experimental=False,
                ),
            )
        assert "experimental=True" in str(exc.value)
