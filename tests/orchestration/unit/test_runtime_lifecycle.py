"""Runtime lifecycle tests for reconcile and completion blocking.

Authority:
    docs/ORCHESTRATION-PLANE-RUNTIME-WORKTREE-LIFECYCLE.md
    docs/ORCHESTRATION-PLANE-INTERFACES.md section 4.3
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from vectl.orchestration.contracts import ExecutionRequest
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
    """Test that capture_reconcile_result correctly updates reconcile tracking sets."""
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

    # Begin reconcile - should add to pending
    runtime.begin_reconcile(execution_id)
    assert workspace in runtime.snapshot().pending_reconciles

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
