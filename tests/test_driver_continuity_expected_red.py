"""Expected-RED tests for continuity authority/ledger persistence gaps.

Source:
- docs/DRIVER-CONTINUITY-FOUNDATION.md §3 (source-of-truth matrix)
- docs/DRIVER-CONTINUITY-FOUNDATION.md §4 (restart/resume/abort/replay)
- docs/DRIVER-CONTINUITY-FOUNDATION.md §7 (journal write-point governance)

These tests intentionally require durable continuity behavior that is not yet
implemented. They must fail red until
`driver-continuity-authority-ledger.impl` lands.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import pytest

from src.vectl.driver.config import (
    DriverConfig,
    JudgeConfig,
    ObservabilityConfig,
    OrchestrationConfig,
    RunnerConfig,
    SessionConfig,
)
from src.vectl.driver.loop import handle_dispatch, reconcile, run, shutdown
from src.vectl.driver.session import SessionPool
from src.vectl.driver.types import CompletedEntry, DriverState, RunnerResult, RunnerStatus
from src.vectl.driver.worktree import MergeOutcome, MergeResult, Success, WorktreeBinding
from vectl.models import Action


@dataclass
class _FakeStep:
    id: str
    description: str = "implementation"
    verification: str = "pytest -q"
    refs: list[str] | None = None
    depends_on: list[str] | None = None

    def __post_init__(self) -> None:
        if self.refs is None:
            self.refs = []
        if self.depends_on is None:
            self.depends_on = []


@dataclass
class _FakePhase:
    steps: list[_FakeStep]


class _FakePlan:
    def __init__(self, step: _FakeStep) -> None:
        self.context = "continuity-test"
        self._step = step
        self.phases = [_FakePhase(steps=[step])]

    def find_step(self, step_id: str) -> tuple[_FakePhase, _FakeStep] | None:
        if step_id != self._step.id:
            return None
        return (self.phases[0], self._step)


class _RecordingObserver:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, object]]] = []

    def emit(self, event_type: str, /, **data: object) -> None:
        self.events.append((event_type, data))

    def close(self) -> None:
        return


class _NoopJudge:
    pass


def _driver_config(plan_path: Path) -> DriverConfig:
    return DriverConfig(
        plan_path=str(plan_path),
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


class _Runner:
    name = "opencode"

    async def dispatch(
        self,
        prompt: str,
        agent: str,
        workdir: str,
        session_id: str | None = None,
    ) -> Any:
        local_session_id = session_id

        class _Handle:
            pid: int | None = 1
            session_id: str | None = local_session_id

            async def wait(self, timeout: float | None = None) -> RunnerResult:
                return RunnerResult(
                    status=RunnerStatus.SUCCESS,
                    session_id=local_session_id,
                    output="ok",
                    elapsed_seconds=0.1,
                    exit_code=0,
                )

            async def kill(self) -> None:
                return

            def is_alive(self) -> bool:
                return False

        return _Handle()


@pytest.mark.anyio
async def test_dispatch_detects_and_journals_sessionpool_decide_double_truth(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Dispatch must detect contradictory reuse authorities.

    Source: docs/DRIVER-CONTINUITY-FOUNDATION.md §3 and §5.
    """

    step_id = "core.impl"
    plan_path = tmp_path / "plan.yaml"
    state = DriverState()
    observer = _RecordingObserver()
    session_pool = SessionPool(SessionConfig(reuse_ttl=300))
    session_pool.record(
        step_id="core.contract",
        session_id="pool-session",
        runner_name="opencode",
        agent="python-executor",
    )

    monkeypatch.setattr(
        "src.vectl.driver.loop.load_plan_definition",
        lambda _p: (_FakePlan(_FakeStep(id=step_id, depends_on=["core.contract"])), "h"),
    )
    monkeypatch.setattr("src.vectl.driver.loop.claim_step", lambda plan, *_a, **_k: (plan, None))
    monkeypatch.setattr("src.vectl.driver.loop.save_plan", lambda *_a, **_k: None)

    async def _create_worktree(*_a: object, **_k: object) -> Success[WorktreeBinding]:
        return Success(
            WorktreeBinding(
                step_id=step_id,
                worktree_path=tmp_path / ".vectl" / "worktrees" / step_id,
                branch_name=f"vectl/step-{step_id}",
                reused_existing=False,
            )
        )

    monkeypatch.setattr("src.vectl.driver.loop.create_worktree", _create_worktree)
    monkeypatch.setattr("src.vectl.driver.loop.render_prompt", lambda **_k: "prompt")

    await handle_dispatch(
        action=Action(
            action="claim_and_dispatch",
            step_id=step_id,
            agent="python-executor",
            session="reuse",
            task_id="decide-session",
        ),
        state=state,
        config=_driver_config(plan_path),
        runners=cast(dict[str, Any], {"opencode": _Runner()}),
        judge=cast(Any, _NoopJudge()),
        session_pool=session_pool,
        observer=observer,
        plan_path=plan_path,
    )

    assert any(event == "CONTINUITY_DOUBLE_TRUTH_DETECTED" for event, _ in observer.events)


@pytest.mark.anyio
async def test_reconcile_success_persists_durable_ledger_for_restart_resume(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Successful reconcile must persist durable resume authority.

    Source: docs/DRIVER-CONTINUITY-FOUNDATION.md §4 (Restart/Resume), §10.
    """

    step_id = "core.impl"
    plan_path = tmp_path / "plan.yaml"
    observer = _RecordingObserver()

    monkeypatch.setattr(
        "src.vectl.driver.loop.load_plan_definition",
        lambda _p: (_FakePlan(_FakeStep(id=step_id)), "h"),
    )
    monkeypatch.setattr("src.vectl.driver.loop.complete_step", lambda plan, *_a, **_k: plan)
    monkeypatch.setattr("src.vectl.driver.loop.save_plan", lambda *_a, **_k: None)

    async def _merge(**_k: object) -> Success[MergeResult]:
        return Success(MergeResult(outcome=MergeOutcome.CLEAN_MERGE))

    async def _cleanup_worktree(*_a: object, **_k: object) -> Success[None]:
        return Success(None)

    monkeypatch.setattr("src.vectl.driver.loop.merge", _merge)
    monkeypatch.setattr("src.vectl.driver.loop.cleanup_worktree", _cleanup_worktree)

    await reconcile(
        completed=CompletedEntry(
            step_id=step_id,
            agent="python-executor",
            runner_name="opencode",
            result=RunnerResult(
                status=RunnerStatus.SUCCESS,
                session_id="session-123",
                output="evidence",
                elapsed_seconds=0.2,
                exit_code=0,
            ),
            worktree_path=str(tmp_path / ".vectl" / "worktrees" / step_id),
            elapsed_seconds=0.2,
        ),
        state=DriverState(),
        judge=cast(Any, _NoopJudge()),
        session_pool=SessionPool(SessionConfig(reuse_ttl=300)),
        observer=observer,
        plan_path=plan_path,
    )

    ledger_path = tmp_path / ".vectl" / "continuity" / "ledger" / f"{step_id}.json"
    assert ledger_path.exists(), "missing durable continuity ledger write for success path"


@pytest.mark.anyio
async def test_reconcile_failure_writes_recovery_grade_journal_fields(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Failure reconcile must persist minimum recovery telemetry.

    Source: docs/DRIVER-CONTINUITY-FOUNDATION.md §3, §4 (Abort/failure), §7.
    """

    step_id = "core.impl"
    plan_path = tmp_path / "plan.yaml"
    monkeypatch.setattr(
        "src.vectl.driver.loop.load_plan_definition",
        lambda _p: (_FakePlan(_FakeStep(id=step_id)), "h"),
    )
    monkeypatch.setattr("src.vectl.driver.loop.defer_step", lambda plan, *_a, **_k: plan)
    monkeypatch.setattr("src.vectl.driver.loop.save_plan", lambda *_a, **_k: None)

    await reconcile(
        completed=CompletedEntry(
            step_id=step_id,
            agent="python-executor",
            runner_name="opencode",
            result=RunnerResult(
                status=RunnerStatus.FAIL,
                session_id="session-123",
                output="runner failed with stack trace",
                elapsed_seconds=1.2,
                exit_code=1,
            ),
            worktree_path=str(tmp_path / ".vectl" / "worktrees" / step_id),
            elapsed_seconds=1.2,
        ),
        state=DriverState(),
        judge=cast(Any, _NoopJudge()),
        session_pool=SessionPool(SessionConfig(reuse_ttl=300)),
        observer=_RecordingObserver(),
        plan_path=plan_path,
    )

    journal_path = tmp_path / ".vectl" / "continuity" / "journal" / f"{step_id}.jsonl"
    assert journal_path.exists(), "missing failure journal write for recovery classification"


@pytest.mark.anyio
async def test_shutdown_persists_abort_or_crash_adjacent_continuity_record(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Shutdown with running work must leave a durable crash-adjacent continuity record.

    Source: docs/DRIVER-CONTINUITY-FOUNDATION.md §4 (Abort/failure), §7.
    """

    class _AliveHandle:
        session_id: str | None = "session-live"
        pid: int | None = 42

        async def wait(self, timeout: float | None = None) -> RunnerResult:
            return RunnerResult(
                status=RunnerStatus.STALL,
                session_id=self.session_id,
                output="still running",
                elapsed_seconds=0.1,
                exit_code=124,
            )

        async def kill(self) -> None:
            return

        def is_alive(self) -> bool:
            return True

    async def _cleanup_worktree(*_a: object, **_k: object) -> Success[None]:
        return Success(None)

    monkeypatch.setattr("src.vectl.driver.loop.cleanup_worktree", _cleanup_worktree)

    state = DriverState()
    state.register(
        step_id="core.impl",
        agent="python-executor",
        runner_name="opencode",
        handle=cast(Any, _AliveHandle()),
        worktree_path=str(tmp_path / ".vectl" / "worktrees" / "core.impl"),
    )

    await shutdown(state=state, observer=_RecordingObserver())

    abort_journal_path = tmp_path / ".vectl" / "continuity" / "journal" / "core.impl.jsonl"
    assert abort_journal_path.exists(), "missing crash-adjacent/abort journal write on shutdown"


@pytest.mark.anyio
async def test_startup_recovery_blocks_main_loop_when_plan_claims_ledger_mismatch(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Startup recovery must validate plan/claims consistency against continuity records.

    Source: docs/DRIVER-CONTINUITY-FOUNDATION.md §4 (Restart), §6, §10.
    """

    plan_path = tmp_path / "plan.yaml"
    continuity_dir = tmp_path / ".vectl" / "continuity" / "ledger"
    continuity_dir.mkdir(parents=True, exist_ok=True)
    (continuity_dir / "orphan.step.json").write_text(
        '{"step_id":"orphan.step","status":"success"}', encoding="utf-8"
    )

    config = _driver_config(plan_path)
    main_loop_called = {"called": False}

    class _RepairResult:
        actions: tuple[object, ...] = ()

    monkeypatch.setattr("src.vectl.driver.loop._load_runtime_config", lambda _p: config)
    monkeypatch.setattr("src.vectl.driver.loop.create_observer", lambda _cfg: _RecordingObserver())
    monkeypatch.setattr("src.vectl.driver.loop.create_runner", lambda _n, _cfg: _Runner())
    monkeypatch.setattr("src.vectl.driver.loop.Judge", lambda *_a, **_k: _NoopJudge())
    monkeypatch.setattr(
        "src.vectl.driver.loop.load_plan_definition",
        lambda _p: (_FakePlan(_FakeStep(id="core.impl")), "h"),
    )
    monkeypatch.setattr("src.vectl.driver.loop.repair_claims", lambda *_a, **_k: _RepairResult())
    monkeypatch.setattr("src.vectl.driver.loop._cleanup_orphan_worktrees", lambda **_k: ())

    async def _mark_called(**_k: object) -> None:
        main_loop_called["called"] = True

    monkeypatch.setattr("src.vectl.driver.loop._run_main_loop", _mark_called)

    await run(tmp_path / "driver.yaml")

    assert main_loop_called["called"] is False, (
        "startup should halt when recovered continuity ledger diverges from plan/claims"
    )
