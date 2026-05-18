"""Pure core checklist inventory and mutation service.

Authority: docs/RFC-deterministic-checklists.md
"""

from __future__ import annotations

from dataclasses import dataclass
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

@pre(lambda step: step is not None)
@post(lambda result: isinstance(result, tuple) and len(result) == 2)
def get_inventory(step: Step) -> tuple[ChecklistInventoryRevision, list[ChecklistItem]]:
    """Get the deterministic checklist inventory and revision for a step.
    
    Only the `description` and `verification` fields are supported.
    
    >>> # Placeholder doctest
    >>> True
    True
    """
    raise NotImplementedError("Contract stub only")


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
    raise NotImplementedError("Contract stub only")
