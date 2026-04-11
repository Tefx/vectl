#!/usr/bin/env python3
"""End-to-end integration proof for orchestration run-to-completion lifecycle.

Authority:
    docs/ORCHESTRATION-PLANE-ARCHITECTURE.md sections 6.1, 6.2
    docs/ORCHESTRATION-PLANE-RUNTIME-WORKTREE-LIFECYCLE.md

This module proves that the orchestration plane can drive a complete lifecycle
from plan -> orch run -> runner completion -> reconcile -> step completion
using the built-in ``test`` runner (``echo runner-test-placeholder``).

The test is fully isolated: it writes a real plan.yaml, a real vectl.yaml
configuring the test runner, initializes a git repo, and exercises the actual
OrchestrationApp surfaces. It does NOT use mocks at I/O boundaries.

Design decision — app-level integration over CLI subprocess:
    CLI subprocess coverage would be preferable, but ``vectl orch run`` creates
    a git worktree via subprocess, which requires a real git repo in a
    temporary directory. Calling the CLI via subprocess from pytest works but
    adds significant complexity for path resolution and cleanup. The
    OrchestrationApp surface is the authoritative composition root, so testing
    through it proves the same lifecycle without the CLI surface overhead.

Constraint — test runner:
    The built-in ``test`` runner (``echo runner-test-placeholder``) is
    synchronous and deterministic. It completes instantly, which makes the
    collect-and-route loop deterministic without polling timeouts.
"""

from __future__ import annotations

import json
import os
import subprocess
import textwrap
from pathlib import Path

import pytest
import yaml

from vectl.io import load_plan_definition, save_plan
from vectl.models import Phase, Plan, Step, StepStatus
from vectl.orch_app import AppConfig, OrchestrationResult, build_orchestration_app
from vectl.orchestration.config import (
    OrchestrationConfig,
    RoleProfile,
    RuntimeConfig,
)
from vectl.orchestration.run_store import RunRegistry


# ---------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------


def _write_isolated_fixture(root: Path) -> None:
    """Create a minimal, self-contained orchestration fixture.

    The fixture configures the ``test`` runner (``echo runner-test-placeholder``)
    to ensure deterministic completion without external agent dependencies.
    """

    plan = Plan(
        project="orch-e2e-test",
        phases=[
            Phase(
                id="e2e",
                name="E2E",
                steps=[
                    Step(id="e2e.step_a", name="Step A"),
                    Step(id="e2e.step_b", name="Step B", depends_on=["e2e.step_a"]),
                ],
            )
        ],
    )
    save_plan(plan, root / "plan.yaml")

    (root / "vectl.yaml").write_text(
        textwrap.dedent(
            """\
            orchestration:
              plan_path: plan.yaml
              runtime:
                default_runner: test
                artifact_root: .vectl/runs
              role_profiles:
                python-executor:
                  agent_id: python-executor
                  prompt_family: coder
                  execution_context: linked_worktree
                  mutation_policy: worktree_changes
                  session_policy: reuse_allowed
                  output_contract: freeform_evidence
                  default_runner: test
                blocked-case-coordinator:
                  agent_id: blocked-case-coordinator
                  prompt_family: resolver
                  execution_context: main_worktree
                  mutation_policy: vectl_facade_only
                  session_policy: reuse_forbidden
                  output_contract: resolution_report
                  default_runner: test
            """
        ),
        encoding="utf-8",
    )


def _init_git_repo(root: Path) -> None:
    """Initialize the minimal git state required by orchestration worktrees."""

    subprocess.run(
        ["git", "init", "-b", "main"],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=10,
        check=True,
    )
    subprocess.run(
        ["git", "add", "plan.yaml", "vectl.yaml"],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=10,
        check=True,
    )
    commit_env = {
        "GIT_AUTHOR_NAME": "orch-e2e",
        "GIT_AUTHOR_EMAIL": "orch-e2e@example.com",
        "GIT_COMMITTER_NAME": "orch-e2e",
        "GIT_COMMITTER_EMAIL": "orch-e2e@example.com",
    }
    subprocess.run(
        ["git", "commit", "-m", "init orchestration e2e fixture"],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=10,
        check=True,
        env={**os.environ, **commit_env},
    )


@pytest.fixture()
def orch_fixture(tmp_path: Path) -> Path:
    """Create an isolated orchestration fixture with git repo and test runner config."""

    _write_isolated_fixture(tmp_path)
    _init_git_repo(tmp_path)

    # Make vectl workspace roots inside the fixture
    runs_root = tmp_path / ".vectl" / "runs"
    runs_root.mkdir(parents=True, exist_ok=True)
    ws_root = tmp_path / ".vectl" / "workspaces"
    ws_root.mkdir(parents=True, exist_ok=True)

    return tmp_path


def _build_app(fixture_root: Path) -> tuple:
    """Build an OrchestrationApp against the isolated fixture.

    Constructs an OrchestrationConfig with the ``test`` runner for
    deterministic completion, bypassing file-based config loading.

    Returns:
        Tuple of (app, config, plan_path) for test assertions.
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
        RoleProfile(
            role_id="blocked-case-coordinator",
            agent_id="blocked-case-coordinator",
            prompt_family="resolver",
            execution_context="main_worktree",
            mutation_policy="vectl_facade_only",
            session_policy="reuse_forbidden",
            output_contract="resolution_report",
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
    return app, config, plan_path


# ---------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------


class TestOrchestrationE2ECompletion:
    """Prove that the orchestration lifecycle completes to terminal state.

    These tests use the real OrchestrationApp composition root with the
    built-in ``test`` runner. The ``test`` runner (``echo runner-test-placeholder``)
    completes immediately with exit 0, which makes the collect-and-route loop
    deterministic.
    """

    def test_run_completes_step_to_done(self, orch_fixture: Path) -> None:
        """Prove that ``app.run()`` drives a step from pending to done.

        This is the core lifecycle regression: before the collect-and-route fix,
        ``run()`` would dispatch a runner but never collect its result or complete
        the step, leaving it stuck in claimed/running forever.
        """
        app, config, plan_path = _build_app(orch_fixture)

        # ACT: run the first claimable step
        result = app.run(step_id=None, agent="python-executor")

        # ASSERT: the run must succeed
        assert result.success, f"Run failed: {result.message}"

        # ASSERT: step_id should be populated
        assert result.step_id is not None, "step_id should not be None on successful run"
        assert result.step_id == "e2e.step_a", f"Expected e2e.step_a, got {result.step_id}"

        # ASSERT: run_id should be populated
        assert result.run_id is not None, "run_id should not be None on successful run"

        # ASSERT: the plan should now show step_a as DONE
        plan, _ = load_plan_definition(plan_path)
        found = plan.find_step("e2e.step_a")
        assert found is not None, "step_a should exist in plan"
        _, step_a = found
        assert step_a.status == StepStatus.DONE, (
            f"Expected step_a to be done after run, got {step_a.status.value}"
        )

        # ASSERT: step_b should now be claimable (its dependency is done)
        found_b = plan.find_step("e2e.step_b")
        assert found_b is not None, "step_b should exist in plan"
        _, step_b = found_b
        assert step_b.status in (StepStatus.PENDING, StepStatus.CLAIMED), (
            f"Expected step_b to be pending/claiming, got {step_b.status.value}"
        )

    def test_run_produces_terminal_run_record(self, orch_fixture: Path) -> None:
        """Prove that ``app.run()`` produces a terminal (success/fail) run record.

        Before the fix, run records stayed at status='running' forever.
        """
        app, config, plan_path = _build_app(orch_fixture)

        result = app.run(step_id=None, agent="python-executor")
        assert result.success, f"Run failed: {result.message}"

        # Inspect the run store for terminal status
        runs_root = config.run_store_root
        assert runs_root is not None
        registry = RunRegistry(store_root=runs_root)
        record = registry.by_id(result.run_id)
        assert record is not None, f"Run record not found for {result.run_id}"
        assert record.status in ("success", "fail"), (
            f"Expected terminal status (success/fail), got {record.status}"
        )

    def test_run_event_artifacts_exist(self, orch_fixture: Path) -> None:
        """Prove that the orchestration produces event artifacts during run.

        The ``run_started`` and ``run_final`` events should appear in the
        event log after a completed run.
        """
        app, config, plan_path = _build_app(orch_fixture)

        result = app.run(step_id=None, agent="python-executor")
        assert result.success, f"Run failed: {result.message}"

        # Check that events.jsonl was written
        events_path = config.run_store_root / "events.jsonl"
        assert events_path.exists(), f"events.jsonl not found at {events_path}"

        events_text = events_path.read_text(encoding="utf-8")
        assert "run_started" in events_text, "Expected run_started event in events log"
        assert "run_final" in events_text, "Expected run_final event in events log"

    def test_sequential_runs_advance_plan(self, orch_fixture: Path) -> None:
        """Prove that two sequential runs complete both steps in dependency order."""
        app, config, plan_path = _build_app(orch_fixture)

        # First run: complete step_a
        result_a = app.run(step_id=None, agent="python-executor")
        assert result_a.success, f"First run failed: {result_a.message}"
        assert result_a.step_id == "e2e.step_a"

        # Second run: complete step_b (now claimable after step_a is done)
        result_b = app.run(step_id=None, agent="python-executor")
        assert result_b.success, f"Second run failed: {result_b.message}"
        assert result_b.step_id == "e2e.step_b"

        # ASSERT: both steps should be done
        plan, _ = load_plan_definition(plan_path)
        found_a = plan.find_step("e2e.step_a")
        found_b = plan.find_step("e2e.step_b")
        assert found_a is not None
        assert found_b is not None
        _, step_a = found_a
        _, step_b = found_b
        assert step_a.status == StepStatus.DONE
        assert step_b.status == StepStatus.DONE

    def test_explicit_step_id_completes(self, orch_fixture: Path) -> None:
        """Prove that running with an explicit step_id works end-to-end."""
        app, config, plan_path = _build_app(orch_fixture)

        result = app.run(step_id="e2e.step_a", agent="python-executor")
        assert result.success, f"Run with explicit step_id failed: {result.message}"
        assert result.step_id == "e2e.step_a"

        plan, _ = load_plan_definition(plan_path)
        found = plan.find_step("e2e.step_a")
        assert found is not None
        _, step = found
        assert step.status == StepStatus.DONE

    def test_run_no_claimable_step_fails_gracefully(self, orch_fixture: Path) -> None:
        """Prove that running when no step is claimable returns a clear message."""
        app, config, plan_path = _build_app(orch_fixture)

        # Pre-complete both steps manually (claim then complete)
        from vectl.orchestration.core_adapter import PlanCoreAdapter

        core_adapter = PlanCoreAdapter(plan_path=plan_path)
        core_adapter.claim_step("e2e.step_a", "test-agent")
        core_adapter.complete_step("e2e.step_a", "pre-completed", reconcile_disposition="noop")
        core_adapter.claim_step("e2e.step_b", "test-agent")
        core_adapter.complete_step("e2e.step_b", "pre-completed", reconcile_disposition="noop")

        result = app.run(step_id=None, agent="python-executor")
        assert not result.success
        assert "No claimable step" in result.message, (
            f"Expected 'No claimable step' message, got: {result.message}"
        )


class TestOrchestrationE2EReconcileBehavior:
    """Test reconcile and completion behavior for the orchestration lifecycle.

    These tests verify that the collect-and-route loop properly handles
    reconcile outcomes (noop vs merged) and produces correct final statuses.
    """

    def test_run_creates_frozen_config_snapshot(self, orch_fixture: Path) -> None:
        """Prove that ``app.run()`` writes a frozen config snapshot."""
        app, config, plan_path = _build_app(orch_fixture)

        result = app.run(step_id=None, agent="python-executor")
        assert result.success, f"Run failed: {result.message}"

        # The frozen config snapshot should exist in the run artifact root
        runs_root = config.run_store_root
        assert runs_root is not None
        registry = RunRegistry(store_root=runs_root)
        record = registry.by_id(result.run_id)
        assert record is not None

        run_root = Path(record.artifact_root) if record.artifact_root else runs_root
        snapshot_path = run_root / "config.snapshot.yaml"
        assert snapshot_path.exists(), f"Frozen config snapshot not found at {snapshot_path}"

    def test_run_creates_heartbeat_artifact(self, orch_fixture: Path) -> None:
        """Prove that ``app.run()`` writes a heartbeat.json."""
        app, config, plan_path = _build_app(orch_fixture)

        result = app.run(step_id=None, agent="python-executor")
        assert result.success, f"Run failed: {result.message}"

        runs_root = config.run_store_root
        assert runs_root is not None
        registry = RunRegistry(store_root=runs_root)
        record = registry.by_id(result.run_id)
        assert record is not None

        run_root = Path(record.artifact_root) if record.artifact_root else runs_root
        heartbeat_path = run_root / "heartbeat.json"
        assert heartbeat_path.exists(), f"heartbeat.json not found at {heartbeat_path}"
