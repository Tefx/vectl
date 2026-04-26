"""Tests for orchestration evidence normalization."""

from __future__ import annotations

from vectl.orchestration.evidence import assess_freeform_evidence, summarize_plan_evidence


def test_summarize_plan_evidence_omits_opencode_event_stream() -> None:
    raw = (
        'OpenCode completed successfully (exit 0); stdout={"type": "session.updated"}\n'
        '{"type": "tool_use", "part": {"state": {"output": "very noisy"}}}'
    )

    summary = summarize_plan_evidence(raw)

    assert summary == (
        "OpenCode completed successfully (exit 0); "
        "stdout=<opencode JSON event stream omitted from plan evidence>"
    )
    assert "tool_use" not in summary


def test_assess_freeform_evidence_blocks_explicit_failure_status() -> None:
    raw = 'OpenCode completed successfully (exit 0); stdout=status: "FAIL"\nerror: tests failed'

    assessment = assess_freeform_evidence(raw)

    assert assessment.failed is True
    assert assessment.reason == "reported status='fail'"


def test_assess_freeform_evidence_allows_empty_error_field() -> None:
    raw = "OpenCode completed successfully (exit 0); stdout=status: pass\nerror: none"

    assessment = assess_freeform_evidence(raw)

    assert assessment.failed is False


def test_assess_freeform_evidence_allows_quoted_empty_error_field() -> None:
    raw = 'OpenCode completed successfully (exit 0); stdout=status: "SUCCESS"\nerror: ""'

    assessment = assess_freeform_evidence(raw)

    assert assessment.failed is False
