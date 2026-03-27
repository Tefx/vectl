"""Git worktree lifecycle contracts for driver step isolation.

Responsibility: define stable, typed contracts for create/merge/cleanup in the
driver worktree lifecycle.

Non-responsibility: make semantic conflict judgments beyond the trivial
auto-resolve set. Non-trivial conflicts are surfaced to callers for resolver
handoff.

Architecture Reference: docs/DRIVER-ARCHITECTURE.md Section 2.6
Blueprint Reference: DRIVER-BLUEPRINT.md Worktree Lifecycle (worktree.py)
"""

from __future__ import annotations

import asyncio
import fnmatch
import shutil
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from .errors import WorktreeError

WORKTREE_BASE_DIR: Path = Path(".vectl/worktrees")
"""Default base directory for isolated worktrees.

Source: docs/DRIVER-ARCHITECTURE.md Section 2.6 (`base_dir=".vectl/worktrees"`).
"""

WORKTREE_BRANCH_PREFIX: str = "vectl/step-"
"""Stable branch naming prefix for per-step branches.

Source: DRIVER-BLUEPRINT.md Worktree Lifecycle (`vectl/step-{step_id}`).
"""

TRIVIAL_CONFLICT_PATTERNS: frozenset[str] = frozenset(
    {
        "plan.yaml",
        "claims.json",
        ".git/vectl/claims.json",
        "*.lock",
        "*.lockb",
    }
)
"""Conflicts eligible for automatic --ours resolution.

Source: DRIVER-BLUEPRINT.md Worktree Lifecycle (`plan.yaml`, `claims.json`,
`*.lock`, `*.lockb`) and docs/DRIVER-ARCHITECTURE.md Section 2.6
(`plan.yaml`, lockfiles).
"""


class MergeStrategy(str, Enum):
    """Supported merge strategies for worktree branch integration.

    Source: docs/DRIVER-ARCHITECTURE.md Section 2.6 (`strategy="squash"`).
    """

    SQUASH = "squash"


class MergeOutcome(str, Enum):
    """Named merge outcomes required by the lifecycle contract.

    Source: docs/DRIVER-ARCHITECTURE.md Section 2.6 (clean, auto_resolved,
    conflict) and DRIVER-BLUEPRINT.md Worktree Lifecycle.
    """

    CLEAN_MERGE = "clean_merge"
    AUTO_RESOLVED_CONFLICT = "auto_resolved_conflict"
    NON_TRIVIAL_CONFLICT = "non_trivial_conflict"


@dataclass(frozen=True)
class WorktreeBinding:
    """Deterministic worktree identity for a step.

    Source: DRIVER-BLUEPRINT.md Worktree Lifecycle (`branch`, `path`,
    retry reuse when path exists).
    """

    step_id: str
    worktree_path: Path
    branch_name: str
    reused_existing: bool


@dataclass(frozen=True)
class ConflictResolverDispatch:
    """Payload for non-trivial conflict handoff to a later resolver path.

    Source: docs/DRIVER-ARCHITECTURE.md Section 2.6 non-responsibility:
    non-trivial conflicts are reported to caller for judgment-based resolution.
    """

    step_id: str
    worktree_path: Path
    source_branch: str
    target_branch: str
    conflicted_files: tuple[str, ...]
    resolver_path: str = "driver.merge_conflict_resolver"


@dataclass(frozen=True)
class MergeResult:
    """Typed result of attempting to merge a step worktree branch.

    Source: docs/DRIVER-ARCHITECTURE.md Section 2.6 (`MergeResult` statuses)
    and DRIVER-BLUEPRINT.md Worktree Lifecycle outcomes.
    """

    outcome: MergeOutcome
    conflicted_files: tuple[str, ...] = field(default_factory=tuple)
    resolver_dispatch: ConflictResolverDispatch | None = None


def derive_branch_name(step_id: str) -> str:
    """Return the stable branch name for a step.

    Args:
        step_id: Step identifier.

    Returns:
        Branch name in the format `vectl/step-{step_id}`.
    """

    return f"{WORKTREE_BRANCH_PREFIX}{step_id}"


def derive_worktree_path(step_id: str, base_dir: Path = WORKTREE_BASE_DIR) -> Path:
    """Return the stable worktree path for a step.

    Args:
        step_id: Step identifier.
        base_dir: Root directory for step worktrees.

    Returns:
        Path in the format `<base_dir>/<step_id>`.
    """

    return base_dir / step_id


async def create(step_id: str, base_dir: Path = WORKTREE_BASE_DIR) -> WorktreeBinding:
    """Create or reuse a step worktree.

    Contract:
        - Worktree path is stable: `<base_dir>/<step_id>`.
        - Branch name is stable: `vectl/step-{step_id}`.
        - If worktree path already exists, this is a retry and MUST be reused.

    Args:
        step_id: Step identifier.
        base_dir: Root directory for step worktrees.

    Returns:
        `WorktreeBinding` with deterministic path/branch and reuse signal.

    Raises:
        WorktreeError: If git operations fail.
    """
    repo_root = await _repo_root(step_id)
    branch_name = derive_branch_name(step_id)
    worktree_path = derive_worktree_path(step_id=step_id, base_dir=base_dir)
    materialized_path = _materialize_path(path=worktree_path, repo_root=repo_root)

    if materialized_path.exists():
        return WorktreeBinding(
            step_id=step_id,
            worktree_path=worktree_path,
            branch_name=branch_name,
            reused_existing=True,
        )

    materialized_path.parent.mkdir(parents=True, exist_ok=True)
    branch_exists = await _branch_exists(
        step_id=step_id, branch_name=branch_name, repo_root=repo_root
    )
    if not branch_exists:
        await _git_checked(step_id=step_id, repo_root=repo_root, args=("branch", branch_name))

    await _git_checked(
        step_id=step_id,
        repo_root=repo_root,
        args=("worktree", "add", str(worktree_path), branch_name),
    )

    return WorktreeBinding(
        step_id=step_id,
        worktree_path=worktree_path,
        branch_name=branch_name,
        reused_existing=branch_exists,
    )


async def merge(
    step_id: str,
    worktree_path: Path,
    strategy: MergeStrategy = MergeStrategy.SQUASH,
    target_branch: str = "main",
) -> MergeResult:
    """Merge a step branch into target branch with squash default.

    Contract:
        - Default strategy is squash merge.
        - Named outcomes are:
          1) `CLEAN_MERGE`
          2) `AUTO_RESOLVED_CONFLICT` (trivial conflicts only)
          3) `NON_TRIVIAL_CONFLICT` (must dispatch resolver path)
        - Auto-resolution is limited to `TRIVIAL_CONFLICT_PATTERNS`.
        - Non-trivial conflict handling MUST return resolver dispatch metadata
          and MUST NOT silently narrow scope.

    Args:
        step_id: Step identifier.
        worktree_path: Existing worktree path for the step.
        strategy: Merge strategy. Defaults to `MergeStrategy.SQUASH`.
        target_branch: Merge destination branch. Defaults to `main`.

    Returns:
        `MergeResult` with explicit outcome and optional resolver dispatch.

    Raises:
        WorktreeError: If git operations fail unexpectedly.
    """
    if strategy is not MergeStrategy.SQUASH:
        raise WorktreeError(step_id, f"Unsupported merge strategy: {strategy}")

    repo_root = await _repo_root(step_id)
    source_branch = derive_branch_name(step_id)
    await _git_checked(step_id=step_id, repo_root=repo_root, args=("checkout", target_branch))

    merge_result = await _git(
        repo_root=repo_root,
        args=("merge", "--squash", source_branch),
    )
    if merge_result.returncode == 0:
        await _commit_merge(
            step_id=step_id,
            repo_root=repo_root,
            message=f"[vectl-driver] {step_id}",
        )
        return MergeResult(outcome=MergeOutcome.CLEAN_MERGE)

    conflicted_files = await _list_conflicts(step_id=step_id, repo_root=repo_root)
    if not conflicted_files:
        raise WorktreeError(
            step_id,
            f"squash merge failed without conflict markers: {_format_git_error(merge_result)}",
        )

    if _all_trivial_conflicts(conflicted_files):
        await _git_checked(
            step_id=step_id,
            repo_root=repo_root,
            args=("checkout", "--ours", "--", *conflicted_files),
        )
        await _git_checked(
            step_id=step_id,
            repo_root=repo_root,
            args=("add", "--", *conflicted_files),
        )
        await _commit_merge(
            step_id=step_id,
            repo_root=repo_root,
            message=f"[vectl-driver] {step_id} (auto-resolved)",
        )
        return MergeResult(
            outcome=MergeOutcome.AUTO_RESOLVED_CONFLICT,
            conflicted_files=tuple(conflicted_files),
        )

    await _abort_merge_state(step_id=step_id, repo_root=repo_root)
    return MergeResult(
        outcome=MergeOutcome.NON_TRIVIAL_CONFLICT,
        conflicted_files=tuple(conflicted_files),
        resolver_dispatch=ConflictResolverDispatch(
            step_id=step_id,
            worktree_path=worktree_path,
            source_branch=source_branch,
            target_branch=target_branch,
            conflicted_files=tuple(conflicted_files),
        ),
    )


async def cleanup(step_id: str, worktree_path: Path, force: bool = True) -> None:
    """Remove step worktree and branch artifacts.

    Contract:
        - Removes worktree path and step branch (`vectl/step-{step_id}`).
        - Best-effort cleanup behavior is defined by caller policy.

    Args:
        step_id: Step identifier.
        worktree_path: Worktree path to remove.
        force: Whether to force cleanup operations.

    Returns:
        None.

    Raises:
        None.
    """
    repo_result = await _git(repo_root=None, args=("rev-parse", "--show-toplevel"))
    if repo_result.returncode != 0:
        return

    repo_root = Path(repo_result.stdout.strip())
    branch_name = derive_branch_name(step_id)
    materialized_path = _materialize_path(path=worktree_path, repo_root=repo_root)

    remove_args: tuple[str, ...]
    if force:
        remove_args = ("worktree", "remove", "--force", str(worktree_path))
    else:
        remove_args = ("worktree", "remove", str(worktree_path))
    await _git(repo_root=repo_root, args=remove_args)

    branch_delete_args: tuple[str, ...]
    if force:
        branch_delete_args = ("branch", "-D", branch_name)
    else:
        branch_delete_args = ("branch", "-d", branch_name)
    await _git(repo_root=repo_root, args=branch_delete_args)

    if force and materialized_path.exists():
        shutil.rmtree(materialized_path, ignore_errors=True)


@dataclass(frozen=True)
class _GitResult:
    """Structured git command result."""

    returncode: int
    stdout: str
    stderr: str


async def _repo_root(step_id: str) -> Path:
    """Resolve repository root for current process."""

    result = await _git_checked(
        step_id=step_id,
        repo_root=None,
        args=("rev-parse", "--show-toplevel"),
    )
    return Path(result.stdout.strip())


def _materialize_path(path: Path, repo_root: Path) -> Path:
    """Return absolute filesystem path for relative/absolute inputs."""

    if path.is_absolute():
        return path
    return repo_root / path


async def _branch_exists(step_id: str, branch_name: str, repo_root: Path) -> bool:
    """Return whether local branch already exists."""

    result = await _git(
        repo_root=repo_root,
        args=("show-ref", "--verify", "--quiet", f"refs/heads/{branch_name}"),
    )
    if result.returncode == 0:
        return True
    if result.returncode == 1:
        return False
    raise WorktreeError(step_id, f"Unable to check branch existence: {_format_git_error(result)}")


async def _list_conflicts(step_id: str, repo_root: Path) -> list[str]:
    """List currently unresolved conflict file paths."""

    result = await _git_checked(
        step_id=step_id,
        repo_root=repo_root,
        args=("diff", "--name-only", "--diff-filter=U"),
    )
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def _all_trivial_conflicts(conflicted_files: list[str]) -> bool:
    """Return True when all conflicts match trivial auto-resolve patterns."""

    for file_path in conflicted_files:
        normalized = Path(file_path).as_posix()
        basename = Path(file_path).name
        if not any(
            fnmatch.fnmatch(normalized, pattern) or fnmatch.fnmatch(basename, pattern)
            for pattern in TRIVIAL_CONFLICT_PATTERNS
        ):
            return False
    return True


async def _commit_merge(step_id: str, repo_root: Path, message: str) -> None:
    """Commit staged squash merge changes when present."""

    result = await _git(repo_root=repo_root, args=("commit", "-m", message))
    if result.returncode == 0:
        return
    combined = f"{result.stdout}\n{result.stderr}".lower()
    if "nothing to commit" in combined or "nothing added to commit" in combined:
        return
    raise WorktreeError(step_id, f"Unable to commit merge result: {_format_git_error(result)}")


async def _abort_merge_state(step_id: str, repo_root: Path) -> None:
    """Abort merge state for regular or squash conflicts."""

    abort_result = await _git(repo_root=repo_root, args=("merge", "--abort"))
    if abort_result.returncode == 0:
        return

    reset_result = await _git(repo_root=repo_root, args=("reset", "--merge"))
    if reset_result.returncode == 0:
        return

    hard_reset_result = await _git(repo_root=repo_root, args=("reset", "--hard", "HEAD"))
    if hard_reset_result.returncode == 0:
        return

    raise WorktreeError(
        step_id,
        "Unable to clear merge state after non-trivial conflict: "
        f"{_format_git_error(abort_result)}; {_format_git_error(reset_result)}; "
        f"{_format_git_error(hard_reset_result)}",
    )


def _format_git_error(result: _GitResult) -> str:
    """Render compact git failure detail for WorktreeError."""

    details = (result.stderr or result.stdout).strip()
    return f"exit={result.returncode}: {details}"


async def _git_checked(step_id: str, repo_root: Path | None, args: tuple[str, ...]) -> _GitResult:
    """Run git command and raise WorktreeError on non-zero exit."""

    result = await _git(repo_root=repo_root, args=args)
    if result.returncode != 0:
        raise WorktreeError(step_id, f"git {' '.join(args)} failed: {_format_git_error(result)}")
    return result


async def _git(repo_root: Path | None, args: tuple[str, ...]) -> _GitResult:
    """Run git command and capture output."""

    process = await asyncio.create_subprocess_exec(
        "git",
        *args,
        cwd=str(repo_root) if repo_root is not None else None,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout_raw, stderr_raw = await process.communicate()
    returncode = process.returncode
    if returncode is None:
        returncode = 1
    return _GitResult(
        returncode=returncode,
        stdout=stdout_raw.decode("utf-8", errors="replace"),
        stderr=stderr_raw.decode("utf-8", errors="replace"),
    )


__all__ = [
    "ConflictResolverDispatch",
    "MergeOutcome",
    "MergeResult",
    "MergeStrategy",
    "TRIVIAL_CONFLICT_PATTERNS",
    "WORKTREE_BASE_DIR",
    "WORKTREE_BRANCH_PREFIX",
    "WorktreeBinding",
    "cleanup",
    "create",
    "derive_branch_name",
    "derive_worktree_path",
    "merge",
]
