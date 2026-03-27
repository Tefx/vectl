"""Git worktree lifecycle contracts for driver step isolation.

Responsibility: define stable, typed contracts for create/merge/cleanup in the
driver worktree lifecycle.

Non-responsibility: execute git commands. All lifecycle functions in this
module are contract stubs for later implementation.

Architecture Reference: docs/DRIVER-ARCHITECTURE.md Section 2.6
Blueprint Reference: DRIVER-BLUEPRINT.md Worktree Lifecycle (worktree.py)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

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
        NotImplementedError: Contract-only stub. Git implementation deferred.
    """

    raise NotImplementedError("Contract-only stub: create() git lifecycle not implemented")


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
        NotImplementedError: Contract-only stub. Git implementation deferred.
    """

    raise NotImplementedError("Contract-only stub: merge() git lifecycle not implemented")


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
        NotImplementedError: Contract-only stub. Git implementation deferred.
    """

    raise NotImplementedError("Contract-only stub: cleanup() git lifecycle not implemented")


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
