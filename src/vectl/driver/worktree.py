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
from typing import Generic, TypeAlias, TypeVar

from .errors import WorktreeError

T = TypeVar("T")
E = TypeVar("E", bound=Exception)


@dataclass(frozen=True)
class Success(Generic[T]):
    """Successful result wrapper."""

    value: T


@dataclass(frozen=True)
class Failure(Generic[E]):
    """Failure result wrapper."""

    error: E


Result: TypeAlias = Success[T] | Failure[E]


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


def derive_branch_name(step_id: str) -> Result[str, WorktreeError]:
    """Return the stable branch name for a step."""

    return Success(f"{WORKTREE_BRANCH_PREFIX}{step_id}")


def derive_worktree_path(
    step_id: str,
    base_dir: Path = WORKTREE_BASE_DIR,
) -> Result[Path, WorktreeError]:
    """Return the stable worktree path for a step."""

    return Success(base_dir / step_id)


async def create(
    step_id: str,
    base_dir: Path = WORKTREE_BASE_DIR,
    cwd: Path | None = None,
) -> Result[WorktreeBinding, WorktreeError]:
    """Create or reuse a step worktree."""

    repo_root_result = await _repo_root(step_id, cwd=cwd)
    if isinstance(repo_root_result, Failure):
        return repo_root_result
    repo_root = repo_root_result.value

    branch_name_result = derive_branch_name(step_id)
    if isinstance(branch_name_result, Failure):
        return branch_name_result
    branch_name = branch_name_result.value

    worktree_path_result = derive_worktree_path(step_id=step_id, base_dir=base_dir)
    if isinstance(worktree_path_result, Failure):
        return worktree_path_result
    worktree_path = worktree_path_result.value

    materialized_path_result = _materialize_path(path=worktree_path, repo_root=repo_root)
    if isinstance(materialized_path_result, Failure):
        return materialized_path_result
    materialized_path = materialized_path_result.value

    if materialized_path.exists():
        return Success(
            WorktreeBinding(
                step_id=step_id,
                worktree_path=worktree_path,
                branch_name=branch_name,
                reused_existing=True,
            )
        )

    materialized_path.parent.mkdir(parents=True, exist_ok=True)
    branch_exists_result = await _branch_exists(
        step_id=step_id,
        branch_name=branch_name,
        repo_root=repo_root,
    )
    if isinstance(branch_exists_result, Failure):
        return branch_exists_result
    branch_exists = branch_exists_result.value

    if not branch_exists:
        create_branch_result = await _git_checked(
            step_id=step_id,
            repo_root=repo_root,
            args=("branch", branch_name),
        )
        if isinstance(create_branch_result, Failure):
            return create_branch_result

    add_worktree_result = await _git_checked(
        step_id=step_id,
        repo_root=repo_root,
        args=("worktree", "add", str(worktree_path), branch_name),
    )
    if isinstance(add_worktree_result, Failure):
        return add_worktree_result

    return Success(
        WorktreeBinding(
            step_id=step_id,
            worktree_path=worktree_path,
            branch_name=branch_name,
            reused_existing=branch_exists,
        )
    )


def _ensure_supported_strategy(
    step_id: str,
    strategy: MergeStrategy,
) -> Result[None, WorktreeError]:
    """Validate merge strategy against supported set."""

    if strategy is not MergeStrategy.SQUASH:
        return Failure(WorktreeError(step_id, f"Unsupported merge strategy: {strategy}"))
    return Success(None)


async def merge(
    step_id: str,
    worktree_path: Path,
    strategy: MergeStrategy = MergeStrategy.SQUASH,
    target_branch: str = "main",
    cwd: Path | None = None,
) -> Result[MergeResult, WorktreeError]:
    """Merge a step branch into target branch with squash default."""

    strategy_result = _ensure_supported_strategy(step_id=step_id, strategy=strategy)
    if isinstance(strategy_result, Failure):
        return strategy_result

    repo_root_result = await _repo_root(step_id, cwd=cwd)
    if isinstance(repo_root_result, Failure):
        return repo_root_result
    repo_root = repo_root_result.value

    source_branch_result = derive_branch_name(step_id)
    assert isinstance(source_branch_result, Success)
    source_branch = source_branch_result.value

    checkout_result = await _git_checked(
        step_id=step_id,
        repo_root=repo_root,
        args=("checkout", target_branch),
    )
    if isinstance(checkout_result, Failure):
        return checkout_result

    return await _run_squash_merge(
        step_id=step_id,
        worktree_path=worktree_path,
        source_branch=source_branch,
        target_branch=target_branch,
        repo_root=repo_root,
    )


async def _run_squash_merge(
    step_id: str,
    worktree_path: Path,
    source_branch: str,
    target_branch: str,
    repo_root: Path,
) -> Result[MergeResult, WorktreeError]:
    """Run squash merge and dispatch clean vs conflict flows."""

    merge_result = await _git(
        repo_root=repo_root,
        args=("merge", "--squash", source_branch),
    )
    if isinstance(merge_result, Failure):
        return merge_result

    if merge_result.value.returncode == 0:
        commit_result = await _git(
            repo_root=repo_root, args=("commit", "-m", f"[vectl-driver] {step_id}")
        )
        if isinstance(commit_result, Failure):
            return commit_result
        commit_git = commit_result.value
        if commit_git.returncode != 0:
            combined = f"{commit_git.stdout}\n{commit_git.stderr}".lower()
            nothing_to_commit = (
                "nothing to commit" in combined or "nothing added to commit" in combined
            )
            if not nothing_to_commit:
                formatted_commit = _format_git_error(commit_git)
                if isinstance(formatted_commit, Failure):
                    return formatted_commit
                return Failure(
                    WorktreeError(
                        step_id,
                        f"Unable to commit merge result: {formatted_commit.value}",
                    )
                )
        return Success(MergeResult(outcome=MergeOutcome.CLEAN_MERGE))

    return await _resolve_merge_conflicts(
        step_id=step_id,
        worktree_path=worktree_path,
        source_branch=source_branch,
        target_branch=target_branch,
        repo_root=repo_root,
        failed_merge=merge_result.value,
    )


async def _resolve_merge_conflicts(
    step_id: str,
    worktree_path: Path,
    source_branch: str,
    target_branch: str,
    repo_root: Path,
    failed_merge: _GitResult,
) -> Result[MergeResult, WorktreeError]:
    """Resolve trivial conflicts or return non-trivial dispatch metadata."""

    conflicts_result = await _list_conflicts(step_id=step_id, repo_root=repo_root)
    if isinstance(conflicts_result, Failure):
        return conflicts_result
    conflicted_files = conflicts_result.value

    if not conflicted_files:
        formatted = _format_git_error(failed_merge)
        if isinstance(formatted, Failure):
            return formatted
        return Failure(
            WorktreeError(
                step_id,
                f"squash merge failed without conflict markers: {formatted.value}",
            )
        )

    trivial_result = _all_trivial_conflicts(conflicted_files)
    if isinstance(trivial_result, Failure):
        return trivial_result
    if trivial_result.value:
        checkout_result = await _git_checked(
            step_id=step_id,
            repo_root=repo_root,
            args=("checkout", "--ours", "--", *conflicted_files),
        )
        if isinstance(checkout_result, Failure):
            return checkout_result
        add_result = await _git_checked(
            step_id=step_id,
            repo_root=repo_root,
            args=("add", "--", *conflicted_files),
        )
        if isinstance(add_result, Failure):
            return add_result
        commit_result = await _git(
            repo_root=repo_root,
            args=("commit", "-m", f"[vectl-driver] {step_id} (auto-resolved)"),
        )
        if isinstance(commit_result, Failure):
            return commit_result
        if commit_result.value.returncode != 0:
            combined = f"{commit_result.value.stdout}\n{commit_result.value.stderr}".lower()
            nothing_to_commit = (
                "nothing to commit" in combined or "nothing added to commit" in combined
            )
            if not nothing_to_commit:
                formatted = _format_git_error(commit_result.value)
                if isinstance(formatted, Failure):
                    return formatted
                return Failure(
                    WorktreeError(step_id, f"Unable to commit merge result: {formatted.value}")
                )
        return Success(
            MergeResult(
                outcome=MergeOutcome.AUTO_RESOLVED_CONFLICT,
                conflicted_files=tuple(conflicted_files),
            )
        )

    abort_result = await _git(repo_root=repo_root, args=("merge", "--abort"))
    if isinstance(abort_result, Failure):
        return abort_result
    if abort_result.value.returncode != 0:
        reset_merge = await _git(repo_root=repo_root, args=("reset", "--merge"))
        if isinstance(reset_merge, Failure):
            return reset_merge
        if reset_merge.value.returncode != 0:
            hard_reset = await _git(repo_root=repo_root, args=("reset", "--hard", "HEAD"))
            if isinstance(hard_reset, Failure):
                return hard_reset
            if hard_reset.value.returncode != 0:
                abort_fmt = _format_git_error(abort_result.value)
                if isinstance(abort_fmt, Failure):
                    return abort_fmt
                reset_fmt = _format_git_error(reset_merge.value)
                if isinstance(reset_fmt, Failure):
                    return reset_fmt
                hard_fmt = _format_git_error(hard_reset.value)
                if isinstance(hard_fmt, Failure):
                    return hard_fmt
                return Failure(
                    WorktreeError(
                        step_id,
                        "Unable to clear merge state after non-trivial conflict: "
                        f"{abort_fmt.value}; {reset_fmt.value}; {hard_fmt.value}",
                    )
                )

    return Success(
        MergeResult(
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
    )


async def cleanup(
    step_id: str,
    worktree_path: Path,
    force: bool = True,
    cwd: Path | None = None,
) -> Result[None, WorktreeError]:
    """Remove step worktree and branch artifacts."""

    repo_result = await _git(repo_root=None, args=("rev-parse", "--show-toplevel"), cwd=cwd)
    if isinstance(repo_result, Failure):
        return repo_result
    if repo_result.value.returncode != 0:
        return Success(None)

    repo_root = Path(repo_result.value.stdout.strip())
    branch_name_result = derive_branch_name(step_id)
    if isinstance(branch_name_result, Failure):
        return branch_name_result
    branch_name = branch_name_result.value

    materialized_path_result = _materialize_path(path=worktree_path, repo_root=repo_root)
    if isinstance(materialized_path_result, Failure):
        return materialized_path_result
    materialized_path = materialized_path_result.value

    remove_args = (
        ("worktree", "remove", "--force", str(worktree_path))
        if force
        else ("worktree", "remove", str(worktree_path))
    )
    remove_result = await _git(repo_root=repo_root, args=remove_args)
    if isinstance(remove_result, Failure):
        return remove_result

    branch_delete_args = ("branch", "-D", branch_name) if force else ("branch", "-d", branch_name)
    delete_result = await _git(repo_root=repo_root, args=branch_delete_args)
    if isinstance(delete_result, Failure):
        return delete_result

    if force and materialized_path.exists():
        shutil.rmtree(materialized_path, ignore_errors=True)
    return Success(None)


@dataclass(frozen=True)
class _GitResult:
    """Structured git command result."""

    returncode: int
    stdout: str
    stderr: str


async def _repo_root(step_id: str, cwd: Path | None = None) -> Result[Path, WorktreeError]:
    """Resolve repository root for current process."""

    result = await _git_checked(
        step_id=step_id,
        repo_root=None,
        args=("rev-parse", "--show-toplevel"),
        cwd=cwd,
    )
    if isinstance(result, Failure):
        return result
    return Success(Path(result.value.stdout.strip()))


def _materialize_path(path: Path, repo_root: Path) -> Result[Path, WorktreeError]:
    """Return absolute filesystem path for relative/absolute inputs."""

    if path.is_absolute():
        return Success(path)
    return Success(repo_root / path)


async def _branch_exists(
    step_id: str,
    branch_name: str,
    repo_root: Path,
) -> Result[bool, WorktreeError]:
    """Return whether local branch already exists."""

    result = await _git(
        repo_root=repo_root,
        args=("show-ref", "--verify", "--quiet", f"refs/heads/{branch_name}"),
    )
    if isinstance(result, Failure):
        return result

    known_code_to_presence = {0: True, 1: False}
    if result.value.returncode in known_code_to_presence:
        return Success(known_code_to_presence[result.value.returncode])

    formatted = _format_git_error(result.value)
    assert isinstance(formatted, Success)
    return Failure(WorktreeError(step_id, f"Unable to check branch existence: {formatted.value}"))


async def _list_conflicts(step_id: str, repo_root: Path) -> Result[list[str], WorktreeError]:
    """List currently unresolved conflict file paths."""

    result = await _git_checked(
        step_id=step_id,
        repo_root=repo_root,
        args=("diff", "--name-only", "--diff-filter=U"),
    )
    if isinstance(result, Failure):
        return result
    return Success([line.strip() for line in result.value.stdout.splitlines() if line.strip()])


def _all_trivial_conflicts(conflicted_files: list[str]) -> Result[bool, WorktreeError]:
    """Return True when all conflicts match trivial auto-resolve patterns."""

    for file_path in conflicted_files:
        normalized = Path(file_path).as_posix()
        basename = Path(file_path).name
        if not any(
            fnmatch.fnmatch(normalized, pattern) or fnmatch.fnmatch(basename, pattern)
            for pattern in TRIVIAL_CONFLICT_PATTERNS
        ):
            return Success(False)
    return Success(True)


def _format_git_error(result: _GitResult) -> Result[str, WorktreeError]:
    """Render compact git failure detail for WorktreeError."""

    details = (result.stderr or result.stdout).strip()
    return Success(f"exit={result.returncode}: {details}")


async def _git_checked(
    step_id: str,
    repo_root: Path | None,
    args: tuple[str, ...],
    cwd: Path | None = None,
) -> Result[_GitResult, WorktreeError]:
    """Run git command and return error-typed result."""

    result = await _git(repo_root=repo_root, args=args, cwd=cwd)
    if isinstance(result, Failure):
        return result
    if result.value.returncode == 0:
        return result
    formatted = _format_git_error(result.value)
    if isinstance(formatted, Failure):
        return formatted
    return Failure(WorktreeError(step_id, f"git {' '.join(args)} failed: {formatted.value}"))


async def _git(
    repo_root: Path | None,
    args: tuple[str, ...],
    cwd: Path | None = None,
) -> Result[_GitResult, WorktreeError]:
    """Run git command and capture output."""

    resolved_cwd = repo_root if repo_root is not None else cwd

    process = await asyncio.create_subprocess_exec(
        "git",
        *args,
        cwd=str(resolved_cwd) if resolved_cwd is not None else None,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout_raw, stderr_raw = await process.communicate()
    returncode = process.returncode
    if returncode is None:
        return Failure(WorktreeError("unknown", "git process returned no exit code"))
    return Success(
        _GitResult(
            returncode=returncode,
            stdout=stdout_raw.decode("utf-8", errors="replace"),
            stderr=stderr_raw.decode("utf-8", errors="replace"),
        )
    )


__all__ = [
    "ConflictResolverDispatch",
    "Failure",
    "MergeOutcome",
    "MergeResult",
    "MergeStrategy",
    "Result",
    "Success",
    "TRIVIAL_CONFLICT_PATTERNS",
    "WORKTREE_BASE_DIR",
    "WORKTREE_BRANCH_PREFIX",
    "WorktreeBinding",
    "WorktreeError",
    "cleanup",
    "create",
    "derive_branch_name",
    "derive_worktree_path",
    "merge",
]
