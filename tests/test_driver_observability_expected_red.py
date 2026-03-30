"""Expected-RED tests for advanced driver observability boundaries.

Source authority:
- step `driver-enhancement-observability.design-and-test`
- docs/DRIVER-ARCHITECTURE.md Section 2.11 (loop/runtime ownership)
- docs/DRIVER-CONTINUITY-FOUNDATION.md Section 7 (minimum journal boundaries)

These tests intentionally fail until downstream implementation step
`driver-enhancement-observability.impl` wires runtime emission paths.
"""

from __future__ import annotations

import inspect

from src.vectl.driver import loop
from src.vectl.driver.events import emitter
from src.vectl.driver.events.registry import EVENT_REGISTRY


def test_observability_contract_exposes_owner_and_gaps() -> None:
    """Design contract must pin owner step and exposed runtime gaps."""
    contract = emitter.ADVANCED_OBSERVABILITY_CONTRACT

    assert contract.source_step_id == "driver-enhancement-observability.design-and-test"
    assert contract.implementation_owner_step == "driver-enhancement-observability.impl"
    assert len(contract.exposed_gaps) == 5


def test_recovery_visibility_schema_preserves_continuity_journal_minimums() -> None:
    """Recovery observability schema must preserve continuity-owned journal minimums."""
    record = EVENT_REGISTRY["RECOVERY_VISIBILITY"]

    assert record.required == (
        "attempt_key",
        "event_kind",
        "recorded_at",
        "runner_name",
        "session_id",
        "step_id",
        "summary",
    )
    assert record.optional == ("recovery_cursor",)
    assert record.owner == "vectl.driver.loop"


def test_lifecycle_visibility_is_wired_from_run_surface_expected_red() -> None:
    """run() must emit lifecycle transitions for startup/shutdown visibility."""
    source = inspect.getsource(loop.run)

    assert "emit_driver_lifecycle" in source


def test_planner_dispatch_progress_visibility_is_wired_expected_red() -> None:
    """Planner dispatch runtime path must emit incremental progress telemetry."""
    source = inspect.getsource(loop.dispatch_planner)

    assert "emit_planner_dispatch_progress" in source


def test_recovery_visibility_is_wired_during_reconcile_expected_red() -> None:
    """reconcile() must emit recovery visibility while handling continuity outcomes."""
    source = inspect.getsource(loop.reconcile)

    assert "emit_recovery_visibility" in source


def test_heartbeat_progress_visibility_is_wired_from_run_loop_expected_red() -> None:
    """Main run loop must emit periodic heartbeat/progress telemetry."""
    source = inspect.getsource(loop.run)

    assert "emit_heartbeat_progress" in source


def test_schema_drift_guard_reports_no_drift_expected_red() -> None:
    """Advanced observability schema drift guard must execute with zero drift."""
    assert emitter.assert_advanced_observability_schema_alignment() == ()
