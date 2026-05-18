"""Batch checklist mutation implementation.

Authority: docs/RFC-deterministic-checklists.md
"""

from __future__ import annotations

from invar_runtime import post, pre

from vectl import core_checklist as c
from vectl.core_checklist import (
    ChecklistContractError,
    ChecklistErrorPayload,
    FieldIndexSelector,
    ItemIdSelector,
    LegacyKeywordSelector,
    SelectorDiagnostic,
    _resolve_request_impl,
)
from vectl.core_checklist_inventory import canonicalize_markdown_impl, set_item_marker_impl
from vectl.models import Step


@pre(
    lambda error_type, payload, diagnostics: issubclass(error_type, Exception)
    and payload.message != ""
    and diagnostics is not None
)
def _raise_batch_error(error_type: type[Exception], payload: object, diagnostics: tuple[object, ...]) -> None:
    """Raise a checklist error with batch diagnostics attached.

    >>> from vectl.core_checklist import InvalidSelectorError, SelectorDiagnostic, _error_payload_impl
    >>> try:
    ...     _raise_batch_error_impl(InvalidSelectorError, _error_payload_impl('invalid_selector', 'bad'), (SelectorDiagnostic(0, 'error', 'bad'),))
    ... except InvalidSelectorError as exc:
    ...     len(exc.diagnostics)
    1
    """
    error = error_type(payload.message)  # type: ignore[attr-defined]
    error.payload = payload  # type: ignore[attr-defined]
    error.diagnostics = diagnostics  # type: ignore[attr-defined]
    raise error


_raise_batch_error_impl = _raise_batch_error.__wrapped__


@pre(lambda requests, current_revision: requests is not None and current_revision != "")
@post(lambda result: isinstance(result, tuple) and all(getattr(d, "code", None) == "stale_revision" and getattr(d, "revision", "") != "" for d in result))
def _stale_diagnostics(requests: list[object], current_revision: str) -> tuple[object, ...]:
    """Build one stale diagnostic per request.

    >>> _stale_diagnostics_impl([object()], 'rev')[0].revision
    'rev'
    """
    return tuple(
        SelectorDiagnostic(
            request_index=request_index,
            status="error",
            code="stale_revision",
            message="Checklist inventory revision is stale.",
            revision=current_revision,
        )
        for request_index, _request in enumerate(requests)
    )


_stale_diagnostics_impl = _stale_diagnostics.__wrapped__


@pre(lambda requests: requests is not None)
@post(lambda result: isinstance(result, tuple) and all(getattr(d, "status", None) == "error" and getattr(d, "code", None) == "invalid_selector" for d in result))
def _mode_diagnostics(requests: list[object]) -> tuple[object, ...]:
    """Return invalid-mode diagnostics without mutating Markdown.

    >>> from vectl.core_checklist import LegacyKeywordSelector, MutationRequest
    >>> _mode_diagnostics_impl([MutationRequest(LegacyKeywordSelector('x'), True)])[0].code
    'invalid_selector'
    """
    diagnostics: list[object] = []
    saw_legacy = False
    saw_deterministic = False
    for request_index, request in enumerate(requests):
        selector = request.selector
        saw_legacy = saw_legacy or isinstance(selector, LegacyKeywordSelector)
        saw_deterministic = saw_deterministic or isinstance(selector, (ItemIdSelector, FieldIndexSelector))
        if isinstance(selector, LegacyKeywordSelector) and request.checked is not None:
            diagnostics.append(SelectorDiagnostic(request_index, "error", "Legacy keyword selectors must omit checked and toggle state.", "invalid_selector"))
        elif isinstance(selector, (ItemIdSelector, FieldIndexSelector)) and request.checked is None:
            diagnostics.append(SelectorDiagnostic(request_index, "error", "Deterministic selectors require an explicit checked boolean.", "invalid_selector", getattr(selector, "item_id", None), getattr(selector, "field", None), getattr(selector, "index", None)))
        elif not isinstance(selector, (LegacyKeywordSelector, ItemIdSelector, FieldIndexSelector)):
            diagnostics.append(SelectorDiagnostic(request_index, "error", "Unknown checklist selector type.", "invalid_selector"))
    if saw_legacy and saw_deterministic:
        diagnostics.extend(SelectorDiagnostic(i, "error", "Cannot mix legacy keyword toggle selectors with deterministic selectors.", "invalid_selector") for i, _ in enumerate(requests))
    return tuple(diagnostics)


_mode_diagnostics_impl = _mode_diagnostics.__wrapped__


@pre(lambda requests, items: requests is not None and items is not None)
@post(lambda result: isinstance(result, tuple) and len(result) == 2)
def _resolve_batch(requests: list[object], items: list[object]) -> tuple[list[tuple[int, object, bool]], tuple[object, ...]]:
    """Resolve every request before marker mutation.

    >>> _resolve_batch_impl([], [])
    ([], ())
    """
    resolved: list[tuple[int, object, bool]] = []
    diagnostics: list[object] = []
    for request_index, request in enumerate(requests):
        try:
            item = _resolve_request_impl(request, items)
        except ChecklistContractError as exc:
            payload = getattr(exc, "payload", None)
            if isinstance(payload, ChecklistErrorPayload):
                diagnostics.append(SelectorDiagnostic(request_index, "error", payload.message, payload.code, payload.item_id, payload.field, payload.index))
            else:
                diagnostics.append(SelectorDiagnostic(request_index, "error", str(exc), "invalid_selector"))
            continue
        target_checked = not item.checked if isinstance(request.selector, LegacyKeywordSelector) else bool(request.checked)
        resolved.append((request_index, item, target_checked))
    return resolved, tuple(diagnostics)


_resolve_batch_impl = _resolve_batch.__wrapped__


@pre(lambda diagnostics: diagnostics is not None)
def _raise_resolution_error(diagnostics: tuple[object, ...]) -> None:
    """Raise using the first selector-specific resolution diagnostic.

    >>> _raise_resolution_error_impl(()) is None
    True
    """
    if not diagnostics:
        return
    first = diagnostics[0]
    error_type = {
        "item_not_found": c.ItemNotFoundError,
        "unsupported_field": c.UnsupportedFieldError,
        "ambiguous_legacy_match": c.AmbiguousLegacyMatch,
        "no_legacy_match": c.NoLegacyMatch,
    }.get(first.code, c.InvalidSelectorError)
    _raise_batch_error_impl(error_type, c._error_payload_impl(first.code or "invalid_selector", first.message, item_id=first.item_id, field=first.field, index=first.index), diagnostics)


_raise_resolution_error_impl = _raise_resolution_error.__wrapped__


@pre(lambda resolved: resolved is not None)
@post(lambda result: isinstance(result, tuple) and all(getattr(d, "status", None) == "error" and getattr(d, "code", None) == "invalid_selector" for d in result))
def _duplicate_diagnostics(resolved: list[tuple[int, object, bool]]) -> tuple[object, ...]:
    """Reject duplicate or conflicting selectors deterministically.

    >>> _duplicate_diagnostics_impl([])
    ()
    """
    seen: dict[str, tuple[int, bool]] = {}
    diagnostics: list[object] = []
    for request_index, item, target_checked in resolved:
        previous = seen.get(item.item_id)
        if previous is None:
            seen[item.item_id] = (request_index, target_checked)
            continue
        previous_index, previous_target = previous
        message = "Conflicting selectors target the same checklist item." if previous_target != target_checked else "Duplicate selectors target the same checklist item."
        diagnostics.append(SelectorDiagnostic(previous_index, "error", message, "invalid_selector", item.item_id, item.field, item.index, target_checked=previous_target))
        diagnostics.append(SelectorDiagnostic(request_index, "error", message, "invalid_selector", item.item_id, item.field, item.index, target_checked=target_checked))
    return tuple(diagnostics)


_duplicate_diagnostics_impl = _duplicate_diagnostics.__wrapped__


@pre(lambda step, resolved: step is not None and resolved is not None)
@post(lambda result: isinstance(result, tuple) and len(result) == 3 and result[0] is not None and isinstance(result[1], int) and result[1] >= 0 and isinstance(result[2], tuple))
def _apply_resolved(step: Step, resolved: list[tuple[int, object, bool]]) -> tuple[Step, int, tuple[object, ...]]:
    """Apply marker-only changes after validation succeeds.

    >>> from vectl.models import Step
    >>> _apply_resolved_impl(Step(id='s', name='S'), [])[1]
    0
    """
    description = canonicalize_markdown_impl(step.description)
    verification = canonicalize_markdown_impl(step.verification)
    changed_items = 0
    diagnostics: list[object] = []
    for request_index, item, target_checked in resolved:
        if item.field == "description":
            description, changed = set_item_marker_impl(description, item.index, target_checked)
        else:
            verification, changed = set_item_marker_impl(verification, item.index, target_checked)
        changed_items += 1 if changed else 0
        diagnostics.append(SelectorDiagnostic(request_index, "changed" if changed else "unchanged", "Checklist marker changed." if changed else "Checklist marker already matched target state.", item_id=item.item_id, field=item.field, index=item.index, target_checked=target_checked))
    return step.model_copy(update={"description": description, "verification": verification}), changed_items, tuple(diagnostics)


_apply_resolved_impl = _apply_resolved.__wrapped__


@pre(lambda step, revision, requests: step is not None and revision != "" and requests is not None)
@post(lambda result: getattr(result, "step", None) is not None and getattr(result, "revision", "") != "" and getattr(result, "diagnostics", None) is not None)
def mutate_checklist_batch(step: Step, revision: str, requests: list[object]) -> object:
    """Validate a checklist batch before any marker-only mutation.

    >>> from vectl.core_checklist import FieldIndexSelector, MutationRequest, get_inventory
    >>> step = Step(id='s', name='S', description='- [ ] A')
    >>> current, _ = get_inventory.__wrapped__(step)
    >>> mutate_checklist_batch_impl(step, current, [MutationRequest(FieldIndexSelector('description', 0), True)]).step.description
    '- [x] A'
    """
    current_revision, items = c._inventory_snapshot_impl(step)
    if revision != current_revision:
        diagnostics = _stale_diagnostics_impl(requests, current_revision)
        _raise_batch_error_impl(c.StaleRevisionError, c._error_payload_impl("stale_revision", "Checklist inventory revision is stale.", revision=current_revision), diagnostics)
    diagnostics = _mode_diagnostics_impl(requests)
    if diagnostics:
        _raise_batch_error_impl(c.InvalidSelectorError, c._error_payload_impl("invalid_selector", diagnostics[0].message), diagnostics)
    resolved, resolution_diagnostics = _resolve_batch_impl(requests, items)
    _raise_resolution_error_impl(resolution_diagnostics)
    duplicate_diagnostics = _duplicate_diagnostics_impl(resolved)
    if duplicate_diagnostics:
        _raise_batch_error_impl(c.InvalidSelectorError, c._error_payload_impl("invalid_selector", duplicate_diagnostics[0].message), duplicate_diagnostics)
    updated_step, changed_items, selector_diagnostics = _apply_resolved_impl(step, resolved)
    updated_revision, _updated_items = c._inventory_snapshot_impl(updated_step)
    return c.MutationBatchResult(updated_step, updated_revision, c.BatchDiagnostics(len(requests), len(resolved), changed_items, selector_diagnostics))


mutate_checklist_batch_impl = mutate_checklist_batch.__wrapped__
