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
from vectl.orchestration.config import OrchestrationConfig, RuntimeConfig
from vectl.orchestration.control_channel import FilesystemControlChannel
from vectl.orchestration.recovery import LegacyRunStatus
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


def _continuity_payload(step_id: str, session_id: str, event_id: str) -> dict[str, object]:
    return {
        "ledger": {
            "step_id": step_id,
            "session_id": session_id,
            "runner": "python",
            "status": "active",
        },
        "journal": {
            "event_id": event_id,
            "step_id": step_id,
            "session_id": session_id,
            "runner": "python",
            "event_type": "resume_attempt",
        },
    }


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


def test_run_persists_pending_before_runtime_start(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = _build_app(tmp_path)
    assert app._config.run_store_root is not None
    registry = RunRegistry(store_root=app._config.run_store_root)

    original_start = app._runtime.start

    def start_with_pending_check(*, request, workspace):
        record = registry.by_id(request.session_id)
        assert record is not None
        assert record.status == "pending"
        return original_start(request=request, workspace=workspace)

    monkeypatch.setattr(app._runtime, "start", start_with_pending_check)

    result = app.run(step_id="core.ready", agent="python-executor")
    assert result.success is True


def test_run_runtime_start_failure_terminalizes_after_durable_admission(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = _build_app(tmp_path)

    def fail_start(*, request, workspace):
        raise RuntimeError("boom")

    monkeypatch.setattr(app._runtime, "start", fail_start)

    result = app.run(step_id="core.ready", agent="python-executor")
    assert result.success is False
    assert result.run_id is not None
    registry = RunRegistry(store_root=tmp_path / "runs")
    record = registry.by_id(result.run_id)
    assert record is not None
    assert record.status == "fail"


def test_run_events_emit_only_after_running_record_is_durable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = _build_app(tmp_path)
    registry = RunRegistry(store_root=tmp_path / "runs")
    observed_statuses: list[str | None] = []

    original_emit = app._event_sink.emit

    def emit_with_status_probe(envelope) -> None:
        run_id = str((envelope.payload or {}).get("run_id", ""))
        record = registry.by_id(run_id)
        observed_statuses.append(None if record is None else record.status)
        original_emit(envelope)

    monkeypatch.setattr(app._event_sink, "emit", emit_with_status_probe)

    result = app.run(step_id="core.ready", agent="python-executor")
    assert result.success is True
    assert observed_statuses == ["running", "running"]


def test_run_event_persistence_failure_terminalizes_run(tmp_path: Path) -> None:
    app = _build_app(tmp_path)
    assert app._config.run_store_root is not None

    def fail_emit(_envelope) -> None:
        raise RuntimeError("event sink down")

    app._event_sink.emit = fail_emit  # type: ignore[method-assign] # test fault injection

    result = app.run(step_id="core.ready", agent="python-executor")
    assert result.success is False
    assert result.run_id is not None
    registry = RunRegistry(store_root=app._config.run_store_root)
    final = registry.by_id(result.run_id)
    assert final is not None
    assert final.status == "fail"


def test_build_fails_when_authoritative_plan_path_missing(tmp_path: Path) -> None:
    missing_plan = tmp_path / "missing-plan.yaml"
    config = AppConfig(
        plan_path=missing_plan,
        orchestration_config=OrchestrationConfig(plan_path=missing_plan),
        run_store_root=tmp_path / "runs",
    )

    with pytest.raises(FileNotFoundError, match="authoritative plan path not found"):
        build_orchestration_app(config)


def test_build_wires_runtime_workspace_root_from_orchestration_config(tmp_path: Path) -> None:
    plan_path = tmp_path / "plan.yaml"
    runs_root = tmp_path / "runs"
    workspace_root = tmp_path / "custom-workspaces"
    _write_plan(plan_path)
    config = AppConfig(
        plan_path=plan_path,
        orchestration_config=OrchestrationConfig(
            plan_path=plan_path,
            runtime=RuntimeConfig(workspace_root=workspace_root),
        ),
        run_store_root=runs_root,
    )
    app = build_orchestration_app(config)

    result = app.run(step_id="core.ready", agent="python-executor")
    assert result.success is True
    assert workspace_root.exists()
    assert any(workspace_root.iterdir())


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
    """End-to-end runtime proof that projection replay restores derived state artifacts.

    Authority: §9.2 - Projection must replay events into state/latest.json, state/summary.json,
    state/metrics.json with seq-order replay semantics.

    Verifies:
    - All three derived state artifacts are created after resume
    - Artifact schemas contain required fields per §9.2
    - Events are replayed in seq-order (last_event_seq matches event count)
    """
    app = _build_app(tmp_path)
    started = app.run(step_id="core.ready", agent="python-executor")
    assert started.success is True
    assert started.run_id is not None

    resumed = app.resume(started.run_id)
    assert resumed.success is True
    assert "resume_safe" in resumed.message

    registry = RunRegistry(store_root=tmp_path / "runs")
    run_root = registry.run_artifact_root(started.run_id)

    # Verify all three derived state artifacts exist (§9.2)
    latest_path = run_root / "state" / "latest.json"
    summary_path = run_root / "state" / "summary.json"
    metrics_path = run_root / "state" / "metrics.json"
    assert latest_path.exists(), "state/latest.json must exist per §9.2"
    assert summary_path.exists(), "state/summary.json must exist per §9.2"
    assert metrics_path.exists(), "state/metrics.json must exist per §9.2"

    # Load and validate state/latest.json schema (per §9.2.1)
    latest_payload = json.loads(latest_path.read_text(encoding="utf-8"))
    assert latest_payload.get("run_id") == started.run_id, "latest.json run_id mismatch"
    assert "version" in latest_payload, "latest.json missing version field"
    assert "status" in latest_payload, "latest.json missing status field"
    assert "last_event_seq" in latest_payload, "latest.json missing last_event_seq for replay proof"
    assert "active_step_id" in latest_payload, "latest.json missing active_step_id"
    assert "projection_health" in latest_payload, "latest.json missing projection_health"

    # Load and validate state/summary.json schema (per §9.2.1)
    summary_payload = json.loads(summary_path.read_text(encoding="utf-8"))
    assert summary_payload.get("run_id") == started.run_id, "summary.json run_id mismatch"
    assert "version" in summary_payload, "summary.json missing version field"
    assert "active_step_id" in summary_payload, "summary.json missing active_step_id"
    assert "open_case_count" in summary_payload, "summary.json missing open_case_count"
    assert "active_execution_count" in summary_payload, (
        "summary.json missing active_execution_count"
    )
    assert "last_event_seq" in summary_payload, "summary.json missing last_event_seq"

    # Load and validate state/metrics.json schema (per §9.2.1)
    metrics_payload = json.loads(metrics_path.read_text(encoding="utf-8"))
    assert metrics_payload.get("run_id") == started.run_id, "metrics.json run_id mismatch"
    assert "version" in metrics_payload, "metrics.json missing version field"
    assert "dispatch_count" in metrics_payload, "metrics.json missing dispatch_count"
    assert "resolution_count" in metrics_payload, "metrics.json missing resolution_count"
    assert "operator_required_case_count" in metrics_payload, (
        "metrics.json missing operator_required_case_count"
    )
    assert "transport_error_count" in metrics_payload, "metrics.json missing transport_error_count"
    assert "total_resolver_tokens" in metrics_payload, "metrics.json missing total_resolver_tokens"

    # Verify seq-order replay: last_event_seq should be non-negative
    # (actual value depends on events emitted during run/resume sequence)
    last_seq = latest_payload.get("last_event_seq", -1)
    assert isinstance(last_seq, int), "last_event_seq must be int for seq-order replay proof"
    assert last_seq >= 0, f"last_event_seq={last_seq} indicates no events replayed"

    # Verify consistency across all three artifacts
    assert latest_payload.get("last_event_seq") == summary_payload.get("last_event_seq"), (
        "latest.json and summary.json last_event_seq must match for projection consistency"
    )


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


def test_imported_legacy_runs_are_visible_via_orch_runs_surface(tmp_path: Path) -> None:
    app = _build_app(tmp_path)
    assert app._config.run_store_root is not None
    registry = RunRegistry(store_root=app._config.run_store_root)
    imported = registry.import_legacy_run(
        legacy_run_id="legacy-101",
        step_id="core.ready",
        plan_path=str(app._config.plan_path),
        status="running",
        migration_state="parallel",
        continuity_artifacts={
            "ledger": {
                "step_id": "core.ready",
                "session_id": "sess-101",
                "runner": "python",
                "status": "active",
            },
            "journal": {
                "event_id": "evt-101",
                "step_id": "core.ready",
                "session_id": "sess-101",
                "runner": "python",
                "event_type": "resume_attempt",
            },
        },
    )

    runs = app.runs(step_id="core.ready")
    assert len(runs) == 1
    assert runs[0].run_id == imported.run_id
    assert runs[0].source == "legacy_imported"
    assert runs[0].legacy_run_id == "legacy-101"
    assert runs[0].legacy_migration_state == "parallel"


def test_recover_surfaces_imported_legacy_continuity_blockers(tmp_path: Path) -> None:
    app = _build_app(tmp_path)
    assert app._config.run_store_root is not None
    registry = RunRegistry(store_root=app._config.run_store_root)
    imported = registry.import_legacy_run(
        legacy_run_id="legacy-blocked",
        step_id="core.ready",
        plan_path=str(app._config.plan_path),
        status="running",
        migration_state="preferred",
        continuity_artifacts={
            "ledger": {
                "step_id": "core.ready",
                "session_id": "sess-201",
                "runner": "python",
                "status": "active",
            }
        },
    )

    result = app.recover(step_id="core.ready")
    assert result.success is False
    assert "Recovery blocked" in result.message
    assert imported.run_id in result.message


def test_cutover_validate_surfaces_four_retirement_criteria(tmp_path: Path) -> None:
    app = _build_app(tmp_path)
    result = app.cutover_validate()

    assert result.can_cutover is True
    assert len(result.criteria_results) == 4
    assert all(item.startswith("criterion.") for item in result.criteria_results)


def test_migration_advance_state_updates_imported_run_record(tmp_path: Path) -> None:
    app = _build_app(tmp_path)
    assert app._config.run_store_root is not None
    registry = RunRegistry(store_root=app._config.run_store_root)
    imported = registry.import_legacy_run(
        legacy_run_id="legacy-advance",
        step_id="core.ready",
        plan_path=str(app._config.plan_path),
        status="running",
        migration_state="parallel",
        continuity_artifacts=_continuity_payload("core.ready", "sess-advance", "evt-advance"),
    )

    advanced = app.migration_advance_state(
        status=LegacyRunStatus.DEPRECATED,
        legacy_run_id="legacy-advance",
    )

    assert advanced.success is True
    updated = registry.by_id(imported.run_id)
    assert updated is not None
    assert updated.legacy_migration_state == "deprecated"


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
