"""
Blocked/unresolved case reasoning.

Authority: docs/ORCHESTRATION-PLANE-INTERFACES.md section 4.4
Authority: docs/ORCHESTRATION-PLANE-IMPLEMENTATION-DESIGN.md section 3.5
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol

from vectl.orchestration import judgments as judgment_support
from vectl.orchestration.contracts import ResolutionCase, ResolutionReport


class ResolverInvocationSurface(Protocol):
    """Invocation glue boundary for blocked/unresolved resolver reasoning.

    Authority:
        docs/ORCHESTRATION-PLANE-IMPLEMENTATION-DESIGN.md sections 3.5 and 3.9
        docs/ORCHESTRATION-PLANE-RESOLUTION-CONTRACT.md sections 2, 4, 5, 8
    """

    def invoke(self, case: ResolutionCase) -> Mapping[str, object]:
        """Invoke allowed resolver tooling for one blocked/unresolved case.

        Args:
            case: Resolution case from control.

        Returns:
            Machine-readable payload parsable into ResolutionReport.
        """
        ...


class Resolver(Protocol):
    """Resolver contract pinned to ResolutionCase -> ResolutionReport.

    Authority:
        docs/ORCHESTRATION-PLANE-INTERFACES.md section 4.4
        docs/ORCHESTRATION-PLANE-RESOLUTION-CONTRACT.md sections 3.2 and 5.1
    """

    def resolve(self, case: ResolutionCase) -> ResolutionReport:
        """Resolve a blocked/unresolved case into bounded report.

        Args:
            case: The blocked/unresolved case snapshot from control.

        Returns:
            Bounded machine-readable resolution report.
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
    """

    invocation: ResolverInvocationSurface

    def resolve(self, case: ResolutionCase) -> ResolutionReport:
        """Resolve case via invocation glue and typed report parsing.

        Args:
            case: Blocked/unresolved case.

        Returns:
            ResolutionReport parsed from invocation payload.
        """
        ...


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
    ...


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
    ...


__all__ = [
    "BoundResolver",
    "Resolver",
    "ResolverInvocationSurface",
    "judgment_support",
    "map_payload_to_report",
    "should_preserve_case_reason",
]
