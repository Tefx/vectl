"""Runtime behavior tests for orchestration runtime.

Authority:
    docs/ORCHESTRATION-PLANE-INTERFACES.md section 4.3
    docs/ORCHESTRATION-PLANE-ARCHITECTURE.md section 5.3
    docs/ORCHESTRATION-PLANE-ISOLATION-SEMANTICS.md sections 3.2, 3.3, 5.4
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from vectl.orchestration.contracts import ExecutionRequest
from vectl.orchestration.runtime import Runtime, WorktreeError


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
        runner="claude",
        work_refs=work_refs,
        session_id=None,
    )


def test_prepare_start_collect_cleanup_lifecycle(
    temp_git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    base_dir = temp_git_repo / ".vectl" / "workspaces"
    runtime = Runtime(workspace_root=base_dir)
    base_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.chdir(temp_git_repo)

    request = _request(step_id="runtime-lifecycle")
    workspace = runtime.prepare(request)

    prepared_snapshot = runtime.snapshot()
    assert workspace in prepared_snapshot.active_workspaces
    assert prepared_snapshot.active_executions == ()

    execution_id = runtime.start(request=request, workspace=workspace)
    running_snapshot = runtime.snapshot()
    assert execution_id in running_snapshot.active_executions
    assert runtime.collect(execution_id) is None

    # Mark execution as complete before cleanup
    state = runtime._active_workspaces[workspace]
    assert state.execution_state is not None
    state.execution_state.status = "success"

    # Capture reconcile result before cleanup
    runtime.begin_reconcile(execution_id)
    runtime.capture_reconcile_result(
        execution_id=execution_id,
        status="noop",
        summary="No changes to merge",
    )

    runtime.cleanup(workspace)
    final_snapshot = runtime.snapshot()
    assert final_snapshot.active_workspaces == ()
    assert final_snapshot.active_executions == ()
    assert final_snapshot.stalled_executions == ()
    assert not (base_dir / request.step_id).exists()


@pytest.mark.parametrize("hint", ["isolation=workspace", "isolation=independent"])
def test_prepare_fresh_isolation_recreates_workspace(
    temp_git_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
    hint: str,
) -> None:
    base_dir = temp_git_repo / ".vectl" / "workspaces"
    runtime = Runtime(workspace_root=base_dir)
    base_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.chdir(temp_git_repo)

    step_id = f"runtime-fresh-{hint.split('=')[-1]}"
    request = _request(step_id=step_id, work_refs=(hint,))

    first_workspace = runtime.prepare(request)
    first_path = base_dir / step_id
    marker = first_path / "stale.txt"
    marker.write_text("stale workspace state\n")

    second_workspace = runtime.prepare(request)

    assert second_workspace != first_workspace
    assert first_path.exists()
    assert not marker.exists()
    snapshot = runtime.snapshot()
    assert snapshot.active_workspaces == (second_workspace,)

    runtime.cleanup(second_workspace)


def test_prepare_rejects_planner_facade_execution_in_linked_worktree_runtime(
    temp_git_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    base_dir = temp_git_repo / ".vectl" / "workspaces"
    runtime = Runtime(workspace_root=base_dir)
    base_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.chdir(temp_git_repo)

    request = ExecutionRequest(
        step_id="case-planner-1",
        role="vectl-planner",
        runner="codex",
        work_refs=(
            "execution_context=main_worktree",
            "mutation_policy=vectl_facade_only",
            "output_contract=vectl_facade_mutation",
            "source_kind=resolution_subtask",
        ),
        session_id=None,
    )

    with pytest.raises(WorktreeError, match="vectl_facade_mutation"):
        runtime.prepare(request)

    assert runtime.snapshot().active_workspaces == ()


@pytest.mark.anyio
async def test_prepare_and_cleanup_work_inside_running_event_loop(
    temp_git_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    base_dir = temp_git_repo / ".vectl" / "workspaces"
    runtime = Runtime(workspace_root=base_dir)
    base_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.chdir(temp_git_repo)

    request = _request(step_id="runtime-async-context", work_refs=("isolation=workspace",))
    workspace = runtime.prepare(request)

    assert (base_dir / request.step_id).exists()
    runtime.cleanup(workspace)
    assert not (base_dir / request.step_id).exists()
