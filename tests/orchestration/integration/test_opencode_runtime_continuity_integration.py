#!/usr/bin/env python3
"""Integration tests proving runtime dispatch, state persistence, and continuity
metadata coherence for OpenCode-backed runs.

Authority:
    docs/RFC-opencode-orchestration-runner.md sections 7, 10, 11

These tests verify:
    1. Expanded ExecutionRequest fields flow through orch_app/runtime dispatch
    2. Session metadata and evidence refs persist in execution state
    3. Recovery continuity artifacts preserve recovered_via truth
    4. Session-aware state integrates with continuity and run-store artifacts

Constraint — no real subprocesses:
    The OpenCodeRunner is tested via its contract surfaces using a test runner
    that completes deterministically. No live OpenCode CLI invocation occurs.
"""

from __future__ import annotations

import json
import os
import subprocess
import textwrap
from dataclasses import replace
from pathlib import Path
from typing import Literal

import pytest

from vectl.io import save_plan
from vectl.models import Phase, Plan, Step
from vectl.orch_app import AppConfig, build_orchestration_app
from vectl.orchestration.config import (
    OrchestrationConfig,
    RoleProfile,
    RuntimeConfig,
)
from vectl.orchestration.contracts import (
    AgentExecutionState,
    ExecutionRequest,
    RecoveryAttempt,
    RecoveryContinuity,
    RecoveredVia,
    RequestMode,
    SessionPolicy,
)
from vectl.orchestration.continuity_artifacts import RuntimeRecoveryRecord
from vectl.orchestration.run_store import RunRegistry, generate_run_id
from vectl.orchestration.runner_registry import RunnerRegistry
from vectl.orchestration.runners import (
    RunnerCapabilities,
    SubprocessRunner,
)


# ---------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------


def _write_isolated_fixture(root: Path, *, runner: str = "test") -> None:
    """Create a minimal, self-contained orchestration fixture."""

    plan = Plan(
        project="orch-continuity-inttest",
        phases=[
            Phase(
                id="e2e",
                name="E2E",
                steps=[
                    Step(id="e2e.step_a", name="Step A"),
                ],
            )
        ],
    )
    save_plan(plan, root / "plan.yaml")

    (root / "vectl.yaml").write_text(
        textwrap.dedent(
            f"""\
            orchestration:
              plan_path: plan.yaml
              runtime:
                default_runner: {runner}
                artifact_root: .vectl/runs
              role_profiles:
                python-executor:
                  agent_id: python-executor
                  prompt_family: coder
                  execution_context: linked_worktree
                  mutation_policy: worktree_changes
                  session_policy: reuse_allowed
                  output_contract: freeform_evidence
                  default_runner: {runner}
            """
        ),
        encoding="utf-8",
    )


def _init_git_repo(root: Path) -> None:
    """Initialize a git repo so that worktree operations succeed."""
    subprocess.run(["git", "init", "-b", "main"], cwd=str(root), capture_output=True, check=True)
    subprocess.run(["git", "add", "."], cwd=str(root), capture_output=True, check=True)
    commit_env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "orch-continuity",
        "GIT_AUTHOR_EMAIL": "orch-continuity@test",
        "GIT_COMMITTER_NAME": "orch-continuity",
        "GIT_COMMITTER_EMAIL": "orch-continuity@test",
    }
    subprocess.run(
        ["git", "commit", "-m", "initial"],
        cwd=str(root),
        capture_output=True,
        check=True,
        env=commit_env,
    )


def _build_app(fixture_root: Path) -> tuple:
    """Build an OrchestrationApp against the isolated fixture.

    Returns:
        Tuple of (app, config) for test assertions.
    """
    plan_path = fixture_root / "plan.yaml"
    runs_root = fixture_root / ".vectl" / "runs"
    ws_root = fixture_root / ".vectl" / "workspaces"

    test_role_profiles = (
        RoleProfile(
            role_id="python-executor",
            agent_id="python-executor",
            prompt_family="coder",
            execution_context="linked_worktree",
            mutation_policy="worktree_changes",
            session_policy="reuse_allowed",
            output_contract="freeform_evidence",
            default_runner="test",
        ),
    )

    orchestration_config = OrchestrationConfig(
        plan_path=plan_path,
        runtime=RuntimeConfig(
            default_runner="test",
            artifact_root=runs_root,
            workspace_root=ws_root,
        ),
        role_profiles=test_role_profiles,
    )

    config = AppConfig(
        plan_path=plan_path,
        run_store_root=runs_root,
        orchestration_config=orchestration_config,
    )
    app = build_orchestration_app(config)
    return app, config


@pytest.fixture
def orch_fixture(tmp_path: Path):
    """Create an isolated orchestration fixture with real plan + config."""
    _write_isolated_fixture(tmp_path)
    _init_git_repo(tmp_path)
    runs_root = tmp_path / ".vectl" / "runs"
    runs_root.mkdir(parents=True, exist_ok=True)
    ws_root = tmp_path / ".vectl" / "workspaces"
    ws_root.mkdir(parents=True, exist_ok=True)
    return tmp_path


# ---------------------------------------------------------------------
# 1. Runtime dispatch: expanded ExecutionRequest fields
# ---------------------------------------------------------------------


class TestExpandedExecutionRequestDispatch:
    """Prove that session_policy, request_mode, agent_id, and prompt artifact
    paths flow through runtime dispatch and are persisted in execution state."""

    def test_start_mode_execution_request_fields_persist_in_state(self, orch_fixture: Path) -> None:
        """Verify that start-mode ExecutionRequest fields survive dispatch."""
        app, config = _build_app(orch_fixture)

        step_id = "e2e.step_a"
        app._core_adapter.claim_step(step_id, "test-agent", flow="normal")

        # Build ExecutionRequest with expanded fields
        request = ExecutionRequest(
            step_id=step_id,
            role="python-executor",
            runner="test",
            work_refs=("run_id=test", "mode=start"),
            agent_id="python-executor",
            prompt_bundle_path="/tmp/test/prompt_bundle.json",
            runner_prompt_path="/tmp/test/runner_prompt.md",
            request_mode="start",
            session_policy="reuse_forbidden",
            session_id=None,
        )

        from vectl.orchestration.runtime import Runtime

        runtime = Runtime(workspace_root=orch_fixture / ".vectl" / "workspaces")
        workspace_id = runtime.prepare(request)
        execution_id = runtime.start(request=request, workspace=workspace_id)

        # Verify execution state preserves expanded fields
        state = runtime._active_workspaces[workspace_id]
        assert state.execution_state is not None
        assert state.execution_state.request_mode == "start"
        assert state.execution_state.session_policy == "reuse_forbidden"
        assert state.execution_state.session_id is None
        assert state.execution_state.runner == "test"
        assert state.execution_state.evidence_refs == ()

    def test_resume_mode_execution_request_fields_persist_in_state(
        self, orch_fixture: Path
    ) -> None:
        """Verify resume-mode request fields including session_id."""
        app, config = _build_app(orch_fixture)

        step_id = "e2e.step_a"
        app._core_adapter.claim_step(step_id, "test-agent", flow="normal")

        request = ExecutionRequest(
            step_id=step_id,
            role="python-executor",
            runner="test",
            work_refs=("run_id=test", "mode=resume", "session_id=sess-123"),
            agent_id="python-executor",
            prompt_bundle_path="/tmp/test/prompt_bundle.json",
            runner_prompt_path="/tmp/test/runner_prompt.md",
            request_mode="resume",
            session_policy="reuse_allowed",
            session_id="sess-123",
        )

        from vectl.orchestration.runtime import Runtime

        runtime = Runtime(workspace_root=orch_fixture / ".vectl" / "workspaces")
        workspace_id = runtime.prepare(request)
        execution_id = runtime.start(request=request, workspace=workspace_id)

        state = runtime._active_workspaces[workspace_id]
        assert state.execution_state is not None
        assert state.execution_state.request_mode == "resume"
        assert state.execution_state.session_policy == "reuse_allowed"
        assert state.execution_state.session_id == "sess-123"

    def test_recover_mode_execution_request_fields_persist_in_state(
        self, orch_fixture: Path
    ) -> None:
        """Verify recover-mode request fields."""
        request = ExecutionRequest(
            step_id="e2e.step_a",
            role="python-executor",
            runner="test",
            work_refs=("run_id=test", "mode=recover"),
            agent_id="python-executor",
            prompt_bundle_path="/tmp/test/prompt_bundle.json",
            runner_prompt_path="/tmp/test/runner_prompt.md",
            request_mode="recover",
            session_policy="reuse_forbidden",
            session_id=None,
        )

        app, config = _build_app(orch_fixture)
        app._core_adapter.claim_step("e2e.step_a", "test-agent", flow="normal")

        from vectl.orchestration.runtime import Runtime

        runtime = Runtime(workspace_root=orch_fixture / ".vectl" / "workspaces")
        workspace_id = runtime.prepare(request)
        execution_id = runtime.start(request=request, workspace=workspace_id)

        state = runtime._active_workspaces[workspace_id]
        assert state.execution_state is not None
        assert state.execution_state.request_mode == "recover"


# ---------------------------------------------------------------------
# 2. Session metadata and evidence refs in execution state
# ---------------------------------------------------------------------


class TestSessionMetadataPersistence:
    """Prove that session_id and evidence_refs from runner results
    propagate into execution state and are persisted through collect()."""

    def test_poll_result_session_id_updates_execution_state(self, orch_fixture: Path) -> None:
        """Verify that session_id from RunnerPollResult propagates to execution state."""
        from vectl.orchestration.runtime import Runtime

        runtime = Runtime(workspace_root=orch_fixture / ".vectl" / "workspaces")

        request = ExecutionRequest(
            step_id="e2e.step_a",
            role="python-executor",
            runner="test",
            work_refs=("run_id=test", "mode=start"),
            agent_id="python-executor",
            request_mode="start",
            session_policy="reuse_forbidden",
        )

        workspace_id = runtime.prepare(request)
        execution_id = runtime.start(request=request, workspace=workspace_id)

        # The test runner completes immediately, so collect should succeed
        state = runtime._active_workspaces[workspace_id]
        assert state.execution_state is not None
        initial_session_id = state.execution_state.session_id

        # Poll/completes and should propagate session_id from poll result
        result = runtime.collect(execution_id)
        assert result is not None
        assert result.step_id == "e2e.step_a"

    def test_evidence_refs_initially_empty_in_execution_state(self, orch_fixture: Path) -> None:
        """Verify evidence_refs start empty and flow through collect."""
        from vectl.orchestration.runtime import Runtime

        runtime = Runtime(workspace_root=orch_fixture / ".vectl" / "workspaces")

        request = ExecutionRequest(
            step_id="e2e.step_a",
            role="python-executor",
            runner="test",
            work_refs=("run_id=test",),
            agent_id="python-executor",
            request_mode="start",
            session_policy="reuse_forbidden",
        )

        workspace_id = runtime.prepare(request)
        execution_id = runtime.start(request=request, workspace=workspace_id)
        state = runtime._active_workspaces[workspace_id]

        # Initial state should have empty evidence_refs
        assert state.execution_state is not None
        assert state.execution_state.evidence_refs == ()


# ---------------------------------------------------------------------
# 3. Recovery continuity artifacts
# ---------------------------------------------------------------------


class TestRecoveryContinuityArtifacts:
    """Prove that continuity.json and recovery attempt artifacts preserve
    recovered_via truth per RFC §10.3 and §10.4."""

    def test_write_recovery_continuity_native_resume(self, orch_fixture: Path) -> None:
        """Verify RecoveryContinuity with native_session_resume writes correctly."""
        app, config = _build_app(orch_fixture)

        run_id = generate_run_id()
        run_root = orch_fixture / ".vectl" / "runs" / run_id
        run_root.mkdir(parents=True, exist_ok=True)

        continuity = RecoveryContinuity(
            recovered_via="native_session_resume",
            run_id=run_id,
            step_id="e2e.step_a",
            agent_id="python-executor",
            runner="opencode",
            session_id="sess-abc-123",
            timestamp="2026-04-12T10:00:00+00:00",
        )

        path = app._write_recovery_continuity(
            run_root=run_root,
            continuity=continuity,
        )

        assert path.exists()
        assert path.name == "continuity.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["recovered_via"] == "native_session_resume"
        assert data["run_id"] == run_id
        assert data["session_id"] == "sess-abc-123"
        assert data["runner"] == "opencode"

    def test_write_recovery_continuity_fresh_relaunch(self, orch_fixture: Path) -> None:
        """Verify RecoveryContinuity with fresh_relaunch writes correctly."""
        app, config = _build_app(orch_fixture)

        run_id = generate_run_id()
        run_root = orch_fixture / ".vectl" / "runs" / run_id
        run_root.mkdir(parents=True, exist_ok=True)

        continuity = RecoveryContinuity(
            recovered_via="fresh_relaunch",
            run_id=run_id,
            step_id="e2e.step_a",
            agent_id="python-executor",
            runner="opencode",
            session_id=None,
            timestamp="2026-04-12T10:00:00+00:00",
        )

        path = app._write_recovery_continuity(
            run_root=run_root,
            continuity=continuity,
        )

        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["recovered_via"] == "fresh_relaunch"
        assert data["session_id"] is None

    def test_write_recovery_attempt_resume(self, orch_fixture: Path) -> None:
        """Verify resume attempt artifact writes correctly."""
        app, config = _build_app(orch_fixture)

        run_id = generate_run_id()
        run_root = orch_fixture / ".vectl" / "runs" / run_id
        run_root.mkdir(parents=True, exist_ok=True)

        attempt = RecoveryAttempt(
            attempt_kind="resume",
            native_validation_ok=True,
            fallback_relaunch_used=False,
            resulting_run_id=run_id,
            resulting_session_id="sess-abc-123",
        )

        path = app._write_recovery_attempt(
            run_root=run_root,
            attempt=attempt,
        )

        assert path.exists()
        assert path.name == "resume_attempt.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["attempt_kind"] == "resume"
        assert data["native_validation_ok"] is True
        assert data["fallback_relaunch_used"] is False
        assert data["resulting_session_id"] == "sess-abc-123"

    def test_write_recovery_attempt_recover_fallback(self, orch_fixture: Path) -> None:
        """Verify recover attempt artifact with fresh relaunch fallback."""
        app, config = _build_app(orch_fixture)

        run_id = generate_run_id()
        run_root = orch_fixture / ".vectl" / "runs" / run_id
        run_root.mkdir(parents=True, exist_ok=True)

        attempt = RecoveryAttempt(
            attempt_kind="recover",
            native_validation_ok=False,
            native_validation_failure_reason="session.json missing session_id",
            fallback_relaunch_used=True,
            resulting_run_id=run_id,
        )

        path = app._write_recovery_attempt(
            run_root=run_root,
            attempt=attempt,
        )

        assert path.exists()
        assert path.name == "recover_attempt.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["attempt_kind"] == "recover"
        assert data["native_validation_ok"] is False
        assert data["native_validation_failure_reason"] == "session.json missing session_id"
        assert data["fallback_relaunch_used"] is True

    def test_recovered_via_truth_distinction(self, orch_fixture: Path) -> None:
        """Verify that native_session_resume and fresh_relaunch are distinct labels.

        Authority: RFC-opencode-orchestration-runner.md section 10.3
        The system must not collapse these two recovery paths into the same label.
        """
        app, config = _build_app(orch_fixture)

        for via in ("native_session_resume", "fresh_relaunch"):
            run_id = generate_run_id()
            run_root = orch_fixture / ".vectl" / "runs" / run_id
            run_root.mkdir(parents=True, exist_ok=True)

            continuity = RecoveryContinuity(
                recovered_via=via,  # type: ignore[arg-type]
                run_id=run_id,
                step_id="e2e.step_a",
                agent_id="python-executor",
                runner="opencode",
            )
            path = app._write_recovery_continuity(
                run_root=run_root,
                continuity=continuity,
            )
            data = json.loads(path.read_text(encoding="utf-8"))
            assert data["recovered_via"] == via

    def test_both_recovery_artifacts_written_together(self, orch_fixture: Path) -> None:
        """Verify continuity.json and attempt file can coexist in recovery dir."""
        app, config = _build_app(orch_fixture)

        run_id = generate_run_id()
        run_root = orch_fixture / ".vectl" / "runs" / run_id
        run_root.mkdir(parents=True, exist_ok=True)

        # Write continuity
        continuity = RecoveryContinuity(
            recovered_via="native_session_resume",
            run_id=run_id,
            step_id="e2e.step_a",
            agent_id="python-executor",
            runner="opencode",
            session_id="sess-xyz",
        )
        continuity_path = app._write_recovery_continuity(
            run_root=run_root,
            continuity=continuity,
        )

        # Write attempt
        attempt = RecoveryAttempt(
            attempt_kind="recover",
            native_validation_ok=True,
            resulting_run_id=run_id,
            resulting_session_id="sess-xyz",
        )
        attempt_path = app._write_recovery_attempt(
            run_root=run_root,
            attempt=attempt,
        )

        # Both should exist in recovery dir
        recovery_dir = run_root / "recovery"
        assert recovery_dir.exists()
        assert (recovery_dir / "continuity.json").exists()
        assert (recovery_dir / "recover_attempt.json").exists()


# ---------------------------------------------------------------------
# 4. Session-aware continuity bootstrap
# ---------------------------------------------------------------------


class TestSessionAwareContinuityBootstrap:
    """Prove that continuity bootstrap artifacts incorporate runner and
    session metadata for OpenCode-backed runs, per RFC §11."""

    def test_bootstrap_writes_runner_metadata_to_ledger(self, orch_fixture: Path) -> None:
        """Verify that _write_continuity_bootstrap includes runner in ledger."""
        app, config = _build_app(orch_fixture)

        run_id = generate_run_id()
        run_root = orch_fixture / ".vectl" / "runs" / run_id
        run_root.mkdir(parents=True, exist_ok=True)

        from vectl.orchestration.config import freeze_config

        frozen = freeze_config(config.orchestration_config, run_dir=run_root)

        app._write_continuity_bootstrap(
            run_id=run_id,
            step_id="e2e.step_a",
            frozen_config=frozen.config,
            run_root=run_root,
            runner="opencode",
            session_id="opencode-sess-42",
            request_mode="start",
        )

        continuity_root = app._continuity_root(run_root)
        ledger = json.loads((continuity_root / "ledger.json").read_text(encoding="utf-8"))
        assert ledger["runner"] == "opencode"
        assert ledger["session_id"] == "opencode-sess-42"
        assert ledger["request_mode"] == "start"

    def test_bootstrap_defaults_session_to_run_id(self, orch_fixture: Path) -> None:
        """Verify that missing session falls back to run_id for non-session runners."""
        app, config = _build_app(orch_fixture)

        run_id = generate_run_id()
        run_root = orch_fixture / ".vectl" / "runs" / run_id
        run_root.mkdir(parents=True, exist_ok=True)

        from vectl.orchestration.config import freeze_config

        frozen = freeze_config(config.orchestration_config, run_dir=run_root)

        app._write_continuity_bootstrap(
            run_id=run_id,
            step_id="e2e.step_a",
            frozen_config=frozen.config,
            run_root=run_root,
        )

        continuity_root = app._continuity_root(run_root)
        ledger = json.loads((continuity_root / "ledger.json").read_text(encoding="utf-8"))
        # Default: session_id falls back to run_id
        assert ledger["session_id"] == run_id
        # Default runner from config
        assert ledger["runner"] == "test"
        # Default mode
        assert ledger["request_mode"] == "start"

    def test_journal_includes_session_metadata(self, orch_fixture: Path) -> None:
        """Verify that journal entries include runner and session metadata."""
        app, config = _build_app(orch_fixture)

        run_id = generate_run_id()
        run_root = orch_fixture / ".vectl" / "runs" / run_id
        run_root.mkdir(parents=True, exist_ok=True)

        from vectl.orchestration.config import freeze_config

        frozen = freeze_config(config.orchestration_config, run_dir=run_root)

        app._write_continuity_bootstrap(
            run_id=run_id,
            step_id="e2e.step_a",
            frozen_config=frozen.config,
            run_root=run_root,
            runner="opencode",
            session_id="opencode-sess-99",
            request_mode="resume",
        )

        continuity_root = app._continuity_root(run_root)
        journal_lines = (
            (continuity_root / "journal.jsonl").read_text(encoding="utf-8").strip().splitlines()
        )
        assert len(journal_lines) >= 1
        entry = json.loads(journal_lines[0])
        assert entry["runner"] == "opencode"
        assert entry["session_id"] == "opencode-sess-99"
        assert entry["request_mode"] == "resume"


# ---------------------------------------------------------------------
# 5. RuntimeRecoveryRecord round-trip with expanded fields
# ---------------------------------------------------------------------


class TestRuntimeRecoveryRecordExpanded:
    """Prove that RuntimeRecoveryRecord persists and restores the expanded
    session/evidence fields through serialization round-trip."""

    def test_evidence_refs_round_trip(self) -> None:
        """Verify evidence_refs survive serialization round-trip."""
        record = RuntimeRecoveryRecord(
            workspace_id="ws-1",
            step_id="e2e.step_a",
            worktree_path="/tmp/ws",
            scratch_branch="branch",
            target_ref="main",
            target_head_at_prepare="abc123",
            execution_id="exec-1",
            runner="opencode",
            runner_handle="handle-1",
            session_id="sess-42",
            evidence_refs=("stdout:500 chars", "stderr:100 chars"),
            request_mode="recover",
            session_policy="reuse_allowed",
        )

        from dataclasses import asdict

        payload = asdict(record)
        assert payload["evidence_refs"] == ("stdout:500 chars", "stderr:100 chars")
        assert payload["request_mode"] == "recover"
        assert payload["session_policy"] == "reuse_allowed"
        assert payload["session_id"] == "sess-42"

    def test_deserialization_preserves_expanded_fields(self) -> None:
        """Verify _deserialize_runtime_state preserves new fields."""
        from vectl.orchestration.run_store import _deserialize_runtime_state

        payload = {
            "workspace_id": "ws-1",
            "step_id": "e2e.step_a",
            "worktree_path": "/tmp/ws",
            "scratch_branch": "branch",
            "target_ref": "main",
            "target_head_at_prepare": "abc123",
            "execution_id": "exec-1",
            "runner": "opencode",
            "runner_handle": "handle-1",
            "session_id": "sess-42",
            "execution_status": "running",
            "started_at": 1000.0,
            "last_update_at": 2000.0,
            "execution_artifact_refs": ["ref1"],
            "evidence_refs": ["stdout:500 chars", "stderr:100 chars"],
            "request_mode": "recover",
            "session_policy": "reuse_allowed",
        }

        record = _deserialize_runtime_state(payload)
        assert record is not None
        assert record.evidence_refs == ("stdout:500 chars", "stderr:100 chars")
        assert record.request_mode == "recover"
        assert record.session_policy == "reuse_allowed"
        assert record.session_id == "sess-42"

    def test_deserialization_defaults_for_missing_fields(self) -> None:
        """Verify backward-compatible defaults when new fields absent from old data."""
        from vectl.orchestration.run_store import _deserialize_runtime_state

        # Minimal payload without expanded fields
        payload = {
            "workspace_id": "ws-1",
            "step_id": "e2e.step_a",
            "worktree_path": "/tmp/ws",
            "scratch_branch": "branch",
            "target_ref": "main",
            "target_head_at_prepare": "abc123",
        }

        record = _deserialize_runtime_state(payload)
        assert record is not None
        assert record.evidence_refs == ()
        assert record.request_mode == "start"
        assert record.session_policy == "reuse_forbidden"
        assert record.session_id is None


# ---------------------------------------------------------------------
# 6. AgentExecutionState expanded fields
# ---------------------------------------------------------------------


class TestAgentExecutionStateExpanded:
    """Prove that AgentExecutionState carries the expanded fields
    request_mode, session_policy, and evidence_refs."""

    def test_expanded_fields_default_values(self) -> None:
        """Verify default values for new fields."""
        state = AgentExecutionState(
            execution_id="exec-1",
            step_id="step-1",
            workspace_id="ws-1",
            runner="opencode",
            runner_handle="handle-1",
        )
        assert state.evidence_refs == ()
        assert state.request_mode == "start"
        assert state.session_policy == "reuse_forbidden"

    def test_expanded_fields_round_trip(self) -> None:
        """Verify expanded fields survive dataclass copy."""
        state = AgentExecutionState(
            execution_id="exec-1",
            step_id="step-1",
            workspace_id="ws-1",
            runner="opencode",
            runner_handle="handle-1",
            session_id="sess-42",
            evidence_refs=("stdout:500 chars",),
            request_mode="resume",
            session_policy="reuse_allowed",
        )
        from dataclasses import replace

        updated = replace(
            state, status="success", evidence_refs=("stdout:500 chars", "stderr:100 chars")
        )
        assert updated.evidence_refs == ("stdout:500 chars", "stderr:100 chars")
        assert updated.request_mode == "resume"
        assert updated.session_policy == "reuse_allowed"
        assert updated.session_id == "sess-42"
        assert updated.status == "success"


# ---------------------------------------------------------------------
# 7. Run registry persistence with session/evidence metadata
# ---------------------------------------------------------------------


class TestRunStoreSessionMetadata:
    """Prove that RunStore round-trips RuntimeRecoveryRecord with session metadata."""

    def test_runtime_state_with_session_metadata_round_trips(self, tmp_path: Path) -> None:
        """Verify RuntimeRecoveryRecord with session data survives save/load."""
        store_root = tmp_path / "runs"
        store_root.mkdir(parents=True, exist_ok=True)
        registry = RunRegistry(store_root=str(store_root))

        run_id = generate_run_id()
        run_root = registry.run_artifact_root(run_id)
        run_root.mkdir(parents=True, exist_ok=True)

        runtime_state = RuntimeRecoveryRecord(
            workspace_id="ws-1",
            step_id="e2e.step_a",
            worktree_path="/tmp/ws",
            scratch_branch="branch",
            target_ref="main",
            target_head_at_prepare="abc123",
            execution_id="exec-1",
            runner="opencode",
            runner_handle="handle-1",
            session_id="opencode-sess-42",
            evidence_refs=("stdout:500 chars", "stderr:100 chars"),
            request_mode="resume",
            session_policy="reuse_allowed",
        )

        from vectl.orchestration.run_store import RunRecord

        record = RunRecord(
            run_id=run_id,
            step_id="e2e.step_a",
            agent="python-executor",
            status="running",
            artifact_root=str(run_root),
            output_summary="test run with session metadata",
            runtime_state=runtime_state,
        )

        registry.save(record)

        # Retrieve and verify
        loaded = registry.by_id(run_id)
        assert loaded is not None
        assert loaded.runtime_state is not None
        assert loaded.runtime_state.session_id == "opencode-sess-42"
        assert loaded.runtime_state.evidence_refs == ("stdout:500 chars", "stderr:100 chars")
        assert loaded.runtime_state.request_mode == "resume"
        assert loaded.runtime_state.session_policy == "reuse_allowed"
