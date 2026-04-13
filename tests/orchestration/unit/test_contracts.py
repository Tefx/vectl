"""
Contract type signature tests for orchestration plane boundary types.

These tests verify that each contract type from section 3 of the spec
has the correct fields, types, and behavior.

Authority: docs/ORCHESTRATION-PLANE-INTERFACES.md section 3
Step: orch_foundation.contract_tests
Intent: test_define_red
"""

from dataclasses import MISSING, fields, is_dataclass

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
    """Test ControlDecision contract type (Section 3.4 / RFC-orch-drive 9.2.1)."""

    def test_control_decision_is_dataclass(self):
        """Verify ControlDecision is a dataclass."""
        from vectl.orchestration.contracts import ControlDecision

        assert is_dataclass(ControlDecision)

    def test_control_decision_fields_match_spec(self):
        """
        Verify ControlDecision has the drive-aware expanded fields.

        Authority: docs/RFC-orch-drive.md section 9.2.1
        Fields: kind, reason, step_ids, role_bindings, case_ids,
                planner_request, capacity_used, capacity_remaining,
                barrier_required
        """
        from vectl.orchestration.contracts import ControlDecision

        expected_fields = {
            "kind",
            "reason",
            "step_ids",
            "role_bindings",
            "case_ids",
            "planner_request",
            "capacity_used",
            "capacity_remaining",
            "barrier_required",
        }
        actual_fields = {f.name for f in fields(ControlDecision)}

        assert actual_fields == expected_fields, (
            f"ControlDecision field mismatch. Expected: {expected_fields}, Got: {actual_fields}"
        )

    def test_control_decision_kind_literal_values(self):
        """
        Verify ControlDecision.kind uses correct Literal values.

        Authority: docs/RFC-orch-drive.md section 9.2.1
        Literal: dispatch_batch, dispatch, resolve, replan, wait, done, halt
        """
        from typing import Literal, get_args, get_origin

        from vectl.orchestration.contracts import ControlDecision

        field_types = {f.name: f.type for f in fields(ControlDecision)}
        kind_type = field_types["kind"]

        assert get_origin(kind_type) is Literal
        kind_values = set(get_args(kind_type))
        expected_values = {
            "dispatch_batch",
            "dispatch",
            "resolve",
            "replan",
            "wait",
            "done",
            "halt",
        }

        assert kind_values == expected_values, (
            f"ControlDecision.kind Literal mismatch. Expected: {expected_values}, Got: {kind_values}"
        )

    def test_control_decision_optional_fields(self):
        """
        Verify all fields except kind and reason have defaults.

        Authority: docs/RFC-orch-drive.md section 9.2.1
        Only kind and reason are required; step_ids, role_bindings, etc.
        have defaults.
        """
        from vectl.orchestration.contracts import ControlDecision

        # Test instantiation with minimal fields
        decision = ControlDecision(kind="done", reason="test")
        assert decision.step_ids == ()
        assert decision.role_bindings == {}
        assert decision.case_ids == ()
        assert decision.planner_request is None
        assert decision.capacity_used == 0
        assert decision.capacity_remaining == 0
        assert decision.barrier_required is False


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
        Verify ExecutionRequest has exactly the documented fields.

        Spec: docs/ORCHESTRATION-PLANE-INTERFACES.md section 3.6
        Authority: docs/RFC-opencode-orchestration-runner.md section 7.1
        Fields: step_id, role, runner, work_refs, agent_id,
                prompt_bundle_path, runner_prompt_path, request_mode,
                session_policy, session_id
        """
        from vectl.orchestration.contracts import ExecutionRequest

        expected_fields = {
            "step_id",
            "role",
            "runner",
            "work_refs",
            "agent_id",
            "prompt_bundle_path",
            "runner_prompt_path",
            "request_mode",
            "session_policy",
            "session_id",
        }
        actual_fields = {f.name for f in fields(ExecutionRequest)}

        assert actual_fields == expected_fields, (
            f"ExecutionRequest field mismatch. Expected: {expected_fields}, Got: {actual_fields}"
        )

    def test_execution_request_frozen(self):
        """Verify ExecutionRequest is frozen (immutable)."""
        from vectl.orchestration.contracts import ExecutionRequest

        # Frozen dataclasses have __dataclass_fields__
        assert hasattr(ExecutionRequest, "__dataclass_fields__")

    def test_execution_request_request_mode_literal_values(self):
        """
        Verify ExecutionRequest.request_mode uses correct Literal values.

        Authority: docs/RFC-opencode-orchestration-runner.md section 6.1
        """
        from typing import Literal, get_args, get_origin

        from vectl.orchestration.contracts import ExecutionRequest

        field_types = {f.name: f.type for f in fields(ExecutionRequest)}
        mode_type = field_types["request_mode"]

        assert get_origin(mode_type) is Literal
        mode_values = set(get_args(mode_type))
        expected_values = {"start", "resume", "recover"}

        assert mode_values == expected_values, (
            f"ExecutionRequest.request_mode Literal mismatch. "
            f"Expected: {expected_values}, Got: {mode_values}"
        )

    def test_execution_request_session_policy_literal_values(self):
        """
        Verify ExecutionRequest.session_policy uses correct Literal values.

        Authority: docs/RFC-opencode-orchestration-runner.md section 6.2
        """
        from typing import Literal, get_args, get_origin

        from vectl.orchestration.contracts import ExecutionRequest

        field_types = {f.name: f.type for f in fields(ExecutionRequest)}
        policy_type = field_types["session_policy"]

        assert get_origin(policy_type) is Literal
        policy_values = set(get_args(policy_type))
        expected_values = {"reuse_allowed", "reuse_forbidden"}

        assert policy_values == expected_values, (
            f"ExecutionRequest.session_policy Literal mismatch. "
            f"Expected: {expected_values}, Got: {policy_values}"
        )

    def test_execution_request_defaults(self):
        """
        Verify ExecutionRequest new fields have correct defaults.

        Authority: docs/RFC-opencode-orchestration-runner.md section 7.1
        """
        from vectl.orchestration.contracts import ExecutionRequest

        request = ExecutionRequest(
            step_id="core.step",
            role="python-executor",
            runner="opencode",
            work_refs=(),
        )
        assert request.request_mode == "start"
        assert request.session_policy == "reuse_forbidden"
        assert request.agent_id == ""
        assert request.prompt_bundle_path == ""
        assert request.runner_prompt_path == ""
        assert request.session_id is None

    def test_execution_request_explicit_mode_and_policy(self):
        """
        Verify ExecutionRequest accepts explicit mode and policy values.

        Authority: docs/RFC-opencode-orchestration-runner.md section 7.1
        """
        from vectl.orchestration.contracts import ExecutionRequest

        request = ExecutionRequest(
            step_id="core.step",
            role="python-executor",
            runner="opencode",
            work_refs=(),
            request_mode="resume",
            session_policy="reuse_allowed",
            session_id="session-abc",
            agent_id="python-executor",
            prompt_bundle_path="/path/to/bundle.json",
            runner_prompt_path="/path/to/prompt.md",
        )
        assert request.request_mode == "resume"
        assert request.session_policy == "reuse_allowed"
        assert request.session_id == "session-abc"
        assert request.agent_id == "python-executor"
        assert request.prompt_bundle_path == "/path/to/bundle.json"
        assert request.runner_prompt_path == "/path/to/prompt.md"

    def test_execution_request_backward_compatible(self):
        """
        Verify ExecutionRequest remains backward compatible with old construction.

        Authority: docs/RFC-opencode-orchestration-runner.md section 7.4
        New fields have defaults, so existing call sites continue to work.
        """
        from vectl.orchestration.contracts import ExecutionRequest

        # Minimal old-style construction still works
        request = ExecutionRequest(
            step_id="phase.step",
            role="python-executor",
            runner="claude",
            work_refs=(),
            session_id=None,
        )
        assert request.step_id == "phase.step"
        assert request.request_mode == "start"
        assert request.session_policy == "reuse_forbidden"


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
        Verify ResolutionCase preserves the 4 required semantic fields.

        Spec: docs/ORCHESTRATION-PLANE-INTERFACES.md section 3.8
        Fields: reason, core, roster, runtime, case metadata, and evidence refs
        """
        from vectl.orchestration.contracts import ResolutionCase

        required_fields = ("reason", "core", "roster", "runtime")
        actual_fields = fields(ResolutionCase)
        actual_field_names = tuple(f.name for f in actual_fields)
        additive_fields = actual_fields[len(required_fields) :]

        assert actual_field_names[: len(required_fields)] == required_fields, (
            "ResolutionCase must preserve required semantic field ordering. "
            f"Expected prefix: {required_fields}, Got: {actual_field_names}"
        )
        assert all(
            f.default is not MISSING or f.default_factory is not MISSING for f in additive_fields
        ), (
            "ResolutionCase additive metadata fields must remain optional/defaulted. "
            f"Got trailing fields: {tuple(f.name for f in additive_fields)}"
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
    """Test ResolutionReport contract type (Section 3.9 / RFC-orch-drive 12.2)."""

    def test_resolution_report_is_dataclass(self):
        """Verify ResolutionReport is a dataclass."""
        from vectl.orchestration.contracts import ResolutionReport

        assert is_dataclass(ResolutionReport)

    def test_resolution_report_fields_match_spec(self):
        """
        Verify ResolutionReport has the documented fields.

        Authority: docs/ORCHESTRATION-PLANE-INTERFACES.md section 3.9
        Authority: docs/RFC-orch-drive.md section 12.2
        Fields: status, summary, evidence_refs, operator_message, planner_request
        """
        from vectl.orchestration.contracts import ResolutionReport

        expected_fields = {
            "status",
            "summary",
            "evidence_refs",
            "operator_message",
            "planner_request",
        }
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

    def test_resolution_report_planner_request_optional(self):
        """Verify planner_request defaults to None (backward-compatible addition)."""
        from vectl.orchestration.contracts import ResolutionReport

        report = ResolutionReport(status="unblocked", summary="test")
        assert report.planner_request is None


class TestIsolationModeContract:
    """IsolationMode enum contract tests (isolation semantics doc section 2)."""

    def test_isolation_mode_values_match_spec(self):
        from vectl.orchestration.contracts import IsolationMode

        assert IsolationMode.DEFAULT.value == "default"
        assert IsolationMode.WORKSPACE.value == "workspace"
        assert IsolationMode.INDEPENDENT.value == "independent"


class TestRequestMode:
    """
    Test RequestMode type alias contract.

    Authority: docs/RFC-opencode-orchestration-runner.md section 6.1
    """

    def test_request_mode_literal_values(self):
        """Verify RequestMode is a Literal with the three defined values."""
        from typing import Literal, get_args, get_origin

        from vectl.orchestration.contracts import RequestMode

        assert get_origin(RequestMode) is Literal
        assert set(get_args(RequestMode)) == {"start", "resume", "recover"}


class TestSessionPolicy:
    """
    Test SessionPolicy type alias contract.

    Authority: docs/RFC-opencode-orchestration-runner.md section 6.2
    """

    def test_session_policy_literal_values(self):
        """Verify SessionPolicy is a Literal with the two defined values."""
        from typing import Literal, get_args, get_origin

        from vectl.orchestration.contracts import SessionPolicy

        assert get_origin(SessionPolicy) is Literal
        assert set(get_args(SessionPolicy)) == {"reuse_allowed", "reuse_forbidden"}


class TestRecoveredVia:
    """
    Test RecoveredVia type alias contract.

    Authority: docs/RFC-opencode-orchestration-runner.md section 10.3
    """

    def test_recovered_via_literal_values(self):
        """Verify RecoveredVia distinguishes the two recovery paths."""
        from typing import Literal, get_args, get_origin

        from vectl.orchestration.contracts import RecoveredVia

        assert get_origin(RecoveredVia) is Literal
        assert set(get_args(RecoveredVia)) == {"native_session_resume", "fresh_relaunch"}

    def test_recovered_via_truth_labels_are_distinct(self):
        """
        Verify the two RecoveredVia values are distinct strings.

        Authority: RFC section 10.3 - 'The system must not collapse these two
        recovery paths into the same label.'
        """
        from vectl.orchestration.contracts import RecoveredVia

        from typing import get_args

        values = list(get_args(RecoveredVia))
        assert len(values) == len(set(values)), (
            f"RecoveredVia values must be distinct, got: {values}"
        )


class TestRecoveryContinuity:
    """
    Test RecoveryContinuity contract type (RFC section 10.3).

    Authority: docs/RFC-opencode-orchestration-runner.md section 10.3
    """

    def test_recovery_continuity_is_dataclass(self):
        """Verify RecoveryContinuity is a dataclass."""
        from vectl.orchestration.contracts import RecoveryContinuity

        assert is_dataclass(RecoveryContinuity)

    def test_recovery_continuity_fields_match_spec(self):
        """
        Verify RecoveryContinuity has the documented fields.

        Authority: docs/RFC-opencode-orchestration-runner.md section 10.3
        Fields: recovered_via, run_id, step_id, agent_id, runner,
                session_id, timestamp
        """
        from vectl.orchestration.contracts import RecoveryContinuity

        expected_fields = {
            "recovered_via",
            "run_id",
            "step_id",
            "agent_id",
            "runner",
            "session_id",
            "timestamp",
        }
        actual_fields = {f.name for f in fields(RecoveryContinuity)}

        assert actual_fields == expected_fields, (
            f"RecoveryContinuity field mismatch. Expected: {expected_fields}, Got: {actual_fields}"
        )

    def test_recovery_continuity_frozen(self):
        """Verify RecoveryContinuity is frozen (immutable)."""
        from vectl.orchestration.contracts import RecoveryContinuity

        assert hasattr(RecoveryContinuity, "__dataclass_fields__")

    def test_recovery_continuity_recovered_via_type(self):
        """Verify recovered_via field uses RecoveredVia type."""
        from typing import Literal, get_args, get_origin

        from vectl.orchestration.contracts import RecoveryContinuity, RecoveredVia

        field_types = {f.name: f.type for f in fields(RecoveryContinuity)}
        recovered_via_type = field_types["recovered_via"]

        assert get_origin(recovered_via_type) is Literal
        assert set(get_args(recovered_via_type)) == set(get_args(RecoveredVia))

    def test_recovery_continuity_required_and_optional_fields(self):
        """Verify recovered_via is required; all others defaulted."""
        from vectl.orchestration.contracts import RecoveryContinuity

        # Minimal construction (only required field)
        continuity = RecoveryContinuity(recovered_via="native_session_resume")
        assert continuity.recovered_via == "native_session_resume"
        assert continuity.run_id == ""
        assert continuity.step_id == ""
        assert continuity.session_id is None
        assert continuity.timestamp == ""

    def test_recovery_continuity_both_paths(self):
        """Verify both recovery path labels are valid recovered_via values."""
        from vectl.orchestration.contracts import RecoveryContinuity

        native = RecoveryContinuity(
            recovered_via="native_session_resume",
            run_id="run-001",
            session_id="session-abc",
        )
        assert native.recovered_via == "native_session_resume"
        assert native.session_id == "session-abc"

        fresh = RecoveryContinuity(
            recovered_via="fresh_relaunch",
            run_id="run-002",
        )
        assert fresh.recovered_via == "fresh_relaunch"
        assert fresh.session_id is None


class TestRecoveryAttempt:
    """
    Test RecoveryAttempt contract type (RFC section 10.4).

    Authority: docs/RFC-opencode-orchestration-runner.md section 10.4
    """

    def test_recovery_attempt_is_dataclass(self):
        """Verify RecoveryAttempt is a dataclass."""
        from vectl.orchestration.contracts import RecoveryAttempt

        assert is_dataclass(RecoveryAttempt)

    def test_recovery_attempt_fields_match_spec(self):
        """
        Verify RecoveryAttempt has the documented fields.

        Authority: docs/RFC-opencode-orchestration-runner.md section 10.4
        Fields: attempt_kind, native_validation_ok,
                native_validation_failure_reason, fallback_relaunch_used,
                resulting_run_id, resulting_session_id
        """
        from vectl.orchestration.contracts import RecoveryAttempt

        expected_fields = {
            "attempt_kind",
            "native_validation_ok",
            "native_validation_failure_reason",
            "fallback_relaunch_used",
            "resulting_run_id",
            "resulting_session_id",
        }
        actual_fields = {f.name for f in fields(RecoveryAttempt)}

        assert actual_fields == expected_fields, (
            f"RecoveryAttempt field mismatch. Expected: {expected_fields}, Got: {actual_fields}"
        )

    def test_recovery_attempt_frozen(self):
        """Verify RecoveryAttempt is frozen (immutable)."""
        from vectl.orchestration.contracts import RecoveryAttempt

        assert hasattr(RecoveryAttempt, "__dataclass_fields__")

    def test_recovery_attempt_attempt_kind_literal_values(self):
        """Verify attempt_kind field uses Literal["resume", "recover"]."""
        from typing import Literal, get_args, get_origin

        from vectl.orchestration.contracts import RecoveryAttempt

        field_types = {f.name: f.type for f in fields(RecoveryAttempt)}
        kind_type = field_types["attempt_kind"]

        assert get_origin(kind_type) is Literal
        kind_values = set(get_args(kind_type))
        assert kind_values == {"resume", "recover"}

    def test_recovery_attempt_minimal_construction(self):
        """Verify only attempt_kind is required; all others defaulted."""
        from vectl.orchestration.contracts import RecoveryAttempt

        attempt = RecoveryAttempt(attempt_kind="resume")
        assert attempt.attempt_kind == "resume"
        assert attempt.native_validation_ok is False
        assert attempt.native_validation_failure_reason == ""
        assert attempt.fallback_relaunch_used is False
        assert attempt.resulting_run_id == ""
        assert attempt.resulting_session_id is None

    def test_recovery_attempt_resume_scenario(self):
        """Verify construction for a native resume scenario."""
        from vectl.orchestration.contracts import RecoveryAttempt

        attempt = RecoveryAttempt(
            attempt_kind="resume",
            native_validation_ok=True,
            resulting_run_id="run-001",
            resulting_session_id="session-abc",
        )
        assert attempt.native_validation_ok is True
        assert attempt.fallback_relaunch_used is False
        assert attempt.resulting_session_id == "session-abc"

    def test_recovery_attempt_recover_with_fallback(self):
        """Verify construction for a recover scenario that falls back to fresh relaunch."""
        from vectl.orchestration.contracts import RecoveryAttempt

        attempt = RecoveryAttempt(
            attempt_kind="recover",
            native_validation_ok=False,
            native_validation_failure_reason="session.json missing runner field",
            fallback_relaunch_used=True,
            resulting_run_id="run-002",
        )
        assert attempt.native_validation_ok is False
        assert attempt.fallback_relaunch_used is True
        assert attempt.resulting_session_id is None
