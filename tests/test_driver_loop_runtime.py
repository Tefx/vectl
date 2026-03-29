"""Expected-RED runtime behavior tests for driver loop.

These tests verify implementation behavior:
- Startup recovery path scaffolding
- Claim and dispatch flow with single runner
- Reconcile success path with accepted evidence
- Reconcile failure path with retry threshold behavior
- Graceful shutdown path bookkeeping

NOTE: These tests WILL FAIL until implementation is complete.
They are EXPECTED-RED tests that document required behavior.
The downstream implementation step is: driver-judgment-runtime-minimal.impl-loop-runtime-minimal

Architecture Reference: docs/DRIVER-ARCHITECTURE.md Section 2.11, Section 3, Section 4
Blueprint Reference: DRIVER-BLUEPRINT.md Flow 1-3
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

import src.vectl.driver.loop as loop_module
from src.vectl.driver.config import (
    DriverConfig,
    JudgeConfig,
    ObservabilityConfig,
    OrchestrationConfig,
    RunnerConfig,
    SessionConfig,
)
from src.vectl.driver.errors import ConfigError
from src.vectl.driver.judge import Judge
from src.vectl.driver.loop import (
    COMPLETION_AUTHORITY_A1_CONTRACT,
    DEFERRED_REPLAN_BRANCHES,
    GRACEFUL_SHUTDOWN_CONTRACT,
    STARTUP_RECOVERY_CONTRACT,
    CompletionAuthorityContract,
    handle_complete,
    handle_dispatch,
    reconcile,
    run,
    shutdown,
)
from src.vectl.driver.observe import FileObserver
from src.vectl.driver.runners import Runner
from src.vectl.driver.session import SessionPool
from src.vectl.driver.types import (
    CompletedEntry,
    DriverState,
    RunnerResult,
    RunnerStatus,
    StartupRecoveryBoundaryOutput,
)
from vectl.models import DecideOutput

# =============================================================================
# FIXTURES
# =============================================================================


@pytest.fixture
def driver_config(tmp_path: Path) -> DriverConfig:
    """Fixture for minimal driver config with single runner."""
    return DriverConfig(
        plan_path=str(tmp_path / "plan.yaml"),
        runners={
            "opencode": RunnerConfig(
                command="opencode",
                args=["run", "--format", "json"],
                prompt_mode="stdin_dash",
                stall_timeout=300,
                output_parser="opencode_jsonl",
            ),
        },
        agent_routing={},
        fallback_runner="opencode",
        orchestration=OrchestrationConfig(max_parallelism=1),
        session=SessionConfig(reuse_ttl=300),
        judge=JudgeConfig(
            runner="opencode",
            structured_output=True,
            timeout=60,
            preflight=False,  # Disable preflight for minimal happy path
        ),
        observability=ObservabilityConfig(events_file=str(tmp_path / "events.jsonl")),
    )


@pytest.fixture
def mock_runner() -> Any:
    """Mock runner for single-runner happy path."""

    class MockRunnerHandle:
        session_id: str | None = "test-session"
        pid: int | None = 12345

        async def wait(self, timeout: float | None = None) -> RunnerResult:
            return RunnerResult(
                status=RunnerStatus.SUCCESS,
                session_id=self.session_id,
                output="Task completed successfully",
                elapsed_seconds=1.0,
                exit_code=0,
            )

        async def kill(self) -> None:
            pass

        def is_alive(self) -> bool:
            return False

    class MockRunner:
        name = "opencode"

        async def dispatch(
            self,
            prompt: str,
            agent: str,
            workdir: str,
            session_id: str | None = None,
        ) -> MockRunnerHandle:
            return MockRunnerHandle()

    return MockRunner()


@pytest.fixture
def mock_judge(driver_config: DriverConfig) -> Judge:
    """Mock judge for single-judge happy path."""
    mock_observer = MagicMock()
    mock_observer.emit = MagicMock()
    mock_observer.close = MagicMock()
    return Judge(driver_config.judge, mock_observer)


@pytest.fixture
def session_pool(driver_config: DriverConfig) -> SessionPool:
    """Session pool for single-runner reuse."""
    return SessionPool(driver_config.session)


@pytest.fixture
def driver_state() -> DriverState:
    """Fresh driver state for each test."""
    return DriverState()


@pytest.fixture
def observer(driver_config: DriverConfig) -> FileObserver:
    """Observer for event emission."""
    return FileObserver(driver_config.observability)


@pytest.fixture
def completed_entry_success() -> CompletedEntry:
    """Completed entry for successful runner result."""
    return CompletedEntry(
        step_id="core.impl",
        agent="python-executor",
        runner_name="opencode",
        result=RunnerResult(
            status=RunnerStatus.SUCCESS,
            session_id="test-session",
            output="Implementation complete\nTests passed\nEvidence: OK",
            elapsed_seconds=5.0,
            exit_code=0,
        ),
        worktree_path="/tmp/worktree-core.impl",
        elapsed_seconds=5.0,
    )


@pytest.fixture
def completed_entry_failure() -> CompletedEntry:
    """Completed entry for failed runner result (transport error)."""
    return CompletedEntry(
        step_id="core.impl",
        agent="python-executor",
        runner_name="opencode",
        result=RunnerResult(
            status=RunnerStatus.FAIL,
            session_id=None,
            output="Process crashed: timeout",
            elapsed_seconds=60.0,
            exit_code=1,
        ),
        worktree_path="/tmp/worktree-core.impl",
        elapsed_seconds=60.0,
    )


# =============================================================================
# STARTUP RECOVERY PATH TESTS (EXPECTED-RED)
# =============================================================================


class TestStartupRecoveryContract:
    """Tests for startup recovery contract values."""

    def test_startup_recovery_contract_defaults(self) -> None:
        """STARTUP_RECOVERY_CONTRACT MUST have correct defaults for minimal runtime."""
        contract = STARTUP_RECOVERY_CONTRACT
        assert contract.reload_plan is True
        assert contract.repair_claims is True
        assert contract.cleanup_orphans is True
        assert contract.anomaly_judgment_deferred is True

    def test_graceful_shutdown_contract_defaults(self) -> None:
        """GRACEFUL_SHUTDOWN_CONTRACT MUST have correct defaults."""
        contract = GRACEFUL_SHUTDOWN_CONTRACT
        assert contract.set_halt_requested is True
        assert contract.wait_for_running_handles is True
        assert contract.kill_remaining_processes is True
        assert contract.cleanup_orphans is True
        assert contract.emit_final_event is True
        assert contract.close_observer is True

    def test_deferred_replan_branches_are_declared(self) -> None:
        """DEFERRED_REPLAN_BRANCHES MUST be bounded and declared."""
        assert len(DEFERRED_REPLAN_BRANCHES) == 5
        branch_names = {b.branch for b in DEFERRED_REPLAN_BRANCHES}
        assert "handle_dispatch.preflight_replan" in branch_names
        assert "reconcile.evidence_replan" in branch_names
        assert "reconcile.failure_classification_replan" in branch_names
        assert "reconcile.escalation_replan" in branch_names
        assert "run.startup_recovery_anomaly_replan" in branch_names


class TestCompletionAuthorityA1Contract:
    """Contract pins for reconcile-only completion authority convergence."""

    def test_completion_authority_contract_shape(self) -> None:
        """A1 completion authority contract MUST expose required metadata."""
        contract = COMPLETION_AUTHORITY_A1_CONTRACT
        assert isinstance(contract, CompletionAuthorityContract)
        assert contract.contract_id == "driver-runtime-completion-authority-a1"
        assert contract.source_step_id == "driver-debt-completion-authority.contract"
        assert contract.sole_completion_sink == "wait_for_any_then_reconcile"
        assert contract.implementation_owner_step == "driver-debt-completion-authority.impl"

    def test_completion_authority_required_statements_are_pinned(self) -> None:
        """A1 contract MUST pin required completion authority statements."""
        contract = COMPLETION_AUTHORITY_A1_CONTRACT
        assert (
            "MUST NOT pass raw completed_results"
            in contract.decide_runtime_completed_results_policy
        )
        assert (
            "MUST NOT execute handle_complete()" in contract.runtime_main_path_forbidden_actions[0]
        )

        side_effects = set(contract.reconcile_side_effect_owner)
        assert side_effects == {
            "evidence judgment",
            "complete/defer lifecycle mutation",
            "merge",
            "session record",
            "worktree cleanup",
            "event emission",
        }

    def test_completion_authority_legacy_shim_and_risk_blockers(self) -> None:
        """A1 contract MUST document shim disposition and blocker regressions."""
        contract = COMPLETION_AUTHORITY_A1_CONTRACT
        assert contract.handle_complete_disposition in {
            "delete_preferred_if_feasible",
            "legacy_internal_shim_only",
        }
        assert len(contract.handle_complete_removal_conditions) >= 1
        assert contract.escalation_once_per_completed_result is True
        assert "duplicate complete_step() for one runner result" in contract.blocker_regressions
        assert (
            "duplicate STEP_COMPLETED emission for one runner result"
            in contract.blocker_regressions
        )


class TestStartupRecoveryPath:
    """Tests for startup recovery behavior (EXPECTED-RED).

    Implementation owner: driver-judgment-runtime-minimal.impl-loop-runtime-minimal
    """

    @pytest.mark.xfail(reason="run() not implemented (expected-red)")
    async def test_run_raises_config_error_for_missing_config(self, tmp_path: Path) -> None:
        """run() MUST raise ConfigError when config file not found."""
        missing_config = tmp_path / "nonexistent.yaml"
        with pytest.raises(ConfigError, match="not found"):
            await run(missing_config)

    @pytest.mark.xfail(reason="run() not implemented (expected-red)")
    async def test_run_loads_durable_plan_state(
        self, driver_config: DriverConfig, tmp_path: Path
    ) -> None:
        """run() MUST reload plan from disk during startup recovery."""
        plan_path = tmp_path / "plan.yaml"
        plan_path.write_text("phases: []\n")

        # This should succeed after loading the plan
        await asyncio.wait_for(run(plan_path), timeout=0.1)
        # Expected: NotImplementedError stub until impl

    @pytest.mark.xfail(reason="run() not implemented (expected-red)")
    async def test_run_performs_claim_repair(
        self, driver_config: DriverConfig, tmp_path: Path
    ) -> None:
        """run() MUST repair stale claim metadata during startup recovery."""
        plan_path = tmp_path / "plan.yaml"
        plan_path.write_text("phases: []\n")

        # This should invoke repair_claims logic
        await run(plan_path)
        # Expected: NotImplementedError stub until impl

    @pytest.mark.xfail(reason="run() not implemented (expected-red)")
    async def test_run_cleans_up_orphan_worktrees(
        self, driver_config: DriverConfig, tmp_path: Path
    ) -> None:
        """run() MUST clean up orphan worktrees during startup recovery."""
        plan_path = tmp_path / "plan.yaml"
        plan_path.write_text("phases: []\n")

        # This should invoke worktree.cleanup for orphaned paths
        await run(plan_path)
        # Expected: NotImplementedError stub until impl


# =============================================================================
# CLAIM AND DISPATCH FLOW TESTS (EXPECTED-RED)
# =============================================================================


class TestClaimAndDispatchFlow:
    """Tests for claim_and_dispatch action handling (EXPECTED-RED).

    Minimal runtime scope: single-runner happy path only.
    Implementation owner: driver-judgment-runtime-minimal.impl-loop-runtime-minimal
    """

    @pytest.mark.xfail(reason="handle_dispatch() not implemented (expected-red)")
    async def test_handle_dispatch_loads_plan_before_mutation(
        self,
        driver_config: DriverConfig,
        driver_state: DriverState,
        mock_runner: Runner,
        mock_judge: Judge,
        session_pool: SessionPool,
        observer: FileObserver,
        tmp_path: Path,
    ) -> None:
        """handle_dispatch() MUST reload plan from disk before claim."""
        from vectl.models import Action

        plan_path = tmp_path / "plan.yaml"
        plan_path.write_text("phases: []\n")

        action = Action(
            action="claim_and_dispatch",
            step_id="core.impl",
            agent="python-executor",
            session="fresh",
        )

        await handle_dispatch(
            action=action,
            state=driver_state,
            config=driver_config,
            runners={"opencode": mock_runner},
            judge=mock_judge,
            session_pool=session_pool,
            observer=observer,
            plan_path=plan_path,
        )
        # Expected: NotImplementedError stub until impl

    @pytest.mark.xfail(reason="handle_dispatch() not implemented (expected-red)")
    async def test_handle_dispatch_claims_step(
        self,
        driver_config: DriverConfig,
        driver_state: DriverState,
        mock_runner: Runner,
        mock_judge: Judge,
        session_pool: SessionPool,
        observer: FileObserver,
        tmp_path: Path,
    ) -> None:
        """handle_dispatch() MUST claim step using CAS-safe lifecycle."""
        from vectl.models import Action

        plan_path = tmp_path / "plan.yaml"
        plan_path.write_text("phases: []\n")

        action = Action(
            action="claim_and_dispatch",
            step_id="core.impl",
            agent="python-executor",
            session="fresh",
        )

        await handle_dispatch(
            action=action,
            state=driver_state,
            config=driver_config,
            runners={"opencode": mock_runner},
            judge=mock_judge,
            session_pool=session_pool,
            observer=observer,
            plan_path=plan_path,
        )
        # Expected: NotImplementedError stub until impl

    @pytest.mark.xfail(reason="handle_dispatch() not implemented (expected-red)")
    async def test_handle_dispatch_creates_worktree(
        self,
        driver_config: DriverConfig,
        driver_state: DriverState,
        mock_runner: Runner,
        mock_judge: Judge,
        session_pool: SessionPool,
        observer: FileObserver,
        tmp_path: Path,
    ) -> None:
        """handle_dispatch() MUST create worktree for step."""
        from vectl.models import Action

        plan_path = tmp_path / "plan.yaml"
        plan_path.write_text("phases: []\n")

        action = Action(
            action="claim_and_dispatch",
            step_id="core.impl",
            agent="python-executor",
            session="fresh",
        )

        await handle_dispatch(
            action=action,
            state=driver_state,
            config=driver_config,
            runners={"opencode": mock_runner},
            judge=mock_judge,
            session_pool=session_pool,
            observer=observer,
            plan_path=plan_path,
        )
        # Expected: NotImplementedError stub until impl

    @pytest.mark.xfail(reason="handle_dispatch() not implemented (expected-red)")
    async def test_handle_dispatch_resolves_runner(
        self,
        driver_config: DriverConfig,
        driver_state: DriverState,
        mock_runner: Runner,
        mock_judge: Judge,
        session_pool: SessionPool,
        observer: FileObserver,
        tmp_path: Path,
    ) -> None:
        """handle_dispatch() MUST resolve runner using config.route_agent()."""
        from vectl.models import Action

        plan_path = tmp_path / "plan.yaml"
        plan_path.write_text("phases: []\n")

        action = Action(
            action="claim_and_dispatch",
            step_id="core.impl",
            agent="python-executor",
            session="fresh",
        )

        await handle_dispatch(
            action=action,
            state=driver_state,
            config=driver_config,
            runners={"opencode": mock_runner},
            judge=mock_judge,
            session_pool=session_pool,
            observer=observer,
            plan_path=plan_path,
        )
        # Expected: NotImplementedError stub until impl

    @pytest.mark.xfail(reason="handle_dispatch() not implemented (expected-red)")
    async def test_handle_dispatch_registers_in_state(
        self,
        driver_config: DriverConfig,
        driver_state: DriverState,
        mock_runner: Runner,
        mock_judge: Judge,
        session_pool: SessionPool,
        observer: FileObserver,
        tmp_path: Path,
    ) -> None:
        """handle_dispatch() MUST register running task in DriverState."""
        from vectl.models import Action

        plan_path = tmp_path / "plan.yaml"
        plan_path.write_text("phases: []\n")

        action = Action(
            action="claim_and_dispatch",
            step_id="core.impl",
            agent="python-executor",
            session="fresh",
        )

        await handle_dispatch(
            action=action,
            state=driver_state,
            config=driver_config,
            runners={"opencode": mock_runner},
            judge=mock_judge,
            session_pool=session_pool,
            observer=observer,
            plan_path=plan_path,
        )
        # Expected: NotImplementedError stub until impl

    @pytest.mark.xfail(reason="handle_dispatch() not implemented (expected-red)")
    async def test_handle_dispatch_skips_preflight_when_disabled(
        self,
        driver_config: DriverConfig,
        driver_state: DriverState,
        mock_runner: Runner,
        mock_judge: Judge,
        session_pool: SessionPool,
        observer: FileObserver,
        tmp_path: Path,
    ) -> None:
        """handle_dispatch() MUST skip preflight when disabled in config."""
        from vectl.models import Action

        # driver_config already has preflight=False
        plan_path = tmp_path / "plan.yaml"
        plan_path.write_text("phases: []\n")

        action = Action(
            action="claim_and_dispatch",
            step_id="core.impl",
            agent="python-executor",
            session="fresh",
        )

        await handle_dispatch(
            action=action,
            state=driver_state,
            config=driver_config,
            runners={"opencode": mock_runner},
            judge=mock_judge,
            session_pool=session_pool,
            observer=observer,
            plan_path=plan_path,
        )
        # Expected: NotImplementedError stub until impl


# =============================================================================
# RECONCILE SUCCESS PATH TESTS (EXPECTED-RED)
# =============================================================================


class TestReconcileSuccessPath:
    """Tests for reconcile success path (EXPECTED-RED).

    Minimal runtime scope: single-runner / single-judge happy path.
    Implementation owner: driver-judgment-runtime-minimal.impl-loop-runtime-minimal
    """

    @pytest.mark.xfail(reason="reconcile() not implemented (expected-red)")
    async def test_reconcile_success_path_completes_step(
        self,
        driver_config: DriverConfig,
        driver_state: DriverState,
        mock_judge: Judge,
        session_pool: SessionPool,
        observer: FileObserver,
        completed_entry_success: CompletedEntry,
        tmp_path: Path,
    ) -> None:
        """reconcile() MUST complete step on success."""
        plan_path = tmp_path / "plan.yaml"
        plan_path.write_text("phases: []\n")

        await reconcile(
            completed=completed_entry_success,
            state=driver_state,
            judge=mock_judge,
            session_pool=session_pool,
            observer=observer,
            plan_path=plan_path,
        )
        # Expected: NotImplementedError stub until impl

    @pytest.mark.xfail(reason="reconcile() not implemented (expected-red)")
    async def test_reconcile_success_path_merges_worktree(
        self,
        driver_config: DriverConfig,
        driver_state: DriverState,
        mock_judge: Judge,
        session_pool: SessionPool,
        observer: FileObserver,
        completed_entry_success: CompletedEntry,
        tmp_path: Path,
    ) -> None:
        """reconcile() MUST merge worktree under merge_lock on success."""
        plan_path = tmp_path / "plan.yaml"
        plan_path.write_text("phases: []\n")

        await reconcile(
            completed=completed_entry_success,
            state=driver_state,
            judge=mock_judge,
            session_pool=session_pool,
            observer=observer,
            plan_path=plan_path,
        )
        # Expected: NotImplementedError stub until impl

    @pytest.mark.xfail(reason="reconcile() not implemented (expected-red)")
    async def test_reconcile_success_path_records_session(
        self,
        driver_config: DriverConfig,
        driver_state: DriverState,
        mock_judge: Judge,
        session_pool: SessionPool,
        observer: FileObserver,
        completed_entry_success: CompletedEntry,
        tmp_path: Path,
    ) -> None:
        """reconcile() MUST record session for reuse after successful merge."""
        plan_path = tmp_path / "plan.yaml"
        plan_path.write_text("phases: []\n")

        await reconcile(
            completed=completed_entry_success,
            state=driver_state,
            judge=mock_judge,
            session_pool=session_pool,
            observer=observer,
            plan_path=plan_path,
        )
        # Expected: NotImplementedError stub until impl

    @pytest.mark.xfail(reason="reconcile() not implemented (expected-red)")
    async def test_reconcile_success_path_cleans_up_worktree(
        self,
        driver_config: DriverConfig,
        driver_state: DriverState,
        mock_judge: Judge,
        session_pool: SessionPool,
        observer: FileObserver,
        completed_entry_success: CompletedEntry,
        tmp_path: Path,
    ) -> None:
        """reconcile() MUST clean up worktree after successful merge."""
        plan_path = tmp_path / "plan.yaml"
        plan_path.write_text("phases: []\n")

        await reconcile(
            completed=completed_entry_success,
            state=driver_state,
            judge=mock_judge,
            session_pool=session_pool,
            observer=observer,
            plan_path=plan_path,
        )
        # Expected: NotImplementedError stub until impl


# =============================================================================
# RECONCILE FAILURE PATH TESTS (EXPECTED-RED)
# =============================================================================


class TestReconcileFailurePath:
    """Tests for reconcile failure path (EXPECTED-RED).

    Implementation owner: driver-judgment-runtime-minimal.impl-loop-runtime-minimal
    """

    @pytest.mark.xfail(reason="reconcile() not implemented (expected-red)")
    async def test_reconcile_failure_path_increments_counter(
        self,
        driver_config: DriverConfig,
        driver_state: DriverState,
        mock_judge: Judge,
        session_pool: SessionPool,
        observer: FileObserver,
        completed_entry_failure: CompletedEntry,
        tmp_path: Path,
    ) -> None:
        """reconcile() MUST increment failure counter on failure."""
        plan_path = tmp_path / "plan.yaml"
        plan_path.write_text("phases: []\n")

        await reconcile(
            completed=completed_entry_failure,
            state=driver_state,
            judge=mock_judge,
            session_pool=session_pool,
            observer=observer,
            plan_path=plan_path,
        )
        # Expected: NotImplementedError stub until impl

    @pytest.mark.xfail(reason="reconcile() not implemented (expected-red)")
    async def test_reconcile_failure_path_defers_under_threshold(
        self,
        driver_config: DriverConfig,
        driver_state: DriverState,
        mock_judge: Judge,
        session_pool: SessionPool,
        observer: FileObserver,
        completed_entry_failure: CompletedEntry,
        tmp_path: Path,
    ) -> None:
        """reconcile() MUST defer step when failure count < 3."""
        plan_path = tmp_path / "plan.yaml"
        plan_path.write_text("phases: []\n")

        # First failure (count becomes 1)
        await reconcile(
            completed=completed_entry_failure,
            state=driver_state,
            judge=mock_judge,
            session_pool=session_pool,
            observer=observer,
            plan_path=plan_path,
        )
        # Expected: NotImplementedError stub until impl

    @pytest.mark.xfail(reason="reconcile() not implemented (expected-red)")
    async def test_reconcile_failure_path_escalates_at_threshold(
        self,
        driver_config: DriverConfig,
        driver_state: DriverState,
        mock_judge: Judge,
        session_pool: SessionPool,
        observer: FileObserver,
        completed_entry_failure: CompletedEntry,
        tmp_path: Path,
    ) -> None:
        """reconcile() MUST escalate when failure count >= 3."""
        plan_path = tmp_path / "plan.yaml"
        plan_path.write_text("phases: []\n")

        # Pre-populate failure count to 2 (threshold - 1)
        driver_state.failure_counts["core.impl"] = 2

        await reconcile(
            completed=completed_entry_failure,
            state=driver_state,
            judge=mock_judge,
            session_pool=session_pool,
            observer=observer,
            plan_path=plan_path,
        )
        # Expected: NotImplementedError stub until impl


# =============================================================================
# GRACEFUL SHUTDOWN PATH TESTS (EXPECTED-RED)
# =============================================================================


class TestGracefulShutdownPath:
    """Tests for graceful shutdown behavior (EXPECTED-RED).

    Implementation owner: driver-judgment-runtime-minimal.impl-loop-runtime-minimal
    """

    @pytest.mark.xfail(reason="shutdown() not implemented (expected-red)")
    async def test_shutdown_sets_halt_requested(
        self,
        driver_state: DriverState,
        observer: FileObserver,
    ) -> None:
        """shutdown() MUST set state.halt_requested = True."""
        await shutdown(state=driver_state, observer=observer)
        # Expected: NotImplementedError stub until impl

    @pytest.mark.xfail(reason="shutdown() not implemented (expected-red)")
    async def test_shutdown_waits_for_running_handles(
        self,
        driver_state: DriverState,
        observer: FileObserver,
    ) -> None:
        """shutdown() MUST wait for running handles with timeout."""
        await shutdown(state=driver_state, observer=observer)
        # Expected: NotImplementedError stub until impl

    @pytest.mark.xfail(reason="shutdown() not implemented (expected-red)")
    async def test_shutdown_kills_remaining_processes(
        self,
        driver_state: DriverState,
        observer: FileObserver,
    ) -> None:
        """shutdown() MUST kill remaining processes after timeout."""
        await shutdown(state=driver_state, observer=observer)
        # Expected: NotImplementedError stub until impl

    @pytest.mark.xfail(reason="shutdown() not implemented (expected-red)")
    async def test_shutdown_cleans_up_orphan_worktrees(
        self,
        driver_state: DriverState,
        observer: FileObserver,
    ) -> None:
        """shutdown() MUST clean up orphan worktrees."""
        await shutdown(state=driver_state, observer=observer)
        # Expected: NotImplementedError stub until impl

    @pytest.mark.xfail(reason="shutdown() not implemented (expected-red)")
    async def test_shutdown_emits_final_event(
        self,
        driver_state: DriverState,
        observer: FileObserver,
    ) -> None:
        """shutdown() MUST emit FINAL event with state.summary()."""
        await shutdown(state=driver_state, observer=observer)
        # Expected: NotImplementedError stub until impl

    @pytest.mark.xfail(reason="shutdown() not implemented (expected-red)")
    async def test_shutdown_closes_observer(
        self,
        driver_state: DriverState,
        observer: FileObserver,
    ) -> None:
        """shutdown() MUST close the observer."""
        await shutdown(state=driver_state, observer=observer)
        # Expected: NotImplementedError stub until impl


# =============================================================================
# HANDLE COMPLETE TESTS (EXPECTED-RED)
# =============================================================================


class TestHandleComplete:
    """Tests for handle_complete action handling (EXPECTED-RED).

    Implementation owner: driver-judgment-runtime-minimal.impl-loop-runtime-minimal
    """

    @pytest.mark.xfail(reason="handle_complete() not implemented (expected-red)")
    async def test_handle_complete_reloads_plan_before_mutation(
        self,
        driver_state: DriverState,
        mock_judge: Judge,
        session_pool: SessionPool,
        observer: FileObserver,
        tmp_path: Path,
    ) -> None:
        """handle_complete() MUST reload plan from disk before mutation."""
        from vectl.models import Action

        plan_path = tmp_path / "plan.yaml"
        plan_path.write_text("phases: []\n")

        action = Action(
            action="complete",
            step_id="core.impl",
            evidence="Tests passed",
        )

        await handle_complete(
            action=action,
            state=driver_state,
            judge=mock_judge,
            session_pool=session_pool,
            observer=observer,
            plan_path=plan_path,
        )
        # Expected: NotImplementedError stub until impl


class TestDecideStateRuntimeWiring:
    """Regression coverage for decide-state instance ownership and loop wiring."""

    def test_driver_state_decide_state_is_instance_local(self) -> None:
        """Two DriverState instances must not share decide-side mutable state."""
        state_a = DriverState()
        state_b = DriverState()

        assert state_a.decide_state is not state_b.decide_state

        state_a.decide_state.failure_counts["core.impl"] = 2
        state_a.decide_state.completion_times["core.impl"] = 123.0
        state_a.decide_state.session_registry["core.impl"] = "task-a"

        assert state_b.decide_state.failure_counts == {}
        assert state_b.decide_state.completion_times == {}
        assert state_b.decide_state.session_registry == {}

    def test_run_main_loop_passes_driver_decide_state_to_decide(
        self,
        driver_config: DriverConfig,
        driver_state: DriverState,
        mock_judge: Judge,
        session_pool: SessionPool,
        observer: FileObserver,
        tmp_path: Path,
    ) -> None:
        """Main loop must provide explicit state.decide_state to decide()."""
        captured_state: dict[str, object] = {}

        def fake_decide(
            *, running_tasks: Any, completed_results: Any, max_parallelism: int, state: Any
        ) -> DecideOutput:
            captured_state["state"] = state
            captured_state["running_tasks"] = running_tasks
            captured_state["completed_results"] = completed_results
            captured_state["max_parallelism"] = max_parallelism
            return DecideOutput(
                actions=[],
                continuation=False,
                halt_reason="NO_EXECUTABLE_STEPS",
                decision_log=[],
            )

        with patch.object(loop_module, "decide", side_effect=fake_decide):
            asyncio.run(
                loop_module._run_main_loop(
                    state=driver_state,
                    config=driver_config,
                    runners={},
                    judge=mock_judge,
                    session_pool=session_pool,
                    observer=observer,
                    plan_path=tmp_path / "plan.yaml",
                )
            )

        assert captured_state["state"] is driver_state.decide_state
        assert captured_state["running_tasks"] == []
        assert captured_state["completed_results"] is None
        assert captured_state["max_parallelism"] == driver_config.orchestration.max_parallelism

    @pytest.mark.xfail(reason="handle_complete() not implemented (expected-red)")
    async def test_handle_complete_uses_cas_safe_lifecycle(
        self,
        driver_state: DriverState,
        mock_judge: Judge,
        session_pool: SessionPool,
        observer: FileObserver,
        tmp_path: Path,
    ) -> None:
        """handle_complete() MUST use CAS-safe lifecycle operations."""
        from vectl.models import Action

        plan_path = tmp_path / "plan.yaml"
        plan_path.write_text("phases: []\n")

        action = Action(
            action="complete",
            step_id="core.impl",
            evidence="Tests passed",
        )

        await handle_complete(
            action=action,
            state=driver_state,
            judge=mock_judge,
            session_pool=session_pool,
            observer=observer,
            plan_path=plan_path,
        )
        # Expected: NotImplementedError stub until impl


@pytest.mark.anyio
async def test_run_routes_startup_through_recovery_boundary_matrix_expected_red(
    monkeypatch: pytest.MonkeyPatch,
    driver_config: DriverConfig,
    tmp_path: Path,
) -> None:
    """run() must route startup decisions through boundary matrix surface."""

    class _Observer:
        def emit(self, event_type: str, /, **data: object) -> None:
            return None

        def close(self) -> None:
            return None

    class _RepairResult:
        actions: tuple[object, ...] = ()

    class _Step:
        id = "core.impl"

    class _Phase:
        steps = [_Step()]

    class _Plan:
        context = "startup-boundary-test"
        phases = [_Phase()]

    class _Runner:
        name = "opencode"

    called = {"value": False}

    def _fake_boundary(**_kwargs: object) -> StartupRecoveryBoundaryOutput:
        called["value"] = True
        return StartupRecoveryBoundaryOutput(
            decisions=(),
            resumable_handoffs=(),
            repair_actions=(),
            blocked_reasons=(),
        )

    async def _noop_main_loop(**_kwargs: object) -> None:
        return None

    monkeypatch.setattr(loop_module, "_load_runtime_config", lambda _p: driver_config)
    monkeypatch.setattr(loop_module, "create_observer", lambda _cfg: _Observer())
    monkeypatch.setattr(loop_module, "create_runner", lambda _n, _cfg: _Runner())
    monkeypatch.setattr(loop_module, "Judge", lambda *_a, **_k: MagicMock())
    monkeypatch.setattr(loop_module, "load_plan_definition", lambda _p: (_Plan(), "hash"))
    monkeypatch.setattr(loop_module, "repair_claims", lambda *_a, **_k: _RepairResult())
    monkeypatch.setattr(loop_module, "_cleanup_orphan_worktrees", lambda **_k: ())
    monkeypatch.setattr(loop_module, "_load_ledger_step_ids", lambda _root: set())
    monkeypatch.setattr(loop_module, "_run_main_loop", _noop_main_loop)
    monkeypatch.setattr(loop_module, "evaluate_startup_recovery_boundary", _fake_boundary)

    await run(tmp_path / "driver.yaml")

    assert called["value"] is True
