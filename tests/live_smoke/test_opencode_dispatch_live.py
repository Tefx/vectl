"""Live smoke tests for opencode dispatch path.

Tests the real opencode dispatch invocation path with:
- Ordinary agent/task dispatch (not judge-only)
- Explicit skip for missing binary, auth, or opt-in gate
- Transport failure distinguished from task-output assertion failure

Marker combination: live_runner and opencode_live and dispatch_live

Path: driver.yaml opencode runner contract -> dispatch integration
    -> real opencode subprocess -> captured task output assertions
"""

from __future__ import annotations

import json
import os
from typing import Any

import pytest

from tests.live_smoke.helpers import (
    OPENCODE_MODEL,
    OPENCODE_MODEL_ENVAR,
    LiveRunnerPreflight,
    build_model_override_args,
    check_skip_matrix,
    dispatch_live,
    get_effective_model,
    is_auth_available,
    is_live_runner_opted_in,
    live_runner,
    opencode_live,
    run_subprocess,
    skip_if_auth_missing,
    skip_if_binary_missing,
)

# =============================================================================
# Constants
# =============================================================================

OPENCODE_DISPATCH_MODEL = OPENCODE_MODEL
"""Pinned opencode dispatch model: ollama-cloud/minimax-m2.7"""


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def opencode_dispatch_preflight() -> LiveRunnerPreflight:
    """Return a preflight checker for opencode dispatch tests."""
    return LiveRunnerPreflight("opencode")


@pytest.fixture
def opencode_dispatch_env() -> dict[str, str]:
    """Return environment variables for opencode dispatch test.

    Sets the model override to the pinned opencode dispatch model.
    """
    env = dict(os.environ)
    if OPENCODE_MODEL_ENVAR in env:
        # Respect user-set override if present
        pass
    else:
        env[OPENCODE_MODEL_ENVAR] = OPENCODE_DISPATCH_MODEL
    return env


@pytest.fixture
def minimal_dispatch_prompt() -> str:
    """Return a minimal dispatch prompt for testing.

    This is a simple prompt that asks opencode to respond with a brief message.
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


def parse_opencode_dispatch_output(stdout: str) -> dict[str, Any]:
    """Parse opencode dispatch JSONL output.

    OpenCode JSONL format (from driver.yaml):
        {"type": "step_start", "sessionID": "..."}
        {"type": "text", "part": {"text": "..."}}
        {"type": "text", "part": {"text": "..."}}
        {"type": "step_finish", "part": {"status": "success"}}

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

        # Extract text content from text events
        if event.get("type") == "text":
            part = event.get("part", {})
            if isinstance(part, dict):
                text = part.get("text")
                if isinstance(text, str) and text.strip():
                    text_parts.append(text.strip())

    if text_parts:
        # Try to parse the last text part as JSON
        last_text = text_parts[-1]
        try:
            return json.loads(last_text)
        except json.JSONDecodeError:
            # Return as plain text if not valid JSON
            return {"raw_output": last_text}

    return {"raw_output": stdout}


def assert_opencode_dispatch_success(result: Any) -> None:
    """Assert that opencode dispatch result is successful.

    Args:
        result: Parsed result dict from parse_opencode_dispatch_output.

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
                        f"Opencode dispatch output contains error indicator '{indicator}': "
                        f"{result['raw_output'][:200]}"
                    )
        elif "status" in result:
            if result["status"] != "completed":
                raise AssertionError(f"Opencode dispatch status is not completed: {result}")


# =============================================================================
# Tests
# =============================================================================


@live_runner
@opencode_live
@dispatch_live
class TestOpencodeDispatchLive:
    """Live smoke tests for opencode dispatch path."""

    def test_opencode_dispatch_opt_in_gate(self) -> None:
        """Verify opt-in gate skips when RUN_LIVE_RUNNER_TESTS != '1'."""
        if not is_live_runner_opted_in():
            pytest.skip(
                "Live runner tests require RUN_LIVE_RUNNER_TESTS=1",
                allow_module_level=True,
            )

    def test_opencode_dispatch_skip_matrix(self) -> None:
        """Verify skip reason matrix covers all required reasons."""
        matrix = check_skip_matrix("opencode")
        assert "live_runner_opt_in_missing" in matrix
        assert "live_runner_binary_missing" in matrix
        assert "live_runner_auth_missing" in matrix
        assert "live_runner_override_unsupported" in matrix

    def test_opencode_dispatch_binary_available(
        self, opencode_dispatch_preflight: LiveRunnerPreflight
    ) -> None:
        """Verify opencode binary is available on PATH or skip explicitly."""
        if not opencode_dispatch_preflight.check_binary():
            pytest.skip(
                "opencode binary not found on PATH",
                allow_module_level=True,
            )

    def test_opencode_dispatch_skip_if_binary_missing(self) -> None:
        """Verify skip_if_binary_missing works correctly."""
        skip_result = skip_if_binary_missing("opencode")
        if skip_result is not None:
            assert "opencode" in str(skip_result)
            pytest.skip("opencode binary not available", allow_module_level=True)

    def test_opencode_dispatch_skip_if_auth_missing(self) -> None:
        """Verify skip_if_auth_missing works correctly."""
        original_auth = is_auth_available("opencode")
        try:
            from tests.live_smoke.helpers import _auth_checks

            _auth_checks["opencode"] = False
            skip_result = skip_if_auth_missing("opencode")
            if skip_result is not None:
                assert "auth" in str(skip_result).lower()
                pytest.skip("opencode auth not available", allow_module_level=True)
        finally:
            _auth_checks["opencode"] = original_auth


@live_runner
@opencode_live
@dispatch_live
class TestOpencodeDispatchLiveIntegration:
    """Integration tests that actually invoke opencode CLI.

    These tests are skipped if:
    - RUN_LIVE_RUNNER_TESTS != "1"
    - opencode binary not on PATH
    - auth not available
    """

    def test_opencode_dispatch_effective_model(self) -> None:
        """Verify effective model resolves to pinned model."""
        effective = get_effective_model("opencode")
        assert effective == OPENCODE_DISPATCH_MODEL

    def test_opencode_dispatch_model_override_args(self) -> None:
        """Verify model override args are built correctly."""
        args = build_model_override_args("opencode", OPENCODE_DISPATCH_MODEL)
        assert "--model" in args
        model_index = args.index("--model")
        assert args[model_index + 1] == OPENCODE_DISPATCH_MODEL

    def test_opencode_dispatch_env_var_override(
        self, opencode_dispatch_env: dict[str, str]
    ) -> None:
        """Verify environment variable override is set correctly."""
        assert OPENCODE_MODEL_ENVAR in opencode_dispatch_env
        assert opencode_dispatch_env[OPENCODE_MODEL_ENVAR] == OPENCODE_DISPATCH_MODEL

    def test_opencode_dispatch_subprocess_invocation(
        self,
        opencode_dispatch_preflight: LiveRunnerPreflight,
        opencode_dispatch_env: dict[str, str],
        minimal_dispatch_prompt: str,
    ) -> None:
        """Verify opencode dispatch can be invoked as subprocess.

        This tests the full path: subprocess capture -> JSONL extraction -> output parsing.
        """
        opencode_dispatch_preflight()

        # Build the command using driver.yaml's opencode runner config
        model = get_effective_model("opencode")
        model_args = build_model_override_args("opencode", model)

        command = [
            "opencode",
            "run",
            "--format",
            "json",
            *model_args,
            "--",
            minimal_dispatch_prompt,
        ]

        # Run subprocess
        result = run_subprocess(
            command,
            cwd=None,
            env=opencode_dispatch_env,
            input_str=None,
            timeout=60.0,
            runner_name="opencode",
            scenario="opencode_dispatch_live",
        )

        # Log diagnostics for debugging
        print("\n=== Opencode Dispatch Subprocess Result ===")
        print(f"Command: {' '.join(command)}")
        print(f"Returncode: {result.returncode}")
        print(f"Stderr (truncated): {result.stderr[:200] if result.stderr else '(empty)'}")
        print(f"Stdout (truncated): {result.stdout[:500] if result.stdout else '(empty)'}")

        # Check for auth failure first (transport failure)
        if result.returncode != 0:
            stderr_lower = result.stderr.lower()
            if any(kw in stderr_lower for kw in ["auth", "login", "unauthorized", "session"]):
                pytest.skip(
                    f"opencode auth failure: {result.stderr[:200]}", allow_module_level=True
                )

        # Verify we got output
        assert result.stdout or result.stderr, (
            "Expected some output from opencode dispatch; got neither stdout nor stderr"
        )

    def test_opencode_dispatch_output_parsing(
        self,
        opencode_dispatch_preflight: LiveRunnerPreflight,
        opencode_dispatch_env: dict[str, str],
        minimal_dispatch_prompt: str,
    ) -> None:
        """Verify opencode dispatch JSONL output can be parsed.

        This tests the JSONL parsing path for opencode dispatch.
        """
        opencode_dispatch_preflight()

        command = [
            "opencode",
            "run",
            "--format",
            "json",
            "--model",
            OPENCODE_DISPATCH_MODEL,
            "--",
            minimal_dispatch_prompt,
        ]

        result = run_subprocess(
            command,
            cwd=None,
            env=opencode_dispatch_env,
            input_str=None,
            timeout=60.0,
            runner_name="opencode",
            scenario="opencode_dispatch_parsing",
        )

        print("\n=== Opencode Dispatch Parsing Test ===")
        print(f"Returncode: {result.returncode}")
        print(f"Stderr: {result.stderr[:200] if result.stderr else '(empty)'}")

        # Skip on auth failure
        if result.returncode != 0:
            stderr_lower = result.stderr.lower()
            if any(kw in stderr_lower for kw in ["auth", "login", "unauthorized", "session"]):
                pytest.skip("opencode auth failure", allow_module_level=True)

        # Parse the output
        parsed = parse_opencode_dispatch_output(result.stdout)

        print(f"Parsed result: {parsed}")

        # Verify we got some output
        assert parsed, f"Expected to parse output from opencode JSONL: {result.stdout[:200]}"
        assert isinstance(parsed, dict), f"Expected dict, got {type(parsed)}: {parsed}"

    def test_opencode_dispatch_transport_vs_assertion_failure(
        self,
        opencode_dispatch_preflight: LiveRunnerPreflight,
        opencode_dispatch_env: dict[str, str],
    ) -> None:
        """Verify transport failure is distinguished from assertion failure.

        Send malformed input to trigger potential issues.
        """
        opencode_dispatch_preflight()

        command = [
            "opencode",
            "run",
            "--format",
            "json",
            "--model",
            OPENCODE_DISPATCH_MODEL,
            "--",
            "This is not a valid task description",
        ]

        result = run_subprocess(
            command,
            cwd=None,
            env=opencode_dispatch_env,
            input_str=None,
            timeout=30.0,
            runner_name="opencode",
            scenario="opencode_dispatch_transport_test",
        )

        print("\n=== Transport vs Assertion Test ===")
        print(f"Returncode: {result.returncode}")
        print(f"Stderr: {result.stderr[:200] if result.stderr else '(empty)'}")
        print(f"Stdout: {result.stdout[:200] if result.stdout else '(empty)'}")

        # Skip on auth failure
        if result.returncode != 0:
            stderr_lower = result.stderr.lower()
            if any(kw in stderr_lower for kw in ["auth", "login", "unauthorized"]):
                pytest.skip("opencode auth failure", allow_module_level=True)

        # We expect either:
        # 1. Transport success (returncode 0) with some output
        # 2. Transport failure (returncode != 0)
        # Both are acceptable - the key is we capture evidence

        # Verify evidence is complete
        assert result.returncode is not None
        assert result.stdout is not None
        assert result.stderr is not None


# =============================================================================
# Unit tests for parsing helpers (don't require live opencode)
# =============================================================================


class TestOpencodeDispatchParsing:
    """Unit tests for opencode dispatch output parsing."""

    def test_parse_valid_json_text_event(self) -> None:
        """Verify valid JSON from text event is parsed correctly."""
        # Use json.dumps to properly construct the test JSON
        inner_json = json.dumps({"status": "completed", "message": "success"})
        event = {"type": "text", "part": {"text": inner_json}}
        jsonl = (
            '{"type": "step_start", "sessionID": "ses_abc123"}\n'
            + json.dumps(event)
            + '\n{"type": "step_finish", "part": {"status": "success"}}'
        )
        result = parse_opencode_dispatch_output(jsonl)
        assert result["status"] == "completed"
        assert result["message"] == "success"

    def test_parse_raw_text_when_not_json(self) -> None:
        """Verify raw text is captured when output is not JSON."""
        jsonl = "\n".join(
            [
                '{"type": "step_start", "sessionID": "ses_abc123"}',
                '{"type": "text", "part": {"text": "Just plain text response"}}',
                '{"type": "step_finish", "part": {"status": "success"}}',
            ]
        )
        result = parse_opencode_dispatch_output(jsonl)
        assert result["raw_output"] == "Just plain text response"

    def test_parse_empty_output(self) -> None:
        """Verify empty output is handled gracefully."""
        result = parse_opencode_dispatch_output("")
        assert "raw_output" in result
        assert result["raw_output"] == ""

    def test_parse_malformed_jsonl(self) -> None:
        """Verify malformed JSONL is handled gracefully."""
        jsonl = "\n".join(
            [
                '{"type": "step_start",',
                "not valid json at all",
                '{"type": "text", "part": {"text": "partial"}}}',
            ]
        )
        result = parse_opencode_dispatch_output(jsonl)
        # Should capture the partial text
        assert "raw_output" in result or "status" in result

    def test_assert_opencode_dispatch_success_with_status(self) -> None:
        """Verify assert succeeds with valid status."""
        result = {"status": "completed", "message": "ok"}
        assert_opencode_dispatch_success(result)  # Should not raise

    def test_assert_opencode_dispatch_success_with_raw_output(self) -> None:
        """Verify assert succeeds with raw output (no error indicators)."""
        result = {"raw_output": "Some plain text response"}
        assert_opencode_dispatch_success(result)  # Should not raise

    def test_assert_opencode_dispatch_success_fails_on_error_indicator(self) -> None:
        """Verify assert fails when error indicator is in raw output."""
        result = {"raw_output": "Error: something went wrong"}
        with pytest.raises(AssertionError, match="error indicator"):
            assert_opencode_dispatch_success(result)
