from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from vectl.orch_app import AppConfig, OrchestrationApp, OrchestrationResult, build_orchestration_app
from vectl.orchestration.contracts import DispatchSpec
from vectl.orchestration.recovery import recovery_action_status, recovery_case_status
from vectl.orchestration.run_store import RunRegistry


def _skip_collect_and_route_terminal(
    self,
    *,
    registry: RunRegistry,
    run_id: str,
    step_id: str,
    execution_id: str,
    dispatch_spec: DispatchSpec,
    agent: str,
    run_root: Path,
    max_poll_iterations: int = 600,
    poll_interval_seconds: float = 0.1,
) -> OrchestrationResult:
    """Skipped collect-and-route that returns success without full lifecycle."""
    return OrchestrationResult(
        success=True,
        message=(
            f"resume_safe: Skipped collect-and-route for recovery contract test (step={step_id})"
        ),
        step_id=step_id,
        run_id=run_id,
    )


_original_collect_and_route = OrchestrationApp._collect_and_route_terminal


@pytest.fixture(autouse=True)
def _patch_collect_and_route():
    """Autouse fixture: skip _collect_and_route_terminal for recovery contract tests.

    These tests verify recovery surface behavior without requiring a real
    runner completion cycle.
    """
    OrchestrationApp._collect_and_route_terminal = _skip_collect_and_route_terminal  # type: ignore[assignment]
    yield
    OrchestrationApp._collect_and_route_terminal = _original_collect_and_route  # type: ignore[assignment]


def _build_app(tmp_path: Path):
    plan_path = tmp_path / "plan.yaml"
    plan_contents = (
        "project: recovery-contract\n"
        "phases:\n"
        "  - id: core\n"
        "    name: Core\n"
        "    steps:\n"
        "      - id: core.ready\n"
        "        name: Ready\n"
    )
    plan_path.write_text(
        plan_contents,
        encoding="utf-8",
    )
    app = build_orchestration_app(AppConfig(plan_path=plan_path, run_store_root=tmp_path / "runs"))
    return app


def test_recovery_report_semantics_are_shared_across_consumer_surfaces(tmp_path: Path) -> None:
    app = _build_app(tmp_path)
    started = app.run(step_id="core.ready", agent="python-executor")
    assert started.success is True
    assert started.run_id is not None

    run_root = RunRegistry(store_root=tmp_path / "runs").run_artifact_root(started.run_id)
    ledger_path = run_root / "continuity" / "ledger.json"
    ledger_payload = json.loads(ledger_path.read_text(encoding="utf-8"))
    ledger_payload["step_id"] = "core.other"
    ledger_path.write_text(json.dumps(ledger_payload) + "\n", encoding="utf-8")

    result = app.recover(step_id="core.ready")
    report = result.recovery_report
    assert report is not None

    status_data = app.inspect_status(step_id="core.ready").data
    assert f"recovery_outcome={report.outcome.value}" in status_data
    assert f"gate_open_allowed={report.gate_open_allowed}" in status_data

    actions_data = app.inspect_actions(run_id=started.run_id).data
    expected_action_status = recovery_action_status(report.outcome)
    assert any(
        row.startswith(f"status={expected_action_status} ") and "type=blocked" in row
        for row in actions_data
    )

    synthetic_case = app.case_show("recovery.latest")
    assert synthetic_case.status == recovery_case_status(report.outcome)
    assert synthetic_case.reason == report.message


def test_recovery_quarantine_preserves_artifacts_when_gate_open_blocked(tmp_path: Path) -> None:
    app = _build_app(tmp_path)
    started = app.run(step_id="core.ready", agent="python-executor")
    assert started.success is True
    assert started.run_id is not None

    run_root = RunRegistry(store_root=tmp_path / "runs").run_artifact_root(started.run_id)
    heartbeat_path = run_root / "heartbeat.json"
    heartbeat_payload = json.loads(heartbeat_path.read_text(encoding="utf-8"))
    heartbeat_payload["last_heartbeat_at"] = time.time() - 1000.0
    heartbeat_path.write_text(json.dumps(heartbeat_payload) + "\n", encoding="utf-8")

    ledger_source = run_root / "continuity" / "ledger.json"
    journal_source = run_root / "continuity" / "journal.jsonl"
    capability_source = run_root / "continuity" / "capability.snapshot.json"

    result = app.recover(step_id="core.ready")
    report = result.recovery_report
    assert report is not None
    assert report.gate_open_allowed is False
    assert report.no_silent_deletion_preserved is True
    assert report.quarantined_artifact_paths

    assert ledger_source.exists()
    assert journal_source.exists()
    assert capability_source.exists()
    assert heartbeat_path.exists()
