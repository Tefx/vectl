"""Compatibility facade for driver event sinks.

Ownership split:
- observe.py remains the sink-facing import surface
- driver.events.registry is the canonical schema/source of truth

Authoritative source:
- docs/ADR-driver-evolution-foundation.md#45-event-registry-becomes-source-of-truth
- docs/ADR-driver-evolution-foundation.md#104-event-registry-is-a-static-declaration-table
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final, Literal

from .events.sinks import FileObserver, NullObserver, Observer, create_observer
from .events.types import Event

AgentSelectionObservabilityField = Literal[
    "surface",
    "runner",
    "selection_mode",
    "external_agent_name",
    "prompt_source",
]


@dataclass(frozen=True)
class AgentSelectionObservabilityContract:
    """Pinned observability payload for planner/judge selection semantics.

    Source:
    - docs/DRIVER-AGENT-SELECTION.md ``Observability Contract``
    - docs/DRIVER-AGENT-SELECTION.md ``Fallback and Error Policy``
    - docs/DRIVER-AGENT-SELECTION.md ``Runtime Matrix``
    """

    contract_id: str
    source_step_id: str
    required_fields: tuple[AgentSelectionObservabilityField, ...]
    explicit_error_outcomes: tuple[str, ...]
    semantic_goal: str


AGENT_SELECTION_OBSERVABILITY_CONTRACT: Final[AgentSelectionObservabilityContract] = (
    AgentSelectionObservabilityContract(
        contract_id="driver-agent-selection-observability-v1",
        source_step_id="driver-agent-selection-contract.pin-contract",
        required_fields=(
            "surface",
            "runner",
            "selection_mode",
            "external_agent_name",
            "prompt_source",
        ),
        explicit_error_outcomes=(
            "unsupported external agent selection emits explicit selection error event",
            "missing external agent emits explicit missing-agent error event",
        ),
        semantic_goal=(
            "logs must distinguish real external persona selection from bundled prompt operation"
        ),
    )
)

__all__ = ["Event", "Observer", "FileObserver", "NullObserver", "create_observer"]
