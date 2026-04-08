"""
Mechanical execution chores for the orchestration plane.

Authority: docs/ORCHESTRATION-PLANE-ARCHITECTURE.md section 5.3
Authority: docs/ORCHESTRATION-PLANE-INTERFACES.md section 4.3
Authority: docs/ORCHESTRATION-PLANE-ISOLATION-SEMANTICS.md sections 5.4, 5.5
Authority: docs/ORCHESTRATION-PLANE-RUNTIME-WORKTREE-LIFECYCLE.md
"""

from __future__ import annotations

import asyncio
import shutil
import subprocess
import threading
import time
import uuid
from collections.abc import Coroutine
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Generic, Literal, TypeVar, cast

from vectl.models import IsolationMode
from vectl.orchestration.contracts import (
    AgentExecutionState,
    ExecutionRequest,
    ExecutionResult,
    ReconcileResult,
    RuntimeSnapshot,
    WorktreeBinding,
)

if TYPE_CHECKING:
    pass


_T = TypeVar("_T")
_E = TypeVar("_E", bound=Exception)


class WorktreeError(RuntimeError):
    """Mechanical worktree/workspace operation error."""


class ReconcileError(RuntimeError):
    """Error during reconciliation."""


@dataclass(frozen=True)
class Success(Generic[_T]):
    value: _T


@dataclass(frozen=True)
class Failure(Generic[_E]):
    error: _E


Result = Success[_T] | Failure[_E]


async def worktree_create(
    step_id: str, base_dir: Path
) -> Success[WorktreeBinding] | Failure[WorktreeError]:
    """Create or ensure a mechanical workspace path for a step."""

    try:
        worktree_path = base_dir / step_id
        worktree_path.mkdir(parents=True, exist_ok=True)
        workspace_id = f"ws-{step_id}-{uuid.uuid4().hex[:8]}"
        binding = WorktreeBinding(
            workspace_id=workspace_id,
            step_id=step_id,
            worktree_path=str(worktree_path),
        )
        return Success(binding)
    except OSError as exc:
        return Failure(WorktreeError(f"Failed to create workspace for '{step_id}': {exc}"))


async def worktree_cleanup(
    step_id: str,
    worktree_path: Path | str,
    force: bool = True,
) -> Success[None] | Failure[WorktreeError]:
    """Remove a mechanical workspace path for a step."""

    del step_id  # retained for surface compatibility
    del force
    try:
        path = Path(worktree_path) if isinstance(worktree_path, str) else worktree_path
        if path.exists():
            shutil.rmtree(path)
        return Success(None)
    except OSError as exc:
        return Failure(WorktreeError(f"Failed to cleanup workspace '{worktree_path}': {exc}"))


_FRESH_WORKSPACE_HINTS: frozenset[str] = frozenset(
    {
        "isolation=workspace",
        "isolation:workspace",
        "isolation/workspace",
        "isolation.workspace",
        "isolation=independent",
        "isolation:independent",
        "isolation/independent",
        "isolation.independent",
    }
)


# Re-export worktree helpers for close proximity to execution collection.
# These are mechanical helpers that live near worktree lifecycle because
# workspace cleanup is part of execution collection semantics.
__all__ = [
    "AgentExecutionState",
    "Failure",
    "ReconcileError",
    "ReconcileResult",
    "Result",
    "Success",
    "WorktreeBinding",
    "WorktreeError",
    "worktree_cleanup",
    "worktree_create",
]


@dataclass
class _WorkspaceState:
    """Internal mutable tracking for active workspace executions."""

    workspace: str
    binding: WorktreeBinding
    execution_id: str = ""
    execution_state: AgentExecutionState | None = None
    reconcile_state: ReconcileResult | None = None
    reconcile_status: Literal[
        "pending", "active", "merged", "noop", "merge_conflict", "aborted"
    ] = "pending"


@dataclass
class _ExecutionHandle:
    """Internal tracking for active subprocess executions."""

    execution_id: str
    process: subprocess.Popen[str] | None = None
    returncode: int | None = None
    stdout: str = ""
    stderr: str = ""


# Module-level registry of active execution processes.
# Maps execution_id -> _ExecutionHandle
_ACTIVE_PROCESSES: dict[str, _ExecutionHandle] = {}


@dataclass
class Runtime:
    """
    Mechanical execution chores for the orchestration plane.

    Responsibility:
        Worktree/workspace preparation and execution support.

    Owns:
        - runtime lifecycle: workspace/worktree preparation and cleanup
        - runner backend wiring: startup / resume / shutdown helpers
        - mechanical execution handles and collection

    Does Not Own:
        - plan-aware dispatch decisions
        - blocked-state reasoning
        - authority mutation semantics

    Ownership Lock:
        Runtime lifecycle ownership stays separate from runner backend ownership.
        This class may host both surfaces for now, but contractually it must not
        turn backend execution details into lifecycle/policy authority.

    Isolation Freshness (per ORCHESTRATION-PLANE-ISOLATION-SEMANTICS.md):
        - ``workspace``: runtime must provide a fresh isolated workspace/worktree
        - ``independent``: runtime must provide a fresh isolated workspace/worktree

    Authority: docs/ORCHESTRATION-PLANE-INTERFACES.md section 4.3
    Authority: docs/ORCHESTRATION-PLANE-RUNTIME-WORKTREE-LIFECYCLE.md
    """

    workspace_root: Path = field(default_factory=lambda: Path(".vectl/workspaces"))
    _active_workspaces: dict[str, _WorkspaceState] = field(default_factory=dict)
    _active_executions: dict[str, str] = field(default_factory=dict)  # execution_id -> workspace
    _stalled_executions: set[str] = field(default_factory=set)
    _pending_reconciles: set[str] = field(default_factory=set)
    _active_reconciles: set[str] = field(default_factory=set)
    _conflicted_reconciles: set[str] = field(default_factory=set)

    def snapshot(self) -> RuntimeSnapshot:
        """
        Capture current runtime state.

        Returns:
            RuntimeSnapshot representing active mechanical state including
            reconcile lifecycle states.
        """
        return RuntimeSnapshot(
            active_workspaces=tuple(self._active_workspaces.keys()),
            active_executions=tuple(self._active_executions.keys()),
            stalled_executions=tuple(self._stalled_executions),
            pending_reconciles=tuple(self._pending_reconciles),
            active_reconciles=tuple(self._active_reconciles),
            conflicted_reconciles=tuple(self._conflicted_reconciles),
        )

    def prepare(self, request: ExecutionRequest) -> str:
        """
        Prepare workspace for execution.

        For ``isolation=workspace`` and ``isolation=independent``, this guarantees
        a fresh isolated workspace/worktree. The caller is responsible for passing
        the appropriate isolation hint in the request metadata.

        Args:
            request: The execution request.

        Returns:
            Workspace identifier for use in start() and cleanup().

        Raises:
            WorktreeError: If workspace creation fails.
        """
        workspace_id = f"ws-{request.step_id}-{uuid.uuid4().hex[:8]}"

        fresh_workspace_required = _request_requires_fresh_workspace(request)

        if fresh_workspace_required:
            stale_workspaces = [
                workspace_key
                for workspace_key, state in self._active_workspaces.items()
                if state.binding.step_id == request.step_id
            ]
            for stale_workspace in stale_workspaces:
                stale_state = self._active_workspaces.pop(stale_workspace)
                if stale_state.execution_id:
                    self._active_executions.pop(stale_state.execution_id, None)
                    self._stalled_executions.discard(stale_state.execution_id)

            existing_path = self.workspace_root / request.step_id
            if existing_path.exists():
                cleanup_result = _run_cleanup(
                    step_id=request.step_id, worktree_path=str(existing_path)
                )
                if isinstance(cleanup_result, Failure):
                    raise cleanup_result.error

        binding_result = _run_create(step_id=request.step_id, base_dir=self.workspace_root)
        if isinstance(binding_result, Failure):
            raise binding_result.error

        # Update binding with workspace_id
        binding = WorktreeBinding(
            workspace_id=workspace_id,
            step_id=binding_result.value.step_id,
            worktree_path=binding_result.value.worktree_path,
            scratch_branch=binding_result.value.scratch_branch,
            target_ref=binding_result.value.target_ref,
            target_head_at_prepare=binding_result.value.target_head_at_prepare,
            isolation=binding_result.value.isolation,
        )

        self._active_workspaces[workspace_id] = _WorkspaceState(
            workspace=workspace_id,
            binding=binding,
            execution_id="",
        )

        return workspace_id

    def start(self, request: ExecutionRequest, workspace: str) -> str:
        """
        Start execution in prepared workspace.

        Spawns a runner subprocess for the given execution request within the
        prepared workspace. The subprocess runs asynchronously; callers use
        collect() to poll for completion.

        Args:
            request: The execution request.
            workspace: Workspace identifier from prepare().

        Returns:
            Execution identifier for use in collect().

        Raises:
            ValueError: If workspace is not found or not prepared.
            WorktreeError: If subprocess spawning fails.
        """
        if workspace not in self._active_workspaces:
            raise ValueError(f"Workspace not found or not prepared: {workspace}")

        execution_id = f"exec-{request.step_id}-{uuid.uuid4().hex[:8]}"

        state = self._active_workspaces[workspace]
        state.execution_id = execution_id
        self._active_executions[execution_id] = workspace

        # Resolve the runner command from the request.
        runner_cmd = _resolve_runner_command(request.runner)
        worktree_path = Path(state.binding.worktree_path)

        # Build the subprocess arguments.
        # The runner command is invoked with the worktree path as cwd.
        try:
            proc = subprocess.Popen(
                runner_cmd,
                cwd=str(worktree_path),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
        except OSError as exc:
            raise WorktreeError(
                f"Failed to spawn runner '{request.runner}' for step '{request.step_id}': {exc}"
            ) from exc

        # Register the running process.
        handle = _ExecutionHandle(
            execution_id=execution_id,
            process=proc,
            returncode=None,
            stdout="",
            stderr="",
        )
        _ACTIVE_PROCESSES[execution_id] = handle

        # Create initial execution state.
        state.execution_state = AgentExecutionState(
            execution_id=execution_id,
            step_id=request.step_id,
            workspace_id=workspace,
            runner=request.runner,
            runner_handle=execution_id,
            session_id=request.session_id,
            status="running",
            started_at=_current_timestamp(),
            last_update_at=_current_timestamp(),
            artifact_refs=(),
        )

        return execution_id

    def collect(self, execution_id: str) -> ExecutionResult | None:
        """
        Collect results from an execution by polling the runner subprocess.

        This mechanical collection surface polls the active subprocess for
        completion and returns an ExecutionResult with the runner output.
        Returning None indicates the subprocess is still running.

        Args:
            execution_id: The execution identifier.

        Returns:
            ExecutionResult if the subprocess has completed or is unknown,
            else None indicating still running.
        """
        if execution_id not in self._active_executions:
            return ExecutionResult(
                step_id="unknown",
                status="transport_error",
                output_summary=f"Unknown execution_id: {execution_id}",
                operator_message=(
                    "Runtime could not reconcile the execution handle; operator/user "
                    "attention is required before treating it as resolved."
                ),
            )

        workspace_id = self._active_executions[execution_id]
        state = self._active_workspaces.get(workspace_id)

        if state is None or state.execution_state is None:
            return None

        # Look up the running subprocess handle.
        handle = _ACTIVE_PROCESSES.get(execution_id)
        if handle is None:
            # No registered subprocess; still running or not started.
            return None

        proc = handle.process
        if proc is None:
            return None

        # Try non-blocking poll first.
        returncode = proc.poll()

        if returncode is None:
            # Process still running. Try a brief non-blocking wait to capture
            # fast-completing processes that poll might miss due to kernel scheduling.
            try:
                returncode = proc.wait(timeout=0.001)
            except subprocess.TimeoutExpired:
                # Still running.
                return None

        # Subprocess has terminated. Capture output.
        stdout, stderr = "", ""
        try:
            # Drain pipes if not yet drained.
            stdout = proc.stdout.read() if proc.stdout else ""
            stderr = proc.stderr.read() if proc.stderr else ""
        except Exception:
            pass

        handle.returncode = returncode
        handle.stdout = stdout
        handle.stderr = stderr

        # Determine the step_id from execution state.
        step_id = state.execution_state.step_id

        # Map returncode to status.
        if returncode == 0:
            status: Literal["success", "fail", "stall", "transport_error"] = "success"
            output_summary = f"Runner completed successfully (exit 0)"
        elif returncode in (-2, -15, -9):
            # SIGINT (-2), SIGTERM (-15), SIGKILL (-9): terminated signal
            status = "stall"
            output_summary = f"Runner terminated by signal {abs(returncode)}"
        else:
            status = "fail"
            output_summary = f"Runner failed with exit code {returncode}"

        # Build the ExecutionResult.
        result = ExecutionResult(
            step_id=step_id,
            status=status,
            output_summary=output_summary,
            session_id=state.execution_state.session_id,
            operator_message=None,
        )

        # Update the execution state.
        state.execution_state.status = status
        state.execution_state.last_update_at = _current_timestamp()

        # Track stalled executions.
        if status in ("stall", "transport_error"):
            self._stalled_executions.add(execution_id)

        # Remove from active processes registry.
        _ACTIVE_PROCESSES.pop(execution_id, None)

        return result

    def begin_reconcile(self, execution_id: str) -> ReconcileResult | None:
        """
        Begin the reconciliation phase for a completed execution.

        This moves the execution from pending_reconcile to active_reconcile.

        Args:
            execution_id: The execution identifier.

        Returns:
            ReconcileResult if reconciliation completes immediately, else None.

        Raises:
            ValueError: If execution is not found or not ready for reconcile.
        """
        if execution_id not in self._active_executions:
            raise ValueError(f"Execution not found: {execution_id}")

        workspace_id = self._active_executions[execution_id]
        state = self._active_workspaces.get(workspace_id)

        if state is None:
            raise ValueError(f"Workspace not found for execution: {execution_id}")

        if state.execution_state is None:
            raise ValueError(f"No execution state for: {execution_id}")

        if state.execution_state.status not in ("success", "fail"):
            raise ValueError(
                f"Execution {execution_id} not ready for reconcile (status={state.execution_state.status})"
            )

        # Mark as pending reconcile
        self._pending_reconciles.add(workspace_id)

        return None

    def capture_reconcile_result(
        self,
        execution_id: str,
        status: Literal["merged", "noop", "merge_conflict", "aborted"],
        summary: str = "",
        conflict_files: tuple[str, ...] = (),
        artifact_refs: tuple[str, ...] = (),
    ) -> ReconcileResult:
        """
        Capture reconcile result from external integration.

        This is the primary entry point for capturing reconcile outcomes.
        The result determines whether completion is allowed.

        Authority: docs/ORCHESTRATION-PLANE-RUNTIME-WORKTREE-LIFECYCLE.md section 9

        Args:
            execution_id: The execution identifier.
            status: Reconcile status (merged, noop, merge_conflict, aborted).
            summary: Human-readable summary of reconcile outcome.
            conflict_files: Tuple of conflict file paths (if merge_conflict).
            artifact_refs: Tuple of artifact references for reconciliation evidence.

        Returns:
            ReconcileResult capturing the reconcile outcome.

        Raises:
            ValueError: If execution or workspace not found.
        """
        if execution_id not in self._active_executions:
            raise ValueError(f"Execution not found: {execution_id}")

        workspace_id = self._active_executions[execution_id]
        state = self._active_workspaces.get(workspace_id)

        if state is None:
            raise ValueError(f"Workspace not found for execution: {execution_id}")

        result = ReconcileResult(
            execution_id=execution_id,
            workspace_id=workspace_id,
            status=status,
            summary=summary,
            conflict_files=conflict_files,
            artifact_refs=artifact_refs,
        )

        # Update reconcile state
        state.reconcile_state = result
        state.reconcile_status = status

        # Update reconcile tracking sets
        self._pending_reconciles.discard(workspace_id)
        self._active_reconciles.discard(workspace_id)

        if status == "merge_conflict":
            self._conflicted_reconciles.add(workspace_id)
        else:
            self._conflicted_reconciles.discard(workspace_id)

        return result

    def can_complete(self, execution_id: str) -> tuple[bool, str]:
        """
        Check whether execution is allowed to complete.

        Completion is allowed ONLY after reconcile returns 'merged' or 'noop'.
        Execution success alone is NOT sufficient for completion.

        Authority: docs/ORCHESTRATION-PLANE-RUNTIME-WORKTREE-LIFECYCLE.md Rule 4

        Args:
            execution_id: The execution identifier.

        Returns:
            Tuple of (can_complete, reason) where can_complete is True only
            if reconcile returned merged or noop.
        """
        if execution_id not in self._active_executions:
            return (False, f"Execution not found: {execution_id}")

        workspace_id = self._active_executions[execution_id]
        state = self._active_workspaces.get(workspace_id)

        if state is None:
            return (False, f"Workspace not found for execution: {execution_id}")

        if state.reconcile_state is None:
            return (
                False,
                f"Cannot complete: reconcile not yet captured for execution {execution_id}",
            )

        if state.reconcile_status not in ("merged", "noop"):
            return (
                False,
                f"Cannot complete: reconcile status '{state.reconcile_status}' "
                f"does not allow completion (requires merged/noop) for execution {execution_id}",
            )

        return (True, f"Reconcile status '{state.reconcile_status}' allows completion")

    def get_unresolved_message(self, execution_id: str) -> str | None:
        """
        Get operator/user message for unresolved runtime issues.

        If reconcile could not close mechanically (merge_conflict or aborted),
        this returns a message that must be surfaced to the operator/user.

        Authority: docs/ORCHESTRATION-PLANE-RUNTIME-WORKTREE-LIFECYCLE.md Rule 6

        Args:
            execution_id: The execution identifier.

        Returns:
            Operator message if unresolved, else None.
        """
        if execution_id not in self._active_executions:
            return None

        workspace_id = self._active_executions[execution_id]
        state = self._active_workspaces.get(workspace_id)

        if state is None or state.reconcile_state is None:
            return None

        if state.reconcile_status in ("merged", "noop"):
            return None

        if state.reconcile_status == "merge_conflict":
            conflict_count = len(state.reconcile_state.conflict_files)
            conflict_file_list = ", ".join(state.reconcile_state.conflict_files[:5])
            if conflict_count > 5:
                conflict_file_list += f", and {conflict_count - 5} more"

            return (
                f"Runtime could not mechanically resolve merge conflicts for execution {execution_id}. "
                f"Conflicted files: {conflict_file_list}. "
                f"Operator attention required before this can be treated as resolved. "
                f"Reconcile summary: {state.reconcile_state.summary}"
            )

        if state.reconcile_status == "aborted":
            return (
                f"Runtime reconciliation aborted for execution {execution_id}. "
                f"Reason: {state.reconcile_state.summary}. "
                f"Operator attention required before this can be treated as resolved."
            )

        return None

    def cleanup(self, workspace: str, force: bool = False) -> None:
        """
        Clean up a workspace after execution.

        This removes the worktree and branch artifacts per the worktree
        lifecycle contract. It is mechanical cleanup, not policy.

        Cleanup is BLOCKED if:
        - execution is still active (starting/running)
        - reconcile has unresolved status (merge_conflict/aborted)
        - execution reached terminal state but reconcile not captured

        The ``force`` parameter bypasses these safety checks for cases where
        cleanup must proceed (e.g., test scenarios, forced teardown).

        Authority: docs/ORCHESTRATION-PLANE-RUNTIME-WORKTREE-LIFECYCLE.md section 13

        Args:
            workspace: Workspace identifier to clean up.
            force: If True, bypass all safety checks and force cleanup.

        Raises:
            ValueError: If workspace is not found or cleanup is blocked (and not forced).
        """
        if workspace not in self._active_workspaces:
            raise ValueError(f"Workspace not found: {workspace}")

        state = self._active_workspaces[workspace]

        # Block cleanup if execution is still active (unless forced)
        if state.execution_id and state.execution_state is not None:
            if state.execution_state.status in ("starting", "running"):
                if not force:
                    raise ValueError(
                        f"Cannot cleanup workspace {workspace}: execution {state.execution_id} is still active"
                    )

            # If execution reached terminal state, reconcile must be captured
            # This enforces Rule 4: "complete only after reconcile merged/noop"
            if (
                state.execution_state.status in ("success", "fail")
                and state.reconcile_state is None
                and not force
            ):
                raise ValueError(
                    f"Cannot cleanup workspace {workspace}: reconcile not yet captured "
                    f"for terminal execution {state.execution_id}"
                )

        # Block cleanup if reconcile has unresolved status (unless forced)
        if state.reconcile_state is not None and state.reconcile_status in (
            "merge_conflict",
            "aborted",
        ):
            if not force:
                raise ValueError(
                    f"Cannot cleanup workspace {workspace}: unresolved reconcile status '{state.reconcile_status}' "
                    f"requires operator attention"
                )

        # Clear any associated execution tracking
        if state.execution_id:
            self._active_executions.pop(state.execution_id, None)
            self._stalled_executions.discard(state.execution_id)

        # Clear reconcile tracking
        self._pending_reconciles.discard(workspace)
        self._active_reconciles.discard(workspace)
        self._conflicted_reconciles.discard(workspace)

        self._active_workspaces.pop(workspace)

        # Perform mechanical worktree cleanup
        cleanup_result = _run_cleanup(
            step_id=state.binding.step_id,
            worktree_path=state.binding.worktree_path,
            force=True,
        )
        if isinstance(cleanup_result, Failure):
            raise cleanup_result.error


def _current_timestamp() -> float:
    """Return current Unix timestamp."""
    return time.time()


def _resolve_runner_command(runner: str) -> list[str]:
    """
    Resolve the runner command for the given runner identifier.

    The runner name maps to a concrete command. The command is invoked
    as a subprocess with the workspace as its working directory.

    Args:
        runner: Runner identifier (e.g., "claude", "opencode", "test").

    Returns:
        Command as list of string arguments.

    Raises:
        WorktreeError: If the runner is unknown or not available.
    """
    # Map runner names to their commands.
    # This is the mechanical runner backend seam: runner name -> subprocess command.
    # Note: "claude" and "opencode" runners are interactive and require proper
    # environment setup. For subprocess mode, use "test" runner or ensure
    # the runner supports non-interactive execution.
    _RUNNER_COMMANDS: dict[str, list[str]] = {
        "claude": ["claude", "-p", "--output-format", "json"],
        "codex": ["codex"],
        "opencode": ["opencode"],
        "test": ["echo", "runner-test-placeholder"],
    }

    if runner not in _RUNNER_COMMANDS:
        raise WorktreeError(
            f"Unknown runner '{runner}'. Available runners: {list(_RUNNER_COMMANDS.keys())}"
        )

    return _RUNNER_COMMANDS[runner]


def _request_requires_fresh_workspace(request: ExecutionRequest) -> bool:
    """Return True when execution metadata requests strict workspace freshness."""

    normalized_refs = {ref.strip().lower() for ref in request.work_refs}
    return any(ref in _FRESH_WORKSPACE_HINTS for ref in normalized_refs)


def _run_create(step_id: str, base_dir: Path) -> Success[WorktreeBinding] | Failure[WorktreeError]:
    """Run worktree_create from sync and async callers."""

    if _is_event_loop_running():
        return _create_workspace_sync(step_id=step_id, base_dir=base_dir)
    return asyncio.run(worktree_create(step_id=step_id, base_dir=base_dir))


def _run_cleanup(
    step_id: str,
    worktree_path: Path | str,
    force: bool = True,
) -> Success[None] | Failure[WorktreeError]:
    """Run worktree_cleanup from sync and async callers."""

    if _is_event_loop_running():
        return _cleanup_workspace_sync(step_id=step_id, worktree_path=worktree_path, force=force)
    return asyncio.run(worktree_cleanup(step_id=step_id, worktree_path=worktree_path, force=force))


def _is_event_loop_running() -> bool:
    """Return True when called inside a running asyncio event loop."""

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return False
    return True


# Synchronous fallbacks for when already in an async context
# These wrap the async worktree operations without awaiting


def _create_workspace_sync(
    step_id: str, base_dir: Path
) -> Success[WorktreeBinding] | Failure[WorktreeError]:
    """Synchronous wrapper for worktree creation."""
    return _run_async_in_thread(worktree_create(step_id=step_id, base_dir=base_dir))


def _cleanup_workspace_sync(
    step_id: str, worktree_path: Path | str, force: bool
) -> Success[None] | Failure[WorktreeError]:
    """Synchronous wrapper for workspace cleanup."""
    return _run_async_in_thread(
        worktree_cleanup(step_id=step_id, worktree_path=worktree_path, force=force)
    )


def _run_async_in_thread(awaitable: Coroutine[Any, Any, _T]) -> _T:
    """Execute an awaitable on a dedicated thread-owned event loop."""

    outcome: dict[str, object] = {}

    def _runner() -> None:
        try:
            outcome["value"] = asyncio.run(awaitable)
        except BaseException as exc:  # pragma: no cover - propagated immediately
            outcome["error"] = exc

    thread = threading.Thread(target=_runner, daemon=True)
    thread.start()
    thread.join()

    if "error" in outcome:
        raise cast(BaseException, outcome["error"])
    if "value" not in outcome:
        raise RuntimeError("async thread runner completed without result")

    return cast(_T, outcome["value"])
