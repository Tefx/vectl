"""Runtime lifecycle tests for reconcile and completion blocking.

Authority:
    docs/ORCHESTRATION-PLANE-RUNTIME-WORKTREE-LIFECYCLE.md
    docs/ORCHESTRATION-PLANE-INTERFACES.md section 4.3
"""

from __future__ import annotations

import shutil
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path

import pytest

from vectl.orchestration.contracts import ExecutionRequest
from vectl.orchestration.runner_registry import RunnerRegistry
from vectl.orchestration.runners import (
    RunnerCapabilities,
    RunnerHandle,
    RunnerLaunchResult,
    RunnerPollResult,
    RunnerResumeError,
)
from vectl.orchestration import runtime as runtime_module
from vectl.orchestration.runtime import Runtime


@pytest.fixture
def temp_git_repo(tmp_path: Path) -> Path:
    """Create a temporary git repository with initial commit."""

    repo_root = tmp_path / "repo"
    repo_root.mkdir()

    subprocess.run(["git", "init"], cwd=repo_root, capture_output=True, check=True)
    subprocess.run(
        ["git", "config", "user.email", "test@test.com"],
        cwd=repo_root,
        capture_output=True,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=repo_root,
        capture_output=True,
        check=True,
    )

    (repo_root / "README.md").write_text("# Runtime Test Repository\n")
    subprocess.run(["git", "add", "README.md"], cwd=repo_root, capture_output=True, check=True)
    subprocess.run(
        ["git", "commit", "-m", "Initial commit"],
        cwd=repo_root,
        capture_output=True,
        check=True,
    )
    return repo_root


def _request(step_id: str, work_refs: tuple[str, ...] = ()) -> ExecutionRequest:
    return ExecutionRequest(
        step_id=step_id,
        role="python-executor",
        runner="test",
        work_refs=work_refs,
        session_id=None,
    )


def test_prepare_creates_workspace(temp_git_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Test that prepare creates workspace directory."""
    base_dir = temp_git_repo / ".vectl" / "workspaces"
    runtime = Runtime(workspace_root=base_dir)
    base_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.chdir(temp_git_repo)

    request = _request(step_id="test-prepare")
    workspace = runtime.prepare(request)

    assert workspace in runtime.snapshot().active_workspaces
    assert (base_dir / request.step_id).exists()


def test_start_creates_execution_state(
    temp_git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test that start creates execution tracking state."""
    base_dir = temp_git_repo / ".vectl" / "workspaces"
    runtime = Runtime(workspace_root=base_dir)
    base_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.chdir(temp_git_repo)

    request = _request(step_id="test-start")
    workspace = runtime.prepare(request)
    execution_id = runtime.start(request=request, workspace=workspace)

    assert execution_id in runtime.snapshot().active_executions
    state = runtime._active_workspaces[workspace]
    assert state.execution_state is not None
    assert state.execution_state.status == "running"
    assert state.execution_state.step_id == request.step_id


def test_snapshot_includes_reconcile_states(
    temp_git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test that RuntimeSnapshot includes pending/active/conflicted reconcile states."""
    base_dir = temp_git_repo / ".vectl" / "workspaces"
    runtime = Runtime(workspace_root=base_dir)
    base_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.chdir(temp_git_repo)

    request = _request(step_id="test-snapshot")
    workspace = runtime.prepare(request)
    runtime.start(request=request, workspace=workspace)

    snapshot = runtime.snapshot()
    assert hasattr(snapshot, "pending_reconciles")
    assert hasattr(snapshot, "active_reconciles")
    assert hasattr(snapshot, "conflicted_reconciles")


def test_can_complete_requires_reconcile(
    temp_git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test that can_complete returns False without reconcile."""
    base_dir = temp_git_repo / ".vectl" / "workspaces"
    runtime = Runtime(workspace_root=base_dir)
    base_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.chdir(temp_git_repo)

    request = _request(step_id="test-complete-block")
    workspace = runtime.prepare(request)
    execution_id = runtime.start(request=request, workspace=workspace)

    # Mark execution as finished (simulating completion)
    state = runtime._active_workspaces[workspace]
    assert state.execution_state is not None
    state.execution_state.status = "success"

    # Without reconcile captured, completion is blocked
    can_complete, reason = runtime.can_complete(execution_id)
    assert can_complete is False
    assert "reconcile not yet captured" in reason.lower()


def test_can_complete_allows_merged(temp_git_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Test that can_complete returns True after merged reconcile."""
    base_dir = temp_git_repo / ".vectl" / "workspaces"
    runtime = Runtime(workspace_root=base_dir)
    base_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.chdir(temp_git_repo)

    request = _request(step_id="test-complete-merged")
    workspace = runtime.prepare(request)
    execution_id = runtime.start(request=request, workspace=workspace)

    # Mark execution as finished
    state = runtime._active_workspaces[workspace]
    assert state.execution_state is not None
    state.execution_state.status = "success"

    # Begin reconcile
    runtime.begin_reconcile(execution_id)

    # Capture successful reconcile
    runtime.capture_reconcile_result(
        execution_id=execution_id,
        status="merged",
        summary="Successfully merged",
    )

    # Now completion is allowed
    can_complete, reason = runtime.can_complete(execution_id)
    assert can_complete is True
    assert "merged" in reason


def test_can_complete_allows_noop(temp_git_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Test that can_complete returns True after noop reconcile."""
    base_dir = temp_git_repo / ".vectl" / "workspaces"
    runtime = Runtime(workspace_root=base_dir)
    base_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.chdir(temp_git_repo)

    request = _request(step_id="test-complete-noop")
    workspace = runtime.prepare(request)
    execution_id = runtime.start(request=request, workspace=workspace)

    # Mark execution as finished
    state = runtime._active_workspaces[workspace]
    assert state.execution_state is not None
    state.execution_state.status = "success"

    runtime.begin_reconcile(execution_id)
    runtime.capture_reconcile_result(
        execution_id=execution_id,
        status="noop",
        summary="No changes to merge",
    )

    can_complete, reason = runtime.can_complete(execution_id)
    assert can_complete is True


def test_can_complete_blocks_merge_conflict(
    temp_git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test that can_complete returns False after merge_conflict reconcile."""
    base_dir = temp_git_repo / ".vectl" / "workspaces"
    runtime = Runtime(workspace_root=base_dir)
    base_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.chdir(temp_git_repo)

    request = _request(step_id="test-complete-conflict")
    workspace = runtime.prepare(request)
    execution_id = runtime.start(request=request, workspace=workspace)

    # Mark execution as finished
    state = runtime._active_workspaces[workspace]
    assert state.execution_state is not None
    state.execution_state.status = "success"

    runtime.begin_reconcile(execution_id)
    runtime.capture_reconcile_result(
        execution_id=execution_id,
        status="merge_conflict",
        summary="Merge conflict detected",
        conflict_files=("file1.py", "file2.py"),
    )

    can_complete, reason = runtime.can_complete(execution_id)
    assert can_complete is False
    assert "merge_conflict" in reason


def test_get_unresolved_message_returns_none_for_success(
    temp_git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test that get_unresolved_message returns None for successful reconcile."""
    base_dir = temp_git_repo / ".vectl" / "workspaces"
    runtime = Runtime(workspace_root=base_dir)
    base_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.chdir(temp_git_repo)

    request = _request(step_id="test-unresolved-success")
    workspace = runtime.prepare(request)
    execution_id = runtime.start(request=request, workspace=workspace)

    state = runtime._active_workspaces[workspace]
    assert state.execution_state is not None
    state.execution_state.status = "success"

    runtime.begin_reconcile(execution_id)
    runtime.capture_reconcile_result(
        execution_id=execution_id,
        status="merged",
        summary="Successfully merged",
    )

    message = runtime.get_unresolved_message(execution_id)
    assert message is None


def test_get_unresolved_message_includes_operator_attention(
    temp_git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test that get_unresolved_message surfaces operator attention for conflicts."""
    base_dir = temp_git_repo / ".vectl" / "workspaces"
    runtime = Runtime(workspace_root=base_dir)
    base_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.chdir(temp_git_repo)

    request = _request(step_id="test-unresolved-conflict")
    workspace = runtime.prepare(request)
    execution_id = runtime.start(request=request, workspace=workspace)

    state = runtime._active_workspaces[workspace]
    assert state.execution_state is not None
    state.execution_state.status = "success"

    runtime.begin_reconcile(execution_id)
    runtime.capture_reconcile_result(
        execution_id=execution_id,
        status="merge_conflict",
        summary="Conflicts in multiple files",
        conflict_files=("file1.py", "file2.py", "file3.py"),
    )

    message = runtime.get_unresolved_message(execution_id)
    assert message is not None
    assert "operator attention required" in message.lower()
    assert "file1.py" in message
    assert "file2.py" in message


def test_cleanup_blocks_on_active_execution(
    temp_git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test that cleanup raises error when execution is still running."""
    base_dir = temp_git_repo / ".vectl" / "workspaces"
    runtime = Runtime(workspace_root=base_dir)
    base_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.chdir(temp_git_repo)

    request = _request(step_id="test-cleanup-active")
    workspace = runtime.prepare(request)
    runtime.start(request=request, workspace=workspace)

    # Execution is still running, so cleanup should raise
    with pytest.raises(ValueError, match="still active"):
        runtime.cleanup(workspace)


def test_cleanup_blocks_on_unresolved_reconcile(
    temp_git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test that cleanup raises error when reconcile is unresolved."""
    base_dir = temp_git_repo / ".vectl" / "workspaces"
    runtime = Runtime(workspace_root=base_dir)
    base_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.chdir(temp_git_repo)

    request = _request(step_id="test-cleanup-unresolved")
    workspace = runtime.prepare(request)
    execution_id = runtime.start(request=request, workspace=workspace)

    state = runtime._active_workspaces[workspace]
    assert state.execution_state is not None
    state.execution_state.status = "fail"  # Execution finished but failed

    runtime.begin_reconcile(execution_id)
    runtime.capture_reconcile_result(
        execution_id=execution_id,
        status="aborted",
        summary="Reconcile failed",
    )

    with pytest.raises(ValueError, match="unresolved reconcile status"):
        runtime.cleanup(workspace)


def test_cleanup_allows_after_merged_reconcile(
    temp_git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test that cleanup succeeds after successful reconcile."""
    base_dir = temp_git_repo / ".vectl" / "workspaces"
    runtime = Runtime(workspace_root=base_dir)
    base_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.chdir(temp_git_repo)

    request = _request(step_id="test-cleanup-success")
    workspace = runtime.prepare(request)
    execution_id = runtime.start(request=request, workspace=workspace)

    state = runtime._active_workspaces[workspace]
    assert state.execution_state is not None
    state.execution_state.status = "success"

    runtime.begin_reconcile(execution_id)
    runtime.capture_reconcile_result(
        execution_id=execution_id,
        status="merged",
        summary="Successfully merged",
    )

    runtime.cleanup(workspace)

    assert workspace not in runtime.snapshot().active_workspaces
    assert not (base_dir / request.step_id).exists()


def test_capture_reconcile_result_updates_tracking_sets(
    temp_git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test reconcile tracking follows the documented runtime state machine."""
    base_dir = temp_git_repo / ".vectl" / "workspaces"
    runtime = Runtime(workspace_root=base_dir)
    base_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.chdir(temp_git_repo)

    request = _request(step_id="test-tracking-sets")
    workspace = runtime.prepare(request)
    execution_id = runtime.start(request=request, workspace=workspace)

    state = runtime._active_workspaces[workspace]
    assert state.execution_state is not None
    state.execution_state.status = "success"

    entered_reconcile = threading.Event()
    finish_reconcile = threading.Event()

    def fake_perform_reconcile(**_: object):
        entered_reconcile.set()
        finish_reconcile.wait(timeout=5)
        return runtime_module.ReconcileResult(
            execution_id=execution_id,
            workspace_id=workspace,
            status="merged",
            summary="Merged",
        )

    monkeypatch.setattr(runtime_module, "_perform_reconcile", fake_perform_reconcile)

    reconcile_thread = threading.Thread(
        target=runtime.begin_reconcile,
        args=(execution_id,),
    )
    reconcile_thread.start()
    assert entered_reconcile.wait(timeout=5)

    # pending/active are in-flight observability only while reconcile is running
    in_flight = runtime.snapshot()
    assert workspace in in_flight.pending_reconciles
    assert workspace in in_flight.active_reconciles

    finish_reconcile.set()
    reconcile_thread.join(timeout=5)
    assert not reconcile_thread.is_alive()

    after_merge = runtime.snapshot()
    assert workspace not in after_merge.pending_reconciles
    assert workspace not in after_merge.active_reconciles
    assert workspace not in after_merge.conflicted_reconciles

    # Capture merge_conflict - should move to conflicted set
    runtime.capture_reconcile_result(
        execution_id=execution_id,
        status="merge_conflict",
        summary="Conflict",
        conflict_files=("file.py",),
    )
    snapshot = runtime.snapshot()
    assert workspace not in snapshot.pending_reconciles
    assert workspace in snapshot.conflicted_reconciles

    # Simulate conflict resolution - re-capture with merged
    runtime.capture_reconcile_result(
        execution_id=execution_id,
        status="merged",
        summary="Merged after resolution",
    )
    snapshot = runtime.snapshot()
    assert workspace not in snapshot.conflicted_reconciles


def test_begin_reconcile_executes_real_merge_into_integration_context(
    temp_git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R20 main-path proof: reconcile performs real integration merge."""

    base_dir = temp_git_repo / ".vectl" / "workspaces"
    runtime = Runtime(workspace_root=base_dir)
    base_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.chdir(temp_git_repo)

    request = _request(step_id="test-reconcile-real-merge")
    workspace = runtime.prepare(request)
    execution_id = runtime.start(request=request, workspace=workspace)

    workspace_path = base_dir / request.step_id
    merged_file = workspace_path / "feature.txt"
    merged_file.write_text("integrated by runtime reconcile\n", encoding="utf-8")
    subprocess.run(
        ["git", "add", "feature.txt"], cwd=workspace_path, capture_output=True, check=True
    )
    subprocess.run(
        ["git", "commit", "-m", "runtime reconcile content"],
        cwd=workspace_path,
        capture_output=True,
        check=True,
    )

    state = runtime._active_workspaces[workspace]
    assert state.execution_state is not None
    state.execution_state.status = "success"

    reconcile_result = runtime.begin_reconcile(execution_id)
    assert reconcile_result is not None
    assert reconcile_result.status == "merged"
    assert (temp_git_repo / "feature.txt").read_text(encoding="utf-8") == (
        "integrated by runtime reconcile\n"
    )


def test_begin_reconcile_returns_aborted_when_worktree_integrity_preflight_fails(
    temp_git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R26 failure-path proof: missing worktree fails integrity preflight."""

    base_dir = temp_git_repo / ".vectl" / "workspaces"
    runtime = Runtime(workspace_root=base_dir)
    base_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.chdir(temp_git_repo)

    request = _request(step_id="test-reconcile-missing-worktree")
    workspace = runtime.prepare(request)
    execution_id = runtime.start(request=request, workspace=workspace)

    state = runtime._active_workspaces[workspace]
    assert state.execution_state is not None
    state.execution_state.status = "success"

    shutil.rmtree(base_dir / request.step_id)

    reconcile_result = runtime.begin_reconcile(execution_id)
    assert reconcile_result is not None
    assert reconcile_result.status == "aborted"
    assert "Worktree integrity preflight failed" in reconcile_result.summary


def test_begin_reconcile_returns_aborted_when_integration_context_is_dirty(
    temp_git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Dirty tracked integration context reconciles through git autostash."""

    base_dir = temp_git_repo / ".vectl" / "workspaces"
    runtime = Runtime(workspace_root=base_dir)
    base_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.chdir(temp_git_repo)

    request = _request(step_id="test-reconcile-dirty-integration")
    workspace = runtime.prepare(request)
    execution_id = runtime.start(request=request, workspace=workspace)

    workspace_path = base_dir / request.step_id
    (workspace_path / "integration.txt").write_text("requires merge\n", encoding="utf-8")
    subprocess.run(
        ["git", "add", "integration.txt"], cwd=workspace_path, capture_output=True, check=True
    )
    subprocess.run(
        ["git", "commit", "-m", "integration context preflight candidate"],
        cwd=workspace_path,
        capture_output=True,
        check=True,
    )

    (temp_git_repo / "README.md").write_text("dirty tracked change\n", encoding="utf-8")

    state = runtime._active_workspaces[workspace]
    assert state.execution_state is not None
    state.execution_state.status = "success"

    reconcile_result = runtime.begin_reconcile(execution_id)
    assert reconcile_result is not None
    assert reconcile_result.status == "merged"
    assert "autostash" in reconcile_result.summary
    assert (temp_git_repo / "README.md").read_text(encoding="utf-8") == "dirty tracked change\n"
    assert (temp_git_repo / "integration.txt").read_text(encoding="utf-8") == "requires merge\n"


def test_begin_reconcile_adopts_identical_untracked_collision(
    temp_git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Identical untracked files do not block retry merge reconciliation."""

    base_dir = temp_git_repo / ".vectl" / "workspaces"
    runtime = Runtime(workspace_root=base_dir)
    base_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.chdir(temp_git_repo)

    request = _request(step_id="test-reconcile-untracked-collision")
    workspace = runtime.prepare(request)
    execution_id = runtime.start(request=request, workspace=workspace)

    workspace_path = base_dir / request.step_id
    (workspace_path / "new_owner.py").write_text("value = 1\n", encoding="utf-8")
    (temp_git_repo / "new_owner.py").write_text("value = 1\n", encoding="utf-8")

    state = runtime._active_workspaces[workspace]
    assert state.execution_state is not None
    state.execution_state.status = "success"

    reconcile_result = runtime.begin_reconcile(execution_id)
    assert reconcile_result is not None
    assert reconcile_result.status == "merged"
    assert "untracked_collisions_adopted:new_owner.py" in reconcile_result.artifact_refs
    assert (temp_git_repo / "new_owner.py").read_text(encoding="utf-8") == "value = 1\n"
    tracked = subprocess.run(
        ["git", "ls-files", "new_owner.py"],
        cwd=temp_git_repo,
        capture_output=True,
        text=True,
        check=True,
    )
    assert tracked.stdout.strip() == "new_owner.py"


def test_begin_reconcile_backs_up_nonidentical_untracked_collision(
    temp_git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Non-identical untracked collisions are preserved and do not block merge."""

    base_dir = temp_git_repo / ".vectl" / "workspaces"
    runtime = Runtime(workspace_root=base_dir)
    base_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.chdir(temp_git_repo)

    request = _request(step_id="test-reconcile-untracked-backup")
    workspace = runtime.prepare(request)
    execution_id = runtime.start(request=request, workspace=workspace)

    workspace_path = base_dir / request.step_id
    (workspace_path / "new_owner.py").write_text("incoming = 1\n", encoding="utf-8")
    (temp_git_repo / "new_owner.py").write_text("local = 2\n", encoding="utf-8")

    state = runtime._active_workspaces[workspace]
    assert state.execution_state is not None
    state.execution_state.status = "success"

    reconcile_result = runtime.begin_reconcile(execution_id)
    assert reconcile_result is not None
    assert reconcile_result.status == "merged"
    refs = "\n".join(reconcile_result.artifact_refs)
    assert "untracked_collision_backup:new_owner.py->" in refs
    assert (temp_git_repo / "new_owner.py").read_text(encoding="utf-8") == "incoming = 1\n"
    backup = temp_git_repo / ".vectl" / "reconcile-untracked" / execution_id / "new_owner.py"
    assert backup.read_text(encoding="utf-8") == "local = 2\n"


def test_begin_reconcile_filters_protected_paths_before_merge(
    temp_git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R25 proof: protected plan.yaml changes are restored before reconcile."""

    (temp_git_repo / "plan.yaml").write_text("project: protected\n", encoding="utf-8")
    subprocess.run(["git", "add", "plan.yaml"], cwd=temp_git_repo, capture_output=True, check=True)
    subprocess.run(
        ["git", "commit", "-m", "add authoritative plan"],
        cwd=temp_git_repo,
        capture_output=True,
        check=True,
    )

    base_dir = temp_git_repo / ".vectl" / "workspaces"
    runtime = Runtime(workspace_root=base_dir)
    base_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.chdir(temp_git_repo)

    request = _request(step_id="test-protected-path")
    workspace = runtime.prepare(request)
    execution_id = runtime.start(request=request, workspace=workspace)

    workspace_path = base_dir / request.step_id
    (workspace_path / "plan.yaml").write_text("project: tampered\n", encoding="utf-8")
    subprocess.run(["git", "add", "plan.yaml"], cwd=workspace_path, capture_output=True, check=True)
    subprocess.run(
        ["git", "commit", "-m", "attempt protected path change"],
        cwd=workspace_path,
        capture_output=True,
        check=True,
    )

    state = runtime._active_workspaces[workspace]
    assert state.execution_state is not None
    state.execution_state.status = "success"

    reconcile_result = runtime.begin_reconcile(execution_id)
    assert reconcile_result is not None
    assert reconcile_result.status == "noop"
    assert "protected_paths_restored:plan.yaml" in reconcile_result.artifact_refs
    assert (temp_git_repo / "plan.yaml").read_text(encoding="utf-8") == "project: protected\n"


def test_begin_reconcile_enforces_serialization_lock_conflict(
    temp_git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R24 failure-path proof: concurrent reconcile attempt fails fast."""

    base_dir = temp_git_repo / ".vectl" / "workspaces"
    runtime = Runtime(workspace_root=base_dir)
    base_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.chdir(temp_git_repo)

    request = _request(step_id="test-reconcile-lock")
    workspace = runtime.prepare(request)
    execution_id = runtime.start(request=request, workspace=workspace)

    state = runtime._active_workspaces[workspace]
    assert state.execution_state is not None
    state.execution_state.status = "success"

    acquired = runtime._reconcile_lock.acquire(blocking=False)
    assert acquired is True
    try:
        with pytest.raises(Exception, match="Reconcile lock busy"):
            runtime.begin_reconcile(execution_id)
    finally:
        runtime._reconcile_lock.release()


@dataclass
class _ResumeCapableRunner:
    """Test runner backend proving runtime resume-path substrate reuse wiring."""

    launched: int = 0
    resumed: int = 0

    def capabilities(self) -> RunnerCapabilities:
        return RunnerCapabilities(
            runner_id="resume-capable",
            supports_resume=True,
            supports_cancel=True,
            supports_streaming=False,
        )

    def launch(self, request: ExecutionRequest, workspace: Path) -> RunnerLaunchResult:
        self.launched += 1
        return RunnerLaunchResult(
            handle=RunnerHandle(
                runner="resume-capable", run_id=f"launch-{request.step_id}", session_id=None
            ),
            initial_summary="launch",
        )

    def resume(self, request: ExecutionRequest, workspace: Path) -> RunnerLaunchResult:
        self.resumed += 1
        return RunnerLaunchResult(
            handle=RunnerHandle(
                runner="resume-capable",
                run_id=f"resume-{request.step_id}",
                session_id=request.session_id,
            ),
            initial_summary="resume",
        )

    def poll(self, handle: RunnerHandle) -> RunnerPollResult:
        return RunnerPollResult(status="running", output_summary="running")

    def cancel(self, handle: RunnerHandle) -> None:
        _ = handle


def test_runtime_start_uses_resume_substrate_when_mode_requests_reuse(
    temp_git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R12 proof: runtime wiring consumes runner resume path for reuse mode."""

    registry = RunnerRegistry()
    runner = _ResumeCapableRunner()
    registry.register("resume-capable", runner)

    base_dir = temp_git_repo / ".vectl" / "workspaces"
    runtime = Runtime(workspace_root=base_dir, _runner_registry=registry)
    base_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.chdir(temp_git_repo)

    request = ExecutionRequest(
        step_id="reuse-runner-substrate",
        role="python-executor",
        runner="resume-capable",
        work_refs=("mode=resume", "isolation=workspace"),
        session_id="sess-123",
    )
    workspace = runtime.prepare(request)
    runtime.start(request=request, workspace=workspace)

    assert runner.resumed == 1
    assert runner.launched == 0


def test_runtime_resume_requires_session_id_with_runner_resume_taxonomy(
    temp_git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R6 failure-path proof: resume wiring surfaces taxonomy fields for missing session."""

    registry = RunnerRegistry()
    runner = _ResumeCapableRunner()
    registry.register("resume-capable", runner)

    base_dir = temp_git_repo / ".vectl" / "workspaces"
    runtime = Runtime(workspace_root=base_dir, _runner_registry=registry)
    base_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.chdir(temp_git_repo)

    request = ExecutionRequest(
        step_id="resume-missing-session",
        role="python-executor",
        runner="resume-capable",
        work_refs=("mode=resume", "isolation=workspace"),
        session_id=None,
    )
    workspace = runtime.prepare(request)

    with pytest.raises(Exception, match="reason=session_missing"):
        runtime.start(request=request, workspace=workspace)


def test_runner_resume_error_exposes_required_taxonomy_fields() -> None:
    """R6 main-path proof: RunnerResumeError includes runner_id and reason taxonomy."""

    err = RunnerResumeError(
        runner_id="resume-capable",
        reason="session_invalid",
        detail="session token expired",
    )
    assert err.runner_id == "resume-capable"
    assert err.reason == "session_invalid"
    assert "detail=session token expired" in str(err)
