"""
Typed judgment helpers and schemas.

Authority: docs/ORCHESTRATION-PLANE-IMPLEMENTATION-DESIGN.md section 3.9
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from typing import Protocol, TypedDict

from vectl.orchestration.contracts import ResolutionReport


class LocalJudgmentKind(str, Enum):
    """Typed local judgment categories for deterministic reasoning.

    Authority:
        docs/ORCHESTRATION-PLANE-IMPLEMENTATION-DESIGN.md section 3.9
        docs/ORCHESTRATION-PLANE-RESOLUTION-CONTRACT.md section 7

    This enum is support-only. It does not create a fifth architecture component.
    """

    CLOSED = "closed"
    BLOCKED = "blocked"
    UNRESOLVED = "unresolved"


@dataclass(frozen=True)
class LocalJudgmentContext:
    """Minimal typed context for local deterministic judgment helpers.

    Authority:
        docs/ORCHESTRATION-PLANE-IMPLEMENTATION-DESIGN.md section 3.9
        docs/ORCHESTRATION-PLANE-RESOLUTION-CONTRACT.md sections 3.1 and 7
    """

    reason: str
    blocked_step_ids: tuple[str, ...]
    unresolved_reasons: tuple[str, ...]


class LocalJudgmentRule(Protocol):
    """Contract for deterministic typed/local judgment helpers.

    Authority:
        docs/ORCHESTRATION-PLANE-IMPLEMENTATION-DESIGN.md section 3.9
    """

    def evaluate(self, context: LocalJudgmentContext) -> LocalJudgmentKind:
        """Evaluate local context into a typed local judgment outcome.

        Args:
            context: Deterministic local judgment input.

        Returns:
            A typed local judgment classification.
        """
        ...


class ResolutionReportPayload(TypedDict):
    """Machine-readable payload shape expected from resolver invocation glue.

    Authority:
        docs/ORCHESTRATION-PLANE-RESOLUTION-CONTRACT.md sections 5.1 and 8
        docs/ORCHESTRATION-PLANE-IMPLEMENTATION-DESIGN.md sections 3.5 and 3.9
    """

    status: str
    summary: str
    evidence_refs: tuple[str, ...]
    operator_message: str | None


def validate_resolution_report_payload(payload: Mapping[str, object]) -> None:
    """Validate resolver output payload against bounded report requirements.

    Args:
        payload: Raw machine-readable payload from resolver invocation glue.

    Raises:
        ValueError: If payload is structurally invalid for ResolutionReport.
    """
    ...


def parse_resolution_report_payload(payload: Mapping[str, object]) -> ResolutionReport:
    """Parse machine-readable resolver payload into ResolutionReport.

    Args:
        payload: Raw resolver payload.

    Returns:
        Parsed ResolutionReport contract value.

    Raises:
        ValueError: If payload cannot be represented as ResolutionReport.
    """
    ...


__all__ = [
    "LocalJudgmentContext",
    "LocalJudgmentKind",
    "LocalJudgmentRule",
    "ResolutionReportPayload",
    "parse_resolution_report_payload",
    "validate_resolution_report_payload",
]
