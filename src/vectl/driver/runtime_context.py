"""Unified runtime handler context contract.

Authority:
- docs/ADR-driver-evolution-foundation.md#96-handlers-receive-a-unified-runtimecontext
- docs/ADR-driver-evolution-foundation.md#116-target-architecture-shape
- docs/ADR-driver-evolution-foundation.md#137-ownership-model
- docs/ADR-driver-evolution-foundation.md#188-resolved-decisions

Contract purity:
- declarations only
- no runtime orchestration logic
- role-specific handler contexts are explicitly deferred in this rollout
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Final, Literal, Protocol

if TYPE_CHECKING:
    from .config import DriverConfig
    from .judge import Judge
    from .observe import Observer
    from .runners import Runner
    from .session import SessionPool
    from .types import DriverState


# =============================================================================
# Concern and boundary declarations (from runtime_context.contract)
# =============================================================================

RuntimeConcern = Literal[
    "runtime_orchestration_state",
    "decide_local_state",
    "event_contracts",
    "policy_helpers",
]


@dataclass(frozen=True)
class RuntimeContextResponsibility:
    """Source-of-truth declaration for a concern touched by loop handlers."""

    concern: RuntimeConcern
    owner: str
    exposed_via_runtime_context: bool
    mutation_rule: str
    rationale: str


@dataclass(frozen=True)
class RuntimeContextBoundaryRule:
    """Boundary rule for the initial shared handler context rollout."""

    subject: RuntimeConcern
    allowed_through_context: str
    forbidden_through_context: str
    rationale: str


@dataclass(frozen=True)
class RoleSpecificContextDeferral:
    """Records the ADR-approved deferment of split handler contexts."""

    status: Literal["deferred"]
    deferred_contexts: tuple[str, ...]
    current_contract: str
    revisit_condition: str
    rationale: str


class RuntimeContextView(Protocol):
    """Structural contract for the initial shared handler context."""

    state: DriverState
    config: DriverConfig
    runners: Mapping[str, Runner]
    judge: Judge
    session_pool: SessionPool
    observer: Observer
    plan_path: Path


# =============================================================================
# Shared RuntimeContext (core contract from both sides)
# =============================================================================

RUNTIME_CONTEXT_ROLLOUT_CONTRACT: Final[str] = (
    "Initial registry-backed loop handlers receive a single shared RuntimeContext; "
    "role-specific contexts remain explicitly deferred for this rollout."
)


@dataclass(frozen=True)
class RuntimeContext:
    """Single shared handler input for the initial action-registry rollout.

    Ownership boundary:
    - exposes ``DriverState`` as the runtime orchestration owner
    - does not duplicate decide-local state outside ``DriverState.decide_state``
    - carries observer/emitter access for runtime event production, not event-schema ownership
    - carries policy helper access only indirectly via importing ``driver.policy`` at call sites

    Authority:
    - docs/ADR-driver-evolution-foundation.md#96-handlers-receive-a-unified-runtimecontext

    Invariants:
        - action handlers consume this shared context boundary rather than
          role-specific micro-contexts in the initial action-registry design.
        - this type is contract surface only; population/wiring remains an
          implementation concern.
    """

    state: DriverState
    config: DriverConfig
    runners: Mapping[str, Runner]
    judge: Judge
    session_pool: SessionPool
    observer: Observer
    plan_path: Path


# =============================================================================
# Responsibility split and boundary rules (from runtime_context.contract)
# =============================================================================

RUNTIME_CONTEXT_RESPONSIBILITY_SPLIT: Final[tuple[RuntimeContextResponsibility, ...]] = (
    RuntimeContextResponsibility(
        concern="runtime_orchestration_state",
        owner="DriverState",
        exposed_via_runtime_context=True,
        mutation_rule="handlers may read/write runtime orchestration state through RuntimeContext.state",
        rationale="ADR ownership model assigns runtime orchestration state to DriverState.",
    ),
    RuntimeContextResponsibility(
        concern="decide_local_state",
        owner="DecideState via DriverState.decide_state and vectl.decide",
        exposed_via_runtime_context=False,
        mutation_rule=(
            "handlers must not mutate decide-local memory directly except by passing "
            "DriverState.decide_state into vectl.decide APIs"
        ),
        rationale="ADR assigns decide-local state to DecideState, not the runtime context surface.",
    ),
    RuntimeContextResponsibility(
        concern="event_contracts",
        owner="driver.events.registry",
        exposed_via_runtime_context=False,
        mutation_rule="handlers may emit events through observer/emitter surfaces but may not define schemas",
        rationale="ADR keeps event contracts in a canonical registry separate from loop orchestration.",
    ),
    RuntimeContextResponsibility(
        concern="policy_helpers",
        owner="driver.policy",
        exposed_via_runtime_context=False,
        mutation_rule="handlers may call pure helpers but must not move policy ownership into RuntimeContext",
        rationale="ADR assigns parsing/classification ownership to driver.policy.",
    ),
)


RUNTIME_CONTEXT_BOUNDARY_RULES: Final[tuple[RuntimeContextBoundaryRule, ...]] = (
    RuntimeContextBoundaryRule(
        subject="runtime_orchestration_state",
        allowed_through_context="read/write DriverState-owned runtime fields needed for orchestration",
        forbidden_through_context="creating parallel runtime state owners outside DriverState",
        rationale="Preserves DriverState as the single runtime orchestration source of truth.",
    ),
    RuntimeContextBoundaryRule(
        subject="decide_local_state",
        allowed_through_context="pass existing DecideState to decide() as an explicit dependency",
        forbidden_through_context="treat RuntimeContext as a second decide-memory container",
        rationale="Prevents DriverState/RuntimeContext from collapsing decide-local ownership.",
    ),
    RuntimeContextBoundaryRule(
        subject="event_contracts",
        allowed_through_context="emit already-defined events through observer/emitter interfaces",
        forbidden_through_context="declaring new event names or schemas inside handlers/runtime context",
        rationale="Keeps event growth auditable and registry-owned.",
    ),
    RuntimeContextBoundaryRule(
        subject="policy_helpers",
        allowed_through_context="invoke pure classification/parsing helpers from driver.policy",
        forbidden_through_context="embedding policy helper state or rule ownership in RuntimeContext",
        rationale="Keeps policy logic pure and separately testable.",
    ),
)


ROLE_SPECIFIC_CONTEXTS_DEFERRED: Final[RoleSpecificContextDeferral] = RoleSpecificContextDeferral(
    status="deferred",
    deferred_contexts=(
        "DispatchContext",
        "ReconcileContext",
        "PlannerDispatchContext",
        "RecoveryContext",
    ),
    current_contract="single shared RuntimeContext",
    revisit_condition="only if RuntimeContext growth creates proven coupling or reviewability pressure",
    rationale="ADR chooses migration safety over premature role-specific context fragmentation.",
)


__all__ = [
    "ROLE_SPECIFIC_CONTEXTS_DEFERRED",
    "RUNTIME_CONTEXT_BOUNDARY_RULES",
    "RUNTIME_CONTEXT_RESPONSIBILITY_SPLIT",
    "RoleSpecificContextDeferral",
    "RuntimeContext",
    "RuntimeContextBoundaryRule",
    "RuntimeContextResponsibility",
    "RuntimeContextView",
    "RUNTIME_CONTEXT_ROLLOUT_CONTRACT",
]
