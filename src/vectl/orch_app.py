# @invar:allow file_size: orchestration app is the documented composition root and public CLI routing facade; splitting requires a compatibility migration outside this scoped guard remediation.
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
import subprocess
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, cast

import yaml

from vectl.io import load_plan_definition
from vectl.models import StepStatus
from vectl.orchestration.config import (
    OrchestrationConfig,
    ResolverToolAllowlist,
    RoleFieldProvenance,
    build_role_profile_provenance,
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
    ChildRunRef,
    ControlDecision,
    CoreSnapshot,
    DispatchSpec,
    DriveBarrier,
    ExecutionRequest,
    ExecutionResult,
    PromptBundle,
    ReconcileResult,
    RecoveryAttempt,
    RecoveryContinuity,
    RequestMode,
    ResolutionCase,
    ResolutionCaseSource,
    ResolutionReport,
    ReviewOutcome,
    RosterSnapshot,
    RuntimeSnapshot,
    SessionPolicy,
    StructuredReviewResult,
    WorkLease,
)
from vectl.orchestration.control_channel import (
    ControlChannelMessage,
    FilesystemControlChannel,
    send_drive_control,
    send_to_control,
)
from vectl.orchestration.dispatch_policy import (
    ConfigPromptRegistry,
    ConfigRoleProfileRegistry,
    DispatchAuthorityError,
    DispatchCoordinator,
    normalize_parse_failure,
    normalize_review_result,
)
from vectl.orchestration.driver import (
    TERMINAL_DRIVE_STATUSES,
    DriveDriver,
    DriveLoopResult,
    DriveRecoverResult,
    DriveResumeResult,
    DriveStartResult,
    DriveStatusResult,
)
from vectl.orchestration.events import (
    EventCorruptionError,
    JsonlEventSink,
    OrchestrationEventEnvelope,
    OrchestrationEventKind,
    load_event_jsonl,
)
from vectl.orchestration.evidence import (
    assess_freeform_evidence,
    extract_runner_payload,
    summarize_plan_evidence,
)
from vectl.orchestration.inspection_queries import (
    CasesQueryImpl,
    DriveInspectQuery,
    DriveInspectView,
    InspectQuery,
    RunsQueryImpl,
    query_cases,
    query_drive_artifacts,
    query_drive_events,
    query_drive_status,
    query_runs,
    validate_child_run_in_drive,
)
from vectl.orchestration.projections import replay_events_to_artifacts
from vectl.orchestration.prompt_materialization import (
    materialize_prompt_artifacts,
    resolve_prompt_artifact_paths,
)
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
from vectl.orchestration.resolution_reports import validate_resolution_report_payload
from vectl.orchestration.resolver import map_payload_to_report
from vectl.orchestration.resolver_gateway import (
    AuditedResolverGateway,
    AuthorizationError,
    ResolverToolCall,
    ResolverToolMediation,
    authorize_and_invoke,
)
from vectl.orchestration.run_store import (
    CorruptJSONLError,
    DriveStore,
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
    status: Literal["pending", "running", "success", "fail", "stall", "paused"] | None = None
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
    Authority: docs/RFC-role-profile-overrides.md §7.1

    Attributes:
        show_output: Output from config show operation.
        validation_passed: True if config validation passed.
        tools: Tuple of registered tool family identifiers.
        role_profile_provenance: Per-field provenance for role profiles.
            When ``effective=True``, maps
            ``role_id -> {field_name -> RoleFieldProvenance}``.
    """

    show_output: str = ""
    validation_passed: bool = False
    tools: tuple[str, ...] = ()
    role_profile_provenance: dict[str, dict[str, Any]] | None = None


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


@dataclass(frozen=True)
class DriveChildRunContext:
    """In-process metadata needed to collect and finalize a drive child run."""

    step_id: str
    role_id: str
    dispatch_spec: DispatchSpec
    run_root: Path


@dataclass(frozen=True)
class CaseRuntimeToolMediationSource:
    """Derive gateway mediation from live resolution-case inputs.

    Authority:
        docs/ORCHESTRATION-PLANE-LIVE-AUTHORITY-CONTRACT-LOCK.md section 6.2
        docs/ORCHESTRATION-PLANE-RESOLUTION-CONTRACT.md sections 3.2, 4.0, 4.1

    This source narrows configured allowlists to only the tool families requested
    by the live case. The default live-path inspection is ``read_case`` when a
    case identifier exists. Additional tool requests may be carried through
    ``ResolutionCase.artifact_refs`` using ``resolver_tool=<family>.<tool>[:surface]``.
    """

    configured_allowlist_families: tuple[str, ...]

    def resolve(self, case: ResolutionCase, *, role_id: str) -> ResolverToolMediation:
        """Build a runtime mediation decision for one case.

        Args:
            case: Live resolution case being authorized.
            role_id: Resolver role chosen for this invocation.

        Returns:
            Runtime mediation decision for the gateway.
        """

        requested_calls: list[ResolverToolCall] = []
        if case.case_id:
            requested_calls.append(
                ResolverToolCall(family="orchestration", name="read_case", surface="read")
            )
        requested_calls.extend(_artifact_ref_mediation_calls(case.artifact_refs))

        planned_tool_calls = tuple(dict.fromkeys(requested_calls))
        requested_families = tuple(dict.fromkeys(call.family for call in planned_tool_calls))
        allowed_tool_families = tuple(
            family for family in requested_families if family in self.configured_allowlist_families
        )

        call_text = ",".join(
            f"{call.family}.{call.name}:{call.surface}" for call in planned_tool_calls
        )
        allowlist_text = ",".join(allowed_tool_families)
        return ResolverToolMediation(
            planned_tool_calls=planned_tool_calls,
            allowed_tool_families=allowed_tool_families,
            evidence_refs=(
                f"resolver_mediation_role={role_id}",
                f"resolver_mediation_calls={call_text or 'none'}",
                f"resolver_mediation_allowlist={allowlist_text or 'deny-all'}",
            ),
        )


# @invar:allow shell_result: Private parser returns the domain tuple expected by resolver mediation; Result wrapping would churn synchronous call sites without adding an I/O boundary.
# @shell_complexity: Branches preserve tolerant parsing of optional resolver_tool evidence directives.
# @shell_orchestration: Resolver-tool evidence parsing stays adjacent to mediation because artifact refs are shell runtime artifacts.
def _artifact_ref_mediation_calls(artifact_refs: tuple[str, ...]) -> tuple[ResolverToolCall, ...]:
    """Parse case-carried runtime mediation directives.

    Authority:
        docs/ORCHESTRATION-PLANE-LIVE-AUTHORITY-CONTRACT-LOCK.md section 6.2
        docs/ORCHESTRATION-PLANE-RESOLUTION-CONTRACT.md section 3.2

    Args:
        artifact_refs: Live case evidence references.

    Returns:
        Parsed resolver tool calls declared by the case.
    """

    calls: list[ResolverToolCall] = []
    prefix = "resolver_tool="
    for ref in artifact_refs:
        if not ref.startswith(prefix):
            continue
        directive = ref[len(prefix) :]
        tool_path, separator, surface_text = directive.partition(":")
        family, dot, tool_name = tool_path.partition(".")
        if not family or not dot or not tool_name:
            continue
        surface: Literal["read", "write"] = "read"
        if separator:
            if surface_text not in {"read", "write"}:
                continue
            surface = cast(Literal["read", "write"], surface_text)
        calls.append(ResolverToolCall(family=family, name=tool_name, surface=surface))
    return tuple(calls)


# @invar:allow shell_result: Public app helper returns ResolutionCase directly for existing resolver-routing callers.
# @shell_orchestration: ResolutionCase construction remains in the app routing module to preserve resolver intake compatibility.
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


# @invar:allow shell_result: Public normalization helper delegates to dispatch-policy domain conversion and preserves Optional[ResolutionCase] API.
# @shell_orchestration: Review-to-resolution normalization is app routing glue between runner output and resolver intake.
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


# @invar:allow shell_result: Public parse-failure helper must return ResolutionCase for resolver intake compatibility.
# @shell_orchestration: Parse-failure case construction is resolver routing glue for shell runner output.
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
        - start_drive()     (start/resolve a drive for a plan)
        - run_drive_loop()  (execute one drive scheduling loop pass)
        - resume_drive()    (resume an interrupted drive)
        - recover_drive()   (recover a drive from interrupted state)
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
            core_adapter=cast("PlanCoreAdapter", core_adapter),
        )
        self._drive_child_run_contexts: dict[str, DriveChildRunContext] = {}

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

        dispatch_spec = self._dispatch_coordinator.build_dispatch_spec(
            ControlDecision(
                kind="dispatch",
                reason="orchestration app dispatch",
                step_ids=(step_id,),
                role_bindings={step_id: role_hint or self._config.default_agent},
            )
        )
        return self._bind_roster_dispatch_inputs(step_id=step_id, dispatch_spec=dispatch_spec)

    def _bind_roster_dispatch_inputs(
        self,
        *,
        step_id: str,
        dispatch_spec: DispatchSpec,
    ) -> DispatchSpec:
        """Bind live roster lease facts into an authoritative dispatch spec.

        Authority:
            docs/ORCHESTRATION-PLANE-LIVE-AUTHORITY-CONTRACT-LOCK.md sections 3.1, 5.1, 5.3
            docs/ORCHESTRATION-PLANE-INTERFACES.md sections 2.G, 4.2
            docs/ORCHESTRATION-PLANE-ISOLATION-SEMANTICS.md section 5.3

        The dispatch coordinator remains authoritative for step intent and role
        meaning. Roster may contribute only compatible supply facts for that
        already-chosen role.
        """

        claim = getattr(self._roster, "claim", None)
        if not callable(claim):
            return dispatch_spec

        isolation = self._core_adapter.step_isolation(step_id)
        lease = claim(dispatch_spec.role_id, isolation=isolation)
        if lease is None:
            return dispatch_spec
        if not isinstance(lease, WorkLease):
            raise TypeError(
                "roster authority violation: claim() must return WorkLease | None, "
                f"got {type(lease).__name__}"
            )
        return _apply_roster_lease(dispatch_spec=dispatch_spec, lease=lease)

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

        freeform_failure_case = self._normalize_freeform_evidence_case(
            step_id=step_id,
            raw_output=execution_result.output_summary,
            role_id=dispatch_spec.role_id,
            output_contract=dispatch_spec.output_contract,
            snapshots=snapshots,
        )
        if freeform_failure_case is not None:
            return freeform_failure_case

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
            summarize_plan_evidence(execution_result.output_summary),
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
        payload = extract_runner_payload(raw_output)
        try:
            parsed = self._parse_structured_review_result(payload)
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

    def _normalize_freeform_evidence_case(
        self,
        *,
        step_id: str,
        raw_output: str,
        role_id: str,
        output_contract: str,
        snapshots: SnapshotBundle,
    ) -> ResolutionCase | None:
        """Block completion when freeform evidence explicitly reports failure."""

        if output_contract != "freeform_evidence":
            return None
        assessment = assess_freeform_evidence(raw_output)
        if not assessment.failed:
            return None
        return ResolutionCase(
            case_id=f"case-{generate_run_id()}",
            case_source="review_failed",
            reason=f"freeform evidence failure: {assessment.reason}",
            summary=assessment.summary,
            core=snapshots.core,
            roster=snapshots.roster,
            runtime=snapshots.runtime,
            blocked_step_ids=(step_id,),
            artifact_refs=(
                f"role_id={role_id}",
                f"output_contract={output_contract}",
                "freeform_evidence_failure",
            ),
        )

    def _parse_structured_review_result(self, raw_output: str) -> StructuredReviewResult:
        """Parse runner review output into StructuredReviewResult."""

        payload = _extract_structured_review_payload(raw_output)
        if payload is None:
            raise ValueError("review output is not parseable as StructuredReviewResult")
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
        typed_outcome = cast(ReviewOutcome, review_outcome)
        return StructuredReviewResult(
            review_outcome=typed_outcome,
            summary=summary,
            findings=tuple(findings),
            evidence_refs=tuple(evidence_refs),
        )

    # @invar:allow function_size: Runtime launch must keep prepare, prompt materialization, and start request ordering together to preserve runner path semantics.
    def _start_runtime_execution(
        self,
        *,
        run_id: str,
        step_id: str,
        dispatch_spec: DispatchSpec,
        mode: Literal["start", "resume", "recover"],
    ) -> tuple[str, str]:
        # Authority: RFC-opencode-orchestration-runner.md sections 8, 10.2
        #
        # Prompt path consistency contract (hotfix):
        # The workspace path used for prompt materialization MUST be the same
        # worktree path that the runner receives via --dir. Before this fix,
        # _resolve_workspace_for_run() returned the workspace root (e.g.
        # .vectl/workspaces/), but runtime.start() launches the runner inside
        # a per-step worktree (e.g. .vectl/workspaces/core.ready-abcd/). The
        # runner then reads --file .vectl/orch/runner_prompt.md relative to
        # the worktree, but the prompt was materialized in the workspace root.
        #
        # Fix: call prepare() first to establish the worktree, then get the
        # worktree path from the runtime, and use that path for prompt
        # materialization so the prompt file lands where the runner expects it.
        prompt_bundle = self._render_validated_prompt_bundle(dispatch_spec)
        agent_id = self._agent_id_for_dispatch(dispatch_spec)

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
        request_mode = _dispatch_request_mode(mode=mode, dispatch_spec=dispatch_spec)
        session_id: str | None = run_id
        if dispatch_spec.session_mode == "reuse":
            session_id = dispatch_spec.reuse_token
        session_policy: SessionPolicy = (
            "reuse_allowed" if dispatch_spec.session_mode == "reuse" else "reuse_forbidden"
        )
        prompt_bundle_ref = self._prompt_bundle_work_ref(prompt_bundle)

        # Build request with placeholder prompt paths; these will be updated
        # after prepare() establishes the worktree path.
        request = ExecutionRequest(
            step_id=step_id,
            role=dispatch_spec.role_id,
            runner=dispatch_spec.runner,
            work_refs=(
                f"run_id={run_id}",
                f"mode={request_mode}",
                isolation_ref,
                f"execution_context={dispatch_spec.execution_context}",
                f"mutation_policy={dispatch_spec.mutation_policy}",
                f"output_contract={dispatch_spec.output_contract}",
                f"source_kind={dispatch_spec.source_kind}",
                prompt_bundle_ref,
            ),
            agent_id=agent_id,
            prompt_bundle_path="",  # placeholder; filled after prepare()
            runner_prompt_path="",  # placeholder; filled after prepare()
            request_mode=request_mode,
            session_policy=session_policy,
            session_id=session_id,
        )

        # Prepare the worktree first so we know the actual execution directory.
        workspace_id = self._runtime.prepare(request)
        worktree_path = self._runtime.workspace_worktree_path(workspace_id)

        # Now materialize prompt artifacts into the correct worktree path.
        artifact_root = self._artifact_root_for_run(run_id)
        artifact_paths = resolve_prompt_artifact_paths(
            artifact_root=artifact_root,
            run_id=run_id,
            workspace=worktree_path,
        )
        materialize_prompt_artifacts(
            bundle=prompt_bundle,
            artifact_paths=artifact_paths,
            role_id=dispatch_spec.role_id,
            agent_id=agent_id,
            runner=dispatch_spec.runner,
            output_contract=dispatch_spec.output_contract,
        )

        # Update the request with validated prompt artifact paths.
        request = ExecutionRequest(
            step_id=step_id,
            role=dispatch_spec.role_id,
            runner=dispatch_spec.runner,
            work_refs=request.work_refs,
            agent_id=agent_id,
            prompt_bundle_path=artifact_paths.prompt_bundle_path,
            runner_prompt_path=artifact_paths.runner_prompt_path,
            request_mode=request_mode,
            session_policy=session_policy,
            session_id=session_id,
        )
        execution_id = self._runtime.start(request=request, workspace=workspace_id)
        return workspace_id, execution_id

    def _start_runtime_child_execution(
        self,
        *,
        drive_id: str,
        run_id: str,
        step_id: str,
        dispatch_spec: DispatchSpec,
        mode: Literal["start", "resume", "recover"],
    ) -> ChildRunRef:
        """Start a drive-scoped step child run through the real runtime."""

        prompt_bundle = self._render_validated_prompt_bundle(dispatch_spec)
        agent_id = self._agent_id_for_dispatch(dispatch_spec)

        blocked, block_reason = self._is_dispatch_blocked_by_gate()
        if blocked:
            raise DispatchBlockedError(
                f"_start_runtime_child_execution: dispatch blocked for step {step_id}: "
                f"{block_reason}"
            )

        if dispatch_spec.source_kind == "step":
            authoritative_isolation = self._core_adapter.step_isolation(step_id)
            isolation_ref = f"isolation={authoritative_isolation.value}"
        else:
            isolation_ref = "isolation=default"
        request_mode = _dispatch_request_mode(mode=mode, dispatch_spec=dispatch_spec)
        session_id: str | None = run_id
        if dispatch_spec.session_mode == "reuse":
            session_id = dispatch_spec.reuse_token
        session_policy: SessionPolicy = (
            "reuse_allowed" if dispatch_spec.session_mode == "reuse" else "reuse_forbidden"
        )
        prompt_bundle_ref = self._prompt_bundle_work_ref(prompt_bundle)

        request = ExecutionRequest(
            step_id=step_id,
            role=dispatch_spec.role_id,
            runner=dispatch_spec.runner,
            work_refs=(
                f"run_id={run_id}",
                f"drive_id={drive_id}",
                f"mode={request_mode}",
                isolation_ref,
                f"execution_context={dispatch_spec.execution_context}",
                f"mutation_policy={dispatch_spec.mutation_policy}",
                f"output_contract={dispatch_spec.output_contract}",
                f"source_kind={dispatch_spec.source_kind}",
                prompt_bundle_ref,
            ),
            agent_id=agent_id,
            prompt_bundle_path="",
            runner_prompt_path="",
            request_mode=request_mode,
            session_policy=session_policy,
            session_id=session_id,
        )

        workspace_id = self._runtime.prepare_child_run(request, drive_id=drive_id, kind="step")
        worktree_path = self._runtime.workspace_worktree_path(workspace_id)
        artifact_root = self._artifact_root_for_run(run_id)
        artifact_paths = resolve_prompt_artifact_paths(
            artifact_root=artifact_root,
            run_id=run_id,
            workspace=worktree_path,
        )
        materialize_prompt_artifacts(
            bundle=prompt_bundle,
            artifact_paths=artifact_paths,
            role_id=dispatch_spec.role_id,
            agent_id=agent_id,
            runner=dispatch_spec.runner,
            output_contract=dispatch_spec.output_contract,
        )

        request = ExecutionRequest(
            step_id=step_id,
            role=dispatch_spec.role_id,
            runner=dispatch_spec.runner,
            work_refs=request.work_refs,
            agent_id=agent_id,
            prompt_bundle_path=artifact_paths.prompt_bundle_path,
            runner_prompt_path=artifact_paths.runner_prompt_path,
            request_mode=request_mode,
            session_policy=session_policy,
            session_id=session_id,
        )
        return self._runtime.start_child_run(
            request,
            workspace_id,
            drive_id=drive_id,
            kind="step",
            step_id=step_id,
        )

    def _render_validated_prompt_bundle(self, dispatch_spec: DispatchSpec) -> PromptBundle:
        """Render prompt bundle from dispatch coordinator with authority validation.

        Authority: RFC-opencode-orchestration-runner.md section 8.3

        Returns:
            Validated PromptBundle ready for materialization.
        """
        try:
            prompt_bundle = self._dispatch_coordinator.render_prompt_bundle(dispatch_spec)
        except DispatchAuthorityError as exc:
            raise ValueError(f"dispatch authority violation: {exc}") from exc

        profile = self._dispatch_coordinator.role_registry.get(dispatch_spec.role_id)
        if dispatch_spec.execution_context != profile.execution_context:
            raise ValueError(
                "dispatch authority violation: "
                f"role={dispatch_spec.role_id!r} requires "
                f"execution_context={profile.execution_context!r} but got "
                f"{dispatch_spec.execution_context!r}"
            )
        return prompt_bundle

    def _agent_id_for_dispatch(self, dispatch_spec: DispatchSpec) -> str:
        """Return concrete runner agent/persona for a role-profile dispatch."""

        return self._dispatch_coordinator.role_registry.get(dispatch_spec.role_id).agent_id

    def _artifact_root_for_run(self, run_id: str) -> Path:
        """Resolve the artifact root path for a run.

        Args:
            run_id: The run identifier.

        Returns:
            Path to artifact root (e.g. ``.vectl/runs``).
        """
        return (
            self._run_registry(config=self._effective_orchestration_config())
            .run_artifact_root(run_id)
            .parent
        )

    def _resolve_workspace_for_run(self, run_id: str) -> Path:
        """Resolve the workspace path for a run.

        For isolated worktree executions, returns the worktree path.
        Falls back to the configured workspace root for non-isolated runs.

        Args:
            run_id: The run identifier.

        Returns:
            Absolute path to the execution workspace.
        """
        if not run_id:
            config = self._effective_orchestration_config()
            return Path(config.runtime.workspace_root)
        config = self._effective_orchestration_config()
        return Path(config.runtime.workspace_root)

    def _validate_dispatch_authority(self, dispatch_spec: DispatchSpec) -> str:
        """Validate dispatch authority before any mechanical runtime launch.

        Enforces that runtime launch metadata cannot drift from centralized role
        policy authority. This protects main-worktree families (planner,
        reviewer, resolver) from accidental execution-context downgrade.

        Returns:
            Stable runtime work-ref proving PromptRegistry rendered the live bundle.
        """
        prompt_bundle = self._render_validated_prompt_bundle(dispatch_spec)
        return self._prompt_bundle_work_ref(prompt_bundle)

    def _prompt_bundle_work_ref(self, prompt_bundle: PromptBundle) -> str:
        """Return stable work-ref proving PromptRegistry rendered the live bundle."""

        digest = hashlib.sha256()
        digest.update(prompt_bundle.system_prompt.encode("utf-8"))
        digest.update(b"\x00")
        digest.update(prompt_bundle.task_prompt.encode("utf-8"))
        for message in prompt_bundle.messages:
            digest.update(b"\x00")
            digest.update(message.get("role", "").encode("utf-8"))
            digest.update(b":")
            digest.update(message.get("content", "").encode("utf-8"))
        return f"prompt_bundle_sha256={digest.hexdigest()}"

    # @invar:allow function_size: Admission/start persistence must remain an ordered lock path for recovery-gate and event sequencing safety.
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

    def _consume_control_requests(
        self,
        *,
        run_id: str,
        execution_id: str,
        _registry: RunRegistry,
        _agent: str,
        step_id: str,
        _run_root: Path,
    ) -> Literal["continue_dispatch", "pause_dispatch", "stop_dispatch"] | None:
        """Check and consume pending control channel requests for a running execution.

        Authority:
            docs/ORCHESTRATION-PLANE-CLI-CONFIG-OBSERVABILITY-DESIGN.md §7.4
                'control requests must be consumed, not only queued'
            docs/ORCHESTRATION-PLANE-CLI-CONFIG-OBSERVABILITY-DESIGN.md §9.3
                'Acknowledgement creates applied/ receipt'

        This method checks the control channel for pending requests targeting
        the given run_id and acts on them:
          - control.pause: acknowledges as applied, returns pause_dispatch
          - control.unpause: acknowledges as applied, returns continue_dispatch
          - control.stop: terminates the runner, acknowledges as applied,
            emits a control_stop event, and returns stop_dispatch

        Returns:
            Dispatch directive:
              - None: no pending control requests
              - "continue_dispatch": unpause consumed, continue normally
              - "pause_dispatch": pause consumed, should pause dispatch
              - "stop_dispatch": stop consumed, runner terminated
        """
        channel = self._control_channel()
        pending = channel.list_requests(run_id, status="pending")
        if not pending:
            return None

        for request in pending:
            action_id = request.action_id
            msg_type = request.msg_type

            if msg_type == "control.pause":
                # Acknowledge the pause request as applied.
                channel.acknowledge_applied(run_id=run_id, action_id=action_id)
                self._emit_event(
                    kind="control_pause",
                    step_id=step_id,
                    agent="operator",
                    payload={
                        "run_id": run_id,
                        "action_id": action_id,
                        "status": "applied",
                    },
                )
                self._append_log(
                    f"run_id={run_id} step_id={step_id} control_pause_applied action_id={action_id}"
                )
                return "pause_dispatch"

            if msg_type == "control.unpause":
                # Acknowledge the unpause request as applied.
                channel.acknowledge_applied(run_id=run_id, action_id=action_id)
                self._emit_event(
                    kind="control_unpause",
                    step_id=step_id,
                    agent="operator",
                    payload={
                        "run_id": run_id,
                        "action_id": action_id,
                        "status": "applied",
                    },
                )
                self._append_log(
                    f"run_id={run_id} step_id={step_id} control_unpause_applied "
                    f"action_id={action_id}"
                )
                return "continue_dispatch"

            if msg_type == "control.stop":
                # Terminate the runner and acknowledge as applied.
                terminated = self._runtime.terminate_execution(execution_id)
                channel.acknowledge_applied(run_id=run_id, action_id=action_id)
                self._emit_event(
                    kind="control_stop",
                    step_id=step_id,
                    agent="operator",
                    payload={
                        "run_id": run_id,
                        "action_id": action_id,
                        "status": "applied",
                        "runner_terminated": terminated,
                    },
                )
                self._append_log(
                    f"run_id={run_id} step_id={step_id} control_stop_applied "
                    f"action_id={action_id} runner_terminated={terminated}"
                )
                return "stop_dispatch"

            # Unknown request type — reject it.
            channel.acknowledge_rejected(
                run_id=run_id, action_id=action_id, reason=f"unknown msg_type: {msg_type}"
            )
            self._emit_event(
                kind="operator_action_requested",
                step_id=step_id,
                agent="operator",
                payload={
                    "run_id": run_id,
                    "action_id": action_id,
                    "status": "rejected",
                    "reason": f"unknown msg_type: {msg_type}",
                },
            )

        return None

    # @invar:allow function_size: Terminal collection preserves polling, control consumption, reconcile, and completion routing in one stateful shell loop.
    def _collect_and_route_terminal(
        self,
        *,
        registry: RunRegistry,
        run_id: str,
        step_id: str,
        execution_id: str,
        dispatch_spec: DispatchSpec,
        agent: str,
        run_root: Path,
        max_poll_iterations: int = 600,
        poll_interval_seconds: float = 0.1,
    ) -> OrchestrationResult:
        """Collect runner output and route through reconcile/completion.

        Authority:
            docs/ORCHESTRATION-PLANE-ARCHITECTURE.md sections 6.1, 6.2
            docs/ORCHESTRATION-PLANE-RUNTIME-WORKTREE-LIFECYCLE.md
            docs/ORCHESTRATION-PLANE-CLI-CONFIG-OBSERVABILITY-DESIGN.md §7.4
                'control actions must be consumed, not only queued'

        This orchestration loop polls runtime.collect() until the runner
        subprocess completes, AND checks the control channel for pending
        operator requests (pause/unpause/stop) on each iteration.

        Control request consumption:
            - control.pause: acknowledged as applied, dispatch pauses
            - control.unpause: acknowledged as applied, dispatch resumes
            - control.stop: runner is terminated, run transitions to
              terminal stopped state

        Args:
            registry: Run registry for status persistence.
            run_id: Run identifier.
            step_id: Step being executed.
            execution_id: Runtime execution identifier from start().
            dispatch_spec: Dispatch spec used for the run.
            agent: Agent name that claimed the step.
            run_root: Artifact root for this run.
            max_poll_iterations: Maximum poll iterations before giving up.
            poll_interval_seconds: Sleep interval between poll attempts.

        Returns:
            OrchestrationResult with terminal outcome.
        """
        execution_result: ExecutionResult | None = None
        for _ in range(max_poll_iterations):
            # Check for pending control channel requests.
            # Authority: §7.4 — 'control actions must be consumed, not only queued'
            # Pause state is persisted in the run registry (status="paused"),
            # not in a local variable. The dispatch loop continues polling the
            # runner while paused — the runner subprocess keeps running.
            control_directive = self._consume_control_requests(
                run_id=run_id,
                execution_id=execution_id,
                _registry=registry,
                _agent=agent,
                step_id=step_id,
                _run_root=run_root,
            )

            if control_directive == "stop_dispatch":
                # Runner has been terminated by the stop request.
                # Transition run to terminal stopped state.
                now_ts = time.time()
                registry.save(
                    RunRecord(
                        run_id=run_id,
                        step_id=step_id,
                        plan_path=str(self._config.plan_path),
                        agent=agent,
                        status="fail",
                        artifact_root=str(run_root),
                        finished_at=now_ts,
                        output_summary=(
                            f"Run stopped by operator control request for step {step_id}"
                        ),
                    )
                )
                self._emit_event(
                    kind="run_final",
                    step_id=step_id,
                    agent=agent,
                    payload={"run_id": run_id, "final_status": "stopped"},
                )
                self._append_log(f"run_id={run_id} step_id={step_id} status=stopped agent={agent}")
                return OrchestrationResult(
                    success=False,
                    message=f"Run stopped by operator control request for step {step_id}",
                    step_id=step_id,
                    run_id=run_id,
                )

            if control_directive == "pause_dispatch":
                registry.save(
                    RunRecord(
                        run_id=run_id,
                        step_id=step_id,
                        plan_path=str(self._config.plan_path),
                        agent=agent,
                        status="paused",
                        artifact_root=str(run_root),
                        output_summary=(
                            f"Run paused by operator control request for step {step_id}"
                        ),
                    )
                )
                self._emit_event(
                    kind="run_status_changed",
                    step_id=step_id,
                    agent=agent,
                    payload={"run_id": run_id, "status": "paused"},
                )

            if control_directive == "continue_dispatch":
                # Unpause consumed — resume from paused state.
                registry.save(
                    RunRecord(
                        run_id=run_id,
                        step_id=step_id,
                        plan_path=str(self._config.plan_path),
                        agent=agent,
                        status="running",
                        artifact_root=str(run_root),
                        output_summary=(
                            f"Run unpaused by operator control request for step {step_id}"
                        ),
                    )
                )
                self._emit_event(
                    kind="run_status_changed",
                    step_id=step_id,
                    agent=agent,
                    payload={"run_id": run_id, "status": "running"},
                )

            # If paused, keep polling but don't advance dispatch. The runner
            # subprocess continues running; we just don't advance new steps.
            # When unpaused, dispatch resumes normally.

            result = self._runtime.collect(execution_id)
            if result is None:
                time.sleep(poll_interval_seconds)
                continue
            execution_result = result
            break

        if execution_result is None:
            now_ts = time.time()
            registry.save(
                RunRecord(
                    run_id=run_id,
                    step_id=step_id,
                    plan_path=str(self._config.plan_path),
                    agent=agent,
                    status="stall",
                    artifact_root=str(run_root),
                    finished_at=now_ts,
                    output_summary=(
                        f"Runner did not complete within poll window "
                        f"(max_iterations={max_poll_iterations})"
                    ),
                )
            )
            self._emit_event(
                kind="run_final",
                step_id=step_id,
                agent=agent,
                payload={"run_id": run_id, "final_status": "stall"},
            )
            return OrchestrationResult(
                success=False,
                message=f"Runner did not complete within poll window for step {step_id}",
                step_id=step_id,
                run_id=run_id,
            )

        resolution_case = self.route_terminal_execution(
            step_id=step_id,
            execution_id=execution_id,
            dispatch_spec=dispatch_spec,
            execution_result=execution_result,
        )

        # Determine final terminal status from the outcome.
        if resolution_case is None:
            # Step was completed successfully through the reconcile/completion path.
            final_status: Literal["success", "fail", "stall"] = "success"
            output_summary = execution_result.output_summary
        else:
            # A resolution case was created — this is a non-closure that
            # needs operator/resolver attention. Mark as fail so the run
            # record reflects the need for intervention.
            final_status = "fail"
            output_summary = (
                f"Resolution case created: {resolution_case.reason} "
                f"(case_id={resolution_case.case_id})"
            )

        now_ts = time.time()
        registry.save(
            RunRecord(
                run_id=run_id,
                step_id=step_id,
                plan_path=str(self._config.plan_path),
                agent=agent,
                status=final_status,
                artifact_root=str(run_root),
                finished_at=now_ts,
                output_summary=output_summary,
            )
        )
        self._emit_event(
            kind="run_final",
            step_id=step_id,
            agent=agent,
            payload={"run_id": run_id, "final_status": final_status},
        )
        self._append_log(
            " ".join(
                (
                    f"run_id={run_id}",
                    f"step_id={step_id}",
                    f"status={final_status}",
                    f"agent={agent}",
                    f"execution_result_status={execution_result.status}",
                )
            )
        )

        if resolution_case is not None:
            return OrchestrationResult(
                success=False,
                message=(
                    f"Step {step_id} produced a resolution case: {resolution_case.reason} "
                    f"(case_id={resolution_case.case_id})"
                ),
                step_id=step_id,
                run_id=run_id,
            )

        return OrchestrationResult(
            success=True,
            message=(
                f"Step {step_id} completed successfully (reconcile_disposition=merged or noop)"
            ),
            step_id=step_id,
            run_id=run_id,
        )

    # -----------------------------------------------------------------
    # Drive-scoped entrypoints
    # Authority: docs/RFC-orch-drive.md sections 7, 10, 14, 15
    # -----------------------------------------------------------------

    def _drive_store(self) -> DriveStore:
        """Return a DriveStore rooted at the configured run store root.

        Authority: docs/RFC-orch-drive.md section 8.1

        The drive store shares the same root directory as the run registry
        for co-located persistence.
        """
        config = self._effective_orchestration_config()
        root = self._config.run_store_root or config.runtime.artifact_root
        return DriveStore(store_root=Path(root) / "drives")

    def _launch_drive_step_child_run(
        self,
        drive_id: str,
        step_id: str,
        role_id: str,
    ) -> ChildRunRef:
        """Launch one drive-owned step child run and persist run-level state."""

        resolved_config = self._effective_orchestration_config()
        registry = self._run_registry(config=resolved_config)
        run_id = generate_run_id()
        run_root = registry.run_artifact_root(run_id)
        frozen_snapshot = freeze_config(resolved_config, run_dir=run_root)
        self._write_continuity_bootstrap(
            run_id=run_id,
            step_id=step_id,
            frozen_config=frozen_snapshot.config,
            run_root=run_root,
            runner=frozen_snapshot.config.runtime.default_runner,
        )
        allowlist_text = self._allowlist_text(frozen_snapshot.config.resolver.tool_allowlist)

        try:
            self._core_adapter.claim_step(step_id, role_id, flow="normal")
            dispatch_spec = self.build_dispatch_spec(step_id=step_id, role_hint=role_id)
            child_ref = self._start_runtime_child_execution(
                drive_id=drive_id,
                run_id=run_id,
                step_id=step_id,
                dispatch_spec=dispatch_spec,
                mode="start",
            )
        except Exception as exc:
            now_ts = time.time()
            registry.save(
                RunRecord(
                    run_id=run_id,
                    step_id=step_id,
                    plan_path=str(self._config.plan_path),
                    agent=role_id,
                    status="fail",
                    artifact_root=str(run_root),
                    finished_at=now_ts,
                    output_summary=(
                        "drive child runtime start failed after durable admission: "
                        f"{exc}"
                    ),
                )
            )
            raise

        registry.save(
            RunRecord(
                run_id=child_ref.run_id,
                step_id=step_id,
                plan_path=str(self._config.plan_path),
                agent=role_id,
                status="running",
                artifact_root=str(run_root),
                output_summary=(
                    "drive child initialized with frozen snapshot "
                    f"drive_id={drive_id} runner={frozen_snapshot.config.runtime.default_runner} "
                    f"allowlist={allowlist_text} workspace={child_ref.workspace}"
                ),
            )
        )
        self._drive_child_run_contexts[child_ref.run_id] = DriveChildRunContext(
            step_id=step_id,
            role_id=role_id,
            dispatch_spec=dispatch_spec,
            run_root=run_root,
        )

        self._emit_event(
            kind="run_started",
            step_id=step_id,
            agent=role_id,
            payload={"run_id": child_ref.run_id, "plan_path": str(self._config.plan_path)},
        )
        self._emit_event(
            kind="run_status_changed",
            step_id=step_id,
            agent=role_id,
            payload={"run_id": child_ref.run_id, "status": "running"},
        )
        self._append_log(
            " ".join(
                (
                    f"drive_id={drive_id}",
                    f"run_id={child_ref.run_id}",
                    f"step_id={step_id}",
                    "status=running",
                    f"agent={role_id}",
                    f"workspace={child_ref.workspace}",
                )
            )
        )
        return child_ref

    def _cancel_drive_child_run(self, child_run_id: str) -> bool:
        """Best-effort cancellation for a drive child run owned by this process."""

        terminated = self._runtime.terminate_execution(child_run_id)
        context = self._drive_child_run_contexts.get(child_run_id)
        if context is not None:
            registry = self._run_registry(config=self._effective_orchestration_config())
            registry.save(
                RunRecord(
                    run_id=child_run_id,
                    step_id=context.step_id,
                    plan_path=str(self._config.plan_path),
                    agent=context.role_id,
                    status="fail",
                    artifact_root=str(context.run_root),
                    finished_at=time.time(),
                    output_summary="drive child cancelled by force stop",
                )
            )
        return terminated

    def _drive_driver(self) -> DriveDriver:
        """Return a DriveDriver wired with the app's components.

        Authority: docs/RFC-orch-drive.md sections 7, 10, 14, 15

        The driver composes the drive store, core adapter, and control
        to implement the four drive-surface entry points.
        """
        from vectl.orchestration.control import PlanAwareControl
        from vectl.orchestration.core_adapter import PlanPlannerMutationApplier

        if not isinstance(self._control, PlanAwareControl):
            raise TypeError(f"drive requires PlanAwareControl; got {type(self._control).__name__}")
        return DriveDriver(
            drive_store=self._drive_store(),
            core_adapter=self._core_adapter,
            control=self._control,
            resolver=self._resolver,
            run_registry=self._run_registry(config=self._effective_orchestration_config()),
            control_channel=self._control_channel(),
            child_run_launcher=self._launch_drive_step_child_run,
            child_run_canceller=self._cancel_drive_child_run,
            planner_mutation_applier=PlanPlannerMutationApplier(self._config.plan_path),
            max_parallelism=4,
        )

    def start_drive(
        self,
        *,
        agent: str = "",
        max_parallelism: int = 4,
    ) -> DriveStartResult:
        """Start or resolve a drive for the current plan.

        Authority: docs/RFC-orch-drive.md sections 7.2, 14.1

        If an active drive already exists for the same plan, this raises
        ``DriveAdmissionError`` with the existing ``drive_id``.

        Args:
            agent: Agent role that owns this drive.
            max_parallelism: Maximum concurrent step child runs (1-32, default 4).

        Returns:
            DriveStartResult with the drive identifier and initial state.

        Raises:
            DriveAdmissionError: If an active drive exists for this plan.
            MaxParallelismError: If ``max_parallelism`` is outside [1, 32].
        """
        plan_path = str(self._config.plan_path.resolve())
        return self._drive_driver().start_drive(
            plan_path=plan_path,
            agent=agent or self._config.default_agent,
            max_parallelism=max_parallelism,
        )

    def run_drive_loop(self, drive_id: str) -> DriveLoopResult:
        """Execute one drive scheduling loop pass.

        Authority: docs/RFC-orch-drive.md section 10

        Args:
            drive_id: The drive to run one loop pass for.

        Returns:
            DriveLoopResult capturing the terminal or paused state.
        """
        result = self._drive_driver().run_drive_loop(drive_id)
        if result.status in TERMINAL_DRIVE_STATUSES:
            self._cleanup_drive_workspaces_for_terminal_status(
                drive_id=drive_id,
                terminal_status=result.status,
            )
        return result

    # @invar:allow function_size: Foreground drive supervision keeps polling, progress callbacks, control handling, and terminal cleanup in one active-drive shell loop.
    def run_drive_foreground(
        self,
        drive_id: str,
        *,
        poll_interval_seconds: float = 2.0,
        status_interval_seconds: float = 30.0,
        progress_callback: Callable[[dict[str, Any]], None] | None = None,
        progress_mode: str = "auto",
        verbose: bool = False,
    ) -> DriveLoopResult:
        """Run a drive as a foreground supervisor until it stops or blocks.

        The one-shot drive loop only admits work.  The foreground supervisor
        keeps the runtime process alive so runner handles remain collectable,
        consumes drive control requests, finalizes terminal child runs, and
        re-enters the scheduling loop until the drive reaches a terminal or
        operator/blocking state.
        """

        start_time = time.time()
        # Foreground supervision may attach to an already-active drive.  Seed
        # the progress cursor with persisted child runs so resumed supervision
        # does not replay historical runs as fresh ``child_dispatched`` events.
        # Any child launched by the first loop pass below will not be in this
        # set yet and will still be reported normally.
        known_child_run_ids: set[str] = {
            child.run_id for child in self.drive_runs(drive_id=drive_id)
        }
        last_snapshot_at = 0.0
        last_child_heartbeat_at: dict[str, float] = {}
        last_workspace_change_count: dict[str, int] = {}
        barrier_idle_key: tuple[str, str, tuple[str, ...], str] | None = None
        barrier_idle_passes = 0
        agent_recovery_attempted_keys: set[tuple[str, str, tuple[str, ...], str]] = set()

        initial_status = self.drive_status(drive_id=drive_id)
        self._emit_drive_progress_event(
            progress_callback,
            "drive_started",
            drive_id=drive_id,
            status=initial_status.status,
            max_parallelism=self._drive_max_parallelism(drive_id),
            poll_interval_seconds=poll_interval_seconds,
            status_interval_seconds=status_interval_seconds,
            progress_mode=progress_mode,
        )

        last_result = self.run_drive_loop(drive_id)
        while True:
            status = self.drive_status(drive_id=drive_id)
            now = time.time()
            snapshot_due = (
                last_snapshot_at == 0.0
                or now - last_snapshot_at >= max(1.0, status_interval_seconds)
            )
            if snapshot_due:
                self._emit_drive_progress_event(
                    progress_callback,
                    "status_snapshot",
                    **self._drive_progress_snapshot(
                        status=status,
                        started_at=start_time,
                    ),
                )
                last_snapshot_at = now

            self._emit_new_child_dispatch_events(
                progress_callback=progress_callback,
                drive_id=drive_id,
                known_child_run_ids=known_child_run_ids,
            )

            if self._foreground_drive_should_exit(status):
                agent_recovery_key = self._foreground_agent_recovery_key(last_result)
                if (
                    agent_recovery_key is not None
                    and agent_recovery_key not in agent_recovery_attempted_keys
                ):
                    agent_recovery_attempted_keys.add(agent_recovery_key)
                    pass
                elif self._foreground_should_continue_after_control_consumption(
                    status=status,
                    last_result=last_result,
                ):
                    pass
                else:
                    final_result = self._drive_status_to_loop_result(status)
                    if final_result.status in TERMINAL_DRIVE_STATUSES:
                        self._cleanup_drive_workspaces_for_terminal_status(
                            drive_id=drive_id,
                            terminal_status=final_result.status,
                        )
                    self._emit_terminal_drive_progress(
                        progress_callback=progress_callback,
                        result=final_result,
                        started_at=start_time,
                    )
                    return final_result

            if status.active_child_run_ids:
                collected = self._collect_drive_child_runs(
                    progress_callback=progress_callback,
                    drive_id=drive_id,
                    child_run_ids=status.active_child_run_ids,
                )
                if collected:
                    continue
                self._emit_child_running_progress(
                    progress_callback=progress_callback,
                    child_run_ids=status.active_child_run_ids,
                    started_at=start_time,
                    last_child_heartbeat_at=last_child_heartbeat_at,
                    last_workspace_change_count=last_workspace_change_count,
                    status_interval_seconds=status_interval_seconds,
                    verbose=verbose,
                )
                time.sleep(max(0.1, poll_interval_seconds))
                last_result = self.run_drive_loop(drive_id)
                if self._foreground_loop_result_should_exit(last_result):
                    agent_recovery_key = self._foreground_agent_recovery_key(last_result)
                    if (
                        agent_recovery_key is not None
                        and agent_recovery_key not in agent_recovery_attempted_keys
                    ):
                        agent_recovery_attempted_keys.add(agent_recovery_key)
                        continue
                    if last_result.status in TERMINAL_DRIVE_STATUSES:
                        self._cleanup_drive_workspaces_for_terminal_status(
                            drive_id=drive_id,
                            terminal_status=last_result.status,
                        )
                    self._emit_terminal_drive_progress(
                        progress_callback=progress_callback,
                        result=last_result,
                        started_at=start_time,
                    )
                    return last_result
                barrier_idle_key, barrier_idle_passes, stalled_result = (
                    self._track_barrier_idle_result(
                        result=last_result,
                        prior_key=barrier_idle_key,
                        prior_passes=barrier_idle_passes,
                    )
                )
                if stalled_result is not None:
                    return stalled_result
                continue

            last_result = self.run_drive_loop(drive_id)
            if self._foreground_loop_result_should_exit(last_result):
                agent_recovery_key = self._foreground_agent_recovery_key(last_result)
                if (
                    agent_recovery_key is not None
                    and agent_recovery_key not in agent_recovery_attempted_keys
                ):
                    agent_recovery_attempted_keys.add(agent_recovery_key)
                    continue
                if last_result.status in TERMINAL_DRIVE_STATUSES:
                    self._cleanup_drive_workspaces_for_terminal_status(
                        drive_id=drive_id,
                        terminal_status=last_result.status,
                    )
                self._emit_terminal_drive_progress(
                    progress_callback=progress_callback,
                    result=last_result,
                    started_at=start_time,
                )
                return last_result
            barrier_idle_key, barrier_idle_passes, stalled_result = self._track_barrier_idle_result(
                result=last_result,
                prior_key=barrier_idle_key,
                prior_passes=barrier_idle_passes,
            )
            if stalled_result is not None:
                return stalled_result
            if last_result.barrier is not None:
                time.sleep(max(0.1, poll_interval_seconds))

    @staticmethod
    def _foreground_drive_should_exit(status: DriveStatusResult) -> bool:
        return status.status in {
            "paused",
            "blocked_operator",
            "completed",
            "halted",
            "failed_unrecoverable",
            "stopped",
        }

    @staticmethod
    def _foreground_loop_result_should_exit(result: DriveLoopResult) -> bool:
        return result.status in {
            "paused",
            "blocked_operator",
            "completed",
            "halted",
            "failed_unrecoverable",
            "stopped",
        }

    @staticmethod
    def _foreground_should_continue_after_control_consumption(
        *,
        status: DriveStatusResult,
        last_result: DriveLoopResult,
    ) -> bool:
        """Let foreground supervision evaluate once after consuming unpause.

        ``run_drive_loop`` intentionally returns immediately after applying a
        drive control message so the consumption summary is not overwritten.
        In foreground mode, an unpause consumed while a runtime barrier remains
        should be followed by the next control evaluation in the same process;
        otherwise operators must run ``vectl orch drive`` twice after unpause.
        """

        if status.status != "blocked_operator" or last_result.status != "blocked_operator":
            return False
        if not last_result.summary.startswith("operator unpause consumed:"):
            return False
        return status.summary == last_result.summary

    @staticmethod
    def _foreground_agent_recovery_key(
        result: DriveLoopResult,
    ) -> tuple[str, str, tuple[str, ...], str] | None:
        """Return a one-shot key when a blocked drive should get resolver help."""

        if result.status != "blocked_operator":
            return None
        if result.barrier is None or not result.barrier.case_ids:
            return None
        if result.barrier.reason == "operator_pause":
            return None
        summary_lower = result.summary.lower()
        if any(
            token in summary_lower
            for token in (
                "resolver requires operator",
                "planner requires operator intervention",
                "operator pause consumed",
                "operator stop consumed",
                "agent-assisted recovery skipped",
                "agent-assisted recovery unavailable",
            )
        ):
            return None
        return (
            result.status,
            result.barrier.reason,
            result.barrier.case_ids,
            result.summary[:160],
        )

    @staticmethod
    def _track_barrier_idle_result(
        *,
        result: DriveLoopResult,
        prior_key: tuple[str, str, tuple[str, ...], str] | None,
        prior_passes: int,
    ) -> tuple[tuple[str, str, tuple[str, ...], str] | None, int, DriveLoopResult | None]:
        if result.barrier is None or result.status not in {"resolving", "replanning"}:
            return None, 0, None
        key = (
            result.status,
            result.barrier.reason,
            result.barrier.case_ids,
            result.summary,
        )
        passes = prior_passes + 1 if key == prior_key else 1
        if passes < 3:
            return key, passes, None
        return (
            key,
            passes,
            DriveLoopResult(
                drive_id=result.drive_id,
                status=result.status,
                completed_steps=result.completed_steps,
                active_child_run_ids=result.active_child_run_ids,
                barrier=result.barrier,
                summary=(
                    "automation stopped: barrier did not progress after "
                    f"{passes} passes; {result.summary}"
                ),
            ),
        )

    @staticmethod
    def _drive_status_to_loop_result(status: DriveStatusResult) -> DriveLoopResult:
        return DriveLoopResult(
            drive_id=status.drive_id,
            status=status.status,
            completed_steps=(),
            active_child_run_ids=status.active_child_run_ids,
            barrier=status.barrier,
            summary=status.summary,
        )

    @staticmethod
    def _emit_drive_progress_event(
        progress_callback: Callable[[dict[str, Any]], None] | None,
        event_type: str,
        **payload: Any,
    ) -> None:
        if progress_callback is None:
            return
        event = {"type": event_type, "timestamp": time.time(), **payload}
        progress_callback(event)

    def _drive_max_parallelism(self, drive_id: str) -> int:
        record = self._drive_store().replay_drive_state(drive_id)
        if record is None:
            return 0
        return record.max_parallelism

    def _drive_progress_snapshot(
        self,
        *,
        status: DriveStatusResult,
        started_at: float,
    ) -> dict[str, Any]:
        total_steps = 0
        done_steps = 0
        try:
            plan, _ = load_plan_definition(self._config.plan_path)
            for phase in plan.phases:
                for step in phase.steps:
                    total_steps += 1
                    if step.status in (StepStatus.DONE, StepStatus.SKIPPED):
                        done_steps += 1
        except Exception:
            total_steps = 0
            done_steps = 0

        return {
            "drive_id": status.drive_id,
            "status": status.status,
            "elapsed_seconds": time.time() - started_at,
            "total_steps": total_steps,
            "done_steps": done_steps,
            "running_count": len(status.active_child_run_ids),
            "max_parallelism": self._drive_max_parallelism(status.drive_id),
            "ready_count": len(status.frontier_step_ids),
            "blocked_count": len(status.blocked_case_ids),
            "case_count": len(status.blocked_case_ids),
            "active_child_run_ids": status.active_child_run_ids,
            "frontier_step_ids": status.frontier_step_ids,
            "blocked_case_ids": status.blocked_case_ids,
            "summary": status.summary,
        }

    def _emit_new_child_dispatch_events(
        self,
        *,
        progress_callback: Callable[[dict[str, Any]], None] | None,
        drive_id: str,
        known_child_run_ids: set[str],
    ) -> None:
        for child in self.drive_runs(drive_id=drive_id):
            if child.run_id in known_child_run_ids:
                continue
            known_child_run_ids.add(child.run_id)
            context = self._drive_child_run_contexts.get(child.run_id)
            self._emit_drive_progress_event(
                progress_callback,
                "child_dispatched",
                drive_id=drive_id,
                run_id=child.run_id,
                step_id=child.step_id,
                status=child.status,
                runner=child.runner,
                agent=(context.role_id if context is not None else child.runner),
                workspace=child.workspace,
                session_id=child.session_id,
            )

    def _emit_terminal_drive_progress(
        self,
        *,
        progress_callback: Callable[[dict[str, Any]], None] | None,
        result: DriveLoopResult,
        started_at: float,
    ) -> None:
        event_type = "drive_terminal"
        blocked_statuses = {"resolving", "replanning", "blocked_operator"}
        if result.status not in TERMINAL_DRIVE_STATUSES and (
            result.barrier is not None or result.status in blocked_statuses
        ):
            event_type = "drive_blocked"
        self._emit_drive_progress_event(
            progress_callback,
            event_type,
            drive_id=result.drive_id,
            status=result.status,
            elapsed_seconds=time.time() - started_at,
            active_child_run_ids=result.active_child_run_ids,
            case_ids=(result.barrier.case_ids if result.barrier is not None else ()),
            summary=result.summary,
        )

    def _emit_child_running_progress(
        self,
        *,
        progress_callback: Callable[[dict[str, Any]], None] | None,
        child_run_ids: tuple[str, ...],
        started_at: float,
        last_child_heartbeat_at: dict[str, float],
        last_workspace_change_count: dict[str, int],
        status_interval_seconds: float,
        verbose: bool,
    ) -> None:
        now = time.time()
        interval = max(1.0, status_interval_seconds)
        for child_run_id in child_run_ids:
            child = self._drive_store().child_run_by_id(child_run_id)
            if child is None:
                continue
            workspace_changes = self._workspace_change_count(child.artifact_root)
            last_at = last_child_heartbeat_at.get(child_run_id, 0.0)
            last_changes = last_workspace_change_count.get(child_run_id)
            if last_at and now - last_at < interval and workspace_changes == last_changes:
                continue
            last_child_heartbeat_at[child_run_id] = now
            last_workspace_change_count[child_run_id] = workspace_changes
            self._emit_drive_progress_event(
                progress_callback,
                "child_running",
                drive_id=child.drive_id,
                run_id=child.run_id,
                step_id=child.step_id,
                status=child.status,
                runner=child.runner,
                workspace=child.workspace,
                workspace_changes=workspace_changes,
                elapsed_seconds=now - started_at,
                idle_seconds=now - last_at if last_at else 0.0,
                verbose=verbose,
            )

    @staticmethod
    def _workspace_change_count(path_text: str) -> int:
        if not path_text:
            return 0
        workspace_path = Path(path_text)
        if not workspace_path.exists():
            return 0
        try:
            result = subprocess.run(
                ["git", "-C", str(workspace_path), "status", "--short"],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
        except Exception:
            return 0
        if result.returncode != 0:
            return 0
        return len([line for line in result.stdout.splitlines() if line.strip()])

    def _collect_drive_child_runs(
        self,
        *,
        progress_callback: Callable[[dict[str, Any]], None] | None,
        drive_id: str,
        child_run_ids: tuple[str, ...],
    ) -> int:
        """Poll active child runs and finalize any terminal child."""

        store = self._drive_store()
        collected = 0
        for child_run_id in child_run_ids:
            execution_result, updated_ref = self._runtime.collect_child_run(child_run_id)
            if execution_result is None:
                continue
            if updated_ref is None:
                persisted_ref = store.child_run_by_id(child_run_id)
                if persisted_ref is not None:
                    status = self._child_status_from_execution(execution_result.status)
                    updated_ref = replace(persisted_ref, status=status)
            if updated_ref is not None:
                store.save_child_run(updated_ref)
            self._emit_drive_progress_event(
                progress_callback,
                "child_completed",
                drive_id=drive_id,
                run_id=child_run_id,
                step_id=execution_result.step_id,
                status=execution_result.status,
                summary=execution_result.output_summary,
            )
            self._finalize_drive_child_run(
                progress_callback=progress_callback,
                drive_id=drive_id,
                child_run_id=child_run_id,
                execution_result=execution_result,
            )
            collected += 1
        return collected

    @staticmethod
    def _child_status_from_execution(status: str) -> str:
        if status in {"success", "fail", "stall", "transport_error"}:
            return status
        return "fail"

    # @invar:allow function_size: Child finalization must route review, reconcile, recovery case creation, and drive refresh atomically for one terminal result.
    def _finalize_drive_child_run(
        self,
        *,
        progress_callback: Callable[[dict[str, Any]], None] | None,
        drive_id: str,
        child_run_id: str,
        execution_result: ExecutionResult,
    ) -> None:
        """Route one terminal child result through reconcile and drive state."""

        context = self._drive_child_run_contexts.get(child_run_id)
        if context is None:
            snapshots = self.refresh_snapshots(agent=self._config.default_agent)
            resolution_case = ResolutionCase(
                case_id=f"case-{generate_run_id()}",
                case_source="runtime_failure",
                reason="drive child finalization context missing",
                summary=execution_result.output_summary,
                core=snapshots.core,
                roster=snapshots.roster,
                runtime=snapshots.runtime,
                blocked_step_ids=(execution_result.step_id,),
            )
            self._emit_drive_progress_event(
                progress_callback,
                "case_opened",
                drive_id=drive_id,
                run_id=child_run_id,
                step_id=execution_result.step_id,
                case_id=resolution_case.case_id,
                reason=resolution_case.reason,
                summary=execution_result.output_summary,
            )
            self._record_drive_child_failure(
                drive_id=drive_id,
                child_run_id=child_run_id,
                resolution_case=resolution_case,
                output_summary=execution_result.output_summary,
            )
            return

        try:
            resolution_case = self.route_terminal_execution(
                step_id=context.step_id,
                execution_id=child_run_id,
                dispatch_spec=context.dispatch_spec,
                execution_result=execution_result,
            )
        except Exception as exc:
            snapshots = self.refresh_snapshots(agent=context.role_id)
            resolution_case = ResolutionCase(
                case_id=f"case-{generate_run_id()}",
                case_source="runtime_failure",
                reason=f"drive child finalization failed: {exc}",
                summary=execution_result.output_summary,
                core=snapshots.core,
                roster=snapshots.roster,
                runtime=snapshots.runtime,
                blocked_step_ids=(context.step_id,),
            )

        registry = self._run_registry(config=self._effective_orchestration_config())
        final_status: Literal["success", "fail", "stall"]
        if resolution_case is None:
            final_status = "success"
            output_summary = execution_result.output_summary
        else:
            final_status = "fail"
            output_summary = (
                f"Resolution case created: {resolution_case.reason} "
                f"(case_id={resolution_case.case_id})"
            )
        registry.save(
            RunRecord(
                run_id=child_run_id,
                step_id=context.step_id,
                plan_path=str(self._config.plan_path),
                agent=context.role_id,
                status=final_status,
                artifact_root=str(context.run_root),
                finished_at=time.time(),
                output_summary=output_summary,
            )
        )
        self._emit_event(
            kind="run_final",
            step_id=context.step_id,
            agent=context.role_id,
            payload={"run_id": child_run_id, "final_status": final_status},
        )
        if resolution_case is None:
            self._emit_drive_progress_event(
                progress_callback,
                "step_completed",
                drive_id=drive_id,
                run_id=child_run_id,
                step_id=context.step_id,
            )
            self._record_drive_child_success(
                drive_id=drive_id,
                child_run_id=child_run_id,
                step_id=context.step_id,
            )
        else:
            self._emit_drive_progress_event(
                progress_callback,
                "case_opened",
                drive_id=drive_id,
                run_id=child_run_id,
                step_id=context.step_id,
                case_id=resolution_case.case_id,
                reason=resolution_case.reason,
                summary=output_summary,
            )
            self._record_drive_child_failure(
                drive_id=drive_id,
                child_run_id=child_run_id,
                resolution_case=resolution_case,
                output_summary=output_summary,
            )
        self._drive_child_run_contexts.pop(child_run_id, None)

    def _record_drive_child_success(
        self,
        *,
        drive_id: str,
        child_run_id: str,
        step_id: str,
    ) -> None:
        store = self._drive_store()
        record = store.replay_drive_state(drive_id)
        if record is None:
            return
        active_ids = tuple(rid for rid in record.active_child_run_ids if rid != child_run_id)
        store.save_drive(
            replace(
                record,
                updated_at=time.time(),
                active_child_run_ids=active_ids,
                summary=f"child run {child_run_id} completed step {step_id}",
            )
        )
        child_ref = store.child_run_by_id(child_run_id)
        if child_ref is not None:
            self._cleanup_child_run_worktree(child_ref=child_ref, force=False)

    def _cleanup_drive_workspaces_for_terminal_status(
        self,
        *,
        drive_id: str,
        terminal_status: str,
    ) -> None:
        """Sweep child-run worktrees when a drive reaches a terminal state."""

        policy = self._effective_orchestration_config().runtime.cleanup_policy
        if policy == "never":
            return
        store = self._drive_store()
        for child_ref in store.child_runs_for_drive(drive_id):
            if child_ref.status in {"pending", "running"}:
                continue
            if policy == "on-success" and child_ref.status != "success":
                continue
            self._cleanup_child_run_worktree(
                child_ref=child_ref,
                force=(policy == "always"),
                terminal_status=terminal_status,
            )

    def _cleanup_child_run_worktree(
        self,
        *,
        child_ref: ChildRunRef,
        force: bool,
        terminal_status: str | None = None,
    ) -> None:
        """Clean a child worktree by live workspace id, falling back to persisted path."""

        if not child_ref.workspace and not child_ref.artifact_root:
            return
        try:
            if child_ref.workspace:
                self._runtime.cleanup_child_run(child_ref.workspace, force=force)
                return
        except Exception as exc:
            self._append_log(
                f"drive_id={child_ref.drive_id} run_id={child_ref.run_id} "
                f"workspace_cleanup_by_id_skipped reason={exc}"
            )
            if not force and "not found" not in str(exc).lower():
                return
        if not child_ref.artifact_root:
            return
        try:
            self._runtime.cleanup_worktree_path(
                step_id=child_ref.step_id or child_ref.run_id,
                worktree_path=Path(child_ref.artifact_root),
                force=force,
            )
        except Exception as exc:
            suffix = f" terminal_status={terminal_status}" if terminal_status else ""
            self._append_log(
                f"drive_id={child_ref.drive_id} run_id={child_ref.run_id} "
                f"workspace_cleanup_by_path_skipped{suffix} reason={exc}"
            )

    def _record_drive_child_failure(
        self,
        *,
        drive_id: str,
        child_run_id: str,
        resolution_case: ResolutionCase,
        output_summary: str,
    ) -> None:
        store = self._drive_store()
        record = store.replay_drive_state(drive_id)
        if record is None:
            return
        active_ids = tuple(rid for rid in record.active_child_run_ids if rid != child_run_id)
        blocked_case_ids = tuple(
            dict.fromkeys((*record.blocked_case_ids, resolution_case.case_id))
        )
        barrier = DriveBarrier(
            reason="runtime_failure",
            entered_at=time.time(),
            case_ids=(resolution_case.case_id,),
            active_child_run_ids_at_entry=active_ids,
        )
        store.save_drive(
            replace(
                record,
                status="resolving",
                updated_at=time.time(),
                active_child_run_ids=active_ids,
                blocked_case_ids=blocked_case_ids,
                barrier=barrier,
                summary=(
                    f"child run {child_run_id} requires resolution: "
                    f"{resolution_case.reason}; {output_summary}"
                ),
            )
        )

    def resume_drive(self, drive_id: str) -> DriveResumeResult:
        """Resume an interrupted drive session.

        Authority: docs/RFC-orch-drive.md section 15.1

        Args:
            drive_id: The drive to resume.

        Returns:
            DriveResumeResult with restored state.
        """
        return self._drive_driver().resume_drive(drive_id)

    def recover_drive(self, drive_id: str, *, dry_run: bool = False) -> DriveRecoverResult:
        """Recover a drive from interrupted state.

        Authority: docs/RFC-orch-drive.md section 15.2, 15.3

        Args:
            drive_id: The drive to recover.
            dry_run: If True, compute recovery plan without applying changes.

        Returns:
            DriveRecoverResult with recovery outcomes.
        """
        return self._drive_driver().recover_drive(drive_id, dry_run=dry_run)

    def attempt_agent_assisted_drive_recovery(
        self,
        drive_id: str,
        *,
        reason: str = "",
        max_attempts: int = 2,
    ) -> DriveLoopResult:
        """Try resolver/planner automation before surfacing a human stop."""

        return self._drive_driver().attempt_agent_assisted_recovery(
            drive_id,
            reason=reason,
            max_attempts=max_attempts,
        )

    def drive_status(self, drive_id: str) -> DriveStatusResult:
        """Inspect current drive status.

        Authority: docs/RFC-orch-drive.md section 7.2

        Args:
            drive_id: The drive to inspect.

        Returns:
            DriveStatusResult with current drive state.
        """
        from vectl.orchestration.run_store import DriveStoreError

        store = self._drive_store()
        try:
            record = store.replay_drive_state(drive_id)
        except DriveStoreError:
            return DriveStatusResult(
                drive_id=drive_id,
                status="halted",
                summary=f"drive {drive_id} not found",
            )
        if record is None:
            return DriveStatusResult(
                drive_id=drive_id,
                status="halted",
                summary=f"drive {drive_id} not found",
            )
        return DriveStatusResult(
            drive_id=record.drive_id,
            status=record.status,
            active_child_run_ids=record.active_child_run_ids,
            frontier_step_ids=record.frontier_step_ids,
            blocked_case_ids=record.blocked_case_ids,
            barrier=record.barrier,
            summary=record.summary,
        )

    def drive_runs(self, drive_id: str) -> tuple[Any, ...]:
        """List child runs belonging to a drive.

        Authority: docs/RFC-orch-drive.md section 7.2

        Args:
            drive_id: The drive whose child runs to list.

        Returns:
            Tuple of ChildRunRef dataclass instances for the drive.
        """
        from vectl.orchestration.run_store import DriveStoreError

        store = self._drive_store()
        try:
            return store.child_runs_for_drive(drive_id)
        except DriveStoreError:
            return ()

    def resolve_latest_drive_id(self) -> str | None:
        """Resolve the most recently updated active drive ID.

        Authority: docs/RFC-orch-drive.md section 7.5

        Selects the most recently updated drive for the current plan.
        If no active drive exists, falls back to the most recently
        updated terminal drive. Returns None if no drives exist at all.

        Returns:
            The latest drive_id string, or None if no drives exist.
        """
        store = self._drive_store()
        plan_path = str(self._config.plan_path.resolve())
        active = store.active_drives()
        for record in active:
            if record.plan_path == plan_path:
                return record.drive_id
        all_drives = store.all_drives()
        for record in all_drives:
            if record.plan_path == plan_path:
                return record.drive_id
        if all_drives:
            return all_drives[0].drive_id
        return None

    def has_active_drive_for_plan(self) -> str | None:
        """Check whether an active drive exists for the current plan.

        Authority: docs/RFC-orch-drive.md section 7.1

        Returns:
            The active drive_id if one exists, or None.
        """
        plan_path = str(self._config.plan_path.resolve())
        store = self._drive_store()
        active = store.active_drive_for_plan(plan_path)
        if active is not None:
            return active.drive_id
        return None

    # @invar:allow function_size: Public run entrypoint preserves start admission, recovery gating, runtime dispatch, and terminal routing compatibility.
    def run(
        self,
        step_id: str | None = None,
        agent: str | None = None,
    ) -> OrchestrationResult:
        """
        Start a new orchestration run.

        Authority: docs/ORCHESTRATION-PLANE-ARCHITECTURE.md section 6.1
        Authority: docs/RFC-orch-drive.md section 7.1

        If an active drive exists for the same plan, this method rejects
        the run and returns an OrchestrationResult with success=False,
        including the active drive_id and an instruction to use drive-scoped
        commands instead.  Exit code boundary: callers should map this to
        exit code 2 (per RFC §7.1: "exit code: 2").

        Args:
            step_id: Optional explicit step to run. None means auto-select next.
            agent: Optional agent name. None uses default.

        Returns:
            OrchestrationResult with run outcome.

        Raises:
            OSError: When frozen snapshot persistence fails.
        """
        # Authority: RFC-orch-drive.md section 7.1
        # "orch run must fail with exit code 2 if an active drive exists
        #  for the same plan"
        active_drive_id = self.has_active_drive_for_plan()
        if active_drive_id is not None:
            return OrchestrationResult(
                success=False,
                message=(
                    f"Run rejected: active drive exists for this plan "
                    f"(drive_id={active_drive_id}). "
                    f"Use drive-scoped commands instead: "
                    f"'vectl orch drive-status {active_drive_id}' or "
                    f"'vectl orch drive-resume {active_drive_id}'"
                ),
            )

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
            runner=frozen_snapshot.config.runtime.default_runner,
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

        dispatch_spec = self.build_dispatch_spec(step_id=resolved_step_id, role_hint=resolved_agent)
        return self._collect_and_route_terminal(
            registry=registry,
            run_id=run_id,
            step_id=resolved_step_id,
            execution_id=execution_id,
            dispatch_spec=dispatch_spec,
            agent=resolved_agent,
            run_root=run_root,
        )

    # @invar:allow function_size: Public resume entrypoint preserves recovery classification, projection replay, runtime restart, and terminal routing compatibility.
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
            _workspace, execution_id = self._admit_start_and_persist_running(
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

        dispatch_spec = self.build_dispatch_spec(
            step_id=record.step_id, role_hint=record.agent or self._config.default_agent
        )
        return self._collect_and_route_terminal(
            registry=registry,
            run_id=run_id,
            step_id=record.step_id,
            execution_id=execution_id,
            dispatch_spec=dispatch_spec,
            agent=record.agent or self._config.default_agent,
            run_root=run_root,
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
        """Return active (non-terminal) run IDs, including paused runs.

        Authority:
            docs/ORCHESTRATION-PLANE-CLI-CONFIG-OBSERVABILITY-DESIGN.md §7.4
                'pause' queues a pause request; the run remains active but
                paused, and is selectable by --latest.
        """
        registry = self._run_registry(config=self._effective_orchestration_config())
        candidates: list[RunRecord] = []
        for status in ("running", "pending", "stall", "paused"):
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
        runner: str | None = None,
        session_id: str | None = None,
        request_mode: str | None = None,
    ) -> None:
        """Write session-aware continuity bootstrap artifacts.

        Authority: docs/ORCHESTRATION-PLANE-ARCHITECTURE.md,
            docs/RFC-opencode-orchestration-runner.md section 11

        When ``runner`` and ``session_id`` are provided (OpenCode-backed runs),
        the continuity ledger and journal record the actual session identity
        instead of the placeholder ``run_id``.

        Args:
            run_id: Unique run identifier.
            step_id: Step being executed.
            frozen_config: Frozen orchestration config for capability fingerprint.
            run_root: Artifact root for the run.
            runner: Runner identifier (e.g. ``opencode``). Falls back to
                ``frozen_config.runtime.default_runner`` if not provided.
            session_id: Session identifier from runner handle. Falls back to
                ``run_id`` if not provided (placeholder for non-session runners).
            request_mode: Launch mode (``start``, ``resume``, ``recover``).
                Written into the ledger for recovery context.
        """
        effective_runner = runner or frozen_config.runtime.default_runner
        effective_session_id = session_id or run_id
        effective_mode = request_mode or "start"
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
            "session_id": effective_session_id,
            "runner": effective_runner,
            "request_mode": effective_mode,
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
            "session_id": effective_session_id,
            "runner": effective_runner,
            "request_mode": effective_mode,
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

    def _write_recovery_continuity(
        self,
        *,
        run_root: Path,
        continuity: RecoveryContinuity,
    ) -> Path:
        """Persist recovery path truth label at ``recovery/continuity.json``.

        Authority: docs/RFC-opencode-orchestration-runner.md section 10.3

        The system must not collapse native session resume and fresh relaunch
        into the same label. This method writes the authoritative ``recovered_via``
        truth so that downstream consumers (summaries, event payloads, audit) can
        distinguish the actual path taken.

        Args:
            run_root: Artifact root for the run (e.g. ``.vectl/runs/<run_id>``).
            continuity: The RecoveryContinuity record to persist.

        Returns:
            Path to the written continuity.json file.
        """
        recovery_dir = run_root / "recovery"
        recovery_dir.mkdir(parents=True, exist_ok=True)
        continuity_path = recovery_dir / "continuity.json"
        payload = {
            "recovered_via": continuity.recovered_via,
            "run_id": continuity.run_id,
            "step_id": continuity.step_id,
            "agent_id": continuity.agent_id,
            "runner": continuity.runner,
            "session_id": continuity.session_id,
            "timestamp": continuity.timestamp,
        }
        continuity_path.write_text(
            json.dumps(payload, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return continuity_path

    def _write_recovery_attempt(
        self,
        *,
        run_root: Path,
        attempt: RecoveryAttempt,
    ) -> Path:
        """Persist individual resume/recover attempt record.

        Authority: docs/RFC-opencode-orchestration-runner.md section 10.4

        Records whether native session validation succeeded, why it may have
        failed, whether fallback relaunch was used, and resulting identifiers.

        Args:
            run_root: Artifact root for the run (e.g. ``.vectl/runs/<run_id>``).
            attempt: The RecoveryAttempt record to persist.

        Returns:
            Path to the written attempt JSON file.
        """
        recovery_dir = run_root / "recovery"
        recovery_dir.mkdir(parents=True, exist_ok=True)
        filename = (
            "resume_attempt.json" if attempt.attempt_kind == "resume" else "recover_attempt.json"
        )
        attempt_path = recovery_dir / filename
        payload = {
            "attempt_kind": attempt.attempt_kind,
            "native_validation_ok": attempt.native_validation_ok,
            "native_validation_failure_reason": attempt.native_validation_failure_reason,
            "fallback_relaunch_used": attempt.fallback_relaunch_used,
            "resulting_run_id": attempt.resulting_run_id,
            "resulting_session_id": attempt.resulting_session_id,
        }
        attempt_path.write_text(
            json.dumps(payload, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return attempt_path

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

    # @invar:allow function_size: Recovery classification intentionally evaluates all artifact families together to preserve restart safety semantics.
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

    # @invar:allow function_size: Public recovery flow preserves dry-run, artifact replay, notification, gate, and terminalization semantics in one operator action.
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
                    # Authority: RFC-opencode-orchestration-runner.md section 10.3
                    # Write recovery continuity truth label distinguishing
                    # native_session_resume from fresh_relaunch.
                    now_iso = datetime.now(timezone.utc).isoformat()
                    self._write_recovery_continuity(
                        run_root=run_root,
                        continuity=RecoveryContinuity(
                            recovered_via="native_session_resume",
                            run_id=record.run_id,
                            step_id=record.step_id,
                            agent_id=record.agent or self._config.default_agent,
                            runner=frozen.runtime.default_runner,
                            session_id=getattr(record, "session_id", None),
                            timestamp=now_iso,
                        ),
                    )
                    # Authority: RFC-opencode-orchestration-runner.md section 10.4
                    # Write recovery attempt record for audit trail.
                    self._write_recovery_attempt(
                        run_root=run_root,
                        attempt=RecoveryAttempt(
                            attempt_kind="recover",
                            native_validation_ok=True,
                            fallback_relaunch_used=False,
                            resulting_run_id=record.run_id,
                            resulting_session_id=getattr(record, "session_id", None),
                        ),
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
                terminal_result = self._collect_and_route_terminal(
                    registry=registry,
                    run_id=record.run_id,
                    step_id=record.step_id,
                    execution_id=execution_id,
                    dispatch_spec=dispatch_spec,
                    agent=record.agent or self._config.default_agent,
                    run_root=run_root,
                )
                if terminal_result.success:
                    recover_and_resume.append(
                        f"run_id={record.run_id} outcome=recover_and_resume_completed "
                        f"artifact_families={','.join(decision.artifact_families)}"
                    )
                else:
                    blocking.append(
                        f"run_id={record.run_id} outcome=recover_and_resume_failed "
                        f"detail={terminal_result.message} "
                        f"artifact_families={','.join(decision.artifact_families)}"
                    )
                    blocked_artifact_paths.append(str(run_root))
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
                # Authority: RFC-opencode-orchestration-runner.md section 10.3
                # The fresh_start_required path terminalizes the stale run.
                # Write recovery continuity truth distinguishing from native_session_resume.
                now_iso = datetime.now(timezone.utc).isoformat()
                self._write_recovery_continuity(
                    run_root=run_root,
                    continuity=RecoveryContinuity(
                        recovered_via="fresh_relaunch",
                        run_id=record.run_id,
                        step_id=record.step_id,
                        agent_id=record.agent or self._config.default_agent,
                        runner=frozen.runtime.default_runner,
                        session_id=None,
                        timestamp=now_iso,
                    ),
                )
                # Authority: RFC-opencode-orchestration-runner.md section 10.4
                # Write recovery attempt record for audit trail.
                self._write_recovery_attempt(
                    run_root=run_root,
                    attempt=RecoveryAttempt(
                        attempt_kind="recover",
                        native_validation_ok=False,
                        native_validation_failure_reason=decision.message,
                        fallback_relaunch_used=True,
                        resulting_run_id=record.run_id,
                    ),
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
                    output_summary=summarize_plan_evidence(record.output_summary),
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

    # @invar:allow dead_param: force is retained for public CLI/app prune compatibility although current registry pruning is non-interactive.
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
    # Drive-Scoped Inspection
    # -----------------------------------------------------------------

    def inspect_drive_status(
        self,
        drive_id: str,
        child_run_id: str | None = None,
    ) -> DriveInspectView:
        """
        Inspect drive-level status.

        Authority: docs/RFC-orch-drive.md section 7.3

        When child_run_id is provided, the result is scoped to that
        child run within the drive. Per RFC §7.4, a mismatched child-run
        selector raises ChildRunScopeError.

        Args:
            drive_id: The drive to inspect.
            child_run_id: Optional child-run selector for drill-down.

        Returns:
            DriveInspectView with current drive state.

        Raises:
            ChildRunScopeError: If child_run_id does not belong to the drive.
            OSError: Propagates drive store read failures.
        """
        store = self._drive_store()
        query = DriveInspectQuery(
            drive_id=drive_id,
            child_run_id=child_run_id,
            limit=100,
            offset=0,
        )
        return query_drive_status(query, drive_store=store)

    def inspect_drive_events(
        self,
        drive_id: str,
        child_run_id: str | None = None,
        limit: int = 100,
    ) -> InspectResult:
        """
        Inspect drive-level events.

        Authority: docs/RFC-orch-drive.md section 7.3

        Args:
            drive_id: The drive to inspect events for.
            child_run_id: Optional child-run selector for drill-down.
            limit: Maximum number of events to return.

        Returns:
            InspectResult with drive-scoped events.

        Raises:
            ChildRunScopeError: If child_run_id does not belong to the drive.
            EventCorruptionError: If the event stream cannot be parsed.
        """

        store = self._drive_store()
        if child_run_id is not None:
            # Validate scope constraint per RFC §7.4
            validate_child_run_in_drive(drive_id, child_run_id, drive_store=store)

        events = load_event_jsonl(self._events_path())
        query = DriveInspectQuery(
            drive_id=drive_id,
            child_run_id=child_run_id,
            limit=limit,
            offset=0,
        )
        filtered = query_drive_events(query, events=events)
        data = tuple(
            f"seq={getattr(e, 'seq', '')} event={getattr(e, 'kind', '')} "
            f"step_id={getattr(e, 'step_id', '') or ''}"
            for e in filtered
        )
        return InspectResult(view_type="events", data=data)

    def inspect_drive_logs(
        self,
        drive_id: str,
        child_run_id: str | None = None,
    ) -> InspectResult:
        """
        Inspect drive-level logs.

        Authority: docs/RFC-orch-drive.md section 7.3

        Args:
            drive_id: The drive to inspect logs for.
            child_run_id: Optional child-run selector for drill-down.

        Returns:
            InspectResult with drive-scoped logs.

        Raises:
            ChildRunScopeError: If child_run_id does not belong to the drive.
        """
        store = self._drive_store()
        if child_run_id is not None:
            validate_child_run_in_drive(drive_id, child_run_id, drive_store=store)

        child_runs = store.child_runs_for_drive(drive_id)
        # Collect run IDs for log filtering
        run_ids = tuple(ref.run_id for ref in child_runs)
        if child_run_id is not None:
            run_ids = (child_run_id,)

        lines = self._text_log_path.read_text(encoding="utf-8").splitlines()
        scoped = [line for line in lines if any(f"run_id={rid}" in line for rid in run_ids)]
        return InspectResult(view_type="logs", data=tuple(scoped[-100:]))

    def inspect_drive_artifacts(
        self,
        drive_id: str,
        child_run_id: str | None = None,
    ) -> InspectResult:
        """
        Inspect drive-level artifacts.

        Authority: docs/RFC-orch-drive.md section 7.3

        Collects artifact references from child runs belonging to the drive.

        Args:
            drive_id: The drive to inspect artifacts for.
            child_run_id: Optional child-run selector for drill-down.

        Returns:
            InspectResult with drive-scoped artifacts.

        Raises:
            ChildRunScopeError: If child_run_id does not belong to the drive.
        """
        store = self._drive_store()
        child_runs = store.child_runs_for_drive(drive_id)

        if child_run_id is not None:
            validate_child_run_in_drive(drive_id, child_run_id, drive_store=store)
            child_runs = tuple(ref for ref in child_runs if ref.run_id == child_run_id)

        query = DriveInspectQuery(
            drive_id=drive_id,
            child_run_id=child_run_id,
        )
        artifact_roots = (
            self._run_registry(config=self._effective_orchestration_config()).store_root,
        )
        refs = query_drive_artifacts(query, child_runs, artifact_roots)
        return InspectResult(view_type="artifacts", data=refs)

    def inspect_drive_actions(
        self,
        drive_id: str,
        child_run_id: str | None = None,
    ) -> InspectResult:
        """
        Inspect drive-level actions.

        Authority: docs/RFC-orch-drive.md section 7.3

        Collects action references from child runs belonging to the drive.

        Args:
            drive_id: The drive to inspect actions for.
            child_run_id: Optional child-run selector for drill-down.

        Returns:
            InspectResult with drive-scoped actions.

        Raises:
            ChildRunScopeError: If child_run_id does not belong to the drive.
        """
        store = self._drive_store()
        if child_run_id is not None:
            validate_child_run_in_drive(drive_id, child_run_id, drive_store=store)

        child_runs = store.child_runs_for_drive(drive_id)
        channel = FilesystemControlChannel(
            runs_root=self._run_registry(config=self._effective_orchestration_config()).store_root,
            max_pending_actions=self._effective_orchestration_config().operator.max_pending_actions,
        )

        rows: list[str] = []
        for ref in child_runs:
            if child_run_id is not None and ref.run_id != child_run_id:
                continue
            for status in ("pending", "applied", "rejected"):
                for request in channel.list_requests(ref.run_id, status=status):
                    rows.append(
                        f"run_id={ref.run_id} status={status} "
                        f"action_id={request.action_id} type={request.msg_type}"
                    )

        return InspectResult(view_type="actions", data=tuple(rows))

    # -----------------------------------------------------------------
    # Drive-Scoped Control
    # -----------------------------------------------------------------

    def control_drive_pause(
        self,
        drive_id: str,
        reason: str | None = None,
    ) -> ControlResult:
        """
        Pause a drive (stop dispatching new work).

        Authority: docs/RFC-orch-drive.md section 7.3

        Args:
            drive_id: The drive to pause.
            reason: Optional pause reason from operator.

        Returns:
            ControlResult with pause outcome.
        """
        send_drive_control(
            drive_id=drive_id,
            action="pause",
            channel=self._control_channel(),
            reason=reason,
        )
        return ControlResult(
            action="pause", success=True, message=f"Pause queued for drive {drive_id}"
        )

    def control_drive_unpause(
        self,
        drive_id: str,
        reason: str | None = None,
    ) -> ControlResult:
        """
        Unpause a drive (resume dispatching).

        Authority: docs/RFC-orch-drive.md section 7.3

        Args:
            drive_id: The drive to unpause.
            reason: Optional unpause reason from operator.

        Returns:
            ControlResult with unpause outcome.
        """
        send_drive_control(
            drive_id=drive_id,
            action="unpause",
            channel=self._control_channel(),
            reason=reason,
        )
        return ControlResult(
            action="unpause",
            success=True,
            message=f"Unpause queued for drive {drive_id}",
        )

    def control_drive_stop(
        self,
        drive_id: str,
        reason: str | None = None,
        force: bool = False,
    ) -> ControlResult:
        """
        Stop a drive entirely.

        Authority: docs/RFC-orch-drive.md section 7.3

        With --force, requests immediate stop semantics per RFC §7.3.1:
        - request cancellation of all active step child runs
        - preserve child-run artifacts
        - allow resolver/planner child runs to complete

        Args:
            drive_id: The drive to stop.
            reason: Optional reason for stopping.
            force: Request immediate stop semantics.

        Returns:
            ControlResult with stop outcome.
        """
        send_drive_control(
            drive_id=drive_id,
            action="stop",
            channel=self._control_channel(),
            reason=reason,
            force=force,
        )
        suffix = " (force)" if force else ""
        return ControlResult(
            action="stop",
            success=True,
            message=f"Stop queued for drive {drive_id}{suffix}",
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
        Authority: docs/RFC-role-profile-overrides.md §7.1

        When ``effective=True``, includes role profile provenance per RFC §7.1:
        each role profile field is annotated with its source category
        (``default``, ``override``, or ``custom``).

        Returns:
            ConfigResult with current configuration.

        Raises:
            OSError: Propagates config materialization failures.
        """
        config = self._effective_orchestration_config()

        # Build role profile provenance (RFC §7.1)
        role_profile_provenance: dict[str, dict[str, RoleFieldProvenance]] = {}
        role_profile_lines: list[str] = []

        if effective:
            role_profile_provenance = build_role_profile_provenance(config)
            for role_id, fields in role_profile_provenance.items():
                for field_name, prov in fields.items():
                    key = f"role_profiles.{role_id}.{field_name}"
                    role_profile_lines.append(f"{key} = {prov.value} (source={prov.source})")

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
            # Append role profile provenance lines (RFC §7.1)
            if role_profile_lines:
                show_output += "\n" + "\n".join(role_profile_lines)
        else:
            show_output = (
                f"plan_path={config.plan_path}\n"
                f"artifact_root={config.runtime.artifact_root}\n"
                f"workspace_root={config.runtime.workspace_root}\n"
                f"resolver_enabled={config.resolver.enabled}\n"
                f"allowlist={self._allowlist_text(config.resolver.tool_allowlist)}"
            )

        # Convert RoleFieldProvenance dataclasses to JSON-ready dicts
        provenance_json: dict[str, dict[str, dict[str, str]]] | None = None
        if effective:
            provenance_json = {
                role_id: {
                    field_name: {"value": prov.value, "source": prov.source}
                    for field_name, prov in fields.items()
                }
                for role_id, fields in role_profile_provenance.items()
            }

        return ConfigResult(
            show_output=show_output,
            validation_passed=True,
            tools=canonical_tool_families(),
            role_profile_provenance=provenance_json,
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


# @shell_orchestration: Runner-output JSON scanning remains colocated with shell payload extraction helpers to preserve parsing compatibility.
def _iter_json_values_from_text(text: str):
    """Yield JSON values embedded in runner text."""

    decoder = json.JSONDecoder()
    index = 0
    while index < len(text):
        char = text[index]
        if char not in "[{":
            index += 1
            continue
        try:
            payload, end = decoder.raw_decode(text[index:])
        except json.JSONDecodeError:
            index += 1
            continue
        yield payload
        index += max(end, 1)


_STRUCTURED_REVIEW_PROTOCOL_KEYS = frozenset(
    {"type", "sessionID", "timestamp", "part", "parts", "message", "messages", "data"}
)


# @invar:allow shell_result: Private string normalizer is pure parsing for runner-output compatibility, not an I/O boundary.
# @shell_orchestration: Markdown fence stripping is runner-output parsing glue for shell review/resolver payload extraction.
def _strip_runner_markdown_fence(raw_output: str) -> str:
    stripped = raw_output.strip()
    if not stripped.startswith("```"):
        return stripped
    lines = stripped.splitlines()
    if len(lines) < 3 or not lines[-1].strip().startswith("```"):
        return stripped
    return "\n".join(lines[1:-1]).strip()


# @invar:allow shell_result: Private predicate keeps payload-shape checks as bool for recursive parser callers.
# @shell_orchestration: Structured-review shape detection stays near runner-output extraction to preserve envelope filtering.
def _looks_like_structured_review_payload(value: dict[str, object]) -> bool:
    if _STRUCTURED_REVIEW_PROTOCOL_KEYS.intersection(value.keys()):
        return False
    if value.get("review_outcome") not in {
        "pass",
        "needs_fix",
        "needs_replan",
        "operator_required",
    }:
        return False
    return isinstance(value.get("summary"), str)


# @invar:allow shell_result: Recursive parser returns the discovered payload directly for existing review-routing callers.
# @shell_complexity: Branches preserve nested dict/list/string traversal and embedded JSON handling for runner envelopes.
# @shell_orchestration: Recursive payload discovery remains app-level runner-output routing glue.
def _find_structured_review_payload(
    value: object,
    *,
    parse_strings: bool = True,
) -> dict[str, object] | None:
    """Find a StructuredReviewResult-shaped payload inside runner values."""

    if isinstance(value, dict):
        candidate = cast(dict[str, object], value)
        if _looks_like_structured_review_payload(candidate):
            return candidate
        for nested in value.values():
            found = _find_structured_review_payload(nested, parse_strings=parse_strings)
            if found is not None:
                return found
        return None
    if isinstance(value, list | tuple):
        for nested in value:
            found = _find_structured_review_payload(nested, parse_strings=parse_strings)
            if found is not None:
                return found
        return None
    if isinstance(value, str) and parse_strings:
        return _extract_structured_review_payload(value)
    return None


# @invar:allow shell_result: Review extraction API returns optional payload consumed by existing StructuredReviewResult mapping.
# @shell_complexity: Branches preserve ordered JSON, embedded JSON, and YAML fallback parsing semantics.
# @shell_orchestration: Review payload extraction handles shell runner wrappers before app-level review routing.
def _extract_structured_review_payload(raw_output: str) -> dict[str, object] | None:
    """Extract a structured-review payload from JSON/YAML or OpenCode envelopes."""

    candidate = _strip_runner_markdown_fence(raw_output)
    if not candidate:
        return None

    try:
        parsed = json.loads(candidate)
    except json.JSONDecodeError:
        parsed = None
    if parsed is not None:
        found = _find_structured_review_payload(parsed, parse_strings=False)
        if found is not None:
            return found

    for parsed_value in _iter_json_values_from_text(candidate):
        found = _find_structured_review_payload(parsed_value)
        if found is not None:
            return found

    try:
        parsed_yaml = yaml.safe_load(candidate)
    except yaml.YAMLError:
        return None
    return _find_structured_review_payload(parsed_yaml, parse_strings=False)


# @invar:allow shell_result: Recursive resolver parser returns optional payload directly for report mapping compatibility.
# @shell_complexity: Branches preserve validation-first traversal across nested runner envelopes and embedded strings.
# @shell_orchestration: ResolutionReport payload discovery remains app-level resolver runner-output glue.
def _find_resolution_report_payload(value: object) -> dict[str, object] | None:
    """Find a ResolutionReport-shaped payload inside nested runner values."""

    if isinstance(value, dict):
        try:
            validate_resolution_report_payload(value)
        except ValueError:
            pass
        else:
            return cast(dict[str, object], value)
        for nested in value.values():
            found = _find_resolution_report_payload(nested)
            if found is not None:
                return found
        return None
    if isinstance(value, list | tuple):
        for nested in value:
            found = _find_resolution_report_payload(nested)
            if found is not None:
                return found
        return None
    if isinstance(value, str):
        for nested in _iter_json_values_from_text(value):
            found = _find_resolution_report_payload(nested)
            if found is not None:
                return found
    return None


# @invar:allow shell_result: Resolver extraction API returns optional payload consumed by map_payload_to_report callers.
# @shell_complexity: Branches preserve raw JSON and OpenCode stdout-wrapper parsing compatibility.
# @shell_orchestration: Resolver report extraction handles shell runner summaries before mapping to domain reports.
def _extract_resolution_report_payload(output_summary: str) -> dict[str, object] | None:
    """Extract a ResolutionReport payload from runtime output summary text.

    OpenCodeRunner wraps successful stdout as a human summary, e.g.
    ``OpenCode completed successfully (exit 0); stdout={...}``. Resolver
    parsing must consume that wrapper while still accepting pure JSON from
    non-OpenCode test runners.
    """

    stripped = output_summary.strip()
    candidates = [stripped]
    stdout_marker = "; stdout="
    if stdout_marker in stripped:
        candidates.append(stripped.rsplit(stdout_marker, 1)[1].strip())

    for candidate in candidates:
        for payload in _iter_json_values_from_text(candidate):
            found = _find_resolution_report_payload(payload)
            if found is not None:
                return found
    return None


# @invar:allow function_size: Composition root must wire control, roster, runtime, resolver, config, and driver dependencies in one public factory.
# @invar:allow shell_result: Public factory returns OrchestrationApp directly; callers and CLI wiring expect the composed facade, not Result.
# @shell_complexity: Factory branches preserve config provenance, frozen snapshots, runner selection, and default component wiring.
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
            artifact_root: Path,
            timeout_seconds: float,
        ) -> None:
            self._runtime = runtime
            self._dispatch_coordinator = dispatch_coordinator
            self._artifact_root = artifact_root
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

            case_id = case.case_id or f"case-{generate_run_id()}"
            dispatch_spec = self._dispatch_coordinator.build_resolution_subtask_spec(
                case_id=case_id,
                role_id=role_id,
                description=self._case_description(case),
                refs=case.artifact_refs,
            )
            agent_id = self._dispatch_coordinator.role_registry.get(dispatch_spec.role_id).agent_id
            resolver_step_id = dispatch_spec.step_id or dispatch_spec.source_id
            resolver_run_id = f"resolver-{generate_run_id()}"
            prompt_bundle = self._dispatch_coordinator.render_prompt_bundle(dispatch_spec)
            request = ExecutionRequest(
                step_id=resolver_step_id,
                role=dispatch_spec.role_id,
                runner=dispatch_spec.runner,
                work_refs=(
                    f"run_id={resolver_run_id}",
                    f"resolver_case_id={case.case_id}",
                    f"resolver_case_source={case.case_source}",
                    f"resolver_role_id={dispatch_spec.role_id}",
                    f"execution_context={dispatch_spec.execution_context}",
                    f"mutation_policy={dispatch_spec.mutation_policy}",
                    f"output_contract={dispatch_spec.output_contract}",
                    f"source_kind={dispatch_spec.source_kind}",
                    *case.artifact_refs,
                ),
                agent_id=agent_id,
                prompt_bundle_path="",
                runner_prompt_path="",
                request_mode="start",
                session_policy="reuse_forbidden",
                session_id=resolver_run_id,
            )

            try:
                workspace = self._runtime.prepare(request)
                worktree_path = self._runtime.workspace_worktree_path(workspace)
                artifact_paths = resolve_prompt_artifact_paths(
                    artifact_root=self._artifact_root,
                    run_id=resolver_run_id,
                    workspace=worktree_path,
                )
                materialize_prompt_artifacts(
                    bundle=prompt_bundle,
                    artifact_paths=artifact_paths,
                    role_id=dispatch_spec.role_id,
                    agent_id=agent_id,
                    runner=dispatch_spec.runner,
                    output_contract=dispatch_spec.output_contract,
                )
                request = ExecutionRequest(
                    step_id=resolver_step_id,
                    role=dispatch_spec.role_id,
                    runner=dispatch_spec.runner,
                    work_refs=request.work_refs,
                    agent_id=agent_id,
                    prompt_bundle_path=artifact_paths.prompt_bundle_path,
                    runner_prompt_path=artifact_paths.runner_prompt_path,
                    request_mode="start",
                    session_policy="reuse_forbidden",
                    session_id=resolver_run_id,
                )
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
                    case=case,
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
            case: ResolutionCase,
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

            payload = _extract_resolution_report_payload(result.output_summary)
            if payload is None:
                fallback = self._fallback_unstructured_success_payload(
                    case=case,
                    execution_id=execution_id,
                    role_id=role_id,
                )
                if fallback is not None:
                    return fallback
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

        def _fallback_unstructured_success_payload(
            self,
            *,
            case: ResolutionCase,
            execution_id: str,
            role_id: str,
        ) -> dict[str, object] | None:
            """Unblock stale drive cases when resolver exits cleanly without JSON.

            This is intentionally narrow: it only applies to runtime-failure
            cases that have no blocked step references and whose drive already
            has runnable frontier work. In that state, keeping the barrier would
            deadlock normal drive progress behind an unstructured resolver reply.
            """

            drive = case.drive
            if case.case_source not in {"runtime_failure", "unknown"}:
                return None
            if drive is None or not drive.frontier_step_ids:
                return None
            if set(case.core.in_progress_step_ids).intersection(drive.frontier_step_ids):
                return None
            if set(case.blocked_step_ids).intersection(drive.frontier_step_ids):
                return None
            return {
                "status": "unblocked",
                "summary": (
                    "Resolver produced unstructured output for a stale runtime case; "
                    "blocked refs do not intersect the runnable drive frontier."
                ),
                "evidence_refs": (
                    f"resolver_execution_id={execution_id}",
                    f"resolver_role_id={role_id}",
                    f"case_id={case.case_id}",
                    f"frontier_step_ids={drive.frontier_step_ids}",
                ),
                "operator_message": None,
            }

    class GatewayEnforcedResolverInvocation(ResolverInvocationSurface):
        def __init__(
            self,
            *,
            delegate: ResolverInvocationSurface,
            mediation_source: CaseRuntimeToolMediationSource,
        ) -> None:
            self._delegate = delegate
            self._mediation_source = mediation_source

        def invoke(self, case: object, *, role_id: str) -> dict[str, object]:
            if not isinstance(case, ResolutionCase):
                raise TypeError("resolver invocation requires ResolutionCase")

            mediation = self._mediation_source.resolve(case, role_id=role_id)

            gateway = AuditedResolverGateway(
                planned_tool_calls=mediation.planned_tool_calls,
                resolver_invoker=lambda request_case, _calls, invocation_ref: self._invoke_delegate(
                    request_case,
                    role_id=role_id,
                    invocation_ref=invocation_ref,
                    mediation=mediation,
                ),
                invocation_ref_factory=lambda: f"resolver-gateway-{generate_run_id()}",
                main_worktree_probe=lambda: not is_linked_worktree()[0],
            )
            try:
                result = authorize_and_invoke(
                    case=case,
                    allowed_tool_families=mediation.allowed_tool_families,
                    gateway=gateway,
                )
            except AuthorizationError as exc:
                evidence_refs = ["resolver:authorization-denied"]
                evidence_refs.extend(mediation.evidence_refs)
                evidence_refs.extend(
                    f"resolver_denied_family={family}" for family in exc.denied_families
                )
                return {
                    "status": "operator_required",
                    "summary": f"Resolver gateway authorization denied: {exc}",
                    "evidence_refs": tuple(evidence_refs),
                    "operator_message": (
                        "Review resolver tool allowlist / execution site and retry."
                    ),
                }

            report = result.report
            if report is None:
                return {
                    "status": "operator_required",
                    "summary": "Resolver gateway completed without a ResolutionReport",
                    "evidence_refs": ("resolver:gateway-empty-report",),
                    "operator_message": "Review resolver gateway wiring and retry.",
                }
            return {
                "status": report.status,
                "summary": report.summary,
                "evidence_refs": list(report.evidence_refs),
                "operator_message": report.operator_message,
            }

        def _invoke_delegate(
            self,
            case: ResolutionCase,
            *,
            role_id: str,
            invocation_ref: str,
            mediation: ResolverToolMediation,
        ) -> ResolutionReport:
            payload = self._delegate.invoke(case, role_id=role_id)
            report = map_payload_to_report(case=case, payload=payload)
            evidence_refs = list(report.evidence_refs)
            for ref in mediation.evidence_refs:
                if ref not in evidence_refs:
                    evidence_refs.append(ref)
            gateway_ref = f"resolver_gateway_invocation={invocation_ref}"
            if gateway_ref not in evidence_refs:
                evidence_refs.append(gateway_ref)
            if tuple(evidence_refs) == report.evidence_refs:
                return report
            return ResolutionReport(
                status=report.status,
                summary=report.summary,
                evidence_refs=tuple(evidence_refs),
                operator_message=report.operator_message,
            )

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
        core_adapter=core_adapter,
    )
    control = PlanAwareControl(
        sources=ControlInputSources(core_adapter=core_adapter, roster=roster, runtime=runtime),
        agent=config.default_agent,
        dispatch_role=config.default_agent,
    )
    resolver_artifact_root = (
        config.run_store_root or resolved_orchestration_config.runtime.artifact_root
    )
    resolver = BoundResolver(
        invocation=GatewayEnforcedResolverInvocation(
            delegate=DefaultRoleResolverInvocation(
                runtime=runtime,
                dispatch_coordinator=dispatch_coordinator,
                artifact_root=resolver_artifact_root,
                timeout_seconds=resolved_orchestration_config.resolver.invocation_timeout_seconds,
            ),
            mediation_source=CaseRuntimeToolMediationSource(
                configured_allowlist_families=(
                    resolved_orchestration_config.resolver.tool_allowlist.allowed_tool_families
                )
            ),
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


# @invar:allow shell_result: Private projection helper returns DispatchSpec directly for dispatch construction compatibility.
# @shell_orchestration: Roster lease projection stays adjacent to dispatch construction in the orchestration app shell.
def _apply_roster_lease(*, dispatch_spec: DispatchSpec, lease: WorkLease) -> DispatchSpec:
    """Project roster lease facts onto dispatch without changing role intent."""

    if lease.role != dispatch_spec.role_id:
        raise ValueError(
            "roster authority violation: "
            f"lease role {lease.role!r} does not satisfy requested role {dispatch_spec.role_id!r}"
        )

    session_mode: Literal["fresh", "reuse"] = "fresh"
    if lease.session_id:
        session_mode = "reuse"

    return replace(
        dispatch_spec,
        runner=lease.runner,
        session_mode=session_mode,
        reuse_token=lease.session_id,
        reuse_runner=lease.runner if lease.session_id else None,
    )


# @invar:allow shell_result: Private mode mapper returns RequestMode directly for ExecutionRequest construction compatibility.
# @shell_orchestration: Request-mode mapping is dispatch-shell glue preserving resume/recover runtime semantics.
def _dispatch_request_mode(
    *, mode: Literal["start", "resume", "recover"], dispatch_spec: DispatchSpec
) -> RequestMode:
    """Return runtime request mode while preserving explicit recovery flows."""

    if mode != "start":
        return mode
    if dispatch_spec.session_mode == "reuse":
        return "resume"
    return "start"


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
