"""
Mechanical execution chores for the orchestration plane.

Authority: docs/ORCHESTRATION-PLANE-ARCHITECTURE.md section 5.3
Authority: docs/ORCHESTRATION-PLANE-INTERFACES.md section 4.3
Authority: docs/ORCHESTRATION-PLANE-ISOLATION-SEMANTICS.md sections 5.4, 5.5
"""

from __future__ import annotations

import asyncio
import shutil
import threading
import uuid
from collections.abc import Coroutine
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Generic, TypeVar, cast

from vectl.orchestration.contracts import (
    ExecutionRequest,
    ExecutionResult,
    RuntimeSnapshot,
)

if TYPE_CHECKING:
    pass


_T = TypeVar("_T")
_E = TypeVar("_E", bound=Exception)


class WorktreeError(RuntimeError):
    """Mechanical worktree/workspace operation error."""


@dataclass(frozen=True)
class WorktreeBinding:
    """Minimal workspace binding returned by runtime worktree helpers."""

    step_id: str
    worktree_path: Path


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
        return Success(WorktreeBinding(step_id=step_id, worktree_path=worktree_path))
    except OSError as exc:
        return Failure(WorktreeError(f"Failed to create workspace for '{step_id}': {exc}"))


async def worktree_cleanup(
    step_id: str,
    worktree_path: Path,
    force: bool = True,
) -> Success[None] | Failure[WorktreeError]:
    """Remove a mechanical workspace path for a step."""

    del step_id  # retained for surface compatibility
    del force
    try:
        if worktree_path.exists():
            shutil.rmtree(worktree_path)
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
    "Failure",
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
    """

    workspace_root: Path = field(default_factory=lambda: Path(".vectl/workspaces"))
    _active_workspaces: dict[str, _WorkspaceState] = field(default_factory=dict)
    _active_executions: dict[str, str] = field(default_factory=dict)  # execution_id -> workspace
    _stalled_executions: set[str] = field(default_factory=set)

    def snapshot(self) -> RuntimeSnapshot:
        """
        Capture current runtime state.

        Returns:
            RuntimeSnapshot representing active mechanical state.
        """
        return RuntimeSnapshot(
            active_workspaces=tuple(self._active_workspaces.keys()),
            active_executions=tuple(self._active_executions.keys()),
            stalled_executions=tuple(self._stalled_executions),
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
                cleanup_result = _run_cleanup(step_id=request.step_id, worktree_path=existing_path)
                if isinstance(cleanup_result, Failure):
                    raise cleanup_result.error

        binding_result = _run_create(step_id=request.step_id, base_dir=self.workspace_root)
        if isinstance(binding_result, Failure):
            raise binding_result.error
        binding = binding_result.value

        self._active_workspaces[workspace_id] = _WorkspaceState(
            workspace=workspace_id,
            binding=binding,
            execution_id="",
        )

        return workspace_id

    def start(self, request: ExecutionRequest, workspace: str) -> str:
        """
        Start execution in prepared workspace.

        Args:
            request: The execution request.
            workspace: Workspace identifier from prepare().

        Returns:
            Execution identifier for use in collect().

        Raises:
            ValueError: If workspace is not found or not prepared.
        """
        if workspace not in self._active_workspaces:
            raise ValueError(f"Workspace not found or not prepared: {workspace}")

        execution_id = f"exec-{request.step_id}-{uuid.uuid4().hex[:8]}"

        state = self._active_workspaces[workspace]
        state.execution_id = execution_id
        self._active_executions[execution_id] = workspace

        return execution_id

    def collect(self, execution_id: str) -> ExecutionResult | None:
        """
        Collect results from an execution.

        This is a mechanical collection surface. Callers may inject actual
        completion results through external runner integration. Returning
        None indicates "still running".

        Args:
            execution_id: The execution identifier.

        Returns:
            ExecutionResult if complete, else None.
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

        # Collection remains non-blocking until runner integration reports
        # a terminal execution result.
        return None

    def cleanup(self, workspace: str) -> None:
        """
        Clean up a workspace after execution.

        This removes the worktree and branch artifacts per the worktree
        lifecycle contract. It is mechanical cleanup, not policy.

        Args:
            workspace: Workspace identifier to clean up.

        Raises:
            ValueError: If workspace is not found.
        """
        if workspace not in self._active_workspaces:
            raise ValueError(f"Workspace not found: {workspace}")

        state = self._active_workspaces.pop(workspace)

        # Clear any associated execution tracking
        if state.execution_id:
            self._active_executions.pop(state.execution_id, None)
            self._stalled_executions.discard(state.execution_id)

        # Perform mechanical worktree cleanup
        cleanup_result = _run_cleanup(
            step_id=state.binding.step_id,
            worktree_path=state.binding.worktree_path,
            force=True,
        )
        if isinstance(cleanup_result, Failure):
            raise cleanup_result.error


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
    worktree_path: Path,
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
    step_id: str, worktree_path: Path, force: bool
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
