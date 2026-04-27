"""Live smoke tests for drive autonomy recovery surfaces.

These tests use the real vectl CLI, real drive store, and real OpenCode
configuration/preflight.  They intentionally seed durable drive facts instead
of launching long runner work so they stay smoke-sized.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from tests.live_smoke.helpers import (
    LiveRunnerPreflight,
    is_live_runner_opted_in,
    live_runner,
    opencode_live,
)
from tests.orchestration.helpers import OpenCodeDriveHarness, default_orchestrator_env
from vectl.orchestration.contracts import ChildRunRef
from vectl.orchestration.run_store import DriveStore, RunRecord, RunRegistry


def _empty_plan(project: str) -> dict[str, object]:
    return {
        "project": project,
        "phases": [
            {
                "id": "empty",
                "name": "Empty",
                "steps": [],
            }
        ],
    }


def _prepare_drive_harness(tmp_path: Path, *, project: str) -> tuple[OpenCodeDriveHarness, str]:
    if not is_live_runner_opted_in():
        pytest.skip("drive autonomy live smoke requires RUN_LIVE_RUNNER_TESTS=1")
    LiveRunnerPreflight("opencode")()

    harness = OpenCodeDriveHarness(tmp_path, env=default_orchestrator_env())
    harness.write_plan(_empty_plan(project))
    harness.write_orchestration_config(max_parallelism=1)
    harness.init_git_repo()
    start = harness.start_drive(max_parallelism=1)
    return harness, start.drive_id


def _seed_stale_child_run(
    root: Path,
    *,
    drive_id: str,
    run_id: str,
    step_id: str,
    prior_failures: int = 0,
) -> None:
    runs_root = root / ".vectl" / "runs"
    drive_store = DriveStore(store_root=runs_root / "drives")
    registry = RunRegistry(store_root=runs_root)

    for index in range(prior_failures):
        failed_run_id = f"{run_id}-prior-{index}"
        drive_store.save_child_run(
            ChildRunRef(
                run_id=failed_run_id,
                drive_id=drive_id,
                kind="step",
                status="fail",
                step_id=step_id,
                artifact_root=str(runs_root / failed_run_id),
            )
        )

    run_root = runs_root / run_id
    run_root.mkdir(parents=True, exist_ok=True)
    stale_at = time.time() - 600.0
    (run_root / "heartbeat.json").write_text(
        json.dumps(
            {
                "run_id": run_id,
                "pid": 0,
                "host_id": "drive-autonomy-live-smoke",
                "started_at": stale_at,
                "last_heartbeat_at": stale_at,
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    registry.save(
        RunRecord(
            run_id=run_id,
            step_id=step_id,
            plan_path=str(root / "plan.yaml"),
            agent="python-executor",
            status="running",
            artifact_root=str(run_root),
            output_summary="seeded stale child run for live smoke",
        )
    )
    drive_store.save_child_run(
        ChildRunRef(
            run_id=run_id,
            drive_id=drive_id,
            kind="step",
            status="running",
            step_id=step_id,
            artifact_root=str(run_root),
        )
    )


@live_runner
@opencode_live
class TestDriveAutonomyLiveSmoke:
    def test_cli_auto_recovers_stale_child_and_continues(self, tmp_path: Path) -> None:
        """Real CLI auto-recovers a provably stale child run without operator input."""
        harness, drive_id = _prepare_drive_harness(
            tmp_path,
            project="drive-autonomy-stale-auto-recover-live",
        )
        _seed_stale_child_run(
            tmp_path,
            drive_id=drive_id,
            run_id="run-stale-live-001",
            step_id="empty.ghost",
        )

        result = harness.run_vectl(
            ["orch", "drive", "--once", "--json"],
            timeout=120,
            scenario="drive_autonomy_auto_recover_stale_child_live",
        )
        result.assert_success("drive stale-child auto-recovery live smoke failed")
        payload = json.loads(result.stdout)

        assert payload["status"] == "completed"
        assert payload["human_required"] is False
        assert payload["auto_recovery"]["applied"] is True
        assert payload["auto_recovery"]["failed_child_run_ids"] == ["run-stale-live-001"]

    def test_cli_reports_retry_limit_without_looping_forever(self, tmp_path: Path) -> None:
        """Real CLI stops with a compact retry-limit reason after repeated stale runs."""
        harness, drive_id = _prepare_drive_harness(
            tmp_path,
            project="drive-autonomy-retry-limit-live",
        )
        _seed_stale_child_run(
            tmp_path,
            drive_id=drive_id,
            run_id="run-stale-live-003",
            step_id="empty.ghost",
            prior_failures=2,
        )

        result = harness.run_vectl(
            ["orch", "drive", "--once", "--json"],
            timeout=120,
            scenario="drive_autonomy_retry_limit_live",
        )
        result.assert_failed("retry-limit live smoke should stop for operator")
        assert result.returncode == 4, result.format_diagnostics()
        payload = json.loads(result.stdout)

        assert payload["human_required"] is True
        assert payload["reason_code"] == "retry_limit"
        assert "retry_limit" in payload["stop_reason"]
        assert payload["auto_recovery"]["applied"] is False
