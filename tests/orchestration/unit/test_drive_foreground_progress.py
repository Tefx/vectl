from __future__ import annotations

from vectl.orch_app import OrchestrationApp
from vectl.orchestration.contracts import ChildRunRef, DriveBarrier
from vectl.orchestration.driver import DriveLoopResult, DriveStatusResult


def test_foreground_drive_does_not_replay_historical_child_dispatches() -> None:
    """Resumed supervision should only report child runs launched after attach."""

    app = object.__new__(OrchestrationApp)
    app._drive_child_run_contexts = {}
    events: list[dict[str, object]] = []

    historical = ChildRunRef(
        run_id="run-historical",
        drive_id="drv-test",
        kind="step",
        status="success",
        step_id="phase.historical",
        runner="opencode",
        workspace="ws-historical",
    )
    fresh = ChildRunRef(
        run_id="run-fresh",
        drive_id="drv-test",
        kind="step",
        status="running",
        step_id="phase.fresh",
        runner="opencode",
        workspace="ws-fresh",
    )
    drive_run_snapshots: list[tuple[ChildRunRef, ...]] = [
        (historical,),
        (historical, fresh),
    ]

    app.drive_runs = lambda drive_id: drive_run_snapshots.pop(0)  # type: ignore[method-assign]
    app.drive_status = lambda drive_id: DriveStatusResult(  # type: ignore[method-assign]
        drive_id=drive_id,
        status="blocked_operator",
        summary="blocked",
    )
    app.run_drive_loop = lambda drive_id: DriveLoopResult(  # type: ignore[method-assign]
        drive_id=drive_id,
        status="blocked_operator",
        summary="blocked",
    )
    app._drive_max_parallelism = lambda drive_id: 1  # type: ignore[method-assign]
    app._drive_progress_snapshot = lambda *, status, started_at: {  # type: ignore[method-assign]
        "drive_id": status.drive_id,
        "status": status.status,
        "elapsed_seconds": 0,
        "total_steps": 0,
        "done_steps": 0,
        "running_count": 0,
        "max_parallelism": 1,
        "ready_count": 0,
        "blocked_count": 0,
        "case_count": 0,
        "active_child_run_ids": (),
        "frontier_step_ids": (),
        "blocked_case_ids": (),
        "summary": status.summary,
    }

    app.run_drive_foreground("drv-test", progress_callback=events.append)

    dispatched_run_ids = [
        event["run_id"] for event in events if event.get("type") == "child_dispatched"
    ]
    assert dispatched_run_ids == ["run-fresh"]


def test_foreground_drive_continues_after_unpause_control_consumption() -> None:
    """Foreground supervision should not require a second drive command after unpause."""

    app = object.__new__(OrchestrationApp)
    app._drive_child_run_contexts = {}
    events: list[dict[str, object]] = []
    loop_calls: list[str] = []
    unpause_summary = (
        "operator unpause consumed: transition blocked_operator → "
        "blocked_operator, operator_pause_state=active"
    )

    status_snapshots = [
        DriveStatusResult(
            drive_id="drv-test",
            status="blocked_operator",
            summary="operator pause consumed",
        ),
        DriveStatusResult(
            drive_id="drv-test",
            status="blocked_operator",
            summary=unpause_summary,
        ),
    ]
    loop_results = [
        DriveLoopResult(
            drive_id="drv-test",
            status="blocked_operator",
            summary=unpause_summary,
        ),
        DriveLoopResult(
            drive_id="drv-test",
            status="blocked_operator",
            summary="resolver requires operator: still blocked",
        ),
    ]

    app.drive_runs = lambda drive_id: ()  # type: ignore[method-assign]
    app.drive_status = lambda drive_id: status_snapshots.pop(0)  # type: ignore[method-assign]

    def _run_drive_loop(drive_id: str) -> DriveLoopResult:
        loop_calls.append(drive_id)
        return loop_results.pop(0)

    app.run_drive_loop = _run_drive_loop  # type: ignore[method-assign]
    app._drive_max_parallelism = lambda drive_id: 1  # type: ignore[method-assign]
    app._drive_progress_snapshot = lambda *, status, started_at: {  # type: ignore[method-assign]
        "drive_id": status.drive_id,
        "status": status.status,
        "elapsed_seconds": 0,
        "total_steps": 0,
        "done_steps": 0,
        "running_count": 0,
        "max_parallelism": 1,
        "ready_count": 0,
        "blocked_count": 0,
        "case_count": 0,
        "active_child_run_ids": (),
        "frontier_step_ids": (),
        "blocked_case_ids": (),
        "summary": status.summary,
    }

    app.run_drive_foreground("drv-test", progress_callback=events.append)

    assert loop_calls == ["drv-test", "drv-test"]
    terminal_events = [event for event in events if event.get("type") == "drive_blocked"]
    assert terminal_events[-1]["summary"] == "resolver requires operator: still blocked"


def test_foreground_drive_reports_stopped_as_terminal_even_with_barrier() -> None:
    """Stopped drives should not render as case-resolution-required blocks."""

    app = object.__new__(OrchestrationApp)
    events: list[dict[str, object]] = []

    app._emit_terminal_drive_progress(
        progress_callback=events.append,
        result=DriveLoopResult(
            drive_id="drv-test",
            status="stopped",
            barrier=DriveBarrier(reason="runtime_failure", case_ids=("case-stale",)),
            summary="operator stop consumed: transition blocked_operator → stopped",
        ),
        started_at=0,
    )

    assert events[-1]["type"] == "drive_terminal"
    assert events[-1]["status"] == "stopped"
