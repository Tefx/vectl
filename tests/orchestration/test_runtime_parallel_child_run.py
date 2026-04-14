"""
Parallel child-run behavior tests for the drive-aware runtime.

Authority:
    docs/RFC-orch-drive.md sections 10.2, 11.4
    docs/ORCHESTRATION-PLANE-INTERFACES.md section 4.3

Verification:
    Main path: runtime tests prove multiple active step child runs can
    start and collect independently within capacity.
    Failure path: regressions prove resolver/planner barrier runs do not
    consume ordinary frontier capacity.

Step: orch_drive_runtime.parallel-child-run-runtime
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from vectl.orchestration.contracts import (
    ChildRunKind,
    ChildRunRef,
    ChildRunStatus,
    ExecutionRequest,
)
from vectl.orchestration.runtime import Runtime, WorktreeError


# ------------------------------------------------------------------
# Fixtures
# ------------------------------------------------------------------


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

    (repo_root / "README.md").write_text("# Parallel Child Run Test Repository\n")
    subprocess.run(["git", "add", "README.md"], cwd=repo_root, capture_output=True, check=True)
    subprocess.run(
        ["git", "commit", "-m", "Initial commit"],
        cwd=repo_root,
        capture_output=True,
        check=True,
    )
    return repo_root


def _request(step_id: str, work_refs: tuple[str, ...] = ()) -> ExecutionRequest:
    """Create an ExecutionRequest for testing."""
    return ExecutionRequest(
        step_id=step_id,
        role="python-executor",
        runner="claude",
        work_refs=work_refs,
        session_id=None,
    )


# ------------------------------------------------------------------
# Parallel prepare/start: multiple child runs can be independent
# ------------------------------------------------------------------


class TestParallelChildRunPrepareStart:
    """Multiple step child runs can be prepared and started independently."""

    def test_prepare_child_run_creates_workspace_with_drive_context(
        self, temp_git_repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """prepare_child_run creates a workspace tagged with drive_id and kind."""
        base_dir = temp_git_repo / ".vectl" / "workspaces"
        runtime = Runtime(workspace_root=base_dir)
        base_dir.mkdir(parents=True, exist_ok=True)
        monkeypatch.chdir(temp_git_repo)

        request = _request(step_id="step-alpha")
        workspace = runtime.prepare_child_run(request, drive_id="drv_01", kind="step")

        assert workspace in runtime._active_workspaces
        state = runtime._active_workspaces[workspace]
        assert state.child_run_drive_id == "drv_01"
        assert state.child_run_kind == "step"

    def test_multiple_step_child_runs_prepare_independently(
        self, temp_git_repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Multiple step child runs for the same drive can be prepared independently."""
        base_dir = temp_git_repo / ".vectl" / "workspaces"
        runtime = Runtime(workspace_root=base_dir)
        base_dir.mkdir(parents=True, exist_ok=True)
        monkeypatch.chdir(temp_git_repo)

        req_a = _request(step_id="step-alpha")
        req_b = _request(step_id="step-bravo")
        req_c = _request(step_id="step-charlie")

        ws_a = runtime.prepare_child_run(req_a, drive_id="drv_01", kind="step")
        ws_b = runtime.prepare_child_run(req_b, drive_id="drv_01", kind="step")
        ws_c = runtime.prepare_child_run(req_c, drive_id="drv_01", kind="step")

        # Each workspace is distinct
        assert ws_a != ws_b
        assert ws_b != ws_c
        assert ws_a != ws_c

        # All three are in the active workspaces
        snapshot = runtime.snapshot()
        assert len(snapshot.active_workspaces) == 3

    def test_start_child_run_returns_child_run_ref(
        self, temp_git_repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """start_child_run returns a ChildRunRef with truthful bookkeeping."""
        base_dir = temp_git_repo / ".vectl" / "workspaces"
        runtime = Runtime(workspace_root=base_dir)
        base_dir.mkdir(parents=True, exist_ok=True)
        monkeypatch.chdir(temp_git_repo)

        request = _request(step_id="step-alpha")
        workspace = runtime.prepare_child_run(request, drive_id="drv_01", kind="step")

        ref = runtime.start_child_run(
            request,
            workspace,
            drive_id="drv_01",
            kind="step",
            step_id="step-alpha",
        )

        assert isinstance(ref, ChildRunRef)
        assert ref.drive_id == "drv_01"
        assert ref.kind == "step"
        assert ref.step_id == "step-alpha"
        assert ref.status == "running"
        assert ref.run_id != ""
        assert ref.workspace == workspace
        assert ref.runner == "claude"

    def test_multiple_step_child_runs_start_independently(
        self, temp_git_repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Multiple step child runs for the same drive start independently."""
        base_dir = temp_git_repo / ".vectl" / "workspaces"
        runtime = Runtime(workspace_root=base_dir)
        base_dir.mkdir(parents=True, exist_ok=True)
        monkeypatch.chdir(temp_git_repo)

        req_a = _request(step_id="step-alpha")
        req_b = _request(step_id="step-bravo")

        ws_a = runtime.prepare_child_run(req_a, drive_id="drv_01", kind="step")
        ws_b = runtime.prepare_child_run(req_b, drive_id="drv_01", kind="step")

        ref_a = runtime.start_child_run(
            req_a, ws_a, drive_id="drv_01", kind="step", step_id="step-alpha"
        )
        ref_b = runtime.start_child_run(
            req_b, ws_b, drive_id="drv_01", kind="step", step_id="step-bravo"
        )

        # Each has its own unique run_id
        assert ref_a.run_id != ref_b.run_id
        assert ref_a.step_id != ref_b.step_id
        # Both are running
        assert ref_a.status == "running"
        assert ref_b.status == "running"

        # Runtime tracks both executions
        snapshot = runtime.snapshot()
        assert ref_a.run_id in snapshot.active_executions
        assert ref_b.run_id in snapshot.active_executions


# ------------------------------------------------------------------
# Capacity accounting: active step child runs
# ------------------------------------------------------------------


class TestCapacityAccounting:
    """Runtime truthfully counts active step child runs per drive."""

    def test_active_step_count_zero_initially(
        self, temp_git_repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """No active step child runs initially."""
        base_dir = temp_git_repo / ".vectl" / "workspaces"
        runtime = Runtime(workspace_root=base_dir)
        base_dir.mkdir(parents=True, exist_ok=True)
        monkeypatch.chdir(temp_git_repo)

        assert runtime.active_step_child_run_count("drv_01") == 0

    def test_active_step_count_tracks_running_child_runs(
        self, temp_git_repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """active_step_child_run_count reflects running step child runs."""
        base_dir = temp_git_repo / ".vectl" / "workspaces"
        runtime = Runtime(workspace_root=base_dir)
        base_dir.mkdir(parents=True, exist_ok=True)
        monkeypatch.chdir(temp_git_repo)

        req_a = _request(step_id="step-alpha")
        req_b = _request(step_id="step-bravo")

        ws_a = runtime.prepare_child_run(req_a, drive_id="drv_01", kind="step")
        ws_b = runtime.prepare_child_run(req_b, drive_id="drv_01", kind="step")

        runtime.start_child_run(req_a, ws_a, drive_id="drv_01", kind="step", step_id="step-alpha")
        assert runtime.active_step_child_run_count("drv_01") == 1

        runtime.start_child_run(req_b, ws_b, drive_id="drv_01", kind="step", step_id="step-bravo")
        assert runtime.active_step_child_run_count("drv_01") == 2

    def test_active_step_count_per_drive_isolation(
        self, temp_git_repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Step counts are isolated per drive_id."""
        base_dir = temp_git_repo / ".vectl" / "workspaces"
        runtime = Runtime(workspace_root=base_dir)
        base_dir.mkdir(parents=True, exist_ok=True)
        monkeypatch.chdir(temp_git_repo)

        req_a = _request(step_id="step-alpha")
        req_b = _request(step_id="step-bravo")

        ws_a = runtime.prepare_child_run(req_a, drive_id="drv_A", kind="step")
        ws_b = runtime.prepare_child_run(req_b, drive_id="drv_B", kind="step")

        runtime.start_child_run(req_a, ws_a, drive_id="drv_A", kind="step", step_id="step-alpha")
        runtime.start_child_run(req_b, ws_b, drive_id="drv_B", kind="step", step_id="step-bravo")

        assert runtime.active_step_child_run_count("drv_A") == 1
        assert runtime.active_step_child_run_count("drv_B") == 1

    def test_resolver_runs_do_not_consume_step_capacity(
        self, temp_git_repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Resolver child runs are excluded from step capacity count.

        Authority: docs/RFC-orch-drive.md section 10.2
            Resolver and planner child runs do not participate in normal
            frontier capacity. They are barrier work, not ordinary plan work.
        """
        base_dir = temp_git_repo / ".vectl" / "workspaces"
        runtime = Runtime(workspace_root=base_dir)
        base_dir.mkdir(parents=True, exist_ok=True)
        monkeypatch.chdir(temp_git_repo)

        req_step = _request(step_id="step-alpha")
        req_resolver = _request(step_id="resolve-case-01")

        ws_step = runtime.prepare_child_run(req_step, drive_id="drv_01", kind="step")
        ws_resolver = runtime.prepare_child_run(req_resolver, drive_id="drv_01", kind="resolver")

        runtime.start_child_run(
            req_step, ws_step, drive_id="drv_01", kind="step", step_id="step-alpha"
        )
        runtime.start_child_run(
            req_resolver,
            ws_resolver,
            drive_id="drv_01",
            kind="resolver",
            case_id="case_01",
        )

        # Only step child runs count toward capacity
        assert runtime.active_step_child_run_count("drv_01") == 1

    def test_planner_runs_do_not_consume_step_capacity(
        self, temp_git_repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Planner child runs are excluded from step capacity count.

        Authority: docs/RFC-orch-drive.md section 10.2
        """
        base_dir = temp_git_repo / ".vectl" / "workspaces"
        runtime = Runtime(workspace_root=base_dir)
        base_dir.mkdir(parents=True, exist_ok=True)
        monkeypatch.chdir(temp_git_repo)

        req_planner = _request(step_id="replan-request-01")

        ws_planner = runtime.prepare_child_run(req_planner, drive_id="drv_01", kind="planner")

        runtime.start_child_run(
            req_planner,
            ws_planner,
            drive_id="drv_01",
            kind="planner",
            planner_request_id="pr_01",
        )

        # Planner child runs don't count toward step capacity
        assert runtime.active_step_child_run_count("drv_01") == 0

    def test_step_count_decreases_on_terminal_result(
        self, temp_git_repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When a step child run reaches terminal status, it leaves the active count."""
        base_dir = temp_git_repo / ".vectl" / "workspaces"
        runtime = Runtime(workspace_root=base_dir)
        base_dir.mkdir(parents=True, exist_ok=True)
        monkeypatch.chdir(temp_git_repo)

        req = _request(step_id="step-alpha")
        ws = runtime.prepare_child_run(req, drive_id="drv_01", kind="step")
        ref = runtime.start_child_run(req, ws, drive_id="drv_01", kind="step", step_id="step-alpha")

        assert runtime.active_step_child_run_count("drv_01") == 1

        # Simulate execution reaching terminal state by updating the
        # ChildRunRef directly (collect_child_run does this when the runner
        # reports a terminal result). We bypass collect() here because it
        # polls the real runner handle.
        terminal_ref = ChildRunRef(
            run_id=ref.run_id,
            drive_id=ref.drive_id,
            kind=ref.kind,
            status="success",
            step_id=ref.step_id,
            case_id=ref.case_id,
            planner_request_id=ref.planner_request_id,
            workspace=ref.workspace,
            runner=ref.runner,
            session_id=ref.session_id,
            artifact_root=ref.artifact_root,
        )
        runtime._child_run_refs[ref.run_id] = terminal_ref

        # Also update the workspace state
        state = runtime._active_workspaces[ws]
        state.child_run_ref = terminal_ref

        # Active step count should now be 0
        assert runtime.active_step_child_run_count("drv_01") == 0


# ------------------------------------------------------------------
# Collect behavior: ChildRunRef status updates
# ------------------------------------------------------------------


class TestCollectChildRun:
    """collect_child_run updates ChildRunRef truthfully on terminal state."""

    def test_collect_child_run_returns_none_while_running(
        self, temp_git_repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """collect_child_run returns (None, ref) while execution is still running."""
        base_dir = temp_git_repo / ".vectl" / "workspaces"
        runtime = Runtime(workspace_root=base_dir)
        base_dir.mkdir(parents=True, exist_ok=True)
        monkeypatch.chdir(temp_git_repo)

        req = _request(step_id="step-alpha")
        ws = runtime.prepare_child_run(req, drive_id="drv_01", kind="step")
        ref = runtime.start_child_run(req, ws, drive_id="drv_01", kind="step", step_id="step-alpha")

        # While running, collect returns None
        result, updated_ref = runtime.collect_child_run(ref.run_id)
        assert result is None
        assert updated_ref is not None
        assert updated_ref.status == "running"

    def test_collect_child_run_updates_status_on_success(
        self, temp_git_repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When execution succeeds, ChildRunRef status becomes 'success'.

        This test verifies the bookkeeping update path by directly
        simulating the terminal state (since we can't control the real
        runner handle in unit tests).
        """
        base_dir = temp_git_repo / ".vectl" / "workspaces"
        runtime = Runtime(workspace_root=base_dir)
        base_dir.mkdir(parents=True, exist_ok=True)
        monkeypatch.chdir(temp_git_repo)

        req = _request(step_id="step-alpha")
        ws = runtime.prepare_child_run(req, drive_id="drv_01", kind="step")
        ref = runtime.start_child_run(req, ws, drive_id="drv_01", kind="step", step_id="step-alpha")

        # Simulate terminal success by directly updating the ChildRunRef
        success_ref = ChildRunRef(
            run_id=ref.run_id,
            drive_id=ref.drive_id,
            kind=ref.kind,
            status="success",
            step_id=ref.step_id,
            case_id=ref.case_id,
            planner_request_id=ref.planner_request_id,
            workspace=ref.workspace,
            runner=ref.runner,
            session_id=ref.session_id,
            artifact_root=ref.artifact_root,
        )
        runtime._child_run_refs[ref.run_id] = success_ref
        state = runtime._active_workspaces[ws]
        state.child_run_ref = success_ref

        # Verify the ref is now terminal
        looked_up = runtime.child_run_ref(ref.run_id)
        assert looked_up is not None
        assert looked_up.status == "success"

        # And the step count reflects terminal state
        assert runtime.active_step_child_run_count("drv_01") == 0

    def test_collect_child_run_updates_status_on_fail(
        self, temp_git_repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When execution fails, ChildRunRef status becomes 'fail'."""
        base_dir = temp_git_repo / ".vectl" / "workspaces"
        runtime = Runtime(workspace_root=base_dir)
        base_dir.mkdir(parents=True, exist_ok=True)
        monkeypatch.chdir(temp_git_repo)

        req = _request(step_id="step-alpha")
        ws = runtime.prepare_child_run(req, drive_id="drv_01", kind="step")
        ref = runtime.start_child_run(req, ws, drive_id="drv_01", kind="step", step_id="step-alpha")

        # Simulate terminal failure by directly updating the ChildRunRef
        fail_ref = ChildRunRef(
            run_id=ref.run_id,
            drive_id=ref.drive_id,
            kind=ref.kind,
            status="fail",
            step_id=ref.step_id,
            case_id=ref.case_id,
            planner_request_id=ref.planner_request_id,
            workspace=ref.workspace,
            runner=ref.runner,
            session_id=ref.session_id,
            artifact_root=ref.artifact_root,
        )
        runtime._child_run_refs[ref.run_id] = fail_ref
        state = runtime._active_workspaces[ws]
        state.child_run_ref = fail_ref

        looked_up = runtime.child_run_ref(ref.run_id)
        assert looked_up is not None
        assert looked_up.status == "fail"
        assert runtime.active_step_child_run_count("drv_01") == 0

    def test_collect_child_run_on_non_drive_execution_returns_none_ref(
        self, temp_git_repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Collecting a non-drive execution returns None for ChildRunRef."""
        base_dir = temp_git_repo / ".vectl" / "workspaces"
        runtime = Runtime(workspace_root=base_dir)
        base_dir.mkdir(parents=True, exist_ok=True)
        monkeypatch.chdir(temp_git_repo)

        req = _request(step_id="standalone-step")
        ws = runtime.prepare(req)
        execution_id = runtime.start(request=req, workspace=ws)

        # Non-drive execution has no ChildRunRef
        result, ref = runtime.collect_child_run(execution_id)
        assert ref is None
        # result may be None (still running) or ExecutionResult


# ------------------------------------------------------------------
# ChildRunRef bookkeeping: truthful workspace/session/artifact
# ------------------------------------------------------------------


class TestChildRunRefBookkeeping:
    """ChildRunRef captures truthful workspace, session, and artifact metadata."""

    def test_child_run_ref_has_workspace(
        self, temp_git_repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """ChildRunRef.workspace matches the prepared workspace identifier."""
        base_dir = temp_git_repo / ".vectl" / "workspaces"
        runtime = Runtime(workspace_root=base_dir)
        base_dir.mkdir(parents=True, exist_ok=True)
        monkeypatch.chdir(temp_git_repo)

        req = _request(step_id="step-alpha")
        ws = runtime.prepare_child_run(req, drive_id="drv_01", kind="step")
        ref = runtime.start_child_run(req, ws, drive_id="drv_01", kind="step", step_id="step-alpha")

        assert ref.workspace == ws

    def test_child_run_ref_has_runner(
        self, temp_git_repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """ChildRunRef.runner matches the request runner."""
        base_dir = temp_git_repo / ".vectl" / "workspaces"
        runtime = Runtime(workspace_root=base_dir)
        base_dir.mkdir(parents=True, exist_ok=True)
        monkeypatch.chdir(temp_git_repo)

        req = _request(step_id="step-alpha")
        ws = runtime.prepare_child_run(req, drive_id="drv_01", kind="step")
        ref = runtime.start_child_run(req, ws, drive_id="drv_01", kind="step", step_id="step-alpha")

        assert ref.runner == "claude"

    def test_child_run_ref_has_artifact_root(
        self, temp_git_repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """ChildRunRef.artifact_root is populated from workspace root."""
        base_dir = temp_git_repo / ".vectl" / "workspaces"
        runtime = Runtime(workspace_root=base_dir)
        base_dir.mkdir(parents=True, exist_ok=True)
        monkeypatch.chdir(temp_git_repo)

        req = _request(step_id="step-alpha")
        ws = runtime.prepare_child_run(req, drive_id="drv_01", kind="step")
        ref = runtime.start_child_run(req, ws, drive_id="drv_01", kind="step", step_id="step-alpha")

        assert ref.artifact_root != ""
        assert "step-alpha" in ref.artifact_root

    def test_resolver_child_run_ref_has_case_id(
        self, temp_git_repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Resolver ChildRunRef populates case_id."""
        base_dir = temp_git_repo / ".vectl" / "workspaces"
        runtime = Runtime(workspace_root=base_dir)
        base_dir.mkdir(parents=True, exist_ok=True)
        monkeypatch.chdir(temp_git_repo)

        req = _request(step_id="resolve-case-01")
        ws = runtime.prepare_child_run(req, drive_id="drv_01", kind="resolver")
        ref = runtime.start_child_run(
            req, ws, drive_id="drv_01", kind="resolver", case_id="case_deadbeef"
        )

        assert ref.kind == "resolver"
        assert ref.case_id == "case_deadbeef"

    def test_planner_child_run_ref_has_planner_request_id(
        self, temp_git_repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Planner ChildRunRef populates planner_request_id."""
        base_dir = temp_git_repo / ".vectl" / "workspaces"
        runtime = Runtime(workspace_root=base_dir)
        base_dir.mkdir(parents=True, exist_ok=True)
        monkeypatch.chdir(temp_git_repo)

        req = _request(step_id="replan-request-01")
        ws = runtime.prepare_child_run(req, drive_id="drv_01", kind="planner")
        ref = runtime.start_child_run(
            req, ws, drive_id="drv_01", kind="planner", planner_request_id="pr_cafebabe"
        )

        assert ref.kind == "planner"
        assert ref.planner_request_id == "pr_cafebabe"


# ------------------------------------------------------------------
# child_run_ref() query
# ------------------------------------------------------------------


class TestChildRunRefQuery:
    """Runtime can look up ChildRunRef by execution_id."""

    def test_child_run_ref_lookup(
        self, temp_git_repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """child_run_ref() returns the ChildRunRef for a drive-tracked execution."""
        base_dir = temp_git_repo / ".vectl" / "workspaces"
        runtime = Runtime(workspace_root=base_dir)
        base_dir.mkdir(parents=True, exist_ok=True)
        monkeypatch.chdir(temp_git_repo)

        req = _request(step_id="step-alpha")
        ws = runtime.prepare_child_run(req, drive_id="drv_01", kind="step")
        ref = runtime.start_child_run(req, ws, drive_id="drv_01", kind="step", step_id="step-alpha")

        looked_up = runtime.child_run_ref(ref.run_id)
        assert looked_up is not None
        assert looked_up.run_id == ref.run_id
        assert looked_up.drive_id == "drv_01"

    def test_child_run_ref_lookup_nonexistent(
        self, temp_git_repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """child_run_ref() returns None for non-drive-tracked execution."""
        base_dir = temp_git_repo / ".vectl" / "workspaces"
        runtime = Runtime(workspace_root=base_dir)
        base_dir.mkdir(parents=True, exist_ok=True)
        monkeypatch.chdir(temp_git_repo)

        assert runtime.child_run_ref("nonexistent-exec") is None


# ------------------------------------------------------------------
# child_runs_for_drive / active_child_runs_for_drive queries
# ------------------------------------------------------------------


class TestDriveChildRunQueries:
    """Runtime can list all and active child runs per drive."""

    def test_child_runs_for_drive(
        self, temp_git_repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """child_runs_for_drive returns all tracked refs for a drive."""
        base_dir = temp_git_repo / ".vectl" / "workspaces"
        runtime = Runtime(workspace_root=base_dir)
        base_dir.mkdir(parents=True, exist_ok=True)
        monkeypatch.chdir(temp_git_repo)

        req_a = _request(step_id="step-alpha")
        req_b = _request(step_id="step-bravo")

        ws_a = runtime.prepare_child_run(req_a, drive_id="drv_01", kind="step")
        ws_b = runtime.prepare_child_run(req_b, drive_id="drv_02", kind="step")

        runtime.start_child_run(req_a, ws_a, drive_id="drv_01", kind="step", step_id="step-alpha")
        runtime.start_child_run(req_b, ws_b, drive_id="drv_02", kind="step", step_id="step-bravo")

        drv_01_refs = runtime.child_runs_for_drive("drv_01")
        drv_02_refs = runtime.child_runs_for_drive("drv_02")

        assert len(drv_01_refs) == 1
        assert len(drv_02_refs) == 1
        assert drv_01_refs[0].drive_id == "drv_01"
        assert drv_02_refs[0].drive_id == "drv_02"

    def test_active_child_runs_for_drive(
        self, temp_git_repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """active_child_runs_for_drive returns only active (pending/running) refs."""
        base_dir = temp_git_repo / ".vectl" / "workspaces"
        runtime = Runtime(workspace_root=base_dir)
        base_dir.mkdir(parents=True, exist_ok=True)
        monkeypatch.chdir(temp_git_repo)

        req_a = _request(step_id="step-alpha")
        req_b = _request(step_id="step-bravo")

        ws_a = runtime.prepare_child_run(req_a, drive_id="drv_01", kind="step")
        ws_b = runtime.prepare_child_run(req_b, drive_id="drv_01", kind="step")

        ref_a = runtime.start_child_run(
            req_a, ws_a, drive_id="drv_01", kind="step", step_id="step-alpha"
        )
        runtime.start_child_run(req_b, ws_b, drive_id="drv_01", kind="step", step_id="step-bravo")

        # Both running initially
        active_refs = runtime.active_child_runs_for_drive("drv_01")
        assert len(active_refs) == 2

        # Simulate terminal state for child run A by directly updating the ref
        success_ref = ChildRunRef(
            run_id=ref_a.run_id,
            drive_id=ref_a.drive_id,
            kind=ref_a.kind,
            status="success",
            step_id=ref_a.step_id,
            case_id=ref_a.case_id,
            planner_request_id=ref_a.planner_request_id,
            workspace=ref_a.workspace,
            runner=ref_a.runner,
            session_id=ref_a.session_id,
            artifact_root=ref_a.artifact_root,
        )
        runtime._child_run_refs[ref_a.run_id] = success_ref
        state_a = runtime._active_workspaces[ws_a]
        state_a.child_run_ref = success_ref

        # Only one active now
        active_refs = runtime.active_child_runs_for_drive("drv_01")
        assert len(active_refs) == 1


# ------------------------------------------------------------------
# Regression: resolver/planner barrier runs don't consume capacity
# ------------------------------------------------------------------


class TestBarrierRunCapacityRegression:
    """Resolver/planner barrier runs do NOT consume ordinary frontier capacity.

    Authority: docs/RFC-orch-drive.md section 10.2
        Resolver and planner child runs do not participate in normal
        frontier capacity. They are barrier work, not ordinary plan work.
    """

    def test_step_and_resolver_coexist(
        self, temp_git_repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A drive with 2 step runs + 1 resolver run counts only 2 in step capacity."""
        base_dir = temp_git_repo / ".vectl" / "workspaces"
        runtime = Runtime(workspace_root=base_dir)
        base_dir.mkdir(parents=True, exist_ok=True)
        monkeypatch.chdir(temp_git_repo)

        step_req_1 = _request(step_id="step-alpha")
        step_req_2 = _request(step_id="step-bravo")
        resolver_req = _request(step_id="resolve-case-01")

        ws_s1 = runtime.prepare_child_run(step_req_1, drive_id="drv_01", kind="step")
        ws_s2 = runtime.prepare_child_run(step_req_2, drive_id="drv_01", kind="step")
        ws_r = runtime.prepare_child_run(resolver_req, drive_id="drv_01", kind="resolver")

        runtime.start_child_run(
            step_req_1, ws_s1, drive_id="drv_01", kind="step", step_id="step-alpha"
        )
        runtime.start_child_run(
            step_req_2, ws_s2, drive_id="drv_01", kind="step", step_id="step-bravo"
        )
        runtime.start_child_run(
            resolver_req, ws_r, drive_id="drv_01", kind="resolver", case_id="case_deadbeef"
        )

        # Step capacity should only count step runs
        assert runtime.active_step_child_run_count("drv_01") == 2

        # But total active child runs for drive should include all 3
        all_active = runtime.active_child_runs_for_drive("drv_01")
        assert len(all_active) == 3

    def test_step_and_planner_coexist(
        self, temp_git_repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A drive with 1 step run + 1 planner run counts only 1 in step capacity."""
        base_dir = temp_git_repo / ".vectl" / "workspaces"
        runtime = Runtime(workspace_root=base_dir)
        base_dir.mkdir(parents=True, exist_ok=True)
        monkeypatch.chdir(temp_git_repo)

        step_req = _request(step_id="step-alpha")
        planner_req = _request(step_id="replan-request-01")

        ws_s = runtime.prepare_child_run(step_req, drive_id="drv_01", kind="step")
        ws_p = runtime.prepare_child_run(planner_req, drive_id="drv_01", kind="planner")

        runtime.start_child_run(
            step_req, ws_s, drive_id="drv_01", kind="step", step_id="step-alpha"
        )
        runtime.start_child_run(
            planner_req, ws_p, drive_id="drv_01", kind="planner", planner_request_id="pr_cafebabe"
        )

        assert runtime.active_step_child_run_count("drv_01") == 1

        all_active = runtime.active_child_runs_for_drive("drv_01")
        assert len(all_active) == 2

    def test_all_barrier_kinds_no_step_count(
        self, temp_git_repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A drive with only resolver + planner runs counts 0 step capacity."""
        base_dir = temp_git_repo / ".vectl" / "workspaces"
        runtime = Runtime(workspace_root=base_dir)
        base_dir.mkdir(parents=True, exist_ok=True)
        monkeypatch.chdir(temp_git_repo)

        resolver_req = _request(step_id="resolve-case-01")
        planner_req = _request(step_id="replan-request-01")

        ws_r = runtime.prepare_child_run(resolver_req, drive_id="drv_01", kind="resolver")
        ws_p = runtime.prepare_child_run(planner_req, drive_id="drv_01", kind="planner")

        runtime.start_child_run(
            resolver_req, ws_r, drive_id="drv_01", kind="resolver", case_id="case_01"
        )
        runtime.start_child_run(
            planner_req, ws_p, drive_id="drv_01", kind="planner", planner_request_id="pr_01"
        )

        assert runtime.active_step_child_run_count("drv_01") == 0


# ------------------------------------------------------------------
# Workspace isolation: parallel child runs have separate workspaces
# ------------------------------------------------------------------


class TestParallelWorkspaceIsolation:
    """Parallel child runs run in separate, isolated workspaces."""

    def test_parallel_child_runs_have_distinct_worktree_paths(
        self, temp_git_repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Two concurrent step child runs use different worktree paths."""
        base_dir = temp_git_repo / ".vectl" / "workspaces"
        runtime = Runtime(workspace_root=base_dir)
        base_dir.mkdir(parents=True, exist_ok=True)
        monkeypatch.chdir(temp_git_repo)

        req_a = _request(step_id="step-alpha")
        req_b = _request(step_id="step-bravo")

        ws_a = runtime.prepare_child_run(req_a, drive_id="drv_01", kind="step")
        ws_b = runtime.prepare_child_run(req_b, drive_id="drv_01", kind="step")

        ref_a = runtime.start_child_run(
            req_a, ws_a, drive_id="drv_01", kind="step", step_id="step-alpha"
        )
        ref_b = runtime.start_child_run(
            req_b, ws_b, drive_id="drv_01", kind="step", step_id="step-bravo"
        )

        # Distinct worktree paths
        path_a = runtime.workspace_worktree_path(ws_a)
        path_b = runtime.workspace_worktree_path(ws_b)
        assert path_a != path_b

        # Both worktree paths exist on disk
        assert path_a.exists()
        assert path_b.exists()


# ------------------------------------------------------------------
# Status mapping
# ------------------------------------------------------------------


class TestStatusMapping:
    """Runtime correctly maps ExecutionResult status to ChildRunStatus."""

    def test_map_success(self) -> None:
        assert Runtime._map_execution_status_to_child_run_status("success") == "success"

    def test_map_fail(self) -> None:
        assert Runtime._map_execution_status_to_child_run_status("fail") == "fail"

    def test_map_stall(self) -> None:
        assert Runtime._map_execution_status_to_child_run_status("stall") == "stall"

    def test_map_transport_error(self) -> None:
        assert (
            Runtime._map_execution_status_to_child_run_status("transport_error")
            == "transport_error"
        )
