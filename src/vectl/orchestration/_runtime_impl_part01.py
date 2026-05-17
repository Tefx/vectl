from __future__ import annotations
"""
Mechanical execution chores for the orchestration plane.

Authority: docs/ORCHESTRATION-PLANE-ARCHITECTURE.md section 5.3
Authority: docs/ORCHESTRATION-PLANE-INTERFACES.md section 4.3
Authority: docs/ORCHESTRATION-PLANE-ISOLATION-SEMANTICS.md sections 5.4, 5.5
Authority: docs/ORCHESTRATION-PLANE-RUNTIME-WORKTREE-LIFECYCLE.md
"""


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

from typing_extensions import TypeAliasType

from vectl.models import IsolationMode
from vectl.orchestration.contracts import (
    AgentExecutionState,
    ChildRunKind,
    ChildRunRef,
    ChildRunStatus,
    ExecutionRequest,
    ExecutionResult,
    ReconcileDisposition,
    ReconcileResult,
    RuntimeSnapshot,
    WorktreeBinding,
)
from vectl.orchestration.continuity_artifacts import (
    ReconcileClosureStatus,
    ReconcileRecoveryState,
    build_reconcile_recovery_state,
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


# Guard-facing compatibility alias: many runtime helpers intentionally preserve
# established direct-return/raise contracts while exposing a Result[T, E]
# boundary annotation for shell findings. Any is limited to this alias so the
# runtime API and call sites keep their existing concrete behavior.
Result = TypeAliasType("Result", Any, type_params=(_T, _E))


# @shell_complexity: Git worktree preparation must preserve ordered repo-root, target-ref, prune, add, and metadata failure diagnostics.
async def worktree_create(
    step_id: str, base_dir: Path
) -> Result[WorktreeBinding, WorktreeError]:
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


# @shell_complexity: Cleanup branches preserve git worktree removal, missing-path idempotence, and force flag behavior in one lifecycle boundary.
async def worktree_cleanup(
    step_id: str,
    worktree_path: Path | str,
    force: bool = True,
) -> Result[None, WorktreeError]:
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
        return Failure(WorktreeError(f"Failed to cleanup worktree for step '{step_id}' at '{worktree_path}': {exc}"))


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
    "ChildRunKind",
    "ChildRunRef",
    "ChildRunStatus",
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

    Drive-aware fields:
        child_run_kind: When set, this workspace belongs to a drive child run
            (step, resolver, or planner). Used for capacity accounting.
        child_run_drive_id: The drive that owns this child run, if any.
        child_run_ref: The durable ChildRunRef record for this workspace, if any.
    """

    workspace: str
    binding: WorktreeBinding
    execution_id: str = ""
    execution_state: AgentExecutionState | None = None
    reconcile_state: ReconcileResult | None = None
    reconcile_status: Literal[
        "pending", "active", "merged", "noop", "merge_conflict", "aborted"
    ] = "pending"
    child_run_kind: ChildRunKind | None = None
    child_run_drive_id: str | None = None
    child_run_ref: ChildRunRef | None = None


