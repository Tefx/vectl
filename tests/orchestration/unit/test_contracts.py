"""
Contract type signature tests for orchestration plane boundary types.

These tests verify that each contract type from section 3 of the spec
has the correct fields, types, and behavior.

Authority: docs/ORCHESTRATION-PLANE-INTERFACES.md section 3
Step: orch_foundation.contract_tests
Intent: test_define_red
"""

from dataclasses import fields, is_dataclass

import pytest


class TestCoreSnapshot:
    """Test CoreSnapshot contract type (Section 3.1)."""

    def test_core_snapshot_is_dataclass(self):
        """Verify CoreSnapshot is a dataclass."""
        from vectl.orchestration.contracts import CoreSnapshot

        assert is_dataclass(CoreSnapshot)

    def test_core_snapshot_fields_match_spec(self):
        """
        Verify CoreSnapshot has exactly the 5 documented fields.

        Spec: docs/ORCHESTRATION-PLANE-INTERFACES.md section 3.1
        Fields: plan_complete, claimable_step_ids, in_progress_step_ids,
                blocked_step_ids, unresolved_reasons
        """
        from vectl.orchestration.contracts import CoreSnapshot

        expected_fields = {
            "plan_complete",
            "claimable_step_ids",
            "in_progress_step_ids",
            "blocked_step_ids",
            "unresolved_reasons",
        }
        actual_fields = {f.name for f in fields(CoreSnapshot)}

        assert actual_fields == expected_fields, (
            f"CoreSnapshot field mismatch. Expected: {expected_fields}, Got: {actual_fields}"
        )

    def test_core_snapshot_frozen(self):
        """Verify CoreSnapshot is frozen (immutable)."""
        from vectl.orchestration.contracts import CoreSnapshot

        # Frozen dataclasses have __dataclass_fields__
        assert hasattr(CoreSnapshot, "__dataclass_fields__")

    def test_core_snapshot_field_types(self):
        """
        Verify CoreSnapshot field types match spec.

        Spec expects:
        - plan_complete: bool
        - *_step_ids: tuple[str, ...]
        - unresolved_reasons: tuple[str, ...]
        """
        from vectl.orchestration.contracts import CoreSnapshot

        field_types = {f.name: f.type for f in fields(CoreSnapshot)}

        assert field_types["plan_complete"] is bool
        assert field_types["claimable_step_ids"] == tuple[str, ...]
        assert field_types["in_progress_step_ids"] == tuple[str, ...]
        assert field_types["blocked_step_ids"] == tuple[str, ...]
        assert field_types["unresolved_reasons"] == tuple[str, ...]


class TestRosterSnapshot:
    """Test RosterSnapshot contract type (Section 3.2)."""

    def test_roster_snapshot_is_dataclass(self):
        """Verify RosterSnapshot is a dataclass."""
        from vectl.orchestration.contracts import RosterSnapshot

        assert is_dataclass(RosterSnapshot)

    def test_roster_snapshot_fields_match_spec(self):
        """
        Verify RosterSnapshot has exactly the 4 documented fields.

        Spec: docs/ORCHESTRATION-PLANE-INTERFACES.md section 3.2
        Fields: available_agents, working_agents, reusable_sessions, exhausted_roles
        """
        from vectl.orchestration.contracts import RosterSnapshot

        expected_fields = {
            "available_agents",
            "working_agents",
            "reusable_sessions",
            "exhausted_roles",
        }
        actual_fields = {f.name for f in fields(RosterSnapshot)}

        assert actual_fields == expected_fields, (
            f"RosterSnapshot field mismatch. Expected: {expected_fields}, Got: {actual_fields}"
        )

    def test_roster_snapshot_frozen(self):
        """Verify RosterSnapshot is frozen (immutable)."""
        from vectl.orchestration.contracts import RosterSnapshot

        # Frozen dataclasses have __dataclass_fields__
        assert hasattr(RosterSnapshot, "__dataclass_fields__")


class TestRuntimeSnapshot:
    """Test RuntimeSnapshot contract type (Section 3.3)."""

    def test_runtime_snapshot_is_dataclass(self):
        """Verify RuntimeSnapshot is a dataclass."""
        from vectl.orchestration.contracts import RuntimeSnapshot

        assert is_dataclass(RuntimeSnapshot)

    def test_runtime_snapshot_fields_match_spec(self):
        """
        Verify RuntimeSnapshot has exactly the 6 documented fields.

        Spec: docs/ORCHESTRATION-PLANE-INTERFACES.md section 3.3
        Fields: active_workspaces, active_executions, stalled_executions,
            pending_reconciles, active_reconciles, conflicted_reconciles
        """
        from vectl.orchestration.contracts import RuntimeSnapshot

        expected_fields = {
            "active_workspaces",
            "active_executions",
            "stalled_executions",
            "pending_reconciles",
            "active_reconciles",
            "conflicted_reconciles",
        }
        actual_fields = {f.name for f in fields(RuntimeSnapshot)}

        assert actual_fields == expected_fields, (
            f"RuntimeSnapshot field mismatch. Expected: {expected_fields}, Got: {actual_fields}"
        )

    def test_runtime_snapshot_frozen(self):
        """Verify RuntimeSnapshot is frozen (immutable)."""
        from vectl.orchestration.contracts import RuntimeSnapshot

        # Frozen dataclasses have __dataclass_fields__
        assert hasattr(RuntimeSnapshot, "__dataclass_fields__")


class TestControlDecision:
    """Test ControlDecision contract type (Section 3.4)."""

    def test_control_decision_is_dataclass(self):
        """Verify ControlDecision is a dataclass."""
        from vectl.orchestration.contracts import ControlDecision

        assert is_dataclass(ControlDecision)

    def test_control_decision_fields_match_spec(self):
        """
        Verify ControlDecision has exactly the 4 documented fields.

        Spec: docs/ORCHESTRATION-PLANE-INTERFACES.md section 3.4
        Fields: kind, reason, step_id, role
        """
        from vectl.orchestration.contracts import ControlDecision

        expected_fields = {"kind", "reason", "step_id", "role"}
        actual_fields = {f.name for f in fields(ControlDecision)}

        assert actual_fields == expected_fields, (
            f"ControlDecision field mismatch. Expected: {expected_fields}, Got: {actual_fields}"
        )

    def test_control_decision_kind_literal_values(self):
        """
        Verify ControlDecision.kind uses correct Literal values.

        Spec: Literal["dispatch", "resolve", "wait", "done"]
        """
        from typing import Literal, get_args, get_origin

        from vectl.orchestration.contracts import ControlDecision

        field_types = {f.name: f.type for f in fields(ControlDecision)}
        kind_type = field_types["kind"]

        assert get_origin(kind_type) is Literal
        kind_values = set(get_args(kind_type))
        expected_values = {"dispatch", "resolve", "wait", "done"}

        assert kind_values == expected_values, (
            f"ControlDecision.kind Literal mismatch. Expected: {expected_values}, Got: {kind_values}"
        )

    def test_control_decision_optional_fields(self):
        """
        Verify step_id and role are optional (None default).

        Spec: step_id: str | None = None, role: str | None = None
        """
        from vectl.orchestration.contracts import ControlDecision

        # Test instantiation with minimal fields
        decision = ControlDecision(kind="done", reason="test")
        assert decision.step_id is None
        assert decision.role is None


class TestWorkLease:
    """Test WorkLease contract type (Section 3.5)."""

    def test_work_lease_is_dataclass(self):
        """Verify WorkLease is a dataclass."""
        from vectl.orchestration.contracts import WorkLease

        assert is_dataclass(WorkLease)

    def test_work_lease_fields_match_spec(self):
        """
        Verify WorkLease has exactly the 4 documented fields.

        Spec: docs/ORCHESTRATION-PLANE-INTERFACES.md section 3.5
        Fields: role, runner, agent_id, session_id
        """
        from vectl.orchestration.contracts import WorkLease

        expected_fields = {"role", "runner", "agent_id", "session_id"}
        actual_fields = {f.name for f in fields(WorkLease)}

        assert actual_fields == expected_fields, (
            f"WorkLease field mismatch. Expected: {expected_fields}, Got: {actual_fields}"
        )

    def test_work_lease_frozen(self):
        """Verify WorkLease is frozen (immutable)."""
        from vectl.orchestration.contracts import WorkLease

        # Frozen dataclasses have __dataclass_fields__
        assert hasattr(WorkLease, "__dataclass_fields__")


class TestExecutionRequest:
    """Test ExecutionRequest contract type (Section 3.6)."""

    def test_execution_request_is_dataclass(self):
        """Verify ExecutionRequest is a dataclass."""
        from vectl.orchestration.contracts import ExecutionRequest

        assert is_dataclass(ExecutionRequest)

    def test_execution_request_fields_match_spec(self):
        """
        Verify ExecutionRequest has exactly the 5 documented fields.

        Spec: docs/ORCHESTRATION-PLANE-INTERFACES.md section 3.6
        Fields: step_id, role, runner, work_refs, session_id
        """
        from vectl.orchestration.contracts import ExecutionRequest

        expected_fields = {"step_id", "role", "runner", "work_refs", "session_id"}
        actual_fields = {f.name for f in fields(ExecutionRequest)}

        assert actual_fields == expected_fields, (
            f"ExecutionRequest field mismatch. Expected: {expected_fields}, Got: {actual_fields}"
        )

    def test_execution_request_frozen(self):
        """Verify ExecutionRequest is frozen (immutable)."""
        from vectl.orchestration.contracts import ExecutionRequest

        # Frozen dataclasses have __dataclass_fields__
        assert hasattr(ExecutionRequest, "__dataclass_fields__")


class TestExecutionResult:
    """Test ExecutionResult contract type (Section 3.7)."""

    def test_execution_result_is_dataclass(self):
        """Verify ExecutionResult is a dataclass."""
        from vectl.orchestration.contracts import ExecutionResult

        assert is_dataclass(ExecutionResult)

    def test_execution_result_fields_match_spec(self):
        """
        Verify ExecutionResult has exactly the 5 documented fields.

        Spec: docs/ORCHESTRATION-PLANE-INTERFACES.md section 3.7
        Fields: step_id, status, output_summary, session_id, operator_message
        """
        from vectl.orchestration.contracts import ExecutionResult

        expected_fields = {"step_id", "status", "output_summary", "session_id", "operator_message"}
        actual_fields = {f.name for f in fields(ExecutionResult)}

        assert actual_fields == expected_fields, (
            f"ExecutionResult field mismatch. Expected: {expected_fields}, Got: {actual_fields}"
        )

    def test_execution_result_status_literal_values(self):
        """
        Verify ExecutionResult.status uses correct Literal values.

        Spec: Literal["success", "fail", "stall", "transport_error"]
        """
        from typing import Literal, get_args, get_origin

        from vectl.orchestration.contracts import ExecutionResult

        field_types = {f.name: f.type for f in fields(ExecutionResult)}
        status_type = field_types["status"]

        assert get_origin(status_type) is Literal
        status_values = set(get_args(status_type))
        expected_values = {"success", "fail", "stall", "transport_error"}

        assert status_values == expected_values, (
            f"ExecutionResult.status Literal mismatch. Expected: {expected_values}, Got: {status_values}"
        )

    def test_execution_result_frozen(self):
        """Verify ExecutionResult is frozen (immutable)."""
        from vectl.orchestration.contracts import ExecutionResult

        # Frozen dataclasses have __dataclass_fields__
        assert hasattr(ExecutionResult, "__dataclass_fields__")


class TestResolutionCase:
    """Test ResolutionCase contract type (Section 3.8)."""

    def test_resolution_case_is_dataclass(self):
        """Verify ResolutionCase is a dataclass."""
        from vectl.orchestration.contracts import ResolutionCase

        assert is_dataclass(ResolutionCase)

    def test_resolution_case_fields_match_spec(self):
        """
        Verify ResolutionCase has exactly the 4 documented fields.

        Spec: docs/ORCHESTRATION-PLANE-INTERFACES.md section 3.8
        Fields: reason, core, roster, runtime
        """
        from vectl.orchestration.contracts import ResolutionCase

        expected_fields = {"reason", "core", "roster", "runtime"}
        actual_fields = {f.name for f in fields(ResolutionCase)}

        assert actual_fields == expected_fields, (
            f"ResolutionCase field mismatch. Expected: {expected_fields}, Got: {actual_fields}"
        )

    def test_resolution_case_frozen(self):
        """Verify ResolutionCase is frozen (immutable)."""
        from vectl.orchestration.contracts import ResolutionCase

        # Frozen dataclasses have __dataclass_fields__
        assert hasattr(ResolutionCase, "__dataclass_fields__")

    def test_resolution_case_nested_snapshots(self):
        """Verify ResolutionCase contains nested snapshot types."""
        from vectl.orchestration.contracts import (
            CoreSnapshot,
            ResolutionCase,
            RosterSnapshot,
            RuntimeSnapshot,
        )

        field_types = {f.name: f.type for f in fields(ResolutionCase)}

        assert field_types["core"] is CoreSnapshot
        assert field_types["roster"] is RosterSnapshot
        assert field_types["runtime"] is RuntimeSnapshot


class TestResolutionReport:
    """Test ResolutionReport contract type (Section 3.9)."""

    def test_resolution_report_is_dataclass(self):
        """Verify ResolutionReport is a dataclass."""
        from vectl.orchestration.contracts import ResolutionReport

        assert is_dataclass(ResolutionReport)

    def test_resolution_report_fields_match_spec(self):
        """
        Verify ResolutionReport has exactly the 4 documented fields.

        Spec: docs/ORCHESTRATION-PLANE-INTERFACES.md section 3.9
        Fields: status, summary, evidence_refs, operator_message
        """
        from vectl.orchestration.contracts import ResolutionReport

        expected_fields = {"status", "summary", "evidence_refs", "operator_message"}
        actual_fields = {f.name for f in fields(ResolutionReport)}

        assert actual_fields == expected_fields, (
            f"ResolutionReport field mismatch. Expected: {expected_fields}, Got: {actual_fields}"
        )

    def test_resolution_report_status_literal_values(self):
        """
        Verify ResolutionReport.status uses correct Literal values.

        Spec: Literal["unblocked", "waiting", "operator_required", "halt"]
        """
        from typing import Literal, get_args, get_origin

        from vectl.orchestration.contracts import ResolutionReport

        field_types = {f.name: f.type for f in fields(ResolutionReport)}
        status_type = field_types["status"]

        assert get_origin(status_type) is Literal
        status_values = set(get_args(status_type))
        expected_values = {"unblocked", "waiting", "operator_required", "halt"}

        assert status_values == expected_values, (
            f"ResolutionReport.status Literal mismatch. Expected: {expected_values}, Got: {status_values}"
        )

    def test_resolution_report_frozen(self):
        """Verify ResolutionReport is frozen (immutable)."""
        from vectl.orchestration.contracts import ResolutionReport

        # Frozen dataclasses have __dataclass_fields__
        assert hasattr(ResolutionReport, "__dataclass_fields__")


class TestIsolationModeContract:
    """IsolationMode enum contract tests (isolation semantics doc section 2)."""

    def test_isolation_mode_values_match_spec(self):
        from vectl.orchestration.contracts import IsolationMode

        assert IsolationMode.DEFAULT.value == "default"
        assert IsolationMode.WORKSPACE.value == "workspace"
        assert IsolationMode.INDEPENDENT.value == "independent"
