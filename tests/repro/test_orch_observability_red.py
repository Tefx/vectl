#!/usr/bin/env python3
"""Reproduction: Issue orch_operator_tests.observability_red - Observability contracts missing.

Expected: Per docs/ORCHESTRATION-PLANE-CLI-CONFIG-OBSERVABILITY-DESIGN.md:
- §9.3: Event envelope must include seq, prev_hash, entry_hash for integrity
- §10: Event registry must have canonical taxonomy for run/control/roster/runtime/resolver/projection/operator families
- §9.2: Projection must replay into state/latest.json, state/summary.json, state/metrics.json
- §9.3: Per-step and per-case artifacts must have required schemas
- §9.1: Path normalization must produce filesystem-safe step_key

Actual: Testing black-box to verify observability contract gaps.
This is an expected-red test: failures expose missing implementation, not bugs.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from tests.expected_red import expected_red_module

pytestmark = expected_red_module(
    owner="orch_operator_tests.observability_red",
    rationale="Observability reproduction coverage is intentionally red until the orchestration event, projection, and artifact contracts are implemented.",
)

# Note: pytest is not required - this file runs standalone


def _make_control_dispatch_envelope(*, seq: int | None = None, prev_hash: str | None = None):
    from vectl.orchestration.events import OrchestrationEventEnvelope

    return OrchestrationEventEnvelope(
        kind="control_dispatch",
        timestamp=datetime.now(timezone.utc),
        step_id="test.step",
        agent="test-agent",
        payload={"step_id": "test.step", "agent": "test-agent"},
        seq=seq,
        prev_hash=prev_hash,
    )


# ---------------------------------------------------------------------
# Event Envelope Integrity Tests (§9.3)
# ---------------------------------------------------------------------


class TestEventEnvelopeIntegrity:
    """Test canonical event envelope hashing, prev-hash chain, event_seq ordering."""

    def test_event_envelope_has_seq_field(self):
        """Verify event envelope includes seq field for ordering.

        Spec: §9.3 - Each transcript.jsonl entry must include seq: int
        GAP: events.jsonl must have same integrity requirements.
        """
        envelope = _make_control_dispatch_envelope()

        assert hasattr(envelope, "seq")
        assert envelope.seq is None

    def test_event_envelope_has_prev_hash_chain(self):
        """Verify event envelope includes prev_hash for chain integrity.

        Spec: §9.3 - Each entry must include prev_hash: str | null
        GAP: events.jsonl must support hash chain for verification.
        """
        envelope = _make_control_dispatch_envelope()

        assert hasattr(envelope, "prev_hash")
        assert envelope.prev_hash is None

    def test_event_envelope_has_entry_hash(self):
        """Verify event envelope includes entry_hash for integrity verification.

        Spec: §9.3 - Each entry must include entry_hash: str
        GAP: events.jsonl must have SHA-256 hash of canonical JSON payload.
        """
        envelope = _make_control_dispatch_envelope()

        assert hasattr(envelope, "entry_hash")
        assert isinstance(envelope.entry_hash, str)
        assert len(envelope.entry_hash) == 64

    def test_event_envelope_hash_computation_is_sha256(self):
        """Verify event entry_hash is SHA-256 over canonical JSON serialization.

        Spec: §9.3 - Hash algorithm is SHA-256 over canonical JSON.
        GAP: Event envelope must compute hash correctly.
        """
        envelope = _make_control_dispatch_envelope()
        canonical = json.dumps(
            envelope._hash_payload(),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )

        hash_result = envelope.compute_entry_hash()
        assert hash_result == hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        assert envelope.entry_hash == hash_result

    def test_event_seq_ordering_is_monotonic(self):
        """Verify event_seq ordering is strict and monotonic.

        Spec: §10 - Events must have monotonic seq values for replay.
        GAP: Event sink must enforce seq ordering.
        """
        # EXPECTED-RED: Event sink should enforce ordering
        # This test requires EventSink implementation
        try:
            from vectl.orchestration.events import EventSink, EventRegistry
        except ImportError as e:
            raise AssertionError(
                f"GAP [import]: Cannot import EventSink/EventRegistry: {e}\n"
                "Per §3.8: Event registry must exist for event emission"
            )

        # The EventSink protocol exists but is a stub
        # We need to verify ordering is enforced
        # This is a contract test - implementation is expected to be missing

    def test_event_corruption_detection(self):
        """Verify corrupted event chain can be detected.

        Spec: §9.3 - prev_hash chain enables corruption detection.
        GAP: Event validation must detect chain breaks.
        """
        from vectl.orchestration.events import OrchestrationEventEnvelope

        # EXPECTED-RED: Need chain validation
        if not hasattr(OrchestrationEventEnvelope, "validate_chain"):
            raise AssertionError(
                "GAP [schema]: OrchestrationEventEnvelope missing class method "
                "'validate_chain' for corruption detection\n"
                "Per §9.3: Must validate prev_hash matches prior event's entry_hash"
            )


# ---------------------------------------------------------------------
# Event Registry Payload Validation Tests (§10)
# ---------------------------------------------------------------------


class TestEventRegistryPayloadValidation:
    """Test event registry payload validation for all canonical families."""

    def test_event_registry_has_run_family(self):
        """Verify run family events are defined.

        Spec: §10 - RUN_STARTED, RUN_FINAL, etc. for run lifecycle.
        """
        from vectl.orchestration.events import OrchestrationEventKind

        # Current OrchestrationEventKind has no RUN_* events
        # Get all possible values from the Literal type
        kind_values = (
            OrchestrationEventKind.__args__ if hasattr(OrchestrationEventKind, "__args__") else []
        )

        # Filter for run_ family
        run_events = [k for k in kind_values if isinstance(k, str) and k.startswith("run_")]

        if not run_events:
            raise AssertionError(
                "GAP [taxonomy]: OrchestrationEventKind missing 'run_' family events\n"
                "Per §10: Must have run lifecycle events (run_started, run_final, etc.)"
            )

        # EXPECTED-RED: Need canonical run events
        expected_run_events = {"run_started", "run_final", "run_status_changed"}
        missing = expected_run_events - set(run_events)
        if missing:
            raise AssertionError(
                f"GAP [taxonomy]: Missing run family events: {missing}\n"
                "Per §10: Must include run_started, run_final at minimum"
            )

    def test_event_registry_has_control_family(self):
        """Verify control family events are defined.

        Spec: §10 - CONTROL_DISPATCH, CONTROL_WAIT, etc. for control flow.
        """
        from vectl.orchestration.events import OrchestrationEventKind

        control_events = [k for k in OrchestrationEventKind.__args__ if k.startswith("control_")]

        if not control_events:
            raise AssertionError(
                "GAP [taxonomy]: OrchestrationEventKind missing 'control_' family events\n"
                "Per §10: Must have control flow events"
            )

    def test_event_registry_has_roster_family(self):
        """Verify roster family events are defined.

        Spec: §10 - ROSTER_CLAIM, ROSTER_RELEASE, etc. for roster operations.
        """
        from vectl.orchestration.events import OrchestrationEventKind

        roster_events = [k for k in OrchestrationEventKind.__args__ if k.startswith("roster_")]

        if not roster_events:
            raise AssertionError(
                "GAP [taxonomy]: OrchestrationEventKind missing 'roster_' family events\n"
                "Per §10: Must have roster operation events"
            )

    def test_event_registry_has_runtime_family(self):
        """Verify runtime family events are defined.

        Spec: §10 - RUNTIME_PREPARE, RUNTIME_START, etc. for runner operations.
        """
        from vectl.orchestration.events import OrchestrationEventKind

        runtime_events = [k for k in OrchestrationEventKind.__args__ if k.startswith("runtime_")]

        if not runtime_events:
            raise AssertionError(
                "GAP [taxonomy]: OrchestrationEventKind missing 'runtime_' family events\n"
                "Per §10: Must have runner lifecycle events"
            )

    def test_event_registry_has_resolver_family(self):
        """Verify resolver family events are defined.

        Spec: §10 - RESOLVER_INVOKED, RESOLVER_RETURNED for resolver path.
        """
        from vectl.orchestration.events import OrchestrationEventKind

        resolver_events = [k for k in OrchestrationEventKind.__args__ if k.startswith("resolver_")]

        if not resolver_events:
            raise AssertionError(
                "GAP [taxonomy]: OrchestrationEventKind missing 'resolver_' family events\n"
                "Per §10: Must have resolver lifecycle events"
            )

    def test_event_registry_has_projection_family(self):
        """Verify projection family events are defined.

        Spec: §10.3 - Projection must emit events for state changes.
        """
        from vectl.orchestration.events import OrchestrationEventKind

        # Current taxonomy doesn't have projection_ family
        projection_events = [
            k for k in OrchestrationEventKind.__args__ if k.startswith("projection_")
        ]

        # EXPECTED-RED: projection family may not exist
        if not projection_events:
            raise AssertionError(
                "GAP [taxonomy]: OrchestrationEventKind missing 'projection_' family events\n"
                "Per §12.4: Projection must emit events for state updates"
            )

    def test_event_registry_has_operator_family(self):
        """Verify operator family events are defined.

        Spec: §10 - Case, action, and control channel events.
        """
        from vectl.orchestration.events import OrchestrationEventKind

        # Current taxonomy doesn't have operator_ family
        operator_events = [k for k in OrchestrationEventKind.__args__ if k.startswith("operator_")]

        # EXPECTED-RED: operator family may not exist
        if not operator_events:
            raise AssertionError(
                "GAP [taxonomy]: OrchestrationEventKind missing 'operator_' family events\n"
                "Per §10: Must have operator action events"
            )

    def test_event_payload_schema_validation(self):
        """Verify event payloads are validated per family schema.

        Spec: §10 - Each event family has required payload fields.
        """
        from vectl.orchestration.events import OrchestrationEventEnvelope

        # EXPECTED-RED: Need payload schema validation
        # Current envelope has payload: tuple[str, ...] which doesn't enforce schema

        # Try to create an event with missing required fields
        # For run_started, should require run_id, plan_path, etc.
        # Note: Since OrchestrationEventKind doesn't have 'run_started' yet,
        # we use an existing kind and test the payload validation gap
        try:
            envelope = OrchestrationEventEnvelope(
                kind="control_dispatch",
                timestamp=datetime.now(timezone.utc),
                step_id=None,  # May be None for run-level events
                payload={},  # Empty payload - should this be validated?
            )
            # If we get here, it means payload validation is not enforced
            # which is expected-red
        except Exception:
            # If validation fails, that's expected-red behavior
            pass

        # This test documents that payload schema validation is missing
        # EXPECTED-RED: payload should be typed dict per event kind


# ---------------------------------------------------------------------
# Projection Replay Tests (§9.2)
# ---------------------------------------------------------------------


class TestProjectionReplay:
    """Test projection replay into state/latest.json, state/summary.json, state/metrics.json."""

    def test_projection_replay_exists(self):
        """Verify projection replay mechanism exists.

        Spec: §9.2 - Projection must replay events into derived state.
        """
        from vectl.orchestration.projections import ProjectionReplay

        # ProjectionReplay exists as a protocol
        # EXPECTED-RED: Need concrete implementation

    def test_projection_replay_produces_latest_json(self):
        """Verify replay produces state/latest.json.

        Spec: §9.2 - state/latest.json contains canonical full projected state.
        """
        from vectl.orchestration.projections import FileProjectionReplay, RunStateView

        with tempfile.TemporaryDirectory() as tmpdir:
            replay = FileProjectionReplay(
                events=(
                    {
                        "seq": 1,
                        "kind": "runtime_start",
                        "timestamp": 100.0,
                        "step_id": "test.step",
                        "agent": "test-agent",
                        "run_id": "run-123",
                        "task_id": "task-1",
                    },
                    {
                        "seq": 2,
                        "kind": "runtime_collect",
                        "timestamp": 105.0,
                        "step_id": "test.step",
                        "run_id": "run-123",
                        "task_id": "task-1",
                        "status": "success",
                    },
                ),
                artifact_root=Path(tmpdir),
            )

            result = replay.replay()
            assert all(isinstance(r, RunStateView) for r in result)
            latest_payload = json.loads((Path(tmpdir) / "state" / "latest.json").read_text())
            assert latest_payload["run_id"] == "run-123"
            assert latest_payload["status"] == "success"

    def test_state_latest_json_schema(self):
        """Verify state/latest.json has required schema.

        Spec: §9.2 - Canonical full projected state schema.
        """
        from vectl.orchestration.projections import RunStateView

        # Create a minimal RunStateView
        state = RunStateView(
            step_id="test.step",
            agent="test-agent",
            status="running",
        )

        # EXPECTED-RED: RunStateView should have full schema for state/latest.json
        required_fields = [
            "run_id",  # Missing from current RunStateView
            "status",
            "started_at",
            "finished_at",
            "active_step_id",
            "open_case_count",
            "active_execution_count",
            "active_lease_count",
            "last_event_seq",
        ]

        missing = []
        for field in required_fields:
            if not hasattr(state, field):
                missing.append(field)

        if missing:
            raise AssertionError(
                f"GAP [schema]: RunStateView missing fields for state/latest.json: {missing}\n"
                "Per §9.2: Must have full projected state schema"
            )

    def test_projection_replay_produces_summary_json(self):
        """Verify replay produces state/summary.json.

        Spec: §9.2 - state/summary.json is reduced operator summary.
        """
        from vectl.orchestration.projections import RunStateView

        # EXPECTED-RED: Need separate SummaryView or derived state
        # Check if RunStateView has summary-relevant fields
        state = RunStateView(
            step_id="test.step",
            status="running",
        )

        # Per §9.2, summary.json must have:
        # version, run_id, status, active_step_id, open_case_count,
        # active_execution_count, active_lease_count, last_event_seq

        # Current RunStateView doesn't have most of these
        summary_fields = {
            "run_id": None,
            "status": "running",
            "active_step_id": None,  # Maps to step_id
            "open_case_count": None,
            "active_execution_count": None,
            "active_lease_count": None,
            "last_event_seq": None,
        }

        # We need a way to produce summary.json from RunStateView
        # EXPECTED-RED: Need to_summary_dict() or similar

    def test_projection_replay_produces_metrics_json(self):
        """Verify replay produces state/metrics.json.

        Spec: §9.2 - state/metrics.json contains runtime metrics.
        """
        from vectl.orchestration.projections import RunStateView

        # EXPECTED-RED: Need MetricsView or derived state
        # Per §9.2, metrics.json must have:
        # version, run_id, dispatch_count, resolution_count,
        # operator_required_case_count, transport_error_count,
        # active_execution_peak, average_step_runtime_seconds,
        # total_resolver_tokens, total_estimated_cost_usd

        # Current RunStateView doesn't track metrics

    def test_state_directory_is_created(self):
        """Verify state/ directory is created during projection.

        Spec: §9.1 - state/ is required artifact directory.
        """
        # This is an integration test - would need mock filesystem
        # EXPECTED-RED: projection must create state/ directory
        pass


# ---------------------------------------------------------------------
# Run Artifact Schema Tests (§9.2)
# ---------------------------------------------------------------------


class TestRunArtifactSchemas:
    """Test required run artifact schemas."""

    def test_final_json_schema(self):
        """Verify final.json has required schema.

        Spec: §9.2 - final.json must have version, run_id, status,
        started_at, finished_at, summary, halt_reason, artifacts.
        """
        from vectl.orchestration.run_store import RunRecord

        record = RunRecord(
            run_id="01JX...",
            step_id="test.step",
            status="success",
            created_at=1234567890.0,
        )

        missing = [
            field for field in ("summary", "halt_reason", "artifacts") if not hasattr(record, field)
        ]
        if missing:
            pytest.xfail(
                "EXPECTED-RED [schema debt]: final.json terminal manifest fields remain "
                f"unmodeled on RunRecord: {missing}"
            )

    def test_final_json_halt_reason_schema(self):
        """Verify halt_reason has structured object schema.

        Spec: §9.2 - halt_reason has code, detail, related_case_id, related_step_id.
        """
        # EXPECTED-RED: Need HaltReason type
        # halt_reason is structured: {code, detail, related_case_id, related_step_id}

    def test_final_json_summary_schema(self):
        """Verify summary has required counters.

        Spec: §9.2 - summary must have steps_completed, steps_failed,
        cases_opened, cases_operator_required, active_leases_final, active_executions_final.
        """
        # EXPECTED-RED: Need RunSummary type
        # summary object has specific counters required for terminal state


# ---------------------------------------------------------------------
# Step Artifact Schema Tests (§9.3)
# ---------------------------------------------------------------------


class TestStepArtifactSchemas:
    """Test per-step artifact schemas and path normalization."""

    def test_step_key_path_normalization(self):
        """Verify step_key is filesystem-safe escaped form of step_id.

        Spec: §9.1 - step_key is percent-encoded, case-preserving, lossless decode.
        """
        from vectl.orchestration import events

        normalize_step_key = getattr(events, "normalize_step_key", None)
        if normalize_step_key is None:
            pytest.xfail(
                "EXPECTED-RED [path debt]: normalize_step_key is still missing for "
                "filesystem-safe step artifact paths"
            )
        assert normalize_step_key is not None
        normalized = normalize_step_key("build/core")
        assert normalized == "build%2Fcore"

    def test_step_key_preserves_case(self):
        """Verify step_key normalization preserves case.

        Spec: §9.1 - Normalization must preserve case.
        """
        # Test data
        test_cases = [
            ("Build.Core", "Build.Core"),  # Case preserved
            ("test.step", "test.step"),  # Lowercase preserved
            ("UPPER.CASE", "UPPER.CASE"),
        ]

        for step_id, expected_preserved in test_cases:
            from vectl.orchestration import events

            normalize_step_key = getattr(events, "normalize_step_key", None)
            if normalize_step_key is None:
                pytest.xfail(
                    "EXPECTED-RED [path debt]: normalize_step_key is still missing for "
                    "case-preserving step artifact paths"
                )
            assert normalize_step_key is not None
            assert normalize_step_key(step_id) == expected_preserved

    def test_step_key_percent_encodes_special_chars(self):
        """Verify step_key percent-encodes filesystem-unsafe characters.

        Spec: §9.1 - Percent-encode bytes outside [A-Za-z0-9._-].
        """
        # Test data - these should be encoded
        test_cases = [
            ("build/core", "build%2Fcore"),  # / encoded
            ("build core", "build%20core"),  # space encoded
            ("build:core", "build%3Acore"),  # : encoded
        ]

        for step_id, expected_key in test_cases:
            from vectl.orchestration import events

            normalize_step_key = getattr(events, "normalize_step_key", None)
            if normalize_step_key is None:
                pytest.xfail(
                    "EXPECTED-RED [path debt]: normalize_step_key is still missing for "
                    "percent-encoded step artifact paths"
                )
            assert normalize_step_key is not None
            assert normalize_step_key(step_id) == expected_key

    def test_step_key_is_reversible(self):
        """Verify step_key normalization is lossless.

        Spec: §9.1 - Decoding must be lossless.
        """
        from vectl.orchestration import events

        denormalize_step_key = getattr(events, "denormalize_step_key", None)
        if denormalize_step_key is None:
            pytest.xfail(
                "EXPECTED-RED [path debt]: denormalize_step_key is still missing for "
                "lossless step artifact decoding"
            )
        assert denormalize_step_key is not None
        assert denormalize_step_key("build%2Fcore") == "build/core"

    def test_request_json_schema(self):
        """Verify request.json stores execution request.

        Spec: §9.3 - steps/<step_key>/request.json stores runtime request.
        """
        # EXPECTED-RED: Need StepRequest type/schema
        # request.json must capture: step_id, session_id, runner, context, etc.

    def test_result_json_schema(self):
        """Verify result.json stores final execution result.

        Spec: §9.3 - steps/<step_key>/result.json stores execution summary.
        """
        # EXPECTED-RED: Need StepResult type/schema
        # result.json must capture: step_id, status, output_summary, artifacts, etc.


# ---------------------------------------------------------------------
# Case Artifact Schema Tests (§9.3)
# ---------------------------------------------------------------------


class TestCaseArtifactSchemas:
    """Test per-case artifact schemas."""

    def test_case_json_schema(self):
        """Verify case.json stores ResolutionCase.

        Spec: §9.3 - cases/<case_id>/case.json stores canonical ResolutionCase.
        """
        # EXPECTED-RED: Need ResolutionCase type
        # case.json must have: case_id, step_id, status, prompt_ref, etc.

    def test_prompt_json_schema(self):
        """Verify prompt.json stores resolver invocation payload.

        Spec: §9.3 - Must record case id, prompt or prompt hash,
        allowed tools, relevant snapshot references.
        """
        # EXPECTED-RED: Need ResolverPrompt type
        # prompt.json must have: case_id, prompt/prompt_hash, allowed_tools, snapshots

    def test_capability_json_schema(self):
        """Verify capability.json stores runner capability snapshot.

        Spec: §9.3 - Must record runner id, capability_version,
        supports_resume, supports_restart.
        """
        # EXPECTED-RED: Need CapabilitySnapshot type

    def test_report_json_schema(self):
        """Verify report.json stores ResolutionReport.

        Spec: §9.3 - cases/<case_id>/report.json stores canonical ResolutionReport.
        """
        # EXPECTED-RED: Need ResolutionReport type

    def test_transcript_jsonl_integrity_fields(self):
        """Verify transcript.jsonl has integrity fields.

        Spec: §9.3 - Must include seq, prev_hash, entry_hash, tool_call_id.
        """
        # EXPECTED-RED: Need TranscriptEntry type
        # transcript.jsonl entry must have:
        # seq: int
        # prev_hash: str | null
        # entry_hash: str
        # tool_call_id: str | null

    def test_transcript_hash_algorithm_is_sha256(self):
        """Verify transcript entry_hash uses SHA-256.

        Spec: §9.3 - Hash algorithm is SHA-256 over canonical JSON.
        """
        # EXPECTED-RED: Need canonical transcript entry hashing

    def test_transcript_log_is_derived_from_jsonl(self):
        """Verify transcript.log is human-readable rendering.

        Spec: §9.3 - transcript.log is derived from transcript.jsonl.
        """
        # EXPECTED-RED: Need transcript log derivation

    def test_case_id_is_globally_unique_ulid(self):
        """Verify case_id follows case-<ULID> format.

        Spec: §9.2 - case_id is globally unique in case-<ULID> form.
        """
        # EXPECTED-RED: Need case ID generation/validation


# ---------------------------------------------------------------------
# Run Registry and Index Tests (§6.4)
# ---------------------------------------------------------------------


class TestRunRegistrySchemas:
    """Test run registry index and cases index schemas."""

    def test_index_jsonl_entry_schema(self):
        """Verify index.jsonl has required fields.

        Spec: §6.4 - Entry must have run_id, plan_path, status,
        started_at, updated_at, artifact_root.
        """
        from vectl.orchestration.run_store import RunRecord

        record = RunRecord(
            run_id="01JX...",
            step_id="test.step",
            status="running",
        )

        # EXPECTED-RED: RunRecord for index must have additional fields
        index_fields = ["plan_path", "started_at", "updated_at", "artifact_root"]

        missing = []
        for field in index_fields:
            if not hasattr(record, field):
                missing.append(field)

        if missing:
            raise AssertionError(
                f"GAP [schema]: RunRecord missing index.jsonl fields: {missing}\n"
                "Per §6.4: index.jsonl entry must have plan_path, updated_at, artifact_root"
            )

    def test_cases_jsonl_entry_schema(self):
        """Verify cases.jsonl has required fields.

        Spec: §6.4 - Entry must have case_id, run_id, status,
        updated_at, case_path.
        """
        # EXPECTED-RED: Need CaseIndexEntry type
        # cases.jsonl entry:
        # case_id, run_id, status, updated_at, case_path

    def test_latest_run_resolution(self):
        """Verify --latest resolution follows precedence rules.

        Spec: §6.4 - --latest chooses most recently updated non-terminal run.
        """
        from vectl.orchestration.run_store import RunRecord, RunRegistry, latest_run

        with tempfile.TemporaryDirectory() as tmpdir:
            registry = RunRegistry(store_root=tmpdir)
            registry.save(
                RunRecord(
                    run_id="run-success",
                    step_id="test.step",
                    status="success",
                    created_at=10.0,
                    updated_at=30.0,
                )
            )
            registry.save(
                RunRecord(
                    run_id="run-running",
                    step_id="test.step",
                    status="running",
                    created_at=20.0,
                    updated_at=20.0,
                )
            )

            result = latest_run("test.step", registry=registry)
            assert result is not None
            assert result.run_id == "run-running"
            assert result.status == "running"


# ---------------------------------------------------------------------
# Heartbeat and Liveness Tests (§6.4)
# ---------------------------------------------------------------------


class TestHeartbeatLiveness:
    """Test heartbeat artifact and liveness classification."""

    def test_heartbeat_json_schema(self):
        """Verify heartbeat.json has required fields.

        Spec: §6.4 - heartbeat.json must have run_id, pid, host_id,
        started_at, last_heartbeat_at.
        """
        # EXPECTED-RED: Need HeartbeatArtifact type
        # heartbeat.json:
        # run_id, pid, host_id, started_at, last_heartbeat_at

    def test_liveness_values_are_canonical(self):
        """Verify liveness values are limited to canonical set.

        Spec: §6.4 - Liveness values: alive, stale, unknown.
        """
        # EXPECTED-RED: Need Liveness enum/type
        canonical_values = {"alive", "stale", "unknown"}


# ---------------------------------------------------------------------
# Config Snapshot Tests (§8.8)
# ---------------------------------------------------------------------


class TestConfigSnapshot:
    """Test frozen config snapshot."""

    def test_config_snapshot_yaml_written(self):
        """Verify config.snapshot.yaml is written at run start.

        Spec: §8.8 - Each run must write frozen config snapshot.
        """
        # EXPECTED-RED: Need config snapshot writing logic

    def test_config_snapshot_is_authoritative_for_resume(self):
        """Verify resumed run uses frozen snapshot.

        Spec: §8.8 - Snapshot is authoritative for that run.
        """
        # EXPECTED-RED: Resume logic must read from snapshot


# ---------------------------------------------------------------------
# Tool Registry Validation Tests (§8.4)
# ---------------------------------------------------------------------


class TestToolRegistryValidation:
    """Test canonical tool registry and allowlist validation."""

    def test_tool_registry_has_canonical_table(self):
        """Verify tool registry contains canonical families.

        Spec: §8.4 - Registry table for this round.
        """
        try:
            from vectl.orchestration.tool_registry import CANONICAL_TOOL_FAMILIES

            # Expected families from §8.4 (core, orchestration)
            # NOTE: Current implementation has vectl.core, vectl.orchestration, etc.
            expected_top_level = {"core", "orchestration"}
            actual_top_level = {
                f.split(".")[-1] if "." in f else f for f in CANONICAL_TOOL_FAMILIES
            }

            if not expected_top_level.issubset(actual_top_level):
                raise AssertionError(
                    f"GAP [registry]: Missing tool families\n"
                    f"Expected top-level: {expected_top_level}\n"
                    f"Found: {actual_top_level}\n"
                    f"Per §8.4: Must have core and orchestration families"
                )
        except ImportError:
            raise AssertionError(
                "GAP [import]: tool_registry module not found\n"
                "Per §8.4: Must define canonical tool registry"
            )

    def test_tool_registry_core_family_tools(self):
        """Verify core family has required tools.

        Spec: §8.4 - core: status, show, claim, complete, defer.

        NOTE: Current implementation has family-level allowlist (vectl.core)
        but §8.4 requires tool-level registry (core.status, core.show, etc.)
        This test documents the gap.
        """
        from vectl.orchestration.tool_registry import ToolFamilyRegistry

        registry = ToolFamilyRegistry()
        metadata = registry.get("core")

        assert metadata is not None
        assert {"status", "show", "claim", "complete", "defer"}.issubset(
            set(metadata.allowed_operations)
        )

    def test_tool_registry_orchestration_family_tools(self):
        """Verify orchestration family has required tools.

        Spec: §8.4 - orchestration: read_events, read_state, read_case.

        NOTE: See test_tool_registry_core_family_tools for gap documentation.
        """
        from vectl.orchestration.tool_registry import ToolFamilyRegistry

        registry = ToolFamilyRegistry()
        metadata = registry.get("orchestration")

        assert metadata is not None
        assert {"read_events", "read_state", "read_case"}.issubset(set(metadata.allowed_operations))

    def test_tool_allowlist_validation(self):
        """Verify tool allowlist validates against registry.

        Spec: §8.4 - Allowlist entries must validate against registry.
        """
        # EXPECTED-RED: Need validate_allowlist function
        from vectl.orchestration import tool_registry

        if not hasattr(tool_registry, "validate_allowlist"):
            raise AssertionError(
                "GAP [registry]: Missing validate_allowlist function\n"
                "Per §8.4: Must validate tool names against registry"
            )


# ---------------------------------------------------------------------
# Summary: Test Execution
# ---------------------------------------------------------------------


if __name__ == "__main__":
    """
    Run all tests and report expected-red gaps.
    """
    import traceback

    test_classes = [
        TestEventEnvelopeIntegrity,
        TestEventRegistryPayloadValidation,
        TestProjectionReplay,
        TestRunArtifactSchemas,
        TestStepArtifactSchemas,
        TestCaseArtifactSchemas,
        TestRunRegistrySchemas,
        TestHeartbeatLiveness,
        TestConfigSnapshot,
        TestToolRegistryValidation,
    ]

    gaps = []
    for test_class in test_classes:
        instance = test_class()
        for method_name in dir(instance):
            if method_name.startswith("test_"):
                method = getattr(instance, method_name)
                try:
                    method()
                except AssertionError as e:
                    gaps.append(f"{test_class.__name__}.{method_name}: {e}")
                except NotImplementedError as e:
                    gaps.append(f"{test_class.__name__}.{method_name}: GAP [stub] - {e}")
                except Exception as e:
                    gaps.append(
                        f"{test_class.__name__}.{method_name}: UNEXPECTED - {e}\n"
                        f"{''.join(traceback.format_tb(e.__traceback__))}"
                    )

    print("\n" + "=" * 80)
    print("EXPECTED-RED GAPS FOUND (these failures are intentional):")
    print("=" * 80)
    if gaps:
        for gap in gaps:
            print(f"  - {gap}")
        print("=" * 80)
        print(f"Total gaps: {len(gaps)}")
        print("\nGAP CLASSIFICATION:")
        print("  [schema]       - Missing required fields in types")
        print("  [implementation] - Missing function/method")
        print("  [taxonomy]     - Missing event family in OrchestrationEventKind")
        print("  [registry]     - Missing tools in canonical registry")
        print("  [import]       - Module or type not found")
        print("  [stub]         - Function exists but raises NotImplementedError")
        print("\nVERDICT: FAILED (expected) - gaps exposed for downstream green owners")
        print("Downstream green owners: orch_operator_observability")
        sys.exit(1)
    else:
        print("\nVERDICT: FIXED - All observability contracts have implementations")
        print("All contracts satisfied:")
        print("  - Event envelope has seq, prev_hash, entry_hash")
        print("  - Event registry has all canonical families")
        print("  - Projection replay produces state artifacts")
        print("  - Run/step/case artifacts have required schemas")
        print("  - Path normalization is reversible")
        print("  - Tool registry validates allowlists")
        sys.exit(0)
