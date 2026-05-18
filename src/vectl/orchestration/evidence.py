"""Completion evidence normalization and freeform failure detection."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

import yaml

from vectl.core_checklist import (
    ChecklistReceiptItem,
    OrchestratorChecklistReceipt,
    SupportedChecklistField,
)

_MAX_PLAN_EVIDENCE_CHARS = 1200
_FAILURE_STATUSES = {"fail", "failed", "failure", "error", "blocked", "red"}
_SUCCESS_STATUSES = {"pass", "passed", "success", "ok", "done"}
_JSON_TYPE_LINE = re.compile(r'^\s*\{\s*"type"\s*:')


@dataclass(frozen=True)
class EvidenceAssessment:
    """Bounded assessment of runner evidence before plan completion."""

    failed: bool
    reason: str | None
    summary: str


class ChecklistReceiptValidationError(ValueError):
    """Raised when a worker checklist receipt violates deterministic schema."""


def assess_freeform_evidence(output_summary: str) -> EvidenceAssessment:
    """Detect explicit failure reports inside freeform runner evidence.

    Freeform coder roles may still emit structured YAML/JSON snippets such as
    ``status: FAIL``.  Exit code 0 alone is not enough to complete a step when
    the evidence itself says verification failed.
    """

    payload = extract_runner_payload(output_summary)
    summary = summarize_plan_evidence(output_summary)
    reason = _structured_failure_reason(payload) or _text_failure_reason(payload)
    return EvidenceAssessment(failed=reason is not None, reason=reason, summary=summary)


def summarize_plan_evidence(output_summary: str) -> str:
    """Return concise completion evidence safe for storing in ``plan.yaml``."""

    prefix, payload = _split_runner_stdout(output_summary)
    payload = payload.strip()
    if _looks_like_opencode_event_stream(payload):
        return _truncate(
            f"{prefix}stdout=<opencode JSON event stream omitted from plan evidence>",
            _MAX_PLAN_EVIDENCE_CHARS,
        )

    if len(output_summary) <= _MAX_PLAN_EVIDENCE_CHARS and not _contains_noisy_artifact(payload):
        return output_summary.strip()

    signal = _extract_signal_lines(payload)
    if not signal:
        signal = payload[:_MAX_PLAN_EVIDENCE_CHARS].strip()
    return _truncate(f"{prefix}stdout_summary={signal}", _MAX_PLAN_EVIDENCE_CHARS)


def extract_runner_payload(output_summary: str) -> str:
    """Extract the runner stdout payload from a prefixed output summary."""

    _prefix, payload = _split_runner_stdout(output_summary)
    return payload.strip()


def parse_checklist_receipt(output_summary: str) -> OrchestratorChecklistReceipt | None:
    """Parse and validate an orchestrator-owned deterministic checklist receipt.

    Authority: docs/RFC-deterministic-checklists.md §6.

    Deterministic receipt entries must include ``step_id``, ``field``,
    ``item_id``, ``checklist_inventory_revision`` (or RFC-compatible alias
    ``revision``), and the desired final ``checked`` state. The parser only
    creates a typed receipt for the orchestrator to apply later; it does not
    mutate plan state and does not do natural-language fuzzy mapping.
    """

    parsed = _parse_payload(extract_runner_payload(output_summary))
    if parsed is None:
        return None
    if not isinstance(parsed, dict):
        raise ChecklistReceiptValidationError("Checklist receipt payload must be a mapping.")

    raw_receipt = parsed.get("checklist_receipt", parsed)
    if raw_receipt in (None, ""):
        return None

    if isinstance(raw_receipt, dict) and "items" in raw_receipt:
        step_id = _require_non_empty_string(raw_receipt, "step_id")
        raw_items = raw_receipt.get("items")
    else:
        step_id = _require_non_empty_string(parsed, "step_id")
        raw_items = raw_receipt

    if not isinstance(raw_items, list):
        raise ChecklistReceiptValidationError("checklist_receipt must be a list of entries.")

    items: list[ChecklistReceiptItem] = []
    for index, raw_item in enumerate(raw_items):
        if not isinstance(raw_item, dict):
            raise ChecklistReceiptValidationError(
                f"checklist_receipt[{index}] must be a mapping."
            )
        item_step_id = raw_item.get("step_id")
        if item_step_id is not None and item_step_id != step_id:
            raise ChecklistReceiptValidationError(
                f"checklist_receipt[{index}] step_id does not match receipt step_id."
            )
        field = _require_supported_field(raw_item, "field", index=index)
        item_id = _require_non_empty_string(raw_item, "item_id", index=index)
        revision = _receipt_revision(raw_item, index=index)
        checked = _require_bool(raw_item, "checked", index=index)
        items.append(
            ChecklistReceiptItem(
                item_id=item_id,
                revision=revision,
                checked=checked,
                field=field,
            )
        )

    return OrchestratorChecklistReceipt(step_id=step_id, items=items)


def _split_runner_stdout(output_summary: str) -> tuple[str, str]:
    marker = "stdout="
    if marker not in output_summary:
        return "", output_summary
    before, _sep, after = output_summary.partition(marker)
    prefix = before.strip().rstrip(";").strip()
    if prefix:
        prefix = f"{prefix}; "
    return prefix, after


def _require_non_empty_string(
    mapping: dict[str, Any], key: str, *, index: int | None = None
) -> str:
    value = mapping.get(key)
    if not isinstance(value, str) or not value.strip():
        location = f"checklist_receipt[{index}]." if index is not None else ""
        raise ChecklistReceiptValidationError(f"Missing required {location}{key}.")
    return value.strip()


def _require_bool(mapping: dict[str, Any], key: str, *, index: int) -> bool:
    value = mapping.get(key)
    if not isinstance(value, bool):
        raise ChecklistReceiptValidationError(
            f"Missing required checklist_receipt[{index}].{key} boolean."
        )
    return value


def _require_supported_field(
    mapping: dict[str, Any], key: str, *, index: int
) -> SupportedChecklistField:
    value = _require_non_empty_string(mapping, key, index=index)
    if value not in ("description", "verification"):
        raise ChecklistReceiptValidationError(
            f"Unsupported checklist_receipt[{index}].field: {value!r}."
        )
    return value


def _receipt_revision(mapping: dict[str, Any], *, index: int) -> str:
    value = mapping.get("checklist_inventory_revision", mapping.get("revision"))
    if not isinstance(value, str) or not value.strip():
        raise ChecklistReceiptValidationError(
            "Missing required checklist_receipt"
            f"[{index}].checklist_inventory_revision."
        )
    return value.strip()


def _structured_failure_reason(payload: str) -> str | None:
    parsed = _parse_payload(payload)
    if not isinstance(parsed, dict):
        return None

    status = _normalized_string(
        parsed.get("status")
        or parsed.get("result")
        or parsed.get("outcome")
        or parsed.get("review_outcome")
    )
    if status in _FAILURE_STATUSES:
        return f"reported status={status!r}"
    if status and status not in _SUCCESS_STATUSES and _has_error_value(parsed.get("error")):
        return f"reported status={status!r} with error"
    if _has_error_value(parsed.get("error")) and status not in _SUCCESS_STATUSES:
        return "reported error field"
    return None


def _text_failure_reason(payload: str) -> str | None:
    patterns = (
        (
            r"(?im)^\s*status\s*:\s*['\"]?"
            r"(fail|failed|failure|error|blocked)\b",
            "reported failing status",
        ),
        (
            r"(?im)^\s*(static|tests?|pytest|ruff|mypy|gate|review)\s*:\s*['\"]?"
            r"(fail|failed|blocked|error)\b",
            "reported failed verification gate",
        ),
        (r"(?i)\bpre-flight static verification failed\b", "pre-flight static verification failed"),
        (r"(?i)\bverification (failed|blocked)\b", "verification failed or blocked"),
    )
    for pattern, reason in patterns:
        if re.search(pattern, payload):
            return reason
    error_line = re.search(r"(?im)^\s*error\s*:\s*(.*?)\s*$", payload)
    if error_line and _has_error_value(error_line.group(1)):
        return "reported error field"
    return None


def _parse_payload(payload: str) -> Any:
    candidate = _strip_markdown_fence(payload.strip())
    if not candidate or _looks_like_opencode_event_stream(candidate):
        return None
    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        pass
    try:
        return yaml.safe_load(candidate)
    except yaml.YAMLError:
        return None


def _strip_markdown_fence(raw: str) -> str:
    if not raw.startswith("```"):
        return raw
    lines = raw.splitlines()
    if len(lines) < 3 or not lines[-1].strip().startswith("```"):
        return raw
    return "\n".join(lines[1:-1]).strip()


def _normalized_string(value: object) -> str:
    if not isinstance(value, str):
        return ""
    return value.strip().strip("'\"").lower()


def _has_error_value(value: object) -> bool:
    normalized = _normalized_string(value)
    return normalized not in {"", "none", "null", "false", "no", "n/a"}


def _looks_like_opencode_event_stream(payload: str) -> bool:
    stripped = payload.lstrip()
    lines = [line for line in stripped.splitlines() if line.strip()]
    return bool(lines and _JSON_TYPE_LINE.match(lines[0]))


def _contains_noisy_artifact(payload: str) -> bool:
    return any(token in payload for token in ('{"type":"tool_use"', "<content>", "<path>"))


def _extract_signal_lines(payload: str) -> str:
    signal_patterns = re.compile(
        r"(?i)^(status|summary|error|result|checkpoint|target|static|invar|pytest|ruff|mypy|command|verification|evidence)\b"
    )
    lines: list[str] = []
    for raw_line in payload.splitlines():
        line = raw_line.strip()
        if not line or _JSON_TYPE_LINE.match(line):
            continue
        if signal_patterns.search(line):
            lines.append(line)
        if len(lines) >= 20:
            break
    if not lines:
        lines = [
            line.strip()
            for line in payload.splitlines()
            if line.strip() and not _JSON_TYPE_LINE.match(line)
        ][:8]
    return "\n".join(lines)


def _truncate(value: str, limit: int) -> str:
    value = value.strip()
    if len(value) <= limit:
        return value
    return value[: limit - 1].rstrip() + "…"
