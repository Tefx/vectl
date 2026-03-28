"""Contract tests for driver loop and entrypoint surfaces.

These tests pin only signatures, exported surfaces, and bounded deferrals.
They intentionally avoid business/runtime behavior.
"""

from __future__ import annotations

import inspect
from pathlib import Path

import src.vectl.driver.__main__ as driver_main
import src.vectl.driver.loop as loop


def test_loop_contract_surfaces_exist() -> None:
    assert inspect.iscoroutinefunction(loop.run)
    assert inspect.iscoroutinefunction(loop.handle_dispatch)
    assert inspect.iscoroutinefunction(loop.handle_complete)
    assert inspect.iscoroutinefunction(loop.reconcile)
    assert inspect.iscoroutinefunction(loop.shutdown)


def test_loop_contract_signatures_are_pinned() -> None:
    assert list(inspect.signature(loop.run).parameters) == ["config_path"]
    assert list(inspect.signature(loop.handle_dispatch).parameters) == [
        "action",
        "state",
        "config",
        "runners",
        "judge",
        "session_pool",
        "observer",
        "plan_path",
    ]
    assert list(inspect.signature(loop.handle_complete).parameters) == [
        "action",
        "state",
        "judge",
        "session_pool",
        "observer",
        "plan_path",
    ]
    assert list(inspect.signature(loop.reconcile).parameters) == [
        "completed",
        "state",
        "judge",
        "session_pool",
        "observer",
        "plan_path",
    ]
    assert list(inspect.signature(loop.shutdown).parameters) == ["state", "observer"]


def test_deferred_replan_branches_are_bounded() -> None:
    branches = {entry.branch for entry in loop.DEFERRED_REPLAN_BRANCHES}
    assert branches == {
        "handle_dispatch.preflight_replan",
        "reconcile.evidence_replan",
        "reconcile.failure_classification_replan",
        "reconcile.escalation_replan",
        "run.startup_recovery_anomaly_replan",
    }
    assert all(
        entry.deferred_to == "driver-judgment-expansion-replan"
        for entry in loop.DEFERRED_REPLAN_BRANCHES
    )


def test_entrypoint_contract_is_pinned() -> None:
    assert list(inspect.signature(driver_main.main).parameters) == ["argv"]
    assert driver_main.DEFAULT_DRIVER_CONFIG_PATH == Path("driver.yaml")
