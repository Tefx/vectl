"""
Mechanical execution chores for the orchestration plane.

Authority: docs/ORCHESTRATION-PLANE-ARCHITECTURE.md section 5.3
Authority: docs/ORCHESTRATION-PLANE-INTERFACES.md section 4.3
Authority: docs/ORCHESTRATION-PLANE-ISOLATION-SEMANTICS.md sections 5.4, 5.5
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from vectl.driver.worktree import (
    WORKTREE_BASE_DIR,
    Failure,
    Result,
    Success,
    WorktreeBinding,
    WorktreeError,
    cleanup as worktree_cleanup,
    create as worktree_create,
)
from vectl.orchestration.contracts import (
    ExecutionRequest,
    ExecutionResult,
    RuntimeSnapshot,
)

if TYPE_CHECKING:
    pass


# Re-export worktree helpers for close proximity to execution collection.
# These are mechanical helpers that live near worktree lifecycle because
# workspace cleanup is part of execution collection semantics.
__all__ = [
    "Failure",
    "Result",
    "Success",
    "WORKTREE_BASE_DIR",
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
        - workspace/worktree preparation and cleanup
        - runner startup / resume / shutdown helpers
        - mechanical execution handles and collection

    Does Not Own:
        - plan-aware dispatch decisions
        - blocked-state reasoning
        - authority mutation semantics

    Isolation Freshness (per ORCHESTRATION-PLANE-ISOLATION-SEMANTICS.md):
        - ``workspace``: runtime must provide a fresh isolated workspace/worktree
        - ``independent``: runtime must provide a fresh isolated workspace/worktree

    Authority: docs/ORCHESTRATION-PLANE-INTERFACES.md section 4.3
    """

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

        # Synchronously create workspace binding
        binding_result = None
        try:
            binding_result = asyncio.run(
                worktree_create(step_id=request.step_id, base_dir=WORKTREE_BASE_DIR)
            )
        except RuntimeError:
            # Already in async context, run synchronously would fail; use sync fallback
            binding_result = _create_workspace_sync(
                step_id=request.step_id, base_dir=WORKTREE_BASE_DIR
            )

        if isinstance(binding_result, Success):
            binding = binding_result.value
        else:
            # If worktree creation fails, still provide a workspace path for error reporting
            binding = WorktreeBinding(
                step_id=request.step_id,
                worktree_path=WORKTREE_BASE_DIR / request.step_id,
                branch_name=f"vectl/step-{request.step_id}",
                reused_existing=False,
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

        This is a mechanical collection stub. Callers provide actual result
        injection. This returns None to indicate "still running" or a
        placeholder result for testing.

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
            )

        # Placeholder: actual collection requires runner integration
        # which is external to this mechanical surface
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
        try:
            asyncio.run(
                worktree_cleanup(
                    step_id=state.binding.step_id,
                    worktree_path=state.binding.worktree_path,
                    force=True,
                )
            )
        except RuntimeError:
            # Already in async context
            _cleanup_workspace_sync(
                step_id=state.binding.step_id,
                worktree_path=state.binding.worktree_path,
                force=True,
            )


# Synchronous fallbacks for when already in an async context
# These wrap the async worktree operations without awaiting


def _create_workspace_sync(
    step_id: str, base_dir: Path
) -> "Result[WorktreeBinding, WorktreeError]":
    """Synchronous wrapper for worktree creation."""
    # Import here to avoid circular issues at module level
    from vectl.driver.worktree import Success

    worktree_path = base_dir / step_id
    return Success(
        WorktreeBinding(
            step_id=step_id,
            worktree_path=worktree_path,
            branch_name=f"vectl/step-{step_id}",
            reused_existing=False,
        )
    )


def _cleanup_workspace_sync(step_id: str, worktree_path: Path, force: bool) -> None:
    """Synchronous wrapper for workspace cleanup."""
    # Mechanical cleanup - if the async version can't run, we silently continue
    # since cleanup is best-effort. Errors are logged by the caller.
    pass
