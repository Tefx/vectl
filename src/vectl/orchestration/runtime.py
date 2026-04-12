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
    ReconcileDisposition,
    ReconcileResult,
    RuntimeSnapshot,
    WorktreeBinding,
)
from vectl.orchestration.runner_registry import RunnerRegistry, build_default_runner_registry
from vectl.orchestration.runners import (
    RunnerCancelError,
    RunnerHandle,
    RunnerLaunchError,
    RunnerNotFoundError,
    RunnerPollError,
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
    """Create an isolated execution workspace using standard git worktree.

    Per ORCHESTRATION-PLANE-RUNTIME-WORKTREE-LIFECYCLE.md section 5 Rule 1:
    Runtime must use standard git worktree flows for isolated execution.
    It must not substitute ad hoc directory creation for real worktree isolation.

    Per ORCHESTRATION-PLANE-RUNTIME-WORKTREE-LIFECYCLE.md section 6.1:
    Prepare creates the isolated execution context via:
    1. resolve target branch/ref
    2. record target head commit at prepare time
    3. create a scratch branch for the step
    4. create a linked worktree using standard git worktree commands
    5. persist worktree binding metadata

    Args:
        step_id: Step identifier for this workspace.
        base_dir: Base directory for workspace naming (workspace_root/step_id).

    Returns:
        Success with WorktreeBinding if worktree was created.
        Failure with WorktreeError if creation failed.
    """
    try:
        worktree_path = base_dir / step_id
        scratch_branch = f"vectl/scratch/{step_id}-{uuid.uuid4().hex[:8]}"

        # Step 1: Resolve target branch/ref from the repo root first.
        # The repo root is the parent of base_dir (the .vectl/workspaces dir)
        # or base_dir itself if it is the repo root.
        repo_root_result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            cwd=str(base_dir) if base_dir.exists() else None,
        )
        if repo_root_result.returncode != 0:
            repo_root_result = subprocess.run(
                ["git", "rev-parse", "--show-toplevel"],
                capture_output=True,
                text=True,
                cwd=str(Path.cwd()),
            )
        if repo_root_result.returncode != 0:
            detail = (
                repo_root_result.stderr.strip()
                or repo_root_result.stdout.strip()
                or "unknown git error"
            )
            raise WorktreeError(f"Failed to resolve git repository root for '{step_id}': {detail}")
        repo_root = repo_root_result.stdout.strip()

        # Prune stale worktree metadata before creating new worktree.
        _ = subprocess.run(
            ["git", "worktree", "prune"],
            capture_output=True,
            text=True,
            cwd=repo_root,
        )

        # Step 2: Record target HEAD at prepare time for reconcile tracking.
        head_result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            cwd=repo_root,
        )
        target_head_at_prepare = head_result.stdout.strip() if head_result.returncode == 0 else ""

        # Determine target ref (main branch or current branch).
        target_ref_result = subprocess.run(
            ["git", "symbolic-ref", "--quiet", "--short", "HEAD"],
            capture_output=True,
            text=True,
            cwd=repo_root,
        )
        target_ref = (
            target_ref_result.stdout.strip() if target_ref_result.returncode == 0 else "HEAD"
        )
        target_spec = target_ref if target_ref != "HEAD" else (target_head_at_prepare or "HEAD")

        # Step 3 & 4: Create a linked worktree using standard git worktree.
        # git worktree add creates the worktree directory; no mkdir needed.
        result = subprocess.run(
            ["git", "worktree", "add", "-b", scratch_branch, str(worktree_path), target_spec],
            capture_output=True,
            text=True,
            cwd=repo_root,
        )

        if result.returncode != 0:
            detail = result.stderr.strip() or result.stdout.strip() or "unknown git error"
            raise WorktreeError(f"git worktree add failed for '{step_id}': {detail}")

        # Step 5: Persist worktree binding metadata.
        workspace_id = f"ws-{step_id}-{uuid.uuid4().hex[:8]}"

        binding = WorktreeBinding(
            workspace_id=workspace_id,
            step_id=step_id,
            worktree_path=str(worktree_path),
            scratch_branch=scratch_branch,
            target_ref=target_ref,
            target_head_at_prepare=target_head_at_prepare,
        )
        return Success(binding)

    except WorktreeError as exc:
        return Failure(exc)
    except Exception as exc:
        return Failure(WorktreeError(f"Failed to create worktree for '{step_id}': {exc}"))


async def worktree_cleanup(
    step_id: str,
    worktree_path: Path | str,
    force: bool = True,
) -> Success[None] | Failure[WorktreeError]:
    """Remove a git worktree workspace for a step.

    Per ORCHESTRATION-PLANE-RUNTIME-WORKTREE-LIFECYCLE.md:
    Cleanup removes the linked worktree using 'git worktree remove'.

    Args:
        step_id: Step identifier (retained for surface compatibility).
        worktree_path: Path to the worktree directory.
        force: If True, force removal even with uncommitted changes.

    Returns:
        Success(None) if cleanup succeeded.
        Failure with WorktreeError if cleanup failed.
    """
    del step_id  # retained for surface compatibility
    try:
        path = Path(worktree_path) if isinstance(worktree_path, str) else worktree_path

        if path.exists():
            repo_root_result = subprocess.run(
                ["git", "rev-parse", "--show-toplevel"],
                capture_output=True,
                text=True,
                cwd=str(path),
            )
            if repo_root_result.returncode != 0:
                detail = (
                    repo_root_result.stderr.strip()
                    or repo_root_result.stdout.strip()
                    or "unknown git error"
                )
                raise WorktreeError(f"Failed to resolve git repository root for cleanup: {detail}")

            cmd = ["git", "worktree", "remove"]
            if force:
                cmd.append("--force")
            cmd.append(str(path))

            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                cwd=repo_root_result.stdout.strip(),
            )

            if result.returncode != 0:
                detail = result.stderr.strip() or result.stdout.strip() or "unknown git error"
                raise WorktreeError(f"git worktree remove failed for '{path}': {detail}")

        return Success(None)
    except WorktreeError as exc:
        return Failure(exc)
    except Exception as exc:
        return Failure(WorktreeError(f"Failed to cleanup worktree '{worktree_path}': {exc}"))


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

_RESUME_MODE_HINTS: frozenset[str] = frozenset({"mode=resume", "mode=recover"})
_PROTECTED_RECONCILE_PATHS: frozenset[str] = frozenset({"plan.yaml"})


# Re-export worktree helpers for close proximity to execution collection.
# These are mechanical helpers that live near worktree lifecycle because
# workspace cleanup is part of execution collection semantics.
__all__ = [
    "AgentExecutionState",
    "Failure",
    "ReconcileDisposition",
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
    """Internal mutable tracking for active workspace executions.

    Contract note:
        This in-memory shape is intentionally smaller than the durable
        ``RuntimeRecoveryRecord`` contract in ``continuity_artifacts.py``.
        Restart-safe recovery must preserve enough metadata to reconstruct this
        state plus paused-routing and reconcile-evidence barriers before normal
        dispatch or completion is permitted again.
    """

    workspace: str
    binding: WorktreeBinding
    execution_id: str = ""
    execution_state: AgentExecutionState | None = None
    reconcile_state: ReconcileResult | None = None
    reconcile_status: Literal[
        "pending", "active", "merged", "noop", "merge_conflict", "aborted"
    ] = "pending"


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
    _runner_registry: RunnerRegistry = field(default_factory=build_default_runner_registry)
    _runner_handles: dict[str, RunnerHandle] = field(default_factory=dict)
    _reconcile_lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    # Reconcile serialization lock per target ref.
    # Per ORCHESTRATION-PLANE-RUNTIME-WORKTREE-LIFECYCLE.md section 9.3:
    # "Concurrent reconcile into the same target branch is not [acceptable].
    #  Runtime must therefore own an integration/reconcile lock so that
    #  merge-back to a single target branch occurs serially."
    _reconcile_lock_by_target: dict[str, str] = field(
        default_factory=dict
    )  # target_ref -> workspace_id

    def snapshot(self) -> RuntimeSnapshot:
        """
        Capture current runtime state.

        Returns:
            RuntimeSnapshot representing active mechanical state including
            reconcile lifecycle states.

        Contract note:
            ``RuntimeSnapshot`` is loop-time observability only. It is not a
            sufficient restart authority for operator/conflict recovery; durable
            recovery must preserve worktree, execution, reconcile, and paused
            routing metadata separately.
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
        planner_output_contract = _request_output_contract(request)
        if planner_output_contract == "vectl_facade_mutation":
            raise WorktreeError(
                "Runtime worktree launch blocked: vectl_facade_mutation requests must stay on "
                "the approved vectl facade because linked-worktree execution could directly edit "
                "plan.yaml outside the authorized mutation boundary"
            )

        workspace_id = f"ws-{request.step_id}-{uuid.uuid4().hex[:8]}"

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
            path_is_worktree = _is_registered_worktree(existing_path)
            if path_is_worktree:
                cleanup_result = _run_cleanup(
                    step_id=request.step_id, worktree_path=str(existing_path)
                )
                if isinstance(cleanup_result, Failure):
                    detail = str(cleanup_result.error)
                    if "not a working tree" in detail and existing_path.exists():
                        if existing_path.is_dir():
                            shutil.rmtree(existing_path)
                        else:
                            existing_path.unlink()
                    else:
                        raise cleanup_result.error
            elif existing_path.is_dir():
                shutil.rmtree(existing_path)
            else:
                existing_path.unlink()

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
            isolation=_request_isolation_mode(request),
        )

        self._active_workspaces[workspace_id] = _WorkspaceState(
            workspace=workspace_id,
            binding=binding,
            execution_id="",
        )

        return workspace_id

    def workspace_worktree_path(self, workspace: str) -> Path:
        """Return the absolute worktree path for a prepared workspace.

        The caller must have called ``prepare()`` to create the workspace
        before calling this method.

        Args:
            workspace: Workspace identifier returned by ``prepare()``.

        Returns:
            Absolute path to the worktree directory for the workspace.

        Raises:
            ValueError: If the workspace identifier is not found among
                active workspaces.
        """
        if workspace not in self._active_workspaces:
            raise ValueError(f"Workspace not found or not prepared: {workspace}")
        return Path(self._active_workspaces[workspace].binding.worktree_path)

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

        worktree_path = Path(state.binding.worktree_path)
        try:
            runner = self._runner_registry.get(request.runner)
        except RunnerNotFoundError as exc:
            raise WorktreeError(str(exc)) from exc

        resume_requested = _request_prefers_resume(request)
        try:
            if resume_requested:
                capabilities = runner.capabilities()
                if capabilities.supports_resume:
                    if request.session_id in (None, ""):
                        raise WorktreeError(
                            "Failed to resume runner "
                            f"'{request.runner}' for step '{request.step_id}': "
                            "runner taxonomy=RunnerResumeError "
                            f"runner={request.runner} reason=session_missing detail=no session_id"
                        )
                    try:
                        launch_result = runner.resume(request=request, workspace=worktree_path)
                    except Exception as exc:
                        reason = str(getattr(exc, "reason", "internal"))
                        if reason == "resume_unsupported":
                            launch_result = runner.launch(request=request, workspace=worktree_path)
                        else:
                            raise WorktreeError(
                                "Failed to resume runner "
                                f"'{request.runner}' for step '{request.step_id}': {exc}"
                            ) from exc
                else:
                    launch_result = runner.launch(request=request, workspace=worktree_path)
            else:
                launch_result = runner.launch(request=request, workspace=worktree_path)
        except RunnerLaunchError as exc:
            raise WorktreeError(
                f"Failed to launch runner '{request.runner}' for step '{request.step_id}': {exc}"
            ) from exc
        self._runner_handles[execution_id] = launch_result.handle

        # Create initial execution state.
        # Authority: RFC-opencode-orchestration-runner.md section 7.1
        # Expanded fields (request_mode, session_policy) are persisted for
        # recovery continuity and session-aware state reconstruction.
        state.execution_state = AgentExecutionState(
            execution_id=execution_id,
            step_id=request.step_id,
            workspace_id=workspace,
            runner=request.runner,
            runner_handle=launch_result.handle.run_id,
            session_id=launch_result.handle.session_id or request.session_id,
            status="running",
            started_at=_current_timestamp(),
            last_update_at=_current_timestamp(),
            artifact_refs=(),
            evidence_refs=(),
            request_mode=request.request_mode,
            session_policy=request.session_policy,
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

        handle = self._runner_handles.get(execution_id)
        if handle is None:
            return ExecutionResult(
                step_id=state.execution_state.step_id,
                status="transport_error",
                output_summary=f"Missing runner handle for execution_id: {execution_id}",
                operator_message=(
                    "Runtime lost the runner handle during collection; operator/user "
                    "attention is required before treating this execution as resolved."
                ),
            )

        try:
            runner = self._runner_registry.get(state.execution_state.runner)
        except RunnerNotFoundError as exc:
            state.execution_state.status = "transport_error"
            state.execution_state.last_update_at = _current_timestamp()
            self._stalled_executions.add(execution_id)
            self._runner_handles.pop(execution_id, None)
            return ExecutionResult(
                step_id=state.execution_state.step_id,
                status="transport_error",
                output_summary=str(exc),
                operator_message=(
                    "Runtime requested an unknown runner backend; operator/user "
                    "attention is required before treating this execution as resolved."
                ),
            )
        try:
            poll_result = runner.poll(handle)
        except RunnerPollError as exc:
            state.execution_state.status = "transport_error"
            state.execution_state.last_update_at = _current_timestamp()
            self._stalled_executions.add(execution_id)
            self._runner_handles.pop(execution_id, None)
            return ExecutionResult(
                step_id=state.execution_state.step_id,
                status="transport_error",
                output_summary=f"Runner poll failed: {exc}",
                operator_message=(
                    "Runtime could not poll the runner backend; operator/user "
                    "attention is required before treating this execution as resolved."
                ),
            )

        if poll_result.status == "running":
            return None

        # Determine the step_id from execution state.
        step_id = state.execution_state.step_id

        status = poll_result.status
        output_summary = poll_result.output_summary

        # Build the ExecutionResult.
        result = ExecutionResult(
            step_id=step_id,
            status=status,
            output_summary=output_summary,
            session_id=poll_result.session_id,
            operator_message=None,
        )

        # Update the execution state.
        # Authority: RFC-opencode-orchestration-runner.md section 7.3
        # Propagate session_id and evidence_refs from poll result into
        # durable execution state so recovery can reconstruct session truth.
        state.execution_state.status = status
        state.execution_state.last_update_at = _current_timestamp()
        if poll_result.session_id is not None:
            state.execution_state.session_id = poll_result.session_id
        state.execution_state.evidence_refs = poll_result.evidence_refs

        # Track stalled executions.
        if status in ("stall", "transport_error"):
            self._stalled_executions.add(execution_id)

        self._runner_handles.pop(execution_id, None)

        return result

    def terminate_execution(self, execution_id: str) -> bool:
        """Attempt to terminate a running execution by cancelling the runner handle.

        Authority:
            docs/ORCHESTRATION-PLANE-CLI-CONFIG-OBSERVABILITY-DESIGN.md §7.4
                'Persist a control.stop request… terminal state change is asynchronous'

        This method sends a terminate signal to the runner subprocess associated
        with the given execution_id. It is intended to be called when a
        ``control.stop`` request is consumed from the control channel during the
        dispatch collect loop.

        Args:
            execution_id: The execution identifier for the running process.

        Returns:
            True if termination was attempted and the runner handle was found,
            False if the execution_id was unknown or no handle existed.
        """
        if execution_id not in self._active_executions:
            return False

        workspace_id = self._active_executions.get(execution_id)
        if workspace_id is None:
            return False

        state = self._active_workspaces.get(workspace_id)
        if state is None or state.execution_state is None:
            return False

        handle = self._runner_handles.get(execution_id)
        if handle is None:
            return False

        runner = self._runner_registry.get(state.execution_state.runner)
        try:
            runner.cancel(handle)
        except RunnerCancelError:
            pass  # Best-effort: the process may already be dead

        # Update execution state to reflect stop requested.
        state.execution_state = AgentExecutionState(
            execution_id=execution_id,
            step_id=state.execution_state.step_id,
            workspace_id=workspace_id,
            runner=state.execution_state.runner,
            runner_handle=state.execution_state.runner_handle,
            session_id=state.execution_state.session_id,
            status="stall",
            started_at=state.execution_state.started_at,
            last_update_at=_current_timestamp(),
            artifact_refs=state.execution_state.artifact_refs,
            evidence_refs=state.execution_state.evidence_refs,
            request_mode=state.execution_state.request_mode,
            session_policy=state.execution_state.session_policy,
        )
        self._stalled_executions.add(execution_id)

        return True

    def begin_reconcile(self, execution_id: str) -> ReconcileResult | None:
        """
        Begin the reconciliation phase for a completed execution.

        Per ORCHESTRATION-PLANE-RUNTIME-WORKTREE-LIFECYCLE.md section 9.3:
        Concurrent reconcile into the same target branch is not acceptable.
        This method enforces serialization by acquiring a per-target-ref lock
        before transitioning to active reconcile.

        If another reconcile is already active for the same target ref,
        this raises ReconcileError to prevent unsafe concurrent integration.

        Args:
            execution_id: The execution identifier.

        Returns:
            ReconcileResult if reconciliation completes immediately, else None.

        State-machine note:
            ``pending_reconciles`` and ``active_reconciles`` are transient,
            in-flight observability sets. A synchronous call that runs to
            completion will usually clear both before callers can snapshot the
            runtime again. Only unresolved ``merge_conflict`` outcomes persist
            in ``conflicted_reconciles`` until an explicit later result clears
            them.

        Raises:
            ValueError: If execution is not found or not ready for reconcile.
            ReconcileError: If a concurrent reconcile is already active for the
                same target ref (serialization enforcement).
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
                "Execution "
                f"{execution_id} not ready for reconcile "
                f"(status={state.execution_state.status})"
            )

        if state.reconcile_state is not None and state.reconcile_status in (
            "merged",
            "noop",
            "merge_conflict",
            "aborted",
        ):
            return state.reconcile_state

        target_ref = state.binding.target_ref or "HEAD"
        existing_holder = self._reconcile_lock_by_target.get(target_ref)
        if existing_holder is not None and existing_holder != workspace_id:
            raise ReconcileError(
                f"Concurrent reconcile blocked: target ref '{target_ref}' is already "
                f"being reconciled by workspace '{existing_holder}'. "
                f"Workspace '{workspace_id}' must wait for serialization."
            )

        # Acquire the reconcile serialization lock for this target ref.
        self._reconcile_lock_by_target[target_ref] = workspace_id

        # Mark as pending reconcile

        self._pending_reconciles.add(workspace_id)
        if not self._reconcile_lock.acquire(blocking=False):
            self._pending_reconciles.discard(workspace_id)
            raise ReconcileError(
                f"Reconcile lock busy for execution '{execution_id}' (serialization enforced)"
            )

        self._active_reconciles.add(workspace_id)
        try:
            raw_result = _perform_reconcile(
                execution_id=execution_id,
                workspace_id=workspace_id,
                binding=state.binding,
            )
            reconcile_result = self.capture_reconcile_result(
                execution_id=execution_id,
                status=raw_result.status,
                summary=raw_result.summary,
                conflict_files=raw_result.conflict_files,
                artifact_refs=raw_result.artifact_refs,
            )
        except ReconcileError as exc:
            reconcile_result = self.capture_reconcile_result(
                execution_id=execution_id,
                status="aborted",
                summary=str(exc),
            )
        finally:
            self._reconcile_lock.release()
            self._active_reconciles.discard(workspace_id)

        return reconcile_result

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
                For ``merge_conflict`` and ``aborted`` this must preserve enough
                evidence for resolver/operator handling after restart. Protected-
                path handling must be explicit in these refs rather than silent.

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

        # Release the reconcile serialization lock for this target ref.
        # Per ORCHESTRATION-PLANE-RUNTIME-WORKTREE-LIFECYCLE.md section 9.3,
        # concurrent reconcile into the same target branch is not acceptable.
        # Releasing the lock after reconcile completion allows the next queued
        # reconcile to proceed.
        target_ref = state.binding.target_ref or "HEAD"
        if self._reconcile_lock_by_target.get(target_ref) == workspace_id:
            del self._reconcile_lock_by_target[target_ref]

        return result

    def can_complete(self, execution_id: str) -> tuple[bool, str]:
        """
        Check whether execution is allowed to complete.

        Completion is allowed ONLY after reconcile returns 'merged' or 'noop'.
        Execution success alone is NOT sufficient for completion.

        Contract note:
            Recovery re-entry must re-establish the same barrier from durable
            metadata so duplicate complete cannot occur merely because an
            execution had already reached terminal success before crash/restart.

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
                "Runtime could not mechanically resolve merge conflicts for execution "
                f"{execution_id}. "
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

    def reconcile_disposition(self, execution_id: str) -> ReconcileDisposition | None:
        """
        Retrieve the reconcile disposition proof for a completed reconcile.

        Per ORCHESTRATION-PLANE-RUNTIME-WORKTREE-LIFECYCLE.md Rule 4,
        completion requires that reconcile returned 'merged' or 'noop'.
        This method surfaces the reconcile disposition so that the core_adapter
        can preserve it through the completion evidence flow.

        Authority: docs/ORCHESTRATION-PLANE-RUNTIME-WORKTREE-LIFECYCLE.md section 9

        Args:
            execution_id: The execution identifier.

        Returns:
            The reconcile disposition ('merged' or 'noop') if reconcile has
            completed with an accepted status, else None.
        """
        if execution_id not in self._active_executions:
            return None

        workspace_id = self._active_executions[execution_id]
        state = self._active_workspaces.get(workspace_id)

        if state is None or state.reconcile_state is None:
            return None

        if state.reconcile_status in ("merged", "noop"):
            return cast(ReconcileDisposition, state.reconcile_status)

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
                        "Cannot cleanup workspace "
                        f"{workspace}: execution {state.execution_id} is still active"
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
                    "Cannot cleanup workspace "
                    f"{workspace}: unresolved reconcile status '{state.reconcile_status}' "
                    f"requires operator attention"
                )

        # Perform mechanical worktree cleanup
        if (
            force
            and state.execution_id
            and state.execution_state is not None
            and state.execution_state.status in ("starting", "running")
        ):
            handle = self._runner_handles.get(state.execution_id)
            if handle is not None:
                runner = self._runner_registry.get(state.execution_state.runner)
                runner.cancel(handle)

        cleanup_result = _run_cleanup(
            step_id=state.binding.step_id,
            worktree_path=state.binding.worktree_path,
            force=True,
        )
        if isinstance(cleanup_result, Failure):
            raise cleanup_result.error

        # Only drop runtime tracking after mechanical cleanup succeeds so that
        # failed cleanup attempts preserve evidence and lifecycle state.
        if state.execution_id:
            self._active_executions.pop(state.execution_id, None)
            self._stalled_executions.discard(state.execution_id)
            self._runner_handles.pop(state.execution_id, None)

        self._pending_reconciles.discard(workspace)
        self._active_reconciles.discard(workspace)
        self._conflicted_reconciles.discard(workspace)

        target_ref = state.binding.target_ref or "HEAD"
        if self._reconcile_lock_by_target.get(target_ref) == workspace:
            del self._reconcile_lock_by_target[target_ref]

        self._active_workspaces.pop(workspace, None)


def _perform_reconcile(
    *,
    execution_id: str,
    workspace_id: str,
    binding: WorktreeBinding,
) -> ReconcileResult:
    """Execute runtime-owned reconcile mechanics for one completed execution."""

    workspace_path = Path(binding.worktree_path)
    _validate_worktree_integrity(binding=binding, workspace_path=workspace_path)
    integration_root = _resolve_integration_root(workspace_path)
    _validate_integration_context(integration_root)

    changed_paths = _list_changed_paths(
        workspace_path=workspace_path,
        base_ref=binding.target_head_at_prepare or "HEAD",
    )
    protected_paths = _protected_paths_from_changes(changed_paths)
    artifact_refs: list[str] = []

    if protected_paths:
        _restore_protected_paths(
            workspace_path=workspace_path,
            target_ref=binding.target_ref,
            protected_paths=protected_paths,
        )
        restored = ",".join(protected_paths)
        artifact_refs.append(f"protected_paths_restored:{restored}")

    effective_paths = tuple(path for path in changed_paths if path not in protected_paths)
    if not effective_paths:
        return ReconcileResult(
            execution_id=execution_id,
            workspace_id=workspace_id,
            status="noop",
            summary="Reconcile noop: only protected-path changes or no changes detected",
            artifact_refs=tuple(artifact_refs),
        )

    workspace_head = _git_stdout(["rev-parse", "HEAD"], cwd=workspace_path)
    merge_result = _run_git(
        ["merge", "--no-ff", "--no-edit", workspace_head],
        cwd=integration_root,
    )
    if merge_result.returncode == 0:
        if "Already up to date." in (merge_result.stdout or ""):
            status: Literal["merged", "noop", "merge_conflict", "aborted"] = "noop"
            summary = "Reconcile noop: integration target already up to date"
        else:
            status = "merged"
            summary = "Reconcile merged workspace changes into integration context"
        return ReconcileResult(
            execution_id=execution_id,
            workspace_id=workspace_id,
            status=status,
            summary=summary,
            artifact_refs=tuple(artifact_refs),
        )

    conflict_files = _list_merge_conflicts(integration_root)
    if conflict_files:
        _abort_merge_if_needed(integration_root)
        return ReconcileResult(
            execution_id=execution_id,
            workspace_id=workspace_id,
            status="merge_conflict",
            summary="Reconcile detected merge conflicts; operator resolution required",
            conflict_files=conflict_files,
            artifact_refs=tuple(artifact_refs),
        )

    detail = merge_result.stderr.strip() or merge_result.stdout.strip() or "unknown merge failure"
    raise ReconcileError(f"Reconcile aborted: git merge failed: {detail}")


def _validate_worktree_integrity(*, binding: WorktreeBinding, workspace_path: Path) -> None:
    """Validate pre-merge worktree integrity requirements."""

    if not workspace_path.exists():
        raise ReconcileError(f"Worktree integrity preflight failed: missing path {workspace_path}")

    inside = _git_stdout(["rev-parse", "--is-inside-work-tree"], cwd=workspace_path)
    if inside.strip() != "true":
        raise ReconcileError("Worktree integrity preflight failed: path is not a git worktree")

    _git_stdout(["rev-parse", "HEAD"], cwd=workspace_path)

    if binding.scratch_branch:
        show_ref = _run_git(
            ["show-ref", "--verify", f"refs/heads/{binding.scratch_branch}"],
            cwd=workspace_path,
        )
        if show_ref.returncode != 0:
            raise ReconcileError(
                "Worktree integrity preflight failed: scratch branch missing "
                f"({binding.scratch_branch})"
            )

    branch_name = _git_stdout(["rev-parse", "--abbrev-ref", "HEAD"], cwd=workspace_path)
    if branch_name.strip() == "HEAD":
        raise ReconcileError(
            "Worktree integrity preflight failed: unexpected detached HEAD state in workspace"
        )


def _validate_integration_context(repo_root: Path) -> None:
    """Validate main integration context safety preflight.

    Per ORCHESTRATION-PLANE-RUNTIME-WORKTREE-LIFECYCLE.md section 9,
    reconcile requires a clean integration context.  However, files that
    are part of the protected-paths set (currently ``plan.yaml``) are
    expected to be modified by the orchestration flow itself (claim/complete
    operations).  Those files are restored to their pre-worktree state by
    ``_restore_protected_paths`` before the actual merge, so their presence
    in the dirty list is not a safety violation.
    """

    git_dir = Path(_git_stdout(["rev-parse", "--git-dir"], cwd=repo_root))
    if not git_dir.is_absolute():
        git_dir = repo_root / git_dir

    active_operation_markers = {
        "MERGE_HEAD": "active merge state",
        "CHERRY_PICK_HEAD": "active cherry-pick state",
        "rebase-apply": "active rebase state",
        "rebase-merge": "active rebase state",
    }
    for marker, reason in active_operation_markers.items():
        if (git_dir / marker).exists():
            raise ReconcileError(f"Integration-context preflight failed: {reason} in {repo_root}")

    status_output = _git_stdout(
        ["status", "--porcelain", "--untracked-files=no"],
        cwd=repo_root,
    )
    if status_output.strip():
        # Allow protected paths (plan.yaml) to be dirty in the integration
        # context because the orchestration flow modifies them as part of
        # claim/complete operations.  They are restored during reconcile.
        dirty_paths = tuple(
            line.strip().split(maxsplit=1)[-1]
            for line in status_output.strip().splitlines()
            if line.strip()
        )
        unprotected_dirty = tuple(
            path for path in dirty_paths if path not in _PROTECTED_RECONCILE_PATHS
        )
        if unprotected_dirty:
            raise ReconcileError(
                "Integration-context preflight failed: "
                "local tracked mutations make reconcile unsafe"
            )


def _list_changed_paths(*, workspace_path: Path, base_ref: str) -> tuple[str, ...]:
    """List paths changed in workspace relative to its integration base."""

    diff_output = _git_stdout(["diff", "--name-only", base_ref, "HEAD"], cwd=workspace_path)
    return tuple(line.strip() for line in diff_output.splitlines() if line.strip())


def _protected_paths_from_changes(changed_paths: tuple[str, ...]) -> tuple[str, ...]:
    """Return changed paths that fall under runtime protected-path policy."""

    normalized = tuple(path.lstrip("./") for path in changed_paths)
    return tuple(path for path in normalized if path in _PROTECTED_RECONCILE_PATHS)


def _restore_protected_paths(
    *,
    workspace_path: Path,
    target_ref: str,
    protected_paths: tuple[str, ...],
) -> None:
    """Restore protected paths to authoritative target state before merge."""

    if not protected_paths:
        return
    target_spec = target_ref or "HEAD"
    _git_stdout(
        ["checkout", target_spec, "--", *protected_paths],
        cwd=workspace_path,
    )


def _list_merge_conflicts(repo_root: Path) -> tuple[str, ...]:
    """List unresolved conflict files in integration context."""

    conflicts = _git_stdout(["diff", "--name-only", "--diff-filter=U"], cwd=repo_root)
    return tuple(line.strip() for line in conflicts.splitlines() if line.strip())


def _abort_merge_if_needed(repo_root: Path) -> None:
    """Abort active merge if merge metadata indicates a pending conflict state."""

    git_dir = Path(_git_stdout(["rev-parse", "--git-dir"], cwd=repo_root))
    if not git_dir.is_absolute():
        git_dir = repo_root / git_dir
    merge_head = git_dir / "MERGE_HEAD"
    if merge_head.exists():
        _run_git(["merge", "--abort"], cwd=repo_root)


def _resolve_integration_root(path: Path) -> Path:
    """Resolve canonical main-worktree integration root from any worktree path."""

    common_dir = Path(_git_stdout(["rev-parse", "--git-common-dir"], cwd=path))
    if not common_dir.is_absolute():
        common_dir = path / common_dir
    common_dir = common_dir.resolve()
    if common_dir.name == ".git":
        return common_dir.parent
    return Path(_git_stdout(["rev-parse", "--show-toplevel"], cwd=path))


def _is_registered_worktree(path: Path) -> bool:
    """Return whether path is currently registered as a git worktree."""

    result = _run_git(["worktree", "list", "--porcelain"], cwd=Path.cwd())
    if result.returncode != 0:
        return False
    normalized_target = str(path.resolve())
    for line in result.stdout.splitlines():
        if line.startswith("worktree "):
            listed = line.removeprefix("worktree ").strip()
            if listed and str(Path(listed).resolve()) == normalized_target:
                return True
    return False


def _run_git(args: list[str], *, cwd: Path) -> subprocess.CompletedProcess[str]:
    """Run a git command with consistent text/capture settings."""

    return subprocess.run(
        ["git", *args],
        capture_output=True,
        text=True,
        cwd=str(cwd),
    )


def _git_stdout(args: list[str], *, cwd: Path) -> str:
    """Run git command and return stripped stdout or raise reconcile error."""

    result = _run_git(args, cwd=cwd)
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or "unknown git error"
        command = " ".join(["git", *args])
        raise ReconcileError(f"git command failed ({command}) in {cwd}: {detail}")
    return result.stdout.strip()


def _current_timestamp() -> float:
    """Return current Unix timestamp."""
    return time.time()


def _request_requires_fresh_workspace(request: ExecutionRequest) -> bool:
    """Return True when execution metadata requests strict workspace freshness."""

    return _request_isolation_mode(request) in (IsolationMode.WORKSPACE, IsolationMode.INDEPENDENT)


def _request_isolation_mode(request: ExecutionRequest) -> IsolationMode:
    """Resolve authoritative isolation mode from execution request refs."""

    normalized_refs = {ref.strip().lower() for ref in request.work_refs}
    if any(
        ref in ("isolation=independent", "isolation:independent", "isolation/independent")
        for ref in normalized_refs
    ):
        return IsolationMode.INDEPENDENT
    if any(ref in _FRESH_WORKSPACE_HINTS for ref in normalized_refs):
        return IsolationMode.WORKSPACE
    return IsolationMode.DEFAULT


def _request_prefers_resume(request: ExecutionRequest) -> bool:
    """Return whether request semantics require runner resume over fresh launch."""

    normalized_refs = {ref.strip().lower() for ref in request.work_refs}
    return any(ref in _RESUME_MODE_HINTS for ref in normalized_refs)


def _request_output_contract(request: ExecutionRequest) -> str:
    """Return declared output contract from execution metadata, if present.

    Authority:
        docs/ORCHESTRATION-PLANE-DISPATCH-AND-PROMPT-POLICY.md section 15
        src/vectl/orchestration/config.py planner family policy

    The runtime uses this to reject planner-class work from the linked-worktree
    launch path. Planner output contract ``vectl_facade_mutation`` means the
    agent is only allowed to mutate state through the approved vectl facade, so
    launching it inside a writable execution worktree would create a direct-edit
    bypass to ``plan.yaml``.
    """

    prefix = "output_contract="
    for ref in request.work_refs:
        normalized_ref = ref.strip()
        if normalized_ref.startswith(prefix):
            return normalized_ref[len(prefix) :]
    return ""


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
