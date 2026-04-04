"""Expected-RED tests for streaming/progress runtime gaps.

Sources:
- Step: ``driver-enhancement-streaming-progress.design-and-test``
- Reference: ``tools/vectl/README.md`` (runner/loop surface context)

These tests intentionally define missing behavior and MUST fail until
``driver-enhancement-streaming-progress.impl`` implements runtime wiring.
"""

from __future__ import annotations

import inspect
import sys
import time
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from src.vectl.driver.config import RunnerConfig
from src.vectl.driver.runners import OpenCodeRunner, _SubprocessRunnerHandle
from src.vectl.driver.types import (
    DriverState,
    RunningEntry,
    RunnerResult,
    RunnerStatus,
    StreamLivenessState,
    StreamProgressSignal,
    StreamSignalKind,
)


class _ImmediateHandle:
    session_id: str | None = None
    pid: int | None = 123

    async def wait(self, timeout: float | None = None) -> RunnerResult:
        return RunnerResult(
            status=RunnerStatus.SUCCESS,
            session_id="sess-1",
            output="done",
            elapsed_seconds=0.01,
            exit_code=0,
        )

    async def kill(self) -> None:
        return

    def is_alive(self) -> bool:
        return False

    def stream_jsonl(self) -> AsyncIterator[str]:
        raise NotImplementedError

    def last_progress_signal(self) -> StreamProgressSignal | None:
        return None


def test_subprocess_wait_contract_forbids_communicate_only_transport() -> None:
    """wait() must not be a one-shot communicate-only boundary.

    Source:
    - step ``driver-enhancement-streaming-progress.design-and-test`` requires
      incremental JSONL consumption semantics before final completion.
    """

    source = inspect.getsource(_SubprocessRunnerHandle.wait)
    assert ".communicate(" not in source


def test_driver_state_contract_requires_heartbeat_progress_awareness() -> None:
    """DriverState must track heartbeat/progress, not only final summaries.

    Source:
    - step ``driver-enhancement-streaming-progress.design-and-test`` heartbeat/
      progress model requirement.
    """

    state = DriverState()
    signal = StreamProgressSignal(
        kind=StreamSignalKind.HEARTBEAT,
        emitted_at_monotonic=time.monotonic(),
        sequence=1,
        message="runner alive",
    )

    state.record_stream_signal(step_id="core.impl", signal=signal)

    live = state.streaming_live_state["core.impl"]
    assert live.last_progress == signal
    assert live.phase == "running"


@pytest.mark.anyio
async def test_runner_handle_exposes_last_progress_after_incremental_stream(tmp_path: Path) -> None:
    """Handle must expose last-progress signal as stream advances.

    Source:
    - step ``driver-enhancement-streaming-progress.design-and-test`` last-
      progress signal requirement.
    """

    code = (
        "import json,time;"
        "print(json.dumps({'type':'step_start','sessionID':'sid-1'}), flush=True);"
        "time.sleep(0.2);"
        "print(json.dumps({'type':'text','part':{'text':'tick'}}), flush=True);"
        "time.sleep(0.2);"
        "print(json.dumps({'type':'step_finish','part':{'status':'success'}}), flush=True)"
    )
    runner = OpenCodeRunner(
        "opencode",
        RunnerConfig(
            command=sys.executable,
            args=["-c", code],
            prompt_mode="stdin",
            output_parser="opencode_jsonl",
        ),
    )
    handle = await runner.dispatch(prompt="prompt", agent="python-senior", workdir=str(tmp_path))

    try:
        stream = handle.stream_jsonl()
        first_line = await anext(stream)
        assert "step_start" in first_line
        assert handle.last_progress_signal() is not None
    finally:
        await handle.kill()


@pytest.mark.anyio
async def test_wait_for_any_requires_streaming_aware_liveness_transition() -> None:
    """wait_for_any() must transition live liveness state to completed.

    Source:
    - step ``driver-enhancement-streaming-progress.design-and-test`` requires
      streaming-aware loop state boundaries.
    """

    state = DriverState()
    step_id = "core.impl"
    state.running[step_id] = RunningEntry(
        step_id=step_id,
        agent="python-senior",
        runner_name="opencode",
        handle=_ImmediateHandle(),
        worktree_path="/tmp/wt",
        dispatched_at=time.monotonic(),
        live_state=StreamLivenessState(
            phase="running",
            last_heartbeat_at_monotonic=time.monotonic(),
            last_progress=None,
        ),
    )
    state.streaming_live_state[step_id] = StreamLivenessState(
        phase="running",
        last_heartbeat_at_monotonic=time.monotonic(),
        last_progress=None,
    )

    await state.wait_for_any()

    assert state.streaming_live_state[step_id].phase == "completed"
