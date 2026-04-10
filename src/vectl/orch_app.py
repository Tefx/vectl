"""
Orchestration application composition root.

Authority: docs/ORCHESTRATION-PLANE-IMPLEMENTATION-DESIGN.md section 6
Authority: docs/ORCHESTRATION-PLANE-ARCHITECTURE.md sections 2, 5
Authority: docs/ORCHESTRATION-PLANE-INTERFACES.md sections 3, 4

This module is the composition root that wires together the orchestration plane
components (control, roster, runtime, resolver, core_adapter) and exposes a
typed application API for the ``vectl orch`` CLI command group.

Public surfaces (this module):
    - OrchestrationApp              (composed application facade / entrypoint contract)
    - AppConfig                      (application-level configuration model)
    - OrchestrationResult            (typed result DTO returned by orchestration operations)
    - build_orchestration_app()      (factory for composing orchestration app from config)

Note: This module addresses the ``orch_app`` composition-root responsibility
and wires runtime start/resume/recovery entrypoints through concrete runtime
execution requests.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import time
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Literal, cast

from vectl.orchestration.config import (
    OrchestrationConfig,
    ResolverToolAllowlist,
    freeze_config,
    load_frozen_snapshot,
    load_orchestration_config,
    validate_orchestration_config,
)
from vectl.orchestration.continuity_artifacts import (
    DispatchRecoveryGate,
    NotificationKind,
    NotificationStatus,
    OperatorNotificationRecord,
    PausedRoutingState,
    RuntimeRecoveryRecord,
)
from vectl.orchestration.contracts import (
    ControlDecision,
    CoreSnapshot,
    DispatchSpec,
    ExecutionRequest,
    ExecutionResult,
    ReconcileResult,
    ResolutionCase,
    ResolutionCaseSource,
    ResolutionReport,
    RosterSnapshot,
    RuntimeSnapshot,
    StructuredReviewResult,
)
from vectl.orchestration.control_channel import (
    ControlChannelMessage,
    FilesystemControlChannel,
    send_to_control,
)
from vectl.orchestration.dispatch_policy import (
    ConfigPromptRegistry,
    ConfigRoleProfileRegistry,
    CoreStepDataAdapter,
    DispatchCoordinator,
    normalize_parse_failure,
    normalize_review_result,
)
from vectl.orchestration.events import (
    EventCorruptionError,
    JsonlEventSink,
    OrchestrationEventEnvelope,
    OrchestrationEventKind,
    load_event_jsonl,
)
from vectl.orchestration.inspection_queries import (
    CasesQueryImpl,
    InspectQuery,
    RunsQueryImpl,
    query_cases,
    query_runs,
)
from vectl.orchestration.projections import replay_events_to_artifacts
from vectl.orchestration.recovery import (
    CutoverValidationResult,
    CutoverValidator,
    LegacyRunStatus,
    RecoveryAction,
    RecoveryOutcome,
    RecoveryReport,
    RunStoreLegacyRunBridge,
    recovery_action_status,
    recovery_case_status,
)
from vectl.orchestration.run_store import (
    CorruptJSONLError,
    RunRecord,
    RunRegistry,
    RunRegistryInspectionView,
    SamePlanAdmissionError,
    generate_run_id,
)
from vectl.orchestration.tool_registry import canonical_tool_families
from vectl.plan_path import is_linked_worktree

if TYPE_CHECKING:
    from vectl.orchestration.control import Control
    from vectl.orchestration.core_adapter import CoreAdapter, PlanCoreAdapter
    from vectl.orchestration.resolver import Resolver
    from vectl.orchestration.roster import Roster
    from vectl.orchestration.runtime import Runtime


# ---------------------------------------------------------------------
# Dispatch Recovery Gate Errors
# ---------------------------------------------------------------------


class DispatchBlockedError(Exception):
    """Raised when dispatch is blocked by a recovery gate or paused operator state.

    Authority:
        docs/ORCHESTRATION-PLANE-OPERATOR-CONFLICT-RECOVERY.md sections 6.3, 6.4, 9
        docs/ORCHESTRATION-PLANE-ORCH-APP-ROUTING.md section 6.3

    This error is raised when:
        - DispatchRecoveryGate indicates unsafe dispatch is blocked
        - Paused operator notification prevents normal dispatch
        - Duplicate complete is blocked by a recovery gate

    The error preserves the gate reason so callers and operators can
    understand why dispatch is blocked without silently bypassing it.
    """

    def __init__(self, message: str = "") -> None:
        self.message = message
        super().__init__(message)


class DuplicateCompleteBlockedError(Exception):
    """Raised when step completion is blocked because a recovery gate
    indicates duplicate complete prevention is active.

    Authority:
        docs/ORCHESTRATION-PLANE-OPERATOR-CONFLICT-RECOVERY.md sections 6.4, 9

    This ensures that after restart, a step cannot be completed until
    reconcile closure is durably re-established.
    """

    def __init__(self, message: str = "") -> None:
        self.message = message
        super().__init__(message)


# ---------------------------------------------------------------------
# Application Configuration
# ---------------------------------------------------------------------


@dataclass(frozen=True)
class AppConfig:
    """
    Application-level configuration for the orchestration app.

    Authority: docs/ORCHESTRATION-PLANE-IMPLEMENTATION-DESIGN.md section 3.7

    Attributes:
        plan_path: Path to the authoritative plan definition file.
        worktree_base_dir: Base directory for worktree/workspace creation.
        default_agent: Default agent name for dispatch.
        resolver_timeout_seconds: Timeout for resolver invocations.
    """

    plan_path: Path = field(default_factory=lambda: Path("plan.yaml"))
    worktree_base_dir: Path = field(default_factory=lambda: Path(".vectl/workspaces"))
    default_agent: str = "python-executor"
    resolver_timeout_seconds: float = 60.0
    orchestration_config: OrchestrationConfig | None = None
    run_store_root: Path | None = None


# ---------------------------------------------------------------------
# Orchestration Result DTOs
# ---------------------------------------------------------------------


@dataclass(frozen=True)
class OrchestrationResult:
    """
    Typed result DTO returned by orchestration operations.

    Authority: docs/ORCHESTRATION-PLANE-INTERFACES.md (shared)

    GAP: The exact result schema for orchestration operations is not yet
    fully specified. The fields below represent the known minimum anchor.

    Attributes:
        success: True if the operation completed successfully.
        message: Human-readable result message.
        step_id: Optional step ID affected by the operation.
        run_id: Optional run identifier created by the operation.
        recovery_report: Canonical recovery report when produced by recover().
    """

    success: bool
    message: str
    step_id: str | None = None
    run_id: str | None = None
    recovery_report: RecoveryReport | None = None


@dataclass(frozen=True)
class RunResult:
    """
    Typed result for a single run execution.

    Authority: docs/ORCHESTRATION-PLANE-INTERFACES.md (shared)

    Attributes:
        run_id: Unique identifier for this run.
        step_id: Step being executed.
        status: Execution status.
        output_summary: Human-readable summary of results.
    """

    run_id: str
    step_id: str
    status: Literal["pending", "running", "success", "fail", "stall"] | None = None
    output_summary: str = ""
    source: Literal["orchestration_native", "legacy_imported"] = "orchestration_native"
    legacy_run_id: str | None = None
    legacy_migration_state: Literal["parallel", "preferred", "deprecated", "retired"] | None = None
    continuity_blocker: str | None = None


@dataclass(frozen=True)
class InspectResult:
    """
    Typed result for inspect operations.

    Authority: docs/ORCHESTRATION-PLANE-INTERFACES.md (shared)

    GAP: The exact inspect result schema is not yet fully specified.

    Attributes:
        view_type: The type of inspect view returned.
        data: Opaque inspection data (structure depends on view_type).
    """

    view_type: str
    data: tuple[str, ...] = ()


@dataclass(frozen=True)
class CaseResult:
    """
    Typed result for case operations.

    Authority: docs/ORCHESTRATION-PLANE-INTERFACES.md (shared)

    GAP: The exact case result schema is not yet fully specified.

    Attributes:
        case_id: Unique identifier for the case.
        status: Case status.
        reason: Reason for the case.
    """

    case_id: str | None = None
    status: Literal["open", "resolved", "halt"] | None = None
    reason: str = ""


@dataclass(frozen=True)
class ControlResult:
    """
    Typed result for control operations.

    Authority: docs/ORCHESTRATION-PLANE-INTERFACES.md (shared)

    GAP: The exact control result schema is not yet fully specified.

    Attributes:
        action: The control action that was taken.
        success: True if the action completed successfully.
        message: Human-readable result message.
    """

    action: Literal["pause", "unpause", "stop"] | None = None
    success: bool = False
    message: str = ""


@dataclass(frozen=True)
class ConfigResult:
    """
    Typed result for config operations.

    Authority: docs/ORCHESTRATION-PLANE-IMPLEMENTATION-DESIGN.md section 3.7

    GAP: The exact config result schema is not yet fully specified.

    Attributes:
        show_output: Output from config show operation.
        validation_passed: True if config validation passed.
        tools: Tuple of registered tool family identifiers.
    """

    show_output: str = ""
    validation_passed: bool = False
    tools: tuple[str, ...] = ()


@dataclass(frozen=True)
class OperatorNotification:
    """Explicit operator-visible resolver escalation state.

    Authority:
        docs/ORCHESTRATION-PLANE-ORCH-APP-ROUTING.md section 10
        docs/ORCHESTRATION-PLANE-OPERATOR-CONFLICT-RECOVERY.md section 4

    Invariants:
        - ``status`` is durable receipt state, not a transient UI hint.
        - ``paused_routing_state`` remains non-``active`` while this notice is
          the authoritative routing reason the app must not resume ordinary
          dispatch.
    """

    case_id: str
    summary: str
    notification_id: str = ""
    run_id: str | None = None
    kind: NotificationKind = "operator_required"
    status: NotificationStatus = "pending"
    evidence_refs: tuple[str, ...] = ()
    operator_message: str | None = None
    paused_routing_state: PausedRoutingState = "paused_operator_wait"
    created_at: float = 0.0
    updated_at: float = 0.0


@dataclass(frozen=True)
class SnapshotBundle:
    """Current orchestration snapshots read in routing order."""

    core: CoreSnapshot
    roster: RosterSnapshot
    runtime: RuntimeSnapshot


def build_resolution_case(
    *,
    case_id: str,
    decision: ControlDecision,
    core: CoreSnapshot,
    roster: RosterSnapshot,
    runtime: RuntimeSnapshot,
    case_source: ResolutionCaseSource = "unknown",
    summary: str | None = None,
    blocked_step_ids: tuple[str, ...] | None = None,
    artifact_refs: tuple[str, ...] = (),
) -> ResolutionCase:
    """Build an explicit ResolutionCase from a resolve decision.

    Authority:
        docs/ORCHESTRATION-PLANE-RESOLUTION-CONTRACT.md section 3
        docs/ORCHESTRATION-PLANE-ORCH-APP-ROUTING.md sections 3 and 8
    """

    if decision.kind != "resolve":
        raise ValueError("resolution cases may be built only from ControlDecision(kind='resolve')")

    return ResolutionCase(
        case_id=case_id,
        case_source=case_source,
        reason=decision.reason,
        summary=summary,
        core=core,
        roster=roster,
        runtime=runtime,
        blocked_step_ids=core.blocked_step_ids if blocked_step_ids is None else blocked_step_ids,
        artifact_refs=artifact_refs,
    )


def normalize_review_resolution_case(
    result: StructuredReviewResult,
    *,
    case_id: str,
    core: CoreSnapshot,
    roster: RosterSnapshot,
    runtime: RuntimeSnapshot,
) -> ResolutionCase | None:
    """Normalize structured review/gate output into explicit resolution intake."""

    return normalize_review_result(
        result=result,
        case_id=case_id,
        core=core,
        roster=roster,
        runtime=runtime,
    )


def build_review_parse_failure_case(
    *,
    raw_output: str,
    role_id: str,
    case_id: str,
    core: CoreSnapshot,
    roster: RosterSnapshot,
    runtime: RuntimeSnapshot,
) -> ResolutionCase:
    """Build explicit resolution intake for structured review parse failures."""

    return normalize_parse_failure(
        raw_output=raw_output,
        role_id=role_id,
        case_id=case_id,
        core=core,
        roster=roster,
        runtime=runtime,
    )


# ---------------------------------------------------------------------
# Orchestration App Composition Root
# ---------------------------------------------------------------------


class OrchestrationApp:
    """
    Composed orchestration application facade.

    Authority: docs/ORCHESTRATION-PLANE-IMPLEMENTATION-DESIGN.md section 6

    This is the composition root that wires together the orchestration plane
    components (control, roster, runtime, resolver, core_adapter) and exposes
    a typed application API for the ``vectl orch`` CLI command group.

    GAP: Concrete component wiring and runtime behavior are deferred to
    implementation phases. This contract step pins only typed boundaries.

    Public surface:
        - run()             (start/resume a run)
        - recover()         (recover from artifacts)
        - cutover_validate() (validate migration cutover retirement criteria)
        - migration_advance_state() (advance imported legacy migration state)
        - prune()           (prune old runs/artifacts)
        - runs()            (list runs)
        - inspect()          (inspect status/events/logs/artifacts/actions)
        - case_*()           (case list/show/respond)
        - control_*()        (control pause/unpause/stop)
        - config_*()         (config show/validate/tools)
    """

    def __init__(
        self,
        config: AppConfig,
        control: Control,
        roster: Roster,
        runtime: Runtime,
        resolver: Resolver,
        core_adapter: CoreAdapter,
        role_registry: ConfigRoleProfileRegistry | None = None,
        prompt_registry: ConfigPromptRegistry | None = None,
        dispatch_coordinator: DispatchCoordinator | None = None,
    ) -> None:
        """
        Initialize the orchestration app with composed components.

        Args:
            config: Application-level configuration.
            control: Plan-aware orchestration control component.
            roster: Reusable resource registry component.
            runtime: Mechanical execution runtime component.
            resolver: Blocked/unresolved case resolver component.
            core_adapter: Authoritative core bridge adapter.
        """
        self._config = config
        self._control = control
        self._roster = roster
        self._runtime = runtime
        self._resolver = resolver
        self._core_adapter = core_adapter
        self._event_sink = JsonlEventSink(self._events_path())
        self._text_log_path = self._logs_path()
        self._text_log_path.parent.mkdir(parents=True, exist_ok=True)
        self._text_log_path.touch(exist_ok=True)
        self._latest_recovery_report: RecoveryReport | None = None
        self._latest_operator_notification: OperatorNotification | None = None
        self._role_registry = role_registry or ConfigRoleProfileRegistry(
            default_role=config.default_agent,
        )
        self._prompt_registry = prompt_registry or ConfigPromptRegistry(
            role_registry=self._role_registry
        )
        self._dispatch_coordinator = dispatch_coordinator or DispatchCoordinator(
            role_registry=self._role_registry,
            prompt_registry=self._prompt_registry,
            step_adapter=CoreStepDataAdapter(cast("PlanCoreAdapter", core_adapter)),
        )

    def refresh_snapshots(self, *, agent: str | None = None) -> SnapshotBundle:
        """Refresh orchestration snapshots in the required evaluation order."""

        return SnapshotBundle(
            core=self._core_adapter.snapshot(agent=agent),
            roster=self._roster.snapshot(),
            runtime=self._runtime.snapshot(),
        )

    def evaluate_control(
        self, *, agent: str | None = None
    ) -> tuple[SnapshotBundle, ControlDecision]:
        """Refresh snapshots before invoking control evaluation."""

        snapshots = self.refresh_snapshots(agent=agent)
        decision = self._control.evaluate(
            core=snapshots.core,
            roster=snapshots.roster,
            runtime=snapshots.runtime,
        )
        return snapshots, decision

    def resolve_case(self, case: ResolutionCase) -> ResolutionReport:
        """Resolve one explicit blocked/non-closure case through the bound resolver.

        Authority:
            docs/ORCHESTRATION-PLANE-RESOLUTION-CONTRACT.md sections 2, 3, 5
            docs/ORCHESTRATION-PLANE-ORCH-APP-ROUTING.md sections 8 and 10
        """

        report = self._resolver.resolve(case)
        self._update_operator_notification(case=case, report=report)
        return report

    def _update_operator_notification(
        self,
        *,
        case: ResolutionCase,
        report: ResolutionReport,
    ) -> None:
        """Maintain explicit operator notification state and observability events.

        Authority:
            docs/ORCHESTRATION-PLANE-ORCH-APP-ROUTING.md sections 8, 10, 12.2
        """

        existing = self._latest_operator_notification
        if report.status == "operator_required":
            notice = OperatorNotification(
                case_id=case.case_id,
                summary=report.summary,
                evidence_refs=report.evidence_refs,
                operator_message=report.operator_message,
            )
            if existing is not None and existing.case_id != case.case_id:
                self._emit_operator_case_resolved(case_id=existing.case_id, resolution="replaced")
            if existing != notice:
                self._emit_operator_case_opened(case=case, notice=notice)
            self._latest_operator_notification = notice
            return

        if existing is not None and existing.case_id == case.case_id:
            self._emit_operator_case_resolved(case_id=case.case_id, resolution=report.status)
            self._latest_operator_notification = None

    def _is_dispatch_blocked_by_gate(self) -> tuple[bool, str]:
        """Check whether dispatch is blocked by a paused operator state or dispatch recovery gate.

        Authority:
            docs/ORCHESTRATION-PLANE-OPERATOR-CONFLICT-RECOVERY.md sections 6.3, 6.4, 9
            docs/ORCHESTRATION-PLANE-ORCH-APP-ROUTING.md sections 6.3, 8, 10

        This method consults:
            1. The in-memory operator notification (if any) for paused routing state.
            2. The latest recovery report's dispatch_recovery_gate (if any).

        Returns:
            Tuple of (is_blocked, reason). If is_blocked is True, reason explains
            the block; if False, reason is the empty string.
        """
        # Check 1: Paused operator notification blocks dispatch.
        # Authority: ORCH-APP-ROUTING.md section 10.4 — "orch app enters a paused
        # operator-wait routing state" and "does not continue ordinary dispatch."
        notification = self._latest_operator_notification
        if notification is not None:
            paused_state = notification.paused_routing_state
            if paused_state != "active":
                return (
                    True,
                    (
                        f"dispatch blocked by paused operator notification: "
                        f"paused_routing_state={paused_state} "
                        f"case_id={notification.case_id} "
                        f"notification_id={notification.notification_id}"
                    ),
                )

        # Check 2: DispatchRecoveryGate blocks dispatch.
        # Authority: OPERATOR-CONFLICT-RECOVERY.md section 6.4, 9 —
        # "unsafe_dispatch_blocked stays true while restart recovery has not yet
        # reconstituted pending reconcile/operator state coherently."
        report = self._latest_recovery_report
        if report is not None:
            gate = report.dispatch_recovery_gate
            if gate is not None:
                if gate.unsafe_dispatch_blocked:
                    return (
                        True,
                        (
                            f"dispatch blocked by recovery gate: "
                            f"status={gate.status} reason={gate.reason} "
                            f"unsafe_dispatch_blocked={gate.unsafe_dispatch_blocked}"
                        ),
                    )

        return (False, "")

    def _is_complete_blocked_by_gate(self) -> tuple[bool, str]:
        """Check whether a recovery gate blocks duplicate completion.

        Authority:
            docs/ORCHESTRATION-PLANE-OPERATOR-CONFLICT-RECOVERY.md sections 6.4, 9
                "duplicate_complete_blocked stays true until reconcile closure is
                 durably re-established as merged or noop."

        This method is consulted BEFORE calling complete_step in
        route_terminal_execution to prevent duplicate complete when recovery
        state indicates the complete is not safe.

        Returns:
            Tuple of (is_blocked, reason). If is_blocked is True, reason explains
            the block; if False, reason is the empty string.
        """
        # Check 1: Paused operator notification blocks completion.
        # If operator notification is active but not resolved, we cannot
        # complete because the operator has not yet reviewed the issue.
        notification = self._latest_operator_notification
        if notification is not None:
            paused_state = notification.paused_routing_state
            if paused_state != "active":
                return (
                    True,
                    (
                        f"complete blocked by paused operator notification: "
                        f"paused_routing_state={paused_state} "
                        f"case_id={notification.case_id} "
                        f"notification_id={notification.notification_id}"
                    ),
                )

        # Check 2: DispatchRecoveryGate.duplicate_complete_blocked blocks completion.
        # Authority: OPERATOR-CONFLICT-RECOVERY.md section 9 —
        # "duplicate_complete_blocked stays true until reconcile closure is
        #  durably re-established as merged or noop."
        report = self._latest_recovery_report
        if report is not None:
            gate = report.dispatch_recovery_gate
            if gate is not None:
                if gate.duplicate_complete_blocked:
                    return (
                        True,
                        (
                            f"complete blocked by recovery gate: "
                            f"duplicate_complete_blocked={gate.duplicate_complete_blocked} "
                            f"status={gate.status} reason={gate.reason}"
                        ),
                    )

        return (False, "")

    def _emit_operator_case_opened(
        self,
        *,
        case: ResolutionCase,
        notice: OperatorNotification,
    ) -> None:
        """Emit explicit operator-required case-opened state."""

        step_id = case.blocked_step_ids[0] if case.blocked_step_ids else ""
        self._emit_event(
            kind="operator_case_opened",
            step_id=step_id or None,
            agent="operator",
            payload={
                "case_id": notice.case_id,
                "step_id": step_id,
                "resolution_status": "operator_required",
                "summary": notice.summary,
                "operator_message": notice.operator_message,
                "evidence_refs": notice.evidence_refs,
                "open_case_delta": 1,
            },
        )

    def _emit_operator_case_resolved(self, *, case_id: str, resolution: str) -> None:
        """Emit explicit operator-required case-resolved state."""

        self._emit_event(
            kind="operator_case_resolved",
            step_id=None,
            agent="operator",
            payload={
                "case_id": case_id,
                "resolution": resolution,
                "open_case_delta": -1,
            },
        )

    def route_resolution_case(
        self,
        case: ResolutionCase,
        *,
        agent: str | None = None,
    ) -> ControlDecision:
        """Apply resolver outcome after refreshing authoritative snapshots."""

        report = self.resolve_case(case)
        refreshed = self.refresh_snapshots(agent=agent)
        return self._control.apply_resolution(
            report=report,
            core=refreshed.core,
            roster=refreshed.roster,
            runtime=refreshed.runtime,
        )

    def build_dispatch_spec(
        self,
        *,
        step_id: str,
        role_hint: str | None = None,
    ) -> DispatchSpec:
        """Build a dispatch spec using role-profile policy."""

        return self._dispatch_coordinator.build_dispatch_spec(
            ControlDecision(
                kind="dispatch",
                reason="orchestration app dispatch",
                step_id=step_id,
                role=role_hint or self._config.default_agent,
            )
        )

    def dispatch_resolution_subtask(
        self,
        *,
        case_id: str,
        role_id: str,
        description: str,
        refs: tuple[str, ...] = (),
        run_id: str,
    ) -> tuple[DispatchSpec, str, str]:
        """Route resolver-owned subtask work through the shared runtime substrate.

        Authority:
            docs/ORCHESTRATION-PLANE-OPERATOR-CONFLICT-RECOVERY.md sections 6.3, 9
            docs/ORCHESTRATION-PLANE-ORCH-APP-ROUTING.md section 6.3

        Enforcement:
            Dispatch is blocked when the recovery gate or paused operator state
            indicates unsafe dispatch conditions. Resolution subtasks must not
            bypass dispatch recovery gates.
        """

        blocked, block_reason = self._is_dispatch_blocked_by_gate()
        if blocked:
            raise DispatchBlockedError(
                f"dispatch_resolution_subtask blocked by recovery gate: {block_reason}"
            )

        spec = self._dispatch_coordinator.build_resolution_subtask_spec(
            case_id=case_id,
            role_id=role_id,
            description=description,
            refs=refs,
        )
        workspace, execution_id = self._start_runtime_execution(
            run_id=run_id,
            step_id=spec.step_id or case_id,
            dispatch_spec=spec,
            mode="start",
        )
        return spec, workspace, execution_id

    def route_terminal_execution(
        self,
        *,
        step_id: str,
        execution_id: str,
        dispatch_spec: DispatchSpec,
        execution_result: ExecutionResult,
    ) -> ResolutionCase | None:
        """Route terminal execution results through reconcile and completion gates.

        Authority:
            docs/ORCHESTRATION-PLANE-OPERATOR-CONFLICT-RECOVERY.md sections 6.4, 9
            docs/ORCHESTRATION-PLANE-ORCH-APP-ROUTING.md section 6.3

        Enforcement:
            Before calling complete_step, this method explicitly consults the
            DispatchRecoveryGate's duplicate_complete_blocked flag and the paused
            operator notification state. If either blocks completion, a
            DuplicateCompleteBlockedError is raised instead of silently completing.
        """

        # Gate: consult paused operator state and DispatchRecoveryGate
        # before any completion path.
        # Authority: OPERATOR-CONFLICT-RECOVERY.md 6.4, 9
        complete_blocked, complete_reason = self._is_complete_blocked_by_gate()
        if complete_blocked:
            raise DuplicateCompleteBlockedError(
                f"route_terminal_execution: complete blocked for step {step_id}: {complete_reason}"
            )

        snapshots = self.refresh_snapshots(agent=dispatch_spec.role_id)
        if execution_result.status in ("fail", "stall", "transport_error"):
            return ResolutionCase(
                case_id=f"case-{generate_run_id()}",
                case_source="runtime_failure",
                reason=f"runtime non-closure: {execution_result.status}",
                summary=execution_result.output_summary,
                core=snapshots.core,
                roster=snapshots.roster,
                runtime=snapshots.runtime,
                blocked_step_ids=(step_id,),
            )

        reconcile_result = self._runtime.begin_reconcile(execution_id)
        if reconcile_result is None:
            raise ValueError(
                f"Reconcile did not produce a terminal result for execution {execution_id}"
            )
        if reconcile_result.status not in ("merged", "noop"):
            return self._build_reconcile_resolution_case(
                step_id=step_id,
                reconcile_result=reconcile_result,
                snapshots=snapshots,
            )

        review_case = self._normalize_review_case(
            raw_output=execution_result.output_summary,
            role_id=dispatch_spec.role_id,
            output_contract=dispatch_spec.output_contract,
            snapshots=snapshots,
        )
        if review_case is not None:
            return review_case

        # Second gate: re-check after reconcile/resolve paths, because
        # paused state might have been set during reconcile processing.
        complete_blocked_2, complete_reason_2 = self._is_complete_blocked_by_gate()
        if complete_blocked_2:
            raise DuplicateCompleteBlockedError(
                f"route_terminal_execution: complete blocked after reconcile for step {step_id}: "
                f"{complete_reason_2}"
            )

        can_complete, reason = self._runtime.can_complete(execution_id)
        if not can_complete:
            raise ValueError(reason)
        reconcile_disposition = self._runtime.reconcile_disposition(execution_id)
        if reconcile_disposition is None:
            raise ValueError("Reconcile disposition missing despite completion gate approval")
        self._core_adapter.complete_step(
            step_id,
            execution_result.output_summary,
            reconcile_disposition=reconcile_disposition,
        )
        return None

    def _build_reconcile_resolution_case(
        self,
        *,
        step_id: str,
        reconcile_result: ReconcileResult,
        snapshots: SnapshotBundle,
    ) -> ResolutionCase:
        case_source: ResolutionCaseSource = "runtime_failure"
        if reconcile_result.status == "merge_conflict":
            case_source = "merge_conflict"
        return ResolutionCase(
            case_id=f"case-{generate_run_id()}",
            case_source=case_source,
            reason=f"reconcile non-closure: {reconcile_result.status}",
            summary=reconcile_result.summary,
            core=snapshots.core,
            roster=snapshots.roster,
            runtime=snapshots.runtime,
            blocked_step_ids=(step_id,),
            artifact_refs=reconcile_result.artifact_refs,
        )

    def _normalize_review_case(
        self,
        *,
        raw_output: str,
        role_id: str,
        output_contract: str,
        snapshots: SnapshotBundle,
    ) -> ResolutionCase | None:
        if output_contract != "structured_review_result":
            return None
        try:
            parsed = self._parse_structured_review_result(raw_output)
        except ValueError:
            return build_review_parse_failure_case(
                raw_output=raw_output,
                role_id=role_id,
                case_id=f"case-{generate_run_id()}",
                core=snapshots.core,
                roster=snapshots.roster,
                runtime=snapshots.runtime,
            )
        return normalize_review_resolution_case(
            parsed,
            case_id=f"case-{generate_run_id()}",
            core=snapshots.core,
            roster=snapshots.roster,
            runtime=snapshots.runtime,
        )

    def _parse_structured_review_result(self, raw_output: str) -> StructuredReviewResult:
        """Parse strict JSON review output into StructuredReviewResult."""

        try:
            payload = json.loads(raw_output)
        except json.JSONDecodeError as exc:
            raise ValueError(f"review output is not valid JSON: {exc}") from exc
        if not isinstance(payload, dict):
            raise ValueError("review output must be a JSON object")
        review_outcome = payload.get("review_outcome")
        summary = payload.get("summary")
        findings = payload.get("findings", [])
        evidence_refs = payload.get("evidence_refs", [])
        valid_outcomes = {"pass", "needs_fix", "needs_replan", "operator_required"}
        if review_outcome not in valid_outcomes:
            raise ValueError(f"unknown review_outcome: {review_outcome!r}")
        if not isinstance(summary, str) or not summary.strip():
            raise ValueError("summary must be a non-empty string")
        if not isinstance(findings, list) or not all(isinstance(item, str) for item in findings):
            raise ValueError("findings must be a list[str]")
        if not isinstance(evidence_refs, list) or not all(
            isinstance(item, str) for item in evidence_refs
        ):
            raise ValueError("evidence_refs must be a list[str]")
        return StructuredReviewResult(
            review_outcome=review_outcome,
            summary=summary,
            findings=tuple(findings),
            evidence_refs=tuple(evidence_refs),
        )

    def _start_runtime_execution(
        self,
        *,
        run_id: str,
        step_id: str,
        dispatch_spec: DispatchSpec,
        mode: Literal["start", "resume", "recover"],
    ) -> tuple[str, str]:
        # Gate: consult paused operator state and DispatchRecoveryGate
        # before dispatch to runtime.
        # Authority: OPERATOR-CONFLICT-RECOVERY.md 6.3, 9
        blocked, block_reason = self._is_dispatch_blocked_by_gate()
        if blocked:
            raise DispatchBlockedError(
                f"_start_runtime_execution: dispatch blocked for step {step_id}: {block_reason}"
            )

        if dispatch_spec.source_kind == "step":
            authoritative_isolation = self._core_adapter.step_isolation(step_id)
            isolation_ref = f"isolation={authoritative_isolation.value}"
        else:
            isolation_ref = "isolation=default"
        request = ExecutionRequest(
            step_id=step_id,
            role=dispatch_spec.role_id,
            runner=dispatch_spec.runner,
            work_refs=(
                f"run_id={run_id}",
                f"mode={mode}",
                isolation_ref,
                f"execution_context={dispatch_spec.execution_context}",
                f"source_kind={dispatch_spec.source_kind}",
            ),
            session_id=run_id,
        )
        workspace = self._runtime.prepare(request)
        execution_id = self._runtime.start(request=request, workspace=workspace)
        return workspace, execution_id

    def _admit_start_and_persist_running(
        self,
        *,
        registry: RunRegistry,
        run_id: str,
        step_id: str,
        plan_path: str,
        agent: str,
        run_root: Path,
        runner: str,
        allowlist_text: str,
        mode: Literal["start", "resume", "recover"],
        current_run_id: str | None = None,
        event_payloads: tuple[tuple[OrchestrationEventKind, dict[str, object]], ...] = (),
    ) -> tuple[str, str]:
        """Persist admission/start/status and events in one ordered lock path.

        Authority:
            OPERATOR-CONFLICT-RECOVERY.md sections 6.3, 9
            ORCH-APP-ROUTING.md section 6.3

        Enforcement:
            Dispatch is blocked when the recovery gate or paused operator state
            indicates unsafe dispatch conditions. This method checks the gate
            before dispatching to the runtime.
        """

        # Gate: consult paused operator state and DispatchRecoveryGate
        # before dispatch to runtime in the admission path.
        # Authority: OPERATOR-CONFLICT-RECOVERY.md 6.3, 9
        blocked, block_reason = self._is_dispatch_blocked_by_gate()
        if blocked:
            raise DispatchBlockedError(
                f"_admit_start_and_persist_running: dispatch blocked for step {step_id}: "
                f"{block_reason}"
            )

        with registry.admission_guard():
            registry.assert_can_admit_same_plan(plan_path=plan_path, current_run_id=current_run_id)
            registry.save(
                RunRecord(
                    run_id=run_id,
                    step_id=step_id,
                    plan_path=plan_path,
                    agent=agent,
                    status="pending",
                    artifact_root=str(run_root),
                    output_summary=(
                        f"{mode} admission persisted before runtime start "
                        f"runner={runner} allowlist={allowlist_text}"
                    ),
                )
            )

            try:
                if mode == "start":
                    self._core_adapter.claim_step(step_id, agent, flow="normal")
                dispatch_spec = self.build_dispatch_spec(step_id=step_id, role_hint=agent)
                workspace, execution_id = self._start_runtime_execution(
                    run_id=run_id,
                    step_id=step_id,
                    dispatch_spec=dispatch_spec,
                    mode=mode,
                )
            except Exception as exc:
                now_ts = time.time()
                registry.save(
                    RunRecord(
                        run_id=run_id,
                        step_id=step_id,
                        plan_path=plan_path,
                        agent=agent,
                        status="fail",
                        artifact_root=str(run_root),
                        finished_at=now_ts,
                        output_summary=f"runtime {mode} failed after durable admission: {exc}",
                    )
                )
                raise

            registry.save(
                RunRecord(
                    run_id=run_id,
                    step_id=step_id,
                    plan_path=plan_path,
                    agent=agent,
                    status="running",
                    artifact_root=str(run_root),
                    output_summary=(
                        f"{mode}: run initialized with frozen snapshot "
                        f"runner={runner} allowlist={allowlist_text} "
                        f"workspace={workspace} execution_id={execution_id}"
                    ),
                )
            )

            try:
                for kind, payload in event_payloads:
                    self._emit_event(
                        kind=kind,
                        step_id=step_id,
                        agent=agent,
                        payload=payload,
                    )
            except Exception as exc:
                now_ts = time.time()
                registry.save(
                    RunRecord(
                        run_id=run_id,
                        step_id=step_id,
                        plan_path=plan_path,
                        agent=agent,
                        status="fail",
                        artifact_root=str(run_root),
                        finished_at=now_ts,
                        output_summary=f"event sequencing failed during {mode}: {exc}",
                    )
                )
                raise

            return workspace, execution_id

    # -----------------------------------------------------------------
    # Run / Resume / Recover / Prune
    # -----------------------------------------------------------------

    def run(
        self,
        step_id: str | None = None,
        agent: str | None = None,
    ) -> OrchestrationResult:
        """
        Start a new orchestration run.

        Authority: docs/ORCHESTRATION-PLANE-ARCHITECTURE.md section 6.1

        Args:
            step_id: Optional explicit step to run. None means auto-select next.
            agent: Optional agent name. None uses default.

        Returns:
            OrchestrationResult with run outcome.

        Raises:
            OSError: When frozen snapshot persistence fails.
        """
        resolved_config = self._effective_orchestration_config()
        resolved_agent = agent or self._config.default_agent
        try:
            resolved_step_id = self._resolve_step_id(step_id=step_id, agent=resolved_agent)
        except ValueError as exc:
            return OrchestrationResult(success=False, message=str(exc))
        if resolved_step_id is None:
            return OrchestrationResult(
                success=False,
                message="No claimable step available for run start",
            )

        registry = self._run_registry(config=resolved_config)

        run_id = generate_run_id()
        run_root = registry.run_artifact_root(run_id)
        frozen_snapshot = freeze_config(resolved_config, run_dir=run_root)
        self._write_continuity_bootstrap(
            run_id=run_id,
            step_id=resolved_step_id,
            frozen_config=frozen_snapshot.config,
            run_root=run_root,
        )

        allowlist_text = self._allowlist_text(frozen_snapshot.config.resolver.tool_allowlist)

        try:
            workspace, execution_id = self._admit_start_and_persist_running(
                registry=registry,
                run_id=run_id,
                step_id=resolved_step_id,
                plan_path=str(self._config.plan_path),
                agent=resolved_agent,
                run_root=run_root,
                runner=frozen_snapshot.config.runtime.default_runner,
                allowlist_text=allowlist_text,
                mode="start",
                event_payloads=(
                    (
                        "run_started",
                        {"run_id": run_id, "plan_path": str(self._config.plan_path)},
                    ),
                    ("run_status_changed", {"run_id": run_id, "status": "running"}),
                ),
            )
        except SamePlanAdmissionError as exc:
            return OrchestrationResult(
                success=False,
                message=f"Run admission denied: {exc}",
            )
        except Exception as exc:
            return OrchestrationResult(
                success=False,
                message=f"Runtime wiring failure during run start: {exc}",
                step_id=resolved_step_id,
                run_id=run_id,
            )
        self._append_log(
            " ".join(
                (
                    f"run_id={run_id}",
                    f"step_id={resolved_step_id}",
                    "status=running",
                    f"agent={resolved_agent}",
                    f"workspace={workspace}",
                    f"execution_id={execution_id}",
                )
            )
        )

        return OrchestrationResult(
            success=True,
            message=(
                f"Run initialized with frozen config snapshot ({frozen_snapshot.snapshot_path})"
            ),
            step_id=resolved_step_id,
            run_id=run_id,
        )

    def resume(
        self,
        run_id: str,
    ) -> OrchestrationResult:
        """
        Resume an existing run from artifacts.

        Authority: docs/ORCHESTRATION-PLANE-ARCHITECTURE.md section 6.2

        Args:
            run_id: The run identifier to resume.

        Returns:
            OrchestrationResult with resume outcome.

        Raises:
            FileNotFoundError: If the run snapshot is missing.
        """
        ambient = self._effective_orchestration_config()
        registry = self._run_registry(config=ambient)
        record = registry.by_id(run_id)
        if record is None:
            return OrchestrationResult(
                success=False, message=f"Run not found: {run_id}", run_id=run_id
            )

        run_root = registry.run_artifact_root(run_id)
        snapshot_path = run_root / "config.snapshot.yaml"
        if not snapshot_path.exists():
            return OrchestrationResult(
                success=False,
                message=(
                    "Resume refused: frozen config snapshot missing for authoritative run "
                    f"{run_id} ({snapshot_path})"
                ),
                step_id=record.step_id,
                run_id=run_id,
            )

        frozen = load_frozen_snapshot(snapshot_path)
        if not frozen.continuity.resume_enabled:
            return OrchestrationResult(
                success=False,
                message=f"Resume disabled by frozen config snapshot for run {run_id}",
                step_id=record.step_id,
                run_id=run_id,
            )

        if self._binding_signature(frozen) != self._binding_signature(ambient):
            return OrchestrationResult(
                success=False,
                message=(
                    "Resume refused: ambient configuration differs from authoritative "
                    f"frozen snapshot for run {run_id}"
                ),
                step_id=record.step_id,
                run_id=run_id,
            )

        decision = self._evaluate_recovery_decision(
            run_id=run_id,
            expected_step_id=record.step_id,
            frozen_config=frozen,
            registry=registry,
        )
        if decision.outcome == "fresh_start_required":
            return OrchestrationResult(
                success=False,
                message=(
                    "Resume refused: fresh_start_required; run heartbeat is stale or unknown. "
                    f"Use 'vectl orch recover {run_id}' to terminalize before restart. "
                    f"artifact_families={','.join(decision.artifact_families)}"
                ),
                step_id=record.step_id,
                run_id=run_id,
            )
        if decision.outcome != "resume_safe":
            return OrchestrationResult(
                success=False,
                message=(
                    "Resume refused: continuity classification is "
                    f"{decision.outcome}. details={decision.message} "
                    f"artifact_families={','.join(decision.artifact_families)}"
                ),
                step_id=record.step_id,
                run_id=run_id,
            )

        replay_result = self._replay_projection_for_run(
            run_id=run_id,
            step_id=record.step_id,
            run_root=run_root,
        )
        if replay_result is not None:
            return replay_result

        try:
            self._admit_start_and_persist_running(
                registry=registry,
                run_id=run_id,
                step_id=record.step_id,
                plan_path=record.plan_path or str(self._config.plan_path),
                agent=record.agent or self._config.default_agent,
                run_root=run_root,
                runner=frozen.runtime.default_runner,
                allowlist_text=self._allowlist_text(frozen.resolver.tool_allowlist),
                mode="resume",
                current_run_id=run_id,
            )
        except Exception as exc:
            return OrchestrationResult(
                success=False,
                message=f"Runtime wiring failure during resume: {exc}",
                step_id=record.step_id,
                run_id=run_id,
            )
        return OrchestrationResult(
            success=True,
            message=(
                "resume_safe: Run resumed from frozen snapshot with replayed projections: "
                f"{snapshot_path}"
            ),
            step_id=record.step_id,
            run_id=run_id,
        )

    def _effective_orchestration_config(self) -> OrchestrationConfig:
        if self._config.orchestration_config is not None:
            return self._config.orchestration_config
        return OrchestrationConfig(plan_path=self._config.plan_path)

    def _run_registry(self, config: OrchestrationConfig) -> RunRegistry:
        if self._config.run_store_root is not None:
            return RunRegistry(store_root=self._config.run_store_root)
        return RunRegistry(store_root=config.runtime.artifact_root)

    def _control_channel(
        self, config: OrchestrationConfig | None = None
    ) -> FilesystemControlChannel:
        resolved = config if config is not None else self._effective_orchestration_config()
        registry = self._run_registry(config=resolved)
        return FilesystemControlChannel(
            runs_root=registry.store_root,
            max_pending_actions=resolved.operator.max_pending_actions,
        )

    def _resolve_step_id(self, step_id: str | None, agent: str) -> str | None:
        if step_id is not None:
            return step_id
        snapshot = self._core_adapter.snapshot(agent=agent)
        if not snapshot.claimable_step_ids:
            return None
        if len(snapshot.claimable_step_ids) > 1:
            joined = ", ".join(snapshot.claimable_step_ids)
            raise ValueError(
                "Run selection is ambiguous: multiple claimable steps available; "
                f"provide explicit step_id ({joined})"
            )
        return next(iter(snapshot.claimable_step_ids), None)

    def _events_path(self) -> Path:
        resolved = self._effective_orchestration_config()
        root = self._config.run_store_root or resolved.runtime.artifact_root
        return Path(root) / "events.jsonl"

    def _logs_path(self) -> Path:
        resolved = self._effective_orchestration_config()
        root = self._config.run_store_root or resolved.runtime.artifact_root
        return Path(root) / "orchestration.log"

    def _emit_event(
        self,
        *,
        kind: OrchestrationEventKind,
        payload: dict[str, object],
        step_id: str | None = None,
        agent: str | None = None,
    ) -> None:
        envelope = OrchestrationEventEnvelope(
            kind=kind,
            timestamp=datetime.now(timezone.utc),
            step_id=step_id,
            payload=payload,
            agent=agent,
        )
        self._event_sink.emit(envelope)

    def _append_log(self, line: str) -> None:
        timestamp = datetime.now(timezone.utc).isoformat()
        with self._text_log_path.open("a", encoding="utf-8") as handle:
            handle.write(f"{timestamp} {line}\n")

    def _active_run_ids(self, step_id: str | None = None) -> tuple[str, ...]:
        registry = self._run_registry(config=self._effective_orchestration_config())
        candidates: list[RunRecord] = []
        for status in ("running", "pending", "stall"):
            candidates.extend(registry.by_status(status))
        if step_id is not None:
            candidates = [record for record in candidates if record.step_id == step_id]
        unique = tuple(dict.fromkeys(record.run_id for record in candidates))
        return unique

    def _resolve_run_selection(
        self,
        *,
        run_id: str | None,
        step_id: str | None = None,
    ) -> tuple[str | None, str | None]:
        registry = self._run_registry(config=self._effective_orchestration_config())
        if run_id is not None:
            found = registry.by_id(run_id)
            if found is None:
                return None, f"Run not found: {run_id}"
            return run_id, None

        active = self._active_run_ids(step_id=step_id)
        if not active:
            return None, "No active run available; provide explicit run_id"
        if len(active) > 1:
            return (
                None,
                "Run selection is ambiguous: multiple active runs available; "
                f"provide explicit run_id ({', '.join(active)})",
            )
        return next(iter(active), None), None

    def _binding_signature(self, config: OrchestrationConfig) -> dict[str, object]:
        return {
            "runtime": asdict(config.runtime),
            "resolver": {
                "enabled": config.resolver.enabled,
                "invocation_timeout_seconds": config.resolver.invocation_timeout_seconds,
                "max_tool_calls_per_invocation": config.resolver.max_tool_calls_per_invocation,
                "max_tool_argument_bytes": config.resolver.max_tool_argument_bytes,
                "tool_allowlist": asdict(config.resolver.tool_allowlist),
            },
            "continuity": asdict(config.continuity),
        }

    def _allowlist_text(self, allowlist: ResolverToolAllowlist) -> str:
        if not allowlist.allowed_tool_families:
            return "deny-all"
        return ",".join(allowlist.allowed_tool_families)

    def _continuity_root(self, run_root: Path) -> Path:
        return run_root / "continuity"

    def _capability_fingerprint(self, config: OrchestrationConfig) -> str:
        canonical = json.dumps(self._binding_signature(config), sort_keys=True, default=str)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def _write_continuity_bootstrap(
        self,
        *,
        run_id: str,
        step_id: str,
        frozen_config: OrchestrationConfig,
        run_root: Path,
    ) -> None:
        continuity_root = self._continuity_root(run_root)
        continuity_root.mkdir(parents=True, exist_ok=True)
        now_ts = time.time()
        capability_fingerprint = self._capability_fingerprint(frozen_config)

        ledger_path = continuity_root / "ledger.json"
        journal_path = continuity_root / "journal.jsonl"
        capability_path = continuity_root / "capability.snapshot.json"
        heartbeat_path = run_root / "heartbeat.json"

        ledger_payload = {
            "run_id": run_id,
            "step_id": step_id,
            "session_id": run_id,
            "runner": frozen_config.runtime.default_runner,
            "status": "active",
            "created_at": now_ts,
            "updated_at": now_ts,
            "capability_snapshot": capability_fingerprint,
            "replay_envelope": frozen_config.continuity.replay_safety,
        }
        ledger_path.write_text(json.dumps(ledger_payload, sort_keys=True) + "\n", encoding="utf-8")

        journal_entry = {
            "event_id": f"{run_id}-started",
            "run_id": run_id,
            "step_id": step_id,
            "session_id": run_id,
            "runner": frozen_config.runtime.default_runner,
            "event_type": "run_started",
            "timestamp": now_ts,
            "outcome": None,
            "details": ["bootstrap"],
        }
        with journal_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(journal_entry, sort_keys=True))
            handle.write("\n")

        capability_path.write_text(
            json.dumps(
                {
                    "run_id": run_id,
                    "step_id": step_id,
                    "fingerprint": capability_fingerprint,
                    "created_at": now_ts,
                },
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        heartbeat_path.write_text(
            json.dumps(
                {
                    "run_id": run_id,
                    "pid": 0,
                    "host_id": "orchestration",
                    "started_at": now_ts,
                    "last_heartbeat_at": now_ts,
                },
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )

    def _replay_projection_for_run(
        self,
        *,
        run_id: str,
        step_id: str,
        run_root: Path,
    ) -> OrchestrationResult | None:
        try:
            events = load_event_jsonl(self._events_path())
        except EventCorruptionError as exc:
            return OrchestrationResult(
                success=False,
                message=(
                    "corrupt_blocking: transcript/event corruption stopped replay. "
                    f"line={exc.line_no} reason={exc.reason}"
                ),
                step_id=step_id,
                run_id=run_id,
            )

        relevant: list[OrchestrationEventEnvelope] = []
        for event in events:
            payload_run_id = None
            payload = event.payload or {}
            if "run_id" in payload:
                payload_run_id = str(payload.get("run_id"))
            if payload_run_id is not None and payload_run_id != run_id:
                continue
            if payload_run_id is None and event.step_id not in {None, step_id}:
                continue
            relevant.append(event)

        replay_events_to_artifacts(events=tuple(relevant), artifact_root=run_root, run_id=run_id)
        return None

    def _evaluate_recovery_decision(
        self,
        *,
        run_id: str,
        expected_step_id: str,
        frozen_config: OrchestrationConfig,
        registry: RunRegistry,
    ) -> _RecoveryDecision:
        artifact_families = (
            "frozen_config",
            "canonical_events",
            "projections",
            "continuity_ledger",
            "continuity_journal",
            "capability_snapshot",
            "operator_receipts",
            "heartbeat",
        )
        run_root = registry.run_artifact_root(run_id)
        continuity_root = self._continuity_root(run_root)
        ledger_path = continuity_root / "ledger.json"
        journal_path = continuity_root / "journal.jsonl"
        capability_path = continuity_root / "capability.snapshot.json"

        if not ledger_path.exists() or not journal_path.exists() or not capability_path.exists():
            return self._RecoveryDecision(
                outcome="corrupt_blocking",
                message=(
                    "required continuity artifacts missing "
                    f"ledger={ledger_path.exists()} journal={journal_path.exists()} "
                    f"capability={capability_path.exists()}"
                ),
                artifact_families=artifact_families,
            )

        try:
            ledger_payload = json.loads(ledger_path.read_text(encoding="utf-8"))
            capability_payload = json.loads(capability_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            return self._RecoveryDecision(
                outcome="corrupt_blocking",
                message=f"unparsable continuity JSON: {exc}",
                artifact_families=artifact_families,
            )

        if not isinstance(ledger_payload, dict) or not isinstance(capability_payload, dict):
            return self._RecoveryDecision(
                outcome="corrupt_blocking",
                message="continuity artifacts must be JSON objects",
                artifact_families=artifact_families,
            )

        ledger_step_id = str(ledger_payload.get("step_id", ""))
        ledger_run_id = str(ledger_payload.get("run_id", ""))
        if ledger_step_id != expected_step_id or ledger_run_id != run_id:
            return self._RecoveryDecision(
                outcome="blocking_divergence",
                message=(
                    "continuity ledger disagrees with run registry "
                    f"ledger.step_id={ledger_step_id} expected.step_id={expected_step_id} "
                    f"ledger.run_id={ledger_run_id} expected.run_id={run_id}"
                ),
                artifact_families=artifact_families,
            )

        try:
            journal_lines = [
                line
                for line in journal_path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            journal_entries = [json.loads(line) for line in journal_lines]
        except json.JSONDecodeError as exc:
            return self._RecoveryDecision(
                outcome="corrupt_blocking",
                message=f"unparsable continuity journal: {exc}",
                artifact_families=artifact_families,
            )

        for entry in journal_entries:
            if not isinstance(entry, dict):
                return self._RecoveryDecision(
                    outcome="corrupt_blocking",
                    message="continuity journal entry must be JSON object",
                    artifact_families=artifact_families,
                )
            if str(entry.get("step_id", "")) != expected_step_id:
                return self._RecoveryDecision(
                    outcome="blocking_divergence",
                    message=(
                        "continuity journal disagrees with run registry "
                        f"entry.step_id={entry.get('step_id')} expected.step_id={expected_step_id}"
                    ),
                    artifact_families=artifact_families,
                )

        expected_capability = self._capability_fingerprint(frozen_config)
        recorded_capability = str(capability_payload.get("fingerprint", ""))
        if not recorded_capability or recorded_capability != expected_capability:
            return self._RecoveryDecision(
                outcome="ambiguous_blocking",
                message=(
                    "capability snapshot mismatch for resume authority "
                    f"recorded={recorded_capability or '<missing>'} expected={expected_capability}"
                ),
                artifact_families=artifact_families,
            )

        receipts_state = self._receipt_state(run_root)
        if receipts_state == "ambiguous":
            return self._RecoveryDecision(
                outcome="ambiguous_blocking",
                message="operator receipts are conflicting across applied/rejected statuses",
                artifact_families=artifact_families,
            )
        if receipts_state == "corrupt":
            return self._RecoveryDecision(
                outcome="corrupt_blocking",
                message="operator receipts contain malformed JSON payloads",
                artifact_families=artifact_families,
            )

        try:
            liveness = registry.liveness_for_run(
                run_id,
                stale_after_seconds=float(
                    frozen_config.observability.heartbeat_stale_threshold_seconds
                ),
            )
        except CorruptJSONLError as exc:
            return self._RecoveryDecision(
                outcome="corrupt_blocking",
                message=f"heartbeat artifact is corrupt: {exc}",
                artifact_families=artifact_families,
            )

        if liveness in {"stale", "unknown"}:
            return self._RecoveryDecision(
                outcome="fresh_start_required",
                message=f"liveness={liveness}; continuity requires terminalization before restart",
                artifact_families=artifact_families,
            )

        return self._RecoveryDecision(
            outcome="resume_safe",
            message="heartbeat alive and continuity artifacts are coherent",
            artifact_families=artifact_families,
        )

    def _receipt_state(self, run_root: Path) -> Literal["ok", "ambiguous", "corrupt"]:
        control_root = run_root / "control" / "actions"
        applied = control_root / "applied"
        rejected = control_root / "rejected"
        if not applied.exists() and not rejected.exists():
            return "ok"

        def _load_action_ids(directory: Path) -> tuple[set[str], bool]:
            ids: set[str] = set()
            corrupt = False
            if not directory.exists():
                return ids, corrupt
            for file_path in directory.glob("*.json"):
                try:
                    payload = json.loads(file_path.read_text(encoding="utf-8"))
                except json.JSONDecodeError:
                    corrupt = True
                    continue
                if not isinstance(payload, dict):
                    corrupt = True
                    continue
                action_id = str(payload.get("action_id", "")).strip()
                if not action_id:
                    corrupt = True
                    continue
                ids.add(action_id)
            return ids, corrupt

        applied_ids, applied_corrupt = _load_action_ids(applied)
        rejected_ids, rejected_corrupt = _load_action_ids(rejected)
        if applied_corrupt or rejected_corrupt:
            return "corrupt"
        if applied_ids.intersection(rejected_ids):
            return "ambiguous"
        return "ok"

    def _quarantine_for_fresh_start(
        self,
        *,
        run_root: Path,
        run_id: str,
        reason: str,
    ) -> tuple[tuple[str, ...], bool]:
        continuity_root = self._continuity_root(run_root)
        quarantine_root = continuity_root / "quarantine"
        quarantine_root.mkdir(parents=True, exist_ok=True)
        manifest_path = quarantine_root / "manifest.jsonl"
        copied_paths: list[str] = []
        source_artifact_paths: list[Path] = []
        for relative in (
            "ledger.json",
            "journal.jsonl",
            "capability.snapshot.json",
            "../heartbeat.json",
        ):
            source = (continuity_root / relative).resolve()
            if not source.exists() or not source.is_file():
                continue
            destination = quarantine_root / source.name
            shutil.copy2(source, destination)
            copied_paths.append(str(destination))
            source_artifact_paths.append(source)
            manifest_entry = {
                "run_id": run_id,
                "classification": "fresh_start_required",
                "reason": reason,
                "original_path": str(source),
                "quarantine_destination": str(destination),
                "audit_timestamp": datetime.now(timezone.utc).isoformat(),
            }
            with manifest_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(manifest_entry, sort_keys=True))
                handle.write("\n")
        no_silent_deletion_preserved = all(source.exists() for source in source_artifact_paths)
        return tuple(copied_paths), no_silent_deletion_preserved

    def _recover_runtime_state(
        self,
        *,
        record: RunRecord,
    ) -> RuntimeRecoveryRecord | None:
        """Normalize durable runtime recovery state for restart reporting.

        Authority:
            docs/ORCHESTRATION-PLANE-OPERATOR-CONFLICT-RECOVERY.md sections 6.2, 6.3, 6.4

        Recovery preserves the persisted reconcile/operator barrier even when the
        referenced worktree path is relative or currently absent. The stored
        path remains authoritative; absolute normalization is added only when it
        can be done without discarding the durable value.
        """

        runtime_state = record.runtime_state
        if runtime_state is None:
            return None

        worktree_path_text = runtime_state.worktree_path
        worktree_path = Path(worktree_path_text)
        if not worktree_path.is_absolute() and worktree_path_text:
            plan_root = self._config.plan_path.parent
            worktree_path = (plan_root / worktree_path).resolve()
            return replace(runtime_state, worktree_path=str(worktree_path))
        return runtime_state

    def _recover_dispatch_recovery_gate(
        self,
        *,
        record: RunRecord,
        runtime_state: RuntimeRecoveryRecord | None,
        operator_notifications: tuple[OperatorNotificationRecord, ...],
    ) -> DispatchRecoveryGate | None:
        """Recover durable dispatch barrier from authoritative persisted state.

        Authority:
            docs/ORCHESTRATION-PLANE-OPERATOR-CONFLICT-RECOVERY.md sections 6.3, 6.4, 9
            docs/ORCHESTRATION-PLANE-ORCH-APP-ROUTING.md section 6.3
        """

        if record.dispatch_recovery_gate is not None:
            return record.dispatch_recovery_gate

        reconcile_state = None if runtime_state is None else runtime_state.reconcile_state
        blocked_case_id = operator_notifications[0].case_id if operator_notifications else None
        if reconcile_state is not None and reconcile_state.status not in {"merged", "noop"}:
            blocked_execution_id = reconcile_state.execution_id
            if runtime_state is not None and runtime_state.execution_id:
                blocked_execution_id = runtime_state.execution_id
            return DispatchRecoveryGate(
                status="blocked_pending_reconcile",
                reason=(
                    "reconcile state recovered from durable metadata requires explicit closure "
                    f"before dispatch/complete may resume ({reconcile_state.status})"
                ),
                duplicate_complete_blocked=True,
                unsafe_dispatch_blocked=True,
                blocked_on_execution_id=blocked_execution_id,
                blocked_on_case_id=blocked_case_id,
            )

        if operator_notifications:
            return DispatchRecoveryGate(
                status="blocked_pending_operator",
                reason="operator notification recovered from durable metadata remains unresolved",
                duplicate_complete_blocked=True,
                unsafe_dispatch_blocked=True,
                blocked_on_execution_id=None
                if runtime_state is None
                else runtime_state.execution_id,
                blocked_on_case_id=blocked_case_id,
            )

        return None

    def _recover_operator_notification_surface(
        self,
        *,
        operator_notifications: tuple[OperatorNotificationRecord, ...],
        runtime_state: RuntimeRecoveryRecord | None,
    ) -> None:
        """Reconstruct in-memory operator pause surface from durable metadata.

        Authority:
            docs/ORCHESTRATION-PLANE-OPERATOR-CONFLICT-RECOVERY.md sections 4.4, 4.5, 6.3
            docs/ORCHESTRATION-PLANE-ORCH-APP-ROUTING.md sections 8.2, 10
        """

        if not operator_notifications:
            self._latest_operator_notification = None
            return

        notification = operator_notifications[0]
        self._latest_operator_notification = OperatorNotification(
            notification_id=notification.notification_id,
            case_id=notification.case_id,
            run_id=notification.run_id
            or (None if runtime_state is None else runtime_state.session_id),
            kind=notification.kind,
            status=notification.status,
            summary=notification.summary,
            evidence_refs=notification.evidence_refs,
            operator_message=notification.operator_message,
            paused_routing_state=notification.paused_routing_state,
            created_at=notification.created_at,
            updated_at=notification.updated_at,
        )

    def recover(
        self,
        step_id: str | None = None,
        dry_run: bool = False,
    ) -> OrchestrationResult:
        """
        Recover orchestration state from continuity artifacts.

        Authority: docs/ORCHESTRATION-PLANE-MIGRATION.md section 4

        Args:
            step_id: Optional step to recover. None means recover all.

        Returns:
            OrchestrationResult with recovery outcome.
        """
        # Legacy bridge import check: blocks recovery when imported runs have incomplete continuity
        run_results = self.runs(step_id=step_id)
        blocking_imports = [
            result
            for result in run_results
            if result.source == "legacy_imported" and result.continuity_blocker
        ]
        if blocking_imports:
            blockers = ", ".join(
                f"{result.run_id}:{result.continuity_blocker}" for result in blocking_imports
            )
            report = RecoveryReport(
                outcome=RecoveryOutcome.BLOCKED,
                message=(
                    "Recovery blocked: imported legacy continuity minimums are incomplete; "
                    f"operator migration action required ({blockers})"
                ),
                step_id=step_id,
                actions=(
                    RecoveryAction(
                        action_type="operator_migration_required",
                        description="Imported legacy continuity minimums are incomplete.",
                    ),
                ),
                operator_message=(
                    "Resolve imported legacy continuity minimum blockers before recovery."
                ),
                gate_open_allowed=False,
                no_silent_deletion_preserved=True,
            )
            return self._recovery_result_from_report(report)

        ambient = self._effective_orchestration_config()
        registry = self._run_registry(config=ambient)

        candidates: list[RunRecord] = []
        for status in ("running", "pending", "stall"):
            candidates.extend(registry.by_status(status))
        if step_id is not None:
            candidates = [record for record in candidates if record.step_id == step_id]
        if not candidates:
            report = RecoveryReport(
                outcome=RecoveryOutcome.NO_ARTIFACTS,
                message="no_artifacts: no active run artifacts require recovery",
            )
            return self._recovery_result_from_report(report)

        blocking: list[str] = []
        recover_and_resume: list[str] = []
        fresh_start_terminalized: list[str] = []
        blocked_artifact_paths: list[str] = []
        quarantined_artifact_paths: list[str] = []
        no_silent_deletion_preserved = True
        recovery_actions: list[RecoveryAction] = []
        recovered_runtime_state: RuntimeRecoveryRecord | None = None
        recovered_operator_notifications: tuple[OperatorNotificationRecord, ...] = ()
        recovered_dispatch_recovery_gate: DispatchRecoveryGate | None = None
        for record in candidates:
            run_root = registry.run_artifact_root(record.run_id)
            snapshot_path = run_root / "config.snapshot.yaml"
            if not snapshot_path.exists():
                blocking.append(
                    f"run_id={record.run_id} outcome=corrupt_blocking missing={snapshot_path}"
                )
                blocked_artifact_paths.append(str(snapshot_path))
                continue

            frozen = load_frozen_snapshot(snapshot_path)
            decision = self._evaluate_recovery_decision(
                run_id=record.run_id,
                expected_step_id=record.step_id,
                frozen_config=frozen,
                registry=registry,
            )
            recovered_runtime_state = self._recover_runtime_state(record=record)
            recovered_operator_notifications = record.operator_notifications
            recovered_dispatch_recovery_gate = self._recover_dispatch_recovery_gate(
                record=record,
                runtime_state=recovered_runtime_state,
                operator_notifications=recovered_operator_notifications,
            )
            if decision.outcome == "resume_safe":
                if dry_run:
                    recover_and_resume.append(
                        f"run_id={record.run_id} outcome=recover_and_resume dry_run=true "
                        f"artifact_families={','.join(decision.artifact_families)}"
                    )
                    recovery_actions.append(
                        RecoveryAction(
                            action_type="recover_and_resume",
                            step_id=record.step_id,
                            description="Dry-run validated recover-and-resume path.",
                            artifacts=decision.artifact_families,
                        )
                    )
                    continue
                replay_result = self._replay_projection_for_run(
                    run_id=record.run_id,
                    step_id=record.step_id,
                    run_root=run_root,
                )
                if replay_result is not None:
                    replay_message = replay_result.message
                    blocking.append(
                        f"run_id={record.run_id} outcome=corrupt_blocking detail={replay_message}"
                    )
                    blocked_artifact_paths.append(str(run_root / "state" / "latest.json"))
                    continue
                try:
                    registry.save(
                        RunRecord(
                            run_id=record.run_id,
                            step_id=record.step_id,
                            plan_path=record.plan_path,
                            agent=record.agent,
                            status="pending",
                            artifact_root=str(run_root),
                            output_summary=(
                                "recover admission persisted before runtime start "
                                f"runner={frozen.runtime.default_runner}"
                            ),
                        )
                    )
                    dispatch_spec = self.build_dispatch_spec(
                        step_id=record.step_id,
                        role_hint=record.agent or self._config.default_agent,
                    )
                    workspace, execution_id = self._start_runtime_execution(
                        run_id=record.run_id,
                        step_id=record.step_id,
                        dispatch_spec=dispatch_spec,
                        mode="recover",
                    )
                except Exception as exc:
                    now_ts = time.time()
                    registry.save(
                        RunRecord(
                            run_id=record.run_id,
                            step_id=record.step_id,
                            plan_path=record.plan_path,
                            agent=record.agent,
                            status="fail",
                            artifact_root=str(run_root),
                            finished_at=now_ts,
                            output_summary=(
                                f"runtime recover failed after durable pending save: {exc}"
                            ),
                        )
                    )
                    blocking.append(
                        " ".join(
                            (
                                f"run_id={record.run_id}",
                                "outcome=runtime_wiring_failed",
                                f"detail={exc}",
                            )
                        )
                    )
                    blocked_artifact_paths.append(str(run_root))
                    continue
                registry.save(
                    RunRecord(
                        run_id=record.run_id,
                        step_id=record.step_id,
                        plan_path=record.plan_path,
                        agent=record.agent,
                        status="running",
                        artifact_root=str(run_root),
                        output_summary="recover_and_resume: continuity and event replay succeeded "
                        "from durable artifacts "
                        f"workspace={workspace} execution_id={execution_id}",
                    )
                )
                recover_and_resume.append(
                    f"run_id={record.run_id} outcome=recover_and_resume "
                    f"artifact_families={','.join(decision.artifact_families)}"
                )
                recovery_actions.append(
                    RecoveryAction(
                        action_type="recover_and_resume",
                        step_id=record.step_id,
                        description=(
                            "Recovered continuity and replayed events from durable artifacts."
                        ),
                        artifacts=decision.artifact_families,
                    )
                )
                continue

            if decision.outcome == "fresh_start_required":
                if dry_run:
                    fresh_start_terminalized.append(
                        f"run_id={record.run_id} outcome=fresh_start_required dry_run=true "
                        f"artifact_families={','.join(decision.artifact_families)}"
                    )
                    recovery_actions.append(
                        RecoveryAction(
                            action_type="quarantine_dry_run",
                            step_id=record.step_id,
                            description="Dry-run validated fresh-start quarantine path.",
                            artifacts=decision.artifact_families,
                        )
                    )
                    continue
                quarantined, preserved = self._quarantine_for_fresh_start(
                    run_root=run_root,
                    run_id=record.run_id,
                    reason=decision.message,
                )
                current_core = self._core_adapter.snapshot(agent=record.agent)
                if record.step_id in current_core.in_progress_step_ids:
                    self._core_adapter.defer_step(record.step_id)
                no_silent_deletion_preserved = no_silent_deletion_preserved and preserved
                quarantined_artifact_paths.extend(quarantined)
                now_ts = time.time()
                registry.save(
                    RunRecord(
                        run_id=record.run_id,
                        step_id=record.step_id,
                        plan_path=record.plan_path,
                        agent=record.agent,
                        status="fail",
                        artifact_root=str(run_root),
                        finished_at=now_ts,
                        output_summary=(
                            "fresh_start_required: terminalized for same-plan restart. "
                            f"quarantined={len(quarantined)}"
                        ),
                    )
                )
                self._emit_event(
                    kind="run_final",
                    step_id=record.step_id,
                    agent=record.agent,
                    payload={"run_id": record.run_id, "final_status": "fresh_start_required"},
                )
                fresh_start_terminalized.append(
                    f"run_id={record.run_id} outcome=fresh_start_required terminalized=true "
                    f"quarantine={len(quarantined)}"
                )
                recovery_actions.append(
                    RecoveryAction(
                        action_type="quarantine_and_terminalize",
                        step_id=record.step_id,
                        description=(
                            "Quarantined stale artifacts and terminalized run for fresh start."
                        ),
                        artifacts=quarantined,
                    )
                )
                continue

            blocking.append(
                f"run_id={record.run_id} outcome={decision.outcome} detail={decision.message} "
                f"artifact_families={','.join(decision.artifact_families)}"
            )
            blocked_artifact_paths.append(str(run_root / "continuity"))
            recovery_actions.append(
                RecoveryAction(
                    action_type="blocked",
                    step_id=record.step_id,
                    description=decision.message,
                    artifacts=decision.artifact_families,
                )
            )

        if blocking:
            report = RecoveryReport(
                outcome=RecoveryOutcome.BLOCKED,
                message=("recovery_blocked: " + " | ".join(blocking)),
                step_id=step_id,
                actions=tuple(recovery_actions),
                blocked_artifact_paths=tuple(blocked_artifact_paths),
                quarantined_artifact_paths=tuple(quarantined_artifact_paths),
                operator_message=(
                    "Resolve blocking continuity artifacts before recovery gate can open."
                ),
                gate_open_allowed=False,
                no_silent_deletion_preserved=no_silent_deletion_preserved,
                runtime_state=recovered_runtime_state,
                operator_notifications=recovered_operator_notifications,
                dispatch_recovery_gate=recovered_dispatch_recovery_gate,
            )
            self._recover_operator_notification_surface(
                operator_notifications=recovered_operator_notifications,
                runtime_state=recovered_runtime_state,
            )
            return self._recovery_result_from_report(report)

        details = [*recover_and_resume, *fresh_start_terminalized]
        report = RecoveryReport(
            outcome=(
                RecoveryOutcome.QUARANTINED
                if fresh_start_terminalized
                else RecoveryOutcome.RECOVERED
            ),
            message=(
                "recovery_complete: " + (" | ".join(details) if details else "no actions required")
            ),
            step_id=step_id,
            actions=tuple(recovery_actions),
            blocked_artifact_paths=tuple(blocked_artifact_paths),
            quarantined_artifact_paths=tuple(quarantined_artifact_paths),
            gate_open_allowed=not bool(fresh_start_terminalized),
            no_silent_deletion_preserved=bool(quarantined_artifact_paths)
            and no_silent_deletion_preserved,
            runtime_state=recovered_runtime_state,
            operator_notifications=recovered_operator_notifications,
            dispatch_recovery_gate=recovered_dispatch_recovery_gate,
        )
        self._recover_operator_notification_surface(
            operator_notifications=recovered_operator_notifications,
            runtime_state=recovered_runtime_state,
        )
        return self._recovery_result_from_report(report)

    def _recovery_result_from_report(self, report: RecoveryReport) -> OrchestrationResult:
        """Convert canonical RecoveryReport into OrchestrationResult surface."""

        self._latest_recovery_report = report
        success = report.outcome in {
            RecoveryOutcome.RECOVERED,
            RecoveryOutcome.QUARANTINED,
            RecoveryOutcome.NO_ARTIFACTS,
        }
        return OrchestrationResult(
            success=success,
            message=report.message,
            step_id=report.step_id,
            recovery_report=report,
        )

    @dataclass(frozen=True)
    class _RecoveryDecision:
        outcome: Literal[
            "resume_safe",
            "recover_and_resume",
            "fresh_start_required",
            "blocking_divergence",
            "corrupt_blocking",
            "ambiguous_blocking",
        ]
        message: str
        artifact_families: tuple[str, ...]

    def runs(
        self,
        step_id: str | None = None,
        limit: int = 100,
    ) -> tuple[RunResult, ...]:
        """
        List runs, optionally filtered by step.

        Authority: docs/ORCHESTRATION-PLANE-INTERFACES.md (shared)

        Args:
            step_id: Optional step filter.
            limit: Maximum number of runs to return.

        Returns:
            Tuple of RunResult for matching runs.

        Raises:
            OSError: Propagates storage read failures from the run registry.
        """
        registry = self._run_registry(config=self._effective_orchestration_config())
        inspect_query = InspectQuery(step_id=step_id, limit=limit, offset=0)
        inspect_view = query_runs(
            inspect_query,
            registry=RunsQueryImpl(RunRegistryInspectionView(registry)),
        )
        results: list[RunResult] = []
        for run_id in inspect_view.runs:
            record = registry.by_id(run_id)
            if record is None:
                continue
            results.append(
                RunResult(
                    run_id=record.run_id,
                    step_id=record.step_id,
                    status=record.status,
                    output_summary=record.output_summary,
                    source=record.source,
                    legacy_run_id=record.legacy_run_id,
                    legacy_migration_state=record.legacy_migration_state,
                    continuity_blocker=record.continuity_blocker,
                )
            )
        return tuple(results)

    def cutover_validate(self) -> CutoverValidationResult:
        """Validate migration cutover readiness against retirement criteria."""

        config = self._effective_orchestration_config()
        registry = self._run_registry(config=config)
        return CutoverValidator(registry=registry).validate_cutover_readiness()

    def migration_advance_state(
        self,
        *,
        status: LegacyRunStatus,
        legacy_run_id: str | None = None,
    ) -> OrchestrationResult:
        """Advance imported legacy migration state via canonical run-store bridge."""

        config = self._effective_orchestration_config()
        registry = self._run_registry(config=config)
        targeted_records = registry.imported_legacy_runs(legacy_run_id=legacy_run_id)

        if legacy_run_id is not None and not targeted_records:
            return OrchestrationResult(
                success=False,
                message=f"Imported legacy run not found: {legacy_run_id}",
            )

        bridge = RunStoreLegacyRunBridge(registry=registry, plan_path=str(config.plan_path))
        bridge.set_migration_status(status=status, legacy_run_id=legacy_run_id)

        scope = (
            "all imported legacy runs"
            if legacy_run_id is None
            else f"legacy_run_id={legacy_run_id}"
        )
        return OrchestrationResult(
            success=True,
            message=(
                "migration state advanced via canonical run-store bridge "
                f"scope={scope} status={status.value}"
            ),
        )

    def prune(
        self,
        before: float | None = None,
        force: bool = False,
    ) -> OrchestrationResult:
        """
        Prune old runs and artifacts.

        Authority: docs/ORCHESTRATION-PLANE-INTERFACES.md (shared)

        Args:
            before: Unix timestamp threshold. None means prune all.
            force: Skip confirmation prompt.

        Returns:
            OrchestrationResult with prune outcome.

        Raises:
            OSError: Propagates run-artifact deletion failures.
        """
        del force
        registry = self._run_registry(config=self._effective_orchestration_config())
        latest = list(registry.latest_records())
        if before is not None:
            latest = [record for record in latest if (record.updated_at or 0.0) < before]
        removed_cases = 0
        for record in latest:
            removed_cases += registry.prune_run(record.run_id)
        return OrchestrationResult(
            success=True,
            message=f"Prune complete: removed {removed_cases} case index entries",
        )

    # -----------------------------------------------------------------
    # Inspect
    # -----------------------------------------------------------------

    def inspect_status(
        self,
        step_id: str | None = None,
    ) -> InspectResult:
        """
        Inspect current orchestration status.

        Authority: docs/ORCHESTRATION-PLANE-INTERFACES.md (shared)

        Args:
            step_id: Optional step to inspect.

        Returns:
            InspectResult with status view.

        Raises:
            OSError: Propagates run store read failures.
        """
        runs = query_runs(
            InspectQuery(step_id=step_id, limit=100, offset=0),
            registry=RunsQueryImpl(
                RunRegistryInspectionView(
                    self._run_registry(config=self._effective_orchestration_config())
                )
            ),
        )
        runtime_snapshot = self._runtime.snapshot()
        data: tuple[str, ...] = (
            f"step_id={step_id or ''}",
            f"total_count={runs.total_count}",
            f"latest_run_id={runs.latest_run_id or ''}",
            f"statuses={','.join(runs.statuses)}",
            f"active_workspaces={','.join(runtime_snapshot.active_workspaces)}",
            f"active_executions={','.join(runtime_snapshot.active_executions)}",
            f"stalled_executions={','.join(runtime_snapshot.stalled_executions)}",
        )
        if self._latest_recovery_report is not None:
            report = self._latest_recovery_report
            data = (
                *data,
                f"recovery_outcome={report.outcome.value}",
                f"gate_open_allowed={report.gate_open_allowed}",
            )
        return InspectResult(view_type="status", data=data)

    def inspect_events(
        self,
        step_id: str | None = None,
        limit: int = 100,
    ) -> InspectResult:
        """
        Inspect orchestration events.

        Authority: docs/ORCHESTRATION-PLANE-INTERFACES.md (shared)

        Args:
            step_id: Optional step to filter events.
            limit: Maximum number of events to return.

        Returns:
            InspectResult with events view.

        Raises:
            EventCorruptionError: If the event stream cannot be parsed.
        """
        events = load_event_jsonl(self._events_path())
        filtered = [event for event in events if step_id is None or event.step_id == step_id]
        selected = filtered[-limit:]
        data = tuple(
            f"seq={event.seq} event={event.kind} step_id={event.step_id or ''}"
            for event in selected
        )
        return InspectResult(view_type="events", data=data)

    def inspect_logs(
        self,
        run_id: str | None = None,
        step_id: str | None = None,
    ) -> InspectResult:
        """
        Inspect run logs.

        Authority: docs/ORCHESTRATION-PLANE-INTERFACES.md (shared)

        Args:
            run_id: Optional specific run to inspect.
            step_id: Optional step to filter logs.

        Returns:
            InspectResult with logs view.

        Raises:
            OSError: If orchestration text logs cannot be read.
        """
        selected_run_id, error = self._resolve_run_selection(run_id=run_id, step_id=step_id)
        if error is not None:
            return InspectResult(view_type="logs", data=(f"error={error}",))
        lines = self._text_log_path.read_text(encoding="utf-8").splitlines()
        scoped = [
            line for line in lines if selected_run_id is None or f"run_id={selected_run_id}" in line
        ]
        return InspectResult(view_type="logs", data=tuple(scoped[-100:]))

    def inspect_artifacts(
        self,
        step_id: str | None = None,
    ) -> InspectResult:
        """
        Inspect derived artifacts from runs.

        Authority: docs/ORCHESTRATION-PLANE-INTERFACES.md (shared)

        Args:
            step_id: Optional step to filter artifacts.

        Returns:
            InspectResult with artifacts view.

        Raises:
            OSError: Propagates run store/artifact read failures.
        """
        results = self.runs(step_id=step_id, limit=100)
        artifact_refs: list[str] = []
        for result in results:
            run_root = self._run_registry(
                config=self._effective_orchestration_config()
            ).run_artifact_root(result.run_id)
            for relative in (
                "config.snapshot.yaml",
                "state/latest.json",
                "state/summary.json",
                "state/metrics.json",
            ):
                candidate = run_root / relative
                if candidate.exists():
                    artifact_refs.append(f"run_id={result.run_id} path={candidate}")
        return InspectResult(view_type="artifacts", data=tuple(artifact_refs))

    def inspect_actions(
        self,
        run_id: str | None = None,
    ) -> InspectResult:
        """
        Inspect actions taken during a run.

        Authority: docs/ORCHESTRATION-PLANE-INTERFACES.md (shared)

        Args:
            run_id: Optional specific run to inspect.

        Returns:
            InspectResult with actions view.

        Raises:
            OSError: Propagates control-channel listing failures.
        """
        selected_run_id, error = self._resolve_run_selection(run_id=run_id)
        if error is not None:
            return InspectResult(view_type="actions", data=(f"error={error}",))
        assert selected_run_id is not None
        channel = FilesystemControlChannel(
            runs_root=self._run_registry(config=self._effective_orchestration_config()).store_root,
            max_pending_actions=self._effective_orchestration_config().operator.max_pending_actions,
        )
        rows: list[str] = []
        for status in ("pending", "applied", "rejected"):
            for request in channel.list_requests(selected_run_id, status=status):
                rows.append(
                    f"status={status} action_id={request.action_id} type={request.msg_type}"
                )
        if self._latest_recovery_report is not None:
            report = self._latest_recovery_report
            mapped_status = recovery_action_status(report.outcome)
            for index, action in enumerate(report.actions):
                rows.append(
                    " ".join(
                        (
                            f"status={mapped_status}",
                            f"action_id=recovery-{index}",
                            f"type={action.action_type}",
                            f"step_id={action.step_id or ''}",
                        )
                    )
                )
        return InspectResult(view_type="actions", data=tuple(rows))

    # -----------------------------------------------------------------
    # Case
    # -----------------------------------------------------------------

    def case_list(
        self,
        status: Literal["open", "resolved", "halt"] | None = None,
    ) -> tuple[CaseResult, ...]:
        """
        List cases (blocked/unresolved situations).

        Authority: docs/ORCHESTRATION-PLANE-RESOLUTION-CONTRACT.md sections 3, 5

        Args:
            status: Optional status filter.

        Returns:
            Tuple of CaseResult for matching cases.

        Raises:
            OSError: Propagates run store read failures.
        """
        registry = self._run_registry(config=self._effective_orchestration_config())
        ids = query_cases("open", registry=CasesQueryImpl(RunRegistryInspectionView(registry)))
        results: list[CaseResult] = [
            CaseResult(case_id=case_id, status="open", reason="") for case_id in ids
        ]
        if self._latest_recovery_report is not None:
            report = self._latest_recovery_report
            results.append(
                CaseResult(
                    case_id="recovery.latest",
                    status=recovery_case_status(report.outcome),
                    reason=report.message,
                )
            )
        if self._latest_operator_notification is not None:
            notice = self._latest_operator_notification
            results.append(
                CaseResult(
                    case_id=notice.case_id,
                    status="open",
                    reason=notice.operator_message or notice.summary,
                )
            )
        if status is not None:
            results = [case for case in results if case.status == status]
        return tuple(results)

    def case_show(
        self,
        case_id: str,
    ) -> CaseResult:
        """
        Show detail for a specific case.

        Authority: docs/ORCHESTRATION-PLANE-RESOLUTION-CONTRACT.md sections 3, 5

        Args:
            case_id: The case identifier to show.

        Returns:
            CaseResult with case detail.

        Raises:
            OSError: Propagates run store read failures.
        """
        registry = self._run_registry(config=self._effective_orchestration_config())
        if case_id == "recovery.latest" and self._latest_recovery_report is not None:
            report = self._latest_recovery_report
            return CaseResult(
                case_id="recovery.latest",
                status=recovery_case_status(report.outcome),
                reason=report.message,
            )
        if (
            self._latest_operator_notification is not None
            and case_id == self._latest_operator_notification.case_id
        ):
            notice = self._latest_operator_notification
            return CaseResult(
                case_id=notice.case_id,
                status="open",
                reason=notice.operator_message or notice.summary,
            )
        entry = registry.case_by_id(case_id)
        if entry is None:
            return CaseResult(case_id=case_id, status=None, reason="Case not found")
        return CaseResult(
            case_id=entry.case_id, status="open" if entry.status == "open" else "resolved"
        )

    def case_respond(
        self,
        case_id: str,
        response: str,
    ) -> OrchestrationResult:
        """
        Respond to a case (operator input to resolver).

        Authority: docs/ORCHESTRATION-PLANE-RESOLUTION-CONTRACT.md sections 3, 5

        Args:
            case_id: The case to respond to.
            response: Operator response message.

        Returns:
            OrchestrationResult with response outcome.

        Raises:
            OSError: Propagates control-channel persistence failures.
        """
        registry = self._run_registry(config=self._effective_orchestration_config())
        entry = registry.case_by_id(case_id)
        if entry is None:
            return OrchestrationResult(success=False, message=f"Case not found: {case_id}")
        message = ControlChannelMessage(
            msg_type="case.respond",
            sender="operator",
            payload=(entry.run_id, case_id, response),
        )
        send_to_control(message, channel=self._control_channel())
        self._emit_event(
            kind="operator_action_requested",
            payload={"case_id": case_id, "action": response},
            step_id=None,
            agent="operator",
        )
        return OrchestrationResult(success=True, message=f"Case response queued for {case_id}")

    # -----------------------------------------------------------------
    # Control
    # -----------------------------------------------------------------

    def control_pause(
        self,
        step_id: str | None = None,
        reason: str | None = None,
    ) -> ControlResult:
        """
        Pause orchestration (stop dispatching new work).

        Authority: docs/ORCHESTRATION-PLANE-ARCHITECTURE.md section 5.1

        Args:
            step_id: Optional specific step to pause.
            reason: Optional pause reason from operator.

        Returns:
            ControlResult with pause outcome.

        Raises:
            OSError: Propagates control-channel persistence failures.
        """
        selected_run_id, error = self._resolve_run_selection(run_id=None, step_id=step_id)
        if error is not None:
            return ControlResult(action="pause", success=False, message=error)
        assert selected_run_id is not None
        payload = (selected_run_id,) if reason is None else (selected_run_id, reason)
        send_to_control(
            ControlChannelMessage(
                msg_type="control.pause",
                sender="operator",
                payload=payload,
            ),
            channel=self._control_channel(),
        )
        return ControlResult(
            action="pause", success=True, message=f"Pause queued for {selected_run_id}"
        )

    def control_unpause(
        self,
        step_id: str | None = None,
        reason: str | None = None,
    ) -> ControlResult:
        """
        Unpause orchestration (resume dispatching).

        Authority: docs/ORCHESTRATION-PLANE-ARCHITECTURE.md section 5.1

        Args:
            step_id: Optional specific step to unpause.
            reason: Optional unpause reason from operator.

        Returns:
            ControlResult with unpause outcome.

        Raises:
            OSError: Propagates control-channel persistence failures.
        """
        selected_run_id, error = self._resolve_run_selection(run_id=None, step_id=step_id)
        if error is not None:
            return ControlResult(action="unpause", success=False, message=error)
        assert selected_run_id is not None
        payload = (selected_run_id,) if reason is None else (selected_run_id, reason)
        send_to_control(
            ControlChannelMessage(
                msg_type="control.unpause",
                sender="operator",
                payload=payload,
            ),
            channel=self._control_channel(),
        )
        return ControlResult(
            action="unpause",
            success=True,
            message=f"Unpause queued for {selected_run_id}",
        )

    def control_stop(
        self,
        run_id: str | None = None,
        reason: str | None = None,
        force: bool = False,
    ) -> ControlResult:
        """
        Stop orchestration entirely.

        Authority: docs/ORCHESTRATION-PLANE-ARCHITECTURE.md section 5.1

        Args:
            run_id: Optional explicit run identifier to stop.
            reason: Optional reason for stopping.
            force: Request immediate stop semantics.

        Returns:
            ControlResult with stop outcome.

        Raises:
            OSError: Propagates control-channel persistence failures.
        """
        selected_run_id, error = self._resolve_run_selection(run_id=run_id)
        if error is not None:
            return ControlResult(action="stop", success=False, message=error)
        assert selected_run_id is not None
        payload_items = [selected_run_id]
        if reason is not None:
            payload_items.append(reason)
        if force:
            payload_items.append("force=true")
        payload = tuple(payload_items)
        send_to_control(
            ControlChannelMessage(
                msg_type="control.stop",
                sender="operator",
                payload=payload,
            ),
            channel=self._control_channel(),
        )
        return ControlResult(
            action="stop", success=True, message=f"Stop queued for {selected_run_id}"
        )

    # -----------------------------------------------------------------
    # Config
    # -----------------------------------------------------------------

    def config_show(
        self,
        effective: bool = False,
    ) -> ConfigResult:
        """
        Show current orchestration configuration.

        Authority: docs/ORCHESTRATION-PLANE-IMPLEMENTATION-DESIGN.md section 3.7

        Returns:
            ConfigResult with current configuration.

        Raises:
            OSError: Propagates config materialization failures.
        """
        config = self._effective_orchestration_config()
        if effective:
            show_output = (
                f"plan_path={config.plan_path}\n"
                f"roster.default_ttl_seconds={config.roster.default_ttl_seconds}\n"
                f"roster.max_reuse_window_seconds={config.roster.max_reuse_window_seconds}\n"
                f"runtime.default_runner={config.runtime.default_runner}\n"
                f"runtime.artifact_root={config.runtime.artifact_root}\n"
                f"runtime.workspace_root={config.runtime.workspace_root}\n"
                f"runtime.isolation_default={config.runtime.isolation_default}\n"
                f"runtime.cleanup_policy={config.runtime.cleanup_policy}\n"
                f"control.idle_poll_interval_ms={config.control.idle_poll_interval_ms}\n"
                f"control.max_resolution_attempts_per_case={config.control.max_resolution_attempts_per_case}\n"
                f"control.action_ack_timeout_seconds={config.control.action_ack_timeout_seconds}\n"
                f"resolver.enabled={config.resolver.enabled}\n"
                f"resolver.invocation_timeout_seconds={config.resolver.invocation_timeout_seconds}\n"
                f"resolver.max_tool_calls_per_invocation={config.resolver.max_tool_calls_per_invocation}\n"
                f"resolver.max_tool_argument_bytes={config.resolver.max_tool_argument_bytes}\n"
                f"resolver.tool_allowlist={self._allowlist_text(config.resolver.tool_allowlist)}\n"
                f"continuity.resume_enabled={config.continuity.resume_enabled}\n"
                f"continuity.stale_artifact_policy={config.continuity.stale_artifact_policy}\n"
                f"continuity.replay_safety={config.continuity.replay_safety}\n"
                f"observability.events_jsonl={config.observability.events_jsonl}\n"
                f"observability.text_log={config.observability.text_log}\n"
                f"observability.projected_state={config.observability.projected_state}\n"
                "observability.heartbeat_stale_threshold_seconds="
                f"{config.observability.heartbeat_stale_threshold_seconds}\n"
                f"observability.per_step_artifacts={config.observability.per_step_artifacts}\n"
                f"observability.per_case_artifacts={config.observability.per_case_artifacts}\n"
                f"observability.redact_env_keys={','.join(config.observability.redact_env_keys)}\n"
                f"observability.max_log_megabytes={config.observability.max_log_megabytes}\n"
                f"observability.retention_days={config.observability.retention_days}\n"
                f"operator.control_channel={config.operator.control_channel}\n"
                f"operator.default_output={config.operator.default_output}\n"
                f"operator.max_pending_actions={config.operator.max_pending_actions}"
            )
        else:
            show_output = (
                f"plan_path={config.plan_path}\n"
                f"artifact_root={config.runtime.artifact_root}\n"
                f"workspace_root={config.runtime.workspace_root}\n"
                f"resolver_enabled={config.resolver.enabled}\n"
                f"allowlist={self._allowlist_text(config.resolver.tool_allowlist)}"
            )
        return ConfigResult(
            show_output=show_output, validation_passed=True, tools=canonical_tool_families()
        )

    def config_validate(
        self,
    ) -> ConfigResult:
        """
        Validate current orchestration configuration.

        Authority: docs/ORCHESTRATION-PLANE-IMPLEMENTATION-DESIGN.md section 3.7

        Returns:
            ConfigResult with validation result.

        Raises:
            OSError: Propagates config materialization failures.
        """
        errors = validate_orchestration_config(self._effective_orchestration_config())
        if errors:
            output = "\n".join(
                f"{error.field}: {error.reason} (value={error.value!r})" for error in errors
            )
            return ConfigResult(
                show_output=output,
                validation_passed=False,
                tools=canonical_tool_families(),
            )
        return ConfigResult(
            show_output="configuration is valid",
            validation_passed=True,
            tools=canonical_tool_families(),
        )

    def config_tools(
        self,
    ) -> ConfigResult:
        """
        Show registered tool families and allowlist.

        Authority: docs/ORCHESTRATION-PLANE-RESOLUTION-CONTRACT.md section 4.0

        Returns:
            ConfigResult with registered tool families.

        Raises:
            OSError: Propagates config materialization failures.
        """
        resolved = self._effective_orchestration_config()
        configured_allowlist = resolved.resolver.tool_allowlist.allowed_tool_families
        output = (
            f"registered={','.join(canonical_tool_families())}\n"
            f"allowlist={','.join(configured_allowlist) if configured_allowlist else 'deny-all'}"
        )
        return ConfigResult(
            show_output=output,
            validation_passed=True,
            tools=canonical_tool_families(),
        )


# ---------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------


def build_orchestration_app(
    config: AppConfig,
) -> OrchestrationApp:
    """
    Factory to build a composed OrchestrationApp from configuration.

    Authority: docs/ORCHESTRATION-PLANE-IMPLEMENTATION-DESIGN.md section 6

    GAP: Concrete component construction and wiring are deferred to
    implementation phases.

    Args:
        config: Application-level configuration.

    Returns:
        A composed OrchestrationApp instance.

    Raises:
        OSError: If authoritative plan path cannot be accessed.
    """
    from vectl.orchestration.control import ControlInputSources, PlanAwareControl
    from vectl.orchestration.core_adapter import PlanCoreAdapter
    from vectl.orchestration.resolver import BoundResolver, ResolverInvocationSurface
    from vectl.orchestration.roster import Roster
    from vectl.orchestration.runtime import Runtime

    resolved_orchestration_config = config.orchestration_config
    if resolved_orchestration_config is None:
        loaded_config, _ = load_orchestration_config()
        resolved_orchestration_config = replace(loaded_config, plan_path=config.plan_path)

    if not config.plan_path.exists():
        raise FileNotFoundError(f"authoritative plan path not found: {config.plan_path}")

    class DefaultRoleResolverInvocation(ResolverInvocationSurface):
        def __init__(
            self,
            *,
            runtime: Runtime,
            dispatch_coordinator: DispatchCoordinator,
            timeout_seconds: float,
        ) -> None:
            self._runtime = runtime
            self._dispatch_coordinator = dispatch_coordinator
            self._timeout_seconds = timeout_seconds

        def invoke(self, case: object, *, role_id: str) -> dict[str, object]:
            from vectl.orchestration.contracts import ResolutionCase

            if not isinstance(case, ResolutionCase):
                raise TypeError("resolver invocation requires ResolutionCase")

            is_linked, main_root = is_linked_worktree()
            if is_linked:
                main_root_text = str(main_root) if main_root is not None else "<unknown>"
                return {
                    "status": "operator_required",
                    "summary": (
                        "Resolver invocation blocked: resolver must run from the main worktree"
                    ),
                    "evidence_refs": (f"resolver_main_worktree={main_root_text}",),
                    "operator_message": (
                        "Rerun resolver from the canonical main worktree before attempting "
                        "blocked-case repair."
                    ),
                }

            dispatch_spec = self._dispatch_coordinator.build_resolution_subtask_spec(
                case_id=case.case_id or f"case-{generate_run_id()}",
                role_id=role_id,
                description=self._case_description(case),
                refs=case.artifact_refs,
            )
            request = ExecutionRequest(
                step_id=dispatch_spec.step_id or dispatch_spec.source_id,
                role=dispatch_spec.role_id,
                runner=dispatch_spec.runner,
                work_refs=(
                    f"resolver_case_id={case.case_id}",
                    f"resolver_case_source={case.case_source}",
                    f"resolver_role_id={dispatch_spec.role_id}",
                    f"execution_context={dispatch_spec.execution_context}",
                    f"source_kind={dispatch_spec.source_kind}",
                    *case.artifact_refs,
                ),
                session_id=f"resolver-{generate_run_id()}",
            )

            try:
                workspace = self._runtime.prepare(request)
                execution_id = self._runtime.start(request=request, workspace=workspace)
            except Exception as exc:
                return {
                    "status": "operator_required",
                    "summary": f"Resolver runtime start failed: {exc}",
                    "evidence_refs": (f"resolver_role_id={dispatch_spec.role_id}",),
                    "operator_message": "Review resolver runtime startup and retry.",
                }

            deadline = time.monotonic() + self._timeout_seconds
            while time.monotonic() <= deadline:
                result = self._runtime.collect(execution_id)
                if result is None:
                    time.sleep(0.01)
                    continue
                return self._result_to_payload(
                    result=result,
                    execution_id=execution_id,
                    role_id=dispatch_spec.role_id,
                )

            return {
                "status": "operator_required",
                "summary": (
                    "Resolver runtime timed out before producing a ResolutionReport payload"
                ),
                "evidence_refs": (
                    f"resolver_execution_id={execution_id}",
                    f"resolver_role_id={dispatch_spec.role_id}",
                ),
                "operator_message": "Review resolver timeout and retry the blocked case.",
            }

        def _case_description(self, case: ResolutionCase) -> str:
            summary = case.summary or case.reason
            blocked_steps = ", ".join(case.blocked_step_ids)
            if blocked_steps:
                return f"Resolve blocked case {case.case_id} for {blocked_steps}: {summary}"
            return f"Resolve blocked case {case.case_id}: {summary}"

        def _result_to_payload(
            self,
            *,
            result: ExecutionResult,
            execution_id: str,
            role_id: str,
        ) -> dict[str, object]:
            if result.status != "success":
                return {
                    "status": "operator_required",
                    "summary": (
                        f"Resolver runtime returned {result.status}: {result.output_summary}"
                    ),
                    "evidence_refs": (
                        f"resolver_execution_id={execution_id}",
                        f"resolver_role_id={role_id}",
                    ),
                    "operator_message": result.operator_message
                    or "Review resolver runtime failure and retry.",
                }

            try:
                payload = json.loads(result.output_summary)
            except json.JSONDecodeError:
                return {
                    "status": "operator_required",
                    "summary": (
                        "Resolver runtime returned non-JSON output for the "
                        "resolution_report contract"
                    ),
                    "evidence_refs": (
                        f"resolver_execution_id={execution_id}",
                        f"resolver_role_id={role_id}",
                    ),
                    "operator_message": "Review resolver output formatting and retry.",
                }
            if not isinstance(payload, dict):
                return {
                    "status": "operator_required",
                    "summary": (
                        "Resolver runtime returned a non-object payload for the "
                        "resolution_report contract"
                    ),
                    "evidence_refs": (
                        f"resolver_execution_id={execution_id}",
                        f"resolver_role_id={role_id}",
                    ),
                    "operator_message": "Review resolver output formatting and retry.",
                }
            return payload

    core_adapter = PlanCoreAdapter(plan_path=config.plan_path)
    roster = Roster(default_ttl_seconds=resolved_orchestration_config.roster.default_ttl_seconds)
    runtime = Runtime(workspace_root=resolved_orchestration_config.runtime.workspace_root)
    role_registry = ConfigRoleProfileRegistry(
        profiles=resolved_orchestration_config.role_profiles,
        default_role=resolved_orchestration_config.dispatch.default_role_id,
    )
    prompt_registry = ConfigPromptRegistry(role_registry=role_registry)
    dispatch_coordinator = DispatchCoordinator(
        role_registry=role_registry,
        prompt_registry=prompt_registry,
        step_adapter=CoreStepDataAdapter(core_adapter),
    )
    control = PlanAwareControl(
        sources=ControlInputSources(core_adapter=core_adapter, roster=roster, runtime=runtime),
        agent=config.default_agent,
        dispatch_role=config.default_agent,
    )
    resolver = BoundResolver(
        invocation=DefaultRoleResolverInvocation(
            runtime=runtime,
            dispatch_coordinator=dispatch_coordinator,
            timeout_seconds=resolved_orchestration_config.resolver.invocation_timeout_seconds,
        ),
        default_role_id=resolved_orchestration_config.resolver.default_role_id,
    )

    app_config = replace(
        config,
        orchestration_config=resolved_orchestration_config,
        run_store_root=config.run_store_root or resolved_orchestration_config.runtime.artifact_root,
    )
    return OrchestrationApp(
        config=app_config,
        control=control,
        roster=roster,
        runtime=runtime,
        resolver=resolver,
        core_adapter=core_adapter,
        role_registry=role_registry,
        prompt_registry=prompt_registry,
        dispatch_coordinator=dispatch_coordinator,
    )


__all__ = [
    "AppConfig",
    "DispatchBlockedError",
    "DuplicateCompleteBlockedError",
    "OrchestrationApp",
    "OrchestrationResult",
    "RunResult",
    "InspectResult",
    "CaseResult",
    "ControlResult",
    "ConfigResult",
    "OperatorNotification",
    "build_resolution_case",
    "normalize_review_resolution_case",
    "build_review_parse_failure_case",
    "build_orchestration_app",
]
