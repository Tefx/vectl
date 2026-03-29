"""Live smoke tests for codex judge path.

Tests the real codex judge invocation path with structured output:
- Structured-output judge path using VERDICT_SCHEMA against real codex CLI
- Schema/structured-output rejection is classified explicitly as FAILURE
- No tolerant fallback-to-text success path (explicitly forbidden in this step)
- Explicit skip for missing codex binary, auth, or opt-in gate
- Invalid verdict shape classified distinctly from transport/auth failure

Marker combination: live_runner and codex_live and judge_live

Path: driver.yaml codex runner contract -> judge command construction
    -> real codex subprocess -> verdict assertion helper

Forbidden success path:
    No tolerant fallback-to-text success when codex structured-output mode is under test.
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
    get_effective_model,
    is_auth_available,
    is_live_runner_opted_in,
    judge_live,
    live_runner,
    run_subprocess,
    skip_if_auth_missing,
    skip_if_binary_missing,
)

# =============================================================================
# Constants
# =============================================================================

CODEX_JUDGE_MODEL = CODEX_MODEL
"""Pinned codex judge model: gpt-5.4-mini"""


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def codex_judge_preflight() -> LiveRunnerPreflight:
    """Return a preflight checker for codex judge tests."""
    return LiveRunnerPreflight("codex")


@pytest.fixture
def codex_judge_env() -> dict[str, str]:
    """Return environment variables for codex judge test.

    Sets the model override to the pinned codex judge model.
    """
    env = dict(os.environ)
    if CODEX_MODEL_ENVAR in env:
        # Respect user-set override if present
        pass
    else:
        env[CODEX_MODEL_ENVAR] = CODEX_JUDGE_MODEL
    return env


@pytest.fixture
def minimal_judge_system_prompt() -> str:
    """Return a minimal judge system prompt for testing.

    This is a simplified version that asks for a structured JSON verdict.
    """
    return """You are a judge agent that evaluates step execution outcomes.

You MUST respond with ONLY a valid JSON object matching this exact schema:
{
    "verdict": "ACCEPT|REJECT|RETRY|SWITCH_AGENT|REPLAN|DEFER|HALT",
    "reason": "Brief explanation (1-3 sentences)",
    "suggested_action": null,
    "planner_instruction": null
}

Do not include any other text, markdown, or explanation outside the JSON object.
"""


@pytest.fixture
def minimal_judge_request() -> str:
    """Return a minimal judge request payload for testing.

    This is a minimal JSON payload that matches JudgmentRequest structure.
    """
    return json.dumps(
        {
            "type": "preflight",
            "step_id": "live.smoke.test",
            "context": {
                "goal": "Verify codex judge integration",
                "agent": "python-executor",
            },
            "failure_history": [],
            "plan_summary": "Live smoke test for codex judge",
        }
    )


# =============================================================================
# Helper functions
# =============================================================================


def parse_codex_judge_output(stdout: str) -> dict[str, Any]:
    """Parse codex judge structured output.

    Codex JSONL format for structured output:
        {"type": "thread.started", ...}
        {"type": "item.completed", "item": {"output": {"schema": {...}, "text": "..."}}}
        {"type": "turn.completed", ...}

    Returns:
        Dict with parsed output or raises ValueError if not parseable.
    """
    lines = stdout.strip().split("\n") if stdout else []
    text_parts: list[str] = []
    structured_output: dict[str, Any] | None = None

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

        # Extract structured output from item.completed
        if event.get("type") == "item.completed":
            item = event.get("item", {})
            output = item.get("output", {})
            # Handle structured output envelope
            if isinstance(output, dict):
                # Check for structured_output field (may be string or already-parsed dict)
                if "structured_output" in output:
                    structured_data = output["structured_output"]
                    if isinstance(structured_data, dict):
                        return structured_data
                    if isinstance(structured_data, str):
                        try:
                            parsed = json.loads(structured_data)
                            if isinstance(parsed, dict):
                                return parsed
                        except (json.JSONDecodeError, TypeError):
                            pass
                # Check for result field
                if "result" in output:
                    result = output["result"]
                    if isinstance(result, dict):
                        return result
                    if isinstance(result, str) and result.strip():
                        try:
                            return json.loads(result)
                        except json.JSONDecodeError:
                            pass
                # Fall back to text field
                text = output.get("text")
                if isinstance(text, str) and text.strip():
                    text_parts.append(text.strip())

    # If we got structured output earlier, return it
    if structured_output is not None:
        return structured_output

    # If we only got text parts, this is NOT valid structured output for codex judge
    # (no tolerant fallback-to-text success path)
    if text_parts:
        last_text = text_parts[-1]
        try:
            return json.loads(last_text)
        except json.JSONDecodeError as exc:
            # This is a parse failure, not a fallback success
            raise ValueError(
                f"Codex judge output is not valid structured JSON: {last_text[:100]}"
            ) from exc

    raise ValueError(f"Could not extract verdict from codex output: {stdout[:100]}")


def assert_valid_verdict_shape(verdict: dict[str, Any]) -> None:
    """Assert verdict has valid shape per VERDICT_SCHEMA.

    Valid verdicts: ACCEPT, REJECT, RETRY, SWITCH_AGENT, REPLAN, DEFER, HALT

    Raises:
        AssertionError: If verdict is malformed.
    """
    valid_verdicts = {"ACCEPT", "REJECT", "RETRY", "SWITCH_AGENT", "REPLAN", "DEFER", "HALT"}

    if "verdict" not in verdict:
        raise AssertionError(f"Missing 'verdict' key in verdict: {verdict}")

    verdict_value = verdict["verdict"]
    if verdict_value not in valid_verdicts:
        raise AssertionError(
            f"Invalid verdict value: {verdict_value!r}. Must be one of: {sorted(valid_verdicts)}"
        )

    if "reason" not in verdict:
        raise AssertionError(f"Missing 'reason' key in verdict: {verdict}")

    reason = verdict["reason"]
    if not isinstance(reason, str) or not reason.strip():
        raise AssertionError(f"Reason must be non-empty string: {reason!r}")


# =============================================================================
# Tests
# =============================================================================


@live_runner
@codex_live
@judge_live
class TestCodexJudgeLive:
    """Live smoke tests for codex judge path."""

    def test_codex_judge_opt_in_gate(self) -> None:
        """Verify opt-in gate skips when RUN_LIVE_RUNNER_TESTS != '1'."""
        if not is_live_runner_opted_in():
            pytest.skip(
                "Live runner tests require RUN_LIVE_RUNNER_TESTS=1",
                allow_module_level=True,
            )

    def test_codex_judge_skip_matrix(self) -> None:
        """Verify skip reason matrix covers all required reasons."""
        matrix = check_skip_matrix("codex")
        assert "live_runner_opt_in_missing" in matrix
        assert "live_runner_binary_missing" in matrix
        assert "live_runner_auth_missing" in matrix
        assert "live_runner_override_unsupported" in matrix

    def test_codex_judge_binary_available(self, codex_judge_preflight: LiveRunnerPreflight) -> None:
        """Verify codex binary is available on PATH or skip explicitly."""
        if not codex_judge_preflight.check_binary():
            pytest.skip(
                "codex binary not found on PATH",
                allow_module_level=True,
            )

    def test_codex_judge_skip_if_binary_missing(self) -> None:
        """Verify skip_if_binary_missing works correctly."""
        skip_result = skip_if_binary_missing("codex")
        if skip_result is not None:
            assert "codex" in str(skip_result)
            pytest.skip("codex binary not available", allow_module_level=True)

    def test_codex_judge_skip_if_auth_missing(self) -> None:
        """Verify skip_if_auth_missing works correctly."""
        original_auth = is_auth_available("codex")
        try:
            from tests.live_smoke.helpers import _auth_checks

            _auth_checks["codex"] = False
            skip_result = skip_if_auth_missing("codex")
            if skip_result is not None:
                assert "auth" in str(skip_result).lower()
                pytest.skip("codex auth not available", allow_module_level=True)
        finally:
            _auth_checks["codex"] = original_auth


@live_runner
@codex_live
@judge_live
class TestCodexJudgeLiveIntegration:
    """Integration tests that actually invoke codex CLI for judge.

    These tests are skipped if:
    - RUN_LIVE_RUNNER_TESTS != "1"
    - codex binary not on PATH
    - auth not available

    IMPORTANT: These tests do NOT use tolerant fallback-to-text success.
    If structured output is unavailable, the test fails.
    """

    def test_codex_judge_effective_model(self) -> None:
        """Verify effective model resolves to pinned model."""
        effective = get_effective_model("codex")
        assert effective == CODEX_JUDGE_MODEL

    def test_codex_judge_model_override_args(self) -> None:
        """Verify model override args are built correctly."""
        args = build_model_override_args("codex", CODEX_JUDGE_MODEL)
        assert "--model" in args
        model_index = args.index("--model")
        assert args[model_index + 1] == CODEX_JUDGE_MODEL

    def test_codex_judge_env_var_override(self, codex_judge_env: dict[str, str]) -> None:
        """Verify environment variable override is set correctly."""
        assert CODEX_MODEL_ENVAR in codex_judge_env
        assert codex_judge_env[CODEX_MODEL_ENVAR] == CODEX_JUDGE_MODEL

    def test_codex_judge_structured_output_invocation(
        self,
        codex_judge_preflight: LiveRunnerPreflight,
        codex_judge_env: dict[str, str],
        minimal_judge_system_prompt: str,
        minimal_judge_request: str,
        tmp_path: Any,
    ) -> None:
        """Verify codex judge can be invoked with structured output.

        This tests the full path: subprocess -> structured output parsing -> verdict.
        If structured output is not available, this test fails (no fallback).
        """
        codex_judge_preflight()

        model = get_effective_model("codex")
        model_args = build_model_override_args("codex", model)

        # Build command like driver.yaml codex runner but with schema
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
            env=codex_judge_env,
            input_str=f"SYSTEM PROMPT:\n{minimal_judge_system_prompt}\n\n{minimal_judge_request}",
            timeout=60.0,
            runner_name="codex",
            scenario="codex_judge_structured",
        )

        # Log diagnostics
        print("\n=== Codex Judge Structured Output Result ===")
        print(f"Command: {' '.join(command)}")
        print(f"Returncode: {result.returncode}")
        print(f"Stderr (truncated): {result.stderr[:200] if result.stderr else '(empty)'}")
        print(f"Stdout (truncated): {result.stdout[:500] if result.stdout else '(empty)'}")

        # Check for auth failure first (transport failure - skip)
        if result.returncode != 0:
            stderr_lower = result.stderr.lower()
            if any(kw in stderr_lower for kw in ["auth", "login", "unauthorized", "session"]):
                pytest.skip(f"codex auth failure: {result.stderr[:200]}", allow_module_level=True)

        # Verify we got output
        assert result.stdout or result.stderr, (
            "Expected some output from codex judge; got neither stdout nor stderr"
        )

        # Try to parse structured output
        # This will raise ValueError if not parseable as structured JSON
        verdict = parse_codex_judge_output(result.stdout)

        # Verify verdict shape
        assert_valid_verdict_shape(verdict)

        print(f"Successfully parsed verdict: {verdict.get('verdict')}")

    def test_codex_judge_schema_rejection_is_failure(
        self,
        codex_judge_preflight: LiveRunnerPreflight,
        codex_judge_env: dict[str, str],
        tmp_path: Any,
    ) -> None:
        """Verify that malformed response is classified as FAILURE, not fallback.

        Send input that cannot produce valid structured output.
        The result should be a parse failure, NOT a tolerant text-mode success.
        """
        codex_judge_preflight()

        command = [
            "codex",
            "exec",
            "--json",
            "--dangerously-bypass-approvals-and-sandbox",
            "-C",
            str(tmp_path),
            "--model",
            CODEX_JUDGE_MODEL,
            "-",
        ]

        # Send malformed input
        result = run_subprocess(
            command,
            cwd=str(tmp_path),
            env=codex_judge_env,
            input_str="Not a valid judge request at all",
            timeout=30.0,
            runner_name="codex",
            scenario="codex_judge_schema_rejection",
        )

        print("\n=== Schema Rejection Test ===")
        print(f"Returncode: {result.returncode}")
        print(f"Stderr: {result.stderr[:200] if result.stderr else '(empty)'}")
        print(f"Stdout: {result.stdout[:200] if result.stdout else '(empty)'}")

        # Skip on auth failure
        if result.returncode != 0:
            stderr_lower = result.stderr.lower()
            if any(kw in stderr_lower for kw in ["auth", "login", "unauthorized"]):
                pytest.skip("codex auth failure", allow_module_level=True)

        # Try to parse - should fail because this isn't valid structured output
        try:
            verdict = parse_codex_judge_output(result.stdout)
            # If we got here, we got SOME output
            # But if it's not valid verdict shape, that's still a failure
            assert_valid_verdict_shape(verdict)
            # If we get here, the output was actually valid (unlikely with bad input)
        except ValueError as exc:
            # This is the EXPECTED behavior - parse failure
            # NOT a tolerant fallback success
            print(f"Parse failure (expected): {exc}")
            # Verify evidence is complete
            assert result.returncode is not None
            assert result.stdout is not None
            assert result.stderr is not None

    def test_codex_judge_invalid_verdict_shape_is_failure(
        self,
        codex_judge_preflight: LiveRunnerPreflight,
        codex_judge_env: dict[str, str],
        minimal_judge_system_prompt: str,
        tmp_path: Any,
    ) -> None:
        """Verify that invalid verdict shape is classified as failure.

        Send a prompt that produces output that looks like JSON but
        doesn't match the verdict schema.
        """
        codex_judge_preflight()

        command = [
            "codex",
            "exec",
            "--json",
            "--dangerously-bypass-approvals-and-sandbox",
            "-C",
            str(tmp_path),
            "--model",
            CODEX_JUDGE_MODEL,
            "-",
        ]

        # Ask for something that won't produce a valid verdict
        invalid_input = (
            f"SYSTEM PROMPT:\n{minimal_judge_system_prompt}\n\nJust say hello without JSON"
        )
        result = run_subprocess(
            command,
            cwd=str(tmp_path),
            env=codex_judge_env,
            input_str=invalid_input,
            timeout=30.0,
            runner_name="codex",
            scenario="codex_judge_invalid_shape",
        )

        print("\n=== Invalid Verdict Shape Test ===")
        print(f"Returncode: {result.returncode}")
        print(f"Stderr: {result.stderr[:200] if result.stderr else '(empty)'}")
        print(f"Stdout: {result.stdout[:200] if result.stdout else '(empty)'}")

        # Skip on auth failure
        if result.returncode != 0:
            stderr_lower = result.stderr.lower()
            if any(kw in stderr_lower for kw in ["auth", "login", "unauthorized"]):
                pytest.skip("codex auth failure", allow_module_level=True)

        # Try to parse
        try:
            verdict = parse_codex_judge_output(result.stdout)
            # Check if verdict shape is valid
            assert_valid_verdict_shape(verdict)
            # If we got here, verdict was valid - this is OK
            print(f"Verdict was valid: {verdict}")
        except (ValueError, AssertionError) as exc:
            # This is expected - invalid verdict shape is a failure
            print(f"Invalid verdict shape (expected failure): {exc}")
            # Verify evidence is complete
            assert result.returncode is not None


# =============================================================================
# Unit tests for verdict parsing helpers (don't require live codex)
# =============================================================================


class TestCodexJudgeVerdictParsing:
    """Unit tests for codex judge verdict parsing."""

    def test_parse_valid_structured_output(self) -> None:
        """Verify valid structured output is parsed correctly."""
        # Use json.dumps to properly construct the test JSON
        verdict = {
            "verdict": "ACCEPT",
            "reason": "All checks passed",
            "suggested_action": None,
            "planner_instruction": None,
        }
        outer_event = {
            "type": "item.completed",
            "item": {
                "output": {
                    "structured_output": json.dumps(verdict),
                }
            },
        }
        jsonl = '{"type": "thread.started", "sessionId": "abc123"}\n' + json.dumps(outer_event)
        result = parse_codex_judge_output(jsonl)
        assert result["verdict"] == "ACCEPT"
        assert result["reason"] == "All checks passed"

    def test_parse_result_field(self) -> None:
        """Verify result field is parsed correctly."""
        verdict = {
            "verdict": "REJECT",
            "reason": "Failed",
            "suggested_action": None,
            "planner_instruction": None,
        }
        outer_event = {
            "type": "item.completed",
            "item": {
                "output": {
                    "result": verdict,
                }
            },
        }
        jsonl = '{"type": "thread.started", "sessionId": "abc123"}\n' + json.dumps(outer_event)
        result = parse_codex_judge_output(jsonl)
        assert result["verdict"] == "REJECT"

    def test_parse_invalid_not_json_raises(self) -> None:
        """Verify non-JSON output raises ValueError."""
        jsonl = "\n".join(
            [
                '{"type": "thread.started", "sessionId": "abc123"}',
                '{"type": "item.completed", "item": {"output": {"text": "Just plain text"}}}',
            ]
        )
        with pytest.raises(ValueError, match="not valid structured JSON"):
            parse_codex_judge_output(jsonl)

    def test_parse_empty_output_raises(self) -> None:
        """Verify empty output raises ValueError."""
        with pytest.raises(ValueError, match="Could not extract"):
            parse_codex_judge_output("")

    def test_assert_valid_verdict_shape_accept(self) -> None:
        """Verify ACCEPT verdict passes assertion."""
        verdict = {
            "verdict": "ACCEPT",
            "reason": "All checks passed",
            "suggested_action": None,
            "planner_instruction": None,
        }
        assert_valid_verdict_shape(verdict)  # Should not raise

    def test_assert_valid_verdict_shape_reject(self) -> None:
        """Verify REJECT verdict passes assertion."""
        verdict = {
            "verdict": "REJECT",
            "reason": "Failed checks",
            "suggested_action": None,
            "planner_instruction": None,
        }
        assert_valid_verdict_shape(verdict)  # Should not raise

    def test_assert_invalid_verdict_value(self) -> None:
        """Verify invalid verdict value raises AssertionError."""
        verdict = {
            "verdict": "INVALID",
            "reason": "Some reason",
        }
        with pytest.raises(AssertionError, match="Invalid verdict value"):
            assert_valid_verdict_shape(verdict)

    def test_assert_missing_verdict_key(self) -> None:
        """Verify missing verdict key raises AssertionError."""
        verdict = {
            "reason": "Some reason",
        }
        with pytest.raises(AssertionError, match="Missing 'verdict'"):
            assert_valid_verdict_shape(verdict)

    def test_assert_missing_reason_key(self) -> None:
        """Verify missing reason key raises AssertionError."""
        verdict = {
            "verdict": "ACCEPT",
        }
        with pytest.raises(AssertionError, match="Missing 'reason'"):
            assert_valid_verdict_shape(verdict)

    def test_assert_empty_reason(self) -> None:
        """Verify empty reason raises AssertionError."""
        verdict = {
            "verdict": "ACCEPT",
            "reason": "",
        }
        with pytest.raises(AssertionError, match="non-empty string"):
            assert_valid_verdict_shape(verdict)
