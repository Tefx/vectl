"""Shared fixtures for continuity capability-safety expected-red tests."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from src.vectl.driver.config import (
    DriverConfig,
    JudgeConfig,
    ObservabilityConfig,
    OrchestrationConfig,
    RunnerConfig,
    SessionConfig,
)
from src.vectl.driver.types import RunnerResult, RunnerStatus


@dataclass
class FakeStep:
    """Plan step fixture used by loop dispatch tests."""

    description: str = "step description"
    verification: str = "step verification"
    refs: list[str] | None = None
    depends_on: list[str] | None = None

    def __post_init__(self) -> None:
        if self.refs is None:
            self.refs = []
        if self.depends_on is None:
            self.depends_on = []


class FakePlan:
    """Plan-like object with a single selectable step."""

    def __init__(self, *, depends_on: list[str] | None = None) -> None:
        self.context = "continuity-context"
        self._step = FakeStep(depends_on=depends_on or [])

    def find_step(self, step_id: str) -> tuple[object, FakeStep] | None:
        return (object(), self._step)


class RecordingObserver:
    """Observer fixture that stores emitted events in memory."""

    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, object]]] = []

    def emit(self, event_type: str, /, **data: object) -> None:
        self.events.append((event_type, data))

    def close(self) -> None:
        return


class RecordingRunner:
    """Runner fixture recording dispatch calls and session inputs."""

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


def continuity_test_config(*, primary_runner: str) -> DriverConfig:
    """Build test config with explicit primary/fallback routing."""

    runners = {
        "claude": RunnerConfig(command="claude", args=["-p"], output_parser="claude_json"),
        "opencode": RunnerConfig(
            command="opencode",
            args=["run", "--format", "json"],
            output_parser="opencode_jsonl",
            supports_agent_selection=True,
        ),
        "codex": RunnerConfig(
            command="codex",
            args=["exec", "--json"],
            output_parser="codex_jsonl",
        ),
        "gemini": RunnerConfig(
            command="gemini",
            args=["-p", "--output-format", "json", "--yolo"],
            output_parser="gemini_json",
            experimental=True,
            resume_flag="--resume",
        ),
    }
    return DriverConfig(
        plan_path="plan.yaml",
        runners=runners,
        agent_routing={"python-executor": primary_runner},
        fallback_runner="opencode",
        orchestration=OrchestrationConfig(max_parallelism=1),
        session=SessionConfig(reuse_ttl=300),
        judge=JudgeConfig(preflight=False),
        observability=ObservabilityConfig(),
    )


def create_worktree_binding(step_id: str) -> dict[str, object]:
    """Return kwargs used for WorktreeBinding construction."""

    return {
        "step_id": step_id,
        "worktree_path": Path(f".vectl/worktrees/{step_id}"),
        "branch_name": f"vectl/step-{step_id}",
        "reused_existing": False,
    }
