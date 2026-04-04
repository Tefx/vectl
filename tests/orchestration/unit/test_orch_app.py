"""Composition-root and operator-surface tests for ``vectl.orch_app``.

Authority:
    orch_operator_control_surface.impl_orch_app_composition_root step contract
    docs/ORCHESTRATION-PLANE-IMPLEMENTATION-DESIGN.md section 6
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, cast

import pytest

from vectl.io import save_plan
from vectl.models import Phase, Plan, Step
from vectl.orch_app import AppConfig, build_orchestration_app
from vectl.orchestration.config import OrchestrationConfig
from vectl.orchestration.control_channel import FilesystemControlChannel
from vectl.orchestration.run_store import RunRecord, RunRegistry, generate_run_id


def _write_plan(plan_path: Path) -> None:
    plan = Plan(
        project="orch-app",
        phases=[
            Phase(
                id="core",
                name="Core",
                steps=[
                    Step(id="core.ready", name="Ready"),
                    Step(id="core.other", name="Other", depends_on=["core.ready"]),
                ],
            )
        ],
    )
    save_plan(plan, plan_path)


def _build_app(tmp_path: Path):
    plan_path = tmp_path / "plan.yaml"
    runs_root = tmp_path / "runs"
    _write_plan(plan_path)
    config = AppConfig(
        plan_path=plan_path,
        orchestration_config=OrchestrationConfig(plan_path=plan_path),
        run_store_root=runs_root,
    )
    return build_orchestration_app(config)


def test_build_composes_real_collaborators_without_noop_resolver_dependency(tmp_path: Path) -> None:
    app = _build_app(tmp_path)

    assert app._control.__class__.__name__ == "PlanAwareControl"
    assert app._core_adapter.__class__.__name__ == "PlanCoreAdapter"
    assert app._resolver.__class__.__name__ == "BoundResolver"
    assert app._resolver.invocation.__class__.__name__ == "GatewayResolverInvocation"


def test_run_and_control_route_through_typed_boundaries(tmp_path: Path) -> None:
    app = _build_app(tmp_path)

    run_result = app.run(step_id="core.ready", agent="python-executor")
    assert run_result.success is True
    assert run_result.run_id is not None

    runs = app.runs(step_id="core.ready")
    assert len(runs) == 1
    assert runs[0].run_id == run_result.run_id
    assert runs[0].status == "running"

    events = app.inspect_events(step_id="core.ready", limit=10)
    assert events.view_type == "events"
    assert any("event=run_started" in row for row in events.data)

    pause = app.control_pause(step_id="core.ready")
    assert pause.success is True

    actions = app.inspect_actions(run_id=run_result.run_id)
    assert actions.view_type == "actions"
    assert any("type=control.pause" in row for row in actions.data)


def test_run_admission_failure_when_same_plan_already_has_active_run(tmp_path: Path) -> None:
    app = _build_app(tmp_path)
    assert app._config.run_store_root is not None
    registry = RunRegistry(store_root=app._config.run_store_root)
    conflicting_run_id = generate_run_id()
    registry.save(
        RunRecord(
            run_id=conflicting_run_id,
            step_id="core.ready",
            plan_path=str(app._config.plan_path),
            status="running",
            agent="python-executor",
        )
    )

    result = app.run(step_id="core.ready", agent="python-executor")
    assert result.success is False
    assert "Run admission denied" in result.message


def test_build_fails_when_authoritative_plan_path_missing(tmp_path: Path) -> None:
    missing_plan = tmp_path / "missing-plan.yaml"
    config = AppConfig(
        plan_path=missing_plan,
        orchestration_config=OrchestrationConfig(plan_path=missing_plan),
        run_store_root=tmp_path / "runs",
    )

    with pytest.raises(FileNotFoundError, match="authoritative plan path not found"):
        build_orchestration_app(config)


def test_control_pause_reports_ambiguous_run_selection(tmp_path: Path) -> None:
    app = _build_app(tmp_path)
    assert app._config.run_store_root is not None
    registry = RunRegistry(store_root=app._config.run_store_root)
    for step_id in ("core.ready", "core.other"):
        registry.save(
            RunRecord(
                run_id=generate_run_id(),
                step_id=step_id,
                plan_path=str(app._config.plan_path),
                status="running",
                agent="python-executor",
            )
        )

    result = app.control_pause(step_id=None)
    assert result.success is False
    assert "Run selection is ambiguous" in result.message


def test_control_reason_and_force_are_persisted_in_pending_actions(tmp_path: Path) -> None:
    app = _build_app(tmp_path)
    orch = cast(Any, app)
    run_result = app.run(step_id="core.ready", agent="python-executor")
    assert run_result.run_id is not None

    pause = orch.control_pause(step_id="core.ready", reason="maintenance")
    unpause = orch.control_unpause(step_id="core.ready", reason="resumed")
    stop = orch.control_stop(reason="halt", force=True)
    assert pause.success is True
    assert unpause.success is True
    assert stop.success is True

    assert app._config.run_store_root is not None
    channel = FilesystemControlChannel(runs_root=app._config.run_store_root)
    requests = channel.list_requests(run_result.run_id, status="pending")
    by_type = {request.msg_type: request for request in requests}

    assert by_type["control.pause"].payload == (run_result.run_id, "maintenance")
    assert by_type["control.unpause"].payload == (run_result.run_id, "resumed")
    assert by_type["control.stop"].payload == (run_result.run_id, "halt", "force=true")


def test_config_show_effective_returns_expanded_view(tmp_path: Path) -> None:
    app = _build_app(tmp_path)
    orch = cast(Any, app)

    summary = orch.config_show(effective=False)
    effective = orch.config_show(effective=True)

    assert "artifact_root=" in summary.show_output
    assert "control.idle_poll_interval_ms=" not in summary.show_output
    assert "control.idle_poll_interval_ms=" in effective.show_output
    assert "operator.max_pending_actions=" in effective.show_output


def test_resume_safe_replays_from_durable_artifacts(tmp_path: Path) -> None:
    app = _build_app(tmp_path)
    started = app.run(step_id="core.ready", agent="python-executor")
    assert started.success is True
    assert started.run_id is not None

    resumed = app.resume(started.run_id)
    assert resumed.success is True
    assert "resume_safe" in resumed.message

    run_root = RunRegistry(store_root=tmp_path / "runs").run_artifact_root(started.run_id)
    assert (run_root / "state" / "latest.json").exists()


def test_recover_and_resume_from_durable_artifacts(tmp_path: Path) -> None:
    app = _build_app(tmp_path)
    started = app.run(step_id="core.ready", agent="python-executor")
    assert started.success is True
    assert started.run_id is not None

    recovered = app.recover()
    assert recovered.success is True
    assert "recover_and_resume" in recovered.message
    assert "artifact_families=" in recovered.message


def test_recover_blocks_on_blocking_divergence(tmp_path: Path) -> None:
    app = _build_app(tmp_path)
    started = app.run(step_id="core.ready", agent="python-executor")
    assert started.run_id is not None

    run_root = RunRegistry(store_root=tmp_path / "runs").run_artifact_root(started.run_id)
    ledger_path = run_root / "continuity" / "ledger.json"
    ledger_payload = json.loads(ledger_path.read_text(encoding="utf-8"))
    ledger_payload["step_id"] = "core.other"
    ledger_path.write_text(json.dumps(ledger_payload) + "\n", encoding="utf-8")

    recovered = app.recover()
    assert recovered.success is False
    assert "blocking_divergence" in recovered.message


def test_recover_blocks_on_corrupt_blocking(tmp_path: Path) -> None:
    app = _build_app(tmp_path)
    started = app.run(step_id="core.ready", agent="python-executor")
    assert started.run_id is not None

    run_root = RunRegistry(store_root=tmp_path / "runs").run_artifact_root(started.run_id)
    journal_path = run_root / "continuity" / "journal.jsonl"
    journal_path.write_text('{"broken"\n', encoding="utf-8")

    recovered = app.recover()
    assert recovered.success is False
    assert "corrupt_blocking" in recovered.message


def test_recover_blocks_on_ambiguous_blocking(tmp_path: Path) -> None:
    app = _build_app(tmp_path)
    started = app.run(step_id="core.ready", agent="python-executor")
    assert started.run_id is not None

    run_root = RunRegistry(store_root=tmp_path / "runs").run_artifact_root(started.run_id)
    capability_path = run_root / "continuity" / "capability.snapshot.json"
    capability_payload = json.loads(capability_path.read_text(encoding="utf-8"))
    capability_payload["fingerprint"] = "mismatch"
    capability_path.write_text(json.dumps(capability_payload) + "\n", encoding="utf-8")

    recovered = app.recover()
    assert recovered.success is False
    assert "ambiguous_blocking" in recovered.message


def test_recover_terminalizes_fresh_start_required_before_restart(tmp_path: Path) -> None:
    app = _build_app(tmp_path)
    started = app.run(step_id="core.ready", agent="python-executor")
    assert started.run_id is not None

    run_root = RunRegistry(store_root=tmp_path / "runs").run_artifact_root(started.run_id)
    heartbeat_path = run_root / "heartbeat.json"
    heartbeat_payload = json.loads(heartbeat_path.read_text(encoding="utf-8"))
    heartbeat_payload["last_heartbeat_at"] = time.time() - 1000.0
    heartbeat_path.write_text(json.dumps(heartbeat_payload) + "\n", encoding="utf-8")

    recovered = app.recover()
    assert recovered.success is True
    assert "fresh_start_required" in recovered.message

    registry = RunRegistry(store_root=tmp_path / "runs")
    terminal = registry.by_id(started.run_id)
    assert terminal is not None
    assert terminal.status == "fail"

    restarted = app.run(step_id="core.other", agent="python-executor")
    assert restarted.success is True


def test_recover_dry_run_does_not_mutate_durable_state(tmp_path: Path) -> None:
    app = _build_app(tmp_path)
    started = app.run(step_id="core.ready", agent="python-executor")
    assert started.run_id is not None

    run_root = RunRegistry(store_root=tmp_path / "runs").run_artifact_root(started.run_id)
    heartbeat_path = run_root / "heartbeat.json"
    heartbeat_payload = json.loads(heartbeat_path.read_text(encoding="utf-8"))
    heartbeat_payload["last_heartbeat_at"] = time.time() - 1000.0
    heartbeat_path.write_text(json.dumps(heartbeat_payload) + "\n", encoding="utf-8")

    recovered = app.recover(dry_run=True)
    assert recovered.success is True
    assert "fresh_start_required" in recovered.message
    assert "dry_run=true" in recovered.message

    registry = RunRegistry(store_root=tmp_path / "runs")
    current = registry.by_id(started.run_id)
    assert current is not None
    assert current.status == "running"
    assert not (run_root / "continuity" / "quarantine").exists()


def test_resume_blocks_when_event_transcript_is_corrupt(tmp_path: Path) -> None:
    app = _build_app(tmp_path)
    started = app.run(step_id="core.ready", agent="python-executor")
    assert started.run_id is not None

    events_path = tmp_path / "runs" / "events.jsonl"
    events_path.write_text('{"broken"\n', encoding="utf-8")

    resumed = app.resume(started.run_id)
    assert resumed.success is False
    assert "corrupt_blocking" in resumed.message


@pytest.mark.parametrize("missing_heartbeat", [True, False])
def test_decision_matrix_blocks_or_requires_fresh_start_for_unknown_or_stale_heartbeat(
    tmp_path: Path,
    missing_heartbeat: bool,
) -> None:
    app = _build_app(tmp_path)
    started = app.run(step_id="core.ready", agent="python-executor")
    assert started.run_id is not None

    run_root = RunRegistry(store_root=tmp_path / "runs").run_artifact_root(started.run_id)
    heartbeat_path = run_root / "heartbeat.json"
    if missing_heartbeat:
        heartbeat_path.unlink()
    else:
        heartbeat_payload = json.loads(heartbeat_path.read_text(encoding="utf-8"))
        heartbeat_payload["last_heartbeat_at"] = time.time() - 1000.0
        heartbeat_path.write_text(json.dumps(heartbeat_payload) + "\n", encoding="utf-8")

    recovered = app.recover(dry_run=True)
    assert recovered.success is True
    assert "fresh_start_required" in recovered.message
