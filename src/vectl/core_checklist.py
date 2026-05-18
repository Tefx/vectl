"""Pure core checklist inventory and mutation service.

Authority: docs/RFC-deterministic-checklists.md
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Union, List, Optional

from vectl.models import Step
from invar_runtime import pre, post


# -- Types and Dataclasses --

ChecklistInventoryRevision = str


@dataclass(frozen=True)
class ItemIdSelector:
    """Selects a checklist item by its snapshot-scoped deterministic ID."""
    item_id: str

@dataclass(frozen=True)
class FieldIndexSelector:
    """Selects a checklist item by its field and positional index."""
    field: Literal["description", "verification"]
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
    field: Literal["description", "verification"]
    index: int
    text: str
    checked: bool


@dataclass(frozen=True)
class MutationRequest:
    """A request to mutate a checklist item."""
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
