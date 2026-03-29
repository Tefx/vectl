"""Pure policy helpers extracted from ``loop.py``.

This module pins the initial extraction seam for driver debt reduction.

Moved without semantic change:
- ``ParsedGateIssue``
- ``_detect_preflight_risk_signals()``
- ``_extract_failure_disposition()``
- ``_parse_gate_issues()``
- ``_is_gate_or_freeze_step()``

Dependent pure helpers moved with them:
- ``_coerce_gate_issue()``

Explicit non-moves for this contract:
- request builders and planner/judge dispatch remain in ``loop.py`` because they
  define coordinator-owned IO boundaries rather than standalone policy rules.

Behavior-preservation rule:
- this module preserves existing helper behavior exactly; no policy rewrites are
  bundled into this extraction step.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Final

_PREFLIGHT_RISK_KEYWORDS: Final[tuple[str, ...]] = (
    "migration",
    "cas",
    "backward compat",
    "backward compatibility",
    "concurrency",
    "race",
    "atomic",
)


@dataclass(frozen=True)
class ParsedGateIssue:
    """Normalized gate issue extracted from gate evidence output.

    Source (doc-mirror/spec): docs/JUDGE-AGENT-PROMPT.md ``TYPE: gate`` issue severity table.
    """

    severity: str
    summary: str
    raw: dict[str, Any]


def _detect_preflight_risk_signals(
    *,
    step_id: str,
    description: str,
    verification: str,
    refs: list[str],
) -> list[str]:
    """Detect textual risk signals for preflight judgment routing.

    Source: docs/DRIVER-ARCHITECTURE.md Section 2.10 Judge vs Rules Decision
    Boundary (preflight risk-signal keyword detection).
    """
    haystack = "\n".join([step_id, description, verification, "\n".join(refs)]).lower()
    return [keyword for keyword in _PREFLIGHT_RISK_KEYWORDS if keyword in haystack]


def _extract_failure_disposition(reason: str) -> str | None:
    """Extract ``disposition`` tag from FAILURE verdict reason.

    Source (doc-mirror/spec): docs/JUDGE-AGENT-PROMPT.md ``TYPE: failure`` verdict contract:
    reason MUST include ``provenance=<value>, disposition=<value>``.
    """

    marker = "disposition="
    lowered = reason.lower()
    start = lowered.find(marker)
    if start < 0:
        return None
    raw = lowered[start + len(marker) :]
    token = raw.split(",", 1)[0].split(";", 1)[0].split(" ", 1)[0].strip()
    return token or None


def _coerce_gate_issue(raw_issue: object) -> ParsedGateIssue | None:
    """Normalize one issue from gate evidence payload.

    Source (doc-mirror/spec): docs/JUDGE-AGENT-PROMPT.md ``TYPE: gate`` severity taxonomy.
    """

    if not isinstance(raw_issue, dict):
        return None

    severity_raw = raw_issue.get("severity", "")
    severity = str(severity_raw).strip().lower()
    if not severity:
        return None

    summary_candidates = (
        raw_issue.get("summary"),
        raw_issue.get("message"),
        raw_issue.get("title"),
        raw_issue.get("issue"),
    )
    summary = ""
    for candidate in summary_candidates:
        if candidate is None:
            continue
        summary = str(candidate).strip()
        if summary:
            break
    if not summary:
        summary = f"{severity} issue"

    return ParsedGateIssue(
        severity=severity,
        summary=summary,
        raw={str(key): value for key, value in raw_issue.items()},
    )


def _parse_gate_issues(gate_evidence: str) -> list[ParsedGateIssue]:
    """Parse gate issues from JSON/JSONL/plain-text evidence.

    Source: DRIVER-BLUEPRINT.md Flow 4 (`parse_gate_issues(gate_evidence)`).
    """

    candidates: list[ParsedGateIssue] = []

    stripped = gate_evidence.strip()
    if stripped:
        try:
            payload = json.loads(stripped)
        except json.JSONDecodeError:
            payload = None
        if isinstance(payload, dict):
            raw_issues = payload.get("issues")
            if isinstance(raw_issues, list):
                for issue in raw_issues:
                    parsed = _coerce_gate_issue(issue)
                    if parsed is not None:
                        candidates.append(parsed)
        elif isinstance(payload, list):
            for issue in payload:
                parsed = _coerce_gate_issue(issue)
                if parsed is not None:
                    candidates.append(parsed)

    if not candidates:
        severities = {"blocker", "should_fix", "suggestion", "tech_debt"}
        for line in gate_evidence.splitlines():
            cleaned = line.strip().lstrip("-*0123456789. ").strip()
            if not cleaned:
                continue
            lowered = cleaned.lower()
            for severity in severities:
                prefix_a = f"[{severity}]"
                prefix_b = f"{severity}:"
                if lowered.startswith(prefix_a):
                    summary = cleaned[len(prefix_a) :].strip() or f"{severity} issue"
                    candidates.append(ParsedGateIssue(severity=severity, summary=summary, raw={}))
                    break
                if lowered.startswith(prefix_b):
                    summary = cleaned[len(prefix_b) :].strip() or f"{severity} issue"
                    candidates.append(ParsedGateIssue(severity=severity, summary=summary, raw={}))
                    break

    return candidates


def _is_gate_or_freeze_step(*, step_id: str, description: str, verification: str) -> bool:
    """Return whether a step should use gate/cold-context judgment paths.

    Source (doc-mirror/spec): docs/JUDGE-AGENT-PROMPT.md ``TYPE: gate`` and ``TYPE: cold_context``.
    """

    haystack = "\n".join([step_id, description, verification]).lower()
    markers = (
        ".gate",
        " gate",
        "freeze",
        "independent auditor",
        "adversarial",
        "liveness",
        "smoke test",
    )
    return any(marker in haystack for marker in markers)
