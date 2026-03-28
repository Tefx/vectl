"""EXPECTED-RED pre-implementation tests for Codex parser behavior.

These tests define the expected behavior for CodexOutputParser BEING
IMPLEMENTATION. They will FAIL until driver-multi-runner-hardening.impl-runners-extended
lands the parser implementation.

Parser Semantics (from DRIVER-BLUEPRINT.md):
    - Format: JSONL stream (thread.started -> item.completed -> turn.completed)
    - Session ID: thread.started event -> thread_id field
    - Status: SUCCESS if item.completed events exist, FAIL otherwise
    - Output: Concatenated text from all item.completed events
    - Tokens: turn.completed.usage

Downstream Owner: driver-multi-runner-hardening.impl-runners-extended
"""

import pytest

from vectl.driver.parsers import CodexOutputParser
from vectl.driver.types import RunnerStatus


class TestCodexParserSuccessCases:
    """Expected-RED tests for successful Codex JSONL parsing.

    GAP: CodexOutputParser.parse() raises NotImplementedError.
    Implementation must parse JSONL stream and extract session_id,
    output text, and tokens.
    """

    @pytest.mark.skip(reason="EXPECTED-RED: CodexOutputParser not implemented")
    def test_parse_success_stream_single_item(self) -> None:
        """Parse Codex JSONL stream with single item.completed event.

        Expected behavior:
        - Extract session_id from thread.started event
        - Concatenate text from item.completed events
        - Return SUCCESS status
        - Extract tokens from turn.completed
        """
        parser = CodexOutputParser()
        stdout = """{"type": "thread.started", "thread_id": "abc-123-def"}
{"type": "item.completed", "item": {"text": "Hello world"}}
{"type": "turn.completed", "usage": {"input_tokens": 100, "output_tokens": 50}}"""

        result = parser.parse(stdout, elapsed_seconds=1.5)

        assert result.status == RunnerStatus.SUCCESS
        assert result.session_id == "abc-123-def"
        assert result.output == "Hello world"
        assert result.elapsed_seconds == 1.5
        assert result.exit_code == 0
        assert result.tokens == {"input_tokens": 100, "output_tokens": 50}

    @pytest.mark.skip(reason="EXPECTED-RED: CodexOutputParser not implemented")
    def test_parse_success_stream_multiple_items(self) -> None:
        """Parse Codex JSONL stream with multiple item.completed events."""
        parser = CodexOutputParser()
        stdout = """{"type": "thread.started", "thread_id": "xyz-789"}
{"type": "item.completed", "item": {"text": "Line 1"}}
{"type": "item.completed", "item": {"text": "Line 2"}}
{"type": "item.completed", "item": {"text": "Line 3"}}
{"type": "turn.completed", "usage": {"input_tokens": 200, "output_tokens": 100}}"""

        result = parser.parse(stdout, elapsed_seconds=3.0)

        assert result.status == RunnerStatus.SUCCESS
        assert result.session_id == "xyz-789"
        # Multiple items concatenate
        assert result.output == "Line 1\nLine 2\nLine 3"
        assert result.tokens == {"input_tokens": 200, "output_tokens": 100}

    @pytest.mark.skip(reason="EXPECTED-RED: CodexOutputParser not implemented")
    def test_parse_success_no_tokens(self) -> None:
        """Parse success stream without turn.completed (tokens = None)."""
        parser = CodexOutputParser()
        stdout = """{"type": "thread.started", "thread_id": "no-tokens"}
{"type": "item.completed", "item": {"text": "Output"}}"""

        result = parser.parse(stdout, elapsed_seconds=1.0)

        assert result.status == RunnerStatus.SUCCESS
        assert result.session_id == "no-tokens"
        assert result.output == "Output"
        assert result.tokens is None


class TestCodexParserFailures:
    """Expected-RED tests for Codex failure detection.

    GAP: CodexOutputParser needs to detect FAIL status when no
    item.completed events present.
    """

    @pytest.mark.skip(reason="EXPECTED-RED: CodexOutputParser not implemented")
    def test_parse_fail_no_items(self) -> None:
        """Parse Stream without item.completed events returns FAIL."""
        parser = CodexOutputParser()
        stdout = """{"type": "thread.started", "thread_id": "empty"}
{"type": "turn.completed", "usage": {}}"""

        result = parser.parse(stdout, elapsed_seconds=1.0)

        # No item.completed means FAIL
        assert result.status == RunnerStatus.FAIL
        assert result.session_id == "empty"
        assert result.output == ""
        assert result.exit_code == 1

    @pytest.mark.skip(reason="EXPECTED-RED: CodexOutputParser not implemented")
    def test_parse_fail_empty_stream(self) -> None:
        """Parse empty stream returns TRANSPORT_ERROR."""
        parser = CodexOutputParser()
        stdout = ""

        result = parser.parse(stdout, elapsed_seconds=0.5)

        assert result.status == RunnerStatus.TRANSPORT_ERROR
        assert result.session_id is None
        assert result.exit_code is None


class TestCodexParserSessionIdExtraction:
    """Expected-RED tests for Codex session ID extraction.

    GAP: CodexOutputParser must extract thread_id from thread.started event.
    """

    @pytest.mark.skip(reason="EXPECTED-RED: CodexOutputParser not implemented")
    def test_session_id_from_thread_started(self) -> None:
        """Session ID extracted from thread.started.thread_id field."""
        parser = CodexOutputParser()
        stdout = """{"type": "thread.started", "thread_id": "01234567-abcd-efgh"}
{"type": "item.completed", "item": {"text": "done"}}"""

        result = parser.parse(stdout, elapsed_seconds=1.0)

        assert result.session_id == "01234567-abcd-efgh"

    @pytest.mark.skip(reason="EXPECTED-RED: CodexOutputParser not implemented")
    def test_no_thread_started_returns_none_session_id(self) -> None:
        """Missing thread.started event results in session_id=None."""
        parser = CodexOutputParser()
        stdout = """{"type": "item.completed", "item": {"text": "test"}}"""

        result = parser.parse(stdout, elapsed_seconds=1.0)

        # No thread.started = no session_id
        assert result.session_id is None


class TestCodexParserTransportErrorCases:
    """Expected-RED tests for Codex transport error handling.

    GAP: CodexOutputParser must handle malformed JSONL gracefully.
    """

    @pytest.mark.skip(reason="EXPECTED-RED: CodexOutputParser not implemented")
    def test_malformed_json_returns_transport_error(self) -> None:
        """Malformed JSON returns TRANSPORT_ERROR."""
        parser = CodexOutputParser()
        stdout = """{"type": "thread.started", "thread_id": "bad"
invalid json here"""

        result = parser.parse(stdout, elapsed_seconds=1.0)

        assert result.status == RunnerStatus.TRANSPORT_ERROR
        assert result.exit_code is None

    @pytest.mark.skip(reason="EXPECTED-RED: CodexOutputParser not implemented")
    def test_only_malformed_lines_returns_transport_error(self) -> None:
        """Stream with only malformed lines returns TRANSPORT_ERROR."""
        parser = CodexOutputParser()
        stdout = """not valid json
also not valid"""

        result = parser.parse(stdout, elapsed_seconds=1.0)

        assert result.status == RunnerStatus.TRANSPORT_ERROR


class TestCodexParserEdgeCases:
    """Expected-RED tests for Codex edge cases.

    GAP: Various edge cases need proper handling in implementation.
    """

    @pytest.mark.skip(reason="EXPECTED-RED: CodexOutputParser not implemented")
    def test_extra_fields_ignored(self) -> None:
        """Extra fields in events are ignored."""
        parser = CodexOutputParser()
        stdout = """{"type": "thread.started", "thread_id": "extra", "extra_field": "value"}
{"type": "item.completed", "item": {"text": "output", "extra": "ignored"}}"""

        result = parser.parse(stdout, elapsed_seconds=1.0)

        assert result.status == RunnerStatus.SUCCESS
        assert result.output == "output"

    @pytest.mark.skip(reason="EXPECTED-RED: CodexOutputParser not implemented")
    def test_nested_item_structure(self) -> None:
        """Nested item structure is handled correctly."""
        parser = CodexOutputParser()
        stdout = """{"type": "thread.started", "thread_id": "nested"}
{"type": "item.completed", "item": {"text": "Nested output here"}}
{"type": "turn.completed", "usage": {"input_tokens": 10}}"""

        result = parser.parse(stdout, elapsed_seconds=1.0)

        assert result.status == RunnerStatus.SUCCESS
        assert result.output == "Nested output here"
