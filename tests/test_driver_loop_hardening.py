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
from src.vectl.driver.judgments import JudgmentRequest, JudgmentType, JudgmentVerdict
from src.vectl.driver.loop import (
    PlannerDispatchRequest,
    _build_anomaly_request,
    _cleanup_orphan_worktrees,
    _run_main_loop,
    dispatch_planner,
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
from vectl.models import Action, DecideOutput, PlanError


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


@pytest.mark.anyio
async def test_handle_dispatch_preflight_replan_invokes_planner_and_skips_claim(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _base_config()
    config.judge.preflight = True
    state = DriverState()
    observer = _RecordingObserver()
    session_pool = SessionPool(config.session)

    class _PreflightJudge:
        async def judge(self, request: object) -> JudgmentVerdict:
            return JudgmentVerdict(
                verdict="REPLAN",
                reason="Spec too risky without decomposition",
                planner_instruction="Split migration into prep + apply + verify",
            )

    dispatch_calls: list[PlannerDispatchRequest] = []

    async def _dispatch_planner(
        request: PlannerDispatchRequest,
        *,
        config: DriverConfig,
        runners: dict[str, object],
        observer: object,
        plan_path: Path,
    ) -> None:
        dispatch_calls.append(request)

    def _claim_step(*_a: object, **_k: object) -> object:
        raise AssertionError("claim_step must not run when preflight verdict is REPLAN")

    monkeypatch.setattr("src.vectl.driver.loop.load_plan_definition", lambda _p: (_FakePlan(), "h"))
    monkeypatch.setattr("src.vectl.driver.loop.claim_step", _claim_step)
    monkeypatch.setattr("src.vectl.driver.loop.dispatch_planner", _dispatch_planner)

    await handle_dispatch(
        action=Action(
            action="claim_and_dispatch",
            step_id="core.impl",
            agent="python-executor",
            step_description="Perform migration with CAS checks",
            step_verification="Prove backward compatibility for migration path",
            step_refs=["docs/migration.md"],
        ),
        state=state,
        config=config,
        runners=cast(
            dict[str, Any], {"claude": _Runner("claude"), "opencode": _Runner("opencode")}
        ),
        judge=cast(Any, _PreflightJudge()),
        session_pool=session_pool,
        observer=observer,
        plan_path=Path("plan.yaml"),
    )

    assert len(dispatch_calls) == 1
    request = dispatch_calls[0]
    assert request.step_id == "core.impl"
    assert request.trigger == "handle_dispatch.preflight_replan"
    assert request.judgment_type == "PREFLIGHT"
    assert request.planner_instruction == "Split migration into prep + apply + verify"


@pytest.mark.anyio
async def test_handle_dispatch_preflight_replan_requires_planner_instruction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _base_config()
    config.judge.preflight = True

    class _BrokenPreflightJudge:
        async def judge(self, request: object) -> JudgmentVerdict:
            return JudgmentVerdict(
                verdict="REPLAN",
                reason="Spec ambiguous",
                planner_instruction=None,
            )

    monkeypatch.setattr("src.vectl.driver.loop.load_plan_definition", lambda _p: (_FakePlan(), "h"))

    with pytest.raises(PlanError, match="planner_instruction"):
        await handle_dispatch(
            action=Action(
                action="claim_and_dispatch",
                step_id="core.impl",
                agent="python-executor",
                step_description="CAS migration",
                step_verification="Backward compatibility",
            ),
            state=DriverState(),
            config=config,
            runners=cast(
                dict[str, Any], {"claude": _Runner("claude"), "opencode": _Runner("opencode")}
            ),
            judge=cast(Any, _BrokenPreflightJudge()),
            session_pool=SessionPool(config.session),
            observer=_RecordingObserver(),
            plan_path=Path("plan.yaml"),
        )


@pytest.mark.anyio
async def test_dispatch_planner_raises_on_runner_failure() -> None:
    class _FailingRunner:
        name = "opencode"

        async def dispatch(
            self,
            prompt: str,
            agent: str,
            workdir: str,
            session_id: str | None = None,
        ) -> Any:
            class _Handle:
                session_id = None
                pid = 321

                async def wait(self, timeout: float | None = None) -> RunnerResult:
                    return RunnerResult(
                        status=RunnerStatus.FAIL,
                        session_id=None,
                        output="planner crashed",
                        elapsed_seconds=0.2,
                        exit_code=1,
                    )

                async def kill(self) -> None:
                    return

                def is_alive(self) -> bool:
                    return False

            return _Handle()

    config = _base_config()
    observer = _RecordingObserver()
    request = PlannerDispatchRequest(
        step_id="core.impl",
        trigger="handle_dispatch.preflight_replan",
        judgment_type="PREFLIGHT",
        planner_instruction="Split implementation",
    )

    with pytest.raises(RunnerError, match="planner dispatch failed"):
        await dispatch_planner(
            request,
            config=config,
            runners=cast(
                dict[str, Any], {"opencode": _FailingRunner(), "claude": _Runner("claude")}
            ),
            observer=observer,
            plan_path=Path("plan.yaml"),
        )

    assert any(event == "PLANNER_DISPATCH_FAILED" for event, _ in observer.events)


@pytest.mark.anyio
async def test_dispatch_planner_requires_non_empty_instruction() -> None:
    config = _base_config()
    observer = _RecordingObserver()
    request = PlannerDispatchRequest(
        step_id="core.impl",
        trigger="handle_dispatch.preflight_replan",
        judgment_type="PREFLIGHT",
        planner_instruction="   ",
    )

    with pytest.raises(PlanError, match="non-empty planner_instruction"):
        await dispatch_planner(
            request,
            config=config,
            runners=cast(
                dict[str, Any], {"opencode": _Runner("opencode"), "claude": _Runner("claude")}
            ),
            observer=observer,
            plan_path=Path("plan.yaml"),
        )


@pytest.mark.anyio
async def test_reconcile_gate_reject_batches_all_blockers_for_planner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = DriverState()
    observer = _RecordingObserver()
    pool = SessionPool(SessionConfig(reuse_ttl=300))
    config = _base_config()
    completed = CompletedEntry(
        step_id="quality.gate",
        agent="python-executor",
        runner_name="opencode",
        result=RunnerResult(
            status=RunnerStatus.SUCCESS,
            session_id="ses-gate",
            output=(
                '{"issues": ['
                '{"severity": "blocker", "summary": "missing branch coverage"},'
                '{"severity": "should_fix", "summary": "flaky integration test"},'
                '{"severity": "blocker", "summary": "unsafe migration ordering"}]}'
            ),
            elapsed_seconds=1.0,
            exit_code=0,
        ),
        worktree_path=".vectl/worktrees/quality.gate",
        elapsed_seconds=1.0,
    )

    class _GateJudge:
        def is_enabled(self, judgment_type: JudgmentType) -> bool:
            return judgment_type == JudgmentType.GATE

        async def judge(self, request: object) -> JudgmentVerdict:
            assert isinstance(request, object)
            return JudgmentVerdict(
                verdict="REJECT",
                reason="Blockers remain",
                planner_instruction="Create remediation chain",
            )

    dispatch_requests: list[PlannerDispatchRequest] = []

    async def _dispatch_planner(
        request: PlannerDispatchRequest,
        *,
        config: DriverConfig,
        runners: dict[str, object],
        observer: object,
        plan_path: Path,
    ) -> None:
        dispatch_requests.append(request)

    monkeypatch.setattr("src.vectl.driver.loop.load_plan_definition", lambda _p: (_FakePlan(), "h"))
    monkeypatch.setattr("src.vectl.driver.loop.complete_step", lambda plan, *_a, **_k: plan)
    monkeypatch.setattr("src.vectl.driver.loop.save_plan", lambda *_a, **_k: None)
    monkeypatch.setattr("src.vectl.driver.loop.defer_step", lambda plan, *_a, **_k: plan)
    monkeypatch.setattr("src.vectl.driver.loop.dispatch_planner", _dispatch_planner)

    state.runtime_config = config
    state.runtime_runners = cast(
        dict[str, Any], {"opencode": _Runner("opencode"), "claude": _Runner("claude")}
    )

    await reconcile(
        completed=completed,
        state=state,
        judge=cast(Any, _GateJudge()),
        session_pool=pool,
        observer=observer,
        plan_path=Path("plan.yaml"),
    )

    assert len(dispatch_requests) == 1
    instruction = dispatch_requests[0].planner_instruction
    assert "missing branch coverage" in instruction
    assert "flaky integration test" in instruction
    assert "unsafe migration ordering" in instruction


@pytest.mark.anyio
async def test_reconcile_failure_classification_uses_remaining_gates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = DriverState()
    observer = _RecordingObserver()
    pool = SessionPool(SessionConfig(reuse_ttl=300))
    config = _base_config()
    completed = CompletedEntry(
        step_id="core.impl",
        agent="python-executor",
        runner_name="opencode",
        result=RunnerResult(
            status=RunnerStatus.FAIL,
            session_id="ses-fail",
            output="integration check failed",
            elapsed_seconds=1.0,
            exit_code=1,
        ),
        worktree_path=".vectl/worktrees/core.impl",
        elapsed_seconds=1.0,
    )

    requests: list[object] = []
    dispatch_requests: list[PlannerDispatchRequest] = []

    class _FailureJudge:
        def is_enabled(self, judgment_type: JudgmentType) -> bool:
            return judgment_type == JudgmentType.FAILURE

        async def judge(self, request: object) -> JudgmentVerdict:
            requests.append(request)
            return JudgmentVerdict(
                verdict="ACCEPT",
                reason="provenance=introduced_now, disposition=downstream_blocker",
                planner_instruction="Add remediation before downstream gate",
            )

    async def _dispatch_planner(
        request: PlannerDispatchRequest,
        *,
        config: DriverConfig,
        runners: dict[str, object],
        observer: object,
        plan_path: Path,
    ) -> None:
        dispatch_requests.append(request)

    class _StepObj:
        def __init__(self, step_id: str, description: str = "desc") -> None:
            self.id = step_id
            self.description = description

    class _PhaseObj:
        def __init__(self, phase_id: str, gate: str, steps: list[_StepObj]) -> None:
            self.id = phase_id
            self.gate = gate
            self.steps = steps

    class _PlanWithRemainingChecks:
        def __init__(self) -> None:
            self.context = "ctx"
            first = _StepObj("core.impl", "desc")
            second = _StepObj("quality.gate", "gate step")
            self.phases = [_PhaseObj("core", "must pass core gate", [first, second])]

        def find_step(self, step_id: str) -> tuple[object, object] | None:
            for phase in self.phases:
                for step in phase.steps:
                    if step.id == step_id:
                        return (phase, step)
            return None

    monkeypatch.setattr(
        "src.vectl.driver.loop.load_plan_definition", lambda _p: (_PlanWithRemainingChecks(), "h")
    )
    monkeypatch.setattr("src.vectl.driver.loop.save_plan", lambda *_a, **_k: None)
    monkeypatch.setattr("src.vectl.driver.loop.defer_step", lambda plan, *_a, **_k: plan)
    monkeypatch.setattr("src.vectl.driver.loop.dispatch_planner", _dispatch_planner)

    state.runtime_config = config
    state.runtime_runners = cast(
        dict[str, Any], {"opencode": _Runner("opencode"), "claude": _Runner("claude")}
    )

    await reconcile(
        completed=completed,
        state=state,
        judge=cast(Any, _FailureJudge()),
        session_pool=pool,
        observer=observer,
        plan_path=Path("plan.yaml"),
    )

    assert requests
    failure_request = requests[0]
    assert isinstance(failure_request, JudgmentRequest)
    context = failure_request.context
    assert "remaining_gates" in context
    assert context["remaining_gates"]
    assert len(dispatch_requests) == 1


@pytest.mark.anyio
async def test_handle_dispatch_cold_context_includes_required_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _base_config()
    config.judge.cold_context = True
    config.agent_routing["python-executor"] = "opencode"
    state = DriverState()
    observer = _RecordingObserver()
    session_pool = SessionPool(config.session)

    class _ColdContextJudge:
        def __init__(self) -> None:
            self.requests: list[object] = []

        def is_enabled(self, judgment_type: JudgmentType) -> bool:
            return judgment_type == JudgmentType.COLD_CONTEXT

        async def judge(self, request: object) -> JudgmentVerdict:
            self.requests.append(request)
            return JudgmentVerdict(verdict="ACCEPT", reason="include objective artifacts only")

    judge = _ColdContextJudge()

    monkeypatch.setattr("src.vectl.driver.loop.load_plan_definition", lambda _p: (_FakePlan(), "h"))
    monkeypatch.setattr("src.vectl.driver.loop.claim_step", lambda plan, *_a, **_k: (plan, None))
    monkeypatch.setattr("src.vectl.driver.loop.save_plan", lambda *_a, **_k: None)

    async def _create_worktree(*_a: object, **_k: object) -> Success[WorktreeBinding]:
        return Success(
            WorktreeBinding(
                step_id="quality.gate",
                worktree_path=Path(".vectl/worktrees/quality.gate"),
                branch_name="vectl/step-quality.gate",
                reused_existing=False,
            )
        )

    monkeypatch.setattr("src.vectl.driver.loop.create_worktree", _create_worktree)
    monkeypatch.setattr("src.vectl.driver.loop.render_prompt", lambda **_k: "prompt")

    await handle_dispatch(
        action=Action(
            action="claim_and_dispatch",
            step_id="quality.gate",
            agent="python-executor",
            step_description="Run independent gate audit",
            step_verification="Run: uv run pytest -q",
        ),
        state=state,
        config=config,
        runners=cast(
            dict[str, Any], {"opencode": _Runner("opencode"), "claude": _Runner("claude")}
        ),
        judge=cast(Any, judge),
        session_pool=session_pool,
        observer=observer,
        plan_path=Path("plan.yaml"),
    )

    assert judge.requests
    request = judge.requests[0]
    assert isinstance(request, JudgmentRequest)
    context = request.context
    assert context["step_id"] == "quality.gate"
    assert "step_spec" in context
    assert "diff" in context


def test_build_anomaly_request_includes_required_sections() -> None:
    request = _build_anomaly_request(
        anomaly_type="orphan_worktrees",
        repair_scope="removed orphan directories",
        dry_run_recommendation="safe_to_apply",
    )
    assert request.context["anomaly_type"] == "orphan_worktrees"
    assert request.context["repair_scope"] == "removed orphan directories"


@pytest.mark.anyio
async def test_main_loop_does_not_feed_completed_results_into_decide(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = DriverState()
    observer = _RecordingObserver()
    config = _base_config()
    captured: dict[str, object] = {}

    state.completed_queue.append(
        CompletedEntry(
            step_id="core.impl",
            agent="python-executor",
            runner_name="opencode",
            result=RunnerResult(
                status=RunnerStatus.SUCCESS,
                session_id="ses-1",
                output="ok",
                elapsed_seconds=0.2,
                exit_code=0,
            ),
            worktree_path=".vectl/worktrees/core.impl",
            elapsed_seconds=0.2,
        )
    )

    def _decide(**kwargs: object) -> DecideOutput:
        captured["completed_results"] = kwargs.get("completed_results")
        return DecideOutput(actions=[], continuation=False, decision_log=[])

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

    assert captured["completed_results"] is None
    assert len(state.completed_queue) == 1


@pytest.mark.anyio
async def test_main_loop_ignores_complete_action_branch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = DriverState()
    observer = _RecordingObserver()
    config = _base_config()

    def _decide(**_k: object) -> DecideOutput:
        return DecideOutput(
            actions=[Action(action="complete", step_id="core.impl", evidence="legacy")],
            continuation=False,
            decision_log=[],
        )

    async def _should_not_call_handle_complete(**_k: object) -> None:
        raise AssertionError("handle_complete must not be called from main runtime loop")

    monkeypatch.setattr("src.vectl.driver.loop.decide", _decide)
    monkeypatch.setattr("src.vectl.driver.loop.handle_complete", _should_not_call_handle_complete)

    await _run_main_loop(
        state=state,
        config=config,
        runners={},
        judge=_fake_judge(),
        session_pool=SessionPool(config.session),
        observer=observer,
        plan_path=Path("plan.yaml"),
    )

    assert any(event == "COMPLETE_ACTION_IGNORED" for event, _ in observer.events)


@pytest.mark.anyio
async def test_reconcile_success_side_effects_fire_once(monkeypatch: pytest.MonkeyPatch) -> None:
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

    calls: dict[str, int] = {
        "complete_step": 0,
        "merge": 0,
        "cleanup": 0,
        "session_record": 0,
    }

    monkeypatch.setattr("src.vectl.driver.loop.load_plan_definition", lambda _p: (_FakePlan(), "h"))
    monkeypatch.setattr("src.vectl.driver.loop.save_plan", lambda *_a, **_k: None)

    def _complete_step(plan: object, *_a: object, **_k: object) -> object:
        calls["complete_step"] += 1
        return plan

    async def _merge(**_k: object) -> Success[MergeResult]:
        calls["merge"] += 1
        return Success(MergeResult(outcome=MergeOutcome.CLEAN_MERGE, conflicted_files=()))

    async def _cleanup(*_a: object, **_k: object) -> Success[None]:
        calls["cleanup"] += 1
        return Success(None)

    def _record_session(*_a: object, **_k: object) -> None:
        calls["session_record"] += 1

    monkeypatch.setattr("src.vectl.driver.loop.complete_step", _complete_step)
    monkeypatch.setattr("src.vectl.driver.loop.merge", _merge)
    monkeypatch.setattr("src.vectl.driver.loop.cleanup_worktree", _cleanup)
    monkeypatch.setattr(pool, "record", _record_session)

    await reconcile(
        completed=completed,
        state=state,
        judge=_fake_judge(),
        session_pool=pool,
        observer=observer,
        plan_path=Path("plan.yaml"),
    )

    assert calls["complete_step"] == 1
    assert calls["merge"] == 1
    assert calls["cleanup"] == 1
    assert calls["session_record"] == 1
    assert sum(1 for event, _ in observer.events if event == "STEP_COMPLETED") == 1
    assert sum(1 for event, _ in observer.events if event == "MERGE_COMPLETED") == 1


@pytest.mark.anyio
async def test_reconcile_failure_threshold_escalates_once(monkeypatch: pytest.MonkeyPatch) -> None:
    state = DriverState()
    observer = _RecordingObserver()
    state.failure_counts["core.impl"] = 2
    completed = CompletedEntry(
        step_id="core.impl",
        agent="python-executor",
        runner_name="opencode",
        result=RunnerResult(
            status=RunnerStatus.FAIL,
            session_id=None,
            output="boom",
            elapsed_seconds=1.0,
            exit_code=1,
        ),
        worktree_path=".vectl/worktrees/core.impl",
        elapsed_seconds=1.0,
    )

    monkeypatch.setattr("src.vectl.driver.loop.load_plan_definition", lambda _p: (_FakePlan(), "h"))
    monkeypatch.setattr("src.vectl.driver.loop.save_plan", lambda *_a, **_k: None)

    class _EscalationJudge:
        async def judge(self, request: object) -> JudgmentVerdict:
            return JudgmentVerdict(verdict="DEFER", reason="manual follow-up")

    await reconcile(
        completed=completed,
        state=state,
        judge=cast(Any, _EscalationJudge()),
        session_pool=SessionPool(SessionConfig(reuse_ttl=300)),
        observer=observer,
        plan_path=Path("plan.yaml"),
    )

    assert state.failure_count("core.impl") == 3
    assert sum(1 for event, _ in observer.events if event == "ESCALATION_VERDICT") == 1
    assert sum(1 for event, _ in observer.events if event == "ESCALATION_DEFERRED") == 1
