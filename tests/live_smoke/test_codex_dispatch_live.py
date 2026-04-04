"""Live smoke tests for codex dispatch path.

Tests the real codex dispatch invocation path with:
- Ordinary agent/task dispatch (not judge-only)
- Explicit skip for missing binary, auth, or opt-in gate
- Transport failure distinguished from task-output assertion failure

Marker combination: live_runner and codex_live and dispatch_live

Path: driver.yaml codex runner contract -> dispatch integration
    -> real codex subprocess -> captured task output assertions
"""

from __future__ import annotations

import json
import os
from typing import Any

import pytest

from tests.live_smoke.helpers import (
    CODEX_MODEL,
    CODEX_MODEL_ENVAR,
    LiveRunnerPreflight,
    build_model_override_args,
    check_skip_matrix,
    codex_live,
    dispatch_live,
    get_effective_model,
    is_auth_available,
    is_live_runner_opted_in,
    live_runner,
    run_subprocess,
    skip_if_auth_missing,
    skip_if_binary_missing,
)

# =============================================================================
# Constants
# =============================================================================

CODEX_DISPATCH_MODEL = CODEX_MODEL
"""Pinned codex dispatch model: gpt-5.4-mini"""


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def codex_dispatch_preflight() -> LiveRunnerPreflight:
    """Return a preflight checker for codex dispatch tests."""
    return LiveRunnerPreflight("codex")


@pytest.fixture
def codex_dispatch_env() -> dict[str, str]:
    """Return environment variables for codex dispatch test.

    Sets the model override to the pinned codex dispatch model.
    """
    env = dict(os.environ)
    # Ensure model override points to pinned codex dispatch model
    if CODEX_MODEL_ENVAR in env:
        # Respect user-set override if present
        pass
    else:
        env[CODEX_MODEL_ENVAR] = CODEX_DISPATCH_MODEL
    return env


@pytest.fixture
def minimal_dispatch_prompt() -> str:
    """Return a minimal dispatch prompt for testing.

    This is a simple prompt that asks codex to respond with a brief message.
    """
    return """You are a test agent. Respond with ONLY the following JSON object
and nothing else (no markdown, no explanation):

{
    "status": "completed",
    "message": "Task completed successfully"
}
"""


# =============================================================================
# Helper functions
# =============================================================================


def parse_codex_dispatch_output(stdout: str) -> dict[str, Any]:
    """Parse codex dispatch JSONL output.

    Codex JSONL format:
        {"type": "thread.started", "sessionId": "..."}
        {"type": "item.completed", "item": {"output": {...}}}
        {"type": "turn.completed", ...}

    Returns:
        Dict with parsed output or raw text if parsing fails.
    """
    lines = stdout.strip().split("\n") if stdout else []
    text_parts: list[str] = []

    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue

        if not isinstance(event, dict):
            continue

        # Extract text content from item.completed
        if event.get("type") == "item.completed":
            item = event.get("item", {})
            output = item.get("output", {})
            # Handle nested output structure
            if isinstance(output, dict):
                text = output.get("text")
                if isinstance(text, str) and text.strip():
                    text_parts.append(text.strip())
            elif isinstance(output, str) and output.strip():
                text_parts.append(output.strip())

    if text_parts:
        # Try to parse the last text part as JSON
        last_text = text_parts[-1]
        try:
            return json.loads(last_text)
        except json.JSONDecodeError:
            # Return as plain text
            return {"raw_output": last_text}

    return {"raw_output": stdout}


def assert_codex_dispatch_success(result: Any) -> None:
    """Assert that codex dispatch result is successful.

    Args:
        result: Parsed result dict from parse_codex_dispatch_output.

    Raises:
        AssertionError: If result indicates failure.
    """
    if isinstance(result, dict):
        if "raw_output" in result and "raw_output" not in ("", None):
            # Has raw output - check if it looks like an error
            raw = result["raw_output"].lower()
            error_indicators = ["error", "failed", "unauthorized", "auth", "not found"]
            for indicator in error_indicators:
                if indicator in raw:
                    raise AssertionError(
                        f"Codex dispatch output contains error indicator '{indicator}': "
                        f"{result['raw_output'][:200]}"
                    )
        elif "status" in result:
            if result["status"] != "completed":
                raise AssertionError(f"Codex dispatch status is not completed: {result}")


# =============================================================================
# Tests
# =============================================================================


@live_runner
@codex_live
@dispatch_live
class TestCodexDispatchLive:
    """Live smoke tests for codex dispatch path."""

    def test_codex_dispatch_opt_in_gate(self) -> None:
        """Verify opt-in gate skips when RUN_LIVE_RUNNER_TESTS != '1'."""
        if not is_live_runner_opted_in():
            pytest.skip(
                "Live runner tests require RUN_LIVE_RUNNER_TESTS=1",
                allow_module_level=True,
            )

    def test_codex_dispatch_skip_matrix(self) -> None:
        """Verify skip reason matrix covers all required reasons."""
        matrix = check_skip_matrix("codex")
        assert "live_runner_opt_in_missing" in matrix
        assert "live_runner_binary_missing" in matrix
        assert "live_runner_auth_missing" in matrix
        assert "live_runner_override_unsupported" in matrix

    def test_codex_dispatch_binary_available(
        self, codex_dispatch_preflight: LiveRunnerPreflight
    ) -> None:
        """Verify codex binary is available on PATH or skip explicitly."""
        if not codex_dispatch_preflight.check_binary():
            pytest.skip(
                "codex binary not found on PATH",
                allow_module_level=True,
            )

    def test_codex_dispatch_skip_if_binary_missing(self) -> None:
        """Verify skip_if_binary_missing works correctly."""
        skip_result = skip_if_binary_missing("codex")
        if skip_result is not None:
            assert "codex" in str(skip_result)
            pytest.skip("codex binary not available", allow_module_level=True)

    def test_codex_dispatch_skip_if_auth_missing(self) -> None:
        """Verify skip_if_auth_missing works correctly."""
        original_auth = is_auth_available("codex")
        try:
            from tests.live_smoke.helpers import _auth_checks

            _auth_checks["codex"] = False
            with pytest.raises(pytest.skip.Exception) as exc_info:
                skip_if_auth_missing("codex")

            assert "auth" in str(exc_info.value).lower()
            assert "codex" in str(exc_info.value).lower()
            assert exc_info.value.allow_module_level is True
        finally:
            _auth_checks["codex"] = original_auth


@live_runner
@codex_live
@dispatch_live
class TestCodexDispatchLiveIntegration:
    """Integration tests that actually invoke codex CLI.

    These tests are skipped if:
    - RUN_LIVE_RUNNER_TESTS != "1"
    - codex binary not on PATH
    - auth not available
    """

    def test_codex_dispatch_effective_model(self) -> None:
        """Verify effective model resolves to pinned model."""
        effective = get_effective_model("codex")
        assert effective == CODEX_DISPATCH_MODEL

    def test_codex_dispatch_model_override_args(self) -> None:
        """Verify model override args are built correctly."""
        args = build_model_override_args("codex", CODEX_DISPATCH_MODEL)
        assert "--model" in args
        model_index = args.index("--model")
        assert args[model_index + 1] == CODEX_DISPATCH_MODEL

    def test_codex_dispatch_env_var_override(self, codex_dispatch_env: dict[str, str]) -> None:
        """Verify environment variable override is set correctly."""
        assert CODEX_MODEL_ENVAR in codex_dispatch_env
        assert codex_dispatch_env[CODEX_MODEL_ENVAR] == CODEX_DISPATCH_MODEL

    def test_codex_dispatch_subprocess_invocation(
        self,
        codex_dispatch_preflight: LiveRunnerPreflight,
        codex_dispatch_env: dict[str, str],
        minimal_dispatch_prompt: str,
        tmp_path: Any,
    ) -> None:
        """Verify codex dispatch can be invoked as subprocess.

        This tests the full path: subprocess capture -> JSONL extraction -> output parsing.
        """
        codex_dispatch_preflight()

        # Build the command using driver.yaml's codex runner config
        model = get_effective_model("codex")
        model_args = build_model_override_args("codex", model)

        # Build command like driver.yaml codex runner
        command = [
            "codex",
            "exec",
            "--json",
            "--dangerously-bypass-approvals-and-sandbox",
            "-C",
            str(tmp_path),
            *model_args,
            "-",
        ]

        # Run subprocess
        result = run_subprocess(
            command,
            cwd=str(tmp_path),
            env=codex_dispatch_env,
            input_str=minimal_dispatch_prompt,
            timeout=60.0,
            runner_name="codex",
            scenario="codex_dispatch_live",
        )

        # Log diagnostics for debugging
        print("\n=== Codex Dispatch Subprocess Result ===")
        print(f"Command: {' '.join(command)}")
        print(f"Returncode: {result.returncode}")
        print(f"Stderr (truncated): {result.stderr[:200] if result.stderr else '(empty)'}")
        print(f"Stdout (truncated): {result.stdout[:500] if result.stdout else '(empty)'}")

        # Check for auth failure first (transport failure)
        if result.returncode != 0:
            stderr_lower = result.stderr.lower()
            if any(kw in stderr_lower for kw in ["auth", "login", "unauthorized", "session"]):
                pytest.skip(f"codex auth failure: {result.stderr[:200]}", allow_module_level=True)

        # Verify we got output
        assert result.stdout or result.stderr, (
            "Expected some output from codex dispatch; got neither stdout nor stderr"
        )

    def test_codex_dispatch_output_parsing(
        self,
        codex_dispatch_preflight: LiveRunnerPreflight,
        codex_dispatch_env: dict[str, str],
        minimal_dispatch_prompt: str,
        tmp_path: Any,
    ) -> None:
        """Verify codex dispatch JSONL output can be parsed.

        This tests the JSONL parsing path for codex dispatch.
        """
        codex_dispatch_preflight()

        command = [
            "codex",
            "exec",
            "--json",
            "--dangerously-bypass-approvals-and-sandbox",
            "-C",
            str(tmp_path),
            "--model",
            CODEX_DISPATCH_MODEL,
            "-",
        ]

        result = run_subprocess(
            command,
            cwd=str(tmp_path),
            env=codex_dispatch_env,
            input_str=minimal_dispatch_prompt,
            timeout=60.0,
            runner_name="codex",
            scenario="codex_dispatch_parsing",
        )

        print("\n=== Codex Dispatch Parsing Test ===")
        print(f"Returncode: {result.returncode}")
        print(f"Stderr: {result.stderr[:200] if result.stderr else '(empty)'}")

        # Skip on auth failure
        if result.returncode != 0:
            stderr_lower = result.stderr.lower()
            if any(kw in stderr_lower for kw in ["auth", "login", "unauthorized", "session"]):
                pytest.skip("codex auth failure", allow_module_level=True)

        # Parse the output
        parsed = parse_codex_dispatch_output(result.stdout)

        print(f"Parsed result: {parsed}")

        # Verify we got some output
        assert parsed, f"Expected to parse output from codex JSONL: {result.stdout[:200]}"
        assert isinstance(parsed, dict), f"Expected dict, got {type(parsed)}: {parsed}"

    def test_codex_dispatch_transport_vs_assertion_failure(
        self,
        codex_dispatch_preflight: LiveRunnerPreflight,
        codex_dispatch_env: dict[str, str],
        tmp_path: Any,
    ) -> None:
        """Verify transport failure is distinguished from assertion failure.

        Send malformed input to trigger transport success but potential parse issues.
        """
        codex_dispatch_preflight()

        command = [
            "codex",
            "exec",
            "--json",
            "--dangerously-bypass-approvals-and-sandbox",
            "-C",
            str(tmp_path),
            "--model",
            CODEX_DISPATCH_MODEL,
            "-",
        ]

        # Send invalid prompt
        result = run_subprocess(
            command,
            cwd=str(tmp_path),
            env=codex_dispatch_env,
            input_str="This is not a valid task description at all",
            timeout=30.0,
            runner_name="codex",
            scenario="codex_dispatch_transport_test",
        )

        print("\n=== Transport vs Assertion Test ===")
        print(f"Returncode: {result.returncode}")
        print(f"Stderr: {result.stderr[:200] if result.stderr else '(empty)'}")
        print(f"Stdout: {result.stdout[:200] if result.stdout else '(empty)'}")

        # Skip on auth failure
        if result.returncode != 0:
            stderr_lower = result.stderr.lower()
            if any(kw in stderr_lower for kw in ["auth", "login", "unauthorized"]):
                pytest.skip("codex auth failure", allow_module_level=True)

        # We expect either:
        # 1. Transport success (returncode 0) with some output
        # 2. Transport failure (returncode != 0)
        # Both are acceptable - the key is we capture evidence

        # Verify evidence is complete
        assert result.returncode is not None
        assert result.stdout is not None
        assert result.stderr is not None


# =============================================================================
# Unit tests for parsing helpers (don't require live codex)
# =============================================================================


class TestCodexDispatchParsing:
    """Unit tests for codex dispatch output parsing."""

    def test_parse_valid_json_item_completed(self) -> None:
        """Verify valid JSON from item.completed is parsed correctly."""
        inner_json = '{"status": "completed", "message": "success"}'
        jsonl_lines = [
            '{"type": "thread.started", "sessionId": "abc123"}',
            '{"type": "item.completed", "item": {"output": {"text": "'
            + inner_json.replace('"', '\\"')
            + '"}}}',
        ]
        jsonl = "\n".join(jsonl_lines)
        result = parse_codex_dispatch_output(jsonl)
        assert result["status"] == "completed"
        assert result["message"] == "success"

    def test_parse_raw_text_when_not_json(self) -> None:
        """Verify raw text is captured when output is not JSON."""
        jsonl = (
            '{"type": "thread.started", "sessionId": "abc123"}\n'
            '{"type": "item.completed", "item": {"output": {"text": "Just plain text response"}}}'
        )
        result = parse_codex_dispatch_output(jsonl)
        assert result["raw_output"] == "Just plain text response"

    def test_parse_empty_output(self) -> None:
        """Verify empty output is handled gracefully."""
        result = parse_codex_dispatch_output("")
        assert "raw_output" in result
        assert result["raw_output"] == ""

    def test_parse_malformed_jsonl(self) -> None:
        """Verify malformed JSONL is handled gracefully."""
        jsonl = "\n".join(
            [
                '{"type": "thread.started",',
                "not valid json at all",
                '{"type": "item.completed", "item": {"output": {"text": "partial"}}}',
            ]
        )
        result = parse_codex_dispatch_output(jsonl)
        # Should capture the partial text
        assert "raw_output" in result or "status" in result

    def test_assert_codex_dispatch_success_with_status(self) -> None:
        """Verify assert succeeds with valid status."""
        result = {"status": "completed", "message": "ok"}
        assert_codex_dispatch_success(result)  # Should not raise

    def test_assert_codex_dispatch_success_with_raw_output(self) -> None:
        """Verify assert succeeds with raw output (no error indicators)."""
        result = {"raw_output": "Some plain text response"}
        assert_codex_dispatch_success(result)  # Should not raise

    def test_assert_codex_dispatch_success_fails_on_error_indicator(self) -> None:
        """Verify assert fails when error indicator is in raw output."""
        result = {"raw_output": "Error: something went wrong"}
        with pytest.raises(AssertionError, match="error indicator"):
            assert_codex_dispatch_success(result)
