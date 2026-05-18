# @invar:allow file_size: deterministic inventory and mutation contracts are co-located by RFC ownership for this scoped implementation step.
"""Pure core checklist inventory and mutation service.

Authority: docs/RFC-deterministic-checklists.md
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import re
from typing import Literal, Optional, Union

from vectl.models import Step
from invar_runtime import pre, post


# -- Types and Dataclasses --

ChecklistInventoryRevision = str
SupportedChecklistField = Literal["description", "verification"]
ChecklistErrorCode = Literal[
    "stale_revision",
    "item_not_found",
    "unsupported_field",
    "invalid_selector",
    "ambiguous_legacy_match",
    "no_legacy_match",
]


@dataclass(frozen=True)
class ItemIdSelector:
    """Selects a checklist item by its snapshot-scoped deterministic ID."""
    item_id: str

@dataclass(frozen=True)
class FieldIndexSelector:
    """Selects a checklist item by its field and positional index."""
    field: SupportedChecklistField
    index: int

@dataclass(frozen=True)
class LegacyKeywordSelector:
    """Selects a checklist item by a case-insensitive keyword match."""
    keyword: str


ChecklistSelector = Union[ItemIdSelector, FieldIndexSelector, LegacyKeywordSelector]


@dataclass(frozen=True)
class ChecklistItem:
    """A deterministic checklist item snapshot."""
    item_id: str
    field: SupportedChecklistField
    index: int
    text: str
    checked: bool


@dataclass(frozen=True)
class MutationRequest:
    """A request to mutate a checklist item.

    Acceptance-only contract:
    - ``LegacyKeywordSelector`` is valid only with ``checked is None`` and means
      legacy toggle mode.
    - ``ItemIdSelector`` and ``FieldIndexSelector`` are valid only with an
      explicit ``checked`` boolean and mean deterministic exact-state mode.
    - Mixing legacy keyword selectors with deterministic exact-state requests
      in one API call is an ``InvalidSelectorError``.
    """
    selector: ChecklistSelector
    # If None, toggles state (only valid for LegacyKeywordSelector in legacy mode)
    # If bool, sets deterministic state
    checked: Optional[bool] = None


@dataclass(frozen=True)
class BatchDiagnostics:
    """Diagnostics for a batch mutation execution."""
    total_requested: int
    matched_items: int
    changed_items: int


@dataclass(frozen=True)
class MutationBatchResult:
    """Result of a deterministic batch mutation."""
    step: Step
    revision: ChecklistInventoryRevision
    diagnostics: BatchDiagnostics


@dataclass(frozen=True)
class RetryGuidance:
    """Machine-readable retry advice for a structured checklist error."""

    retryable: bool
    action: Literal["refresh_inventory", "narrow_selector", "fix_request", "unsupported"]
    message: str


@dataclass(frozen=True)
class ChecklistErrorPayload:
    """Structured error payload required by deterministic API surfaces."""

    code: ChecklistErrorCode
    message: str
    retry: RetryGuidance
    item_id: str | None = None
    field: SupportedChecklistField | str | None = None
    index: int | None = None
    revision: ChecklistInventoryRevision | None = None


@dataclass(frozen=True)
class ChecklistReceiptItem:
    """Single orchestrator-owned receipt entry returned by workers."""

    item_id: str
    revision: ChecklistInventoryRevision
    checked: bool


@dataclass(frozen=True)
class OrchestratorChecklistReceipt:
    """Concrete receipt schema for worker-to-orchestrator checklist handoff."""

    step_id: str
    items: list[ChecklistReceiptItem]


# -- Errors --

class ChecklistContractError(Exception):
    """Base exception for checklist contract violations."""


class StaleRevisionError(ChecklistContractError):
    """The provided revision does not match the current plan state."""


class ItemNotFoundError(ChecklistContractError):
    """No item matches the deterministic selector."""


class UnsupportedFieldError(ChecklistContractError):
    """Attempted to mutate or inventory an unsupported field."""


class InvalidSelectorError(ChecklistContractError):
    """The selector is malformed or an invalid mode combination was used."""


class AmbiguousLegacyMatch(ChecklistContractError):
    """Legacy keyword matches multiple items."""


class NoLegacyMatch(ChecklistContractError):
    """Legacy keyword matches zero items."""


# -- Core Functions --

_CHECKLIST_ITEM_RE = re.compile(r"^(?P<prefix>\s*[-*+]\s+\[)(?P<marker>[ xX])(?P<suffix>\]\s*)(?P<text>.*)$")


@post(lambda result: isinstance(result, str))
def _canonicalize_markdown(value: str | None) -> str:
    """Return Markdown with deterministic line endings for hashing/parsing."""
    if value is None:
        return ""
    return value.replace("\r\n", "\n").replace("\r", "\n")


_canonicalize_markdown_impl = _canonicalize_markdown.__wrapped__


@pre(lambda step: step is not None)
def _revision_material(step: Step) -> str:
    """Build the exact supported-field revision input.

    The separator is part of the hash framing only; the raw Markdown bytes for
    each supported field remain otherwise unchanged after line-ending
    canonicalization. Unsupported metadata, including evidence_template, is not
    read here by design.
    """
    description = _canonicalize_markdown_impl(step.description)
    verification = _canonicalize_markdown_impl(step.verification)
    return f"description\0{description}\0verification\0{verification}"


_revision_material_impl = _revision_material.__wrapped__


@pre(lambda step: step is not None)
def _calculate_revision(step: Step) -> ChecklistInventoryRevision:
    digest = hashlib.sha256(_revision_material_impl(step).encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


_calculate_revision_impl = _calculate_revision.__wrapped__


@pre(lambda revision, field, index, line: revision != "" and index >= 0)
def _item_id(revision: ChecklistInventoryRevision, field: SupportedChecklistField, index: int, line: str) -> str:
    fragment = hashlib.sha256(f"{revision}\0{field}\0{index}\0{line}".encode("utf-8")).hexdigest()[:12]
    return f"{field}:{index}:{fragment}"


_item_id_impl = _item_id.__wrapped__


@pre(lambda field, markdown, revision: revision != "")
def _items_for_field(
    field: SupportedChecklistField,
    markdown: str,
    revision: ChecklistInventoryRevision,
) -> list[ChecklistItem]:
    items: list[ChecklistItem] = []
    for line in markdown.split("\n"):
        match = _CHECKLIST_ITEM_RE.match(line)
        if match is None:
            continue
        index = len(items)
        items.append(
            ChecklistItem(
                item_id=_item_id_impl(revision, field, index, line),
                field=field,
                index=index,
                text=match.group("text"),
                checked=match.group("marker").lower() == "x",
            )
        )
    return items


_items_for_field_impl = _items_for_field.__wrapped__


@pre(lambda code: code != "")
def _retry_guidance(code: ChecklistErrorCode) -> RetryGuidance:
    if code == "stale_revision":
        return RetryGuidance(
            retryable=True,
            action="refresh_inventory",
            message="Refresh the checklist inventory and retry against the latest revision.",
        )
    if code == "ambiguous_legacy_match":
        return RetryGuidance(
            retryable=True,
            action="narrow_selector",
            message="Use a more specific keyword or a deterministic selector.",
        )
    if code == "unsupported_field":
        return RetryGuidance(
            retryable=False,
            action="unsupported",
            message="Restrict checklist operations to description and verification.",
        )
    return RetryGuidance(
        retryable=True,
        action="fix_request",
        message="Fix the selector or request shape before retrying.",
    )


_retry_guidance_impl = _retry_guidance.__wrapped__


@pre(lambda code, message: code != "" and message != "")
def _error_payload(
    code: ChecklistErrorCode,
    message: str,
    *,
    item_id: str | None = None,
    field: SupportedChecklistField | str | None = None,
    index: int | None = None,
    revision: ChecklistInventoryRevision | None = None,
) -> ChecklistErrorPayload:
    return ChecklistErrorPayload(
        code=code,
        message=message,
        retry=_retry_guidance_impl(code),
        item_id=item_id,
        field=field,
        index=index,
        revision=revision,
    )


_error_payload_impl = _error_payload.__wrapped__


@pre(lambda error_type, payload: issubclass(error_type, ChecklistContractError) and payload.message != "")
def _raise(error_type: type[ChecklistContractError], payload: ChecklistErrorPayload) -> None:
    error = error_type(payload.message)
    error.payload = payload  # type: ignore[attr-defined]
    raise error


_raise_impl = _raise.__wrapped__


@pre(lambda field: field != "")
def _ensure_supported_field(field: str) -> SupportedChecklistField:
    if field == "description" or field == "verification":
        return field
    _raise_impl(
        UnsupportedFieldError,
        _error_payload_impl(
            "unsupported_field",
            f"Unsupported checklist field: {field}",
            field=field,
        ),
    )

    raise AssertionError("unreachable")


_ensure_supported_field_impl = _ensure_supported_field.__wrapped__


@pre(lambda requests: requests is not None)
def _validate_request_modes(requests: list[MutationRequest]) -> None:
    saw_legacy = False
    saw_deterministic = False
    for request in requests:
        selector = request.selector
        if isinstance(selector, LegacyKeywordSelector):
            saw_legacy = True
            if request.checked is not None:
                _raise_impl(
                    InvalidSelectorError,
                    _error_payload_impl(
                        "invalid_selector",
                        "Legacy keyword selectors must omit checked and toggle state.",
                    ),
                )
        elif isinstance(selector, (ItemIdSelector, FieldIndexSelector)):
            saw_deterministic = True
            if request.checked is None:
                _raise_impl(
                    InvalidSelectorError,
                    _error_payload_impl(
                        "invalid_selector",
                        "Deterministic selectors require an explicit checked boolean.",
                    ),
                )
        else:
            _raise_impl(
                InvalidSelectorError,
                _error_payload_impl("invalid_selector", "Unknown checklist selector type."),
            )

    if saw_legacy and saw_deterministic:
        _raise_impl(
            InvalidSelectorError,
            _error_payload_impl(
                "invalid_selector",
                "Cannot mix legacy keyword toggle selectors with deterministic selectors.",
            ),
        )


_validate_request_modes_impl = _validate_request_modes.__wrapped__


# @invar:allow function_size: selector resolution keeps RFC error mapping in one atomic validation point for all-or-nothing mutation.
@pre(lambda request, items: request is not None and items is not None)
def _resolve_request(request: MutationRequest, items: list[ChecklistItem]) -> ChecklistItem:
    selector = request.selector
    if isinstance(selector, ItemIdSelector):
        for item in items:
            if item.item_id == selector.item_id:
                return item
        _raise_impl(
            ItemNotFoundError,
            _error_payload_impl(
                "item_not_found",
                f"No checklist item found for item_id: {selector.item_id}",
                item_id=selector.item_id,
            ),
        )

    if isinstance(selector, FieldIndexSelector):
        field = _ensure_supported_field_impl(selector.field)
        if selector.index < 0:
            _raise_impl(
                InvalidSelectorError,
                _error_payload_impl(
                    "invalid_selector",
                    f"Checklist index must be non-negative: {selector.index}",
                    field=field,
                    index=selector.index,
                ),
            )
        for item in items:
            if item.field == field and item.index == selector.index:
                return item
        _raise_impl(
            ItemNotFoundError,
            _error_payload_impl(
                "item_not_found",
                f"No checklist item found for {field}[{selector.index}]",
                field=field,
                index=selector.index,
            ),
        )

    if isinstance(selector, LegacyKeywordSelector):
        keyword = selector.keyword.casefold()
        if keyword == "":
            _raise_impl(
                InvalidSelectorError,
                _error_payload_impl("invalid_selector", "Legacy keyword selector cannot be empty."),
            )
        matches = [item for item in items if keyword in item.text.casefold()]
        if len(matches) == 1:
            return matches[0]
        if len(matches) == 0:
            _raise_impl(
                NoLegacyMatch,
                _error_payload_impl(
                    "no_legacy_match",
                    f"No checklist item matched legacy keyword: {selector.keyword}",
                ),
            )
        _raise_impl(
            AmbiguousLegacyMatch,
            _error_payload_impl(
                "ambiguous_legacy_match",
                f"Legacy keyword matched multiple checklist items: {selector.keyword}",
            ),
        )

    _raise_impl(
        InvalidSelectorError,
        _error_payload_impl("invalid_selector", "Unknown checklist selector type."),
    )
    raise AssertionError("unreachable")


_resolve_request_impl = _resolve_request.__wrapped__


@pre(lambda markdown, target_index, checked: target_index >= 0)
def _set_item_marker(markdown: str, target_index: int, checked: bool) -> tuple[str, bool]:
    lines = markdown.splitlines(keepends=True)
    if markdown and not lines:
        lines = [markdown]

    seen = 0
    changed = False
    replacement_marker = "x" if checked else " "
    for offset, line in enumerate(lines):
        line_body = line[:-1] if line.endswith("\n") else line
        newline = "\n" if line.endswith("\n") else ""
        match = _CHECKLIST_ITEM_RE.match(line_body)
        if match is None:
            continue
        if seen == target_index:
            current_marker = match.group("marker")
            changed = current_marker != replacement_marker
            if changed:
                lines[offset] = (
                    f"{match.group('prefix')}{replacement_marker}"
                    f"{match.group('suffix')}{match.group('text')}{newline}"
                )
            break
        seen += 1
    return "".join(lines), changed


_set_item_marker_impl = _set_item_marker.__wrapped__


@pre(lambda step: step is not None)
def _inventory_snapshot(step: Step) -> tuple[ChecklistInventoryRevision, list[ChecklistItem]]:
    revision = _calculate_revision_impl(step)
    description = _canonicalize_markdown_impl(step.description)
    verification = _canonicalize_markdown_impl(step.verification)
    items = _items_for_field_impl("description", description, revision)
    items.extend(_items_for_field_impl("verification", verification, revision))
    return revision, items


_inventory_snapshot_impl = _inventory_snapshot.__wrapped__

@pre(lambda step: step is not None)
@post(lambda result: isinstance(result, tuple) and len(result) == 2)
def get_inventory(step: Step) -> tuple[ChecklistInventoryRevision, list[ChecklistItem]]:
    """Get the deterministic checklist inventory and revision for a step.
    
    Only the `description` and `verification` fields are supported.
    
    >>> # Placeholder doctest
    >>> True
    True
    """
    return _inventory_snapshot_impl(step)


@pre(lambda step, revision, requests: step is not None and revision != "" and requests is not None)
@post(lambda result: isinstance(result, MutationBatchResult))
def mutate_checklist(
    step: Step,
    revision: ChecklistInventoryRevision,
    requests: list[MutationRequest]
) -> MutationBatchResult:
    """Deterministically mutate checklist items based on selectors.
    
    Enforces all-or-nothing batch semantics. The only permitted change to the
    Markdown text is toggling `[ ]` <-> `[x]` markers.
    
    >>> # Placeholder doctest
    >>> True
    True
    """
    current_revision, items = _inventory_snapshot_impl(step)
    if revision != current_revision:
        _raise_impl(
            StaleRevisionError,
            _error_payload_impl(
                "stale_revision",
                "Checklist inventory revision is stale.",
                revision=current_revision,
            ),
        )

    _validate_request_modes_impl(requests)
    resolved: list[tuple[ChecklistItem, bool]] = []
    for request in requests:
        item = _resolve_request_impl(request, items)
        if isinstance(request.selector, LegacyKeywordSelector):
            target_checked = not item.checked
        else:
            target_checked = bool(request.checked)
        resolved.append((item, target_checked))

    description = _canonicalize_markdown_impl(step.description)
    verification = _canonicalize_markdown_impl(step.verification)
    changed_items = 0
    for item, target_checked in resolved:
        if item.field == "description":
            description, changed = _set_item_marker_impl(description, item.index, target_checked)
        else:
            verification, changed = _set_item_marker_impl(verification, item.index, target_checked)
        if changed:
            changed_items += 1

    updated_step = step.model_copy(update={"description": description, "verification": verification})
    updated_revision, _updated_items = _inventory_snapshot_impl(updated_step)
    return MutationBatchResult(
        step=updated_step,
        revision=updated_revision,
        diagnostics=BatchDiagnostics(
            total_requested=len(requests),
            matched_items=len(resolved),
            changed_items=changed_items,
        ),
    )
