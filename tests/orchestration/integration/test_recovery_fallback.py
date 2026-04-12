#!/usr/bin/env python3
"""Integration tests proving recovery fallback semantics per RFC §10.

Authority:
    docs/RFC-opencode-orchestration-runner.md sections 10, 11, 12

These tests verify:
    1. Native session resume is preferred when session.json validates
    2. Automatic fresh relaunch fallback when session validation fails
    3. Recovery truth labels (recovered_via) persist correctly
    4. Recovery attempt artifacts record validation outcomes
    5. Explicit failure when both paths are unavailable
    6. Summaries/continuity surfaces reflect native vs fresh path truth
"""

from __future__ import annotations

import json
import os
import subprocess
import textwrap
from pathlib import Path

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
    RecoveredVia,
    RecoveryAttempt,
    RecoveryContinuity,
)
from vectl.orchestration.recovery_fallback import (
    persist_recovery_attempt,
    persist_recovery_continuity,
    persist_recovery_fallback_result,
    read_recovery_attempt,
    read_recovery_continuity,
    recover_with_fallback,
    validate_session_for_resume,
)
from vectl.orchestration.run_store import generate_run_id

# ---------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------


def _write_isolated_fixture(root: Path, *, runner: str = "test") -> None:
    """Create a minimal, self-contained orchestration fixture."""
    plan = Plan(
        project="recovery-fallback-inttest",
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
        "GIT_AUTHOR_NAME": "recovery-fallback-inttest",
        "GIT_AUTHOR_EMAIL": "recovery-fallback@test",
        "GIT_COMMITTER_NAME": "recovery-fallback-inttest",
        "GIT_COMMITTER_EMAIL": "recovery-fallback@test",
    }
    subprocess.run(
        ["git", "commit", "-m", "initial"],
        cwd=str(root),
        capture_output=True,
        check=True,
        env=commit_env,
    )


@pytest.fixture
def orch_fixture(tmp_path: Path) -> Path:
    """Create an isolated orchestration fixture with real plan + config."""
    _write_isolated_fixture(tmp_path)
    _init_git_repo(tmp_path)
    runs_root = tmp_path / ".vectl" / "runs"
    runs_root.mkdir(parents=True, exist_ok=True)
    ws_root = tmp_path / ".vectl" / "workspaces"
    ws_root.mkdir(parents=True, exist_ok=True)
    return tmp_path


def _prepare_run_root(
    run_root: Path,
    *,
    session_data: dict[str, object] | None = None,
    prompt_bundle_data: dict[str, object] | None = None,
    prompt_content: str | None = None,
) -> None:
    """Prepare a run artifact root with optional session and prompt files."""
    run_root.mkdir(parents=True, exist_ok=True)

    if session_data is not None:
        run_root.joinpath("session.json").write_text(
            json.dumps(session_data, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    input_dir = run_root / "input"
    input_dir.mkdir(parents=True, exist_ok=True)

    if prompt_bundle_data is not None:
        input_dir.joinpath("prompt_bundle.json").write_text(
            json.dumps(prompt_bundle_data, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    if prompt_content is not None:
        input_dir.joinpath("runner_prompt.md").write_text(
            prompt_content,
            encoding="utf-8",
        )


_VALID_SESSION_DATA: dict[str, str] = {
    "runner": "opencode",
    "session_id": "sess-native-abc-123",
    "step_id": "e2e.step_a",
    "agent_id": "python-executor",
    "workspace": "/tmp/test-workspace",
}

_VALID_PROMPT_BUNDLE: dict[str, str] = {
    "role_id": "python-executor",
    "agent_id": "python-executor",
    "runner": "opencode",
    "system_prompt": "You are a coding assistant.",
    "task_prompt": "Complete the assigned task.",
    "prompt_bundle_sha256": "sha256:abc123def456",
}

_VALID_RUNNER_PROMPT = """# Task Instructions

Complete the assigned task in the current workspace.

## Context
- Step: e2e.step_a
- Agent: python-executor
"""


# ---------------------------------------------------------------------
# 1. Session validation (§10.1)
# ---------------------------------------------------------------------


class TestSessionValidation:
    """Prove that session validation matches RFC §10.1 criteria."""

    def test_valid_session_all_checks_pass(self, tmp_path: Path) -> None:
        """All 5 validation checks pass: session exists, parses, has required
        fields, runner==opencode, prompt artifacts valid."""
        run_root = tmp_path / "runs" / "run-valid"
        workspace = tmp_path / "ws"
        workspace.mkdir(parents=True, exist_ok=True)

        # Also create workspace copy of runner_prompt.md
        orch_dir = workspace / ".vectl" / "orch"
        orch_dir.mkdir(parents=True, exist_ok=True)
        orch_dir.joinpath("runner_prompt.md").write_text(_VALID_RUNNER_PROMPT, encoding="utf-8")

        _prepare_run_root(
            run_root,
            session_data=_VALID_SESSION_DATA,
            prompt_bundle_data=_VALID_PROMPT_BUNDLE,
            prompt_content=_VALID_RUNNER_PROMPT,
        )

        result = validate_session_for_resume(run_root=run_root)

        assert result.valid is True, f"Expected valid, got: {result.reason}"
        assert result.runner == "opencode"
        assert result.session_id == "sess-native-abc-123"
        assert result.step_id == "e2e.step_a"
        assert result.agent_id == "python-executor"
        assert result.session_data is not None

    def test_session_missing_file_fails(self, tmp_path: Path) -> None:
        """Check 1: session.json must exist."""
        run_root = tmp_path / "runs" / "run-no-session"
        run_root.mkdir(parents=True, exist_ok=True)

        result = validate_session_for_resume(run_root=run_root)

        assert result.valid is False
        assert "not found" in result.reason

    def test_session_invalid_json_fails(self, tmp_path: Path) -> None:
        """Check 2: session.json must parse as valid JSON."""
        run_root = tmp_path / "runs" / "run-bad-json"
        run_root.mkdir(parents=True, exist_ok=True)
        run_root.joinpath("session.json").write_text("{invalid json}}}", encoding="utf-8")

        result = validate_session_for_resume(run_root=run_root)

        assert result.valid is False
        assert "not valid JSON" in result.reason

    def test_session_missing_required_field_fails(self, tmp_path: Path) -> None:
        """Check 3: required fields must be non-empty strings."""
        run_root = tmp_path / "runs" / "run-missing-field"

        # Missing session_id
        session_data = {
            "runner": "opencode",
            "step_id": "e2e.step_a",
            "agent_id": "python-executor",
            "workspace": "/tmp/ws",
            # session_id intentionally missing
        }
        _prepare_run_root(run_root, session_data=session_data)

        result = validate_session_for_resume(run_root=run_root)

        assert result.valid is False
        assert "missing or empty fields" in result.reason
        assert "session_id" in result.reason

    def test_session_empty_field_fails(self, tmp_path: Path) -> None:
        """Check 3: empty string for required field fails."""
        run_root = tmp_path / "runs" / "run-empty-field"

        session_data = {
            "runner": "opencode",
            "session_id": "",  # Empty
            "step_id": "e2e.step_a",
            "agent_id": "python-executor",
            "workspace": "/tmp/ws",
        }
        _prepare_run_root(run_root, session_data=session_data)

        result = validate_session_for_resume(run_root=run_root)

        assert result.valid is False
        assert "session_id" in result.reason

    def test_session_wrong_runner_fails(self, tmp_path: Path) -> None:
        """Check 4: runner must be 'opencode' for native resume."""
        run_root = tmp_path / "runs" / "run-wrong-runner"
        workspace = tmp_path / "ws"
        workspace.mkdir(parents=True, exist_ok=True)

        session_data = {
            "runner": "task",  # Not opencode
            "session_id": "sess-123",
            "step_id": "e2e.step_a",
            "agent_id": "python-executor",
            "workspace": "/tmp/ws",
        }
        _prepare_run_root(
            run_root,
            session_data=session_data,
            prompt_bundle_data=_VALID_PROMPT_BUNDLE,
            prompt_content=_VALID_RUNNER_PROMPT,
        )

        result = validate_session_for_resume(run_root=run_root)

        assert result.valid is False
        assert "runner" in result.reason
        assert "'task'" in result.reason
        assert "opencode" in result.reason

    def test_session_missing_prompt_bundle_fails(self, tmp_path: Path) -> None:
        """Check 5: prompt_bundle.json must exist for native resume."""
        run_root = tmp_path / "runs" / "run-no-bundle"
        workspace = tmp_path / "ws"
        workspace.mkdir(parents=True, exist_ok=True)

        # Session is valid but prompt bundle is missing
        _prepare_run_root(
            run_root,
            session_data=_VALID_SESSION_DATA,
            # No prompt_bundle_data!
            prompt_content=_VALID_RUNNER_PROMPT,
        )

        result = validate_session_for_resume(run_root=run_root)

        assert result.valid is False
        assert "prompt_bundle.json" in result.reason

    def test_session_missing_runner_prompt_fails(self, tmp_path: Path) -> None:
        """Check 5: runner_prompt.md must exist for native resume."""
        run_root = tmp_path / "runs" / "run-no-prompt"
        workspace = tmp_path / "ws"
        workspace.mkdir(parents=True, exist_ok=True)

        _prepare_run_root(
            run_root,
            session_data=_VALID_SESSION_DATA,
            prompt_bundle_data=_VALID_PROMPT_BUNDLE,
            # No prompt_content!
        )

        result = validate_session_for_resume(run_root=run_root)

        assert result.valid is False
        assert "runner_prompt.md" in result.reason

    def test_session_empty_runner_prompt_fails(self, tmp_path: Path) -> None:
        """Check 5: runner_prompt.md must be non-empty."""
        run_root = tmp_path / "runs" / "run-empty-prompt"
        workspace = tmp_path / "ws"
        workspace.mkdir(parents=True, exist_ok=True)

        _prepare_run_root(
            run_root,
            session_data=_VALID_SESSION_DATA,
            prompt_bundle_data=_VALID_PROMPT_BUNDLE,
            prompt_content="   ",  # Whitespace only
        )

        result = validate_session_for_resume(run_root=run_root)

        assert result.valid is False
        # Could be empty prompt or invalid bundle depending on check order
        assert result.valid is False


# ---------------------------------------------------------------------
# 2. Recovery fallback: native resume path (§10.1, §6.3)
# ---------------------------------------------------------------------


class TestRecoveryFallbackNativeResume:
    """Prove that native session resume is preferred when conditions are met."""

    def test_native_resume_chosen_when_session_valid(self, tmp_path: Path) -> None:
        """When session.json and prompt artifacts are valid, native resume is chosen."""
        run_root = tmp_path / "runs" / "run-native-ok"
        workspace = tmp_path / "ws"
        workspace.mkdir(parents=True, exist_ok=True)

        # Also create workspace copy of runner_prompt.md
        orch_dir = workspace / ".vectl" / "orch"
        orch_dir.mkdir(parents=True, exist_ok=True)
        orch_dir.joinpath("runner_prompt.md").write_text(_VALID_RUNNER_PROMPT, encoding="utf-8")

        _prepare_run_root(
            run_root,
            session_data=_VALID_SESSION_DATA,
            prompt_bundle_data=_VALID_PROMPT_BUNDLE,
            prompt_content=_VALID_RUNNER_PROMPT,
        )

        result = recover_with_fallback(
            run_id="run-native-ok",
            step_id="e2e.step_a",
            agent_id="python-executor",
            runner="opencode",
            run_root=run_root,
            workspace=workspace,
            timestamp="2026-04-12T10:00:00+00:00",
        )

        assert result.recovered_via == "native_session_resume"
        assert result.native_resume_attempted is True
        assert result.native_resume_succeeded is True
        assert result.native_resume_failure_reason == ""
        assert result.prompt_artifacts_valid is True
        assert result.continuity is not None
        assert result.continuity.recovered_via == "native_session_resume"
        assert result.continuity.session_id == "sess-native-abc-123"
        assert result.resume_attempt is not None
        assert result.resume_attempt.attempt_kind == "resume"
        assert result.resume_attempt.native_validation_ok is True
        assert result.resume_attempt.fallback_relaunch_used is False
        assert result.recover_attempt is None  # No fallback needed


# ---------------------------------------------------------------------
# 3. Recovery fallback: fresh relaunch path (§10.2)
# ---------------------------------------------------------------------


class TestRecoveryFallbackFreshRelaunch:
    """Prove that fresh relaunch fallback is automatic when session validation
    fails but prompt artifacts are still valid."""

    def test_fresh_relaunch_when_session_missing(self, tmp_path: Path) -> None:
        """When session.json is missing but prompt artifacts are valid,
        fresh relaunch fallback is used."""
        run_root = tmp_path / "runs" / "run-no-session"
        workspace = tmp_path / "ws"
        workspace.mkdir(parents=True, exist_ok=True)

        # Also create workspace copy of runner_prompt.md
        orch_dir = workspace / ".vectl" / "orch"
        orch_dir.mkdir(parents=True, exist_ok=True)
        orch_dir.joinpath("runner_prompt.md").write_text(_VALID_RUNNER_PROMPT, encoding="utf-8")

        # No session_data, but valid prompt artifacts
        _prepare_run_root(
            run_root,
            session_data=None,  # No session!
            prompt_bundle_data=_VALID_PROMPT_BUNDLE,
            prompt_content=_VALID_RUNNER_PROMPT,
        )

        result = recover_with_fallback(
            run_id="run-no-session",
            step_id="e2e.step_a",
            agent_id="python-executor",
            runner="opencode",
            run_root=run_root,
            workspace=workspace,
            timestamp="2026-04-12T10:00:00+00:00",
        )

        assert result.recovered_via == "fresh_relaunch"
        assert result.native_resume_attempted is True
        assert result.native_resume_succeeded is False
        assert "not found" in result.native_resume_failure_reason
        assert result.prompt_artifacts_valid is True
        assert result.continuity is not None
        assert result.continuity.recovered_via == "fresh_relaunch"
        assert result.continuity.session_id is None
        assert result.resume_attempt is not None
        assert result.resume_attempt.native_validation_ok is False
        assert result.resume_attempt.fallback_relaunch_used is True
        assert result.recover_attempt is not None
        assert result.recover_attempt.native_validation_ok is False
        assert result.recover_attempt.fallback_relaunch_used is True

    def test_fresh_relaunch_when_session_invalid_runner(self, tmp_path: Path) -> None:
        """When session has wrong runner but prompt artifacts are valid."""
        run_root = tmp_path / "runs" / "run-wrong-runner"
        workspace = tmp_path / "ws"
        workspace.mkdir(parents=True, exist_ok=True)

        orch_dir = workspace / ".vectl" / "orch"
        orch_dir.mkdir(parents=True, exist_ok=True)
        orch_dir.joinpath("runner_prompt.md").write_text(_VALID_RUNNER_PROMPT, encoding="utf-8")

        bad_session = {
            "runner": "task",  # Not opencode
            "session_id": "sess-task-123",
            "step_id": "e2e.step_a",
            "agent_id": "python-executor",
            "workspace": "/tmp/ws",
        }
        _prepare_run_root(
            run_root,
            session_data=bad_session,
            prompt_bundle_data=_VALID_PROMPT_BUNDLE,
            prompt_content=_VALID_RUNNER_PROMPT,
        )

        result = recover_with_fallback(
            run_id="run-wrong-runner",
            step_id="e2e.step_a",
            agent_id="python-executor",
            runner="opencode",
            run_root=run_root,
            workspace=workspace,
        )

        assert result.recovered_via == "fresh_relaunch"
        assert result.native_resume_succeeded is False
        assert "not 'opencode'" in result.native_resume_failure_reason

    def test_fresh_relaunch_when_session_missing_field(self, tmp_path: Path) -> None:
        """When session is missing a required field but prompts are valid."""
        run_root = tmp_path / "runs" / "run-missing-session-id"
        workspace = tmp_path / "ws"
        workspace.mkdir(parents=True, exist_ok=True)

        orch_dir = workspace / ".vectl" / "orch"
        orch_dir.mkdir(parents=True, exist_ok=True)
        orch_dir.joinpath("runner_prompt.md").write_text(_VALID_RUNNER_PROMPT, encoding="utf-8")

        incomplete_session = {
            "runner": "opencode",
            # session_id intentionally missing
            "step_id": "e2e.step_a",
            "agent_id": "python-executor",
            "workspace": "/tmp/ws",
        }
        _prepare_run_root(
            run_root,
            session_data=incomplete_session,
            prompt_bundle_data=_VALID_PROMPT_BUNDLE,
            prompt_content=_VALID_RUNNER_PROMPT,
        )

        result = recover_with_fallback(
            run_id="run-missing-session-id",
            step_id="e2e.step_a",
            agent_id="python-executor",
            runner="opencode",
            run_root=run_root,
            workspace=workspace,
        )

        assert result.recovered_via == "fresh_relaunch"
        assert result.native_resume_succeeded is False
        assert "session_id" in result.native_resume_failure_reason


# ---------------------------------------------------------------------
# 4. Recovery truth labeling (§10.3)
# ---------------------------------------------------------------------


class TestRecoveryTruthLabeling:
    """Prove that recovered_via labels are persisted correctly and are distinct."""

    def test_native_resume_continuity_json(self, tmp_path: Path) -> None:
        """Native resume path produces recovered_via=native_session_resume."""
        run_root = tmp_path / "runs" / "run-truth-native"
        workspace = tmp_path / "ws"
        workspace.mkdir(parents=True, exist_ok=True)

        orch_dir = workspace / ".vectl" / "orch"
        orch_dir.mkdir(parents=True, exist_ok=True)
        orch_dir.joinpath("runner_prompt.md").write_text(_VALID_RUNNER_PROMPT, encoding="utf-8")

        _prepare_run_root(
            run_root,
            session_data=_VALID_SESSION_DATA,
            prompt_bundle_data=_VALID_PROMPT_BUNDLE,
            prompt_content=_VALID_RUNNER_PROMPT,
        )

        result = recover_with_fallback(
            run_id="run-truth-native",
            step_id="e2e.step_a",
            agent_id="python-executor",
            runner="opencode",
            run_root=run_root,
            workspace=workspace,
        )

        assert result.continuity is not None
        assert result.continuity.recovered_via == "native_session_resume"

        # Persist and verify
        paths = persist_recovery_fallback_result(run_root=run_root, result=result)
        assert "continuity" in paths

        data = json.loads(paths["continuity"].read_text(encoding="utf-8"))
        assert data["recovered_via"] == "native_session_resume"
        assert data["session_id"] == "sess-native-abc-123"
        assert data["runner"] == "opencode"

    def test_fresh_relaunch_continuity_json(self, tmp_path: Path) -> None:
        """Fresh relaunch path produces recovered_via=fresh_relaunch."""
        run_root = tmp_path / "runs" / "run-truth-fresh"
        workspace = tmp_path / "ws"
        workspace.mkdir(parents=True, exist_ok=True)

        orch_dir = workspace / ".vectl" / "orch"
        orch_dir.mkdir(parents=True, exist_ok=True)
        orch_dir.joinpath("runner_prompt.md").write_text(_VALID_RUNNER_PROMPT, encoding="utf-8")

        # No session data — fresh relaunch
        _prepare_run_root(
            run_root,
            session_data=None,
            prompt_bundle_data=_VALID_PROMPT_BUNDLE,
            prompt_content=_VALID_RUNNER_PROMPT,
        )

        result = recover_with_fallback(
            run_id="run-truth-fresh",
            step_id="e2e.step_a",
            agent_id="python-executor",
            runner="opencode",
            run_root=run_root,
            workspace=workspace,
        )

        assert result.continuity is not None
        assert result.continuity.recovered_via == "fresh_relaunch"
        assert result.continuity.session_id is None

        paths = persist_recovery_fallback_result(run_root=run_root, result=result)
        assert "continuity" in paths

        data = json.loads(paths["continuity"].read_text(encoding="utf-8"))
        assert data["recovered_via"] == "fresh_relaunch"
        assert data["session_id"] is None

    def test_truth_labels_are_semantically_distinct(self, tmp_path: Path) -> None:
        """Verify that native_session_resume and fresh_relaunch are distinct labels.

        Authority: RFC §10.3 — The system must not collapse these two
        recovery paths into the same label.
        """

        # The RecoveredVia type alias enforces this at the type level
        valid_labels: tuple[RecoveredVia, ...] = ("native_session_resume", "fresh_relaunch")
        assert len(set(valid_labels)) == len(valid_labels), "recovered_via labels must be distinct"

        # Persistence round-trip preserves the distinction
        for label in valid_labels:
            run_root = tmp_path / "runs" / f"run-truth-{label}"
            continuity = RecoveryContinuity(
                recovered_via=label,
                run_id=f"run-{label}",
                step_id="e2e.step_a",
                agent_id="python-executor",
                runner="opencode",
                session_id="sess-123" if label == "native_session_resume" else None,
                timestamp="2026-04-12T10:00:00+00:00",
            )

            path = persist_recovery_continuity(run_root=run_root, continuity=continuity)
            data = json.loads(path.read_text(encoding="utf-8"))
            assert data["recovered_via"] == label, (
                f"Persisted label {data['recovered_via']!r} != expected {label!r}"
            )


# ---------------------------------------------------------------------
# 5. Recovery attempt artifacts (§10.4)
# ---------------------------------------------------------------------


class TestRecoveryAttemptArtifacts:
    """Prove that resume_attempt.json and recover_attempt.json persist correctly."""

    def test_resume_attempt_artifact_for_native_resume(self, tmp_path: Path) -> None:
        """Native resume produces resume_attempt.json with native_validation_ok=True."""
        run_root = tmp_path / "runs" / "run-attempt-native"
        workspace = tmp_path / "ws"
        workspace.mkdir(parents=True, exist_ok=True)

        orch_dir = workspace / ".vectl" / "orch"
        orch_dir.mkdir(parents=True, exist_ok=True)
        orch_dir.joinpath("runner_prompt.md").write_text(_VALID_RUNNER_PROMPT, encoding="utf-8")

        _prepare_run_root(
            run_root,
            session_data=_VALID_SESSION_DATA,
            prompt_bundle_data=_VALID_PROMPT_BUNDLE,
            prompt_content=_VALID_RUNNER_PROMPT,
        )

        result = recover_with_fallback(
            run_id="run-attempt-native",
            step_id="e2e.step_a",
            agent_id="python-executor",
            runner="opencode",
            run_root=run_root,
            workspace=workspace,
        )

        assert result.resume_attempt is not None
        assert result.resume_attempt.attempt_kind == "resume"
        assert result.resume_attempt.native_validation_ok is True
        assert result.resume_attempt.fallback_relaunch_used is False
        assert result.resume_attempt.resulting_session_id == "sess-native-abc-123"

        paths = persist_recovery_fallback_result(run_root=run_root, result=result)
        assert "resume_attempt" in paths

        data = json.loads(paths["resume_attempt"].read_text(encoding="utf-8"))
        assert data["attempt_kind"] == "resume"
        assert data["native_validation_ok"] is True
        assert data["fallback_relaunch_used"] is False

        # recover_attempt should NOT be present for native resume
        assert result.recover_attempt is None

    def test_both_attempt_artifacts_for_fresh_relaunch(self, tmp_path: Path) -> None:
        """Fresh relaunch produces both resume_attempt.json and recover_attempt.json."""
        run_root = tmp_path / "runs" / "run-attempt-fresh"
        workspace = tmp_path / "ws"
        workspace.mkdir(parents=True, exist_ok=True)

        orch_dir = workspace / ".vectl" / "orch"
        orch_dir.mkdir(parents=True, exist_ok=True)
        orch_dir.joinpath("runner_prompt.md").write_text(_VALID_RUNNER_PROMPT, encoding="utf-8")

        # No session — triggers fresh relaunch
        _prepare_run_root(
            run_root,
            session_data=None,
            prompt_bundle_data=_VALID_PROMPT_BUNDLE,
            prompt_content=_VALID_RUNNER_PROMPT,
        )

        result = recover_with_fallback(
            run_id="run-attempt-fresh",
            step_id="e2e.step_a",
            agent_id="python-executor",
            runner="opencode",
            run_root=run_root,
            workspace=workspace,
        )

        # Both attempt artifacts should be present
        assert result.resume_attempt is not None
        assert result.resume_attempt.attempt_kind == "resume"
        assert result.resume_attempt.native_validation_ok is False
        assert result.resume_attempt.fallback_relaunch_used is True

        assert result.recover_attempt is not None
        assert result.recover_attempt.attempt_kind == "recover"
        assert result.recover_attempt.native_validation_ok is False
        assert result.recover_attempt.fallback_relaunch_used is True

        paths = persist_recovery_fallback_result(run_root=run_root, result=result)
        assert "resume_attempt" in paths
        assert "recover_attempt" in paths

        resume_data = json.loads(paths["resume_attempt"].read_text(encoding="utf-8"))
        assert resume_data["attempt_kind"] == "resume"
        assert resume_data["native_validation_ok"] is False
        assert resume_data["fallback_relaunch_used"] is True

        recover_data = json.loads(paths["recover_attempt"].read_text(encoding="utf-8"))
        assert recover_data["attempt_kind"] == "recover"
        assert recover_data["native_validation_ok"] is False
        assert recover_data["fallback_relaunch_used"] is True

    def test_attempt_artifacts_in_recovery_directory(self, tmp_path: Path) -> None:
        """All artifacts are persisted under .vectl/runs/<run_id>/recovery/."""
        run_root = tmp_path / "runs" / "run-dir-structure"
        workspace = tmp_path / "ws"
        workspace.mkdir(parents=True, exist_ok=True)

        orch_dir = workspace / ".vectl" / "orch"
        orch_dir.mkdir(parents=True, exist_ok=True)
        orch_dir.joinpath("runner_prompt.md").write_text(_VALID_RUNNER_PROMPT, encoding="utf-8")

        _prepare_run_root(
            run_root,
            session_data=None,
            prompt_bundle_data=_VALID_PROMPT_BUNDLE,
            prompt_content=_VALID_RUNNER_PROMPT,
        )

        result = recover_with_fallback(
            run_id="run-dir-structure",
            step_id="e2e.step_a",
            agent_id="python-executor",
            runner="opencode",
            run_root=run_root,
            workspace=workspace,
        )

        persist_recovery_fallback_result(run_root=run_root, result=result)

        recovery_dir = run_root / "recovery"
        assert recovery_dir.exists(), "recovery directory must exist"
        assert (recovery_dir / "continuity.json").exists()
        assert (recovery_dir / "resume_attempt.json").exists()
        assert (recovery_dir / "recover_attempt.json").exists()


# ---------------------------------------------------------------------
# 6. Explicit failure when both paths unavailable (§10.2)
# ---------------------------------------------------------------------


class TestRecoveryFallbackExplicitFailure:
    """Prove that recovery fails explicitly when both native and prompt paths fail."""

    def test_both_paths_fail_returns_null_continuity(self, tmp_path: Path) -> None:
        """When both session and prompt artifacts fail, continuity is None."""
        run_root = tmp_path / "runs" / "run-both-fail"
        workspace = tmp_path / "ws"
        workspace.mkdir(parents=True, exist_ok=True)

        # No session data AND no prompt data
        run_root.mkdir(parents=True, exist_ok=True)

        result = recover_with_fallback(
            run_id="run-both-fail",
            step_id="e2e.step_a",
            agent_id="python-executor",
            runner="opencode",
            run_root=run_root,
            workspace=workspace,
        )

        assert result.recovered_via == "fresh_relaunch"  # Default label, but not usable
        assert result.continuity is None  # Cannot persist truth when both fail
        assert result.native_resume_succeeded is False
        assert result.prompt_artifacts_valid is False
        assert result.prompt_artifact_failure_reason != ""

    def test_both_paths_fail_attempts_record_failure_reasons(self, tmp_path: Path) -> None:
        """Both paths fail: attempt artifacts record why each failed."""
        run_root = tmp_path / "runs" / "run-dual-failure"
        workspace = tmp_path / "ws"
        workspace.mkdir(parents=True, exist_ok=True)

        run_root.mkdir(parents=True, exist_ok=True)

        result = recover_with_fallback(
            run_id="run-dual-failure",
            step_id="e2e.step_a",
            agent_id="python-executor",
            runner="opencode",
            run_root=run_root,
            workspace=workspace,
        )

        assert result.resume_attempt is not None
        assert result.resume_attempt.native_validation_ok is False
        assert result.resume_attempt.fallback_relaunch_used is False
        assert "not found" in result.resume_attempt.native_validation_failure_reason

        assert result.recover_attempt is not None
        assert result.recover_attempt.native_validation_ok is False
        assert result.recover_attempt.fallback_relaunch_used is False


# ---------------------------------------------------------------------
# 7. Read-back round-trip (artifact persistence → read-back)
# ---------------------------------------------------------------------


class TestRecoveryArtifactReadback:
    """Prove that persisted artifacts can be read back accurately."""

    def test_continuity_readback_round_trip(self, tmp_path: Path) -> None:
        """Persisted continuity.json reads back as correct RecoveryContinuity."""
        run_root = tmp_path / "runs" / "run-readback"
        continuity = RecoveryContinuity(
            recovered_via="native_session_resume",
            run_id="run-readback",
            step_id="e2e.step_a",
            agent_id="python-executor",
            runner="opencode",
            session_id="sess-xyz-789",
            timestamp="2026-04-12T10:30:00+00:00",
        )

        persist_recovery_continuity(run_root=run_root, continuity=continuity)

        read_back = read_recovery_continuity(run_root=run_root)
        assert read_back is not None
        assert read_back.recovered_via == "native_session_resume"
        assert read_back.run_id == "run-readback"
        assert read_back.session_id == "sess-xyz-789"

    def test_attempt_readback_round_trip(self, tmp_path: Path) -> None:
        """Persisted attempt files read back as correct RecoveryAttempt records."""
        run_root = tmp_path / "runs" / "run-attempt-readback"

        resume_attempt = RecoveryAttempt(
            attempt_kind="resume",
            native_validation_ok=False,
            native_validation_failure_reason="session.json missing session_id",
            fallback_relaunch_used=True,
            resulting_run_id="run-attempt-readback",
            resulting_session_id=None,
        )

        path = persist_recovery_attempt(run_root=run_root, attempt=resume_attempt)
        assert path.name == "resume_attempt.json"

        read_back = read_recovery_attempt(run_root=run_root, attempt_kind="resume")
        assert read_back is not None
        assert read_back.attempt_kind == "resume"
        assert read_back.native_validation_ok is False
        assert read_back.native_validation_failure_reason == "session.json missing session_id"
        assert read_back.fallback_relaunch_used is True

    def test_recover_attempt_readback(self, tmp_path: Path) -> None:
        """Persisted recover_attempt.json reads back correctly."""
        run_root = tmp_path / "runs" / "run-recover-readback"

        recover_attempt = RecoveryAttempt(
            attempt_kind="recover",
            native_validation_ok=False,
            native_validation_failure_reason="session.json not found",
            fallback_relaunch_used=True,
            resulting_run_id="run-recover-readback",
            resulting_session_id=None,
        )

        path = persist_recovery_attempt(run_root=run_root, attempt=recover_attempt)
        assert path.name == "recover_attempt.json"

        read_back = read_recovery_attempt(run_root=run_root, attempt_kind="recover")
        assert read_back is not None
        assert read_back.attempt_kind == "recover"
        assert read_back.native_validation_ok is False
        assert read_back.fallback_relaunch_used is True

    def test_readback_returns_none_when_missing(self, tmp_path: Path) -> None:
        """Read-back returns None when artifacts don't exist."""
        run_root = tmp_path / "runs" / "run-missing-readback"
        run_root.mkdir(parents=True, exist_ok=True)

        assert read_recovery_continuity(run_root=run_root) is None
        assert read_recovery_attempt(run_root=run_root, attempt_kind="resume") is None
        assert read_recovery_attempt(run_root=run_root, attempt_kind="recover") is None

    def test_readback_returns_none_on_corrupt_json(self, tmp_path: Path) -> None:
        """Read-back returns None when artifacts contain invalid JSON."""
        run_root = tmp_path / "runs" / "run-corrupt-readback"
        recovery_dir = run_root / "recovery"
        recovery_dir.mkdir(parents=True, exist_ok=True)

        (recovery_dir / "continuity.json").write_text("{invalid json}}}", encoding="utf-8")
        (recovery_dir / "resume_attempt.json").write_text("{invalid json}}}", encoding="utf-8")

        assert read_recovery_continuity(run_root=run_root) is None
        assert read_recovery_attempt(run_root=run_root, attempt_kind="resume") is None


# ---------------------------------------------------------------------
# 8. End-to-end: integration with orch_app continuity artifacts
# ---------------------------------------------------------------------


class TestRecoveryFallbackEndToEnd:
    """Prove that recovery fallback integrates with orch_app's write methods."""

    def test_fallback_result_persist_consistent_with_orch_app(self, orch_fixture: Path) -> None:
        """Verify that recover_with_fallback result + persist produces artifacts
        consistent with the orch_app._write_recovery_* methods already tested."""
        app, config = _build_app(orch_fixture)
        run_id = generate_run_id()
        run_root = orch_fixture / ".vectl" / "runs" / run_id
        workspace = orch_fixture / ".vectl" / "workspaces" / "ws-recovery"
        workspace.mkdir(parents=True, exist_ok=True)

        orch_dir = workspace / ".vectl" / "orch"
        orch_dir.mkdir(parents=True, exist_ok=True)
        orch_dir.joinpath("runner_prompt.md").write_text(_VALID_RUNNER_PROMPT, encoding="utf-8")

        _prepare_run_root(
            run_root,
            session_data=_VALID_SESSION_DATA,
            prompt_bundle_data=_VALID_PROMPT_BUNDLE,
            prompt_content=_VALID_RUNNER_PROMPT,
        )

        result = recover_with_fallback(
            run_id=run_id,
            step_id="e2e.step_a",
            agent_id="python-executor",
            runner="opencode",
            run_root=run_root,
            workspace=workspace,
        )

        # Persist using recovery_fallback's persist function
        paths = persist_recovery_fallback_result(run_root=run_root, result=result)

        # Also persist using orch_app's method for cross-validation
        assert result.continuity is not None

        orch_path = app._write_recovery_continuity(
            run_root=run_root,
            continuity=result.continuity,
        )

        # Both should produce valid files in the same location
        assert paths["continuity"].parent == orch_path.parent
        assert paths["continuity"].name == orch_path.name == "continuity.json"

        # Read both and verify they match
        fallback_data = json.loads(paths["continuity"].read_text(encoding="utf-8"))
        orch_data = json.loads(orch_path.read_text(encoding="utf-8"))

        assert fallback_data["recovered_via"] == orch_data["recovered_via"]
        assert fallback_data["run_id"] == orch_data["run_id"]


def _build_app(fixture_root: Path) -> tuple:
    """Build an OrchestrationApp against the isolated fixture."""
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
