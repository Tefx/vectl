"""Runtime integration tests for judge failure recovery policy wiring.

Source:
- docs/DRIVER-ARCHITECTURE.md Section 2.10 (judge extraction failures and
  failure-path branching)
- docs/DRIVER-CONTINUITY-FOUNDATION.md Section 4 and Section 7
  (judge outcome handoff consumed by continuity policy without taking durable
  resume/restart ownership)
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

from src.vectl.driver.config import SessionConfig
from src.vectl.driver.errors import JudgmentParseError, JudgmentTimeoutError
from src.vectl.driver.loop import reconcile
from src.vectl.driver.session import SessionPool
from src.vectl.driver.types import CompletedEntry, DriverState, RunnerResult, RunnerStatus


@dataclass
class _FakeStep:
    id: str
    description: str = "implementation"
    verification: str = "pytest -q"


@dataclass
class _FakePhase:
    id: str
    gate: str
    steps: list[_FakeStep]


class _FakePlan:
    def __init__(self, step: _FakeStep) -> None:
        self.context = "policy-test"
        self._step = step
        self.phases = [_FakePhase(id="core", gate="", steps=[step])]

    def find_step(self, step_id: str) -> tuple[_FakePhase, _FakeStep] | None:
        if step_id != self._step.id:
            return None
        return self.phases[0], self._step


class _Observer:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, object]]] = []

    def emit(self, event_type: str, /, **data: object) -> None:
        self.events.append((event_type, data))

    def close(self) -> None:
        return None


class _JudgeTimeout:
    def is_enabled(self, _judgment_type: object) -> bool:
        return True

    async def judge(self, _request: object) -> object:
        raise JudgmentTimeoutError(
            judgment_type="failure",
            step_id="core.impl",
            timeout_seconds=60,
        )


class _JudgeParseError:
    def __init__(self, raw_output: str) -> None:
        self._raw_output = raw_output

    def is_enabled(self, _judgment_type: object) -> bool:
        return True

    async def judge(self, _request: object) -> object:
        raise JudgmentParseError(
            judgment_type="failure",
            step_id="core.impl",
            raw_output=self._raw_output,
        )


def _failed_completed_entry(tmp_path: Path) -> CompletedEntry:
    return CompletedEntry(
        step_id="core.impl",
        agent="python-executor",
        runner_name="opencode",
        result=RunnerResult(
            status=RunnerStatus.FAIL,
            session_id="session-1",
            output="runner failure output",
            elapsed_seconds=1.2,
            exit_code=1,
        ),
        worktree_path=str(tmp_path / ".vectl" / "worktrees" / "core.impl"),
        elapsed_seconds=1.2,
    )


@pytest.mark.anyio
async def test_reconcile_failure_timeout_uses_retry_policy_and_defers(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    step = _FakeStep(id="core.impl")
    plan = _FakePlan(step)
    observer = _Observer()
    saved: list[object] = []

    monkeypatch.setattr(
        "src.vectl.driver.loop.load_plan_definition",
        lambda _path: (plan, "hash"),
    )
    monkeypatch.setattr("src.vectl.driver.loop.defer_step", lambda current, *_a, **_k: current)
    monkeypatch.setattr(
        "src.vectl.driver.loop.save_plan",
        lambda current, **_k: saved.append(current),
    )

    await reconcile(
        completed=_failed_completed_entry(tmp_path),
        state=DriverState(),
        judge=cast(Any, _JudgeTimeout()),
        session_pool=SessionPool(SessionConfig(reuse_ttl=300)),
        observer=observer,
        plan_path=tmp_path / "plan.yaml",
    )

    policy_events = [
        payload for event, payload in observer.events if event == "JUDGE_FAILURE_POLICY"
    ]
    assert len(policy_events) == 1
    assert policy_events[0]["action"] == "retry"
    assert saved, "reconcile should defer/save after retry-policy classification"


@pytest.mark.anyio
async def test_reconcile_failure_parse_error_uses_fallback_policy(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    step = _FakeStep(id="core.impl")
    plan = _FakePlan(step)
    observer = _Observer()
    state = DriverState()
    state.decide_state.failure_counts["core.impl"] = 2
    state.runtime_config = SimpleNamespace(fallback_runner="codex")

    monkeypatch.setattr(
        "src.vectl.driver.loop.load_plan_definition",
        lambda _path: (plan, "hash"),
    )
    monkeypatch.setattr("src.vectl.driver.loop.defer_step", lambda current, *_a, **_k: current)
    monkeypatch.setattr("src.vectl.driver.loop.save_plan", lambda *_a, **_k: None)

    await reconcile(
        completed=_failed_completed_entry(tmp_path),
        state=state,
        judge=cast(Any, _JudgeParseError(raw_output="not-json-output")),
        session_pool=SessionPool(SessionConfig(reuse_ttl=300)),
        observer=observer,
        plan_path=tmp_path / "plan.yaml",
    )

    policy_events = [
        payload for event, payload in observer.events if event == "JUDGE_FAILURE_POLICY"
    ]
    assert len(policy_events) == 1
    assert policy_events[0]["action"] == "fallback"
    assert state.runner_failures[("core.impl", "opencode")] >= 2


@pytest.mark.anyio
async def test_reconcile_failure_empty_output_no_fallback_halts(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    step = _FakeStep(id="core.impl")
    plan = _FakePlan(step)
    observer = _Observer()
    state = DriverState()
    state.decide_state.failure_counts["core.impl"] = 2

    monkeypatch.setattr(
        "src.vectl.driver.loop.load_plan_definition",
        lambda _path: (plan, "hash"),
    )
    monkeypatch.setattr("src.vectl.driver.loop.defer_step", lambda current, *_a, **_k: current)
    monkeypatch.setattr("src.vectl.driver.loop.save_plan", lambda *_a, **_k: None)

    await reconcile(
        completed=_failed_completed_entry(tmp_path),
        state=state,
        judge=cast(Any, _JudgeParseError(raw_output="empty output from judge runner")),
        session_pool=SessionPool(SessionConfig(reuse_ttl=300)),
        observer=observer,
        plan_path=tmp_path / "plan.yaml",
    )

    policy_events = [
        payload for event, payload in observer.events if event == "JUDGE_FAILURE_POLICY"
    ]
    assert len(policy_events) == 1
    assert policy_events[0]["action"] == "halt"
    assert state.halt_requested is True
