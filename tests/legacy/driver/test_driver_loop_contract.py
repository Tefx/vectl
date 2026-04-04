"""Contract tests for driver loop and entrypoint surfaces.

These tests pin only signatures, exported surfaces, and bounded deferrals.
They intentionally avoid business/runtime behavior.
"""

from __future__ import annotations

import inspect
from pathlib import Path

import src.vectl.driver.__main__ as driver_main
import src.vectl.driver.events.emitter as event_emitter
import src.vectl.driver.entrypoint as entrypoint
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
        "context",
    ]
    assert list(inspect.signature(loop.handle_complete).parameters) == [
        "action",
        "state",
        "judge",
        "session_pool",
        "observer",
        "plan_path",
        "context",
    ]
    assert list(inspect.signature(loop.reconcile).parameters) == [
        "completed",
        "context",
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


def test_loop_exposes_registry_backed_typed_event_helpers() -> None:
    assert loop.emit_decide is event_emitter.emit_decide
    assert loop.emit_final is event_emitter.emit_final
    assert loop.emit_step_completed is event_emitter.emit_step_completed
    assert loop.LOOP_EVENT_HELPER_DEFS == ("DECIDE", "FINAL", "STEP_COMPLETED")


def test_entrypoint_contract_is_pinned() -> None:
    assert list(inspect.signature(driver_main.main).parameters) == ["argv"]
    assert driver_main.DEFAULT_DRIVER_CONFIG_PATH == Path("driver.yaml")


def test_module_entrypoint_delegates_to_shared_adapter_and_loop(
    monkeypatch, tmp_path: Path
) -> None:
    called: dict[str, Path] = {}

    async def fake_run(config_path: Path) -> None:
        called["config"] = config_path

    monkeypatch.setattr(loop, "run", fake_run)
    adapter = entrypoint.get_runtime_adapter()
    config_path = tmp_path / "driver.yaml"

    exit_code = entrypoint.run_driver_module_entrypoint(
        argv=[str(config_path)],
        adapter=adapter,
    )

    assert exit_code == 0
    assert called["config"] == config_path.resolve()


def test_drive_entrypoint_delegates_to_shared_adapter_and_loop(monkeypatch, tmp_path: Path) -> None:
    called: dict[str, Path] = {}

    async def fake_run(config_path: Path) -> None:
        called["config"] = config_path

    monkeypatch.setattr(loop, "run", fake_run)
    adapter = entrypoint.get_runtime_adapter()
    config_path = tmp_path / "driver.yaml"

    exit_code = entrypoint.run_drive_cli_entrypoint(config=config_path, adapter=adapter)

    assert exit_code == 0
    assert called["config"] == config_path.resolve()
