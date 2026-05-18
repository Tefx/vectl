"""Pure runner-output payload discovery helpers.

These helpers keep tolerant JSON/YAML/envelope parsing out of the orchestration
application shell while preserving the model-visible runner output contract.

>>> extract_structured_review_payload('{"review_outcome":"pass","summary":"ok"}')
{'review_outcome': 'pass', 'summary': 'ok'}
>>> extract_resolution_report_payload('OpenCode completed successfully (exit 0); stdout={"status":"unblocked","summary":"done"}')
{'status': 'unblocked', 'summary': 'done'}
"""

from __future__ import annotations

import json
from collections.abc import Mapping

import yaml
from deal import post, pre


_STRUCTURED_REVIEW_PROTOCOL_KEYS = frozenset(
    {"type", "sessionID", "timestamp", "part", "parts", "message", "messages", "data"}
)

_STRUCTURED_REVIEW_OUTCOMES = frozenset(
    {"pass", "needs_fix", "needs_replan", "operator_required"}
)

_RESOLUTION_REPORT_STATUSES = frozenset(
    {"unblocked", "waiting", "operator_required", "halt"}
)

_RESOLUTION_REPORT_FIELDS = frozenset(
    {"status", "summary", "evidence_refs", "operator_message", "planner_request"}
)


@pre(lambda text: "\x00" not in text)
@post(lambda result: isinstance(result, tuple))
def iter_json_values_from_text(text: str) -> tuple[object, ...]:
    """Return JSON values embedded in free-form runner text.

    >>> iter_json_values_from_text('noise {"a": 1} [2]')
    ({'a': 1}, [2])
    """

    values: list[object] = []
    decoder = json.JSONDecoder()
    index = 0
    while index < len(text):
        char = text[index]
        if char not in "[{":
            index += 1
            continue
        try:
            payload, end = decoder.raw_decode(text[index:])
        except json.JSONDecodeError:
            index += 1
            continue
        values.append(payload)
        index += max(end, 1)
    return tuple(values)


@pre(lambda raw_output: "\x00" not in raw_output)
@post(lambda result: "\x00" not in result)
def strip_runner_markdown_fence(raw_output: str) -> str:
    """Strip a complete Markdown code fence while preserving non-fenced text.

    >>> strip_runner_markdown_fence('```json\\n{"a": 1}\\n```')
    '{"a": 1}'
    >>> strip_runner_markdown_fence('plain')
    'plain'
    """

    stripped = raw_output.strip()
    if not stripped.startswith("```"):
        return stripped
    lines = stripped.splitlines()
    if len(lines) < 3 or not lines[-1].strip().startswith("```"):
        return stripped
    return "\n".join(lines[1:-1]).strip()


@pre(lambda value: isinstance(value, Mapping))
@post(lambda result: result in {True, False})
def looks_like_structured_review_payload(value: Mapping[str, object]) -> bool:
    """Return whether a mapping is a structured-review payload, not a runner envelope.

    >>> looks_like_structured_review_payload({'review_outcome': 'pass', 'summary': 'ok'})
    True
    >>> looks_like_structured_review_payload({'type': 'message', 'review_outcome': 'pass', 'summary': 'ok'})
    False
    """

    if _STRUCTURED_REVIEW_PROTOCOL_KEYS.intersection(value.keys()):
        return False
    if value.get("review_outcome") not in _STRUCTURED_REVIEW_OUTCOMES:
        return False
    return isinstance(value.get("summary"), str)


@pre(lambda value, parse_strings=True: parse_strings in {True, False})
@post(lambda result: result is None or isinstance(result, dict))
def find_structured_review_payload(
    value: object,
    parse_strings: bool = True,
) -> dict[str, object] | None:
    """Find a StructuredReviewResult-shaped payload inside runner values.

    >>> find_structured_review_payload({'data': {'review_outcome': 'needs_fix', 'summary': 'fix'}})
    {'review_outcome': 'needs_fix', 'summary': 'fix'}
    """

    if isinstance(value, dict):
        candidate = dict(value)
        if looks_like_structured_review_payload(candidate):
            return candidate
        for nested in value.values():
            found = find_structured_review_payload(nested, parse_strings=parse_strings)
            if found is not None:
                return found
        return None
    if isinstance(value, list | tuple):
        for nested in value:
            found = find_structured_review_payload(nested, parse_strings=parse_strings)
            if found is not None:
                return found
        return None
    if isinstance(value, str) and parse_strings:
        return extract_structured_review_payload(value)
    return None


@pre(lambda raw_output: "\x00" not in raw_output)
@post(lambda result: result is None or isinstance(result, dict))
def extract_structured_review_payload(raw_output: str) -> dict[str, object] | None:
    """Extract a structured-review payload from JSON/YAML or runner envelopes.

    >>> extract_structured_review_payload('review_outcome: pass\\nsummary: ok')
    {'review_outcome': 'pass', 'summary': 'ok'}
    """

    candidate = strip_runner_markdown_fence(raw_output)
    if not candidate:
        return None

    try:
        parsed = json.loads(candidate)
    except json.JSONDecodeError:
        parsed = None
    if parsed is not None:
        found = find_structured_review_payload(parsed, parse_strings=False)
        if found is not None:
            return found

    for parsed_value in iter_json_values_from_text(candidate):
        found = find_structured_review_payload(parsed_value)
        if found is not None:
            return found

    try:
        parsed_yaml = yaml.safe_load(candidate)
    except yaml.YAMLError:
        return None
    return find_structured_review_payload(parsed_yaml, parse_strings=False)


@pre(lambda value: isinstance(value, Mapping))
@post(lambda result: result in {True, False})
def looks_like_resolution_report_payload(value: Mapping[str, object]) -> bool:
    """Return whether a mapping satisfies the resolver report payload envelope.

    >>> looks_like_resolution_report_payload({'status': 'waiting', 'summary': 'hold'})
    True
    >>> looks_like_resolution_report_payload({'status': 'bad', 'summary': 'hold'})
    False
    """

    keys = set(value.keys())
    if keys - _RESOLUTION_REPORT_FIELDS:
        return False
    if value.get("status") not in _RESOLUTION_REPORT_STATUSES:
        return False
    summary = value.get("summary")
    if not isinstance(summary, str) or not summary.strip():
        return False
    evidence_refs = value.get("evidence_refs", ())
    if not isinstance(evidence_refs, tuple | list):
        return False
    if any(not isinstance(ref, str) for ref in evidence_refs):
        return False
    operator_message = value.get("operator_message")
    if operator_message is not None and not isinstance(operator_message, str):
        return False
    planner_request = value.get("planner_request")
    if planner_request is not None and not isinstance(planner_request, dict):
        return False
    if isinstance(planner_request, dict):
        reason = planner_request.get("reason")
        if not isinstance(reason, str):
            return False
    return True


@pre(lambda value: not isinstance(value, bytes))
@post(lambda result: result is None or isinstance(result, dict))
def find_resolution_report_payload(value: object) -> dict[str, object] | None:
    """Find a ResolutionReport-shaped payload inside nested runner values.

    >>> find_resolution_report_payload({'wrapper': [{'status': 'halt', 'summary': 'stop'}]})
    {'status': 'halt', 'summary': 'stop'}
    """

    if isinstance(value, dict):
        candidate = dict(value)
        if looks_like_resolution_report_payload(candidate):
            return candidate
        for nested in value.values():
            found = find_resolution_report_payload(nested)
            if found is not None:
                return found
        return None
    if isinstance(value, list | tuple):
        for nested in value:
            found = find_resolution_report_payload(nested)
            if found is not None:
                return found
        return None
    if isinstance(value, str):
        for nested in iter_json_values_from_text(value):
            found = find_resolution_report_payload(nested)
            if found is not None:
                return found
    return None


@pre(lambda output_summary: "\x00" not in output_summary)
@post(lambda result: result is None or isinstance(result, dict))
def extract_resolution_report_payload(output_summary: str) -> dict[str, object] | None:
    """Extract a ResolutionReport payload from runtime output summary text.

    >>> extract_resolution_report_payload('{"status":"operator_required","summary":"need human"}')
    {'status': 'operator_required', 'summary': 'need human'}
    """

    stripped = output_summary.strip()
    candidates = [stripped]
    stdout_marker = "; stdout="
    if stdout_marker in stripped:
        candidates.append(stripped.rsplit(stdout_marker, 1)[1].strip())

    for candidate in candidates:
        for payload in iter_json_values_from_text(candidate):
            found = find_resolution_report_payload(payload)
            if found is not None:
                return found
    return None
