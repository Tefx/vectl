from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from unittest.mock import MagicMock

import pytest

from src.vectl.driver.config import (
    DriverConfig,
    JudgeConfig,
    ObservabilityConfig,
    OrchestrationConfig,
    RunnerConfig,
    SessionConfig,
)
from src.vectl.driver.errors import RunnerError
from src.vectl.driver.loop import (
    _cleanup_orphan_worktrees,
    _run_main_loop,
    handle_dispatch,
    reconcile,
)
from src.vectl.driver.session import SessionPool
from src.vectl.driver.types import CompletedEntry, DriverState, RunnerResult, RunnerStatus
from src.vectl.driver.worktree import (
    ConflictResolverDispatch,
    MergeOutcome,
    MergeResult,
    Success,
    WorktreeBinding,
)
from vectl.models import Action, DecideOutput


@dataclass
class _FakeStep:
    description: str = "desc"
    verification: str = "verify"
    refs: list[str] | None = None
    depends_on: list[str] | None = None

    def __post_init__(self) -> None:
        if self.refs is None:
            self.refs = []
        if self.depends_on is None:
            self.depends_on = []


class _FakePlan:
    def __init__(self, depends_on: list[str] | None = None) -> None:
        self.context = "ctx"
        self._step = _FakeStep(depends_on=depends_on or [])

    def find_step(self, step_id: str) -> tuple[object, _FakeStep] | None:
        return (object(), self._step)


class _RecordingObserver:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, object]]] = []

    def emit(self, event_type: str, /, **data: object) -> None:
        self.events.append((event_type, data))

    def close(self) -> None:
        return


def _base_config() -> DriverConfig:
    return DriverConfig(
        plan_path="plan.yaml",
        runners={
            "claude": RunnerConfig(command="claude", args=["-p"], output_parser="claude_json"),
            "opencode": RunnerConfig(
                command="opencode",
                args=["run", "--format", "json"],
                output_parser="opencode_jsonl",
            ),
        },
        agent_routing={"python-executor": "claude"},
        fallback_runner="opencode",
        orchestration=OrchestrationConfig(max_parallelism=2),
        session=SessionConfig(reuse_ttl=300),
        judge=JudgeConfig(preflight=False),
        observability=ObservabilityConfig(),
    )


class _Runner:
    def __init__(self, name: str) -> None:
        self.name = name
        self.calls: list[dict[str, object]] = []

    async def dispatch(
        self,
        prompt: str,
        agent: str,
        workdir: str,
        session_id: str | None = None,
    ) -> Any:
        self.calls.append(
            {
                "prompt": prompt,
                "agent": agent,
                "workdir": workdir,
                "session_id": session_id,
            }
        )

        local_session_id = session_id

        class _Handle:
            pid = 1
            session_id = local_session_id

            async def wait(self, timeout: float | None = None) -> RunnerResult:
                return RunnerResult(
                    status=RunnerStatus.SUCCESS,
                    session_id=session_id,
                    output="ok",
                    elapsed_seconds=0.1,
                    exit_code=0,
                )

            async def kill(self) -> None:
                return

            def is_alive(self) -> bool:
                return False

        return _Handle()


def _fake_judge() -> Any:
    return MagicMock()


@pytest.mark.anyio
async def test_handle_dispatch_runner_fallback_after_repeated_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _base_config()
    state = DriverState()
    observer = _RecordingObserver()
    session_pool = SessionPool(config.session)
    state.runner_failures[("core.impl", "claude")] = 2

    claude = _Runner("claude")
    opencode = _Runner("opencode")
    runners = {"claude": claude, "opencode": opencode}

    monkeypatch.setattr("src.vectl.driver.loop.load_plan_definition", lambda _p: (_FakePlan(), "h"))
    monkeypatch.setattr("src.vectl.driver.loop.claim_step", lambda plan, *_a, **_k: (plan, None))
    monkeypatch.setattr("src.vectl.driver.loop.save_plan", lambda *_a, **_k: None)

    async def _create_worktree(*_a: object, **_k: object) -> Success[WorktreeBinding]:
        return Success(
            WorktreeBinding(
                step_id="core.impl",
                worktree_path=Path(".vectl/worktrees/core.impl"),
                branch_name="vectl/step-core.impl",
                reused_existing=False,
            )
        )

    monkeypatch.setattr("src.vectl.driver.loop.create_worktree", _create_worktree)
    monkeypatch.setattr("src.vectl.driver.loop.render_prompt", lambda **_k: "prompt")

    await handle_dispatch(
        action=Action(action="claim_and_dispatch", step_id="core.impl", agent="python-executor"),
        state=state,
        config=config,
        runners=cast(dict[str, Any], runners),
        judge=_fake_judge(),
        session_pool=session_pool,
        observer=observer,
        plan_path=Path("plan.yaml"),
    )

    assert len(opencode.calls) == 1
    assert len(claude.calls) == 0
    assert any(e[0] == "RUNNER_FALLBACK" for e in observer.events)


@pytest.mark.anyio
async def test_handle_dispatch_stale_session_retries_fresh(monkeypatch: pytest.MonkeyPatch) -> None:
    config = _base_config()
    config.agent_routing["python-executor"] = "opencode"
    state = DriverState()
    observer = _RecordingObserver()
    session_pool = SessionPool(config.session)

    class _StaleThenFreshRunner(_Runner):
        async def dispatch(
            self, prompt: str, agent: str, workdir: str, session_id: str | None = None
        ) -> Any:
            self.calls.append({"session_id": session_id})
            if session_id is not None:
                raise RunnerError("opencode", "core.impl", "stale session")

            class _Handle:
                pid = 1
                session_id = None

                async def wait(self, timeout: float | None = None) -> RunnerResult:
                    return RunnerResult(
                        status=RunnerStatus.SUCCESS,
                        session_id=None,
                        output="ok",
                        elapsed_seconds=0.1,
                        exit_code=0,
                    )

                async def kill(self) -> None:
                    return

                def is_alive(self) -> bool:
                    return False

            return _Handle()

    runner = _StaleThenFreshRunner("opencode")

    monkeypatch.setattr(
        "src.vectl.driver.loop.load_plan_definition",
        lambda _p: (_FakePlan(depends_on=["dep.a"]), "h"),
    )
    monkeypatch.setattr("src.vectl.driver.loop.claim_step", lambda plan, *_a, **_k: (plan, None))
    monkeypatch.setattr("src.vectl.driver.loop.save_plan", lambda *_a, **_k: None)

    async def _create_worktree(*_a: object, **_k: object) -> Success[WorktreeBinding]:
        return Success(
            WorktreeBinding(
                step_id="core.impl",
                worktree_path=Path(".vectl/worktrees/core.impl"),
                branch_name="vectl/step-core.impl",
                reused_existing=False,
            )
        )

    monkeypatch.setattr("src.vectl.driver.loop.create_worktree", _create_worktree)
    monkeypatch.setattr("src.vectl.driver.loop.render_prompt", lambda **_k: "prompt")

    await handle_dispatch(
        action=Action(
            action="claim_and_dispatch",
            step_id="core.impl",
            agent="python-executor",
            session="reuse",
            task_id="ses-stale",
        ),
        state=state,
        config=config,
        runners=cast(dict[str, Any], {"opencode": runner, "claude": _Runner("claude")}),
        judge=_fake_judge(),
        session_pool=session_pool,
        observer=observer,
        plan_path=Path("plan.yaml"),
    )

    assert [c["session_id"] for c in runner.calls] == ["ses-stale", None]
    assert any(e[0] == "SESSION_REUSE_HIT" for e in observer.events)
    assert any(
        e[0] == "SESSION_REUSE_MISS" and e[1].get("reason") == "stale_or_unavailable_session"
        for e in observer.events
    )


@pytest.mark.anyio
async def test_reconcile_routes_non_trivial_conflict(monkeypatch: pytest.MonkeyPatch) -> None:
    state = DriverState()
    observer = _RecordingObserver()
    pool = SessionPool(SessionConfig(reuse_ttl=300))
    completed = CompletedEntry(
        step_id="core.impl",
        agent="python-executor",
        runner_name="opencode",
        result=RunnerResult(
            status=RunnerStatus.SUCCESS,
            session_id="ses-1",
            output="evidence",
            elapsed_seconds=1.0,
            exit_code=0,
        ),
        worktree_path=".vectl/worktrees/core.impl",
        elapsed_seconds=1.0,
    )

    calls: dict[str, int] = {"defer": 0, "cleanup": 0}

    monkeypatch.setattr("src.vectl.driver.loop.load_plan_definition", lambda _p: (_FakePlan(), "h"))
    monkeypatch.setattr("src.vectl.driver.loop.complete_step", lambda plan, *_a, **_k: plan)
    monkeypatch.setattr("src.vectl.driver.loop.save_plan", lambda *_a, **_k: None)

    async def _merge(**_k: object) -> Success[MergeResult]:
        return Success(
            MergeResult(
                outcome=MergeOutcome.NON_TRIVIAL_CONFLICT,
                conflicted_files=("src/x.py",),
                resolver_dispatch=ConflictResolverDispatch(
                    step_id="core.impl",
                    worktree_path=Path(".vectl/worktrees/core.impl"),
                    source_branch="vectl/step-core.impl",
                    target_branch="main",
                    conflicted_files=("src/x.py",),
                ),
            )
        )

    monkeypatch.setattr("src.vectl.driver.loop.merge", _merge)

    def _defer(plan: object, *_a: object, **_k: object) -> object:
        calls["defer"] += 1
        return plan

    async def _cleanup(*_a: object, **_k: object) -> Success[None]:
        calls["cleanup"] += 1
        return Success(None)

    monkeypatch.setattr("src.vectl.driver.loop.defer_step", _defer)
    monkeypatch.setattr("src.vectl.driver.loop.cleanup_worktree", _cleanup)

    await reconcile(
        completed=completed,
        state=state,
        judge=_fake_judge(),
        session_pool=pool,
        observer=observer,
        plan_path=Path("plan.yaml"),
    )

    assert calls["defer"] == 1
    assert calls["cleanup"] == 0
    assert any(e[0] == "MERGE_CONFLICTED" for e in observer.events)


def test_cleanup_orphan_worktrees_removes_unknown_dirs(tmp_path: Path) -> None:
    plan_path = tmp_path / "plan.yaml"
    plan_path.write_text("project: t\nphases: []\n", encoding="utf-8")
    base = tmp_path / ".vectl" / "worktrees"
    keep = base / "known.step"
    orphan = base / "orphan.step"
    keep.mkdir(parents=True)
    orphan.mkdir(parents=True)

    _cleanup_orphan_worktrees(plan_path=plan_path, plan_step_ids={"known.step"})

    assert keep.exists()
    assert not orphan.exists()


@pytest.mark.anyio
async def test_main_loop_halts_on_infinite_loop_signature(monkeypatch: pytest.MonkeyPatch) -> None:
    state = DriverState()
    observer = _RecordingObserver()
    config = _base_config()
    config.orchestration.max_parallelism = 1

    def _decide(**_k: object) -> DecideOutput:
        return DecideOutput(
            actions=[Action(action="wait", reason="idle")],
            continuation=True,
            decision_log=[],
        )

    monkeypatch.setattr("src.vectl.driver.loop.decide", _decide)

    await _run_main_loop(
        state=state,
        config=config,
        runners={},
        judge=_fake_judge(),
        session_pool=SessionPool(config.session),
        observer=observer,
        plan_path=Path("plan.yaml"),
    )

    assert state.halt_requested is True
    assert any(
        e[0] == "HALT" and e[1].get("reason") == "SUSPECTED_INFINITE_LOOP" for e in observer.events
    )


@pytest.mark.anyio
async def test_main_loop_all_stalled_recovery(monkeypatch: pytest.MonkeyPatch) -> None:
    state = DriverState()
    observer = _RecordingObserver()
    config = _base_config()

    class _DeadHandle:
        session_id: str | None = None
        pid: int | None = 99

        async def wait(self, timeout: float | None = None) -> RunnerResult:
            return RunnerResult(
                status=RunnerStatus.STALL,
                session_id=None,
                output="stall",
                elapsed_seconds=1.0,
                exit_code=124,
            )

        async def kill(self) -> None:
            return

        def is_alive(self) -> bool:
            return False

    state.register(
        "s1", "python-executor", "opencode", cast(Any, _DeadHandle()), ".vectl/worktrees/s1"
    )

    outputs = [
        DecideOutput(
            actions=[Action(action="wait", reason="running")], continuation=True, decision_log=[]
        ),
        DecideOutput(actions=[], continuation=False, decision_log=[]),
    ]

    def _decide(**_k: object) -> DecideOutput:
        return outputs.pop(0)

    async def _fake_wait_for_any() -> CompletedEntry:
        return CompletedEntry(
            step_id="s1",
            agent="python-executor",
            runner_name="opencode",
            result=RunnerResult(
                status=RunnerStatus.STALL,
                session_id=None,
                output="stall",
                elapsed_seconds=1.0,
                exit_code=124,
            ),
            worktree_path=".vectl/worktrees/s1",
            elapsed_seconds=1.0,
        )

    async def _noop_reconcile(**_k: object) -> None:
        return

    monkeypatch.setattr("src.vectl.driver.loop.decide", _decide)
    monkeypatch.setattr(state, "wait_for_any", _fake_wait_for_any)
    monkeypatch.setattr("src.vectl.driver.loop.reconcile", _noop_reconcile)

    await _run_main_loop(
        state=state,
        config=config,
        runners={},
        judge=_fake_judge(),
        session_pool=SessionPool(config.session),
        observer=observer,
        plan_path=Path("plan.yaml"),
    )

    assert state.running == {}
    assert any(e[0] == "RECOVERY" and e[1].get("type") == "all_stalled" for e in observer.events)
