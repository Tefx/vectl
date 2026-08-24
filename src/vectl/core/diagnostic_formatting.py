"""Pure bounded formatting for validation and review diagnostics."""

from __future__ import annotations

import types
import unicodedata
from collections.abc import Mapping
from typing import Literal, TypeAlias, Union, get_args, get_origin

from invar_runtime import Contract, post, pre
from pydantic import BaseModel
from pydantic_core import ErrorType

from vectl.models import Plan, PlanValidationIssue

ReviewScope: TypeAlias = Literal["global", "phase", "active_dependency_closure"]
FinalValidationSource: TypeAlias = Literal["structural", "canonical_model"]
ReviewIssueCategory: TypeAlias = Literal[
    "current_error", "current_warning", "historical_warning"
]
_REVIEW_ISSUE_ORDER: tuple[ReviewIssueCategory, ...] = (
    "current_error",
    "current_warning",
    "historical_warning",
)
_MAX_BOUNDED_REVIEW_ISSUE_CODEPOINTS = 512
_MAX_BOUNDED_REVIEW_SCALAR_CODEPOINTS = 160
_MAX_BOUNDED_REVIEW_REF_VALUE_CODEPOINTS = _MAX_BOUNDED_REVIEW_SCALAR_CODEPOINTS
_MAX_BOUNDED_REVIEW_OUTPUT_CODEPOINTS = 65_536
_MAX_FINAL_VALIDATION_ISSUE_CODEPOINTS = 512
_BUILTIN_PYDANTIC_ERROR_TYPES = frozenset(
    error_type
    for error_type in get_args(ErrorType)
    if isinstance(error_type, str)
)


@pre(
    Contract(
        lambda value, limit=1: isinstance(value, str) and limit > 0,
        "bounded diagnostic text uses a positive codepoint limit",
    )
)
@post(
    Contract(
        lambda result: isinstance(result, tuple)
        and len(result) == 2
        and isinstance(result[0], str)
        and isinstance(result[1], bool),
        "bounded diagnostic text returns content and a truncation flag",
    )
)
def bounded_review_value(value: str, limit: int) -> tuple[str, bool]:
    """Escape controls/Markdown table separators, then clip by codepoint count.

    The returned string has at most ``limit`` codepoints. Newlines cannot create
    report rows or forge metadata.

    >>> bounded_review_value("a\\nb|c", 20)
    ('a\\\\nb\\\\|c', False)
    >>> bounded_review_value("abcdef", 4)
    ('abc…', True)
    """
    if limit < 1:
        raise ValueError("bounded diagnostic value limit must be positive")

    escaped_parts: list[str] = []
    for character in value:
        if character == "\n":
            escaped_parts.append("\\n")
        elif character == "\r":
            escaped_parts.append("\\r")
        elif character == "\t":
            escaped_parts.append("\\t")
        elif character == "|":
            escaped_parts.append("\\|")
        elif (
            unicodedata.category(character).startswith("C")
            or unicodedata.category(character) in {"Zl", "Zp"}
        ):
            escaped_parts.append(
                f"\\u{ord(character):04x}"
                if ord(character) <= 0xFFFF
                else f"\\U{ord(character):08x}"
            )
        else:
            escaped_parts.append(character)
    escaped = "".join(escaped_parts)
    if len(escaped) <= limit:
        return escaped, False
    if limit == 1:
        return "…", True
    return escaped[: limit - 1] + "…", True


@pre(
    Contract(
        lambda issues, max_issues=None, source="structural": all(
            isinstance(issue, PlanValidationIssue) for issue in issues
        )
        and (
            max_issues is None
            or (
                isinstance(max_issues, int)
                and not isinstance(max_issues, bool)
                and max_issues > 0
            )
        )
        and source in {"structural", "canonical_model"},
        "diagnostic issues use the validation-issue type",
    )
)
@post(Contract(lambda result: isinstance(result, str), "diagnostic output is text"))
def format_final_validation_failure(
    issues: list[PlanValidationIssue],
    max_issues: int | None,
    source: FinalValidationSource = "structural",
) -> str:
    """Render blocking candidate issues with bounded, control-safe values.

    ``max_issues=None`` retains every issue row. Each row remains capped at 512
    codepoints for both structural and canonical sources.

    >>> print(format_final_validation_failure([PlanValidationIssue("bad\\nforged")], None))
    **Error:** stage=final_validation
    validation_source: structural
    blocking_issue_count: 1
    rendered_issue_count: 1
    issues_truncated: false
    issue_value_codepoint_limit: 512
    issue_values_truncated: false
    blocking_issues:
    - ERROR: bad\\nforged
    """
    if max_issues is not None and (
        isinstance(max_issues, bool) or not isinstance(max_issues, int) or max_issues < 1
    ):
        raise ValueError("max_issues must be a positive integer")
    if source not in {"structural", "canonical_model"}:
        raise ValueError(f"Unknown final-validation source: {source}")

    blocking = [issue for issue in issues if not issue.is_warning]
    rendered = blocking if max_issues is None else blocking[:max_issues]
    rendered_messages: list[str] = []
    issue_values_truncated = False
    for issue in rendered:
        message, value_truncated = bounded_review_value(
            issue.message, _MAX_FINAL_VALIDATION_ISSUE_CODEPOINTS
        )
        rendered_messages.append(message)
        issue_values_truncated = issue_values_truncated or value_truncated

    lines = [
        "**Error:** stage=final_validation",
        f"validation_source: {source}",
        f"blocking_issue_count: {len(blocking)}",
        f"rendered_issue_count: {len(rendered_messages)}",
        f"issues_truncated: {str(len(rendered) < len(blocking)).lower()}",
        f"issue_value_codepoint_limit: {_MAX_FINAL_VALIDATION_ISSUE_CODEPOINTS}",
        f"issue_values_truncated: {str(issue_values_truncated).lower()}",
        "blocking_issues:",
    ]
    lines.extend(f"- ERROR: {message}" for message in rendered_messages)
    return "\n".join(lines)


@pre(
    Contract(
        lambda source, max_issues=None: source in {"structural", "canonical_model"}
        and (
            max_issues is None
            or (
                isinstance(max_issues, int)
                and not isinstance(max_issues, bool)
                and max_issues > 0
            )
        ),
        "unexpected final validation uses a known source and optional positive bound",
    )
)
@post(
    Contract(
        lambda result: isinstance(result, str)
        and "[unexpected_validation_error]" in result,
        "unexpected final validation returns one fixed diagnostic",
    )
)
def format_unexpected_final_validation_failure(
    source: FinalValidationSource,
    max_issues: int | None,
) -> str:
    """Render one fixed failure without candidate or exception text.

    >>> failure = format_unexpected_final_validation_failure("structural", 1)
    >>> "structural validation could not complete" in failure
    True
    >>> "validation_source: structural" in failure
    True
    """
    label = "canonical validation" if source == "canonical_model" else "structural validation"
    return format_final_validation_failure(
        [PlanValidationIssue(f"{label} could not complete [unexpected_validation_error]")],
        max_issues=max_issues,
        source=source,
    )


@pre(
    Contract(
        lambda errors: all(
            isinstance(error, Mapping)
            and isinstance(error.get("loc"), (list, tuple))
            and isinstance(error.get("type"), str)
            for error in errors
        ),
        "canonical validation errors expose deterministic location/type fields",
    )
)
@post(
    Contract(
        lambda result: isinstance(result, list)
        and all(isinstance(issue, PlanValidationIssue) for issue in result),
        "canonical validation diagnostics are validation issues",
    )
)
def normalize_model_validation_issues(
    errors: list[Mapping[str, object]],
) -> list[PlanValidationIssue]:
    """Normalize Pydantic errors without messages, inputs, contexts, or URLs.

    Location components are retained only when the current schema node admits
    that exact model field or list index. Dynamic components become ``<field>``.
    Error types are retained only when Pydantic Core declares them as built-ins;
    custom values become ``validation_error``.

    >>> issues = normalize_model_validation_issues([{
    ...     "loc": ("phases", 0, "status"),
    ...     "msg": "raw candidate must not appear",
    ...     "type": "enum",
    ... }])
    >>> issues[0].message
    'canonical_model.phases[0].status: canonical model constraint violated [enum]'
    >>> normalize_model_validation_issues([{
    ...     "loc": ("phases", 0, "RAW_LOCATION_SECRET"),
    ...     "type": "RAW_TYPE_SECRET",
    ... }])[0].message
    'canonical_model.phases[0].<field>: canonical model constraint violated [validation_error]'
    """

    def unwrapped_schema(annotation: object) -> object:
        origin = get_origin(annotation)
        if origin in {types.UnionType, Union}:
            alternatives = tuple(
                alternative
                for alternative in get_args(annotation)
                if alternative is not type(None)
            )
            if len(alternatives) == 1:
                return unwrapped_schema(alternatives[0])
        return annotation

    issues: list[PlanValidationIssue] = []
    for error in errors:
        location = "canonical_model"
        schema: object = Plan
        for component in error["loc"]:  # type: ignore[union-attr]
            schema = unwrapped_schema(schema)
            if (
                isinstance(component, str)
                and isinstance(schema, type)
                and issubclass(schema, BaseModel)
                and component in schema.model_fields
            ):
                location += f".{component}"
                schema = schema.model_fields[component].annotation
                continue
            if (
                isinstance(component, int)
                and not isinstance(component, bool)
                and component >= 0
                and get_origin(schema) is list
            ):
                location += f"[{component}]"
                item_types = get_args(schema)
                schema = item_types[0] if len(item_types) == 1 else None
                continue
            location += ".<field>"
            schema = None

        error_type = error["type"]
        assert isinstance(error_type, str)
        safe_error_type = (
            error_type
            if error_type in _BUILTIN_PYDANTIC_ERROR_TYPES
            else "validation_error"
        )
        issues.append(
            PlanValidationIssue(
                f"{location}: canonical model constraint violated [{safe_error_type}]"
            )
        )
    return issues


@pre(
    Contract(
        lambda ref_index, max_items=1: isinstance(ref_index, dict)
        and all(
            isinstance(ref, str)
            and isinstance(step_ids, list)
            and all(isinstance(step_id, str) for step_id in step_ids)
            for ref, step_ids in ref_index.items()
        )
        and max_items > 0,
        "bounded ref detail uses text refs/step IDs and a positive item bound",
    )
)
@post(
    Contract(
        lambda result: isinstance(result, list)
        and len(result) >= 8
        and all(isinstance(line, str) for line in result),
        "bounded ref detail includes metadata and Markdown lines",
    )
)
def format_bounded_ref_detail(
    ref_index: dict[str, list[str]], max_items: int
) -> list[str]:
    """Bound L4 ref rows and step links with one global link budget.

    >>> lines = format_bounded_ref_detail({"b": ["s1", "s2"], "a": ["x"]}, 1)
    >>> lines[0], lines[3]
    ('total_ref_count: 2', 'total_ref_link_count: 3')
    >>> lines[4], lines[7]
    ('rendered_ref_link_count: 1', 'ref_detail_truncated: true')
    >>> lines[8]
    '  a ← x'
    """
    if max_items < 1:
        raise ValueError("bounded ref detail max_items must be positive")
    ref_paths = sorted(ref_index)
    rendered_ref_paths = ref_paths[:max_items]
    total_ref_link_count = sum(len(step_ids) for step_ids in ref_index.values())
    remaining_link_budget = max_items
    rendered_ref_link_count = 0
    ref_values_truncated = False
    rendered_ref_lines: list[str] = []
    for ref_path in rendered_ref_paths:
        bounded_ref_path, path_truncated = bounded_review_value(
            ref_path, _MAX_BOUNDED_REVIEW_REF_VALUE_CODEPOINTS
        )
        rendered_step_ids = ref_index[ref_path][:remaining_link_budget]
        bounded_step_ids: list[str] = []
        for step_id in rendered_step_ids:
            bounded_step_id, step_id_truncated = bounded_review_value(
                step_id, _MAX_BOUNDED_REVIEW_REF_VALUE_CODEPOINTS
            )
            bounded_step_ids.append(bounded_step_id)
            ref_values_truncated = ref_values_truncated or step_id_truncated
        rendered_count = len(rendered_step_ids)
        remaining_link_budget -= rendered_count
        rendered_ref_link_count += rendered_count
        ref_values_truncated = ref_values_truncated or path_truncated
        rendered_ref_lines.append(
            f"  {bounded_ref_path} ← "
            + (", ".join(bounded_step_ids) if bounded_step_ids else "<links omitted>")
        )

    refs_truncated = len(rendered_ref_paths) < len(ref_paths)
    ref_links_truncated = rendered_ref_link_count < total_ref_link_count
    ref_detail_truncated = refs_truncated or ref_links_truncated or ref_values_truncated
    lines = [
        f"total_ref_count: {len(ref_paths)}",
        f"rendered_ref_count: {len(rendered_ref_paths)}",
        f"refs_truncated: {str(refs_truncated).lower()}",
        f"total_ref_link_count: {total_ref_link_count}",
        f"rendered_ref_link_count: {rendered_ref_link_count}",
        f"ref_links_truncated: {str(ref_links_truncated).lower()}",
        f"ref_values_truncated: {str(ref_values_truncated).lower()}",
        f"ref_detail_truncated: {str(ref_detail_truncated).lower()}",
    ]
    if rendered_ref_lines:
        lines.extend(rendered_ref_lines)
    else:
        lines.append("*No refs defined in any steps.*")
    return lines


@pre(
    Contract(
        lambda issue: isinstance(issue, PlanValidationIssue),
        "a validation issue is required",
    )
)
@post(
    Contract(
        lambda result: result in {"current_error", "current_warning", "historical_warning"},
        "issue category is stable",
    )
)
def review_issue_category(issue: PlanValidationIssue) -> ReviewIssueCategory:
    """Classify current errors, current warnings, and historical warnings.

    >>> review_issue_category(PlanValidationIssue("old", is_warning=True, is_historical=True))
    'historical_warning'
    """
    if issue.is_warning and issue.is_historical:
        return "historical_warning"
    if issue.is_warning:
        return "current_warning"
    return "current_error"


@pre(
    Contract(
        lambda issues: all(isinstance(issue, PlanValidationIssue) for issue in issues),
        "diagnostic issues use the validation-issue type",
    )
)
@post(Contract(lambda result: isinstance(result, list), "ordered diagnostics are a list"))
def order_review_issues(issues: list[PlanValidationIssue]) -> list[PlanValidationIssue]:
    """Order current errors, current warnings, then historical warnings.

    >>> ordered = order_review_issues([
    ...     PlanValidationIssue("history", is_warning=True, is_historical=True),
    ...     PlanValidationIssue("current"),
    ... ])
    >>> [issue.message for issue in ordered]
    ['current', 'history']
    """
    return [
        issue
        for category in _REVIEW_ISSUE_ORDER
        for issue in issues
        if review_issue_category(issue) == category
    ]
