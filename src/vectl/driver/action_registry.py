"""Static loop action registry contracts.

Contract purity: this module declares auditable action metadata only. It does
not perform runtime registration or dispatch.

Authority:
- docs/ADR-driver-evolution-foundation.md
  #71-loop-action-registry-covers-all-loop-executed-actions-including-planner-dispatch
- docs/ADR-driver-evolution-foundation.md#85-action-registry-is-layered-not-flat
- docs/ADR-driver-evolution-foundation.md#96-handlers-receive-a-unified-runtimecontext
- docs/ADR-driver-evolution-foundation.md#116-target-architecture-shape
- docs/ADR-driver-evolution-foundation.md#137-ownership-model
- docs/ADR-driver-evolution-foundation.md#188-resolved-decisions
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final, Literal, Protocol, TypeAlias, TypeVar

from vectl.models import Action

from .runtime_context import RuntimeContext

# =============================================================================
# Planner-dispatch specific types (HEAD contract surface)
# =============================================================================

ActionCategory = Literal["execution", "planner", "control", "recovery"]

RuntimeExecutionActionType = Literal["claim_and_dispatch", "wait"]
RuntimeControlActionType = Literal["complete", "escalate"]
RuntimeRecoveryActionType = Literal["recovery.all_stalled"]
RuntimeLoopActionType: TypeAlias = (
    RuntimeExecutionActionType | RuntimeControlActionType | RuntimeRecoveryActionType
)

CLAIM_AND_DISPATCH_ACTION_TYPE: Final[Literal["claim_and_dispatch"]] = "claim_and_dispatch"
WAIT_ACTION_TYPE: Final[Literal["wait"]] = "wait"
COMPLETE_ACTION_TYPE: Final[Literal["complete"]] = "complete"
ESCALATE_ACTION_TYPE: Final[Literal["escalate"]] = "escalate"
RECOVERY_ALL_STALLED_ACTION_TYPE: Final[Literal["recovery.all_stalled"]] = "recovery.all_stalled"

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
PLANNER_DISPATCH_REPLAN_HANDLER_CONTRACT: Final[Literal["dispatch_planner_replan"]] = (
    "dispatch_planner_replan"
)
PLANNER_DISPATCH_GATE_REJECT_HANDLER_CONTRACT: Final[Literal["dispatch_planner_gate_reject"]] = (
    "dispatch_planner_gate_reject"
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


# =============================================================================
# Planner-dispatch contracts (HEAD contract surface)
# =============================================================================


@dataclass(frozen=True)
class PlannerDispatchAction:
    """Canonical planner-dispatch payload contract for loop actions.

    Authority:
    - docs/ADR-driver-evolution-foundation.md
      #71-loop-action-registry-covers-all-loop-executed-actions-including-planner-dispatch
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

    started: str | None = None
    completed: str | None = None
    failed: str | None = None
    emitted: tuple[str, ...] = ()


@dataclass(frozen=True)
class ActionDeclaration:
    """Static declaration row for the layered loop action registry."""

    action_type: str
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
    emitted=(
        "PLANNER_DISPATCH_STARTED",
        "PLANNER_DISPATCH_COMPLETED",
        "PLANNER_DISPATCH_FAILED",
    ),
)

CLAIM_AND_DISPATCH_EVENT_EXPECTATIONS: Final[ActionEventExpectation] = ActionEventExpectation(
    emitted=(
        "STEP_DISPATCHED",
        "SESSION_REUSE_HIT",
        "SESSION_REUSE_MISS",
        "RUNNER_FALLBACK",
        "PREFLIGHT_VERDICT",
        "PREFLIGHT_REJECT",
        "PREFLIGHT_DEFER",
        "PREFLIGHT_SKIPPED",
        "COLD_CONTEXT_VERDICT",
    ),
)

WAIT_EVENT_EXPECTATIONS: Final[ActionEventExpectation] = ActionEventExpectation(emitted=("WAIT",))

COMPLETE_EVENT_EXPECTATIONS: Final[ActionEventExpectation] = ActionEventExpectation(
    emitted=("COMPLETE_ACTION_IGNORED",)
)

ESCALATE_EVENT_EXPECTATIONS: Final[ActionEventExpectation] = ActionEventExpectation(
    emitted=("ESCALATION_DEFERRED",)
)

RECOVERY_ALL_STALLED_EVENT_EXPECTATIONS: Final[ActionEventExpectation] = ActionEventExpectation(
    emitted=("RECOVERY",)
)

PLANNER_DISPATCH_ACTION_DECLARATIONS: Final[tuple[ActionDeclaration, ...]] = (
    ActionDeclaration(
        action_type=PLANNER_DISPATCH_REPLAN_ACTION_TYPE,
        category=PLANNER_ACTION_CATEGORY,
        payload_contract="PlannerDispatchAction",
        handler_contract=PLANNER_DISPATCH_REPLAN_HANDLER_CONTRACT,
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
        handler_contract=PLANNER_DISPATCH_GATE_REJECT_HANDLER_CONTRACT,
        runtime_context_contract="RuntimeContext",
        event_expectations=PLANNER_DISPATCH_EVENT_EXPECTATIONS,
        included_in_registry_scope=True,
        deferred_outside_registry=False,
        auditable_declaration=True,
    ),
)

EXECUTION_ACTION_DECLARATIONS: Final[tuple[ActionDeclaration, ...]] = (
    ActionDeclaration(
        action_type=CLAIM_AND_DISPATCH_ACTION_TYPE,
        category="execution",
        payload_contract="Action",
        handler_contract="handle_dispatch",
        runtime_context_contract="RuntimeContext",
        event_expectations=CLAIM_AND_DISPATCH_EVENT_EXPECTATIONS,
        included_in_registry_scope=True,
        deferred_outside_registry=False,
        auditable_declaration=True,
    ),
    ActionDeclaration(
        action_type=WAIT_ACTION_TYPE,
        category="execution",
        payload_contract="Action",
        handler_contract="emit_wait",
        runtime_context_contract="RuntimeContext",
        event_expectations=WAIT_EVENT_EXPECTATIONS,
        included_in_registry_scope=True,
        deferred_outside_registry=False,
        auditable_declaration=True,
    ),
)

CONTROL_ACTION_DECLARATIONS: Final[tuple[ActionDeclaration, ...]] = (
    ActionDeclaration(
        action_type=COMPLETE_ACTION_TYPE,
        category="control",
        payload_contract="Action",
        handler_contract="emit_complete_action_ignored",
        runtime_context_contract="RuntimeContext",
        event_expectations=COMPLETE_EVENT_EXPECTATIONS,
        included_in_registry_scope=True,
        deferred_outside_registry=False,
        auditable_declaration=True,
    ),
    ActionDeclaration(
        action_type=ESCALATE_ACTION_TYPE,
        category="control",
        payload_contract="Action",
        handler_contract="emit_escalation_deferred",
        runtime_context_contract="RuntimeContext",
        event_expectations=ESCALATE_EVENT_EXPECTATIONS,
        included_in_registry_scope=True,
        deferred_outside_registry=False,
        auditable_declaration=True,
    ),
)

RECOVERY_ACTION_DECLARATIONS: Final[tuple[ActionDeclaration, ...]] = (
    ActionDeclaration(
        action_type=RECOVERY_ALL_STALLED_ACTION_TYPE,
        category="recovery",
        payload_contract="DriverState",
        handler_contract="recover_all_stalled",
        runtime_context_contract="RuntimeContext",
        event_expectations=RECOVERY_ALL_STALLED_EVENT_EXPECTATIONS,
        included_in_registry_scope=True,
        deferred_outside_registry=False,
        auditable_declaration=True,
    ),
)

LAYERED_ACTION_REGISTRY: Final[dict[ActionCategory, tuple[ActionDeclaration, ...]]] = {
    "execution": EXECUTION_ACTION_DECLARATIONS,
    "planner": PLANNER_DISPATCH_ACTION_DECLARATIONS,
    "control": CONTROL_ACTION_DECLARATIONS,
    "recovery": RECOVERY_ACTION_DECLARATIONS,
}

_RUNTIME_LOOP_ACTION_INDEX: Final[dict[str, ActionDeclaration]] = {
    declaration.action_type: declaration
    for declaration in (
        *EXECUTION_ACTION_DECLARATIONS,
        *CONTROL_ACTION_DECLARATIONS,
        *RECOVERY_ACTION_DECLARATIONS,
    )
}

_PLANNER_DISPATCH_ACTION_INDEX: Final[dict[str, ActionDeclaration]] = {
    declaration.action_type: declaration for declaration in PLANNER_DISPATCH_ACTION_DECLARATIONS
}


class UnknownLoopActionError(ValueError):
    """Raised when loop receives an action not declared in the registry."""


class UnknownPlannerDispatchActionError(ValueError):
    """Raised when planner dispatch action is not declared in registry."""


def resolve_runtime_loop_action_declaration(action_type: str) -> ActionDeclaration:
    """Resolve one runtime loop action declaration by action type.

    Args:
        action_type: Action type emitted by ``decide()`` for runtime execution.

    Returns:
        The static ``ActionDeclaration`` for ``action_type``.

    Raises:
        UnknownLoopActionError: If ``action_type`` is not registry-declared.
    """

    declaration = _RUNTIME_LOOP_ACTION_INDEX.get(action_type)
    if declaration is None:
        declared = ", ".join(sorted(_RUNTIME_LOOP_ACTION_INDEX))
        raise UnknownLoopActionError(
            f"Unknown loop action '{action_type}' at registry boundary. "
            f"Declared runtime actions: {declared}"
        )
    return declaration


def resolve_planner_dispatch_action_declaration(
    action_type: PlannerDispatchActionType,
) -> ActionDeclaration:
    """Resolve one planner-dispatch declaration by action type.

    Args:
        action_type: Planner-dispatch action type to resolve.

    Returns:
        The static ``ActionDeclaration`` for ``action_type``.

    Raises:
        UnknownPlannerDispatchActionError: If ``action_type`` is not registry-declared.
    """

    declaration = _PLANNER_DISPATCH_ACTION_INDEX.get(action_type)
    if declaration is None:
        declared = ", ".join(sorted(_PLANNER_DISPATCH_ACTION_INDEX))
        raise UnknownPlannerDispatchActionError(
            f"Unknown planner dispatch action '{action_type}' at registry boundary. "
            f"Declared planner actions: {declared}"
        )
    return declaration


# =============================================================================
# General loop action handler contracts (incoming contract surface)
# =============================================================================

LoopActionKind = Literal[
    "claim_and_dispatch",
    "complete",
    "wait",
    "escalate",
    "planner_dispatch",
    "recovery",
]


LoopActionCategory = Literal["runtime", "planner", "control", "recovery"]


class RuntimeActionHandler(Protocol):
    """Initial shared handler signature for registry-backed loop actions."""

    async def __call__(self, action: Action, context: RuntimeContext) -> None: ...


@dataclass(frozen=True)
class ActionHandlerContract:
    """Registry declaration for one loop action surface."""

    action: LoopActionKind
    category: LoopActionCategory
    handler_name: str
    handler_input: str
    state_owner: str
    decide_state_boundary: str
    event_boundary: str
    policy_boundary: str
    role_specific_contexts_deferred: bool
    rationale: str


INITIAL_HANDLER_INPUT_CONTRACT: Final[str] = "single shared RuntimeContext"


ACTION_HANDLER_CONTRACTS: Final[tuple[ActionHandlerContract, ...]] = (
    ActionHandlerContract(
        action="claim_and_dispatch",
        category="runtime",
        handler_name="handle_dispatch",
        handler_input=INITIAL_HANDLER_INPUT_CONTRACT,
        state_owner="DriverState",
        decide_state_boundary="pass DriverState.decide_state only through vectl.decide interfaces",
        event_boundary="emit declared events only; schema ownership stays outside handlers",
        policy_boundary="use driver.policy helpers without transferring policy ownership",
        role_specific_contexts_deferred=True,
        rationale=(
            "Dispatch remains a runtime handler but adopts the shared context contract first."
        ),
    ),
    ActionHandlerContract(
        action="complete",
        category="control",
        handler_name="handle_complete",
        handler_input=INITIAL_HANDLER_INPUT_CONTRACT,
        state_owner="DriverState",
        decide_state_boundary=(
            "complete-action compatibility must not become decide-state ownership drift"
        ),
        event_boundary="legacy compatibility emissions still use declared event surfaces",
        policy_boundary="no policy ownership transfer",
        role_specific_contexts_deferred=True,
        rationale="Legacy complete compatibility remains explicitly bounded during migration.",
    ),
    ActionHandlerContract(
        action="planner_dispatch",
        category="planner",
        handler_name="dispatch_planner",
        handler_input=INITIAL_HANDLER_INPUT_CONTRACT,
        state_owner="DriverState",
        decide_state_boundary=(
            "planner dispatch may observe runtime state but does not own DecideState"
        ),
        event_boundary="planner dispatch emits events through runtime surfaces only",
        policy_boundary="planner trigger classification remains policy/judgment owned",
        role_specific_contexts_deferred=True,
        rationale=(
            "Planner actions are first-class loop action contracts in the target architecture."
        ),
    ),
    ActionHandlerContract(
        action="recovery",
        category="recovery",
        handler_name="startup_recovery_or_runtime_repair",
        handler_input=INITIAL_HANDLER_INPUT_CONTRACT,
        state_owner="DriverState",
        decide_state_boundary=(
            "recovery may restore orchestration state without becoming decide-memory owner"
        ),
        event_boundary="recovery reports through declared event surfaces only",
        policy_boundary="recovery-specific classification remains external to RuntimeContext",
        role_specific_contexts_deferred=True,
        rationale=(
            "Recovery is preserved as a separate category without introducing "
            "special-purpose contexts yet."
        ),
    ),
)


__all__ = [
    "ACTION_HANDLER_CONTRACTS",
    "ACTION_REGISTRY_DECLARATION_MODE",
    "ACTION_REGISTRY_PUBLIC_DYNAMIC_REGISTRATION",
    "ACTION_REGISTRY_SCOPE_STATEMENT",
    "CLAIM_AND_DISPATCH_ACTION_TYPE",
    "CLAIM_AND_DISPATCH_EVENT_EXPECTATIONS",
    "COMPLETE_ACTION_TYPE",
    "COMPLETE_EVENT_EXPECTATIONS",
    "CONTROL_ACTION_DECLARATIONS",
    "ESCALATE_ACTION_TYPE",
    "ESCALATE_EVENT_EXPECTATIONS",
    "EXECUTION_ACTION_DECLARATIONS",
    "ActionCategory",
    "ActionDeclaration",
    "ActionEventExpectation",
    "ActionHandlerContract",
    "INITIAL_HANDLER_INPUT_CONTRACT",
    "LAYERED_ACTION_REGISTRY",
    "LoopActionCategory",
    "LoopActionHandler",
    "LoopActionKind",
    "PLANNER_ACTION_CATEGORY",
    "RECOVERY_ACTION_DECLARATIONS",
    "RECOVERY_ALL_STALLED_ACTION_TYPE",
    "RECOVERY_ALL_STALLED_EVENT_EXPECTATIONS",
    "PLANNER_DISPATCH_ACTION_DECLARATIONS",
    "PLANNER_DISPATCH_GATE_REJECT_HANDLER_CONTRACT",
    "PLANNER_DISPATCH_EVENT_EXPECTATIONS",
    "PLANNER_DISPATCH_GATE_REJECT_ACTION_TYPE",
    "PLANNER_DISPATCH_REPLAN_HANDLER_CONTRACT",
    "PLANNER_DISPATCH_REPLAN_ACTION_TYPE",
    "PlannerDispatchAction",
    "PlannerDispatchActionType",
    "PlannerDispatchEventName",
    "PlannerDispatchHandler",
    "PlannerDispatchSourceVerdict",
    "PlannerDispatchTriggerName",
    "RuntimeActionHandler",
    "UnknownLoopActionError",
    "UnknownPlannerDispatchActionError",
    "WAIT_ACTION_TYPE",
    "WAIT_EVENT_EXPECTATIONS",
    "resolve_planner_dispatch_action_declaration",
    "resolve_runtime_loop_action_declaration",
]
