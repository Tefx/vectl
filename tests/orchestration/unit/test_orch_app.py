"""Composition-root and operator-surface tests for ``vectl.orch_app``.

Authority:
    orch_operator_control_surface.impl_orch_app_composition_root step contract
    docs/ORCHESTRATION-PLANE-IMPLEMENTATION-DESIGN.md section 6
"""

from __future__ import annotations

from pathlib import Path

import pytest

from vectl.io import save_plan
from vectl.models import Phase, Plan, Step
from vectl.orch_app import AppConfig, build_orchestration_app
from vectl.orchestration.config import OrchestrationConfig
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
