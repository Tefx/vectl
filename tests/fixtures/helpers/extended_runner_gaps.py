"""Test fixtures and helpers for extended runner tests.

This module provides test utilities for Codex and Gemini runner testing.
It documents expected-RED test patterns and provides mock output fixtures
for downstream implementation validation.

Downstream Owner: driver-multi-runner-hardening.impl-runners-extended
"""


# =============================================================================
# Codex JSONL Output Fixtures
# =============================================================================

CODEX_SUCCESS_SINGLE_ITEM = """{"type": "thread.started", "thread_id": "abc-123-def"}
{"type": "item.completed", "item": {"text": "Hello world"}}
{"type": "turn.completed", "usage": {"input_tokens": 100, "output_tokens": 50}}"""
"""Expected Codex output: success with single item."""

CODEX_SUCCESS_MULTIPLE_ITEMS = """{"type": "thread.started", "thread_id": "xyz-789"}
{"type": "item.completed", "item": {"text": "Line 1"}}
{"type": "item.completed", "item": {"text": "Line 2"}}
{"type": "item.completed", "item": {"text": "Line 3"}}
{"type": "turn.completed", "usage": {"input_tokens": 200, "output_tokens": 100}}"""
"""Expected Codex output: success with multiple items."""

CODEX_FAIL_NO_ITEMS = """{"type": "thread.started", "thread_id": "empty"}
{"type": "turn.completed", "usage": {}}"""
"""Expected Codex output: fail with no items."""

CODEX_MALFORMED_JSON = """{"type": "thread.started", "thread_id": "bad"
invalid json here"""
"""Expected Codex output: malformed JSON for transport error."""


# =============================================================================
# Gemini JSON Output Fixtures
# =============================================================================

GEMINI_SUCCESS_SIMPLE = '{"session_id": "gemini-session-123", "result": "Hello world", "subtype": "success", "usage": {"input_tokens": 100, "output_tokens": 50}}'
"""Expected Gemini output: success with result."""

GEMINI_SUCCESS_STRUCTURED = (
    '{"session_id": "xyz", "structured_output": {"answer": 42}, "subtype": "success"}'
)
"""Expected Gemini output: success with structured output."""

GEMINI_ERROR = '{"session_id": "err-123", "result": "Error: file not found", "subtype": "error"}'
"""Expected Gemini output: error with result."""

GEMINI_NO_SUBTYPE = '{"session_id": "no-subtype", "result": "some output"}'
"""Expected Gemini output: missing subtype defaults to fail."""

GEMINI_MALFORMED_JSON = '{"session_id": "broken", "result": "incomplete'
"""Expected Gemini output: malformed JSON for transport error."""


# =============================================================================
# Expected Parser Behavior Gaps
# =============================================================================

EXPECTED_PARSER_GAPS = {
    "codex": [
        "CodexOutputParser.parse() raises NotImplementedError",
        "Must extract session_id from thread.started.thread_id field",
        "Must concatenate text from all item.completed events",
        "Must detect SUCCESS when item.completed events present",
        "Must detect FAIL when no item.completed events",
        "Must extract tokens from turn.completed.usage field",
        "Must return TRANSPORT_ERROR on malformed JSONL",
    ],
    "gemini": [
        "GeminiOutputParser.parse() raises NotImplementedError",
        "Must parse single JSON object (like Claude)",
        "Must extract session_id from session_id field",
        "Must detect SUCCESS when subtype == 'success'",
        "Must detect FAIL when subtype != 'success' or missing",
        "Must extract tokens from usage field",
        "Cost tracking may be unavailable (set cost_usd = None)",
        "Must return TRANSPORT_ERROR on malformed JSON",
        "Session ID format may be numeric (not UUID)",
    ],
}

# =============================================================================
# Expected Runner Behavior Gaps
# =============================================================================

EXPECTED_RUNNER_GAPS = {
    "codex": [
        "CodexRunnerStub.dispatch() raises NotImplementedError",
        "Must use stdin_dash mode with '-' argument",
        "Must use resume_command instead of resume_flag for resume",
        "Must support -C flag for working directory",
        "Must handle prompt_mode='stdin_dash' in dispatch",
        "Must build different argv for initial dispatch vs resume",
    ],
    "gemini": [
        "GeminiRunnerStub.dispatch() raises NotImplementedError",
        "Must use --yolo or --approval-mode yolo for auto-approve",
        "Must use standard stdin mode",
        "Must use --resume INDEX flag for resume (not UUID)",
        "Experimental: elevated failure risk, graceful error handling",
        "May require additional authentication setup",
        "Cost tracking may be unavailable",
    ],
}

# =============================================================================
# Protocol Compatibility Gaps
# =============================================================================

EXPECTED_PROTOCOL_GAPS = [
    "Extended runners must implement Runner protocol",
    "Extended runners must return RunnerHandle from dispatch()",
    "RunnerHandle must have session_id, pid, wait(), kill(), is_alive()",
    "wait() must return RunnerResult with consistent fields",
    "All runners share the same RunnerStatus enum values",
    "Fallback chain: codex -> opencode, gemini -> opencode (after 2 failures)",
    "Session reuse may not work for experimental runners",
]


def get_expected_gaps_summary() -> str:
    """Get summary of all expected-RED gaps for downstream owner.

    Returns:
        Markdown-formatted summary of implementation gaps.
    """
    return """
# Expected-RED Gaps for driver-multi-runner-hardening.impl-runners-extended

## Parser Gaps

### CodexOutputParser
{codex_parser_gaps}

### GeminiOutputParser
{gemini_parser_gaps}

## Runner Gaps

### CodexRunner
{codex_runner_gaps}

### GeminiRunner
{gemini_runner_gaps}

## Protocol Compatibility Gaps
{protocol_gaps}

## Test Files
- test_codex_parser_behavior.py: Codex parser expected-RED tests
- test_gemini_parser_behavior.py: Gemini parser expected-RED tests
- test_codex_resume_behavior.py: Codex resume semantics expected-RED tests
- test_gemini_resume_behavior.py: Gemini resume semantics expected-RED tests
- test_extended_runner_protocol.py: Protocol compatibility expected-RED tests

All tests are marked with @pytest.mark.skip(reason="EXPECTED-RED: ... not implemented")
and will be unskipped as implementation lands.
""".format(
        codex_parser_gaps="\n".join(f"- {gap}" for gap in EXPECTED_PARSER_GAPS["codex"]),
        gemini_parser_gaps="\n".join(f"- {gap}" for gap in EXPECTED_PARSER_GAPS["gemini"]),
        codex_runner_gaps="\n".join(f"- {gap}" for gap in EXPECTED_RUNNER_GAPS["codex"]),
        gemini_runner_gaps="\n".join(f"- {gap}" for gap in EXPECTED_RUNNER_GAPS["gemini"]),
        protocol_gaps="\n".join(f"- {gap}" for gap in EXPECTED_PROTOCOL_GAPS),
    )
