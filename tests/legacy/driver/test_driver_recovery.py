"""Expected-RED tests for startup restart-recovery boundary.

Source:
- docs/DRIVER-ARCHITECTURE.md Section 2.11 (startup recovery ordering)
- docs/DRIVER-CONTINUITY-FOUNDATION.md Section 3, Section 4, and Section 7
  (ledger/plan/claims reconciliation, restart matrix, judge-policy input)

These tests intentionally require behavior deferred to
``driver-continuity-restart-recovery.impl``.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any

import src.vectl.driver.entrypoint as driver_entrypoint
import src.vectl.driver.loop as loop_module
from src.vectl.driver.config import (
    DriverConfig,
    JudgeConfig,
    ObservabilityConfig,
    OrchestrationConfig,
    RunnerConfig,
    SessionConfig,
)
from src.vectl.driver.loop import (
    STARTUP_RECOVERY_SCAN_ORDER,
    evaluate_startup_recovery_boundary,
)
from src.vectl.driver.types import (
    ContinuityHandoff,
    ContinuityJournalEntry,
    ContinuityLedgerEntry,
    JudgeContinuityPolicyOutput,
    ReplaySafetyEnvelope,
    StartupRecoveryBoundaryInput,
    StartupRecoveryBoundaryOutput,
    StartupRecoveryDecision,
    StartupRecoveryJudgeInput,
    StartupRecoveryReconciliationFacts,
)


def _ledger_entry(step_id: str) -> ContinuityLedgerEntry:
    envelope = ReplaySafetyEnvelope(
        step_id=step_id,
        attempt_key=f"{step_id}:opencode:session-1",
        runner_name="opencode",
        session_id="session-1",
        idempotency_scope="step-attempt",
    )
    journal = ContinuityJournalEntry(
        step_id=step_id,
        event_kind="failure",
        recorded_at="2026-03-29T00:00:00Z",
        attempt_key=envelope.attempt_key,
        runner_name="opencode",
        session_id="session-1",
        summary="runner failed",
        replay_envelope=envelope,
    )
    return ContinuityLedgerEntry(
        step_id=step_id,
        latest_attempt_key=envelope.attempt_key,
        status="failed",
        runner_name="opencode",
        last_session_id="session-1",
        replay_envelope=envelope,
        last_journal_event=journal,
    )


def _boundary_input(
    *,
    plan_step_ids: tuple[str, ...],
    claim_step_ids: tuple[str, ...],
    ledger_step_ids: tuple[str, ...],
    capability_snapshot_ids: tuple[str, ...] = ("opencode|resume=1|persist=1",),
    judge_inputs: tuple[StartupRecoveryJudgeInput, ...] = (),
) -> StartupRecoveryBoundaryInput:
    ledger_entries = tuple(_ledger_entry(step_id) for step_id in ledger_step_ids)
    return StartupRecoveryBoundaryInput(
        reconciliation=StartupRecoveryReconciliationFacts(
            plan_step_ids=plan_step_ids,
            claim_step_ids=claim_step_ids,
            ledger_step_ids=ledger_step_ids,
        ),
        capability_snapshot_ids=capability_snapshot_ids,
        ledger_entries=ledger_entries,
        judge_failure_inputs=judge_inputs,
    )


def test_startup_recovery_scan_order_is_pinned() -> None:
    """Startup scan order contract must remain explicit and stable."""

    assert STARTUP_RECOVERY_SCAN_ORDER == (
        "load_plan",
        "repair_claims",
        "cleanup_orphan_worktrees",
        "scan_continuity_ledger",
        "reconcile_plan_claims_ledger",
        "consume_judge_failure_policy_inputs",
        "classify_resume_restart_halt",
    )


def test_startup_recovery_halts_on_plan_claims_ledger_divergence_expected_red() -> None:
    """Divergent ledger/plan/claims must classify to explicit HALT."""

    boundary_output = evaluate_startup_recovery_boundary(
        boundary_input=_boundary_input(
            plan_step_ids=("core.impl",),
            claim_step_ids=("core.impl",),
            ledger_step_ids=("core.impl", "orphan.step"),
        )
    )

    assert (
        StartupRecoveryDecision(
            step_id="orphan.step",
            disposition="halt",
            reason="ledger_plan_claims_divergence",
        )
        in boundary_output.decisions
    )


def test_startup_recovery_consumes_judge_failure_halt_input_expected_red() -> None:
    """Judge failure policy HALT outcome must feed startup HALT matrix path."""

    judge_input = StartupRecoveryJudgeInput(
        step_id="core.impl",
        attempt_key="core.impl:opencode:session-1",
        policy=JudgeContinuityPolicyOutput(
            step_id="core.impl",
            judgment_type="FAILURE",
            judge_outcome="timeout",
            action="halt",
            provenance="judge_transport_timeout",
            continuity_recovery_reason="judge_timeout_no_fallback_runner",
            attempt_key="core.impl:opencode:session-1",
            halt_reason="judge instructed halt",
        ),
    )

    boundary_output = evaluate_startup_recovery_boundary(
        boundary_input=_boundary_input(
            plan_step_ids=("core.impl",),
            claim_step_ids=("core.impl",),
            ledger_step_ids=("core.impl",),
            judge_inputs=(judge_input,),
        )
    )

    assert (
        StartupRecoveryDecision(
            step_id="core.impl",
            disposition="halt",
            reason="judge_policy_halt",
            source_attempt_key="core.impl:opencode:session-1",
        )
        in boundary_output.decisions
    )


def test_startup_recovery_selects_restart_when_capability_snapshot_missing_expected_red() -> None:
    """Missing capability snapshot must force restart, not resume."""

    boundary_output = evaluate_startup_recovery_boundary(
        boundary_input=_boundary_input(
            plan_step_ids=("core.impl",),
            claim_step_ids=("core.impl",),
            ledger_step_ids=("core.impl",),
            capability_snapshot_ids=(),
        )
    )

    assert any(
        decision.step_id == "core.impl"
        and decision.disposition == "restart"
        and decision.reason == "capability_snapshot_missing"
        for decision in boundary_output.decisions
    )

    assert all(
        isinstance(handoff, ContinuityHandoff) for handoff in boundary_output.resumable_handoffs
    )


def test_startup_recovery_halts_when_ledger_status_completed() -> None:
    """Completed ledger status must halt startup resume/restart classification."""

    completed_entry = replace(
        _ledger_entry("core.impl"),
        status="completed",
        recovery_cursor="terminal",
    )
    boundary_output = evaluate_startup_recovery_boundary(
        boundary_input=StartupRecoveryBoundaryInput(
            reconciliation=StartupRecoveryReconciliationFacts(
                plan_step_ids=("core.impl",),
                claim_step_ids=("core.impl",),
                ledger_step_ids=("core.impl",),
            ),
            capability_snapshot_ids=(),
            ledger_entries=(completed_entry,),
            judge_failure_inputs=(),
        )
    )

    assert any(
        decision.step_id == "core.impl"
        and decision.disposition == "halt"
        and decision.reason == "ledger_status_completed"
        for decision in boundary_output.decisions
    )


def test_startup_recovery_restarts_when_recovery_cursor_pre_merge() -> None:
    """pre_merge recovery cursor must force restart before capability checks."""

    pre_merge_entry = replace(
        _ledger_entry("core.impl"),
        status="runner_success",
        recovery_cursor="pre_merge",
    )
    boundary_output = evaluate_startup_recovery_boundary(
        boundary_input=StartupRecoveryBoundaryInput(
            reconciliation=StartupRecoveryReconciliationFacts(
                plan_step_ids=("core.impl",),
                claim_step_ids=("core.impl",),
                ledger_step_ids=("core.impl",),
            ),
            capability_snapshot_ids=(),
            ledger_entries=(pre_merge_entry,),
            judge_failure_inputs=(),
        )
    )

    assert any(
        decision.step_id == "core.impl"
        and decision.disposition == "restart"
        and decision.reason == "recovery_cursor_pre_merge"
        for decision in boundary_output.decisions
    )


def test_entrypoint_path_halts_when_startup_boundary_blocks(
    monkeypatch: Any,
    tmp_path: Path,
) -> None:
    """Entrypoint path must halt runtime when startup boundary blocks."""

    class _Observer:
        def __init__(self) -> None:
            self.halt_events: list[dict[str, object]] = []

        def emit(self, event_type: str, /, **data: object) -> None:
            if event_type == "HALT":
                self.halt_events.append(data)

        def close(self) -> None:
            return None

    class _RepairResult:
        branch = "vectl/step-driver-continuity-restart-recovery.impl"
        actions: tuple[object, ...] = ()

    class _Step:
        id = "core.impl"

    class _Phase:
        steps = [_Step()]

    class _Plan:
        context = "startup-recovery-integration"
        phases = [_Phase()]

    class _Runner:
        name = "opencode"

    driver_config = DriverConfig(
        plan_path=str(tmp_path / "plan.yaml"),
        runners={
            "opencode": RunnerConfig(
                command="opencode",
                args=["run", "--format", "json"],
                output_parser="opencode_jsonl",
            )
        },
        agent_routing={"python-executor": "opencode"},
        fallback_runner="opencode",
        orchestration=OrchestrationConfig(max_parallelism=1),
        session=SessionConfig(reuse_ttl=300),
        judge=JudgeConfig(preflight=False),
        observability=ObservabilityConfig(),
    )

    observed = _Observer()
    main_loop_called = {"value": False}

    async def _noop_main_loop(**_kwargs: object) -> None:
        main_loop_called["value"] = True

    monkeypatch.setattr(loop_module, "_load_runtime_config", lambda _p: driver_config)
    monkeypatch.setattr(loop_module, "create_observer", lambda _cfg: observed)
    monkeypatch.setattr(loop_module, "create_runner", lambda _n, _cfg: _Runner())
    monkeypatch.setattr(loop_module, "Judge", lambda *_a, **_k: _Runner())
    monkeypatch.setattr(loop_module, "load_plan_definition", lambda _p: (_Plan(), "hash"))
    monkeypatch.setattr(loop_module, "repair_claims", lambda *_a, **_k: _RepairResult())
    monkeypatch.setattr(loop_module, "load_claims_for_branch", lambda *_a, **_k: {})
    monkeypatch.setattr(loop_module, "_cleanup_orphan_worktrees", lambda **_k: ())
    monkeypatch.setattr(
        loop_module,
        "_load_ledger_entries",
        lambda _root: (
            (_ledger_entry("orphan.step"),),
            (),
        ),
    )
    monkeypatch.setattr(loop_module, "_run_main_loop", _noop_main_loop)

    exit_code = driver_entrypoint.run_driver_module_entrypoint(
        argv=[str(tmp_path / "driver.yaml")],
        adapter=driver_entrypoint.get_runtime_adapter(),
    )

    assert exit_code == 0
    assert main_loop_called["value"] is False
    assert observed.halt_events


def test_run_consumes_durable_judge_halt_policy_inputs_on_startup(
    monkeypatch: Any,
    tmp_path: Path,
) -> None:
    """Startup run path must forward durable judge HALT facts to boundary input."""

    class _Observer:
        def emit(self, event_type: str, /, **data: object) -> None:
            return None

        def close(self) -> None:
            return None

    class _RepairResult:
        branch = "vectl/step-core.impl"
        actions: tuple[object, ...] = ()

    class _Step:
        id = "core.impl"

    class _Phase:
        steps = [_Step()]

    class _Plan:
        context = "startup-recovery-judge-input"
        phases = [_Phase()]

    class _Runner:
        name = "opencode"

    driver_config = DriverConfig(
        plan_path=str(tmp_path / "plan.yaml"),
        runners={
            "opencode": RunnerConfig(
                command="opencode",
                args=["run", "--format", "json"],
                output_parser="opencode_jsonl",
            )
        },
        agent_routing={"python-executor": "opencode"},
        fallback_runner="opencode",
        orchestration=OrchestrationConfig(max_parallelism=1),
        session=SessionConfig(reuse_ttl=300),
        judge=JudgeConfig(preflight=False),
        observability=ObservabilityConfig(),
    )

    captured: dict[str, object] = {}

    class _CaptureController:
        def plan_recovery(
            self,
            *,
            boundary_input: StartupRecoveryBoundaryInput,
        ) -> StartupRecoveryBoundaryOutput:
            captured["boundary_input"] = boundary_input
            return StartupRecoveryBoundaryOutput(
                decisions=(),
                resumable_handoffs=(),
                repair_actions=(),
                blocked_reasons=(),
            )

    async def _noop_main_loop(**_kwargs: object) -> None:
        return None

    judge_policy = JudgeContinuityPolicyOutput(
        step_id="core.impl",
        judgment_type="FAILURE",
        judge_outcome="HALT",
        action="halt",
        provenance="judge_verdict",
        continuity_recovery_reason="judge_requested_halt",
        attempt_key="core.impl:opencode:session-1",
        halt_reason="unsafe replay",
    )

    monkeypatch.setattr(loop_module, "_load_runtime_config", lambda _p: driver_config)
    monkeypatch.setattr(loop_module, "create_observer", lambda _cfg: _Observer())
    monkeypatch.setattr(loop_module, "create_runner", lambda _n, _cfg: _Runner())
    monkeypatch.setattr(loop_module, "Judge", lambda *_a, **_k: _Runner())
    monkeypatch.setattr(loop_module, "load_plan_definition", lambda _p: (_Plan(), "hash"))
    monkeypatch.setattr(loop_module, "repair_claims", lambda *_a, **_k: _RepairResult())
    monkeypatch.setattr(
        loop_module, "load_claims_for_branch", lambda *_a, **_k: {"core.impl": "python-senior"}
    )
    monkeypatch.setattr(loop_module, "_cleanup_orphan_worktrees", lambda **_k: ())
    monkeypatch.setattr(
        loop_module,
        "_load_ledger_entries",
        lambda _root: (
            (
                ContinuityLedgerEntry(
                    step_id="core.impl",
                    latest_attempt_key="core.impl:opencode:session-1",
                    status="failed",
                    runner_name="opencode",
                    last_session_id="session-1",
                    replay_envelope=ReplaySafetyEnvelope(
                        step_id="core.impl",
                        attempt_key="core.impl:opencode:session-1",
                        runner_name="opencode",
                        session_id="session-1",
                        idempotency_scope="step-attempt",
                    ),
                    last_journal_event=ContinuityJournalEntry(
                        step_id="core.impl",
                        event_kind="judge_failure_policy",
                        recorded_at="2026-03-29T00:00:00Z",
                        attempt_key="core.impl:opencode:session-1",
                        runner_name="opencode",
                        session_id="session-1",
                        summary="unsafe replay",
                        replay_envelope=ReplaySafetyEnvelope(
                            step_id="core.impl",
                            attempt_key="core.impl:opencode:session-1",
                            runner_name="opencode",
                            session_id="session-1",
                            idempotency_scope="step-attempt",
                        ),
                    ),
                    judge_policy=judge_policy,
                    recovery_cursor="halt_requested_by_judge_policy",
                ),
            ),
            (),
        ),
    )
    monkeypatch.setattr(loop_module, "_startup_recovery_controller", lambda: _CaptureController())
    monkeypatch.setattr(loop_module, "_run_main_loop", _noop_main_loop)

    exit_code = driver_entrypoint.run_driver_module_entrypoint(
        argv=[str(tmp_path / "driver.yaml")],
        adapter=driver_entrypoint.get_runtime_adapter(),
    )

    assert exit_code == 0
    boundary_input = captured["boundary_input"]
    assert isinstance(boundary_input, StartupRecoveryBoundaryInput)
    assert len(boundary_input.judge_failure_inputs) == 1
    assert boundary_input.judge_failure_inputs[0].policy.action == "halt"
    assert boundary_input.judge_failure_inputs[0].attempt_key == "core.impl:opencode:session-1"


def test_evaluate_startup_recovery_boundary_routes_through_controller(monkeypatch: Any) -> None:
    """Standalone evaluator must route through StartupRecoveryController."""

    expected = StartupRecoveryBoundaryOutput(
        decisions=(),
        resumable_handoffs=(),
        repair_actions=("restart:core.impl:test",),
        blocked_reasons=("core.impl:test",),
    )
    captured: dict[str, object] = {"called": False}

    class _Controller:
        def plan_recovery(
            self,
            *,
            boundary_input: StartupRecoveryBoundaryInput,
        ) -> StartupRecoveryBoundaryOutput:
            captured["called"] = True
            captured["boundary_input"] = boundary_input
            return expected

    boundary_input = _boundary_input(
        plan_step_ids=("core.impl",),
        claim_step_ids=("core.impl",),
        ledger_step_ids=("core.impl",),
    )
    monkeypatch.setattr(loop_module, "_startup_recovery_controller", lambda: _Controller())

    observed = evaluate_startup_recovery_boundary(boundary_input=boundary_input)

    assert observed == expected
    assert captured["called"] is True
    assert captured["boundary_input"] == boundary_input


def test_run_consumes_ledger_status_and_recovery_cursor_via_controller(
    monkeypatch: Any,
    tmp_path: Path,
) -> None:
    """Production startup path must consume status/recovery_cursor for control flow."""

    class _Observer:
        def __init__(self) -> None:
            self.halt_events: list[dict[str, object]] = []
            self.startup_decisions: list[dict[str, object]] = []

        def emit(self, event_type: str, /, **data: object) -> None:
            if event_type == "HALT":
                self.halt_events.append(data)
            if event_type == "STARTUP_RECOVERY_DECISION":
                self.startup_decisions.append(data)

        def close(self) -> None:
            return None

    class _RepairResult:
        branch = "vectl/step-core.impl"
        actions: tuple[object, ...] = ()

    class _StepCore:
        id = "core.impl"

    class _StepApi:
        id = "api.impl"

    class _Phase:
        steps = [_StepCore(), _StepApi()]

    class _Plan:
        context = "startup-recovery-ledger-status-cursor"
        phases = [_Phase()]

    class _Runner:
        name = "opencode"

    driver_config = DriverConfig(
        plan_path=str(tmp_path / "plan.yaml"),
        runners={
            "opencode": RunnerConfig(
                command="opencode",
                args=["run", "--format", "json"],
                output_parser="opencode_jsonl",
            )
        },
        agent_routing={"python-executor": "opencode"},
        fallback_runner="opencode",
        orchestration=OrchestrationConfig(max_parallelism=1),
        session=SessionConfig(reuse_ttl=300),
        judge=JudgeConfig(preflight=False),
        observability=ObservabilityConfig(),
    )

    observed = _Observer()
    main_loop_called = {"value": False}

    async def _noop_main_loop(**_kwargs: object) -> None:
        main_loop_called["value"] = True

    completed_status_entry = replace(
        _ledger_entry("core.impl"),
        status="completed",
        recovery_cursor="terminal",
    )
    cursor_halt_entry = replace(
        _ledger_entry("api.impl"),
        status="failed",
        recovery_cursor="halt_requested_by_judge_policy",
    )

    monkeypatch.setattr(loop_module, "_load_runtime_config", lambda _p: driver_config)
    monkeypatch.setattr(loop_module, "create_observer", lambda _cfg: observed)
    monkeypatch.setattr(loop_module, "create_runner", lambda _n, _cfg: _Runner())
    monkeypatch.setattr(loop_module, "Judge", lambda *_a, **_k: _Runner())
    monkeypatch.setattr(loop_module, "load_plan_definition", lambda _p: (_Plan(), "hash"))
    monkeypatch.setattr(loop_module, "repair_claims", lambda *_a, **_k: _RepairResult())
    monkeypatch.setattr(
        loop_module,
        "load_claims_for_branch",
        lambda *_a, **_k: {
            "core.impl": "python-senior",
            "api.impl": "python-executor",
        },
    )
    monkeypatch.setattr(loop_module, "_cleanup_orphan_worktrees", lambda **_k: ())
    monkeypatch.setattr(
        loop_module,
        "_load_ledger_entries",
        lambda _root: (
            (completed_status_entry, cursor_halt_entry),
            (),
        ),
    )
    monkeypatch.setattr(loop_module, "_run_main_loop", _noop_main_loop)

    exit_code = driver_entrypoint.run_driver_module_entrypoint(
        argv=[str(tmp_path / "driver.yaml")],
        adapter=driver_entrypoint.get_runtime_adapter(),
    )

    decision_reasons = {decision["reason"] for decision in observed.startup_decisions}

    assert exit_code == 0
    assert main_loop_called["value"] is False
    assert decision_reasons == {
        "ledger_status_completed",
        "recovery_cursor_halt_requested_by_judge_policy",
    }
    assert observed.halt_events


def test_run_uses_startup_recovery_controller_contract(
    monkeypatch: Any,
    tmp_path: Path,
) -> None:
    """Startup run path must invoke StartupRecoveryController.plan_recovery."""

    class _Observer:
        def emit(self, event_type: str, /, **data: object) -> None:
            return None

        def close(self) -> None:
            return None

    class _RepairResult:
        branch = "vectl/step-core.impl"
        actions: tuple[object, ...] = ()

    class _Step:
        id = "core.impl"

    class _Phase:
        steps = [_Step()]

    class _Plan:
        context = "startup-recovery-controller-contract"
        phases = [_Phase()]

    class _Runner:
        name = "opencode"

    class _Controller:
        def __init__(self) -> None:
            self.called = False
            self.boundary_input: StartupRecoveryBoundaryInput | None = None

        def plan_recovery(
            self,
            *,
            boundary_input: StartupRecoveryBoundaryInput,
        ) -> StartupRecoveryBoundaryOutput:
            self.called = True
            self.boundary_input = boundary_input
            return StartupRecoveryBoundaryOutput(
                decisions=(),
                resumable_handoffs=(),
                repair_actions=(),
                blocked_reasons=(),
            )

    driver_config = DriverConfig(
        plan_path=str(tmp_path / "plan.yaml"),
        runners={
            "opencode": RunnerConfig(
                command="opencode",
                args=["run", "--format", "json"],
                output_parser="opencode_jsonl",
            )
        },
        agent_routing={"python-executor": "opencode"},
        fallback_runner="opencode",
        orchestration=OrchestrationConfig(max_parallelism=1),
        session=SessionConfig(reuse_ttl=300),
        judge=JudgeConfig(preflight=False),
        observability=ObservabilityConfig(),
    )

    main_loop_called = {"value": False}

    async def _noop_main_loop(**_kwargs: object) -> None:
        main_loop_called["value"] = True

    controller = _Controller()

    monkeypatch.setattr(loop_module, "_load_runtime_config", lambda _p: driver_config)
    monkeypatch.setattr(loop_module, "create_observer", lambda _cfg: _Observer())
    monkeypatch.setattr(loop_module, "create_runner", lambda _n, _cfg: _Runner())
    monkeypatch.setattr(loop_module, "Judge", lambda *_a, **_k: _Runner())
    monkeypatch.setattr(loop_module, "load_plan_definition", lambda _p: (_Plan(), "hash"))
    monkeypatch.setattr(loop_module, "repair_claims", lambda *_a, **_k: _RepairResult())
    monkeypatch.setattr(
        loop_module, "load_claims_for_branch", lambda *_a, **_k: {"core.impl": "python-senior"}
    )
    monkeypatch.setattr(loop_module, "_cleanup_orphan_worktrees", lambda **_k: ())
    monkeypatch.setattr(loop_module, "_load_ledger_entries", lambda _root: ((), ()))
    monkeypatch.setattr(loop_module, "_startup_recovery_controller", lambda: controller)
    monkeypatch.setattr(loop_module, "_run_main_loop", _noop_main_loop)

    exit_code = driver_entrypoint.run_driver_module_entrypoint(
        argv=[str(tmp_path / "driver.yaml")],
        adapter=driver_entrypoint.get_runtime_adapter(),
    )

    assert exit_code == 0
    assert controller.called is True
    assert isinstance(controller.boundary_input, StartupRecoveryBoundaryInput)
    assert main_loop_called["value"] is True
