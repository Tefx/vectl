"""Unit tests for resolver allowlist invocation gateway.

Authority:
    docs/ORCHESTRATION-PLANE-RESOLUTION-CONTRACT.md sections 4, 5, 8
    docs/ORCHESTRATION-PLANE-CLI-CONFIG-OBSERVABILITY-DESIGN.md section 8.6
"""

from __future__ import annotations

from vectl.orchestration.contracts import (
    CoreSnapshot,
    ResolutionCase,
    ResolutionReport,
    RosterSnapshot,
    RuntimeSnapshot,
)
from vectl.orchestration.resolver_gateway import (
    AuditedResolverGateway,
    AuthorizationError,
    ResolverToolCall,
    authorize_and_invoke,
)


def _case() -> ResolutionCase:
    return ResolutionCase(
        reason="Blocked steps require resolution: core.blocked",
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


def test_authorize_and_invoke_requires_explicit_gateway() -> None:
    case = _case()

    try:
        authorize_and_invoke(case=case, allowed_tool_families=("core",), gateway=None)
    except NotImplementedError as exc:
        assert "default gateway instance" in str(exc)
    else:
        raise AssertionError("Expected NotImplementedError when gateway is omitted")


def test_allowed_call_passes_and_records_audit_event() -> None:
    case = _case()
    seen_invocation_refs: list[str] = []

    def _invoker(
        request_case: ResolutionCase,
        calls: tuple[ResolverToolCall, ...],
        invocation_ref: str,
    ) -> ResolutionReport:
        assert request_case is case
        assert calls == (
            ResolverToolCall(family="orchestration", name="read_state", surface="read"),
        )
        seen_invocation_refs.append(invocation_ref)
        return ResolutionReport(
            status="unblocked",
            summary="resolver completed through allowed read surface",
            evidence_refs=("case://1",),
        )

    gateway = AuditedResolverGateway(
        planned_tool_calls=(
            ResolverToolCall(family="orchestration", name="read_state", surface="read"),
        ),
        resolver_invoker=_invoker,
        invocation_ref_factory=lambda: "inv-1",
    )

    result = authorize_and_invoke(
        case=case, allowed_tool_families=("orchestration",), gateway=gateway
    )

    assert result.outcome == "success"
    assert result.invocation_ref == "inv-1"
    assert result.report is not None
    assert result.report.status == "unblocked"
    assert seen_invocation_refs == ["inv-1"]
    assert len(result.tool_call_audit) == 1
    assert result.tool_call_audit[0].outcome == "allowed"


def test_disallowed_family_is_denied_with_machine_readable_reason() -> None:
    case = _case()

    gateway = AuditedResolverGateway(
        planned_tool_calls=(ResolverToolCall(family="core", name="status", surface="read"),),
        resolver_invoker=lambda _case, _calls, _inv_ref: ResolutionReport(
            status="waiting",
            summary="should not run",
        ),
        invocation_ref_factory=lambda: "inv-denied-family",
    )

    try:
        authorize_and_invoke(case=case, allowed_tool_families=("orchestration",), gateway=gateway)
    except AuthorizationError as exc:
        assert exc.denied_families == ("core",)
        assert len(exc.denied) == 1
        assert exc.denied[0].reason_code == "family_not_allowed"
        assert len(exc.audit_events) == 1
        assert exc.audit_events[0].outcome == "denied"
    else:
        raise AssertionError("Expected AuthorizationError for disallowed family")


def test_unknown_tool_name_is_denied() -> None:
    case = _case()

    gateway = AuditedResolverGateway(
        planned_tool_calls=(ResolverToolCall(family="core", name="nope", surface="read"),),
        resolver_invoker=lambda _case, _calls, _inv_ref: ResolutionReport(
            status="waiting",
            summary="should not run",
        ),
        invocation_ref_factory=lambda: "inv-denied-tool",
    )

    try:
        authorize_and_invoke(case=case, allowed_tool_families=("core",), gateway=gateway)
    except AuthorizationError as exc:
        assert len(exc.denied) == 1
        assert exc.denied[0].reason_code == "unknown_tool"
    else:
        raise AssertionError("Expected AuthorizationError for unknown tool")


def test_write_surface_request_for_read_only_tool_is_denied() -> None:
    case = _case()

    gateway = AuditedResolverGateway(
        planned_tool_calls=(
            ResolverToolCall(family="orchestration", name="read_case", surface="write"),
        ),
        resolver_invoker=lambda _case, _calls, _inv_ref: ResolutionReport(
            status="waiting",
            summary="should not run",
        ),
        invocation_ref_factory=lambda: "inv-denied-surface",
    )

    try:
        authorize_and_invoke(case=case, allowed_tool_families=("orchestration",), gateway=gateway)
    except AuthorizationError as exc:
        assert len(exc.denied) == 1
        assert exc.denied[0].reason_code == "surface_mismatch"
    else:
        raise AssertionError("Expected AuthorizationError for surface mismatch")
