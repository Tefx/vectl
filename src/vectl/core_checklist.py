"""Pure core checklist inventory and mutation service.

Authority: docs/RFC-deterministic-checklists.md
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Optional, Union

from vectl.core_checklist_inventory import (
    canonicalize_markdown_impl,
    inventory_snapshot_impl as _raw_inventory_snapshot_impl,
    set_item_marker_impl,
)
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
class SelectorDiagnostic:
    """Per-request structured diagnostic for batch checklist mutation.

    ``status`` is ``"error"`` when ``code`` is set.  Successful requests use
    ``matched`` before marker application and then ``changed``/``unchanged`` in
    the final batch result.
    """

    request_index: int
    status: Literal["matched", "changed", "unchanged", "error"]
    message: str
    code: ChecklistErrorCode | None = None
    item_id: str | None = None
    field: SupportedChecklistField | str | None = None
    index: int | None = None
    revision: ChecklistInventoryRevision | None = None
    target_checked: bool | None = None


@dataclass(frozen=True)
class BatchDiagnostics:
    """Diagnostics for a batch mutation execution."""
    total_requested: int
    matched_items: int
    changed_items: int
    selector_diagnostics: tuple[SelectorDiagnostic, ...] = ()


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
    field: SupportedChecklistField | None = None


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


@pre(lambda code: code != "")
def _retry_guidance(code: ChecklistErrorCode) -> RetryGuidance:
    if code == "stale_revision":
        return RetryGuidance(True, "refresh_inventory", "Refresh the checklist inventory and retry against the latest revision.")
    if code == "ambiguous_legacy_match":
        return RetryGuidance(True, "narrow_selector", "Use a more specific keyword or a deterministic selector.")
    if code == "unsupported_field":
        return RetryGuidance(False, "unsupported", "Restrict checklist operations to description and verification.")
    return RetryGuidance(True, "fix_request", "Fix the selector or request shape before retrying.")


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
                _raise_invalid_selector_impl("Legacy keyword selectors must omit checked and toggle state.")
        elif isinstance(selector, (ItemIdSelector, FieldIndexSelector)):
            saw_deterministic = True
            if request.checked is None:
                _raise_invalid_selector_impl("Deterministic selectors require an explicit checked boolean.")
        else:
            _raise_invalid_selector_impl("Unknown checklist selector type.")

    if saw_legacy and saw_deterministic:
        _raise_invalid_selector_impl("Cannot mix legacy keyword toggle selectors with deterministic selectors.")


_validate_request_modes_impl = _validate_request_modes.__wrapped__


@pre(lambda message: message != "")
def _raise_invalid_selector(message: str) -> None:
    _raise_impl(InvalidSelectorError, _error_payload_impl("invalid_selector", message))


_raise_invalid_selector_impl = _raise_invalid_selector.__wrapped__


@pre(lambda request, items: request is not None and items is not None)
def _resolve_request(request: MutationRequest, items: list[ChecklistItem]) -> ChecklistItem:
    selector = request.selector
    if isinstance(selector, ItemIdSelector):
        return _resolve_item_id_selector_impl(selector, items)

    if isinstance(selector, FieldIndexSelector):
        return _resolve_field_index_selector_impl(selector, items)

    if isinstance(selector, LegacyKeywordSelector):
        return _resolve_legacy_keyword_selector_impl(selector, items)

    _raise_impl(
        InvalidSelectorError,
        _error_payload_impl("invalid_selector", "Unknown checklist selector type."),
    )
    raise AssertionError("unreachable")


_resolve_request_impl = _resolve_request.__wrapped__


@pre(lambda selector, items: selector.item_id != "" and items is not None)
def _resolve_item_id_selector(
    selector: ItemIdSelector, items: list[ChecklistItem]
) -> ChecklistItem:
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
    raise AssertionError("unreachable")


_resolve_item_id_selector_impl = _resolve_item_id_selector.__wrapped__


@pre(lambda selector, items: selector is not None and items is not None)
def _resolve_field_index_selector(
    selector: FieldIndexSelector, items: list[ChecklistItem]
) -> ChecklistItem:
    field = _ensure_supported_field_impl(selector.field)
    if selector.index < 0:
        _raise_invalid_index_impl(field, selector.index)
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
    raise AssertionError("unreachable")


_resolve_field_index_selector_impl = _resolve_field_index_selector.__wrapped__


@pre(lambda field, index: field in ("description", "verification") and index < 0)
def _raise_invalid_index(field: SupportedChecklistField, index: int) -> None:
    _raise_impl(
        InvalidSelectorError,
        _error_payload_impl(
            "invalid_selector",
            f"Checklist index must be non-negative: {index}",
            field=field,
            index=index,
        ),
    )


_raise_invalid_index_impl = _raise_invalid_index.__wrapped__


@pre(lambda selector, items: selector is not None and items is not None)
def _resolve_legacy_keyword_selector(
    selector: LegacyKeywordSelector, items: list[ChecklistItem]
) -> ChecklistItem:
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
        _raise_no_legacy_match_impl(selector.keyword)
    _raise_ambiguous_legacy_match_impl(selector.keyword)
    raise AssertionError("unreachable")


_resolve_legacy_keyword_selector_impl = _resolve_legacy_keyword_selector.__wrapped__


@pre(lambda keyword: keyword != "")
def _raise_no_legacy_match(keyword: str) -> None:
    _raise_impl(
        NoLegacyMatch,
        _error_payload_impl(
            "no_legacy_match",
            f"No checklist item matched legacy keyword: {keyword}",
        ),
    )


_raise_no_legacy_match_impl = _raise_no_legacy_match.__wrapped__


@pre(lambda keyword: keyword != "")
def _raise_ambiguous_legacy_match(keyword: str) -> None:
    _raise_impl(
        AmbiguousLegacyMatch,
        _error_payload_impl(
            "ambiguous_legacy_match",
            f"Legacy keyword matched multiple checklist items: {keyword}",
        ),
    )


_raise_ambiguous_legacy_match_impl = _raise_ambiguous_legacy_match.__wrapped__


@pre(lambda step: step is not None)
def _inventory_snapshot(step: Step) -> tuple[ChecklistInventoryRevision, list[ChecklistItem]]:
    revision, rows = _raw_inventory_snapshot_impl(step)
    items = [
        ChecklistItem(
            item_id=row.item_id,
            field=row.field,
            index=row.index,
            text=row.text,
            checked=row.checked,
        )
        for row in rows
    ]
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
    
    >>> from vectl.models import Step
    >>> step = Step(id="s", name="S", description="- [ ] A")
    >>> current_revision, _ = get_inventory.__wrapped__(step)
    >>> request = MutationRequest(FieldIndexSelector("description", 0), True)
    >>> mutate_checklist.__wrapped__(step, current_revision, [request]).step.description
    '- [x] A'
    """
    from vectl.core.checklist_batch import mutate_checklist_batch_impl

    return mutate_checklist_batch_impl(step, revision, requests)
