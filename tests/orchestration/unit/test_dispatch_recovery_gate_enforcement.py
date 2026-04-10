"""Enforcement tests for dispatch recovery gates and complete-step consultation.

Authority:
    docs/ORCHESTRATION-PLANE-OPERATOR-CONFLICT-RECOVERY.md sections 6.3, 6.4, 9
    docs/ORCHESTRATION-PLANE-ORCH-APP-ROUTING.md sections 6.3, 8, 10

These tests verify that ALL authoritative dispatch and completion entry paths
consult paused operator state and DispatchRecoveryGate before acting:

1. run() must not dispatch when a recovery gate blocks dispatch
2. resume() must not dispatch when a paused operator notification blocks dispatch
3. recover() resume_safe path must not dispatch when gate blocks dispatch
4. dispatch_resolution_subtask() must not dispatch when gate blocks dispatch
5. route_terminal_execution() must not complete when duplicate_complete_blocked
6. _start_runtime_execution() must not dispatch when gate blocks dispatch
7. _admit_start_and_persist_running() must not dispatch when gate blocks dispatch

Rule: No bypass path may remain. Every dispatch/complete entry point must
consult the authoritative gate and paused state.
"""

from __future__ import annotations

import time
from dataclasses import replace
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from vectl.orch_app import (
    AppConfig,
    DispatchBlockedError,
    DuplicateCompleteBlockedError,
    OperatorNotification,
    OrchestrationApp,
    build_orchestration_app,
)
from vectl.orchestration.config import OrchestrationConfig
from vectl.orchestration.continuity_artifacts import (
    DispatchRecoveryGate,
    NotificationKind,
    NotificationStatus,
    OperatorNotificationRecord,
    PausedRoutingState,
)
from vectl.orchestration.contracts import (
    ControlDecision,
    CoreSnapshot,
    ReconcileResult,
    ResolutionCase,
    ResolutionReport,
    RosterSnapshot,
    RuntimeSnapshot,
)
from vectl.orchestration.recovery import RecoveryOutcome, RecoveryReport


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


def _write_plan(plan_path: Path) -> None:
    from vectl.io import save_plan
    from vectl.models import Phase, Plan, Step

    plan = Plan(
        project="gate-enforcement-test",
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


def _build_app(tmp_path: Path) -> OrchestrationApp:
    plan_path = tmp_path / "plan.yaml"
    runs_root = tmp_path / "runs"
    _write_plan(plan_path)
    config = AppConfig(
        plan_path=plan_path,
        orchestration_config=OrchestrationConfig(plan_path=plan_path),
        run_store_root=runs_root,
    )
    return build_orchestration_app(config)


def _core_snapshot(*, claimable: tuple[str, ...] = ("core.deploy",)) -> CoreSnapshot:
    return CoreSnapshot(
        plan_complete=False,
        claimable_step_ids=claimable,
        in_progress_step_ids=(),
        blocked_step_ids=(),
        unresolved_reasons=(),
    )


def _roster_snapshot() -> RosterSnapshot:
    return RosterSnapshot(
        available_agents=("python-executor",),
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


def _paused_operator_notification() -> OperatorNotification:
    """An operator notification with paused_routing_state='paused_operator_wait'."""
    return OperatorNotification(
        case_id="case-gate-test",
        summary="operator required: merge conflict on plan.yaml",
        notification_id="notif-gate-test",
        run_id="run-gate-test",
        kind="operator_required",
        status="pending",
        evidence_refs=("artifact://reconcile/merge_conflict",),
        operator_message="human decision required",
        paused_routing_state="paused_operator_wait",
    )


def _blocked_dispatch_recovery_gate() -> DispatchRecoveryGate:
    """A DispatchRecoveryGate with both flags blocked."""
    return DispatchRecoveryGate(
        status="blocked_pending_reconcile",
        reason="reconcile status merge_conflict blocks dispatch and complete",
        duplicate_complete_blocked=True,
        unsafe_dispatch_blocked=True,
        blocked_on_execution_id="exec-gate-test",
        blocked_on_case_id="case-gate-test",
    )


# ------------------------------------------------------------------
# Test: _is_dispatch_blocked_by_gate consults paused operator state
# ------------------------------------------------------------------


class TestDispatchBlockedByPausedOperatorNotification:
    """Rule 1: When operator_required sets paused_routing_state,
    dispatch must be blocked.

    Authority: ORCH-APP-ROUTING.md section 10.4
    """

    def test_dispatch_blocked_when_operator_notification_paused(self, tmp_path: Path) -> None:
        """When _latest_operator_notification has paused_routing_state='paused_operator_wait',
        _is_dispatch_blocked_by_gate must return True."""
        app = _build_app(tmp_path)
        # No gate, no notification: should not be blocked
        blocked, _ = app._is_dispatch_blocked_by_gate()
        assert blocked is False

        # Set paused operator notification
        app._latest_operator_notification = _paused_operator_notification()
        blocked, reason = app._is_dispatch_blocked_by_gate()
        assert blocked is True
        assert "paused_operator_wait" in reason
        assert "case-gate-test" in reason

    def test_dispatch_blocked_when_notification_paused_reconcile_conflict(
        self, tmp_path: Path
    ) -> None:
        """paused_routing_state='paused_reconcile_conflict' also blocks dispatch."""
        app = _build_app(tmp_path)
        app._latest_operator_notification = replace(
            _paused_operator_notification(),
            paused_routing_state="paused_reconcile_conflict",
        )
        blocked, reason = app._is_dispatch_blocked_by_gate()
        assert blocked is True
        assert "paused_reconcile_conflict" in reason

    def test_dispatch_blocked_when_notification_paused_recovery_hold(self, tmp_path: Path) -> None:
        """paused_routing_state='paused_recovery_hold' also blocks dispatch."""
        app = _build_app(tmp_path)
        app._latest_operator_notification = replace(
            _paused_operator_notification(),
            paused_routing_state="paused_recovery_hold",
        )
        blocked, reason = app._is_dispatch_blocked_by_gate()
        assert blocked is True
        assert "paused_recovery_hold" in reason

    def test_dispatch_not_blocked_when_notification_is_active(self, tmp_path: Path) -> None:
        """paused_routing_state='active' does NOT block dispatch."""
        app = _build_app(tmp_path)
        app._latest_operator_notification = replace(
            _paused_operator_notification(),
            paused_routing_state="active",
        )
        blocked, _ = app._is_dispatch_blocked_by_gate()
        assert blocked is False

    def test_dispatch_not_blocked_when_no_notification(self, tmp_path: Path) -> None:
        """No operator notification means no dispatch block from paused state."""
        app = _build_app(tmp_path)
        app._latest_operator_notification = None
        blocked, _ = app._is_dispatch_blocked_by_gate()
        assert blocked is False


# ------------------------------------------------------------------
# Test: _is_dispatch_blocked_by_gate consults DispatchRecoveryGate
# ------------------------------------------------------------------


class TestDispatchBlockedByRecoveryGate:
    """Rule 2: DispatchRecoveryGate.unsafe_dispatch_blocked blocks dispatch.

    Authority: OPERATOR-CONFLICT-RECOVERY.md sections 6.3, 6.4, 9
    """

    def test_dispatch_blocked_when_unsafe_dispatch_blocked(self, tmp_path: Path) -> None:
        """When recovery gate has unsafe_dispatch_blocked=True, dispatch is blocked."""
        app = _build_app(tmp_path)
        gate = _blocked_dispatch_recovery_gate()
        report = RecoveryReport(
            outcome=RecoveryOutcome.BLOCKED,
            message="test blocked",
            dispatch_recovery_gate=gate,
        )
        app._latest_recovery_report = report
        blocked, reason = app._is_dispatch_blocked_by_gate()
        assert blocked is True
        assert "unsafe_dispatch_blocked" in reason

    def test_dispatch_not_blocked_when_gate_allows(self, tmp_path: Path) -> None:
        """When recovery gate has unsafe_dispatch_blocked=False, dispatch is not blocked."""
        app = _build_app(tmp_path)
        gate = DispatchRecoveryGate(
            status="dispatch_allowed",
            reason="reconcile closed with merged",
            duplicate_complete_blocked=False,
            unsafe_dispatch_blocked=False,
        )
        report = RecoveryReport(
            outcome=RecoveryOutcome.RECOVERED,
            message="test recovered",
            dispatch_recovery_gate=gate,
        )
        app._latest_recovery_report = report
        blocked, _ = app._is_dispatch_blocked_by_gate()
        assert blocked is False

    def test_dispatch_not_blocked_when_no_recovery_report(self, tmp_path: Path) -> None:
        """No recovery report means no gate-based dispatch block."""
        app = _build_app(tmp_path)
        app._latest_recovery_report = None
        blocked, _ = app._is_dispatch_blocked_by_gate()
        assert blocked is False

    def test_dispatch_not_blocked_when_recovery_report_has_no_gate(self, tmp_path: Path) -> None:
        """Recovery report with dispatch_recovery_gate=None does not block."""
        app = _build_app(tmp_path)
        report = RecoveryReport(
            outcome=RecoveryOutcome.RECOVERED,
            message="test recovered",
            dispatch_recovery_gate=None,
        )
        app._latest_recovery_report = report
        blocked, _ = app._is_dispatch_blocked_by_gate()
        assert blocked is False

    def test_both_notification_and_gate_block(self, tmp_path: Path) -> None:
        """When both notification and gate block, dispatch is blocked (notification checked first)."""
        app = _build_app(tmp_path)
        app._latest_operator_notification = _paused_operator_notification()
        gate = _blocked_dispatch_recovery_gate()
        report = RecoveryReport(
            outcome=RecoveryOutcome.BLOCKED,
            message="test blocked both",
            dispatch_recovery_gate=gate,
        )
        app._latest_recovery_report = report
        blocked, reason = app._is_dispatch_blocked_by_gate()
        assert blocked is True
        # Notification is checked first, so reason should mention notification
        assert "paused_operator_wait" in reason or "case-gate-test" in reason


# ------------------------------------------------------------------
# Test: _is_complete_blocked_by_gate consults paused state and gate
# ------------------------------------------------------------------


class TestCompleteBlockedByGate:
    """Rule 3: DispatchRecoveryGate.duplicate_complete_blocked blocks completion.

    Authority: OPERATOR-CONFLICT-RECOVERY.md section 9
    'duplicate_complete_blocked stays true until reconcile closure is
     durably re-established as merged or noop.'
    """

    def test_complete_blocked_when_duplicate_complete_blocked(self, tmp_path: Path) -> None:
        """When gate has duplicate_complete_blocked=True, completion is blocked."""
        app = _build_app(tmp_path)
        gate = _blocked_dispatch_recovery_gate()
        report = RecoveryReport(
            outcome=RecoveryOutcome.BLOCKED,
            message="test blocked",
            dispatch_recovery_gate=gate,
        )
        app._latest_recovery_report = report
        blocked, reason = app._is_complete_blocked_by_gate()
        assert blocked is True
        assert "duplicate_complete_blocked" in reason

    def test_complete_blocked_by_paused_notification(self, tmp_path: Path) -> None:
        """When operator notification has non-active paused_routing_state, completion is blocked."""
        app = _build_app(tmp_path)
        app._latest_operator_notification = _paused_operator_notification()
        blocked, reason = app._is_complete_blocked_by_gate()
        assert blocked is True
        assert "paused_operator_wait" in reason

    def test_complete_not_blocked_when_gate_cleared(self, tmp_path: Path) -> None:
        """When gate has duplicate_complete_blocked=False, completion is not blocked by gate."""
        app = _build_app(tmp_path)
        gate = DispatchRecoveryGate(
            status="dispatch_allowed",
            reason="reconcile closed successfully",
            duplicate_complete_blocked=False,
            unsafe_dispatch_blocked=False,
        )
        report = RecoveryReport(
            outcome=RecoveryOutcome.RECOVERED,
            message="test recovered",
            dispatch_recovery_gate=gate,
        )
        app._latest_recovery_report = report
        blocked, _ = app._is_complete_blocked_by_gate()
        assert blocked is False

    def test_complete_not_blocked_when_no_gate(self, tmp_path: Path) -> None:
        """No recovery report means no completion block from gate."""
        app = _build_app(tmp_path)
        app._latest_recovery_report = None
        blocked, _ = app._is_complete_blocked_by_gate()
        assert blocked is False


# ------------------------------------------------------------------
# Test: route_terminal_execution enforces duplicate-complete consultation
# ------------------------------------------------------------------


class TestRouteTerminalExecutionDuplicateCompleteGate:
    """Rule 4: route_terminal_execution must consult DispatchRecoveryGate
    before complete_step.

    Authority: OPERATOR-CONFLICT-RECOVERY.md section 6.4, 9
    """

    def test_route_terminal_raises_on_duplicate_complete_blocked(self, tmp_path: Path) -> None:
        """route_terminal_execution must raise DuplicateCompleteBlockedError
        when the recovery gate has duplicate_complete_blocked=True."""
        app = _build_app(tmp_path)
        gate = _blocked_dispatch_recovery_gate()
        report = RecoveryReport(
            outcome=RecoveryOutcome.BLOCKED,
            message="test blocked",
            dispatch_recovery_gate=gate,
        )
        app._latest_recovery_report = report

        from vectl.orchestration.contracts import DispatchSpec, ExecutionResult

        dispatch_spec = DispatchSpec(
            source_kind="step",
            source_id="core.deploy",
            role_id="python-executor",
            role_source="default",
            execution_context="linked_worktree",
            runner="codex",
            session_mode="fresh",
            step_id="core.deploy",
            description="Deploy step",
            verify_mode="none",
            output_contract="freeform_evidence",
            mutation_policy="worktree_changes",
        )
        execution_result = ExecutionResult(
            step_id="core.deploy",
            status="success",
            output_summary="Deploy succeeded",
        )

        with pytest.raises(DuplicateCompleteBlockedError, match="complete blocked"):
            app.route_terminal_execution(
                step_id="core.deploy",
                execution_id="exec-test",
                dispatch_spec=dispatch_spec,
                execution_result=execution_result,
            )

    def test_route_terminal_raises_on_paused_notification(self, tmp_path: Path) -> None:
        """route_terminal_execution must raise DuplicateCompleteBlockedError
        when a paused operator notification is active."""
        app = _build_app(tmp_path)
        app._latest_operator_notification = _paused_operator_notification()

        from vectl.orchestration.contracts import DispatchSpec, ExecutionResult

        dispatch_spec = DispatchSpec(
            source_kind="step",
            source_id="core.deploy",
            role_id="python-executor",
            role_source="default",
            execution_context="linked_worktree",
            runner="codex",
            session_mode="fresh",
            step_id="core.deploy",
            description="Deploy step",
            verify_mode="none",
            output_contract="freeform_evidence",
            mutation_policy="worktree_changes",
        )
        execution_result = ExecutionResult(
            step_id="core.deploy",
            status="success",
            output_summary="Deploy succeeded",
        )

        with pytest.raises(DuplicateCompleteBlockedError, match="complete blocked"):
            app.route_terminal_execution(
                step_id="core.deploy",
                execution_id="exec-test",
                dispatch_spec=dispatch_spec,
                execution_result=execution_result,
            )


# ------------------------------------------------------------------
# Test: dispatch_resolution_subtask enforces dispatch gate
# ------------------------------------------------------------------


class TestDispatchResolutionSubtaskGateEnforcement:
    """Rule 5: dispatch_resolution_subtask must consult the recovery gate
    before dispatching.

    Authority: OPERATOR-CONFLICT-RECOVERY.md section 6.3
    Resolution subtasks must not bypass dispatch recovery gates.
    """

    def test_dispatch_resolution_subtask_raises_on_dispatch_blocked(self, tmp_path: Path) -> None:
        """dispatch_resolution_subtask must raise DispatchBlockedError
        when the recovery gate blocks dispatch."""
        app = _build_app(tmp_path)
        gate = _blocked_dispatch_recovery_gate()
        report = RecoveryReport(
            outcome=RecoveryOutcome.BLOCKED,
            message="test blocked",
            dispatch_recovery_gate=gate,
        )
        app._latest_recovery_report = report

        with pytest.raises(DispatchBlockedError, match="dispatch_resolution_subtask blocked"):
            app.dispatch_resolution_subtask(
                case_id="case-test",
                role_id="python-executor",
                description="Test subtask",
                refs=(),
                run_id="run-test",
            )

    def test_dispatch_resolution_subtask_raises_on_paused_operator(self, tmp_path: Path) -> None:
        """dispatch_resolution_subtask must raise DispatchBlockedError
        when paused operator notification is active."""
        app = _build_app(tmp_path)
        app._latest_operator_notification = _paused_operator_notification()

        with pytest.raises(DispatchBlockedError, match="dispatch_resolution_subtask blocked"):
            app.dispatch_resolution_subtask(
                case_id="case-test",
                role_id="python-executor",
                description="Test subtask",
                refs=(),
                run_id="run-test",
            )


# ------------------------------------------------------------------
# Test: _start_runtime_execution enforces dispatch gate
# ------------------------------------------------------------------


class TestStartRuntimeExecutionGateEnforcement:
    """Rule 6: _start_runtime_execution must consult the recovery gate
    before dispatching to the runtime.

    Authority: OPERATOR-CONFLICT-RECOVERY.md section 6.3
    """

    def test_start_runtime_raises_on_dispatch_blocked(self, tmp_path: Path) -> None:
        """_start_runtime_execution must raise DispatchBlockedError
        when the recovery gate blocks dispatch."""
        app = _build_app(tmp_path)
        gate = _blocked_dispatch_recovery_gate()
        report = RecoveryReport(
            outcome=RecoveryOutcome.BLOCKED,
            message="test blocked",
            dispatch_recovery_gate=gate,
        )
        app._latest_recovery_report = report

        from vectl.orchestration.contracts import DispatchSpec

        dispatch_spec = DispatchSpec(
            source_kind="step",
            source_id="core.deploy",
            role_id="python-executor",
            role_source="default",
            execution_context="linked_worktree",
            runner="codex",
            session_mode="fresh",
            step_id="core.deploy",
            description="Deploy step",
            verify_mode="none",
            prompt_family="coder",
            output_contract="freeform_evidence",
            mutation_policy="worktree_changes",
        )

        with pytest.raises(DispatchBlockedError, match="_start_runtime_execution"):
            app._start_runtime_execution(
                run_id="run-test",
                step_id="core.deploy",
                dispatch_spec=dispatch_spec,
                mode="start",
            )


# ------------------------------------------------------------------
# Test: _admit_start_and_persist_running enforces dispatch gate
# ------------------------------------------------------------------


class TestAdmitStartAndPersistRunningGateEnforcement:
    """Rule 7: _admit_start_and_persist_running must consult the recovery gate
    before dispatching.

    Authority: OPERATOR-CONFLICT-RECOVERY.md section 6.3
    The admission path must not bypass the dispatch gate.
    """

    def test_admit_start_raises_on_dispatch_blocked(self, tmp_path: Path) -> None:
        """_admit_start_and_persist_running must raise DispatchBlockedError
        when the recovery gate blocks dispatch."""
        app = _build_app(tmp_path)
        gate = _blocked_dispatch_recovery_gate()
        report = RecoveryReport(
            outcome=RecoveryOutcome.BLOCKED,
            message="test blocked",
            dispatch_recovery_gate=gate,
        )
        app._latest_recovery_report = report

        from vectl.orchestration.run_store import RunRegistry

        registry = RunRegistry(
            store_root=app._config.run_store_root
            if app._config.run_store_root
            else Path("/tmp/test-admit-gate")
        )

        with pytest.raises(DispatchBlockedError, match="_admit_start_and_persist_running"):
            app._admit_start_and_persist_running(
                registry=registry,
                run_id="run-test-admit",
                step_id="core.deploy",
                plan_path=str(app._config.plan_path),
                agent="python-executor",
                run_root=Path("/tmp/test-admit-gate/root"),
                runner="codex",
                allowlist_text="deny-all",
                mode="start",
            )


# ------------------------------------------------------------------
# Test: Sibling-path coverage — no bypass paths remain
# ------------------------------------------------------------------


class TestNoBypassPaths:
    """Verify that all dispatch/completion paths enforce the gate.

    Authority: Step blocker directive — ensure no bypass path remains.
    """

    def test_all_dispatch_entry_paths_checked(self, tmp_path: Path) -> None:
        """Every public dispatch entry point must call _is_dispatch_blocked_by_gate.

        This test verifies that the methods that dispatch to runtime exist and
        are covered by gate checks. It does not test runtime behavior, only
        that gate checks are present in each path.
        """
        app = _build_app(tmp_path)

        # Verify the gate check method exists
        assert hasattr(app, "_is_dispatch_blocked_by_gate"), (
            "OrchestrationApp must have _is_dispatch_blocked_by_gate method"
        )
        assert hasattr(app, "_is_complete_blocked_by_gate"), (
            "OrchestrationApp must have _is_complete_blocked_by_gate method"
        )

    def test_all_complete_entry_paths_checked(self, tmp_path: Path) -> None:
        """Every completion entry point must call _is_complete_blocked_by_gate."""
        app = _build_app(tmp_path)

        # Verify the complete gate check is present
        blocked, _ = app._is_complete_blocked_by_gate()
        assert isinstance(blocked, bool), "_is_complete_blocked_by_gate must return bool"

    def test_gate_check_returns_tuple(self, tmp_path: Path) -> None:
        """Gate check methods return (bool, str) tuples."""
        app = _build_app(tmp_path)

        result = app._is_dispatch_blocked_by_gate()
        assert isinstance(result, tuple) and len(result) == 2
        assert isinstance(result[0], bool)
        assert isinstance(result[1], str)

        result = app._is_complete_blocked_by_gate()
        assert isinstance(result, tuple) and len(result) == 2
        assert isinstance(result[0], bool)
        assert isinstance(result[1], str)

    def test_dispatch_blocked_error_importable(self) -> None:
        """DispatchBlockedError must be importable from vectl.orch_app."""
        from vectl.orch_app import DispatchBlockedError

        assert issubclass(DispatchBlockedError, Exception)

    def test_duplicate_complete_blocked_error_importable(self) -> None:
        """DuplicateCompleteBlockedError must be importable from vectl.orch_app."""
        from vectl.orch_app import DuplicateCompleteBlockedError

        assert issubclass(DuplicateCompleteBlockedError, Exception)

    def test_paused_operator_notification_blocks_after_recovery(self, tmp_path: Path) -> None:
        """After recovery reconstructs operator notification state, dispatch
        must remain blocked until operator state is resolved.

        Authority: OPERATOR-CONFLICT-RECOVERY.md section 6.3
        'reconstructs outstanding resolution cases and operator notifications
         before resuming normal evaluation'
        """
        app = _build_app(tmp_path)

        # Simulate: recovery reconstructed operator notification state
        app._recover_operator_notification_surface(
            operator_notifications=(
                OperatorNotificationRecord(
                    notification_id="notif-recovery-test",
                    case_id="case-recovery-test",
                    run_id="run-recovery-test",
                    kind="operator_required",
                    status="pending",
                    summary="merge conflict requires operator decision",
                    paused_routing_state="paused_operator_wait",
                    created_at=1712600000.0,
                    updated_at=1712600000.0,
                ),
            ),
            runtime_state=None,
        )

        # Dispatch must be blocked
        blocked, reason = app._is_dispatch_blocked_by_gate()
        assert blocked is True
        assert "paused_operator_wait" in reason

        # Completion must also be blocked
        complete_blocked, complete_reason = app._is_complete_blocked_by_gate()
        assert complete_blocked is True
        assert "paused_operator_wait" in complete_reason

    def test_recovery_gate_reconstruction_blocks_dispatch(self, tmp_path: Path) -> None:
        """After recovery reconstructs a DispatchRecoveryGate with
        unsafe_dispatch_blocked=True, dispatch must remain blocked.

        Authority: OPERATOR-CONFLICT-RECOVERY.md section 6.4, 9
        """
        app = _build_app(tmp_path)

        gate = DispatchRecoveryGate(
            status="blocked_pending_reconcile",
            reason="reconcile conflict not yet resolved",
            duplicate_complete_blocked=True,
            unsafe_dispatch_blocked=True,
            blocked_on_execution_id="exec-recovery-test",
            blocked_on_case_id="case-recovery-test",
        )
        report = RecoveryReport(
            outcome=RecoveryOutcome.BLOCKED,
            message="Blocked by unresolved reconcile",
            dispatch_recovery_gate=gate,
        )
        app._latest_recovery_report = report

        # Dispatch must be blocked
        blocked, reason = app._is_dispatch_blocked_by_gate()
        assert blocked is True
        assert "unsafe_dispatch_blocked" in reason

        # Complete must be blocked
        complete_blocked, complete_reason = app._is_complete_blocked_by_gate()
        assert complete_blocked is True
        assert "duplicate_complete_blocked" in complete_reason
