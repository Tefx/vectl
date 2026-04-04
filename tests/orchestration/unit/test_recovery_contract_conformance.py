from __future__ import annotations

import json
import time
from pathlib import Path

from vectl.orch_app import AppConfig, build_orchestration_app
from vectl.orchestration.recovery import recovery_action_status, recovery_case_status
from vectl.orchestration.run_store import RunRegistry


def _build_app(tmp_path: Path):
    plan_path = tmp_path / "plan.yaml"
    plan_contents = (
        "project: recovery-contract\n"
        "phases:\n"
        "  - id: core\n"
        "    steps:\n"
        "      - id: core.ready\n"
        "        name: Ready\n"
    )
    plan_path.write_text(
        plan_contents,
        encoding="utf-8",
    )
    return build_orchestration_app(AppConfig(plan_path=plan_path, run_store_root=tmp_path / "runs"))


def test_recovery_report_semantics_are_shared_across_consumer_surfaces(tmp_path: Path) -> None:
    app = _build_app(tmp_path)
    started = app.run(step_id="core.ready", agent="python-executor")
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
