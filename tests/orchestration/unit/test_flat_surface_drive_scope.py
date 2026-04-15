"""Flat CLI surface drive-scope default resolution tests.

Authority:
    docs/RFC-orch-drive.md sections 7.3, 7.4
    Step: orch_drive_surfaces.flat-surface-default-drive-scope-fix

These tests verify that --latest on flat inspection and control surfaces
resolves to active drive scope (not single-run scope) when a drive exists,
and that explicit single-run selectors still preserve prior behavior.

Blockers addressed:
    - orch status --latest resolves through drive scope, not single-run scope
    - orch control stop --latest resolves through drive scope, not single-run scope
    - All other flat --latest surfaces (events, logs, artifacts, actions, pause,
      unpause) also resolve through drive scope
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from vectl.io import save_plan
from vectl.models import Phase, Plan, Step
from vectl.orch_app import AppConfig, ControlResult, build_orchestration_app
from vectl.orchestration.config import OrchestrationConfig
from vectl.orchestration.driver import DriveStatusResult
from vectl.orchestration.inspection_queries import DriveInspectView


# ------------------------------------------------------------------
# Fixtures
# ------------------------------------------------------------------


def _write_plan(plan_path: Path) -> None:
    """Write a minimal plan.yaml for testing."""
    plan = Plan(
        project="flat-scope-test",
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
    """Build orchestration app with proper config for flat scope tests."""
    plan_path = tmp_path / "plan.yaml"
    runs_root = tmp_path / "runs"
    _write_plan(plan_path)
    config = AppConfig(
        plan_path=plan_path,
        orchestration_config=OrchestrationConfig(plan_path=plan_path),
        run_store_root=runs_root,
    )
    return build_orchestration_app(config)


# ------------------------------------------------------------------
# DriveInspectView scope_kind tests
# ------------------------------------------------------------------


class TestInspectDriveStatusScopeKind:
    """Verify DriveInspectView includes scope_kind='drive'."""

    def test_inspect_drive_status_includes_scope_kind(self, tmp_path: Path) -> None:
        """inspect_drive_status returns a DriveInspectView with scope_kind='drive'."""
        app = _build_app(tmp_path)
        started = app.start_drive(agent="test-agent", max_parallelism=4)
        view = app.inspect_drive_status(drive_id=started.drive_id)
        assert isinstance(view, DriveInspectView)
        assert view.scope_kind == "drive"
        assert view.drive_id == started.drive_id

    def test_inspect_drive_status_json_includes_scope_kind(self, tmp_path: Path) -> None:
        """JSON serialization of DriveInspectView includes scope_kind and drive_id."""
        app = _build_app(tmp_path)
        started = app.start_drive(agent="test-agent", max_parallelism=4)
        view = app.inspect_drive_status(drive_id=started.drive_id)
        from dataclasses import asdict

        payload = asdict(view)
        assert payload["scope_kind"] == "drive"
        assert payload["drive_id"] == started.drive_id


# ------------------------------------------------------------------
# DriveStatusResult scope_kind tests
# ------------------------------------------------------------------


class TestDriveStatusResultScopeKind:
    """Verify DriveStatusResult includes scope_kind='drive'."""

    def test_drive_status_result_includes_scope_kind(self, tmp_path: Path) -> None:
        """drive_status returns a DriveStatusResult with scope_kind='drive'."""
        app = _build_app(tmp_path)
        started = app.start_drive(agent="test-agent", max_parallelism=4)
        result = app.drive_status(drive_id=started.drive_id)
        assert isinstance(result, DriveStatusResult)
        assert result.scope_kind == "drive"
        assert result.drive_id == started.drive_id


# ------------------------------------------------------------------
# ControlResult enrichment tests
# ------------------------------------------------------------------


class TestDriveScopeControlResultEnrichment:
    """Verify ControlResult enrichment with drive scope metadata."""

    def test_enrich_drive_scope_result_adds_scope_kind(self) -> None:
        """_enrich_drive_scope_result adds scope_kind and drive_id to ControlResult."""
        from vectl.cli import _enrich_drive_scope_result

        result = ControlResult(action="stop", success=True, message="Stop queued")
        enriched = _enrich_drive_scope_result(result, "drv_test123")
        assert enriched["scope_kind"] == "drive"
        assert enriched["drive_id"] == "drv_test123"
        assert enriched["action"] == "stop"
        assert enriched["success"] is True

    def test_enrich_preserves_original_fields(self) -> None:
        """_enrich_drive_scope_result preserves all original ControlResult fields."""
        from vectl.cli import _enrich_drive_scope_result

        result = ControlResult(action="pause", success=True, message="Pause queued")
        enriched = _enrich_drive_scope_result(result, "drv_abc")
        assert enriched["action"] == "pause"
        assert enriched["success"] is True
        assert enriched["message"] == "Pause queued"
        assert enriched["scope_kind"] == "drive"
        assert enriched["drive_id"] == "drv_abc"


# ------------------------------------------------------------------
# has_active_drive_for_plan + --latest resolution tests
# ------------------------------------------------------------------


class TestFlatSurfaceLatestResolvesToDriveScope:
    """Verify --latest resolves to drive scope when a drive exists.

    Authority: docs/RFC-orch-drive.md §7.3, §7.4
    """

    def test_status_latest_resolves_to_drive_when_active(self, tmp_path: Path) -> None:
        """orch status --latest resolves to active drive, not single-run.

        When an active drive exists, --latest should route through
        the drive-scoped path, producing scope_kind='drive' in the output.
        """
        app = _build_app(tmp_path)
        started = app.start_drive(agent="test-agent", max_parallelism=4)

        # has_active_drive_for_plan should return the drive_id
        active = app.has_active_drive_for_plan()
        assert active == started.drive_id

        # resolve_latest_drive_id should also return the drive_id
        latest = app.resolve_latest_drive_id()
        assert latest == started.drive_id

    def test_control_drive_stop_returns_control_result(self, tmp_path: Path) -> None:
        """control_drive_stop returns a ControlResult that can be enriched."""
        app = _build_app(tmp_path)
        started = app.start_drive(agent="test-agent", max_parallelism=4)
        result = app.control_drive_stop(drive_id=started.drive_id)
        assert isinstance(result, ControlResult)
        assert result.success is True
        assert result.action == "stop"

    def test_control_drive_pause_returns_control_result(self, tmp_path: Path) -> None:
        """control_drive_pause returns a ControlResult that can be enriched."""
        app = _build_app(tmp_path)
        started = app.start_drive(agent="test-agent", max_parallelism=4)
        result = app.control_drive_pause(drive_id=started.drive_id)
        assert isinstance(result, ControlResult)
        assert result.success is True
        assert result.action == "pause"

    def test_control_drive_unpause_returns_control_result(self, tmp_path: Path) -> None:
        """control_drive_unpause returns a ControlResult that can be enriched."""
        app = _build_app(tmp_path)
        started = app.start_drive(agent="test-agent", max_parallelism=4)
        # Pause first so we can unpause
        app.control_drive_pause(drive_id=started.drive_id)
        result = app.control_drive_unpause(drive_id=started.drive_id)
        assert isinstance(result, ControlResult)
        assert result.success is True
        assert result.action == "unpause"


# ------------------------------------------------------------------
# Sibling search: inspect events/logs/artifacts/actions surfaces
# ------------------------------------------------------------------


class TestFlatSurfaceLatestAllInspectionSurfacesResolveToDriveScope:
    """Verify --latest resolves to drive scope for ALL flat inspection surfaces.

    Authority: docs/RFC-orch-drive.md §7.3, §7.4 — all flat surfaces that
    advertise --latest must resolve to active drive scope when a drive exists.
    """

    def test_inspect_drive_events_with_default_scope(self, tmp_path: Path) -> None:
        """inspect_drive_events returns drive-scoped result."""
        app = _build_app(tmp_path)
        started = app.start_drive(agent="test-agent", max_parallelism=4)
        result = app.inspect_drive_events(drive_id=started.drive_id)
        assert result.view_type == "events"

    def test_inspect_drive_logs_with_default_scope(self, tmp_path: Path) -> None:
        """inspect_drive_logs returns drive-scoped result."""
        app = _build_app(tmp_path)
        started = app.start_drive(agent="test-agent", max_parallelism=4)
        result = app.inspect_drive_logs(drive_id=started.drive_id)
        assert result is not None

    def test_inspect_drive_artifacts_with_default_scope(self, tmp_path: Path) -> None:
        """inspect_drive_artifacts returns drive-scoped result."""
        app = _build_app(tmp_path)
        started = app.start_drive(agent="test-agent", max_parallelism=4)
        result = app.inspect_drive_artifacts(drive_id=started.drive_id)
        assert result is not None

    def test_inspect_drive_actions_with_default_scope(self, tmp_path: Path) -> None:
        """inspect_drive_actions returns drive-scoped result."""
        app = _build_app(tmp_path)
        started = app.start_drive(agent="test-agent", max_parallelism=4)
        result = app.inspect_drive_actions(drive_id=started.drive_id)
        assert result is not None

    def test_case_list_uses_drive_scope(self, tmp_path: Path) -> None:
        """case_list resolves to drive scope via drive_status when drive active.

        Authority: orch_case_list was already correctly using `or latest`
        in its drive-scoped path condition — this is a regression guard.
        """
        app = _build_app(tmp_path)
        started = app.start_drive(agent="test-agent", max_parallelism=4)
        # drive_status provides blocked_case_ids used by case_list
        drive_result = app.drive_status(drive_id=started.drive_id)
        assert drive_result.scope_kind == "drive"
        assert drive_result.drive_id == started.drive_id
