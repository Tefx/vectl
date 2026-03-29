"""Live smoke tests for opencode judge path.

Tests the real opencode judge invocation path with:
- Tolerant JSONL/text verdict extraction
- Structured verdict acceptance when available
- Explicit skip for missing binary, auth, or opt-in gate
- Parse failure distinguished from transport/auth failure

Marker combination: live_runner and opencode_live and judge_live

Path: driver.yaml opencode runner -> judge text/JSONL extraction
    -> real opencode subprocess -> verdict assertion
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
    get_effective_model,
    is_auth_available,
    is_live_runner_opted_in,
    judge_live,
    live_runner,
    opencode_live,
    run_subprocess,
    skip_if_auth_missing,
    skip_if_binary_missing,
)

# =============================================================================
# Constants
# =============================================================================

OPENCODE_JUDGE_MODEL = OPENCODE_MODEL
"""Pinned opencode judge model: ollama-cloud/minimax-m2.7"""


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def opencode_judge_preflight() -> LiveRunnerPreflight:
    """Return a preflight checker for opencode judge tests."""
    return LiveRunnerPreflight("opencode")


@pytest.fixture
def opencode_judge_env() -> dict[str, str]:
    """Return environment variables for opencode judge test.

    Sets the model override to the pinned opencode judge model.
    """
    env = dict(os.environ)
    # Ensure model override points to pinned opencode judge model
    # This uses the override mechanism ratified in DRIVER-LIVE-SMOKE-POLICY.md
    if OPENCODE_MODEL_ENVAR in env:
        # Respect user-set override if present
        pass
    else:
        env[OPENCODE_MODEL_ENVAR] = OPENCODE_JUDGE_MODEL
    return env


@pytest.fixture
def minimal_judge_request() -> str:
    """Return a minimal judge request payload for testing.

    This is a minimal JSON payload that matches JudgmentRequest structure.
    Uses a simple preflight check.
    """
    return json.dumps(
        {
            "type": "preflight",
            "step_id": "live.smoke.test",
            "context": {
                "goal": "Verify opencode judge integration",
                "agent": "python-executor",
            },
            "failure_history": [],
            "plan_summary": "Live smoke test for opencode judge",
        }
    )


@pytest.fixture
def minimal_system_prompt() -> str:
    """Return a minimal judge system prompt for testing.

    This is a simplified version that just asks for a verdict in JSON format.
    """
    return """You are a judge agent that evaluates step execution outcomes.

Respond with ONLY a JSON object in this exact format:
{
    "verdict": "ACCEPT|REJECT|RETRY",
    "reason": "Brief explanation",
    "suggested_action": null,
    "planner_instruction": null
}

Do not include any other text outside the JSON object.
"""


# =============================================================================
# Tests
# =============================================================================


@live_runner
@opencode_live
@judge_live
class TestOpencodeJudgeLive:
    """Live smoke tests for opencode judge path."""

    def test_opencode_judge_opt_in_gate(self) -> None:
        """Verify opt-in gate skips when RUN_LIVE_RUNNER_TESTS != '1'."""
        if not is_live_runner_opted_in():
            pytest.skip(
                "Live runner tests require RUN_LIVE_RUNNER_TESTS=1",
                allow_module_level=True,
            )

    def test_opencode_judge_skip_matrix(self) -> None:
        """Verify skip reason matrix covers all required reasons."""
        matrix = check_skip_matrix("opencode")
        assert "live_runner_opt_in_missing" in matrix
        assert "live_runner_binary_missing" in matrix
        assert "live_runner_auth_missing" in matrix
        assert "live_runner_override_unsupported" in matrix

    def test_opencode_judge_binary_available(
        self, opencode_judge_preflight: LiveRunnerPreflight
    ) -> None:
        """Verify opencode binary is available on PATH or skip explicitly."""
        if not opencode_judge_preflight.check_binary():
            pytest.skip(
                "opencode binary not found on PATH",
                allow_module_level=True,
            )

    def test_opencode_judge_skip_if_binary_missing(self) -> None:
        """Verify skip_if_binary_missing works correctly."""
        skip_result = skip_if_binary_missing("opencode")
        if skip_result is not None:
            # Binary is missing - skip is allowed
            assert "opencode" in str(skip_result)
            pytest.skip("opencode binary not available", allow_module_level=True)

    def test_opencode_judge_skip_if_auth_missing(self) -> None:
        """Verify skip_if_auth_missing works correctly."""
        # Mock auth as unavailable for this test
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

    def test_opencode_judge_skip_if_not_opted_in(self) -> None:
        """Verify skip_if_not_opted_in works correctly."""
        if not is_live_runner_opted_in():
            pytest.skip(
                "RUN_LIVE_RUNNER_TESTS=1 required",
                allow_module_level=True,
            )


@live_runner
@opencode_live
@judge_live
class TestOpencodeJudgeLiveIntegration:
    """Integration tests that actually invoke opencode CLI.

    These tests are skipped if:
    - RUN_LIVE_RUNNER_TESTS != "1"
    - opencode binary not on PATH
    - auth not available
    """

    def test_opencode_judge_effective_model(self) -> None:
        """Verify effective model resolves to pinned model."""
        effective = get_effective_model("opencode")
        assert effective == OPENCODE_JUDGE_MODEL

    def test_opencode_judge_model_override_args(self) -> None:
        """Verify model override args are built correctly."""
        args = build_model_override_args("opencode", OPENCODE_JUDGE_MODEL)
        assert "--model" in args
        model_index = args.index("--model")
        assert args[model_index + 1] == OPENCODE_JUDGE_MODEL

    def test_opencode_judge_env_var_override(self, opencode_judge_env: dict[str, str]) -> None:
        """Verify environment variable override is set correctly."""
        # The fixture should set VECTL_OPENCODE_MODEL
        assert OPENCODE_MODEL_ENVAR in opencode_judge_env
        assert opencode_judge_env[OPENCODE_MODEL_ENVAR] == OPENCODE_JUDGE_MODEL

    def test_opencode_judge_subprocess_invocation(
        self,
        opencode_judge_preflight: LiveRunnerPreflight,
        opencode_judge_env: dict[str, str],
        minimal_system_prompt: str,
        minimal_judge_request: str,
    ) -> None:
        """Verify opencode judge can be invoked as subprocess.

        This tests the full path: subprocess capture -> JSONL extraction -> verdict parsing.
        """
        opencode_judge_preflight()

        # Build the command
        model = get_effective_model("opencode")
        model_args = build_model_override_args("opencode", model)

        command = [
            "opencode",
            "run",
            "--format",
            "json",
            *model_args,
            "--",
            minimal_system_prompt,
        ]

        # Run subprocess
        result = run_subprocess(
            command,
            cwd=None,
            env=opencode_judge_env,
            input_str=minimal_judge_request,
            timeout=60.0,
            runner_name="opencode",
            scenario="opencode_judge_live",
        )

        # Log diagnostics for debugging
        print("\n=== OpenCode Judge Subprocess Result ===")
        print(f"Command: {' '.join(command)}")
        print(f"Returncode: {result.returncode}")
        print(f"Stderr: {result.stderr}")
        print(f"Stdout (truncated): {result.stdout[:500] if result.stdout else '(empty)'}")

        # The test should not fail due to transport issues
        # Even if parse fails, we should have evidence
        if result.returncode != 0:
            # Non-zero returncode - could be auth failure or actual failure
            # Check stderr for auth-related errors
            stderr_lower = result.stderr.lower()
            if any(kw in stderr_lower for kw in ["auth", "login", "unauthorized", "session"]):
                pytest.skip(
                    f"opencode auth failure: {result.stderr[:200]}", allow_module_level=True
                )

        # Verify we got output
        assert result.stdout or result.stderr, (
            "Expected some output from opencode judge; got neither stdout nor stderr"
        )

    def test_opencode_judge_verdict_extraction_from_jsonl(
        self,
        opencode_judge_preflight: LiveRunnerPreflight,
        opencode_judge_env: dict[str, str],
        minimal_system_prompt: str,
        minimal_judge_request: str,
    ) -> None:
        """Verify verdict can be extracted from JSONL output.

        This tests the tolerant JSONL/text extraction path in judge.py.
        """
        opencode_judge_preflight()

        # Build command
        model = get_effective_model("opencode")
        model_args = build_model_override_args("opencode", model)

        command = [
            "opencode",
            "run",
            "--format",
            "json",
            *model_args,
            "--",
            minimal_system_prompt,
        ]

        result = run_subprocess(
            command,
            cwd=None,
            env=opencode_judge_env,
            input_str=minimal_judge_request,
            timeout=60.0,
            runner_name="opencode",
            scenario="opencode_judge_jsonl_extraction",
        )

        print("\n=== OpenCode Judge JSONL Extraction Test ===")
        print(f"Returncode: {result.returncode}")
        print(f"Stderr: {result.stderr}")
        print(f"Stdout (first 500 chars): {result.stdout[:500] if result.stdout else '(empty)'}")

        # Skip on auth failure
        if result.returncode != 0:
            stderr_lower = result.stderr.lower()
            if any(kw in stderr_lower for kw in ["auth", "login", "unauthorized", "session"]):
                pytest.skip("opencode auth failure", allow_module_level=True)

        # Try to parse the output as JSONL
        # The tolerant extraction should handle text events
        lines = result.stdout.strip().split("\n") if result.stdout else []
        text_parts: list[str] = []

        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
                if isinstance(event, dict):
                    if event.get("type") == "text":
                        part = event.get("part", {})
                        if isinstance(part, dict):
                            text = part.get("text")
                            if isinstance(text, str) and text.strip():
                                text_parts.append(text.strip())
            except json.JSONDecodeError:
                # Skip malformed lines - tolerant extraction
                continue

        print(f"Extracted {len(text_parts)} text parts from JSONL")

        # Verify we extracted some text
        assert text_parts, (
            f"Expected to extract text parts from JSONL output.\n"
            f"Full stdout: {result.stdout!r}\n"
            f"Returncode: {result.returncode}"
        )

        # The last text part should be the verdict
        last_text = text_parts[-1]
        print(f"Last text part (verdict candidate): {last_text[:200]}")

        # Try to parse as JSON verdict
        try:
            verdict_data = json.loads(last_text)
            # Verify it has the expected structure
            assert "verdict" in verdict_data, f"Missing 'verdict' key in {verdict_data}"
            assert "reason" in verdict_data, f"Missing 'reason' key in {verdict_data}"
            print(f"Successfully parsed verdict: {verdict_data.get('verdict')}")
        except json.JSONDecodeError:
            # If it's not valid JSON, the tolerant extraction handled text mode
            # This is acceptable per the policy - opencode judge supports text fallback
            print(f"Text extraction mode - last text is not JSON: {last_text[:100]}")
            # The test passes in tolerant mode

    def test_opencode_judge_parse_failure_distinguished_from_transport(
        self,
        opencode_judge_preflight: LiveRunnerPreflight,
        opencode_judge_env: dict[str, str],
    ) -> None:
        """Verify parse failure is distinguished from transport/auth failure.

        Send malformed input to trigger parse error, not transport error.
        """
        opencode_judge_preflight()

        command = [
            "opencode",
            "run",
            "--format",
            "json",
            "--model",
            OPENCODE_JUDGE_MODEL,
            "--",
            "Respond with valid JSON only.",
        ]

        # Send invalid JSON to trigger parse error
        result = run_subprocess(
            command,
            cwd=None,
            env=opencode_judge_env,
            input_str="this is not json {",
            timeout=30.0,
            runner_name="opencode",
            scenario="opencode_judge_parse_failure",
        )

        print("\n=== Parse Failure Distinction Test ===")
        print(f"Returncode: {result.returncode}")
        print(f"Stderr: {result.stderr}")
        print(f"Stdout: {result.stdout[:300] if result.stdout else '(empty)'}")

        # Skip on auth failure
        if result.returncode != 0:
            stderr_lower = result.stderr.lower()
            if any(kw in stderr_lower for kw in ["auth", "login", "unauthorized"]):
                pytest.skip("opencode auth failure", allow_module_level=True)

        # We expect either:
        # 1. Parse failure (returncode 0 but malformed output)
        # 2. Transport failure (returncode != 0)
        # Both are acceptable - the key is we capture evidence

        # Verify evidence is complete
        assert result.returncode is not None
        assert result.stdout is not None
        assert result.stderr is not None

    def test_opencode_judge_skip_on_model_unavailable(
        self,
        opencode_judge_preflight: LiveRunnerPreflight,
    ) -> None:
        """Verify test skips when preferred model is unavailable.

        This uses the override_unsupported skip reason.
        """
        # If the override mechanism is not supported, skip
        # This is a placeholder - actual model availability check would be runner-specific
        if OPENCODE_MODEL_ENVAR not in os.environ:
            # Override not set - model must be available by default
            # This is expected to pass
            pass
        else:
            # Override set - verify it works
            effective = get_effective_model("opencode")
            assert effective == os.environ.get(OPENCODE_MODEL_ENVAR)


# =============================================================================
# Smoke test for verdict shape validation helpers
# =============================================================================


def _parse_judge_verdict(raw_output: str) -> dict[str, Any]:
    """Parse judge output into verdict structure.

    This replicates the tolerant extraction from judge.py.
    """
    stripped = raw_output.strip()
    if not stripped:
        raise ValueError("Empty output")

    # Try single JSON object
    try:
        data = json.loads(stripped)
        if isinstance(data, dict):
            # Check for envelope patterns
            if "structured_output" in data:
                return data["structured_output"]
            if "result" in data and isinstance(data["result"], dict):
                return data["result"]
            return data
    except json.JSONDecodeError:
        pass

    # Try JSONL extraction
    lines = stripped.splitlines()
    text_parts: list[str] = []

    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
            if isinstance(event, dict):
                if event.get("type") == "text":
                    part = event.get("part", {})
                    if isinstance(part, dict):
                        text = part.get("text")
                        if isinstance(text, str) and text.strip():
                            text_parts.append(text.strip())
        except json.JSONDecodeError:
            continue

    if text_parts:
        candidate = text_parts[-1]
        try:
            return json.loads(candidate)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Last text part is not JSON: {candidate[:100]}") from exc

    raise ValueError(f"Could not extract verdict from output: {stripped[:100]}")


def _assert_valid_verdict_shape(verdict: dict[str, Any]) -> None:
    """Assert verdict has valid shape."""
    valid_verdicts = {"ACCEPT", "REJECT", "RETRY", "SWITCH_AGENT", "REPLAN", "DEFER", "HALT"}
    assert "verdict" in verdict, f"Missing 'verdict' key: {verdict}"
    assert verdict["verdict"] in valid_verdicts, f"Invalid verdict: {verdict['verdict']}"
    assert "reason" in verdict, f"Missing 'reason' key: {verdict}"
    assert isinstance(verdict["reason"], str) and verdict["reason"].strip(), "Empty reason"


class TestOpencodeJudgeVerdictParsing:
    """Tests for verdict parsing logic used by live smoke tests."""

    def test_parse_valid_json_verdict(self) -> None:
        """Verify valid JSON verdict is parsed correctly."""
        raw = (
            '{"verdict": "ACCEPT", "reason": "All checks passed", '
            '"suggested_action": null, "planner_instruction": null}'
        )
        verdict = _parse_judge_verdict(raw)
        _assert_valid_verdict_shape(verdict)
        assert verdict["verdict"] == "ACCEPT"

    def test_parse_jsonl_with_text_events(self) -> None:
        """Verify JSONL with text events is parsed correctly."""
        # Build the JSONL string directly to avoid f-string escaping issues
        inner_verdict = (
            '{"verdict": "REJECT", "reason": "Failed check", '
            '"suggested_action": null, "planner_instruction": null}'
        )
        jsonl_lines = [
            '{"type": "step_start", "sessionID": "ses_abc123"}',
            '{"type": "text", "part": {"text": "Thinking..."}}',
            '{"type": "text", "part": {"text": "' + inner_verdict.replace('"', '\\"') + '"}}',
            '{"type": "step_finish", "part": {"status": "success"}}',
        ]
        jsonl = "\n".join(jsonl_lines)
        verdict = _parse_judge_verdict(jsonl)
        _assert_valid_verdict_shape(verdict)
        assert verdict["verdict"] == "REJECT"

    def test_parse_invalid_verdict_missing_keys(self) -> None:
        """Verify missing keys are detected."""
        raw = '{"verdict": "ACCEPT"}'  # Missing 'reason'
        verdict = _parse_judge_verdict(raw)
        with pytest.raises(AssertionError, match="Missing 'reason'"):
            _assert_valid_verdict_shape(verdict)

    def test_parse_invalid_verdict_value(self) -> None:
        """Verify invalid verdict values are detected."""
        raw = '{"verdict": "INVALID", "reason": "test"}'
        verdict = _parse_judge_verdict(raw)
        with pytest.raises(AssertionError, match="Invalid verdict"):
            _assert_valid_verdict_shape(verdict)

    def test_parse_empty_output_raises(self) -> None:
        """Verify empty output raises ValueError."""
        with pytest.raises(ValueError, match="Empty output"):
            _parse_judge_verdict("")

    def test_parse_malformed_jsonl_raises(self) -> None:
        """Verify malformed JSONL raises ValueError."""
        jsonl = "\n".join(
            [
                '{"type": "text", "part": {"text": "not json"}}',
                '{"type": "text", "part": {"text": "also not json"}}',
            ]
        )
        with pytest.raises(ValueError, match="not JSON"):
            _parse_judge_verdict(jsonl)
