"""
Blocked/unresolved case reasoning.

Authority: docs/ORCHESTRATION-PLANE-INTERFACES.md section 4.4
Authority: docs/ORCHESTRATION-PLANE-IMPLEMENTATION-DESIGN.md section 3.5
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol

from vectl.orchestration.config import DEFAULT_RESOLVER_ROLE_ID
from vectl.orchestration.contracts import ResolutionCase, ResolutionReport
from vectl.orchestration.interfaces import Resolver
from vectl.orchestration.resolution_reports import parse_resolution_report_payload


class ResolverInvocationSurface(Protocol):
    """Invocation glue boundary for blocked/unresolved resolver reasoning.

    Authority:
        docs/ORCHESTRATION-PLANE-IMPLEMENTATION-DESIGN.md sections 3.5 and 3.9
        docs/ORCHESTRATION-PLANE-RESOLUTION-CONTRACT.md sections 2, 4, 5, 8

    Contract Locks:
        - invocation executes from the canonical main worktree only
        - mutation authority is limited to the approved vectl facade
        - claiming remains outside resolver invocation; normal flow owns it
    """

    def invoke(self, case: ResolutionCase, *, role_id: str) -> Mapping[str, object]:
        """Invoke allowed resolver tooling for one blocked/unresolved case.

        Args:
            case: Resolution case from control.
            role_id: Configured resolver role selected from orchestration config.

        Returns:
            Machine-readable payload parsable into ResolutionReport.
        """
        ...


@dataclass(frozen=True)
class BoundResolver:
    """Resolver adapter wiring invocation glue to the pinned report contract.

    Authority:
        docs/ORCHESTRATION-PLANE-IMPLEMENTATION-DESIGN.md sections 3.5 and 5 (Step 6)
        docs/ORCHESTRATION-PLANE-RESOLUTION-CONTRACT.md sections 2, 5, 10

    Note:
        This adapter is a boundary stub in this contract step.
        It intentionally does not implement permanent control authority.
        It also does not create resolver-local claim/mutation authority beyond
        the approved main-worktree vectl facade contract supplied in the case.
    """

    invocation: ResolverInvocationSurface
    default_role_id: str = DEFAULT_RESOLVER_ROLE_ID

    def resolve(self, case: ResolutionCase) -> ResolutionReport:
        """Resolve case via invocation glue and typed report parsing.

        Args:
            case: Blocked/unresolved case.

        Returns:
            ResolutionReport parsed from invocation payload.
        """
        try:
            payload = self.invocation.invoke(case, role_id=self.default_role_id)
        except Exception as exc:
            return ResolutionReport(
                status="operator_required",
                summary=f"Resolver invocation failed: {exc}",
                evidence_refs=("resolver:invocation-failed",),
                operator_message="Review resolver invocation failure and retry.",
            )

        try:
            return map_payload_to_report(case=case, payload=payload)
        except ValueError as exc:
            return ResolutionReport(
                status="operator_required",
                summary=f"Resolver payload validation failed: {exc}",
                evidence_refs=("resolver:payload-invalid",),
                operator_message="Review resolver output formatting and retry.",
            )


def should_preserve_case_reason(case: ResolutionCase) -> bool:
    """Return whether resolver should preserve case reason in report mapping.

    Authority:
        Step scope directive: "preserve resolver reasons over blocked/unresolved
        cases only"
        docs/ORCHESTRATION-PLANE-RESOLUTION-CONTRACT.md sections 3.1 and 3.2

    Args:
        case: Resolution case with control-supplied reason.

    Returns:
        True only for blocked/unresolved reason classes.
    """
    normalized_reason = case.reason.strip().lower()
    return "blocked" in normalized_reason or "unresolved" in normalized_reason


def map_payload_to_report(case: ResolutionCase, payload: Mapping[str, object]) -> ResolutionReport:
    """Map invocation payload into report while honoring reason-preservation policy.

    Authority:
        docs/ORCHESTRATION-PLANE-IMPLEMENTATION-DESIGN.md sections 3.5 and 5 (Step 6)
        docs/ORCHESTRATION-PLANE-RESOLUTION-CONTRACT.md sections 5, 8, 10

    Args:
        case: Original blocked/unresolved case.
        payload: Resolver invocation payload.

    Returns:
        Bounded report for control.
    """
    parsed_report = parse_resolution_report_payload(payload)
    if not should_preserve_case_reason(case):
        return parsed_report

    if parsed_report.summary.startswith(case.reason):
        return parsed_report

    return ResolutionReport(
        status=parsed_report.status,
        summary=f"{case.reason} | {parsed_report.summary}",
        evidence_refs=parsed_report.evidence_refs,
        operator_message=parsed_report.operator_message,
        planner_request=parsed_report.planner_request,
    )


__all__ = [
    "BoundResolver",
    "Resolver",
    "ResolverInvocationSurface",
    "map_payload_to_report",
    "parse_resolution_report_payload",
    "should_preserve_case_reason",
]
