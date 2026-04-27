"""
Contract type tests for drive scheduling types.

These tests verify that the drive scheduling contract types from
docs/RFC-orch-drive.md sections 8, 9, 12, and 13 have the correct
fields, types, and invariants.

Authority: docs/RFC-orch-drive.md sections 8, 9, 12, 13
Step: orch_drive_contracts.drive-types-lock
"""

import dataclasses
from dataclasses import fields, is_dataclass
from typing import Literal, Protocol, get_args, get_origin, get_type_hints

import pytest

from vectl.orchestration.contracts import (
    BarrierReason,
    ChildRunKind,
    ChildRunRef,
    ChildRunStatus,
    ControlDecision,
    DriveBarrier,
    DriveRecord,
    DriveStatus,
    PlannerBundleStatus,
    PlannerMutationAction,
    PlannerMutationBundle,
    PlannerMutationItem,
    PlannerRequest,
    ResolutionReport,
)
from vectl.orchestration.interfaces import PlannerAdapter

# ------------------------------------------------------------------
# DriveStatus type alias
# ------------------------------------------------------------------


class TestDriveStatus:
    """Test DriveStatus type alias contract."""

    def test_drive_status_literal_values(self):
        """Verify DriveStatus contains all RFC-orch-drive section 8.2 values."""
        assert get_origin(DriveStatus) is Literal
        expected_values = {
            "running",
            "paused",
            "resolving",
            "replanning",
            "blocked_operator",
            "recovering",
            "completed",
            "halted",
            "failed_unrecoverable",
            "stopped",
        }
        actual_values = set(get_args(DriveStatus))
        assert actual_values == expected_values, (
            f"DriveStatus mismatch. Expected: {expected_values}, Got: {actual_values}"
        )


class TestChildRunKind:
    """Test ChildRunKind type alias contract."""

    def test_child_run_kind_literal_values(self):
        """Verify ChildRunKind contains the three defined values."""
        assert get_origin(ChildRunKind) is Literal
        expected_values = {"step", "resolver", "planner"}
        actual_values = set(get_args(ChildRunKind))
        assert actual_values == expected_values


class TestChildRunStatus:
    """Test ChildRunStatus type alias contract."""

    def test_child_run_status_literal_values(self):
        """Verify ChildRunStatus contains RFC-orch-drive section 8.3 values."""
        assert get_origin(ChildRunStatus) is Literal
        expected_values = {
            "pending",
            "running",
            "success",
            "fail",
            "stall",
            "transport_error",
            "cancelled",
        }
        actual_values = set(get_args(ChildRunStatus))
        assert actual_values == expected_values


class TestBarrierReason:
    """Test BarrierReason type alias contract."""

    def test_barrier_reason_literal_values(self):
        """Verify BarrierReason contains RFC-orch-drive section 8.4.1 values."""
        assert get_origin(BarrierReason) is Literal
        expected_values = {
            "runtime_failure",
            "merge_conflict",
            "review_failed",
            "planner_needed",
            "recovery_gate",
            "operator_pause",
        }
        actual_values = set(get_args(BarrierReason))
        assert actual_values == expected_values


class TestPlannerMutationAction:
    """Test PlannerMutationAction type alias contract."""

    def test_planner_mutation_action_literal_values(self):
        """Verify PlannerMutationAction matches vectl facade surface."""
        assert get_origin(PlannerMutationAction) is Literal
        expected_values = {
            "add-step",
            "edit-step",
            "remove-step",
            "move-step",
            "add-phase",
            "edit-phase",
            "skip-step",
            "complete-phase",
        }
        actual_values = set(get_args(PlannerMutationAction))
        assert actual_values == expected_values


class TestPlannerBundleStatus:
    """Test PlannerBundleStatus type alias contract."""

    def test_planner_bundle_status_literal_values(self):
        """Verify PlannerBundleStatus matches RFC-orch-drive section 13.5."""
        assert get_origin(PlannerBundleStatus) is Literal
        expected_values = {"applyable", "operator_required", "halt"}
        actual_values = set(get_args(PlannerBundleStatus))
        assert actual_values == expected_values


# ------------------------------------------------------------------
# PlannerRequest dataclass
# ------------------------------------------------------------------


class TestPlannerRequest:
    """Test PlannerRequest contract type (RFC-orch-drive section 12.2)."""

    def test_planner_request_is_dataclass(self):
        assert is_dataclass(PlannerRequest)

    def test_planner_request_fields_match_rfc(self):
        """Verify PlannerRequest has the fields from RFC-orch-drive section 12.2."""
        expected_fields = {
            "reason",
            "affected_steps",
            "evidence_refs",
            "constraints",
            "mutations",
        }
        actual_fields = {f.name for f in fields(PlannerRequest)}
        assert actual_fields == expected_fields, (
            f"PlannerRequest field mismatch. Expected: {expected_fields}, Got: {actual_fields}"
        )

    def test_planner_request_frozen(self):
        with pytest.raises(dataclasses.FrozenInstanceError):
            req = PlannerRequest(reason="test")
            req.reason = "changed"

    def test_planner_request_required_field(self):
        """Only reason is required; all others have defaults."""
        req = PlannerRequest(reason="step needs replan")
        assert req.reason == "step needs replan"
        assert req.affected_steps == ()
        assert req.evidence_refs == ()
        assert req.constraints == ()
        assert req.mutations == ()

    def test_planner_request_full_construction(self):
        req = PlannerRequest(
            reason="verification step needs a prerequisite fix",
            affected_steps=("core.verify",),
            evidence_refs=("artifact://review/gate_output.json",),
            constraints=("Preserve completed steps", "Do not widen scope"),
            mutations=(PlannerMutationItem(action="add-step"),),
        )
        assert req.reason == "verification step needs a prerequisite fix"
        assert req.affected_steps == ("core.verify",)
        assert req.evidence_refs == ("artifact://review/gate_output.json",)
        assert req.constraints == ("Preserve completed steps", "Do not widen scope")
        assert req.mutations[0].action == "add-step"


# ------------------------------------------------------------------
# PlannerMutationItem dataclass
# ------------------------------------------------------------------


class TestPlannerMutationItem:
    """Test PlannerMutationItem contract type (RFC-orch-drive section 8.5.1)."""

    def test_is_dataclass(self):
        assert is_dataclass(PlannerMutationItem)

    def test_fields_match_rfc(self):
        expected_fields = {"action", "arguments", "reason", "safety_notes"}
        actual_fields = {f.name for f in fields(PlannerMutationItem)}
        assert actual_fields == expected_fields

    def test_frozen(self):
        with pytest.raises(AttributeError):
            item = PlannerMutationItem(action="add-step")
            item.action = "edit-step"  # type: ignore[misc]

    def test_minimal_construction(self):
        item = PlannerMutationItem(action="add-step")
        assert item.action == "add-step"
        assert item.arguments == {}
        assert item.reason == ""
        assert item.safety_notes == ()

    def test_full_construction(self):
        item = PlannerMutationItem(
            action="edit-step",
            arguments={"step_id": "core.verify", "changes": {"depends_on": ["core.fix"]}},
            reason="Verification must wait for repair",
            safety_notes=("Do not delete existing completed steps.",),
        )
        assert item.action == "edit-step"
        assert item.arguments["step_id"] == "core.verify"
        assert item.reason == "Verification must wait for repair"


# ------------------------------------------------------------------
# PlannerMutationBundle dataclass
# ------------------------------------------------------------------


class TestPlannerMutationBundle:
    """Test PlannerMutationBundle contract type (RFC-orch-drive section 8.5)."""

    def test_is_dataclass(self):
        assert is_dataclass(PlannerMutationBundle)

    def test_fields_match_rfc(self):
        expected_fields = {
            "status",
            "summary",
            "mutations",
            "affected_steps",
            "evidence_refs",
            "safety_notes",
        }
        actual_fields = {f.name for f in fields(PlannerMutationBundle)}
        assert actual_fields == expected_fields

    def test_frozen(self):
        with pytest.raises(AttributeError):
            bundle = PlannerMutationBundle(status="applyable", summary="test")
            bundle.status = "halt"  # type: ignore[misc]

    def test_required_fields_only(self):
        bundle = PlannerMutationBundle(status="applyable", summary="Insert fix step")
        assert bundle.status == "applyable"
        assert bundle.summary == "Insert fix step"
        assert bundle.mutations == ()
        assert bundle.affected_steps == ()
        assert bundle.evidence_refs == ()
        assert bundle.safety_notes == ()

    def test_full_construction_rfc_example(self):
        """Matches the JSON example in RFC-orch-drive section 8.6.4."""
        bundle = PlannerMutationBundle(
            status="applyable",
            summary="Insert a fix step before the blocked verification step.",
            mutations=(
                PlannerMutationItem(
                    action="add-step",
                    arguments={
                        "phase_id": "core",
                        "step_id": "core.fix",
                        "name": "Repair generated artifact",
                        "description": "Rewrite artifact.txt with VERSION=2 and commit it.",
                    },
                    reason=(
                        "The current frontier needs an intermediate repair before "
                        "verification can pass."
                    ),
                    safety_notes=("Do not delete existing completed steps.",),
                ),
                PlannerMutationItem(
                    action="edit-step",
                    arguments={
                        "step_id": "core.verify",
                        "changes": {"depends_on": ["core.fix"]},
                    },
                    reason="Verification must wait for the new repair step.",
                    safety_notes=(),
                ),
            ),
            affected_steps=("core.fix", "core.verify"),
            evidence_refs=("planner://analysis",),
            safety_notes=("Apply through vectl facade only.",),
        )
        assert bundle.status == "applyable"
        assert len(bundle.mutations) == 2
        assert bundle.mutations[0].action == "add-step"
        assert bundle.mutations[1].action == "edit-step"


# ------------------------------------------------------------------
# DriveBarrier dataclass
# ------------------------------------------------------------------


class TestDriveBarrier:
    """Test DriveBarrier contract type (RFC-orch-drive section 8.4)."""

    def test_is_dataclass(self):
        assert is_dataclass(DriveBarrier)

    def test_fields_match_rfc(self):
        expected_fields = {
            "reason",
            "entered_at",
            "case_ids",
            "pending_resolver_run_id",
            "pending_planner_run_id",
            "active_child_run_ids_at_entry",
        }
        actual_fields = {f.name for f in fields(DriveBarrier)}
        assert actual_fields == expected_fields, (
            f"DriveBarrier field mismatch. Expected: {expected_fields}, Got: {actual_fields}"
        )

    def test_frozen(self):
        with pytest.raises(AttributeError):
            barrier = DriveBarrier(reason="merge_conflict")
            barrier.reason = "runtime_failure"  # type: ignore[misc]

    def test_required_field(self):
        barrier = DriveBarrier(reason="merge_conflict")
        assert barrier.reason == "merge_conflict"
        assert barrier.entered_at == 0.0
        assert barrier.case_ids == ()
        assert barrier.pending_resolver_run_id is None
        assert barrier.pending_planner_run_id is None
        assert barrier.active_child_run_ids_at_entry == ()

    def test_full_construction_rfc_example(self):
        """Matches JSON example in RFC-orch-drive section 8.6.3."""
        barrier = DriveBarrier(
            reason="merge_conflict",
            entered_at=1776124820.0,
            case_ids=("case_01M",),
            pending_resolver_run_id="run_resolve_01",
            pending_planner_run_id=None,
            active_child_run_ids_at_entry=("run_01B",),
        )
        assert barrier.reason == "merge_conflict"
        assert barrier.entered_at == 1776124820.0
        assert barrier.case_ids == ("case_01M",)

    def test_barrier_reason_type(self):
        """Verify barrier.reason is BarrierReason literal."""
        barrier_type_map = {f.name: f.type for f in fields(DriveBarrier)}
        assert get_origin(barrier_type_map["reason"]) is Literal
        reason_args = set(get_args(barrier_type_map["reason"]))
        assert reason_args == set(get_args(BarrierReason))


# ------------------------------------------------------------------
# ChildRunRef dataclass
# ------------------------------------------------------------------


class TestChildRunRef:
    """Test ChildRunRef contract type (RFC-orch-drive section 8.3)."""

    def test_is_dataclass(self):
        assert is_dataclass(ChildRunRef)

    def test_fields_match_rfc(self):
        expected_fields = {
            "run_id",
            "drive_id",
            "kind",
            "status",
            "step_id",
            "case_id",
            "planner_request_id",
            "workspace",
            "runner",
            "session_id",
            "artifact_root",
        }
        actual_fields = {f.name for f in fields(ChildRunRef)}
        assert actual_fields == expected_fields, (
            f"ChildRunRef field mismatch. Expected: {expected_fields}, Got: {actual_fields}"
        )

    def test_frozen(self):
        with pytest.raises(AttributeError):
            ref = ChildRunRef(run_id="run_01A", drive_id="drv_01K", kind="step")
            ref.kind = "resolver"  # type: ignore[misc]

    def test_required_fields(self):
        ref = ChildRunRef(run_id="run_01A", drive_id="drv_01K", kind="step")
        assert ref.run_id == "run_01A"
        assert ref.drive_id == "drv_01K"
        assert ref.kind == "step"
        assert ref.status == "pending"
        assert ref.step_id is None
        assert ref.case_id is None
        assert ref.planner_request_id is None

    def test_full_construction_rfc_example(self):
        """Matches JSON example in RFC-orch-drive section 8.6.2."""
        ref = ChildRunRef(
            run_id="run_01A",
            drive_id="drv_01K",
            kind="step",
            status="running",
            step_id="core.verify",
            workspace=".vectl/workspaces/core.verify",
            runner="opencode",
            session_id="ses_01ABC",
            artifact_root=".vectl/runs/run_01A",
        )
        assert ref.kind == "step"
        assert ref.status == "running"
        assert ref.step_id == "core.verify"

    def test_kind_literal_type(self):
        """Verify kind field uses ChildRunKind."""
        ref_type_map = {f.name: f.type for f in fields(ChildRunRef)}
        assert get_origin(ref_type_map["kind"]) is Literal
        kind_args = set(get_args(ref_type_map["kind"]))
        assert kind_args == set(get_args(ChildRunKind))

    def test_status_literal_type(self):
        """Verify status field uses ChildRunStatus."""
        ref_type_map = {f.name: f.type for f in fields(ChildRunRef)}
        assert get_origin(ref_type_map["status"]) is Literal
        status_args = set(get_args(ref_type_map["status"]))
        assert status_args == set(get_args(ChildRunStatus))

    def test_kind_discriminator_step_has_step_id(self):
        """Step child runs must populate step_id."""
        ref = ChildRunRef(run_id="r1", drive_id="d1", kind="step", step_id="core.verify")
        assert ref.kind == "step"
        assert ref.step_id == "core.verify"

    def test_kind_discriminator_resolver_has_case_id(self):
        """Resolver child runs should populate case_id."""
        ref = ChildRunRef(run_id="r2", drive_id="d1", kind="resolver", case_id="case_01M")
        assert ref.kind == "resolver"
        assert ref.case_id == "case_01M"

    def test_kind_discriminator_planner_has_planner_request_id(self):
        """Planner child runs should populate planner_request_id."""
        ref = ChildRunRef(run_id="r3", drive_id="d1", kind="planner", planner_request_id="pr_01")
        assert ref.kind == "planner"
        assert ref.planner_request_id == "pr_01"


# ------------------------------------------------------------------
# DriveRecord dataclass
# ------------------------------------------------------------------


class TestDriveRecord:
    """Test DriveRecord contract type (RFC-orch-drive section 8.1)."""

    def test_is_dataclass(self):
        assert is_dataclass(DriveRecord)

    def test_fields_match_rfc(self):
        expected_fields = {
            "drive_id",
            "plan_path",
            "status",
            "started_at",
            "updated_at",
            "finished_at",
            "agent",
            "max_parallelism",
            "active_child_run_ids",
            "frontier_step_ids",
            "blocked_case_ids",
            "barrier",
            "operator_pause_state",
            "summary",
        }
        actual_fields = {f.name for f in fields(DriveRecord)}
        assert actual_fields == expected_fields, (
            f"DriveRecord field mismatch. Expected: {expected_fields}, Got: {actual_fields}"
        )

    def test_frozen(self):
        with pytest.raises(AttributeError):
            record = DriveRecord(drive_id="drv_01", plan_path="/repo/plan.yaml")
            record.drive_id = "changed"  # type: ignore[misc]

    def test_required_fields(self):
        record = DriveRecord(drive_id="drv_01", plan_path="/repo/plan.yaml")
        assert record.drive_id == "drv_01"
        assert record.plan_path == "/repo/plan.yaml"
        assert record.status == "running"
        assert record.max_parallelism == 4
        assert record.active_child_run_ids == ()
        assert record.frontier_step_ids == ()
        assert record.blocked_case_ids == ()
        assert record.barrier is None
        assert record.operator_pause_state == "active"
        assert record.summary == ""

    def test_full_construction_rfc_example(self):
        """Matches JSON example in RFC-orch-drive section 8.6.1."""
        record = DriveRecord(
            drive_id="drv_01K",
            plan_path="/repo/plan.yaml",
            status="running",
            started_at=1776124800.0,
            updated_at=1776124812.0,
            finished_at=None,
            agent="python-executor",
            max_parallelism=4,
            active_child_run_ids=("run_01A", "run_01B"),
            frontier_step_ids=("core.verify", "core.snapshot"),
            blocked_case_ids=(),
            barrier=None,
            operator_pause_state="active",
            summary="2 child runs active; frontier width=2",
        )
        assert record.status == "running"
        assert record.max_parallelism == 4
        assert len(record.active_child_run_ids) == 2

    def test_status_literal_type(self):
        """Verify status field uses DriveStatus."""
        record_type_map = {f.name: f.type for f in fields(DriveRecord)}
        assert get_origin(record_type_map["status"]) is Literal
        status_args = set(get_args(record_type_map["status"]))
        assert status_args == set(get_args(DriveStatus))

    def test_barrier_embedding(self):
        """Verify barrier is DriveBarrier | None as per RFC persistence rule."""
        record_type_map = {f.name: f.type for f in fields(DriveRecord)}
        # barrier should be DriveBarrier | None
        barrier_type = record_type_map["barrier"]
        # Just check it mentions DriveBarrier
        assert "DriveBarrier" in str(barrier_type)

    def test_barrier_embed_not_top_level_siblings(self):
        """RFC persistence rule: barrier subfields must not be top-level siblings."""
        barrier_field_names = {f.name for f in fields(DriveBarrier)}
        drive_field_names = {f.name for f in fields(DriveRecord)}
        # No barrier subfield should appear as a top-level field on DriveRecord
        overlap = barrier_field_names - {"reason"} & drive_field_names
        # "reason" is allowed as overlap since DriveRecord has its own reason-like "summary"
        assert not overlap, (
            f"DriveRecord must not duplicate barrier subfields as top-level: {overlap}"
        )

    def test_barrier_construction_in_drive_record(self):
        """Verify barrier can be embedded in DriveRecord."""
        barrier = DriveBarrier(
            reason="merge_conflict",
            entered_at=1776124820.0,
            case_ids=("case_01M",),
            pending_resolver_run_id="run_resolve_01",
        )
        record = DriveRecord(
            drive_id="drv_01",
            plan_path="/repo/plan.yaml",
            status="resolving",
            barrier=barrier,
        )
        assert record.barrier is not None
        assert record.barrier.reason == "merge_conflict"

    def test_terminal_statuses(self):
        """Verify that terminal statuses can be assigned."""
        for status in ("completed", "halted", "failed_unrecoverable", "stopped"):
            record = DriveRecord(drive_id="drv_x", plan_path="/p", status=status)
            assert record.status == status

    def test_operator_pause_state_literal(self):
        """Verify operator_pause_state uses Literal["active", "paused"]."""
        record_type_map = {f.name: f.type for f in fields(DriveRecord)}
        ops_type = record_type_map["operator_pause_state"]
        assert get_origin(ops_type) is Literal
        assert set(get_args(ops_type)) == {"active", "paused"}


# ------------------------------------------------------------------
# Expanded ControlDecision
# ------------------------------------------------------------------


class TestControlDecisionExpanded:
    """Test expanded ControlDecision shape (RFC-orch-drive section 9.2.1)."""

    def test_dispatch_batch_decision(self):
        """Verify dispatch_batch decision with step_ids and role_bindings."""
        decision = ControlDecision(
            kind="dispatch_batch",
            reason="Frontier dispatch",
            step_ids=("core.verify", "core.snapshot"),
            role_bindings={
                "core.verify": "python-executor",
                "core.snapshot": "python-executor",
            },
            capacity_used=2,
            capacity_remaining=2,
        )
        assert decision.kind == "dispatch_batch"
        assert len(decision.step_ids) == 2
        assert decision.role_bindings["core.verify"] == "python-executor"

    def test_resolve_decision_with_case_ids(self):
        """Verify resolve decision carries case_ids."""
        decision = ControlDecision(
            kind="resolve",
            reason="Blocked step requires resolution",
            case_ids=("case_01M",),
        )
        assert decision.kind == "resolve"
        assert decision.case_ids == ("case_01M",)

    def test_replan_decision_with_planner_request(self):
        """Verify replan decision carries planner_request."""
        planner_req = PlannerRequest(
            reason="Step needs new prerequisite",
            affected_steps=("core.verify",),
        )
        decision = ControlDecision(
            kind="replan",
            reason="Resolver requests planner mutation",
            planner_request=planner_req,
            barrier_required=True,
        )
        assert decision.kind == "replan"
        assert decision.planner_request is not None
        assert decision.planner_request.reason == "Step needs new prerequisite"
        assert decision.barrier_required is True

    def test_wait_decision(self):
        """Verify wait decision has empty step_ids and case_ids."""
        decision = ControlDecision(
            kind="wait",
            reason="Execution already in progress",
        )
        assert decision.kind == "wait"
        assert decision.step_ids == ()
        assert decision.case_ids == ()
        assert decision.planner_request is None

    def test_done_decision(self):
        """Verify done decision."""
        decision = ControlDecision(
            kind="done",
            reason="Plan complete and runtime idle",
        )
        assert decision.kind == "done"

    def test_halt_decision(self):
        """Verify halt decision with barrier_required."""
        decision = ControlDecision(
            kind="halt",
            reason="Resolver halted orchestration",
            barrier_required=True,
        )
        assert decision.kind == "halt"
        assert decision.barrier_required is True

    def test_dispatch_backward_compatible(self):
        """Verify transitional 'dispatch' kind still works."""
        decision = ControlDecision(
            kind="dispatch",
            reason="Claimable work available",
            step_ids=("core.impl",),
            role_bindings={"core.impl": "python-executor"},
        )
        assert decision.kind == "dispatch"
        assert decision.step_ids == ("core.impl",)


# ------------------------------------------------------------------
# ResolutionReport planner_request extension
# ------------------------------------------------------------------


class TestResolutionReportPlannerRequestExtension:
    """Test ResolutionReport.planner_request extension (RFC-orch-drive section 12.2)."""

    def test_planner_request_defaults_to_none(self):
        """Backward-compatible: existing construction still works."""
        report = ResolutionReport(status="unblocked", summary="resolved")
        assert report.planner_request is None

    def test_planner_request_attached(self):
        """RFC section 12.2: resolver may attach planner_request."""
        planner_req = PlannerRequest(
            reason="Step core.verify needs a new prerequisite step",
            affected_steps=("core.verify",),
            evidence_refs=("artifact://review/gate_output.json",),
            constraints=("Preserve completed steps",),
        )
        report = ResolutionReport(
            status="unblocked",
            summary="Root cause identified: needs replan",
            planner_request=planner_req,
        )
        assert report.planner_request is not None
        assert report.planner_request.reason == "Step core.verify needs a new prerequisite step"

    def test_planner_request_with_operator_required(self):
        """RFC section 12.2: planner_request may accompany operator_required status."""
        planner_req = PlannerRequest(
            reason="Needs replan before unblocking",
            affected_steps=("core.verify",),
        )
        report = ResolutionReport(
            status="operator_required",
            summary="Cannot resolve without plan modification",
            operator_message="Manual review needed before planner runs",
            planner_request=planner_req,
        )
        assert report.status == "operator_required"
        assert report.planner_request is not None


# ------------------------------------------------------------------
# PlannerAdapter Protocol
# ------------------------------------------------------------------


class TestPlannerAdapterProtocol:
    """Test PlannerAdapter protocol (RFC-orch-drive sections 12, 13)."""

    def test_planner_adapter_is_protocol(self):
        assert issubclass(PlannerAdapter, Protocol)

    def test_planner_adapter_has_invoke_method(self):
        assert hasattr(PlannerAdapter, "invoke")

    def test_planner_adapter_has_apply_method(self):
        assert hasattr(PlannerAdapter, "apply")

    def test_planner_adapter_invoke_return_type(self):
        hints = get_type_hints(PlannerAdapter.invoke)
        assert hints.get("return") is PlannerMutationBundle

    def test_planner_adapter_invoke_parameter_type(self):
        hints = get_type_hints(PlannerAdapter.invoke)
        assert hints.get("request") is PlannerRequest
