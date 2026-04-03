"""Unit tests for resolver invocation glue and typed report parsing.

Authority:
    docs/ORCHESTRATION-PLANE-IMPLEMENTATION-DESIGN.md section 3.5 and 3.9
    docs/ORCHESTRATION-PLANE-RESOLUTION-CONTRACT.md sections 5, 8, 10
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from vectl.orchestration.contracts import (
    CoreSnapshot,
    ResolutionCase,
    ResolutionReport,
    RosterSnapshot,
    RuntimeSnapshot,
)
from vectl.orchestration.judgments import (
    parse_resolution_report_payload,
    validate_resolution_report_payload,
)
from vectl.orchestration.resolver import (
    BoundResolver,
    map_payload_to_report,
    should_preserve_case_reason,
)


def _case(reason: str) -> ResolutionCase:
    return ResolutionCase(
        reason=reason,
        core=CoreSnapshot(
            plan_complete=False,
            claimable_step_ids=(),
            in_progress_step_ids=(),
            blocked_step_ids=("core.blocked",),
            unresolved_reasons=(),
        ),
        roster=RosterSnapshot(
            available_agents=(),
            working_agents=(),
            reusable_sessions=(),
            exhausted_roles=(),
        ),
        runtime=RuntimeSnapshot(
            active_workspaces=(),
            active_executions=(),
            stalled_executions=(),
        ),
    )


def test_validate_resolution_report_payload_accepts_machine_readable_shape() -> None:
    payload: dict[str, object] = {
        "status": "unblocked",
        "summary": "resolved via deterministic repair",
        "evidence_refs": ["log://resolver/1"],
        "operator_message": None,
    }

    validate_resolution_report_payload(payload)


def test_parse_resolution_report_payload_returns_bounded_report() -> None:
    payload: dict[str, object] = {
        "status": "waiting",
        "summary": "awaiting external reconciliation",
        "evidence_refs": ["ticket://123"],
    }

    report = parse_resolution_report_payload(payload)

    assert isinstance(report, ResolutionReport)
    assert report.status == "waiting"
    assert report.summary == "awaiting external reconciliation"
    assert report.evidence_refs == ("ticket://123",)
    assert report.operator_message is None


def test_parse_resolution_report_payload_rejects_unknown_fields() -> None:
    payload: dict[str, object] = {
        "status": "unblocked",
        "summary": "resolved",
        "hidden_backchannel": "forbidden",
    }

    with pytest.raises(ValueError, match="unknown fields"):
        parse_resolution_report_payload(payload)


@dataclass(frozen=True)
class _FakeInvocation:
    payload: dict[str, object]

    def invoke(self, case: ResolutionCase) -> dict[str, object]:
        _ = case
        return self.payload


@dataclass(frozen=True)
class _FailingInvocation:
    def invoke(self, case: ResolutionCase) -> dict[str, object]:
        _ = case
        raise RuntimeError("tool timeout")


def test_bound_resolver_returns_bounded_report_for_unblocked_case() -> None:
    case = _case("Blocked steps require resolution: core.blocked")
    resolver = BoundResolver(
        invocation=_FakeInvocation(
            {
                "status": "unblocked",
                "summary": "resolved after retrying official surface",
                "evidence_refs": ["run://abc"],
            }
        )
    )

    report = resolver.resolve(case)

    assert report.status == "unblocked"
    assert "Blocked steps require resolution" in report.summary
    assert report.evidence_refs == ("run://abc",)


def test_bound_resolver_returns_bounded_report_for_waiting_case() -> None:
    case = _case("Unresolved authoritative state: claim graph conflict")
    resolver = BoundResolver(
        invocation=_FakeInvocation(
            {
                "status": "waiting",
                "summary": "awaiting asynchronous repair",
                "evidence_refs": ["case://waiting"],
                "operator_message": None,
            }
        )
    )

    report = resolver.resolve(case)

    assert report.status == "waiting"
    assert "Unresolved authoritative state" in report.summary
    assert report.evidence_refs == ("case://waiting",)


def test_bound_resolver_returns_operator_required_instead_of_false_certainty() -> None:
    case = _case("Blocked steps require resolution: core.blocked")
    resolver = BoundResolver(invocation=_FailingInvocation())

    report = resolver.resolve(case)

    assert report.status == "operator_required"
    assert "failed" in report.summary.lower()
    assert report.evidence_refs == ("resolver:invocation-failed",)


def test_should_preserve_case_reason_only_for_blocked_or_unresolved() -> None:
    assert should_preserve_case_reason(_case("Blocked steps require resolution: x")) is True
    assert should_preserve_case_reason(_case("Unresolved authoritative state: y")) is True
    assert should_preserve_case_reason(_case("Plan complete and runtime idle")) is False


def test_map_payload_to_report_does_not_force_reason_for_non_blocked_cases() -> None:
    case = _case("Plan complete and runtime idle")
    report = map_payload_to_report(
        case=case,
        payload={
            "status": "waiting",
            "summary": "awaiting operator ack",
            "evidence_refs": ["log://wait"],
        },
    )

    assert report.summary == "awaiting operator ack"
