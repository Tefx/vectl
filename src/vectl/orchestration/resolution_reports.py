"""
Typed resolution-report helpers and schemas.

Authority: docs/ORCHESTRATION-PLANE-IMPLEMENTATION-DESIGN.md section 3.9
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Literal, TypedDict, cast

from vectl.orchestration.contracts import ResolutionReport


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


_ALLOWED_REPORT_STATUSES: frozenset[str] = frozenset(
    {"unblocked", "waiting", "operator_required", "halt"}
)

_REQUIRED_REPORT_FIELDS: frozenset[str] = frozenset({"status", "summary"})

_OPTIONAL_REPORT_FIELDS: frozenset[str] = frozenset({"evidence_refs", "operator_message"})

_ALLOWED_REPORT_FIELDS: frozenset[str] = _REQUIRED_REPORT_FIELDS | _OPTIONAL_REPORT_FIELDS


def _validate_evidence_refs(value: object) -> None:
    """Validate report evidence_refs field shape.

    Args:
        value: Raw evidence_refs field value.

    Raises:
        ValueError: If value is not a list/tuple[str, ...].
    """
    if not isinstance(value, tuple | list):
        raise ValueError("ResolutionReport payload field 'evidence_refs' must be list/tuple[str]")
    if isinstance(value, str | bytes):
        raise ValueError("ResolutionReport payload field 'evidence_refs' cannot be string")
    for index, ref in enumerate(value):
        if not isinstance(ref, str):
            raise ValueError(
                "ResolutionReport payload field 'evidence_refs' must contain only strings "
                f"(index {index})"
            )


def validate_resolution_report_payload(payload: Mapping[str, object]) -> None:
    """Validate resolver output payload against bounded report requirements.

    Args:
        payload: Raw machine-readable payload from resolver invocation glue.

    Raises:
        ValueError: If payload is structurally invalid for ResolutionReport.
    """
    unknown_fields = set(payload.keys()) - _ALLOWED_REPORT_FIELDS
    if unknown_fields:
        unknown_sorted = ", ".join(sorted(unknown_fields))
        raise ValueError(f"ResolutionReport payload contains unknown fields: {unknown_sorted}")

    missing_required = _REQUIRED_REPORT_FIELDS - set(payload.keys())
    if missing_required:
        missing_sorted = ", ".join(sorted(missing_required))
        raise ValueError(f"ResolutionReport payload missing required fields: {missing_sorted}")

    status = payload["status"]
    if not isinstance(status, str):
        raise ValueError("ResolutionReport payload field 'status' must be string")
    if status not in _ALLOWED_REPORT_STATUSES:
        allowed_sorted = ", ".join(sorted(_ALLOWED_REPORT_STATUSES))
        raise ValueError(f"ResolutionReport payload field 'status' must be one of {allowed_sorted}")

    summary = payload["summary"]
    if not isinstance(summary, str):
        raise ValueError("ResolutionReport payload field 'summary' must be string")
    if not summary.strip():
        raise ValueError("ResolutionReport payload field 'summary' must be non-empty")

    if "evidence_refs" in payload:
        _validate_evidence_refs(payload["evidence_refs"])

    if "operator_message" in payload and payload["operator_message"] is not None:
        if not isinstance(payload["operator_message"], str):
            raise ValueError(
                "ResolutionReport payload field 'operator_message' must be string or null"
            )


def parse_resolution_report_payload(payload: Mapping[str, object]) -> ResolutionReport:
    """Parse machine-readable resolver payload into ResolutionReport.

    Args:
        payload: Raw resolver payload.

    Returns:
        Parsed ResolutionReport contract value.

    Raises:
        ValueError: If payload cannot be represented as ResolutionReport.
    """
    validate_resolution_report_payload(payload)
    status = cast(Literal["unblocked", "waiting", "operator_required", "halt"], payload["status"])
    summary = cast(str, payload["summary"])
    evidence_refs_raw = payload.get("evidence_refs", ())
    operator_message_raw = payload.get("operator_message")

    evidence_refs: tuple[str, ...]
    if isinstance(evidence_refs_raw, tuple):
        evidence_refs = evidence_refs_raw
    elif isinstance(evidence_refs_raw, list):
        evidence_refs = tuple(evidence_refs_raw)
    else:
        evidence_refs = ()

    operator_message: str | None
    if isinstance(operator_message_raw, str):
        operator_message = operator_message_raw
    else:
        operator_message = None

    return ResolutionReport(
        status=status,
        summary=summary,
        evidence_refs=evidence_refs,
        operator_message=operator_message,
    )


__all__ = [
    "ResolutionReportPayload",
    "parse_resolution_report_payload",
    "validate_resolution_report_payload",
]
