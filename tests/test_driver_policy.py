"""Tests for driver policy helpers that were extracted from loop.py.

This test module locks current behavior before any further refactoring
of the policy helpers into a standalone module.

Coverage:
- gate issue parsing behavior unchanged
- preflight risk signal detection unchanged
- failure disposition extraction unchanged
- gate/freeze step classification unchanged

Refs: src/vectl/driver/policy.py, tests/test_driver_loop_hardening.py
"""

from __future__ import annotations

import json

import pytest

from src.vectl.driver.policy import (
    ParsedGateIssue,
    _coerce_gate_issue,
    _detect_preflight_risk_signals,
    _extract_failure_disposition,
    _is_gate_or_freeze_step,
    _parse_gate_issues,
)


# ==============================================================================
# Gate Issue Parsing Tests
# ==============================================================================


class TestParseGateIssues:
    """Lock gate issue parsing behavior across JSON, JSONL, and plain-text formats."""

    def test_parses_json_issues_list_format(self) -> None:
        """JSON object with issues array is the primary gate output format."""
        evidence = json.dumps(
            {
                "issues": [
                    {"severity": "blocker", "summary": "missing branch coverage"},
                    {"severity": "should_fix", "summary": "flaky integration test"},
                    {"severity": "suggestion", "summary": "add inline comments"},
                    {"severity": "tech_debt", "summary": "legacy config"},
                ]
            }
        )
        issues = _parse_gate_issues(evidence)
        assert len(issues) == 4
        severities = {issue.severity for issue in issues}
        assert severities == {"blocker", "should_fix", "suggestion", "tech_debt"}
        summaries = {issue.summary for issue in issues}
        assert "missing branch coverage" in summaries
        assert "flaky integration test" in summaries

    def test_parses_json_issues_direct_list_format(self) -> None:
        """JSON top-level array is also a valid gate output format."""
        evidence = json.dumps(
            [
                {"severity": "blocker", "summary": "missing branch coverage"},
                {"severity": "should_fix", "summary": "flaky integration test"},
            ]
        )
        issues = _parse_gate_issues(evidence)
        assert len(issues) == 2
        severities = {issue.severity for issue in issues}
        assert severities == {"blocker", "should_fix"}

    def test_parses_plaintext_blocker_prefix(self) -> None:
        """Plain-text lines with [blocker] prefix are parsed as blocker issues."""
        evidence = """
        Running tests...
        [blocker] missing branch coverage
        [should_fix] flaky integration test
        [suggestion] add inline comments
        [tech_debt] legacy config
        """
        issues = _parse_gate_issues(evidence)
        severities = {issue.severity for issue in issues}
        assert severities == {"blocker", "should_fix", "suggestion", "tech_debt"}

    def test_parses_plaintext_severity_colon_prefix(self) -> None:
        """Plain-text lines with severity: prefix are parsed correctly."""
        evidence = """
        blocker: missing branch coverage
        should_fix: flaky integration test
        suggestion: add inline comments
        tech_debt: legacy config
        """
        issues = _parse_gate_issues(evidence)
        severities = {issue.severity for issue in issues}
        assert severities == {"blocker", "should_fix", "suggestion", "tech_debt"}

    def test_parses_empty_evidence_returns_empty_list(self) -> None:
        """Empty evidence string returns empty list, not an error."""
        issues = _parse_gate_issues("")
        assert issues == []
        issues = _parse_gate_issues("   ")
        assert issues == []

    def test_parses_invalid_json_falls_back_to_plaintext_line_format(self) -> None:
        """Invalid JSON falls back to plain-text parsing when line starts with severity prefix."""
        # Plain-text parsing requires the line to START with [severity] or severity:
        evidence = "[blocker] this is not json at all but has a marker"
        issues = _parse_gate_issues(evidence)
        assert len(issues) == 1
        assert issues[0].severity == "blocker"
        assert issues[0].summary == "this is not json at all but has a marker"

    def test_parses_json_missing_issues_key_returns_empty(self) -> None:
        """JSON object without issues key returns empty list."""
        evidence = json.dumps({"result": "ok", "message": "all good"})
        issues = _parse_gate_issues(evidence)
        assert issues == []

    def test_issue_severity_normalized_to_lowercase(self) -> None:
        """Issue severity is normalized to lowercase regardless of input case."""
        evidence = json.dumps(
            {
                "issues": [
                    {"severity": "BLOCKER", "summary": "uppercase"},
                    {"severity": "Blocker", "summary": "mixed case"},
                    {"severity": "blocker", "summary": "lowercase"},
                ]
            }
        )
        issues = _parse_gate_issues(evidence)
        assert len(issues) == 3
        severities = {issue.severity for issue in issues}
        assert severities == {"blocker"}

    def test_issue_summary_fallback_chain(self) -> None:
        """Issue summary uses fallback chain: summary -> message -> title -> issue."""
        evidence = json.dumps(
            {
                "issues": [
                    {"severity": "blocker", "message": "from message field"},
                    {"severity": "should_fix", "title": "from title field"},
                    {"severity": "suggestion", "issue": "from issue field"},
                    {"severity": "tech_debt"},  # no summary field
                ]
            }
        )
        issues = _parse_gate_issues(evidence)
        summaries = {issue.summary for issue in issues}
        assert "from message field" in summaries
        assert "from title field" in summaries
        assert "from issue field" in summaries
        # Default fallback when no summary candidate exists
        assert "tech_debt issue" in summaries

    def test_issue_raw_contains_all_fields(self) -> None:
        """Parsed issue raw dict contains stringified original fields."""
        original = {"severity": "blocker", "summary": "missing branch coverage", "extra": "value"}
        evidence = json.dumps({"issues": [original]})
        issues = _parse_gate_issues(evidence)
        assert len(issues) == 1
        assert issues[0].raw["severity"] == "blocker"
        assert issues[0].raw["summary"] == "missing branch coverage"
        assert issues[0].raw["extra"] == "value"

    def test_plaintext_issue_summary_preserves_text_after_prefix(self) -> None:
        """Plain-text parsed issues preserve the summary text after the prefix."""
        evidence = "[blocker] missing branch coverage for the auth module"
        issues = _parse_gate_issues(evidence)
        assert len(issues) == 1
        assert issues[0].severity == "blocker"
        assert issues[0].summary == "missing branch coverage for the auth module"

    def test_plaintext_numeric_prefix_stripped(self) -> None:
        """Plain-text lines with leading numbers are handled correctly."""
        evidence = "1. [blocker] first issue\n2. [should_fix] second issue"
        issues = _parse_gate_issues(evidence)
        assert len(issues) == 2

    def test_plaintext_dash_bullet_stripped(self) -> None:
        """Plain-text lines with dash bullets are handled correctly."""
        evidence = "- [blocker] first issue\n- [should_fix] second issue"
        issues = _parse_gate_issues(evidence)
        assert len(issues) == 2


class TestCoerceGateIssue:
    """Lock behavior of individual issue coercion/normalization."""

    def test_returns_none_for_non_dict_input(self) -> None:
        """Non-dict input returns None instead of raising."""
        assert _coerce_gate_issue("string") is None
        assert _coerce_gate_issue(123) is None
        assert _coerce_gate_issue(None) is None
        assert _coerce_gate_issue([]) is None

    def test_returns_none_for_empty_severity(self) -> None:
        """Issue with empty/missing severity returns None."""
        assert _coerce_gate_issue({}) is None
        assert _coerce_gate_issue({"severity": ""}) is None
        assert _coerce_gate_issue({"severity": "   "}) is None

    def test_returns_none_for_whitespace_only_severity(self) -> None:
        """Issue with whitespace-only severity returns None."""
        assert _coerce_gate_issue({"severity": "  "}) is None


# ==============================================================================
# Preflight Risk Signal Detection Tests
# ==============================================================================


class TestDetectPreflightRiskSignals:
    """Lock preflight risk signal detection based on keyword matching."""

    def test_detects_migration_keyword(self) -> None:
        """'migration' in step_id triggers risk signal."""
        signals = _detect_preflight_risk_signals(
            step_id="core.db-migration",
            description="Migrate database schema",
            verification="Run migration tests",
            refs=["docs/migration.md"],
        )
        assert "migration" in signals

    def test_detects_cas_keyword(self) -> None:
        """'cas' in description triggers risk signal."""
        signals = _detect_preflight_risk_signals(
            step_id="core.impl",
            description="Implement CAS operation for distributed lock",
            verification="Verify atomicity",
            refs=[],
        )
        assert "cas" in signals

    def test_detects_backward_compat_keyword_variants(self) -> None:
        """Both 'backward compat' and 'backward compatibility' are detected."""
        signals1 = _detect_preflight_risk_signals(
            step_id="core.impl",
            description="Ensure backward compat",
            verification="Test",
            refs=[],
        )
        assert "backward compat" in signals1

        signals2 = _detect_preflight_risk_signals(
            step_id="core.impl",
            description="Ensure backward compatibility",
            verification="Test",
            refs=[],
        )
        assert "backward compatibility" in signals2

    def test_detects_concurrency_in_description(self) -> None:
        """'concurrency' in description triggers risk signal."""
        signals = _detect_preflight_risk_signals(
            step_id="core.impl",
            description="Handle concurrent requests with proper concurrency controls",
            verification="Load test",
            refs=[],
        )
        assert "concurrency" in signals

    def test_detects_race_keyword(self) -> None:
        """'race' triggers risk signal."""
        signals = _detect_preflight_risk_signals(
            step_id="core.impl",
            description="Fix race condition in cache",
            verification="Run race detector",
            refs=[],
        )
        assert "race" in signals

    def test_detects_atomic_keyword(self) -> None:
        """'atomic' triggers risk signal."""
        signals = _detect_preflight_risk_signals(
            step_id="core.impl",
            description="Implement atomic swap",
            verification="Test atomicity",
            refs=[],
        )
        assert "atomic" in signals

    def test_returns_empty_list_when_no_risk_signals(self) -> None:
        """Steps without risk keywords return empty list."""
        signals = _detect_preflight_risk_signals(
            step_id="core.impl",
            description="Implement basic feature",
            verification="Run tests",
            refs=[],
        )
        assert signals == []

    def test_risk_signals_case_insensitive(self) -> None:
        """Keyword matching is case-insensitive."""
        signals = _detect_preflight_risk_signals(
            step_id="core.MIGRATION",
            description="MIGRATE the database",
            verification="MIGRATION tests",
            refs=["MIGRATION.md"],
        )
        assert "migration" in signals

    def test_risk_signals_from_refs(self) -> None:
        """Risk signals can be detected from refs list, not just step fields."""
        signals = _detect_preflight_risk_signals(
            step_id="core.impl",
            description="Implement feature",
            verification="Run tests",
            refs=["docs/migration-guide.md", "docs/cas-design.md"],
        )
        assert "migration" in signals
        assert "cas" in signals

    def test_multiple_risk_signals_detected(self) -> None:
        """Multiple different risk keywords can all be detected at once."""
        signals = _detect_preflight_risk_signals(
            step_id="core.migration",
            description="Implement CAS for migration with backward compatibility",
            verification="Test concurrency",
            refs=[],
        )
        assert "migration" in signals
        assert "cas" in signals
        assert "backward compatibility" in signals
        assert "concurrency" in signals

    def test_risk_signals_searches_combined_haystack(self) -> None:
        """Risk signals search across step_id, description, verification, and refs combined."""
        # All keywords should be found when distributed across fields
        signals = _detect_preflight_risk_signals(
            step_id="step",
            description="desc",
            verification="verify",
            refs=["migration", "cas"],  # refs can supply the keywords
        )
        assert "migration" in signals
        assert "cas" in signals


# ==============================================================================
# Failure Disposition Extraction Tests
# ==============================================================================


class TestExtractFailureDisposition:
    """Lock failure disposition extraction from verdict reason strings."""

    def test_extracts_disposition_from_reason(self) -> None:
        """disposition= tag in reason is extracted correctly."""
        reason = "provenance=introduced_now, disposition=downstream_blocker"
        disposition = _extract_failure_disposition(reason)
        assert disposition == "downstream_blocker"

    def test_extracts_disposition_with_semicolon_separator(self) -> None:
        """disposition= followed by semicolon is extracted correctly."""
        reason = "provenance=introduced_now; disposition=downstream_blocker; extra=info"
        disposition = _extract_failure_disposition(reason)
        assert disposition == "downstream_blocker"

    def test_extracts_disposition_with_space_separator(self) -> None:
        """disposition= followed by space is extracted correctly."""
        reason = "provenance=introduced_now disposition=downstream_blocker next_field=value"
        disposition = _extract_failure_disposition(reason)
        assert disposition == "downstream_blocker"

    def test_returns_none_when_no_disposition_marker(self) -> None:
        """Reason without disposition= marker returns None."""
        reason = "provenance=introduced_now, other_field=value"
        disposition = _extract_failure_disposition(reason)
        assert disposition is None

    def test_returns_none_for_empty_reason(self) -> None:
        """Empty reason string returns None."""
        disposition = _extract_failure_disposition("")
        assert disposition is None

    def test_returns_none_for_whitespace_only_reason(self) -> None:
        """Whitespace-only reason returns None."""
        disposition = _extract_failure_disposition("   ")
        assert disposition is None

    def test_returns_none_when_disposition_value_is_empty(self) -> None:
        """disposition= with no value returns None."""
        reason = "provenance=introduced_now, disposition=, other=value"
        disposition = _extract_failure_disposition(reason)
        assert disposition is None

    def test_case_insensitive_marker_search(self) -> None:
        """Marker search is case-insensitive."""
        reason = "DISPOSITION=downstream_blocker"
        disposition = _extract_failure_disposition(reason)
        assert disposition == "downstream_blocker"

    def test_extracts_various_disposition_values(self) -> None:
        """Various disposition values are extracted correctly."""
        test_cases = [
            ("disposition=downstream_blocker", "downstream_blocker"),
            ("disposition=local_fix", "local_fix"),
            ("disposition=ignore", "ignore"),
            ("disposition=defer", "defer"),
        ]
        for reason, expected in test_cases:
            assert _extract_failure_disposition(reason) == expected


# ==============================================================================
# Gate/Freeze Step Classification Tests
# ==============================================================================


class TestIsGateOrFreezeStep:
    """Lock gate/freeze step classification based on name and description markers."""

    def test_identifies_step_with_gate_in_id(self) -> None:
        """.gate in step_id marks it as gate/freeze step."""
        assert (
            _is_gate_or_freeze_step(
                step_id="quality.gate",
                description="Run quality checks",
                verification="Run gate tests",
            )
            is True
        )

    def test_identifies_step_with_gate_in_description(self) -> None:
        """' gate' (space + gate) in description marks it as gate/freeze step."""
        assert (
            _is_gate_or_freeze_step(
                step_id="quality.checks",
                description="Run quality gate checks",
                verification="Verify",
            )
            is True
        )

    def test_identifies_freeze_marker(self) -> None:
        """'freeze' in text marks step as gate/freeze."""
        assert (
            _is_gate_or_freeze_step(
                step_id="release.freeze",
                description="Freeze the release",
                verification="Confirm frozen",
            )
            is True
        )

    def test_identifies_independent_auditor_marker(self) -> None:
        """'independent auditor' marks step as gate/freeze."""
        assert (
            _is_gate_or_freeze_step(
                step_id="security.audit",
                description="Run independent auditor review",
                verification="Audit complete",
            )
            is True
        )

    def test_identifies_adversarial_marker(self) -> None:
        """'adversarial' marks step as gate/freeze."""
        assert (
            _is_gate_or_freeze_step(
                step_id="security.test",
                description="Run adversarial testing",
                verification="Tests passed",
            )
            is True
        )

    def test_identifies_liveness_marker(self) -> None:
        """'liveness' marks step as gate/freeze."""
        assert (
            _is_gate_or_freeze_step(
                step_id="ops.check",
                description="Liveness check",
                verification="Service alive",
            )
            is True
        )

    def test_identifies_smoke_test_marker(self) -> None:
        """'smoke test' marks step as gate/freeze."""
        assert (
            _is_gate_or_freeze_step(
                step_id="test.smoke",
                description="Run smoke tests",
                verification="Smoke tests pass",
            )
            is True
        )

    def test_returns_false_for_regular_impl_step(self) -> None:
        """Regular implementation steps are not gate/freeze."""
        assert (
            _is_gate_or_freeze_step(
                step_id="core.impl",
                description="Implement the feature",
                verification="Run unit tests",
            )
            is False
        )

    def test_returns_false_when_no_markers_present(self) -> None:
        """Steps without any gate/freeze markers return False."""
        assert (
            _is_gate_or_freeze_step(
                step_id="core.impl",
                description="Implement something",
                verification="Verify it works",
            )
            is False
        )

    def test_classification_is_case_insensitive(self) -> None:
        """Marker matching is case-insensitive."""
        assert (
            _is_gate_or_freeze_step(
                step_id="QUALITY.GATE",
                description="RUN QUALITY GATE",
                verification="TESTS",
            )
            is True
        )
        assert (
            _is_gate_or_freeze_step(
                step_id="quality.gate",
                description="Run GATE checks",
                verification="Verify",
            )
            is True
        )

    def test_checks_all_fields_combined(self) -> None:
        """Classification checks step_id, description, and verification combined."""
        # marker in verification only
        assert (
            _is_gate_or_freeze_step(
                step_id="step.id",
                description="description",
                verification="Run the smoke test",
            )
            is True
        )

    def test_impl_suffix_not_mistaken_for_gate(self) -> None:
        """.impl suffix should not be mistaken for gate classification."""
        assert (
            _is_gate_or_freeze_step(
                step_id="core.impl",
                description="Implementation step",
                verification="Verify implementation",
            )
            is False
        )

    def test_gate_marker_requires_dot_prefix_or_space_prefix(self) -> None:
        """Bare 'gate' without prefix is not detected in step_id unless preceded by dot or space."""
        # This is the current behavior: .gate or " gate" works
        assert (
            _is_gate_or_freeze_step(
                step_id="qualityaudit",
                description="Run quality audit",
                verification="Verify",
            )
            is False
        )  # "gate" not found as substring
        assert (
            _is_gate_or_freeze_step(
                step_id="gatekeeper",
                description="desc",
                verification="verify",
            )
            is False
        )  # "gate" as substring not a marker


# ==============================================================================
# ParsedGateIssue Dataclass Tests
# ==============================================================================


class TestParsedGateIssue:
    """Lock ParsedGateIssue dataclass behavior."""

    def test_dataclass_is_frozen(self) -> None:
        """ParsedGateIssue is a frozen dataclass."""
        issue = ParsedGateIssue(severity="blocker", summary="test", raw={})
        with pytest.raises(AttributeError):
            issue.severity = "changed"  # type: ignore

    def test_dataclass_has_expected_fields(self) -> None:
        """ParsedGateIssue has severity, summary, and raw fields."""
        issue = ParsedGateIssue(
            severity="blocker",
            summary="missing coverage",
            raw={"extra": "info"},
        )
        assert issue.severity == "blocker"
        assert issue.summary == "missing coverage"
        assert issue.raw == {"extra": "info"}

    def test_dataclass_is_comparable(self) -> None:
        """Two issues with same values are equal."""
        issue1 = ParsedGateIssue(severity="blocker", summary="test", raw={})
        issue2 = ParsedGateIssue(severity="blocker", summary="test", raw={})
        assert issue1 == issue2
