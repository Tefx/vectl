"""
Drive config extension, frozen config provenance, canonical event envelopes,
drive event families, replay-safe projections, and artifact schema wiring.

Authority:
    - docs/ORCHESTRATION-PLANE-CLI-CONFIG-OBSERVABILITY-DESIGN.md §8.4.1, §8.7, §8.8, §9.3, §10.4
    - docs/RFC-orch-drive.md §8.1, §8.2, §8.4, §16.3, §16.4

Verification:
    Main path: config/event/projection tests pass.
    Failure path: regressions prove event chain integrity, frozen config
    provenance, and replay semantics hold under restart and mixed
    child-run timelines.

Step: orch_drive_surfaces.config-and-observability-upgrades
"""

from __future__ import annotations

import json
import time
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import pytest
import yaml

from vectl.orchestration.config import (
    DEFAULT_DRIVE_COLLECT_POLL_INTERVAL_MS,
    DEFAULT_DRIVE_MAX_PARALLELISM,
    DEFAULT_DRIVE_PLANNER_TIMEOUT_SECONDS,
    DEFAULT_DRIVE_RESOLVER_TIMEOUT_SECONDS,
    DriveConfig,
    FrozenConfigSnapshot,
    OrchestrationConfig,
    build_drive_config_provenance,
    freeze_config,
    freeze_drive_config,
    validate_orchestration_config,
    write_frozen_snapshot,
    load_frozen_snapshot,
)
from vectl.orchestration.contracts import (
    ChildRunKind,
    ChildRunRef,
    ChildRunStatus,
    DriveBarrier,
    DriveConfigFrozen,
    DriveRecord,
    DriveStatus,
)
from vectl.orchestration.events import (
    CANONICAL_EVENT_REGISTRY,
    DRIVE_EVENT_KINDS,
    DriveEventEnvelope,
    DriveEventKind,
    EventCorruptionError,
    EventValidationError,
    OrchestrationEventEnvelope,
    build_drive_event,
    validate_drive_event_chain,
)
from vectl.orchestration.projections import (
    DriveProjection,
    FileProjectionReplay,
    ReplayResult,
    replay_drive_events,
)
from vectl.orchestration.tool_registry import (
    CANONICAL_TOOL_FAMILIES,
    validate_tool_allowlist,
    validate_tool_allowlist_entry,
)


# =====================================================================
# Drive Config Extension Tests
# =====================================================================


class TestDriveConfig:
    """DriveConfig model and validation per §8.4.1."""

    def test_default_values(self) -> None:
        """DriveConfig defaults match spec §8.4.1."""
        cfg = DriveConfig()
        assert cfg.max_parallelism == 4
        assert cfg.collect_poll_interval_ms == 250
        assert cfg.resolver_timeout_seconds == 300.0
        assert cfg.planner_timeout_seconds == 300.0

    def test_custom_values(self) -> None:
        """DriveConfig accepts custom valid values."""
        cfg = DriveConfig(max_parallelism=8, collect_poll_interval_ms=500)
        assert cfg.max_parallelism == 8
        assert cfg.collect_poll_interval_ms == 500

    def test_in_orchestration_config(self) -> None:
        """OrchestrationConfig includes DriveConfig fragment."""
        cfg = OrchestrationConfig()
        assert isinstance(cfg.drive, DriveConfig)
        assert cfg.drive.max_parallelism == DEFAULT_DRIVE_MAX_PARALLELISM

    def test_config_round_trip_yaml(self, tmp_path: Path) -> None:
        """DriveConfig survives YAML round-trip serialization."""
        original = OrchestrationConfig()
        snapshot_path = tmp_path / "config.snapshot.yaml"
        snapshot_path.parent.mkdir(parents=True, exist_ok=True)

        from vectl.orchestration.config import _config_to_dict, _dict_to_config

        data = _config_to_dict(original)
        assert "drive" in data["orchestration"]
        assert data["orchestration"]["drive"]["max_parallelism"] == 4

        round_tripped = _dict_to_config(data)
        assert round_tripped.drive.max_parallelism == original.drive.max_parallelism
        assert (
            round_tripped.drive.collect_poll_interval_ms == original.drive.collect_poll_interval_ms
        )
        assert (
            round_tripped.drive.resolver_timeout_seconds == original.drive.resolver_timeout_seconds
        )
        assert round_tripped.drive.planner_timeout_seconds == original.drive.planner_timeout_seconds


class TestDriveConfigValidation:
    """DriveConfig validation rules per §8.5, §8.4.1."""

    def test_valid_config_no_errors(self) -> None:
        """Default OrchestrationConfig (with DriveConfig) passes validation."""
        errors = validate_orchestration_config(OrchestrationConfig())
        drive_errors = [e for e in errors if e.field.startswith("drive.")]
        assert drive_errors == [], f"unexpected drive validation errors: {drive_errors}"

    def test_max_parallelism_below_minimum(self) -> None:
        """max_parallelism < 1 fails validation."""
        cfg = OrchestrationConfig(drive=DriveConfig(max_parallelism=0))
        errors = validate_orchestration_config(cfg)
        drive_errors = [e for e in errors if e.field == "drive.max_parallelism"]
        assert len(drive_errors) == 1
        assert "between 1 and 32" in drive_errors[0].reason

    def test_max_parallelism_above_maximum(self) -> None:
        """max_parallelism > 32 fails validation."""
        cfg = OrchestrationConfig(drive=DriveConfig(max_parallelism=33))
        errors = validate_orchestration_config(cfg)
        drive_errors = [e for e in errors if e.field == "drive.max_parallelism"]
        assert len(drive_errors) == 1

    def test_max_parallelism_boundary_min(self) -> None:
        """max_parallelism = 1 passes validation."""
        cfg = OrchestrationConfig(drive=DriveConfig(max_parallelism=1))
        errors = validate_orchestration_config(cfg)
        drive_errors = [e for e in errors if e.field == "drive.max_parallelism"]
        assert drive_errors == []

    def test_max_parallelism_boundary_max(self) -> None:
        """max_parallelism = 32 passes validation."""
        cfg = OrchestrationConfig(drive=DriveConfig(max_parallelism=32))
        errors = validate_orchestration_config(cfg)
        drive_errors = [e for e in errors if e.field == "drive.max_parallelism"]
        assert drive_errors == []

    def test_collect_poll_interval_zero(self) -> None:
        """collect_poll_interval_ms = 0 fails validation."""
        cfg = OrchestrationConfig(drive=DriveConfig(collect_poll_interval_ms=0))
        errors = validate_orchestration_config(cfg)
        drive_errors = [e for e in errors if e.field == "drive.collect_poll_interval_ms"]
        assert len(drive_errors) == 1

    def test_resolver_timeout_zero(self) -> None:
        """resolver_timeout_seconds = 0 fails validation."""
        cfg = OrchestrationConfig(drive=DriveConfig(resolver_timeout_seconds=0))
        errors = validate_orchestration_config(cfg)
        drive_errors = [e for e in errors if e.field == "drive.resolver_timeout_seconds"]
        assert len(drive_errors) == 1

    def test_planner_timeout_zero(self) -> None:
        """planner_timeout_seconds = 0 fails validation."""
        cfg = OrchestrationConfig(drive=DriveConfig(planner_timeout_seconds=0))
        errors = validate_orchestration_config(cfg)
        drive_errors = [e for e in errors if e.field == "drive.planner_timeout_seconds"]
        assert len(drive_errors) == 1


# =====================================================================
# Frozen Config Provenance Tests
# =====================================================================


class TestFrozenConfigProvenance:
    """Frozen config provenance tracking per §8.7, §8.8."""

    def test_freeze_config_returns_drive_provenance(self) -> None:
        """freeze_config populates drive_provenance field."""
        config = OrchestrationConfig()
        snapshot = freeze_config(config)
        assert isinstance(snapshot.drive_provenance, dict)
        assert "drive.max_parallelism" in snapshot.drive_provenance
        assert "drive.collect_poll_interval_ms" in snapshot.drive_provenance

    def test_default_provenance_source(self) -> None:
        """Default drive config values get 'default' provenance."""
        config = OrchestrationConfig()
        provenance = build_drive_config_provenance(config)
        assert provenance["drive.max_parallelism"] == "default"
        assert provenance["drive.collect_poll_interval_ms"] == "default"

    def test_custom_provenance_source(self) -> None:
        """Non-default drive config values get 'file' provenance."""
        config = OrchestrationConfig(drive=DriveConfig(max_parallelism=8))
        provenance = build_drive_config_provenance(config)
        assert provenance["drive.max_parallelism"] == "file"

    def test_drift_detection(self) -> None:
        """Drift is detected when frozen values differ from ambient."""
        frozen = OrchestrationConfig(drive=DriveConfig(max_parallelism=2))
        ambient = OrchestrationConfig(drive=DriveConfig(max_parallelism=8))
        provenance = build_drive_config_provenance(frozen, ambient_config=ambient)
        assert provenance["drive.max_parallelism"] == "drift"

    def test_no_drift_when_equal(self) -> None:
        """No drift when frozen and ambient values match."""
        frozen = OrchestrationConfig(drive=DriveConfig(max_parallelism=4))
        ambient = OrchestrationConfig(drive=DriveConfig(max_parallelism=4))
        provenance = build_drive_config_provenance(frozen, ambient_config=ambient)
        assert provenance["drive.max_parallelism"] == "default"

    def test_freeze_drive_config(self) -> None:
        """freeze_drive_config produces DriveConfigFrozen from OrchestrationConfig."""
        config = OrchestrationConfig(drive=DriveConfig(max_parallelism=2))
        frozen = freeze_drive_config(config, drive_id="drv_test")
        assert isinstance(frozen, DriveConfigFrozen)
        assert frozen.drive_id == "drv_test"
        assert frozen.max_parallelism == 2
        assert frozen.control_idle_poll_interval_ms == config.control.idle_poll_interval_ms
        assert frozen.frozen_at > 0.0

    def test_frozen_snapshot_round_trip(self, tmp_path: Path) -> None:
        """Frozen snapshot survives write/load round-trip with drive config."""
        original = OrchestrationConfig(drive=DriveConfig(max_parallelism=2))
        run_dir = tmp_path / "runs" / "run_001"
        run_dir.mkdir(parents=True, exist_ok=True)

        path = write_frozen_snapshot(original, run_dir)
        loaded = load_frozen_snapshot(path)

        assert loaded.drive.max_parallelism == 2
        assert loaded.drive.collect_poll_interval_ms == original.drive.collect_poll_interval_ms


# =====================================================================
# Drive Event Family Tests
# =====================================================================


class TestDriveEventRegistry:
    """Drive event kinds registered in canonical event registry per §10.4."""

    def test_all_drive_kinds_in_registry(self) -> None:
        """All required drive event kinds are in CANONICAL_EVENT_REGISTRY."""
        for kind in DRIVE_EVENT_KINDS:
            assert kind in CANONICAL_EVENT_REGISTRY, f"missing drive event kind: {kind}"

    def test_drive_started_schema(self) -> None:
        """drive_started has required drive_id and plan_path keys."""
        schema = CANONICAL_EVENT_REGISTRY["drive_started"]
        assert "drive_id" in schema.required_payload_keys
        assert "plan_path" in schema.required_payload_keys

    def test_child_run_admitted_schema(self) -> None:
        """child_run_admitted has required drive_id, run_id, and kind keys."""
        schema = CANONICAL_EVENT_REGISTRY["child_run_admitted"]
        assert "drive_id" in schema.required_payload_keys
        assert "run_id" in schema.required_payload_keys
        assert "kind" in schema.required_payload_keys

    def test_drive_final_schema(self) -> None:
        """drive_final has required drive_id and final_status keys."""
        schema = CANONICAL_EVENT_REGISTRY["drive_final"]
        assert "drive_id" in schema.required_payload_keys
        assert "final_status" in schema.required_payload_keys

    def test_no_missing_drive_family_members(self) -> None:
        """All 11 required drive event kinds are present (§10.4)."""
        expected = {
            "drive_started",
            "drive_status_changed",
            "drive_barrier_entered",
            "drive_barrier_cleared",
            "planner_invoked",
            "planner_applied",
            "resolver_invoked",
            "resolver_returned",
            "child_run_admitted",
            "child_run_final",
            "drive_final",
        }
        actual = set(DRIVE_EVENT_KINDS)
        missing = expected - actual
        assert missing == set(), f"missing drive event family members: {missing}"
        extra = actual - expected
        assert extra == set(), f"unexpected extra drive event kinds: {extra}"


class TestDriveEventEnvelope:
    """DriveEventEnvelope construction and integrity per §9.3, §10.4."""

    def test_basic_construction(self) -> None:
        """DriveEventEnvelope constructs with required fields."""
        env = DriveEventEnvelope(
            kind="drive_started",
            timestamp=datetime.now(timezone.utc),
            drive_id="drv_01K",
            payload={"drive_id": "drv_01K", "plan_path": "/repo/plan.yaml"},
        )
        assert env.drive_id == "drv_01K"
        assert env.entry_hash is not None

    def test_missing_drive_id_raises(self) -> None:
        """DriveEventEnvelope with empty drive_id raises validation error."""
        with pytest.raises(EventValidationError, match="drive_id is required"):
            DriveEventEnvelope(
                kind="drive_started",
                timestamp=datetime.now(timezone.utc),
                drive_id="",
                payload={"drive_id": "", "plan_path": "/repo/plan.yaml"},
            )

    def test_naive_timestamp_raises(self) -> None:
        """DriveEventEnvelope with naive timestamp raises validation error."""
        with pytest.raises(EventValidationError, match="timezone-aware"):
            DriveEventEnvelope(
                kind="drive_started",
                timestamp=datetime.now(),
                drive_id="drv_01K",
                payload={"drive_id": "drv_01K", "plan_path": "/repo/plan.yaml"},
            )

    def test_entry_hash_deterministic(self) -> None:
        """Same input produces same entry_hash."""
        ts = datetime(2026, 4, 15, 12, 0, 0, tzinfo=timezone.utc)
        env1 = DriveEventEnvelope(
            kind="drive_started",
            timestamp=ts,
            drive_id="drv_01K",
            payload={"drive_id": "drv_01K", "plan_path": "/repo/plan.yaml"},
            seq=1,
        )
        env2 = DriveEventEnvelope(
            kind="drive_started",
            timestamp=ts,
            drive_id="drv_01K",
            payload={"drive_id": "drv_01K", "plan_path": "/repo/plan.yaml"},
            seq=1,
        )
        assert env1.entry_hash == env2.entry_hash

    def test_different_payload_different_hash(self) -> None:
        """Different payload produces different entry_hash."""
        ts = datetime(2026, 4, 15, 12, 0, 0, tzinfo=timezone.utc)
        env1 = DriveEventEnvelope(
            kind="drive_started",
            timestamp=ts,
            drive_id="drv_01K",
            payload={"drive_id": "drv_01K", "plan_path": "/repo/plan.yaml"},
            seq=1,
        )
        env2 = DriveEventEnvelope(
            kind="drive_started",
            timestamp=ts,
            drive_id="drv_01K",
            payload={"drive_id": "drv_01K", "plan_path": "/other/plan.yaml"},
            seq=1,
        )
        assert env1.entry_hash != env2.entry_hash

    def test_to_orchestration_envelope(self) -> None:
        """DriveEventEnvelope converts to OrchestrationEventEnvelope."""
        env = DriveEventEnvelope(
            kind="drive_started",
            timestamp=datetime.now(timezone.utc),
            drive_id="drv_01K",
            payload={"drive_id": "drv_01K", "plan_path": "/repo/plan.yaml"},
        )
        orch = env.to_orchestration_envelope()
        assert isinstance(orch, OrchestrationEventEnvelope)
        assert orch.drive_id == "drv_01K"

    def test_with_integrity(self) -> None:
        """with_integrity reassigns seq and prev_hash."""
        env = DriveEventEnvelope(
            kind="drive_started",
            timestamp=datetime.now(timezone.utc),
            drive_id="drv_01K",
            payload={"drive_id": "drv_01K", "plan_path": "/repo/plan.yaml"},
        )
        prev_hash_value = "a" * 64  # Valid SHA-256 hex format
        with_integrity = env.with_integrity(seq=5, prev_hash=prev_hash_value)
        assert with_integrity.seq == 5
        assert with_integrity.prev_hash == prev_hash_value
        assert with_integrity.entry_hash is not None


class TestBuildDriveEvent:
    """build_drive_event convenience constructor per §16.3."""

    def test_basic_event(self) -> None:
        """build_drive_event creates a valid DriveEventEnvelope."""
        env = build_drive_event(
            "drive_started",
            "drv_01K",
            {"drive_id": "drv_01K", "plan_path": "/repo/plan.yaml"},
        )
        assert env.kind == "drive_started"
        assert env.drive_id == "drv_01K"

    def test_with_optional_fields(self) -> None:
        """build_drive_event accepts optional child_run_id and step_id."""
        env = build_drive_event(
            "child_run_admitted",
            "drv_01K",
            {"drive_id": "drv_01K", "run_id": "run_01A", "kind": "step"},
            child_run_id="run_01A",
            step_id="core.compile",
        )
        assert env.child_run_id == "run_01A"
        assert env.step_id == "core.compile"


# =====================================================================
# Event Chain Integrity Tests (Hash/Seq Drift)
# =====================================================================


class TestDriveEventChainIntegrity:
    """Event chain integrity: seq/prev_hash/entry_hash per §9.3."""

    def test_valid_chain(self) -> None:
        """Valid chain with monotonic seq and correct hash links."""
        ts = datetime(2026, 4, 15, 12, 0, 0, tzinfo=timezone.utc)
        events: list[DriveEventEnvelope] = []
        prev_hash: str | None = None
        for seq in range(1, 4):
            env = DriveEventEnvelope(
                kind="drive_status_changed",
                timestamp=ts,
                drive_id="drv_01K",
                payload={"drive_id": "drv_01K", "status": "running"},
                seq=seq,
                prev_hash=prev_hash,
            )
            events.append(env)
            prev_hash = env.entry_hash
        # Should not raise
        validate_drive_event_chain(events)

    def test_seq_drift_detected(self) -> None:
        """Non-monotonic seq is detected in chain validation."""
        ts = datetime(2026, 4, 15, 12, 0, 0, tzinfo=timezone.utc)
        env1 = DriveEventEnvelope(
            kind="drive_status_changed",
            timestamp=ts,
            drive_id="drv_01K",
            payload={"drive_id": "drv_01K", "status": "running"},
            seq=1,
        )
        env2 = DriveEventEnvelope(
            kind="drive_status_changed",
            timestamp=ts,
            drive_id="drv_01K",
            payload={"drive_id": "drv_01K", "status": "paused"},
            seq=3,  # Gap: expected 2
            prev_hash=env1.entry_hash,
        )
        with pytest.raises(EventCorruptionError, match="non-monotonic seq"):
            validate_drive_event_chain([env1, env2])

    def test_prev_hash_drift_detected(self) -> None:
        """prev_hash mismatch is detected in chain validation."""
        ts = datetime(2026, 4, 15, 12, 0, 0, tzinfo=timezone.utc)
        env1 = DriveEventEnvelope(
            kind="drive_status_changed",
            timestamp=ts,
            drive_id="drv_01K",
            payload={"drive_id": "drv_01K", "status": "running"},
            seq=1,
        )
        env2 = DriveEventEnvelope(
            kind="drive_status_changed",
            timestamp=ts,
            drive_id="drv_01K",
            payload={"drive_id": "drv_01K", "status": "paused"},
            seq=2,
            prev_hash="deadbeef" + "0" * 56,  # Wrong hash
        )
        with pytest.raises(EventCorruptionError, match="prev_hash mismatch"):
            validate_drive_event_chain([env1, env2])

    def test_empty_chain_valid(self) -> None:
        """Empty chain is valid."""
        validate_drive_event_chain([])

    def test_orchestration_envelope_chain_preserves_drive_id(self) -> None:
        """OrchestrationEventEnvelope chain includes drive_id for drive events."""
        env = DriveEventEnvelope(
            kind="drive_started",
            timestamp=datetime.now(timezone.utc),
            drive_id="drv_01K",
            payload={"drive_id": "drv_01K", "plan_path": "/repo/plan.yaml"},
            seq=1,
        )
        orch_env = env.to_orchestration_envelope()
        assert orch_env.drive_id == "drv_01K"
        record = orch_env.to_record()
        assert record["drive_id"] == "drv_01K"


# =====================================================================
# Drive Projection Replay Tests
# =====================================================================


class TestDriveProjectionReplay:
    """Replay-safe projections after restart per §9.2, §16.4."""

    def test_replay_drive_events_persists_artifacts(self, tmp_path: Path) -> None:
        """replay_drive_events produces drive summary and frontier artifacts."""
        ts = datetime(2026, 4, 15, 12, 0, 0, tzinfo=timezone.utc)
        events = (
            OrchestrationEventEnvelope(
                kind="run_started",
                timestamp=ts,
                step_id="core.compile",
                payload={"run_id": "run_01", "plan_path": "/repo/plan.yaml"},
                seq=1,
            ),
            OrchestrationEventEnvelope(
                kind="drive_started",
                timestamp=ts,
                step_id=None,
                payload={"drive_id": "drv_01", "plan_path": "/repo/plan.yaml"},
                drive_id="drv_01",
                seq=2,
            ),
        )
        result = replay_drive_events(events, tmp_path, "drv_01")
        assert isinstance(result, ReplayResult)

        drive_summary_path = tmp_path / "drive" / "summary.json"
        drive_frontier_path = tmp_path / "drive" / "frontier.json"
        assert drive_summary_path.exists()
        assert drive_frontier_path.exists()

        summary = json.loads(drive_summary_path.read_text())
        assert summary["drive_id"] == "drv_01"

    def test_projection_handles_drive_events(self, tmp_path: Path) -> None:
        """Projection state includes drive event data."""
        ts = datetime(2026, 4, 15, 12, 0, 0, tzinfo=timezone.utc)
        events = (
            OrchestrationEventEnvelope(
                kind="child_run_admitted",
                timestamp=ts,
                step_id="core.compile",
                payload={"drive_id": "drv_01", "run_id": "run_01", "kind": "step"},
                drive_id="drv_01",
                seq=1,
            ),
            OrchestrationEventEnvelope(
                kind="child_run_final",
                timestamp=ts,
                step_id="core.compile",
                payload={"drive_id": "drv_01", "run_id": "run_01", "status": "success"},
                drive_id="drv_01",
                seq=2,
            ),
        )
        replay = FileProjectionReplay(events=events, artifact_root=tmp_path, run_id="drv_01")
        states = replay.replay()
        assert len(states) == 1
        latest = states[0]
        assert latest.dispatch_count == 0  # drive events, not control_dispatch

    def test_drive_barrier_events_affect_case_count(self, tmp_path: Path) -> None:
        """drive_barrier_entered with case_ids increments open_case_count."""
        ts = datetime(2026, 4, 15, 12, 0, 0, tzinfo=timezone.utc)
        events = (
            OrchestrationEventEnvelope(
                kind="drive_barrier_entered",
                timestamp=ts,
                step_id=None,
                payload={
                    "drive_id": "drv_01",
                    "reason": "merge_conflict",
                    "case_ids": ["case_01", "case_02"],
                },
                drive_id="drv_01",
                seq=1,
            ),
        )
        replay = FileProjectionReplay(events=events, artifact_root=tmp_path, run_id="drv_01")
        states = replay.replay()
        assert states[0].open_case_count == 2

    def test_projection_replay_after_restart(self, tmp_path: Path) -> None:
        """Projection produces identical state when replaying same events twice (restart sim)."""
        ts = datetime(2026, 4, 15, 12, 0, 0, tzinfo=timezone.utc)
        events = (
            OrchestrationEventEnvelope(
                kind="run_started",
                timestamp=ts,
                step_id="core.compile",
                payload={"run_id": "run_01", "plan_path": "/repo/plan.yaml"},
                seq=1,
            ),
            OrchestrationEventEnvelope(
                kind="control_dispatch",
                timestamp=ts,
                step_id="core.compile",
                payload={"step_id": "core.compile", "agent": "python-executor"},
                seq=2,
            ),
            OrchestrationEventEnvelope(
                kind="drive_status_changed",
                timestamp=ts,
                payload={"drive_id": "drv_01", "status": "running"},
                drive_id="drv_01",
                seq=3,
            ),
        )
        # First replay
        replay1 = FileProjectionReplay(events=events, artifact_root=tmp_path / "r1")
        states1 = replay1.replay()

        # Second replay (simulating restart)
        replay2 = FileProjectionReplay(events=events, artifact_root=tmp_path / "r2")
        states2 = replay2.replay()

        s1, s2 = states1[0], states2[0]
        assert s1.dispatch_count == s2.dispatch_count == 1
        assert s1.last_event_seq == s2.last_event_seq == 3
        assert s1.active_execution_count == s2.active_execution_count


# =====================================================================
# Drive Projection from Records Tests
# =====================================================================


class TestDriveProjectionFromRecords:
    """DriveProjection rebuilt from DriveRecord + ChildRunRef per §9.2."""

    def test_projection_from_records(self) -> None:
        """_rebuild_drive_projection_from_records produces DriveProjection."""
        from vectl.orchestration.projections import _rebuild_drive_projection_from_records

        record = DriveRecord(
            drive_id="drv_01",
            plan_path="/repo/plan.yaml",
            status="running",
            max_parallelism=4,
            frontier_step_ids=("core.compile", "core.test"),
            operator_pause_state="active",
            summary="2 active runs",
        )
        active_refs: tuple[ChildRunRef, ...] = (
            ChildRunRef(
                run_id="run_01",
                drive_id="drv_01",
                kind="step",
                status="running",
                step_id="core.compile",
            ),
        )
        all_refs = active_refs + (
            ChildRunRef(
                run_id="run_02",
                drive_id="drv_01",
                kind="step",
                status="success",
                step_id="core.compile",
            ),
        )
        projection = _rebuild_drive_projection_from_records(record, all_refs, active_refs)
        assert isinstance(projection, DriveProjection)
        assert projection.drive_id == "drv_01"
        assert projection.active_child_run_ids == ("run_01",)
        assert projection.frontier_step_ids == ("core.compile", "core.test")
        assert len(projection.active_step_child_runs) == 1  # core.compile has 1 active

    def test_projection_with_barrier(self) -> None:
        """Projection preserves barrier state from DriveRecord."""
        from vectl.orchestration.projections import _rebuild_drive_projection_from_records

        barrier = DriveBarrier(
            reason="merge_conflict",
            entered_at=1776124820.0,
            case_ids=("case_01",),
        )
        record = DriveRecord(
            drive_id="drv_01",
            plan_path="/repo/plan.yaml",
            status="resolving",
            barrier=barrier,
        )
        projection = _rebuild_drive_projection_from_records(record, (), ())
        assert projection.barrier is not None
        assert isinstance(projection.barrier, DriveBarrier)
        assert projection.barrier.reason == "merge_conflict"

    def test_projection_active_from_child_runs_not_record(self) -> None:
        """Active child IDs come from child run refs, not DriveRecord.active_child_run_ids."""
        from vectl.orchestration.projections import _rebuild_drive_projection_from_records

        # DriveRecord says run_01 and run_02 are active
        record = DriveRecord(
            drive_id="drv_01",
            plan_path="/repo/plan.yaml",
            status="running",
            active_child_run_ids=("run_01", "run_02"),
        )
        # But child run index says only run_01 is active, run_02 is success
        active_refs = (
            ChildRunRef(
                run_id="run_01", drive_id="drv_01", kind="step", status="running", step_id="core.a"
            ),
        )
        all_refs = active_refs + (
            ChildRunRef(
                run_id="run_02", drive_id="drv_01", kind="step", status="success", step_id="core.b"
            ),
        )
        projection = _rebuild_drive_projection_from_records(record, all_refs, active_refs)
        # Projection must use child run refs, not the stale record
        assert projection.active_child_run_ids == ("run_01",)


# =====================================================================
# Tool Registry Drive Family Tests
# =====================================================================


class TestToolRegistryDriveFamily:
    """Drive tool family in canonical registry per §8.4."""

    def test_drive_family_registered(self) -> None:
        """'drive' is in CANONICAL_TOOL_FAMILIES."""
        assert "drive" in CANONICAL_TOOL_FAMILIES

    def test_drive_tools_validated(self) -> None:
        """Drive tool names validate against the registry."""
        errors = validate_tool_allowlist_entry(
            "drive",
            ("drive_status", "drive_events"),
        )
        assert errors == []

    def test_unknown_drive_tool_rejected(self) -> None:
        """Unknown tool in drive family is rejected."""
        errors = validate_tool_allowlist_entry(
            "drive",
            ("drive_status", "nonexistent_drive_tool"),
        )
        assert len(errors) == 1
        assert errors[0].tool == "nonexistent_drive_tool"

    def test_drive_family_in_allowlist(self) -> None:
        """Drive family can be included in resolver allowlist."""
        errors = validate_tool_allowlist(
            {
                "core": ("status",),
                "drive": ("drive_status",),
            }
        )
        assert errors == []


# =====================================================================
# Artifact Schema Wiring Tests
# =====================================================================


class TestArtifactSchemaWiring:
    """Artifact schemas for cases and step results per §9.3.3, §9.3.4."""

    def test_step_artifact_from_projection(self, tmp_path: Path) -> None:
        """Projection tracks step-scoped derived artifacts."""
        ts = datetime(2026, 4, 15, 12, 0, 0, tzinfo=timezone.utc)
        events = (
            OrchestrationEventEnvelope(
                kind="runtime_start",
                timestamp=ts,
                step_id="core.compile",
                payload={"step_id": "core.compile", "task_id": "task_01"},
                seq=1,
            ),
        )
        replay = FileProjectionReplay(events=events, artifact_root=tmp_path)
        result = replay.replay_result()
        # Step artifacts accessible from result
        assert isinstance(result.step_artifacts, tuple)

    def test_case_artifact_from_projection(self, tmp_path: Path) -> None:
        """Projection tracks case-scoped derived artifacts."""
        ts = datetime(2026, 4, 15, 12, 0, 0, tzinfo=timezone.utc)
        events = (
            OrchestrationEventEnvelope(
                kind="operator_case_opened",
                timestamp=ts,
                step_id="core.compile",
                payload={"case_id": "case_01", "step_id": "core.compile"},
                seq=1,
            ),
        )
        replay = FileProjectionReplay(events=events, artifact_root=tmp_path)
        result = replay.replay_result()
        assert isinstance(result.case_artifacts, tuple)

    def test_drive_artifacts_persisted(self, tmp_path: Path) -> None:
        """Drive-specific artifacts persisted under drive/ subdirectory."""
        ts = datetime(2026, 4, 15, 12, 0, 0, tzinfo=timezone.utc)
        events = (
            OrchestrationEventEnvelope(
                kind="drive_started",
                timestamp=ts,
                payload={"drive_id": "drv_01", "plan_path": "/repo/plan.yaml"},
                drive_id="drv_01",
                seq=1,
            ),
            OrchestrationEventEnvelope(
                kind="drive_status_changed",
                timestamp=ts,
                payload={"drive_id": "drv_01", "status": "running"},
                drive_id="drv_01",
                seq=2,
            ),
        )
        result = replay_drive_events(events, tmp_path, "drv_01")
        drive_dir = tmp_path / "drive"
        assert drive_dir.exists()
        assert (drive_dir / "summary.json").exists()
        assert (drive_dir / "frontier.json").exists()

        summary = json.loads((drive_dir / "summary.json").read_text())
        assert summary["drive_id"] == "drv_01"
        assert summary["status"] is not None


# =====================================================================
# Config Precedence Drift Tests
# =====================================================================


class TestConfigPrecedenceDrift:
    """Config precedence drift between frozen and ambient values per §8.7."""

    def test_drift_on_max_parallelism(self) -> None:
        """Drift detected when max_parallelism differs between frozen and ambient."""
        frozen = OrchestrationConfig(drive=DriveConfig(max_parallelism=2))
        ambient = OrchestrationConfig(drive=DriveConfig(max_parallelism=8))
        provenance = build_drive_config_provenance(frozen, ambient_config=ambient)
        assert provenance["drive.max_parallelism"] == "drift"

    def test_no_drift_on_matching_values(self) -> None:
        """No drift when frozen and ambient match for all drive fields."""
        frozen = OrchestrationConfig()
        ambient = OrchestrationConfig()
        provenance = build_drive_config_provenance(frozen, ambient_config=ambient)
        for key, source in provenance.items():
            assert source != "drift", f"unexpected drift for {key}"

    def test_partial_drift(self) -> None:
        """Only drifted fields get 'drift' provenance; others retain source."""
        frozen = OrchestrationConfig(drive=DriveConfig(max_parallelism=2))
        ambient = OrchestrationConfig(drive=DriveConfig(max_parallelism=8))
        provenance = build_drive_config_provenance(frozen, ambient_config=ambient)
        assert provenance["drive.max_parallelism"] == "drift"
        # Other fields should not be drift since they match defaults
        assert provenance["drive.collect_poll_interval_ms"] != "drift"

    def test_freeze_config_snapshot_includes_drive_provenance(self) -> None:
        """Freezing config includes drive_provenance in snapshot."""
        config = OrchestrationConfig(drive=DriveConfig(max_parallelism=2))
        snapshot = freeze_config(config)
        assert "drive.max_parallelism" in snapshot.drive_provenance
        assert snapshot.drive_provenance["drive.max_parallelism"] == "file"

    def test_drive_env_var_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """VECTL_ORCH_DRIVE_MAX_PARALLELISM overrides drive config."""
        from vectl.orchestration.config import _flatten_env_vars, _apply_env_overrides

        monkeypatch.setenv("VECTL_ORCH_DRIVE_MAX_PARALLELISM", "16")
        env_vars = _flatten_env_vars()
        # The key should be drive.max_parallelism
        assert "drive.max_parallelism" in env_vars
        assert env_vars["drive.max_parallelism"] == "16"
