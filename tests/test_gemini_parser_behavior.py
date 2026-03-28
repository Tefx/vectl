"""EXPECTED-RED pre-implementation tests for Gemini parser behavior.

These tests define the expected behavior for GeminiOutputParser BEFORE
IMPLEMENTATION. They will FAIL until driver-multi-runner-hardening.impl-runners-extended
lands the parser implementation.

Parser Semantics (from DRIVER-BLUEPRINT.md):
    - Format: Single JSON object (like Claude)
    - Session ID: session_id field (may be different format)
    - Status: subtype field (success/error)
    - Output: result field
    - Tokens: usage field
    - Cost: unverified (may be unavailable)

Downstream Owner: driver-multi-runner-hardening.impl-runners-extended
"""

import pytest

from vectl.driver.parsers import GeminiOutputParser
from vectl.driver.types import RunnerStatus


class TestGeminiParserSuccessCases:
    """Expected-RED tests for successful Gemini JSON parsing.

    GAP: GeminiOutputParser.parse() raises NotImplementedError.
    Implementation must parse single JSON object and extract session_id,
    output text, and tokens (similar to Claude format).
    """

    @pytest.mark.skip(reason="EXPECTED-RED: GeminiOutputParser not implemented")
    def test_parse_success_with_result(self) -> None:
        """Parse Gemini success output with result field.

        Expected behavior:
        - Extract session_id from session_id field
        - Return SUCCESS status when subtype == "success"
        - Return result field as output
        - Extract tokens from usage field
        """
        parser = GeminiOutputParser()
        stdout = '{"session_id": "gemini-session-123", "result": "Hello world", "subtype": "success", "usage": {"input_tokens": 100, "output_tokens": 50}}'

        result = parser.parse(stdout, elapsed_seconds=2.0)

        assert result.status == RunnerStatus.SUCCESS
        assert result.session_id == "gemini-session-123"
        assert result.output == "Hello world"
        assert result.elapsed_seconds == 2.0
        assert result.exit_code == 0
        assert result.tokens == {"input_tokens": 100, "output_tokens": 50}

    @pytest.mark.skip(reason="EXPECTED-RED: GeminiOutputParser not implemented")
    def test_parse_success_with_structured_output(self) -> None:
        """Parse Gemini success with structured output (if supported)."""
        parser = GeminiOutputParser()
        stdout = '{"session_id": "xyz", "structured_output": {"answer": 42}, "subtype": "success"}'

        result = parser.parse(stdout, elapsed_seconds=1.5)

        assert result.status == RunnerStatus.SUCCESS
        # Structured output should be JSON-serialized
        import json

        assert json.loads(result.output) == {"answer": 42}

    @pytest.mark.skip(reason="EXPECTED-RED: GeminiOutputParser not implemented")
    def test_parse_success_no_tokens(self) -> None:
        """Parse success without usage field (tokens = None)."""
        parser = GeminiOutputParser()
        stdout = '{"session_id": "no-tokens", "result": "Output", "subtype": "success"}'

        result = parser.parse(stdout, elapsed_seconds=1.0)

        assert result.status == RunnerStatus.SUCCESS
        assert result.tokens is None

    @pytest.mark.skip(reason="EXPECTED-RED: GeminiOutputParser not implemented")
    def test_parse_cost_usd_unverified_none(self) -> None:
        """Cost tracking may be unavailable for Gemini.

        GAP: Gemini may not return cost information in output.
        Implementation should set cost_usd = None if not present.
        """
        parser = GeminiOutputParser()
        stdout = '{"session_id": "no-cost", "result": "done", "subtype": "success"}'

        result = parser.parse(stdout, elapsed_seconds=1.0)

        assert result.cost_usd is None


class TestGeminiParserFailures:
    """Expected-RED tests for Gemini failure detection.

    GAP: GeminiOutputParser needs to detect FAIL status when
    subtype == "error".
    """

    @pytest.mark.skip(reason="EXPECTED-RED: GeminiOutputParser not implemented")
    def test_parse_error_with_result(self) -> None:
        """Parse Gemini error output with result field."""
        parser = GeminiOutputParser()
        stdout = '{"session_id": "err-123", "result": "Error: file not found", "subtype": "error"}'

        result = parser.parse(stdout, elapsed_seconds=0.5)

        assert result.status == RunnerStatus.FAIL
        assert result.session_id == "err-123"
        assert result.output == "Error: file not found"
        assert result.exit_code == 1

    @pytest.mark.skip(reason="EXPECTED-RED: GeminiOutputParser not implemented")
    def test_parse_missing_subtype_defaults_to_fail(self) -> None:
        """Output without subtype defaults to FAIL."""
        parser = GeminiOutputParser()
        stdout = '{"session_id": "no-subtype", "result": "some output"}'

        result = parser.parse(stdout, elapsed_seconds=1.0)

        assert result.status == RunnerStatus.FAIL
        assert result.exit_code == 1


class TestGeminiParserTransportErrors:
    """Expected-RED tests for Gemini transport error handling.

    GAP: GeminiOutputParser must handle malformed JSON gracefully.
    """

    @pytest.mark.skip(reason="EXPECTED-RED: GeminiOutputParser not implemented")
    def test_malformed_json_returns_transport_error(self) -> None:
        """Malformed JSON returns TRANSPORT_ERROR."""
        parser = GeminiOutputParser()
        stdout = '{"session_id": "broken", "result": "incomplete'

        result = parser.parse(stdout, elapsed_seconds=1.0)

        assert result.status == RunnerStatus.TRANSPORT_ERROR
        assert result.session_id is None
        assert result.exit_code is None

    @pytest.mark.skip(reason="EXPECTED-RED: GeminiOutputParser not implemented")
    def test_empty_output_returns_transport_error(self) -> None:
        """Empty output returns TRANSPORT_ERROR."""
        parser = GeminiOutputParser()
        stdout = ""

        result = parser.parse(stdout, elapsed_seconds=0.5)

        assert result.status == RunnerStatus.TRANSPORT_ERROR
        assert result.session_id is None


class TestGeminiParserSessionIdFormat:
    """Expected-RED tests for Gemini session ID format.

    Note: Gemini may use different session ID format than Claude.
    Implementation should accept any session_id format.
    """

    @pytest.mark.skip(reason="EXPECTED-RED: GeminiOutputParser not implemented")
    def test_session_id_any_format(self) -> None:
        """Accept any session_id format (not UUID-specific)."""
        parser = GeminiOutputParser()
        # Gemini uses --resume INDEX, may have numeric ID
        stdout = '{"session_id": "0", "result": "done", "subtype": "success"}'

        result = parser.parse(stdout, elapsed_seconds=1.0)

        assert result.status == RunnerStatus.SUCCESS
        assert result.session_id == "0"

    @pytest.mark.skip(reason="EXPECTED-RED: GeminiOutputParser not implemented")
    def test_missing_session_id_accepted(self) -> None:
        """Missing session_id is accepted (session_id = None)."""
        parser = GeminiOutputParser()
        stdout = '{"result": "done", "subtype": "success"}'

        result = parser.parse(stdout, elapsed_seconds=1.0)

        assert result.status == RunnerStatus.SUCCESS
        assert result.session_id is None


class TestGeminiParserExperimentalNotes:
    """Documentation of Gemini experimental status effects on parser.

    Note: These are NOT tests - they document known issues that
    implementation must handle gracefully.
    """

    def test_cost_tracking_may_be_unavailable(self) -> None:
        """DOCUMENTATION: Cost tracking is unverified for Gemini.

        Implementation should set cost_usd = None and not fail
        if cost field is missing or has unexpected format.
        """
        # This is a documentation of known issue
        # Implementation should gracefully handle missing cost_usd
        assert True  # Placeholder for documentation

    def test_authentication_may_fail(self) -> None:
        """DOCUMENTATION: Gemini authentication may fail.

        Implementation should propagate TRANSPORT_ERROR for
        authentication failures (non-zero exit code with error output).
        """
        # Runner level handles this - parser just classifies output
        assert True  # Placeholder for documentation
