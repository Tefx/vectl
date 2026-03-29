"""Static loop action registry contracts.

Contract purity: this module declares auditable action metadata only. It does
not perform runtime registration or dispatch.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final, Literal, Protocol, TypeAlias, TypeVar

from .runtime_context import RuntimeContext


ActionCategory = Literal["execution", "planner", "control", "recovery"]

PlannerReplanTriggerName = Literal[
    "handle_dispatch.preflight_replan",
    "reconcile.evidence_replan",
    "reconcile.failure_classification_replan",
    "reconcile.escalation_replan",
    "run.startup_recovery_anomaly_replan",
]
PlannerGateRejectTriggerName = Literal["reconcile.gate_reject_fix_retest_chain"]
PlannerDispatchTriggerName: TypeAlias = PlannerReplanTriggerName | PlannerGateRejectTriggerName
PlannerDispatchSourceVerdict = Literal["REPLAN", "REJECT"]
PlannerDispatchActionType = Literal[
    "planner.dispatch_replan",
    "planner.dispatch_gate_reject",
]
PlannerDispatchEventName = Literal[
    "PLANNER_DISPATCH_STARTED",
    "PLANNER_DISPATCH_COMPLETED",
    "PLANNER_DISPATCH_FAILED",
]


PLANNER_DISPATCH_REPLAN_ACTION_TYPE: Final[Literal["planner.dispatch_replan"]] = (
    "planner.dispatch_replan"
)
PLANNER_DISPATCH_GATE_REJECT_ACTION_TYPE: Final[Literal["planner.dispatch_gate_reject"]] = (
    "planner.dispatch_gate_reject"
)
PLANNER_ACTION_CATEGORY: Final[Literal["planner"]] = "planner"
ACTION_REGISTRY_DECLARATION_MODE: Final[Literal["static_declaration_table"]] = (
    "static_declaration_table"
)
ACTION_REGISTRY_PUBLIC_DYNAMIC_REGISTRATION: Final[bool] = False
ACTION_REGISTRY_SCOPE_STATEMENT: Final[str] = (
    "Planner dispatch is included in loop action registry scope and is not deferred "
    "outside the registry contract."
)


@dataclass(frozen=True)
class PlannerDispatchAction:
    """Canonical planner-dispatch payload contract for loop actions.

    Authority:
    - docs/ADR-driver-evolution-foundation.md#71-loop-action-registry-covers-all-loop-executed-actions-including-planner-dispatch
    - docs/ADR-driver-evolution-foundation.md#188-resolved-decisions

    Invariants:
        - ``action_type`` explicitly distinguishes planner dispatch from generic
          execution actions.
        - ``planner_instruction`` is required and is the planner input payload.
        - ``context`` for the eventual handler is provided separately via the
          shared ``RuntimeContext`` contract.
    """

    action_type: PlannerDispatchActionType
    step_id: str
    trigger: PlannerDispatchTriggerName
    judgment_type: str
    planner_instruction: str
    source_verdict: PlannerDispatchSourceVerdict


PlannerDispatchActionT = TypeVar("PlannerDispatchActionT", bound=PlannerDispatchAction)


class LoopActionHandler(Protocol[PlannerDispatchActionT]):
    """Shared handler boundary for loop action registry entries."""

    async def __call__(
        self,
        action: PlannerDispatchActionT,
        *,
        context: RuntimeContext,
    ) -> None: ...


class PlannerDispatchHandler(Protocol):
    """Dedicated planner-dispatch handler boundary using shared RuntimeContext."""

    async def __call__(
        self,
        action: PlannerDispatchAction,
        *,
        context: RuntimeContext,
    ) -> None: ...


@dataclass(frozen=True)
class ActionEventExpectation:
    """Auditable event expectations for an action declaration."""

    started: PlannerDispatchEventName
    completed: PlannerDispatchEventName
    failed: PlannerDispatchEventName


@dataclass(frozen=True)
class ActionDeclaration:
    """Static declaration row for the layered loop action registry."""

    action_type: PlannerDispatchActionType
    category: ActionCategory
    payload_contract: str
    handler_contract: str
    runtime_context_contract: str
    event_expectations: ActionEventExpectation
    included_in_registry_scope: bool
    deferred_outside_registry: bool
    auditable_declaration: bool


PLANNER_DISPATCH_EVENT_EXPECTATIONS: Final[ActionEventExpectation] = ActionEventExpectation(
    started="PLANNER_DISPATCH_STARTED",
    completed="PLANNER_DISPATCH_COMPLETED",
    failed="PLANNER_DISPATCH_FAILED",
)

PLANNER_DISPATCH_ACTION_DECLARATIONS: Final[tuple[ActionDeclaration, ...]] = (
    ActionDeclaration(
        action_type=PLANNER_DISPATCH_REPLAN_ACTION_TYPE,
        category=PLANNER_ACTION_CATEGORY,
        payload_contract="PlannerDispatchAction",
        handler_contract="PlannerDispatchHandler",
        runtime_context_contract="RuntimeContext",
        event_expectations=PLANNER_DISPATCH_EVENT_EXPECTATIONS,
        included_in_registry_scope=True,
        deferred_outside_registry=False,
        auditable_declaration=True,
    ),
    ActionDeclaration(
        action_type=PLANNER_DISPATCH_GATE_REJECT_ACTION_TYPE,
        category=PLANNER_ACTION_CATEGORY,
        payload_contract="PlannerDispatchAction",
        handler_contract="PlannerDispatchHandler",
        runtime_context_contract="RuntimeContext",
        event_expectations=PLANNER_DISPATCH_EVENT_EXPECTATIONS,
        included_in_registry_scope=True,
        deferred_outside_registry=False,
        auditable_declaration=True,
    ),
)

LAYERED_ACTION_REGISTRY: Final[dict[ActionCategory, tuple[ActionDeclaration, ...]]] = {
    "execution": (),
    "planner": PLANNER_DISPATCH_ACTION_DECLARATIONS,
    "control": (),
    "recovery": (),
}


__all__ = [
    "ACTION_REGISTRY_DECLARATION_MODE",
    "ACTION_REGISTRY_PUBLIC_DYNAMIC_REGISTRATION",
    "ACTION_REGISTRY_SCOPE_STATEMENT",
    "ActionCategory",
    "ActionDeclaration",
    "ActionEventExpectation",
    "LAYERED_ACTION_REGISTRY",
    "LoopActionHandler",
    "PLANNER_ACTION_CATEGORY",
    "PLANNER_DISPATCH_ACTION_DECLARATIONS",
    "PLANNER_DISPATCH_EVENT_EXPECTATIONS",
    "PLANNER_DISPATCH_GATE_REJECT_ACTION_TYPE",
    "PLANNER_DISPATCH_REPLAN_ACTION_TYPE",
    "PlannerDispatchAction",
    "PlannerDispatchActionType",
    "PlannerDispatchEventName",
    "PlannerDispatchHandler",
    "PlannerDispatchSourceVerdict",
    "PlannerDispatchTriggerName",
]
