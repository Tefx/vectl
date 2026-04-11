"""Red tests for operator/pause conflict preservation and recovery alignment.

Authority:
    docs/ORCHESTRATION-PLANE-OPERATOR-CONFLICT-RECOVERY.md
    docs/ORCHESTRATION-PLANE-ORCH-APP-ROUTING.md
    docs/ORCHESTRATION-PLANE-RESOLVER-COORDINATION.md
    src/vectl/orch_app.py
    src/vectl/orchestration/recovery.py
    src/vectl/orchestration/run_store.py
    src/vectl/orchestration/continuity_artifacts.py

These tests define RED coverage for four durable behavioral guarantees:

1. operator_required persists notification metadata, receipt state, and
   paused-routing state durably  (Rules 1, 4, 5 from OPERATOR-CONFLICT-RECOVERY)
2. unresolved merge_conflict and aborted reconcile evidence survive restart
   without silent protected-path integration  (Rules 2, 4)
3. restart reconstructs enough state to block unsafe dispatch and duplicate
   complete until reconcile closure  (Rules 3, 5, section 6.4 invariants)
4. protected-path conflict behavior is surfaced explicitly after restart  (Rule 4)

The tests are organized into two layers:

Layer 1 — Structural contract round-trips: verify that the data structures
and persistence layer preserve notification, reconcile, and gate state.
These may already be GREEN because the dataclasses/serialization exist.

Layer 2 — Runtime behavioral guarantees: verify that the OrchestrationApp
actually uses these structures during routing, recovery, and dispatch to
enforce the invariants.  These are expected-RED until the behavioral wiring
lands.
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any, Literal, cast

import pytest

from tests.expected_red import expected_red_module

pytestmark = expected_red_module(
    owner="orchestration_operator_conflict_recovery",
    rationale="Conflict-recovery behavioral guarantees are intentionally red until orch-app routing and recovery wiring preserve and rehydrate them end-to-end.",
)

from vectl.orch_app import (
    AppConfig,
    OperatorNotification,
    OrchestrationApp,
    OrchestrationResult,
    build_orchestration_app,
)
from vectl.orchestration.config import OrchestrationConfig
from vectl.orchestration.continuity_artifacts import (
    DispatchRecoveryGate,
    NotificationKind,
    NotificationStatus,
    OperatorNotificationRecord,
    PausedRoutingState,
    ReconcileClosureStatus,
    ReconcileRecoveryState,
    RuntimeRecoveryRecord,
)
from vectl.orchestration.contracts import (
    ControlDecision,
    CoreSnapshot,
    DispatchSpec,
    ResolutionCase,
    ResolutionCaseSource,
    ResolutionReport,
    RosterSnapshot,
    RuntimeSnapshot,
)
from vectl.orchestration.recovery import (
    RecoveryAction,
    RecoveryOutcome,
    RecoveryReport as RecoveryReportDTO,
    StartupRecoveryControllerInput,
    StartupRecoveryControllerOutput,
    recovery_action_status,
    recovery_case_status,
    recovery_gate_open_allowed,
)
from vectl.orchestration.run_store import RunRecord, RunRegistry, generate_run_id


# ------------------------------------------------------------------
# Fixtures derived from authoritative docs
# ------------------------------------------------------------------


def _write_plan(plan_path: Path) -> None:
    from vectl.io import save_plan
    from vectl.models import Phase, Plan, Step

    plan = Plan(
        project="operator-pause-recovery-red",
        phases=[
            Phase(
                id="core",
                name="Core",
                steps=[
                    Step(id="core.deploy", name="Deploy"),
                ],
            )
        ],
    )
    save_plan(plan, plan_path)


def _skip_collect_and_route_terminal(
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
    """Skipped collect-and-route that returns success without full lifecycle."""
    return OrchestrationResult(
        success=True,
        message=(
            f"resume_safe: Skipped collect-and-route for pause/recovery test (step={step_id})"
        ),
        step_id=step_id,
        run_id=run_id,
    )


_original_collect_and_route = OrchestrationApp._collect_and_route_terminal


@pytest.fixture(autouse=True)
def _patch_collect_and_route():
    """Autouse fixture: skip _collect_and_route_terminal for pause/recovery tests."""
    OrchestrationApp._collect_and_route_terminal = _skip_collect_and_route_terminal  # type: ignore[assignment]
    yield
    OrchestrationApp._collect_and_route_terminal = _original_collect_and_route  # type: ignore[assignment]


def _build_app(tmp_path: Path):
    plan_path = tmp_path / "plan.yaml"
    runs_root = tmp_path / "runs"
    _write_plan(plan_path)
    config = AppConfig(
        plan_path=plan_path,
        orchestration_config=OrchestrationConfig(plan_path=plan_path),
        run_store_root=runs_root,
    )
    app = build_orchestration_app(config)
    return app


def _core_snapshot(*, blocked: tuple[str, ...] = ("core.deploy",)) -> CoreSnapshot:
    return CoreSnapshot(
        plan_complete=False,
        claimable_step_ids=(),
        in_progress_step_ids=(),
        blocked_step_ids=blocked,
        unresolved_reasons=(),
    )


def _roster_snapshot() -> RosterSnapshot:
    return RosterSnapshot(
        available_agents=(),
        working_agents=(),
        reusable_sessions=(),
        exhausted_roles=(),
    )


def _runtime_snapshot() -> RuntimeSnapshot:
    return RuntimeSnapshot(
        active_workspaces=(),
        active_executions=(),
        stalled_executions=(),
    )


# ------------------------------------------------------------------
# Spec-fixture: minimal persisted shape from authoritative docs
# ------------------------------------------------------------------
# ORCHESTRATION-PLANE-OPERATOR-CONFLICT-RECOVERY.md section 4.2 defines
# NotificationRecord with: notification_id, case_id, run_id,
# kind (operator_required | halt_notice), status (pending | acknowledged |
# resolved | dismissed), summary, operator_message, evidence_refs,
# created_at, updated_at.
#
# OPERATOR-CONFLICT-RECOVERY.md section 6.4 invariants:
#   - active execution is not forgotten
#   - unresolved reconcile conflict is not lost
#   - operator_required state is not silently cleared
#   - complete is not replayed before reconcile merged|noop
#   - linked worktree evidence is not destroyed before recovery classifies it
#
# ORCH-APP-ROUTING.md section 10: notification must persist reason,
# summary, evidence_refs, case_id.


@pytest.fixture
def notification_record_from_spec(
    tmp_path: Path,
) -> OperatorNotificationRecord:
    """Fixture deriving directly from OPERATOR-CONFLICT-RECOVERY.md section 4.2.

    Uses the documented minimal persisted shape without convenience fields
    beyond spec.  This is the authoritative NotificationRecord shape.
    """
    return OperatorNotificationRecord(
        notification_id="notif-01JFX",
        case_id="case-01JFX",
        run_id="run-01JFX",
        kind="operator_required",
        status="pending",
        summary="resolver could not close safely: merge conflict on plan.yaml",
        operator_message="human decision required: protected-path conflict",
        evidence_refs=("artifact://reconcile/merge_conflict",),
        paused_routing_state="paused_operator_wait",
        created_at=1712600000.0,
        updated_at=1712600000.0,
    )


@pytest.fixture
def reconcile_conflict_from_spec(
    tmp_path: Path,
) -> ReconcileRecoveryState:
    """Fixture deriving directly from OPERATOR-CONFLICT-RECOVERY.md section 5.

    Uses the documented minimal shape for unresolved reconcile state with
    merge_conflict, protected paths, and conflict evidence.
    """
    return ReconcileRecoveryState(
        execution_id="exec-01JFX",
        workspace_id="ws-01JFX",
        status="merge_conflict",
        summary="merge conflict on protected path plan.yaml",
        conflict_files=("plan.yaml",),
        protected_paths=("plan.yaml",),
        protected_path_policy="blocked_explicitly",
        target_ref="refs/heads/main",
        target_head_at_prepare="abc123",
        artifact_refs=("artifact://reconcile/conflict",),
    )


@pytest.fixture
def dispatch_recovery_gate_from_spec() -> DispatchRecoveryGate:
    """Fixture deriving from OPERATOR-CONFLICT-RECOVERY.md section 6.4 invariants."""
    return DispatchRecoveryGate(
        status="blocked_pending_reconcile",
        reason="reconcile status is merge_conflict; cannot dispatch or complete",
        duplicate_complete_blocked=True,
        unsafe_dispatch_blocked=True,
        blocked_on_execution_id="exec-01JFX",
        blocked_on_case_id="case-01JFX",
    )


@pytest.fixture
def runtime_recovery_from_spec(
    tmp_path: Path,
    reconcile_conflict_from_spec: ReconcileRecoveryState,
    notification_record_from_spec: OperatorNotificationRecord,
    dispatch_recovery_gate_from_spec: DispatchRecoveryGate,
) -> RuntimeRecoveryRecord:
    """Fixture deriving from OPERATOR-CONFLICT-RECOVERY.md section 6.2."""
    return RuntimeRecoveryRecord(
        workspace_id="ws-01JFX",
        step_id="core.deploy",
        worktree_path=".vectl/workspaces/ws-01JFX",
        scratch_branch="scratch/core.deploy",
        target_ref="refs/heads/main",
        target_head_at_prepare="abc123",
        execution_id="exec-01JFX",
        runner="python-executor",
        runner_handle="handle-01JFX",
        session_id="run-01JFX",
        execution_status="running",
        started_at=1712599900.0,
        last_update_at=1712600000.0,
        execution_artifact_refs=("artifact://exec/output",),
        reconcile_state=reconcile_conflict_from_spec,
        paused_routing_state="paused_operator_wait",
    )


# ==================================================================
# LAYER 1: Structural contract round-trips
# ==================================================================


class TestOperatorNotificationPersistenceStructural:
    """Rule 1: operator_required is a durable orchestration state.

    These tests verify that OperatorNotificationRecord fields survive
    RunStore save/restore round-trips.
    """

    def test_operator_notification_record_round_trip(
        self,
        notification_record_from_spec: OperatorNotificationRecord,
    ) -> None:
        """NotificationRecord with all spec-prescribed fields survives save/restore."""
        registry = RunRegistry(store_root=Path("/tmp/test-op-notif-roundtrip"))
        record = RunRecord(
            run_id="run-01JFX",
            step_id="core.deploy",
            status="running",
            operator_notifications=(notification_record_from_spec,),
        )
        registry.save(record)
        restored = registry.by_id("run-01JFX")
        assert restored is not None
        notif = restored.operator_notifications[0]
        # Spec-prescribed fields from section 4.2
        assert notif.notification_id == "notif-01JFX"
        assert notif.kind == "operator_required"
        assert notif.status == "pending"
        assert notif.paused_routing_state == "paused_operator_wait"
        assert notif.summary == "resolver could not close safely: merge conflict on plan.yaml"
        assert notif.operator_message == "human decision required: protected-path conflict"
        assert notif.evidence_refs == ("artifact://reconcile/merge_conflict",)

    def test_receipt_state_transitions_preserved(self) -> None:
        """Acknowledged notification must stay acknowledged after round-trip."""
        acknowledged = OperatorNotificationRecord(
            notification_id="notif-02",
            case_id="case-02",
            run_id="run-02",
            kind="operator_required",
            status="acknowledged",
            summary="awaiting operator decision",
            paused_routing_state="paused_operator_wait",
            created_at=1712600000.0,
            updated_at=1712600100.0,
        )
        registry = RunRegistry(store_root=Path("/tmp/test-op-receipt"))
        RunRecord(
            run_id="run-02",
            step_id="core.deploy",
            status="running",
            operator_notifications=(acknowledged,),
        )
        record = RunRecord(
            run_id="run-02",
            step_id="core.deploy",
            status="running",
            operator_notifications=(acknowledged,),
        )
        registry.save(record)
        restored = registry.by_id("run-02")
        assert restored is not None
        assert restored.operator_notifications[0].status == "acknowledged"
        assert restored.operator_notifications[0].paused_routing_state == "paused_operator_wait"

    def test_paused_routing_state_values_round_trip(self) -> None:
        """All PausedRoutingState pause categories survive round-trip."""
        pause_states: tuple[PausedRoutingState, ...] = (
            "paused_operator_wait",
            "paused_reconcile_conflict",
            "paused_recovery_hold",
        )
        for state in pause_states:
            notif = OperatorNotificationRecord(
                notification_id=f"notif-{state}",
                case_id=f"case-{state}",
                run_id=f"run-{state}",
                kind="operator_required",
                status="pending",
                summary=f"paused in {state}",
                paused_routing_state=state,
            )
            reg = RunRegistry(store_root=Path(f"/tmp/test-pause-states-{state}"))
            run = RunRecord(
                run_id=f"run-{state}",
                step_id="core.deploy",
                status="running",
                operator_notifications=(notif,),
            )
            reg.save(run)
            restored = reg.by_id(f"run-{state}")
            assert restored is not None
            assert restored.operator_notifications[0].paused_routing_state == state


class TestReconcileConflictSurvivalStructural:
    """Rule 2: unresolved merge conflict is a durable runtime state.

    These tests verify ReconcileRecoveryState with merge_conflict/aborted
    preserves evidence through RunStore round-trips.
    """

    def test_merge_conflict_reconcile_state_round_trip(
        self,
        reconcile_conflict_from_spec: ReconcileRecoveryState,
    ) -> None:
        """merge_conflict reconcile state with protected_paths survives round-trip."""
        registry = RunRegistry(store_root=Path("/tmp/test-reconcile-conflict-rt"))
        runtime_state = RuntimeRecoveryRecord(
            workspace_id="ws-01JFX",
            step_id="core.deploy",
            worktree_path=".vectl/workspaces/ws-01JFX",
            scratch_branch="scratch/core.deploy",
            target_ref="refs/heads/main",
            target_head_at_prepare="abc123",
            execution_id="exec-01JFX",
            reconcile_state=reconcile_conflict_from_spec,
        )
        record = RunRecord(
            run_id="run-conflict-rt",
            step_id="core.deploy",
            status="running",
            runtime_state=runtime_state,
        )
        registry.save(record)
        restored = registry.by_id("run-conflict-rt")
        assert restored is not None
        assert restored.runtime_state is not None
        rc = restored.runtime_state.reconcile_state
        assert rc is not None
        assert rc.status == "merge_conflict"
        assert "plan.yaml" in rc.conflict_files
        assert rc.protected_paths == ("plan.yaml",)
        assert rc.protected_path_policy == "blocked_explicitly"
        assert rc.artifact_refs == ("artifact://reconcile/conflict",)

    def test_aborted_reconcile_preserves_evidence(self) -> None:
        """Aborted reconcile with protected_path_policy='blocked_explicitly' survives."""
        aborted = ReconcileRecoveryState(
            execution_id="exec-abort",
            workspace_id="ws-abort",
            status="aborted",
            summary="integrity preflight failed",
            protected_paths=("plan.yaml",),
            protected_path_policy="blocked_explicitly",
            artifact_refs=("artifact://preflight/failure",),
        )
        registry = RunRegistry(store_root=Path("/tmp/test-reconcile-abort-rt"))
        runtime_state = RuntimeRecoveryRecord(
            workspace_id="ws-abort",
            step_id="core.deploy",
            worktree_path=".vectl/workspaces/ws-abort",
            scratch_branch="scratch/core.deploy",
            target_ref="refs/heads/main",
            target_head_at_prepare="abc123",
            reconcile_state=aborted,
        )
        record = RunRecord(
            run_id="run-abort-rt",
            step_id="core.deploy",
            status="running",
            runtime_state=runtime_state,
        )
        registry.save(record)
        restored = registry.by_id("run-abort-rt")
        assert restored is not None
        assert restored.runtime_state is not None
        rc = restored.runtime_state.reconcile_state
        assert rc is not None
        assert rc.status == "aborted"
        assert rc.protected_paths == ("plan.yaml",)
        assert rc.protected_path_policy == "blocked_explicitly"

    def test_protected_path_policies_round_trip(self) -> None:
        """Both 'blocked_explicitly' and 'restored_with_evidence' survive round-trip."""
        for policy in ("blocked_explicitly", "restored_with_evidence"):
            reconcile = ReconcileRecoveryState(
                execution_id="exec-pp",
                workspace_id="ws-pp",
                status="merge_conflict",
                summary="protected path conflict",
                conflict_files=("plan.yaml",),
                protected_paths=("plan.yaml",),
                protected_path_policy=policy,
                artifact_refs=("artifact://reconcile/conflict",),
            )
            runtime_state = RuntimeRecoveryRecord(
                workspace_id="ws-pp",
                step_id="core.deploy",
                worktree_path=".vectl/workspaces/ws-pp",
                scratch_branch="scratch/core.deploy",
                target_ref="refs/heads/main",
                target_head_at_prepare="abc123",
                reconcile_state=reconcile,
            )
            record = RunRecord(
                run_id=f"run-pp-rt-{policy}",
                step_id="core.deploy",
                status="running",
                runtime_state=runtime_state,
            )
            registry = RunRegistry(store_root=Path(f"/tmp/test-pp-rt-{policy}"))
            registry.save(record)
            restored = registry.by_id(f"run-pp-rt-{policy}")
            assert restored is not None
            assert restored.runtime_state is not None
            rc = restored.runtime_state.reconcile_state
            assert rc is not None
            assert rc.protected_path_policy == policy


class TestDispatchGatePersistenceStructural:
    """Rule 3: dispatch recovery gate persist round-trips.

    These tests verify that DispatchRecoveryGate survives RunRecord round-trips.
    """

    def test_dispatch_recovery_gate_round_trip(
        self,
        dispatch_recovery_gate_from_spec: DispatchRecoveryGate,
    ) -> None:
        registry = RunRegistry(store_root=Path("/tmp/test-dispatch-gate-rt"))
        record = RunRecord(
            run_id="run-gate-rt",
            step_id="core.deploy",
            status="running",
            dispatch_recovery_gate=dispatch_recovery_gate_from_spec,
        )
        registry.save(record)
        restored = registry.by_id("run-gate-rt")
        assert restored is not None
        gate = restored.dispatch_recovery_gate
        assert gate is not None
        assert gate.unsafe_dispatch_blocked is True
        assert gate.duplicate_complete_blocked is True
        assert gate.status == "blocked_pending_reconcile"
        assert gate.blocked_on_execution_id == "exec-01JFX"

    def test_dispatch_gate_clears_on_reconcile_closure(self) -> None:
        gate_after_closure = DispatchRecoveryGate(
            status="dispatch_allowed",
            reason="reconcile closed with merged",
            duplicate_complete_blocked=False,
            unsafe_dispatch_blocked=False,
        )
        registry = RunRegistry(store_root=Path("/tmp/test-gate-clear-rt"))
        record = RunRecord(
            run_id="run-closed-rt",
            step_id="core.deploy",
            status="running",
            dispatch_recovery_gate=gate_after_closure,
        )
        registry.save(record)
        restored = registry.by_id("run-closed-rt")
        assert restored is not None
        gate = restored.dispatch_recovery_gate
        assert gate is not None
        assert gate.status == "dispatch_allowed"
        assert gate.duplicate_complete_blocked is False


class TestRecoverySemanticsStructural:
    """Recovery outcome semantics — preventing silent clearing."""

    def test_gate_open_allowed_prevents_operator_required(self) -> None:
        """operator_required must NOT allow gate to open."""
        assert recovery_gate_open_allowed(RecoveryOutcome.OPERATOR_REQUIRED) is False
        assert recovery_gate_open_allowed(RecoveryOutcome.BLOCKED) is False
        assert recovery_gate_open_allowed(RecoveryOutcome.HALT) is False
        assert recovery_gate_open_allowed(RecoveryOutcome.RECOVERED) is True
        assert recovery_gate_open_allowed(RecoveryOutcome.QUARANTINED) is False
        assert recovery_gate_open_allowed(RecoveryOutcome.NO_ARTIFACTS) is True

    def test_recovery_case_status_maps_operator_required_to_open(self) -> None:
        assert recovery_case_status(RecoveryOutcome.OPERATOR_REQUIRED) == "open"
        assert recovery_case_status(RecoveryOutcome.BLOCKED) == "open"
        assert recovery_case_status(RecoveryOutcome.QUARANTINED) == "open"
        assert recovery_case_status(RecoveryOutcome.HALT) == "halt"
        assert recovery_case_status(RecoveryOutcome.RECOVERED) == "resolved"
        assert recovery_case_status(RecoveryOutcome.NO_ARTIFACTS) == "resolved"

    def test_recovery_action_status_maps_operator_required_to_pending(self) -> None:
        assert recovery_action_status(RecoveryOutcome.OPERATOR_REQUIRED) == "pending"
        assert recovery_action_status(RecoveryOutcome.RECOVERED) == "applied"
        assert recovery_action_status(RecoveryOutcome.NO_ARTIFACTS) == "applied"
        assert recovery_action_status(RecoveryOutcome.QUARANTINED) == "rejected"


class TestCombinedPersistenceStructural:
    """Verified combined structural persistence in RunRecord and continuity ledger."""

    def test_run_record_persists_notification_gate_and_runtime_together(
        self,
        notification_record_from_spec: OperatorNotificationRecord,
        dispatch_recovery_gate_from_spec: DispatchRecoveryGate,
        runtime_recovery_from_spec: RuntimeRecoveryRecord,
    ) -> None:
        """All three durable state carriers must survive together in RunRecord."""
        registry = RunRegistry(store_root=Path("/tmp/test-combined-rt"))
        record = RunRecord(
            run_id="run-combined-rt",
            step_id="core.deploy",
            status="running",
            runtime_state=runtime_recovery_from_spec,
            operator_notifications=(notification_record_from_spec,),
            dispatch_recovery_gate=dispatch_recovery_gate_from_spec,
        )
        registry.save(record)
        restored = registry.by_id("run-combined-rt")
        assert restored is not None
        assert restored.runtime_state is not None
        assert restored.runtime_state.reconcile_state is not None
        assert restored.runtime_state.reconcile_state.status == "merge_conflict"
        assert len(restored.operator_notifications) == 1
        assert restored.operator_notifications[0].kind == "operator_required"
        assert restored.dispatch_recovery_gate is not None
        assert restored.dispatch_recovery_gate.unsafe_dispatch_blocked is True

    def test_continuity_ledger_entry_carries_notifications_and_gate(
        self,
        notification_record_from_spec: OperatorNotificationRecord,
        dispatch_recovery_gate_from_spec: DispatchRecoveryGate,
        runtime_recovery_from_spec: RuntimeRecoveryRecord,
    ) -> None:
        """ContinuityLedgerEntry must carry all three carriers."""
        from vectl.orchestration.continuity_artifacts import ContinuityLedgerEntry

        ledger = ContinuityLedgerEntry(
            step_id="core.deploy",
            session_id="run-combined-rt",
            runner="python-executor",
            status="active",
            created_at=1712600000.0,
            updated_at=1712600000.0,
            runtime_state=runtime_recovery_from_spec,
            operator_notifications=(notification_record_from_spec,),
            dispatch_recovery_gate=dispatch_recovery_gate_from_spec,
        )
        assert ledger.operator_notifications == (notification_record_from_spec,)
        assert ledger.dispatch_recovery_gate == dispatch_recovery_gate_from_spec
        assert ledger.runtime_state is not None
        assert ledger.runtime_state.reconcile_state is not None
        assert ledger.runtime_state.reconcile_state.status == "merge_conflict"
        assert ledger.runtime_state.paused_routing_state == "paused_operator_wait"

    def test_startup_recovery_input_output_carries_all_state(
        self,
        notification_record_from_spec: OperatorNotificationRecord,
        dispatch_recovery_gate_from_spec: DispatchRecoveryGate,
        runtime_recovery_from_spec: RuntimeRecoveryRecord,
    ) -> None:
        """StartupRecoveryControllerInput/Output must carry all three carriers."""
        input_data = StartupRecoveryControllerInput(
            operator_notifications=(notification_record_from_spec,),
            runtime_states=(runtime_recovery_from_spec,),
        )
        assert len(input_data.operator_notifications) == 1
        assert input_data.operator_notifications[0].kind == "operator_required"
        assert len(input_data.runtime_states) == 1
        assert input_data.runtime_states[0].reconcile_state is not None
        assert input_data.runtime_states[0].reconcile_state.status == "merge_conflict"

        output = StartupRecoveryControllerOutput(
            startup_safe=False,
            operator_notifications=(notification_record_from_spec,),
            dispatch_recovery_gates=(dispatch_recovery_gate_from_spec,),
            recovered_runtime_states=(runtime_recovery_from_spec,),
            operator_messages=("operator decision required on merge conflict",),
        )
        assert output.startup_safe is False
        assert len(output.operator_notifications) == 1
        assert output.operator_notifications[0].paused_routing_state == "paused_operator_wait"
        assert len(output.dispatch_recovery_gates) == 1
        assert output.dispatch_recovery_gates[0].unsafe_dispatch_blocked is True


# ==================================================================
# LAYER 2: Runtime behavioral guarantees (expected-RED)
# ==================================================================


class TestOperatorPauseBlocksDispatchBehavior:
    """Rule 1 / Rule 4: When operator_required sets paused_routing_state,
    the orch app must NOT resume normal dispatch.

    ORCH-APP-ROUTING.md section 10.4:
    'orch app enters a paused operator-wait routing state'
    'orch app does not continue ordinary dispatch'

    These tests verify that the OrchestrationApp evaluates route_control
    behavior correctly when paused_routing_state is not 'active'.

    EXPECTED-RED: The orch app does not yet check paused_routing_state
    before dispatching. The route_control/evaluate_control loop must
    honor the paused state.
    """

    def test_orch_app_operator_required_creates_notification_with_paused_state(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """When resolve_case returns operator_required, the orch app
        must produce an OperatorNotification with
        paused_routing_state='paused_operator_wait'."""
        app = _build_app(tmp_path)
        case = ResolutionCase(
            case_id="case-pause-create",
            case_source="merge_conflict",
            reason="merge conflict blocks dispatch",
            summary="conflict on plan.yaml",
            core=_core_snapshot(),
            roster=_roster_snapshot(),
            runtime=_runtime_snapshot(),
            blocked_step_ids=("core.deploy",),
            artifact_refs=("artifact://conflict/plan.yaml",),
        )
        monkeypatch.setattr(
            app,
            "_resolver",
            type(
                "_PauseResolver",
                (),
                {
                    "resolve": lambda self, _c: ResolutionReport(
                        status="operator_required",
                        summary="requires operator decision",
                        operator_message="conflict needs human resolution",
                    ),
                },
            )(),
        )
        report = app.resolve_case(case)
        assert report.status == "operator_required"

        notification = app._latest_operator_notification
        assert notification is not None, (
            "RED: operator_required must update _latest_operator_notification"
        )
        assert notification.paused_routing_state == "paused_operator_wait", (
            "RED: paused_routing_state must be paused_operator_wait, not 'active'"
        )
        assert notification.kind == "operator_required"
        assert notification.case_id == "case-pause-create"

    def test_orch_app_halted_resolver_clears_notification(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """When a case is resolved (not operator_required), the notification
        must be cleared, showing that the paused state is transient and
        explicitly managed."""
        app = _build_app(tmp_path)
        case = ResolutionCase(
            case_id="case-halt-clear",
            case_source="merge_conflict",
            reason="conflict resolved",
            summary="resolved",
            core=_core_snapshot(),
            roster=_roster_snapshot(),
            runtime=_runtime_snapshot(),
            blocked_step_ids=("core.deploy",),
        )
        # First: operator_required creates notification
        monkeypatch.setattr(
            app,
            "_resolver",
            type(
                "_OpReqResolver",
                (),
                {
                    "resolve": lambda self, _c: ResolutionReport(
                        status="operator_required",
                        summary="needs operator",
                    ),
                },
            )(),
        )
        app.resolve_case(case)
        assert app._latest_operator_notification is not None

        # Second: resolved clears notification
        monkeypatch.setattr(
            app,
            "_resolver",
            type(
                "_UnblockedResolver",
                (),
                {
                    "resolve": lambda self, _c: ResolutionReport(
                        status="unblocked",
                        summary="conflict resolved",
                    ),
                },
            )(),
        )
        app.resolve_case(case)
        assert app._latest_operator_notification is None, (
            "RED: resolved case must clear operator notification"
        )


class TestRecoveryReconstructsPausedStateBehavior:
    """Rule 3 / Rule 5: restart must reconstruct enough state to block
    unsafe dispatch and duplicate complete.

    OPERATOR-CONFLICT-RECOVERY.md section 6.3:
    After restart, reconstruct outstanding resolution cases and operator
    notifications before resuming normal evaluation.

    EXPECTED-RED: The recover() method does not yet reconstruct
    operator_notifications from RunRecord into the app's live routing state.
    """

    def test_recovery_report_carries_operator_notifications(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """app.recover() must produce a RecoveryReport that includes
        outstanding operator_notifications from persisted RunRecord state.

        EXPECTED-RED: Currently recover() does not populate
        operator_notifications in the RecoveryReport from RunRecord.
        """
        app = _build_app(tmp_path)
        started = app.run(step_id="core.deploy", agent="python-executor")
        assert started.run_id is not None

        registry = RunRegistry(store_root=app._config.run_store_root)  # type: ignore[arg-type]

        # Persist a RunRecord with an operator notification
        notification = OperatorNotificationRecord(
            notification_id="notif-recover",
            case_id="case-recover",
            run_id=started.run_id,
            kind="operator_required",
            status="pending",
            summary="merge conflict requires operator",
            paused_routing_state="paused_operator_wait",
            created_at=1712600000.0,
            updated_at=1712600000.0,
        )
        existing = registry.by_id(started.run_id)
        assert existing is not None

        updated = RunRecord(
            run_id=existing.run_id,
            step_id=existing.step_id,
            plan_path=existing.plan_path,
            agent=existing.agent,
            status=existing.status,
            created_at=existing.created_at,
            started_at=existing.started_at,
            updated_at=existing.updated_at,
            finished_at=existing.finished_at,
            artifact_root=existing.artifact_root,
            output_summary=existing.output_summary,
            source=existing.source,
            legacy_run_id=existing.legacy_run_id,
            legacy_migration_state=existing.legacy_migration_state,
            continuity_blocker=existing.continuity_blocker,
            runtime_state=existing.runtime_state,
            operator_notifications=(notification,),
            dispatch_recovery_gate=existing.dispatch_recovery_gate,
        )
        registry.save(updated)

        result = app.recover(step_id="core.deploy")
        report = result.recovery_report
        assert report is not None, "RED: recover() must produce a RecoveryReport"

        # EXPECTED-RED: The report should carry operator_notifications
        # from the persisted RunRecord state
        assert len(report.operator_notifications) > 0, (
            "RED: RecoveryReport must carry operator_notifications from persisted state"
        )
        found_notif = report.operator_notifications[0]
        assert found_notif.kind == "operator_required", (
            "RED: recovered notification must preserve kind=operator_required"
        )
        assert found_notif.paused_routing_state == "paused_operator_wait", (
            "RED: recovered notification must preserve paused_routing_state"
        )

    def test_recovery_report_carries_dispatch_recovery_gate(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """app.recover() must produce a RecoveryReport that includes
        dispatch_recovery_gate from persisted RunRecord state.

        EXPECTED-RED: Currently recover() does not populate
        dispatch_recovery_gate in the RecoveryReport.
        """
        app = _build_app(tmp_path)
        started = app.run(step_id="core.deploy", agent="python-executor")
        assert started.run_id is not None

        registry = RunRegistry(store_root=app._config.run_store_root)  # type: ignore[arg-type]

        gate = DispatchRecoveryGate(
            status="blocked_pending_reconcile",
            reason="reconcile conflict unresolved",
            duplicate_complete_blocked=True,
            unsafe_dispatch_blocked=True,
            blocked_on_execution_id=f"exec-{started.run_id}",
        )
        existing = registry.by_id(started.run_id)
        assert existing is not None

        updated = RunRecord(
            run_id=existing.run_id,
            step_id=existing.step_id,
            plan_path=existing.plan_path,
            agent=existing.agent,
            status=existing.status,
            created_at=existing.created_at,
            started_at=existing.started_at,
            updated_at=existing.updated_at,
            finished_at=existing.finished_at,
            artifact_root=existing.artifact_root,
            output_summary=existing.output_summary,
            source=existing.source,
            legacy_run_id=existing.legacy_run_id,
            legacy_migration_state=existing.legacy_migration_state,
            continuity_blocker=existing.continuity_blocker,
            runtime_state=existing.runtime_state,
            operator_notifications=existing.operator_notifications,
            dispatch_recovery_gate=gate,
        )
        registry.save(updated)

        result = app.recover(step_id="core.deploy")
        report = result.recovery_report
        assert report is not None

        # EXPECTED-RED: The report should carry dispatch_recovery_gate
        assert report.dispatch_recovery_gate is not None, (
            "RED: RecoveryReport must carry dispatch_recovery_gate from persisted state"
        )
        assert report.dispatch_recovery_gate.duplicate_complete_blocked is True, (
            "RED: dispatch_recovery_gate must preserve duplicate_complete_blocked=True"
        )
        assert report.dispatch_recovery_gate.unsafe_dispatch_blocked is True, (
            "RED: dispatch_recovery_gate must preserve unsafe_dispatch_blocked=True"
        )


class TestProtectedPathExplicitSurfaceBehavior:
    """Rule 4: protected-path conflict behavior must be surfaced explicitly
    after restart.

    OPERATOR-CONFLICT-RECOVERY.md section 5.4:
    'runtime must either restore protected paths before reconcile while
    preserving evidence, or stop and surface the issue explicitly'

    EXPECTED-RED: The recover() method does not yet surface
    protected_path_policy in the RecoveryReport.
    """

    def test_recovery_report_surfaces_protected_path_policy(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A recovery with a protected-path merge conflict must surface
        the protected_path_policy explicitly in the RecoveryReport,
        not silently downgrade it to 'none' or omit it."""
        app = _build_app(tmp_path)
        started = app.run(step_id="core.deploy", agent="python-executor")
        assert started.run_id is not None

        registry = RunRegistry(store_root=app._config.run_store_root)  # type: ignore[arg-type]

        # Persist RunRecord with protected-path conflict in runtime_state
        reconcile = ReconcileRecoveryState(
            execution_id=f"exec-{started.run_id}",
            workspace_id=f"ws-{started.run_id}",
            status="merge_conflict",
            summary="protected path conflict: plan.yaml",
            conflict_files=("plan.yaml",),
            protected_paths=("plan.yaml",),
            protected_path_policy="blocked_explicitly",
            target_ref="refs/heads/main",
            target_head_at_prepare="abc123",
            artifact_refs=("artifact://reconcile/conflict",),
        )
        runtime_state = RuntimeRecoveryRecord(
            workspace_id=f"ws-{started.run_id}",
            step_id="core.deploy",
            worktree_path=f".vectl/workspaces/ws-{started.run_id}",
            scratch_branch="scratch/core.deploy",
            target_ref="refs/heads/main",
            target_head_at_prepare="abc123",
            execution_id=f"exec-{started.run_id}",
            reconcile_state=reconcile,
            paused_routing_state="paused_reconcile_conflict",
        )
        existing = registry.by_id(started.run_id)
        assert existing is not None

        updated = RunRecord(
            run_id=existing.run_id,
            step_id=existing.step_id,
            plan_path=existing.plan_path,
            agent=existing.agent,
            status=existing.status,
            created_at=existing.created_at,
            started_at=existing.started_at,
            updated_at=existing.updated_at,
            finished_at=existing.finished_at,
            artifact_root=existing.artifact_root,
            output_summary=existing.output_summary,
            source=existing.source,
            legacy_run_id=existing.legacy_run_id,
            legacy_migration_state=existing.legacy_migration_state,
            continuity_blocker=existing.continuity_blocker,
            runtime_state=runtime_state,
            operator_notifications=existing.operator_notifications,
            dispatch_recovery_gate=existing.dispatch_recovery_gate,
        )
        registry.save(updated)

        result = app.recover(step_id="core.deploy")
        report = result.recovery_report
        assert report is not None

        # EXPECTED-RED: report.runtime_state must carry reconcile_state
        # with protected_path_policy="blocked_explicitly"
        assert report.runtime_state is not None, "RED: RecoveryReport must carry runtime_state"
        assert report.runtime_state.reconcile_state is not None, (
            "RED: RecoveryReport.runtime_state must carry reconcile_state"
        )
        assert report.runtime_state.reconcile_state.status == "merge_conflict", (
            "RED: reconcile_state must preserve merge_conflict status"
        )
        assert report.runtime_state.reconcile_state.protected_path_policy == "blocked_explicitly", (
            "RED: protected_path_policy must be 'blocked_explicitly' after recovery, "
            "not silently downgraded to 'none'"
        )
        assert "plan.yaml" in report.runtime_state.reconcile_state.protected_paths, (
            "RED: protected_paths must survive recovery and include 'plan.yaml'"
        )


class TestOperatorNotificationMatchesOrchAppSurface:
    """Verify OrchestrationApp's OperatorNotification matches the persistence
    shape from OperatorNotificationRecord."""

    def test_orch_app_notification_fields_cover_spec(self) -> None:
        """OrchApp's OperatorNotification must have all fields from
        OPERATOR-CONFLICT-RECOVERY.md section 4.2 NotificationRecord."""
        notif = OperatorNotification(
            case_id="case-compat",
            summary="compatibility check",
            notification_id="notif-compat",
            run_id="run-compat",
            kind="operator_required",
            status="pending",
            evidence_refs=("artifact://compat",),
            operator_message="check compat",
            paused_routing_state="paused_operator_wait",
        )
        # Spec-prescribed fields (section 4.2)
        assert notif.notification_id == "notif-compat"
        assert notif.case_id == "case-compat"
        assert notif.run_id == "run-compat"
        assert notif.kind == "operator_required"
        assert notif.status == "pending"
        assert notif.summary == "compatibility check"
        assert notif.operator_message == "check compat"
        assert notif.evidence_refs == ("artifact://compat",)
        assert notif.paused_routing_state == "paused_operator_wait"
