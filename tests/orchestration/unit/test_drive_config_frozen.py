"""
Lease ownership and frozen drive config persistence tests.

These tests verify:
  - DriveLease creation, persistence, and lookup
  - Same-plan admission blocking (active drive blocks new drive)
  - Planner mutation invalidates unstarted leases cleanly
  - Resume/recover reuses frozen drive parameters instead of ambient config drift
  - Frozen drive config is immutable once written

Authority: docs/RFC-orch-drive.md sections 14.3, 14.4, 8.1, 15.1, 15.2
Step: orch_drive_state_store.lease-and-freeze-persistence
"""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

import pytest

from vectl.orchestration.config import (
    ControlConfig,
    OrchestrationConfig,
    ResolverConfig,
)
from vectl.orchestration.contracts import (
    DriveConfigFrozen,
    DriveLease,
    DriveRecord,
)
from vectl.orchestration.run_store import (
    DriveAdmissionConflictError,
    DriveStore,
    LeaseStatus,
)


# ------------------------------------------------------------------
# DriveLease contract type tests
# ------------------------------------------------------------------


class TestDriveLeaseContract:
    """Test DriveLease contract type."""

    def test_drive_lease_fields(self) -> None:
        """Verify DriveLease has the required fields."""
        from dataclasses import fields

        expected_fields = {
            "drive_id",
            "step_id",
            "run_id",
            "status",
            "created_at",
            "released_at",
            "released_reason",
        }
        actual_fields = {f.name for f in fields(DriveLease)}
        assert actual_fields == expected_fields, (
            f"DriveLease field mismatch. Expected: {expected_fields}, Got: {actual_fields}"
        )

    def test_drive_lease_frozen(self) -> None:
        """Verify DriveLease is frozen (immutable)."""
        import dataclasses

        lease = DriveLease(drive_id="drv_01", step_id="core.verify", run_id="run_01A")
        with pytest.raises(dataclasses.FrozenInstanceError):
            lease.status = "released"  # type: ignore[misc]

    def test_drive_lease_minimal_construction(self) -> None:
        """DriveLease requires drive_id, step_id, run_id; others have defaults."""
        lease = DriveLease(drive_id="drv_01", step_id="core.verify", run_id="run_01A")
        assert lease.drive_id == "drv_01"
        assert lease.step_id == "core.verify"
        assert lease.run_id == "run_01A"
        assert lease.status == "active"
        assert lease.created_at == 0.0
        assert lease.released_at is None
        assert lease.released_reason is None

    def test_drive_lease_full_construction(self) -> None:
        """DriveLease with all fields populated."""
        lease = DriveLease(
            drive_id="drv_01K",
            step_id="core.verify",
            run_id="run_01A",
            status="released",
            created_at=1776124800.0,
            released_at=1776124900.0,
            released_reason="invalidated",
        )
        assert lease.status == "released"
        assert lease.released_reason == "invalidated"

    def test_drive_lease_status_literal(self) -> None:
        """Verify status is Literal["active", "released", "invalidated"]."""
        from dataclasses import fields
        from typing import Literal, get_args, get_origin

        lease_type_map = {f.name: f.type for f in fields(DriveLease)}
        assert get_origin(lease_type_map["status"]) is Literal
        assert set(get_args(lease_type_map["status"])) == {"active", "released", "invalidated"}

    def test_drive_lease_released_reason_literal(self) -> None:
        """Verify released_reason is Literal["completed", "invalidated", "superseded"]."""
        from dataclasses import fields
        from typing import Literal, get_args, get_origin, Union

        lease_type_map = {f.name: f.type for f in fields(DriveLease)}
        # released_reason is Literal[...] | None
        reason_type = lease_type_map["released_reason"]
        # The type is Optional[Literal["completed", "invalidated", "superseded"]]
        # We just check the literal values appear in the string representation
        assert "completed" in str(reason_type)
        assert "invalidated" in str(reason_type)
        assert "superseded" in str(reason_type)


# ------------------------------------------------------------------
# DriveConfigFrozen contract type tests
# ------------------------------------------------------------------


class TestDriveConfigFrozenContract:
    """Test DriveConfigFrozen contract type."""

    def test_drive_config_frozen_fields(self) -> None:
        """Verify DriveConfigFrozen has the required fields."""
        from dataclasses import fields

        expected_fields = {
            "drive_id",
            "max_parallelism",
            "control_idle_poll_interval_ms",
            "control_action_ack_timeout_seconds",
            "resolver_invocation_timeout_seconds",
            "resolver_max_tool_calls_per_invocation",
            "frozen_at",
        }
        actual_fields = {f.name for f in fields(DriveConfigFrozen)}
        assert actual_fields == expected_fields, (
            f"DriveConfigFrozen field mismatch. Expected: {expected_fields}, Got: {actual_fields}"
        )

    def test_drive_config_frozen_frozen(self) -> None:
        """Verify DriveConfigFrozen is frozen (immutable)."""
        import dataclasses

        config = DriveConfigFrozen(drive_id="drv_01")
        with pytest.raises(dataclasses.FrozenInstanceError):
            config.max_parallelism = 8  # type: ignore[misc]

    def test_drive_config_frozen_defaults(self) -> None:
        """Verify DriveConfigFrozen defaults match orchestration config defaults."""
        config = DriveConfigFrozen(drive_id="drv_01")
        assert config.max_parallelism == 4
        assert config.control_idle_poll_interval_ms == 1000
        assert config.control_action_ack_timeout_seconds == 5.0
        assert config.resolver_invocation_timeout_seconds == 600.0
        assert config.resolver_max_tool_calls_per_invocation == 100

    def test_drive_config_frozen_from_orch_config(self, tmp_path: Path) -> None:
        """Verify freeze_drive_config_from_orch_config captures config values."""
        store = DriveStore(store_root=tmp_path)
        orch_config = OrchestrationConfig(
            control=ControlConfig(
                idle_poll_interval_ms=2000,
                action_ack_timeout_seconds=10.0,
            ),
            resolver=ResolverConfig(
                invocation_timeout_seconds=120.0,
                max_tool_calls_per_invocation=200,
            ),
        )
        frozen = store.freeze_drive_config_from_orch_config("drv_01", orch_config)
        assert frozen.drive_id == "drv_01"
        assert frozen.control_idle_poll_interval_ms == 2000
        assert frozen.control_action_ack_timeout_seconds == 10.0
        assert frozen.resolver_invocation_timeout_seconds == 120.0
        assert frozen.resolver_max_tool_calls_per_invocation == 200
        assert frozen.frozen_at > 0.0


# ------------------------------------------------------------------
# Lease persistence tests
# ------------------------------------------------------------------


class TestDriveLeasePersistence:
    """Verify DriveLease JSONL persistence and retrieval."""

    def test_save_and_load_lease(self, tmp_path: Path) -> None:
        """Minimal DriveLease round-trips through persistence."""
        store = DriveStore(store_root=tmp_path)
        lease = DriveLease(
            drive_id="drv_01",
            step_id="core.verify",
            run_id="run_01A",
            status="active",
            created_at=1776124800.0,
        )
        store.save_lease(lease)

        loaded = store.lease_by_run_id("run_01A")
        assert loaded is not None
        assert loaded.drive_id == "drv_01"
        assert loaded.step_id == "core.verify"
        assert loaded.run_id == "run_01A"
        assert loaded.status == "active"
        assert loaded.created_at == 1776124800.0

    def test_lease_update_latest_wins(self, tmp_path: Path) -> None:
        """Later lease entry for same run_id supersedes prior."""
        store = DriveStore(store_root=tmp_path)
        store.save_lease(
            DriveLease(
                drive_id="drv_01",
                step_id="core.verify",
                run_id="run_01",
                status="active",
                created_at=100.0,
            )
        )
        store.save_lease(
            DriveLease(
                drive_id="drv_01",
                step_id="core.verify",
                run_id="run_01",
                status="released",
                created_at=100.0,
                released_at=200.0,
                released_reason="completed",
            )
        )

        loaded = store.lease_by_run_id("run_01")
        assert loaded is not None
        assert loaded.status == "released"
        assert loaded.released_reason == "completed"

    def test_active_leases_for_drive(self, tmp_path: Path) -> None:
        """active_leases_for_drive returns only active leases for a drive."""
        store = DriveStore(store_root=tmp_path)
        store.save_lease(
            DriveLease(drive_id="drv_01", step_id="core.a", run_id="run_01", status="active")
        )
        store.save_lease(
            DriveLease(drive_id="drv_01", step_id="core.b", run_id="run_02", status="active")
        )
        store.save_lease(
            DriveLease(
                drive_id="drv_01",
                step_id="core.c",
                run_id="run_03",
                status="released",
                released_reason="completed",
            )
        )
        store.save_lease(
            DriveLease(drive_id="drv_02", step_id="core.x", run_id="run_04", status="active")
        )

        active_d1 = store.active_leases_for_drive("drv_01")
        active_ids = {l.run_id for l in active_d1}
        assert active_ids == {"run_01", "run_02"}
        # "run_03" is released, not active; "run_04" is for drv_02

    def test_active_lease_for_step(self, tmp_path: Path) -> None:
        """active_lease_for_step returns the lease for a specific step."""
        store = DriveStore(store_root=tmp_path)
        store.save_lease(
            DriveLease(drive_id="drv_01", step_id="core.verify", run_id="run_01", status="active")
        )

        found = store.active_lease_for_step("drv_01", "core.verify")
        assert found is not None
        assert found.step_id == "core.verify"
        assert found.run_id == "run_01"

        # Different step returns None
        not_found = store.active_lease_for_step("drv_01", "core.impl")
        assert not_found is None

    def test_lease_by_run_id_not_found(self, tmp_path: Path) -> None:
        """Lookup on non-existent run_id returns None."""
        store = DriveStore(store_root=tmp_path)
        assert store.lease_by_run_id("nonexistent") is None

    def test_empty_store_active_leases(self, tmp_path: Path) -> None:
        """Empty store returns empty tuple for active leases."""
        store = DriveStore(store_root=tmp_path)
        assert store.active_leases_for_drive("drv_01") == ()


# ------------------------------------------------------------------
# Lease invalidation tests (planner mutation scenario)
# ------------------------------------------------------------------


class TestLeaseInvalidation:
    """Verify planner mutation invalidates unstarted leases cleanly.

    Authority: docs/RFC-orch-drive.md section 14.4
    """

    def test_invalidate_lease_marks_released(self, tmp_path: Path) -> None:
        """invalidate_lease marks an active lease as released."""
        store = DriveStore(store_root=tmp_path)
        store.save_lease(
            DriveLease(
                drive_id="drv_01",
                step_id="core.verify",
                run_id="run_01",
                status="active",
                created_at=100.0,
            )
        )

        released = store.invalidate_lease("run_01", reason="invalidated")
        assert released is not None
        assert released.status == "released"
        assert released.released_reason == "invalidated"
        assert released.released_at is not None
        assert released.released_at > 0.0

    def test_invalidate_already_released_returns_none(self, tmp_path: Path) -> None:
        """invalidate_lease returns None for an already-released lease."""
        store = DriveStore(store_root=tmp_path)
        store.save_lease(
            DriveLease(
                drive_id="drv_01",
                step_id="core.verify",
                run_id="run_01",
                status="released",
                released_at=200.0,
                released_reason="completed",
            )
        )

        result = store.invalidate_lease("run_01")
        assert result is None

    def test_invalidate_nonexistent_returns_none(self, tmp_path: Path) -> None:
        """invalidate_lease returns None for non-existent lease."""
        store = DriveStore(store_root=tmp_path)
        result = store.invalidate_lease("nonexistent")
        assert result is None

    def test_invalidate_leases_for_step(self, tmp_path: Path) -> None:
        """invalidate_leases_for_step releases all active leases for a step.

        This covers the planner mutation scenario: when a planner mutation
        removes or replaces a step, all unstarted leases for that step
        must be released before frontier reopens.
        """
        store = DriveStore(store_root=tmp_path)
        # Create two active leases for step "core.verify" and one for "core.impl"
        store.save_lease(
            DriveLease(drive_id="drv_01", step_id="core.verify", run_id="run_01", status="active")
        )
        store.save_lease(
            DriveLease(drive_id="drv_01", step_id="core.verify", run_id="run_02", status="active")
        )
        store.save_lease(
            DriveLease(drive_id="drv_01", step_id="core.impl", run_id="run_03", status="active")
        )

        invalidated = store.invalidate_leases_for_step("drv_01", "core.verify", reason="superseded")
        assert len(invalidated) == 2
        assert all(l.status == "released" for l in invalidated)
        assert all(l.released_reason == "superseded" for l in invalidated)

        # "core.impl" lease should remain active
        impl_lease = store.active_lease_for_step("drv_01", "core.impl")
        assert impl_lease is not None
        assert impl_lease.status == "active"

        # "core.verify" lease should be released
        verify_lease = store.lease_by_run_id("run_01")
        assert verify_lease is not None
        assert verify_lease.status == "released"

    def test_invalidate_leases_for_nonexistent_step(self, tmp_path: Path) -> None:
        """No leases to invalidate for a step with no leases."""
        store = DriveStore(store_root=tmp_path)
        store.save_lease(
            DriveLease(drive_id="drv_01", step_id="core.a", run_id="run_01", status="active")
        )

        result = store.invalidate_leases_for_step("drv_01", "core.nonexistent")
        assert result == ()

    def test_invalidate_leases_preserves_completed_lease(self, tmp_path: Path) -> None:
        """Invalidating a step does not touch already-released (completed) leases."""
        store = DriveStore(store_root=tmp_path)
        # A completed lease for "core.verify"
        store.save_lease(
            DriveLease(
                drive_id="drv_01",
                step_id="core.verify",
                run_id="run_completed",
                status="released",
                released_at=150.0,
                released_reason="completed",
            )
        )
        # An active lease for "core.verify"
        store.save_lease(
            DriveLease(
                drive_id="drv_01", step_id="core.verify", run_id="run_active", status="active"
            )
        )

        invalidated = store.invalidate_leases_for_step("drv_01", "core.verify")
        assert len(invalidated) == 1
        assert invalidated[0].run_id == "run_active"

        # The completed lease should still be "released" with reason "completed"
        completed = store.lease_by_run_id("run_completed")
        assert completed is not None
        assert completed.released_reason == "completed"


# ------------------------------------------------------------------
# Same-plan admission blocking tests
# ------------------------------------------------------------------


class TestSamePlanDriveAdmission:
    """Verify same-plan new drive is blocked while active drive exists.

    Authority: docs/RFC-orch-drive.md section 14.1
    """

    def test_new_drive_blocked_while_active_exists(self, tmp_path: Path) -> None:
        """A new drive cannot be admitted while an active drive exists for same plan."""
        store = DriveStore(store_root=tmp_path)
        store.save_drive(
            DriveRecord(drive_id="drv_active", plan_path="/repo/plan.yaml", status="running")
        )

        with pytest.raises(DriveAdmissionConflictError) as exc_info:
            store.assert_can_admit_drive("/repo/plan.yaml")

        assert exc_info.value.active_drive_id == "drv_active"

    def test_new_drive_allowed_when_terminal_exists(self, tmp_path: Path) -> None:
        """A new drive CAN be admitted when existing drive is terminal."""
        store = DriveStore(store_root=tmp_path)
        for terminal_status in ("completed", "halted", "failed_unrecoverable", "stopped"):
            store_id = f"drv_{terminal_status}"
            store.save_drive(
                DriveRecord(drive_id=store_id, plan_path="/repo/plan.yaml", status=terminal_status)
            )

        # Should not raise
        store.assert_can_admit_drive("/repo/plan.yaml")

    def test_paused_drive_blocks_admission(self, tmp_path: Path) -> None:
        """A paused drive still blocks new drive admission."""
        store = DriveStore(store_root=tmp_path)
        store.save_drive(
            DriveRecord(drive_id="drv_paused", plan_path="/repo/plan.yaml", status="paused")
        )

        with pytest.raises(DriveAdmissionConflictError):
            store.assert_can_admit_drive("/repo/plan.yaml")

    def test_different_plan_allows_admission(self, tmp_path: Path) -> None:
        """Active drive for one plan does not block admission for a different plan."""
        store = DriveStore(store_root=tmp_path)
        store.save_drive(
            DriveRecord(drive_id="drv_plan_a", plan_path="/repo/plan_a.yaml", status="running")
        )

        # Should not raise for a different plan
        store.assert_can_admit_drive("/repo/plan_b.yaml")


# ------------------------------------------------------------------
# Frozen drive config persistence tests
# ------------------------------------------------------------------


class TestFrozenDriveConfigPersistence:
    """Verify frozen drive config persistence and immutability.

    Authority: docs/RFC-orch-drive.md sections 8.1, 15.1, 15.2

    Resume/recover must reuse frozen drive parameters instead of ambient
    config drift.
    """

    def test_save_and_load_frozen_config(self, tmp_path: Path) -> None:
        """Frozen config round-trips through persistence."""
        store = DriveStore(store_root=tmp_path)
        config = DriveConfigFrozen(
            drive_id="drv_01",
            max_parallelism=2,
            control_idle_poll_interval_ms=3000,
            control_action_ack_timeout_seconds=15.0,
            resolver_invocation_timeout_seconds=300.0,
            resolver_max_tool_calls_per_invocation=50,
            frozen_at=1776124800.0,
        )
        store.save_drive_config_frozen(config)

        loaded = store.load_drive_config_frozen("drv_01")
        assert loaded is not None
        assert loaded.drive_id == "drv_01"
        assert loaded.max_parallelism == 2
        assert loaded.control_idle_poll_interval_ms == 3000
        assert loaded.control_action_ack_timeout_seconds == 15.0
        assert loaded.resolver_invocation_timeout_seconds == 300.0
        assert loaded.resolver_max_tool_calls_per_invocation == 50
        assert loaded.frozen_at == 1776124800.0

    def test_frozen_config_is_immutable(self, tmp_path: Path) -> None:
        """Frozen config is not overwritten on second save (idempotent)."""
        store = DriveStore(store_root=tmp_path)

        config_v1 = DriveConfigFrozen(
            drive_id="drv_01",
            max_parallelism=2,
            frozen_at=100.0,
        )
        store.save_drive_config_frozen(config_v1)

        # Try to save with different max_parallelism
        config_v2 = DriveConfigFrozen(
            drive_id="drv_01",
            max_parallelism=8,  # Different!
            frozen_at=200.0,
        )
        store.save_drive_config_frozen(config_v2)

        # Should still have the original values
        loaded = store.load_drive_config_frozen("drv_01")
        assert loaded is not None
        assert loaded.max_parallelism == 2
        assert loaded.frozen_at == 100.0

    def test_frozen_config_survives_config_drift(self, tmp_path: Path) -> None:
        """Frozen config is used for resume/recover, NOT ambient config.

        This is the key test for the config drift failure mode:
        even if ambient OrchestrationConfig changes, resume/recover
        must use the frozen parameters.
        """
        store = DriveStore(store_root=tmp_path)

        # Create a drive with initial config
        orch_config_initial = OrchestrationConfig(
            control=ControlConfig(
                idle_poll_interval_ms=1000,
                action_ack_timeout_seconds=5.0,
            ),
            resolver=ResolverConfig(
                invocation_timeout_seconds=600.0,
                max_tool_calls_per_invocation=100,
            ),
        )
        frozen_config = store.freeze_drive_config_from_orch_config("drv_01", orch_config_initial)
        store.save_drive_config_frozen(frozen_config)

        # Config changes (ambient drift) — user changes their config file
        orch_config_drifted = OrchestrationConfig(
            control=ControlConfig(
                idle_poll_interval_ms=500,  # Changed from 1000
                action_ack_timeout_seconds=30.0,  # Changed from 5
            ),
            resolver=ResolverConfig(
                invocation_timeout_seconds=120.0,  # Changed from 600
                max_tool_calls_per_invocation=500,  # Changed from 100
            ),
        )

        # The drifted config should NOT match the frozen config
        drifted_frozen = store.freeze_drive_config_from_orch_config("drv_01", orch_config_drifted)
        assert drifted_frozen.control_idle_poll_interval_ms == 500
        assert drifted_frozen.control_action_ack_timeout_seconds == 30.0
        assert drifted_frozen.resolver_invocation_timeout_seconds == 120.0

        # But loading the persisted frozen config still gives original values
        loaded = store.load_drive_config_frozen("drv_01")
        assert loaded is not None
        assert loaded.control_idle_poll_interval_ms == 1000
        assert loaded.control_action_ack_timeout_seconds == 5.0
        assert loaded.resolver_invocation_timeout_seconds == 600.0
        assert loaded.resolver_max_tool_calls_per_invocation == 100

    def test_load_missing_frozen_config_returns_none(self, tmp_path: Path) -> None:
        """Loading a non-existent frozen config returns None."""
        store = DriveStore(store_root=tmp_path)
        assert store.load_drive_config_frozen("nonexistent") is None

    def test_frozen_config_separate_per_drive(self, tmp_path: Path) -> None:
        """Each drive has its own frozen config."""
        store = DriveStore(store_root=tmp_path)

        config_a = DriveConfigFrozen(drive_id="drv_a", max_parallelism=2, frozen_at=100.0)
        config_b = DriveConfigFrozen(drive_id="drv_b", max_parallelism=8, frozen_at=200.0)
        store.save_drive_config_frozen(config_a)
        store.save_drive_config_frozen(config_b)

        loaded_a = store.load_drive_config_frozen("drv_a")
        loaded_b = store.load_drive_config_frozen("drv_b")

        assert loaded_a is not None
        assert loaded_b is not None
        assert loaded_a.max_parallelism == 2
        assert loaded_b.max_parallelism == 8

    def test_freeze_from_orch_config_defaults(self) -> None:
        """freeze_drive_config_from_orch_config uses defaults from OrchestrationConfig."""
        store = DriveStore(store_root=Path("/tmp/test_drives"))
        config = OrchestrationConfig()  # All defaults
        frozen = store.freeze_drive_config_from_orch_config("drv_01", config)
        assert frozen.drive_id == "drv_01"
        assert frozen.max_parallelism == 4
        assert frozen.control_idle_poll_interval_ms == 1000
        assert frozen.control_action_ack_timeout_seconds == 5.0
        assert frozen.resolver_invocation_timeout_seconds == 600.0
        assert frozen.resolver_max_tool_calls_per_invocation == 100
        assert frozen.frozen_at > 0.0


# ------------------------------------------------------------------
# Lease + Drive admission integration tests
# ------------------------------------------------------------------


class TestLeaseDriveAdmissionIntegration:
    """Integration tests for lease ownership and drive admission interaction."""

    def test_lease_ownership_is_drive_step_run(self, tmp_path: Path) -> None:
        """Lease ownership key is drive_id + step_id + run_id."""
        store = DriveStore(store_root=tmp_path)
        store.save_drive(
            DriveRecord(drive_id="drv_01", plan_path="/repo/plan.yaml", status="running")
        )

        # Create leases for two steps in the same drive
        store.save_lease(DriveLease(drive_id="drv_01", step_id="core.verify", run_id="run_01A"))
        store.save_lease(DriveLease(drive_id="drv_01", step_id="core.impl", run_id="run_02B"))

        # Both leases should be active for the drive
        active = store.active_leases_for_drive("drv_01")
        assert len(active) == 2

        # Each step can be looked up by step_id
        lease_verify = store.active_lease_for_step("drv_01", "core.verify")
        assert lease_verify is not None
        assert lease_verify.run_id == "run_01A"

        lease_impl = store.active_lease_for_step("drv_01", "core.impl")
        assert lease_impl is not None
        assert lease_impl.run_id == "run_02B"

    def test_active_drive_blocks_new_drive_with_leases(self, tmp_path: Path) -> None:
        """An active drive with leases blocks new drive for same plan."""
        store = DriveStore(store_root=tmp_path)
        store.save_drive(
            DriveRecord(drive_id="drv_active", plan_path="/repo/plan.yaml", status="running")
        )
        store.save_lease(DriveLease(drive_id="drv_active", step_id="core.verify", run_id="run_01"))

        with pytest.raises(DriveAdmissionConflictError) as exc_info:
            store.assert_can_admit_drive("/repo/plan.yaml")

        assert exc_info.value.active_drive_id == "drv_active"

    def test_terminal_drive_allows_new_drive_with_leases_cleared(self, tmp_path: Path) -> None:
        """After a drive completes, new drive for same plan is admitted.

        Leases from the completed drive are still in the store but
        are not blocking since the drive is terminal.
        """
        store = DriveStore(store_root=tmp_path)
        store.save_drive(
            DriveRecord(drive_id="drv_completed", plan_path="/repo/plan.yaml", status="completed")
        )
        store.save_lease(
            DriveLease(
                drive_id="drv_completed",
                step_id="core.verify",
                run_id="run_01",
                status="released",
                released_reason="completed",
            )
        )

        # New drive should be admitted
        store.assert_can_admit_drive("/repo/plan.yaml")  # Should not raise

    def test_planner_mutation_invalidates_unstarted_lease(self, tmp_path: Path) -> None:
        """Planner mutation invalidates unstarted leases for a step.

        Simulates: planner removes a step, so any active lease for that
        step must be released before frontier reopens.
        """
        store = DriveStore(store_root=tmp_path)
        store.save_drive(
            DriveRecord(drive_id="drv_01", plan_path="/repo/plan.yaml", status="running")
        )

        # Create active leases for three steps
        store.save_lease(DriveLease(drive_id="drv_01", step_id="core.verify", run_id="run_01"))
        store.save_lease(DriveLease(drive_id="drv_01", step_id="core.impl", run_id="run_02"))
        store.save_lease(DriveLease(drive_id="drv_01", step_id="core.test", run_id="run_03"))

        # Planner mutation removes "core.impl" step — invalidate its lease
        invalidated = store.invalidate_leases_for_step("drv_01", "core.impl", reason="superseded")

        assert len(invalidated) == 1
        assert invalidated[0].step_id == "core.impl"
        assert invalidated[0].released_reason == "superseded"

        # Other leases remain active
        active = store.active_leases_for_drive("drv_01")
        active_step_ids = {l.step_id for l in active}
        assert "core.verify" in active_step_ids
        assert "core.impl" not in active_step_ids
        assert "core.test" in active_step_ids
