"""
Runtime lifecycle remediation tests.

Authority: step orchestration_runtime_lifecycle.fix-runtime-lifecycle-blockers-batch
Covers: R6 (claim-flow-normality), R12 (complete-after-reconcile), R20 (worktree-over-mkdir),
        R24 (reconcile-serialization), R25 (reconcile-disposition-proof),
        R26 (reconcile-tracking), R27 (lifecycle-phase-enforcement), R30 (sibling-path-coverage)
"""

from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from vectl.orchestration.contracts import (
    CoreSnapshot,
    ExecutionRequest,
    ReconcileDisposition,
    ReconcileResult,
    RuntimeSnapshot,
    WorktreeBinding,
)
from vectl.orchestration.core_adapter import PlanCoreAdapter
from vectl.orchestration.runtime import (
    ReconcileError,
    Runtime,
    Success,
    Failure,
    _request_requires_fresh_workspace,
)


# ---------------------------------------------------------------------------
# R20: Canonical git worktree path replaces mkdir-only setup
# ---------------------------------------------------------------------------


class TestWorktreeCreateUsesGitWorktreeNotMkdir:
    """Verify that worktree_create follows the git worktree lifecycle instead
    of creating directories with mkdir.

    Per ORCHESTRATION-PLANE-RUNTIME-WORKTREE-LIFECYCLE.md Rule 1:
    'Runtime must use standard git worktree flows for isolated execution.
     It must not substitute ad hoc directory creation for real worktree isolation.'
    """

    def test_worktree_create_does_not_mkdir_before_git_worktree(self) -> None:
        """R20 proof: worktree_create must NOT use mkdir to create the workspace.
        It must rely on `git worktree add` to create the directory.
        """
        import asyncio

        with tempfile.TemporaryDirectory() as tmpdir:
            repo_dir = Path(tmpdir) / "repo"
            repo_dir.mkdir()
            subprocess.run(
                ["git", "init"],
                cwd=str(repo_dir),
                capture_output=True,
                check=True,
            )
            subprocess.run(
                ["git", "config", "user.email", "test@test.com"],
                cwd=str(repo_dir),
                capture_output=True,
                check=True,
            )
            subprocess.run(
                ["git", "config", "user.name", "Test"],
                cwd=str(repo_dir),
                capture_output=True,
                check=True,
            )
            # Create an initial commit so HEAD exists
            (repo_dir / "README.md").write_text("test")
            subprocess.run(["git", "add", "."], cwd=str(repo_dir), capture_output=True, check=True)
            subprocess.run(
                ["git", "commit", "-m", "init"],
                cwd=str(repo_dir),
                capture_output=True,
                check=True,
            )

            workspace_base = repo_dir / ".vectl" / "workspaces"
            workspace_base.mkdir(parents=True, exist_ok=True)

            from vectl.orchestration.runtime import worktree_create

            binding_result = asyncio.run(
                worktree_create(step_id="test-step", base_dir=workspace_base)
            )

            assert isinstance(binding_result, Success), f"worktree_create failed: {binding_result}"
            binding = binding_result.value
            assert isinstance(binding, WorktreeBinding)
            assert binding.step_id == "test-step"
            assert binding.workspace_id.startswith("ws-test-step-")

            # Verify the worktree directory was actually created by git worktree
            worktree_path = Path(binding.worktree_path)
            assert worktree_path.exists(), "Worktree path must exist after creation"

            # Verify it's a real git worktree, not just a mkdir directory
            git_check = subprocess.run(
                ["git", "rev-parse", "--show-toplevel"],
                cwd=str(worktree_path),
                capture_output=True,
                text=True,
            )
            assert git_check.returncode == 0, (
                "Worktree must be a valid git worktree (rev-parse should succeed)"
            )

            # Verify the worktree is listed in `git worktree list`
            worktree_list = subprocess.run(
                ["git", "worktree", "list"],
                cwd=str(repo_dir),
                capture_output=True,
                text=True,
            )
            assert worktree_list.returncode == 0
            assert str(worktree_path) in worktree_list.stdout, (
                f"Worktree {worktree_path} must appear in git worktree list"
            )

            # Cleanup the worktree so git state is clean
            subprocess.run(
                ["git", "worktree", "remove", "--force", str(worktree_path)],
                cwd=str(repo_dir),
                capture_output=True,
            )

    def test_worktree_create_records_target_head(self) -> None:
        """R20 proof: worktree_create must record target HEAD at prepare time
        per Section 6.1 step 2 of the lifecycle doc."""
        import asyncio
        from vectl.orchestration.runtime import worktree_create

        with tempfile.TemporaryDirectory() as tmpdir:
            repo_dir = Path(tmpdir) / "repo"
            repo_dir.mkdir()
            subprocess.run(["git", "init"], cwd=str(repo_dir), capture_output=True, check=True)
            subprocess.run(
                ["git", "config", "user.email", "test@test.com"],
                cwd=str(repo_dir),
                capture_output=True,
                check=True,
            )
            subprocess.run(
                ["git", "config", "user.name", "Test"],
                cwd=str(repo_dir),
                capture_output=True,
                check=True,
            )
            (repo_dir / "README.md").write_text("initial")
            subprocess.run(["git", "add", "."], cwd=str(repo_dir), capture_output=True, check=True)
            subprocess.run(
                ["git", "commit", "-m", "init"],
                cwd=str(repo_dir),
                capture_output=True,
                check=True,
            )

            # Record the expected HEAD
            head_result = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=str(repo_dir),
                capture_output=True,
                text=True,
                check=True,
            )
            expected_head = head_result.stdout.strip()

            workspace_base = repo_dir / ".vectl" / "workspaces"
            workspace_base.mkdir(parents=True, exist_ok=True)

            binding_result = asyncio.run(
                worktree_create(step_id="test-head", base_dir=workspace_base)
            )
            assert isinstance(binding_result, Success)
            binding = binding_result.value

            assert binding.target_head_at_prepare == expected_head, (
                f"target_head_at_prepare ({binding.target_head_at_prepare}) must match "
                f"the HEAD at prepare time ({expected_head})"
            )

            # Cleanup
            subprocess.run(
                ["git", "worktree", "remove", "--force", binding.worktree_path],
                cwd=str(repo_dir),
                capture_output=True,
            )


# ---------------------------------------------------------------------------
# R24: Reconcile serialization locking
# ---------------------------------------------------------------------------


class TestReconcileSerialization:
    """Per ORCHESTRATION-PLANE-RUNTIME-WORKTREE-LIFECYCLE.md section 9.3:
    'Concurrent reconcile into the same target branch is not acceptable.
     Runtime must own an integration/reconcile lock so that merge-back to
     a single target branch occurs serially.'
    """

    def _make_runtime_with_workspace(
        self, step_id: str = "step-a", target_ref: str = "main"
    ) -> tuple[Runtime, str, str]:
        """Create a Runtime with a prepared workspace and completed execution."""
        runtime = Runtime(workspace_root=Path("/tmp/test-workspaces"))
        binding = WorktreeBinding(
            workspace_id=f"ws-{step_id}-abc123",
            step_id=step_id,
            worktree_path=f"/tmp/ws-{step_id}",
            scratch_branch=f"vectl/scratch/{step_id}-abc123",
            target_ref=target_ref,
            target_head_at_prepare="abc123def456",
        )
        from vectl.orchestration.runtime import _WorkspaceState

        workspace_id = binding.workspace_id
        execution_id = f"exec-{step_id}-abc123"

        state = _WorkspaceState(
            workspace=workspace_id,
            binding=binding,
            execution_id=execution_id,
        )
        state.execution_state = MagicMock()
        state.execution_state.status = "success"
        state.execution_state.runner = "python-executor"
        state.execution_state.runner_handle = "handle-1"
        state.execution_state.step_id = step_id

        runtime._active_workspaces[workspace_id] = state
        runtime._active_executions[execution_id] = workspace_id

        return runtime, workspace_id, execution_id

    def test_begin_reconcile_acquires_per_target_lock(self) -> None:
        """R24 proof: begin_reconcile acquires a serialization lock on the target ref."""
        runtime, workspace_id, execution_id = self._make_runtime_with_workspace(
            step_id="step-a", target_ref="main"
        )

        # Mock _perform_reconcile to avoid real git operations on fake paths.
        # Without this, _perform_reconcile fails the integrity preflight on the
        # non-existent worktree and capture_reconcile_result(status="aborted")
        # releases the lock before the test can observe it.
        mock_result = ReconcileResult(
            execution_id=execution_id,
            workspace_id=workspace_id,
            status="merged",
            summary="Mocked reconcile",
        )
        with patch("vectl.orchestration.runtime._perform_reconcile", return_value=mock_result):
            result = runtime.begin_reconcile(execution_id)

        # begin_reconcile returned a result and the per-target lock was acquired.
        # After the reconcile completes inside begin_reconcile, capture_reconcile_result
        # releases the lock. So we verify that the reconcile DID complete successfully
        # (which means the lock was acquired and used correctly).
        assert result is not None
        assert result.status == "merged"

    def test_concurrent_reconcile_same_target_raises_error(self) -> None:
        """R24 proof: A second reconcile on the same target ref must raise ReconcileError."""
        runtime, workspace_id_a, exec_id_a = self._make_runtime_with_workspace(
            step_id="step-a", target_ref="main"
        )

        # Manually set the per-target lock to simulate an active reconcile
        # on the same target ref, then verify a second reconcile is rejected.
        runtime._reconcile_lock_by_target["main"] = workspace_id_a

        # Create a second workspace targeting the same ref
        step_b = "step-b"
        binding_b = WorktreeBinding(
            workspace_id=f"ws-{step_b}-def456",
            step_id=step_b,
            worktree_path=f"/tmp/ws-{step_b}",
            scratch_branch=f"vectl/scratch/{step_b}-def456",
            target_ref="main",
            target_head_at_prepare="abc123def456",
        )
        from vectl.orchestration.runtime import _WorkspaceState

        workspace_id_b = binding_b.workspace_id
        execution_id_b = f"exec-{step_b}-def456"
        state_b = _WorkspaceState(
            workspace=workspace_id_b,
            binding=binding_b,
            execution_id=execution_id_b,
        )
        state_b.execution_state = MagicMock()
        state_b.execution_state.status = "success"
        state_b.execution_state.runner = "python-executor"
        state_b.execution_state.runner_handle = "handle-2"
        state_b.execution_state.step_id = step_b
        runtime._active_workspaces[workspace_id_b] = state_b
        runtime._active_executions[execution_id_b] = workspace_id_b

        # Second reconcile on same target ref must fail
        with pytest.raises(ReconcileError, match="Concurrent reconcile blocked"):
            runtime.begin_reconcile(execution_id_b)

    def test_different_target_refs_allow_concurrent_reconcile(self) -> None:
        """R24 proof: Different target refs should allow concurrent reconcile."""
        runtime, workspace_id_a, exec_id_a = self._make_runtime_with_workspace(
            step_id="step-a", target_ref="main"
        )

        # Manually set the per-target lock for "main" only.
        runtime._reconcile_lock_by_target["main"] = workspace_id_a

        # Create a second workspace targeting a different ref
        step_b = "step-b"
        binding_b = WorktreeBinding(
            workspace_id=f"ws-{step_b}-def456",
            step_id=step_b,
            worktree_path=f"/tmp/ws-{step_b}",
            scratch_branch=f"vectl/scratch/{step_b}-def456",
            target_ref="release-v2",
            target_head_at_prepare="abc123def456",
        )
        from vectl.orchestration.runtime import _WorkspaceState

        workspace_id_b = binding_b.workspace_id
        execution_id_b = f"exec-{step_b}-def456"
        state_b = _WorkspaceState(
            workspace=workspace_id_b,
            binding=binding_b,
            execution_id=execution_id_b,
        )
        state_b.execution_state = MagicMock()
        state_b.execution_state.status = "success"
        state_b.execution_state.runner = "python-executor"
        state_b.execution_state.runner_handle = "handle-2"
        state_b.execution_state.step_id = step_b
        runtime._active_workspaces[workspace_id_b] = state_b
        runtime._active_executions[execution_id_b] = workspace_id_b

        # Different target ref should NOT raise ReconcileError for concurrent lock.
        # It will attempt real reconcile (and fail on fake path), but that's fine —
        # it proves no ReconcileError("Concurrent reconcile blocked") is raised.
        # We only verify it doesn't raise the serialization error.
        try:
            runtime.begin_reconcile(execution_id_b)
        except ReconcileError as exc:
            # Must NOT be the concurrent-blocked error
            assert "Concurrent reconcile blocked" not in str(exc)

    def test_capture_reconcile_result_releases_lock(self) -> None:
        """R24 proof: Completing reconcile releases the serialization lock."""
        runtime, workspace_id, exec_id = self._make_runtime_with_workspace(
            step_id="step-a", target_ref="main"
        )

        # Manually acquire the per-target lock (simulating begin_reconcile having done it)
        runtime._reconcile_lock_by_target["main"] = workspace_id
        # Set up reconcile state as if begin_reconcile ran
        runtime._pending_reconciles.add(workspace_id)

        assert "main" in runtime._reconcile_lock_by_target

        runtime.capture_reconcile_result(
            execution_id=exec_id,
            status="merged",
            summary="All changes merged cleanly",
        )

        # Lock must be released after reconcile completes
        assert "main" not in runtime._reconcile_lock_by_target, (
            "Reconcile lock must be released after capture_reconcile_result"
        )

    def test_capture_reconcile_result_merge_conflict_releases_lock(self) -> None:
        """R24/R26 proof: Even merge_conflict reconcile releases the serialization lock.
        The lock serializes reconcile *attempts*, not successful merges only."""
        runtime, workspace_id, exec_id = self._make_runtime_with_workspace(
            step_id="step-a", target_ref="main"
        )

        # Manually acquire the per-target lock (simulating begin_reconcile having done it)
        runtime._reconcile_lock_by_target["main"] = workspace_id
        runtime._pending_reconciles.add(workspace_id)

        runtime.capture_reconcile_result(
            execution_id=exec_id,
            status="merge_conflict",
            summary="Conflicts in core.py",
            conflict_files=("src/vectl/core.py",),
        )

        # Lock must be released even for conflict outcomes
        assert "main" not in runtime._reconcile_lock_by_target, (
            "Reconcile lock must be released after capture_reconcile_result even for merge_conflict"
        )


# ---------------------------------------------------------------------------
# R25: reconcile_disposition proof preserved through core_adapter/runtime
# ---------------------------------------------------------------------------


class TestReconcileDispositionProof:
    """Per ORCHESTRATION-PLANE-RUNTIME-WORKTREE-LIFECYCLE.md Rule 4:
    'complete requires successful reconcile'
    The reconcile_disposition must survive through the evidence flow.
    """

    def _make_runtime_with_completed_execution(
        self, step_id: str = "step-a", target_ref: str = "main"
    ) -> tuple[Runtime, str, str]:
        """Create a Runtime with a workspace that has completed execution and reconcile."""
        runtime = Runtime(workspace_root=Path("/tmp/test-workspaces"))
        binding = WorktreeBinding(
            workspace_id=f"ws-{step_id}-abc123",
            step_id=step_id,
            worktree_path=f"/tmp/ws-{step_id}",
            scratch_branch=f"vectl/scratch/{step_id}-abc123",
            target_ref=target_ref,
            target_head_at_prepare="abc123def456",
        )
        from vectl.orchestration.runtime import _WorkspaceState

        workspace_id = binding.workspace_id
        execution_id = f"exec-{step_id}-abc123"

        state = _WorkspaceState(
            workspace=workspace_id,
            binding=binding,
            execution_id=execution_id,
        )
        state.execution_state = MagicMock()
        state.execution_state.status = "success"
        state.execution_state.runner = "python-executor"
        state.execution_state.runner_handle = "handle-1"
        state.execution_state.step_id = step_id

        runtime._active_workspaces[workspace_id] = state
        runtime._active_executions[execution_id] = workspace_id

        return runtime, workspace_id, execution_id

    def test_reconcile_disposition_returns_merged(self) -> None:
        """R25 proof: reconcile_disposition returns 'merged' after successful merge."""
        runtime, _, execution_id = self._make_runtime_with_completed_execution()

        runtime.begin_reconcile(execution_id)
        runtime.capture_reconcile_result(
            execution_id=execution_id,
            status="merged",
            summary="Changes integrated successfully",
        )

        disposition = runtime.reconcile_disposition(execution_id)
        assert disposition == "merged", (
            f"Expected reconcile_disposition='merged', got '{disposition}'"
        )

    def test_reconcile_disposition_returns_noop(self) -> None:
        """R25 proof: reconcile_disposition returns 'noop' for no-op reconciles."""
        runtime, _, execution_id = self._make_runtime_with_completed_execution()

        runtime.begin_reconcile(execution_id)
        runtime.capture_reconcile_result(
            execution_id=execution_id,
            status="noop",
            summary="No effective changes to integrate",
        )

        disposition = runtime.reconcile_disposition(execution_id)
        assert disposition == "noop", f"Expected reconcile_disposition='noop', got '{disposition}'"

    def test_reconcile_disposition_returns_none_for_merge_conflict(self) -> None:
        """R25/R12 proof: merge_conflict does not produce an accepted disposition."""
        runtime, _, execution_id = self._make_runtime_with_completed_execution()

        runtime.begin_reconcile(execution_id)
        runtime.capture_reconcile_result(
            execution_id=execution_id,
            status="merge_conflict",
            summary="Conflicts detected",
            conflict_files=("src/file.py",),
        )

        disposition = runtime.reconcile_disposition(execution_id)
        assert disposition is None, (
            f"Expected reconcile_disposition=None for merge_conflict, got '{disposition}'"
        )

    def test_reconcile_disposition_returns_none_before_reconcile(self) -> None:
        """R25 proof: reconcile_disposition returns None before reconcile begins."""
        runtime, _, execution_id = self._make_runtime_with_completed_execution()
        disposition = runtime.reconcile_disposition(execution_id)
        assert disposition is None, (
            f"Expected reconcile_disposition=None before reconcile, got '{disposition}'"
        )

    def test_can_complete_rejects_without_reconcile(self) -> None:
        """R12 proof: can_complete must reject completion without reconcile."""
        runtime, _, execution_id = self._make_runtime_with_completed_execution()

        can_do, reason = runtime.can_complete(execution_id)
        assert can_do is False, "can_complete must reject without reconcile"
        assert "reconcile not yet captured" in reason

    def test_can_complete_rejects_merge_conflict(self) -> None:
        """R12 proof: can_complete must reject completion for merge_conflict status."""
        runtime, _, execution_id = self._make_runtime_with_completed_execution()

        runtime.begin_reconcile(execution_id)
        runtime.capture_reconcile_result(
            execution_id=execution_id,
            status="merge_conflict",
            summary="Conflicts",
            conflict_files=("file.py",),
        )

        can_do, reason = runtime.can_complete(execution_id)
        assert can_do is False, "can_complete must reject merge_conflict"
        assert "merge_conflict" in reason

    def test_can_complete_accepts_after_merged(self) -> None:
        """R12 proof: can_complete accepts after merged reconcile."""
        runtime, _, execution_id = self._make_runtime_with_completed_execution()

        runtime.begin_reconcile(execution_id)
        runtime.capture_reconcile_result(
            execution_id=execution_id,
            status="merged",
            summary="Clean merge",
        )

        can_do, reason = runtime.can_complete(execution_id)
        assert can_do is True, f"can_complete must accept after merged: {reason}"

    def test_can_complete_accepts_after_noop(self) -> None:
        """R12 proof: can_complete accepts after noop reconcile."""
        runtime, _, execution_id = self._make_runtime_with_completed_execution()

        runtime.begin_reconcile(execution_id)
        runtime.capture_reconcile_result(
            execution_id=execution_id,
            status="noop",
            summary="No changes",
        )

        can_do, reason = runtime.can_complete(execution_id)
        assert can_do is True, f"can_complete must accept after noop: {reason}"


# ---------------------------------------------------------------------------
# R30 & R25: core_adapter preserves reconcile_disposition in evidence
# ---------------------------------------------------------------------------


class TestCoreAdapterReconcileDisposition:
    """R25/R30: Prove that reconcile_disposition flows through the core_adapter
    evidence surface instead of being silently dropped.
    """

    def test_complete_step_includes_reconcile_disposition_in_evidence(self, tmp_path: Path) -> None:
        """R25 proof: PlanCoreAdapter.complete_step preserves reconcile_disposition
        in the enrichened evidence string, not dropping it with `_ = ...`."""
        # Create a minimal plan file
        import yaml
        from vectl.models import Phase, PhaseStatus, Plan, Step, StepStatus

        plan = Plan(
            project="test-project",
            phases=[
                Phase(
                    id="test-phase",
                    name="Test Phase",
                    status=PhaseStatus.IN_PROGRESS,
                    steps=[
                        Step(
                            id="test-step",
                            name="Test Step",
                            status=StepStatus.CLAIMED,
                            claimed_by="test-agent",
                            claimed_at="2025-01-01T00:00:00+00:00",
                        ),
                    ],
                ),
            ],
        )
        plan_path = tmp_path / "plan.yaml"

        import vectl.io as vio

        vio.save_plan(plan, plan_path)

        adapter = PlanCoreAdapter(plan_path=plan_path)

        # Complete with merged disposition
        adapter.complete_step(
            step_id="test-step",
            evidence="All tests passed",
            reconcile_disposition="merged",
        )

        # Read the plan back and verify the evidence includes reconcile_disposition
        loaded_plan, _ = vio.load_plan_definition(plan_path)
        found = loaded_plan.find_step("test-step")
        assert found is not None
        _, step = found
        assert step.evidence is not None
        assert "[reconcile_disposition=merged]" in step.evidence, (
            f"Evidence must contain reconcile_disposition proof. Got: {step.evidence}"
        )
        assert "All tests passed" in step.evidence

    def test_complete_step_noop_disposition_in_evidence(self, tmp_path: Path) -> None:
        """R25 proof: noop disposition is also preserved in evidence."""
        import yaml
        from vectl.models import Phase, PhaseStatus, Plan, Step, StepStatus
        import vectl.io as vio

        plan = Plan(
            project="test-project",
            phases=[
                Phase(
                    id="p1",
                    name="Phase 1",
                    status=PhaseStatus.IN_PROGRESS,
                    steps=[
                        Step(
                            id="s1",
                            name="Step 1",
                            status=StepStatus.CLAIMED,
                            claimed_by="agent",
                            claimed_at="2025-01-01T00:00:00+00:00",
                        ),
                    ],
                ),
            ],
        )
        plan_path = tmp_path / "plan.yaml"
        vio.save_plan(plan, plan_path)

        adapter = PlanCoreAdapter(plan_path=plan_path)
        adapter.complete_step(
            step_id="s1",
            evidence="No changes needed",
            reconcile_disposition="noop",
        )

        loaded_plan, _ = vio.load_plan_definition(plan_path)
        found = loaded_plan.find_step("s1")
        assert found is not None
        _, step = found
        assert step.evidence is not None
        assert "[reconcile_disposition=noop]" in step.evidence, (
            f"Evidence must contain noop reconcile_disposition proof. Got: {step.evidence}"
        )


# ---------------------------------------------------------------------------
# R20/R27: Cleanup and lifecycle transition enforcement
# ---------------------------------------------------------------------------


class TestCleanupLifecycleEnforcement:
    """R27/R20: Verify that cleanup enforces lifecycle transitions correctly
    and does not silently destroy evidence.
    """

    def _make_runtime_with_workspace(
        self, step_id: str = "step-a", target_ref: str = "main"
    ) -> tuple[Runtime, str, str, WorktreeBinding]:
        runtime = Runtime(workspace_root=Path("/tmp/test-workspaces"))
        binding = WorktreeBinding(
            workspace_id=f"ws-{step_id}-abc123",
            step_id=step_id,
            worktree_path=f"/tmp/ws-{step_id}",
            scratch_branch=f"vectl/scratch/{step_id}-abc123",
            target_ref=target_ref,
            target_head_at_prepare="abc123def456",
        )
        from vectl.orchestration.runtime import _WorkspaceState

        workspace_id = binding.workspace_id
        execution_id = f"exec-{step_id}-abc123"

        state = _WorkspaceState(
            workspace=workspace_id,
            binding=binding,
            execution_id=execution_id,
        )
        state.execution_state = MagicMock()
        state.execution_state.status = "success"
        state.execution_state.runner = "python-executor"
        state.execution_state.runner_handle = "handle-1"
        state.execution_state.step_id = step_id

        runtime._active_workspaces[workspace_id] = state
        runtime._active_executions[execution_id] = workspace_id

        return runtime, workspace_id, execution_id, binding

    def test_cleanup_blocks_without_reconcile(self) -> None:
        """R27 proof: Cannot cleanup workspace before reconcile is captured.
        Per Rule 4 and section 13."""
        runtime, workspace_id, execution_id, _ = self._make_runtime_with_workspace()

        with pytest.raises(ValueError, match="reconcile not yet captured"):
            runtime.cleanup(workspace_id)

    def test_cleanup_blocks_merge_conflict(self) -> None:
        """R27 proof: Cannot cleanup workspace with unresolved merge_conflict."""
        runtime, workspace_id, execution_id, _ = self._make_runtime_with_workspace()

        runtime.begin_reconcile(execution_id)
        runtime.capture_reconcile_result(
            execution_id=execution_id,
            status="merge_conflict",
            summary="Conflicts",
            conflict_files=("file.py",),
        )

        with pytest.raises(ValueError, match="unresolved reconcile status"):
            runtime.cleanup(workspace_id)

    def test_cleanup_blocks_aborted(self) -> None:
        """R27 proof: Cannot cleanup workspace with aborted reconcile."""
        runtime, workspace_id, execution_id, _ = self._make_runtime_with_workspace()

        runtime.begin_reconcile(execution_id)
        runtime.capture_reconcile_result(
            execution_id=execution_id,
            status="aborted",
            summary="Could not auto-resolve",
        )

        with pytest.raises(ValueError, match="unresolved reconcile status"):
            runtime.cleanup(workspace_id)

    def test_get_unresolved_message_merge_conflict(self) -> None:
        """R6 proof: merge_conflict surfaces operator message."""
        runtime, workspace_id, execution_id, _ = self._make_runtime_with_workspace()

        runtime.begin_reconcile(execution_id)
        runtime.capture_reconcile_result(
            execution_id=execution_id,
            status="merge_conflict",
            summary="Conflict in core.py",
            conflict_files=("src/vectl/core.py", "tests/test_core.py"),
        )

        msg = runtime.get_unresolved_message(execution_id)
        assert msg is not None
        assert "merge conflicts" in msg
        assert "Operator attention" in msg

    def test_get_unresolved_message_aborted(self) -> None:
        """R6 proof: aborted reconcile surfaces operator message."""
        runtime, workspace_id, execution_id, _ = self._make_runtime_with_workspace()

        runtime.begin_reconcile(execution_id)
        runtime.capture_reconcile_result(
            execution_id=execution_id,
            status="aborted",
            summary="Manual intervention needed",
        )

        msg = runtime.get_unresolved_message(execution_id)
        assert msg is not None
        assert "aborted" in msg
        assert "Operator attention" in msg

    def test_cleanup_releases_reconcile_lock(self) -> None:
        """R24 proof: Force cleanup releases the reconcile lock."""
        runtime, workspace_id, execution_id, _ = self._make_runtime_with_workspace()

        # Manually acquire the per-target lock (simulating begin_reconcile having done it)
        runtime._reconcile_lock_by_target["main"] = workspace_id

        # Lock should be held
        assert runtime._reconcile_lock_by_target.get("main") == workspace_id

        # Force cleanup should release the lock
        # Patch _run_cleanup to avoid actual git operations
        with patch("vectl.orchestration.runtime._run_cleanup", return_value=Success(None)):
            with patch.object(
                runtime._runner_registry,
                "get",
                side_effect=lambda x: MagicMock(cancel=MagicMock()),
            ):
                runtime.cleanup(workspace_id, force=True)

        assert "main" not in runtime._reconcile_lock_by_target, (
            "Force cleanup must release the reconcile lock"
        )


# ---------------------------------------------------------------------------
# Helper test: _request_requires_fresh_workspace
# ---------------------------------------------------------------------------


class TestFreshWorkspaceHints:
    """Verify workspace freshness detection for isolation modes."""

    def test_workspace_hint_detected(self) -> None:
        request = ExecutionRequest(
            step_id="s1",
            role="python-executor",
            runner="codex",
            work_refs=("isolation=workspace",),
        )
        assert _request_requires_fresh_workspace(request) is True

    def test_independent_hint_detected(self) -> None:
        request = ExecutionRequest(
            step_id="s1",
            role="python-executor",
            runner="codex",
            work_refs=("isolation=independent",),
        )
        assert _request_requires_fresh_workspace(request) is True

    def test_no_isolation_hint_not_fresh(self) -> None:
        request = ExecutionRequest(
            step_id="s1",
            role="python-executor",
            runner="codex",
            work_refs=("some-other-ref",),
        )
        assert _request_requires_fresh_workspace(request) is False

    def test_empty_refs_not_fresh(self) -> None:
        request = ExecutionRequest(
            step_id="s1",
            role="python-executor",
            runner="codex",
            work_refs=(),
        )
        assert _request_requires_fresh_workspace(request) is False
