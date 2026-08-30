"""Shared plan path resolution for CLI and MCP.

Single source of truth for finding plan.yaml. Both CLI and MCP
MUST use resolve_plan_path() to ensure identical behavior.

Canonical env var: VECTL_PLAN_PATH
Deprecated alias: VECTL_PLAN (lower precedence, emits warning)

Resolution order:
  1. explicit parameter (--plan flag or function arg)
  2. VECTL_PLAN_PATH env var
  3. VECTL_PLAN env var (deprecated, warns)
  4. linked worktree detection (find main worktree's plan.yaml)
  5. walk-up discovery (find plan.yaml in parent dirs)
  6. ./plan.yaml (fallback, may not exist)

Source: p0-parity.1-plan-path step — expert P0 finding:
CLI and MCP could target different plan files.
"""

from __future__ import annotations

import os
import subprocess
import warnings
from dataclasses import dataclass
from pathlib import Path

# Canonical environment variable name
ENV_PLAN_PATH = "VECTL_PLAN_PATH"

# Deprecated alias (kept for backward compatibility)
ENV_PLAN_PATH_DEPRECATED = "VECTL_PLAN"


@dataclass(frozen=True)
class _WorktreeProbe:
    is_git_repo: bool
    is_linked: bool
    main_root: Path | None
    malformed: bool


def _normalize_git_path(raw: str, cwd: Path):
    """Normalize git path output to an absolute path.

    Returns None for empty/malformed path output.
    """
    value = raw.strip()
    if not value:
        return None

    path = Path(value)
    if not path.is_absolute():
        path = cwd / path
    return path.resolve()


# @shell_complexity: git worktree fail-closed edge cases are kept in one audited probe
def _probe_worktree_layout():
    """Probe git worktree layout with fail-closed malformed detection."""
    cwd = Path.cwd()

    try:
        common_result = subprocess.run(
            ["git", "rev-parse", "--git-common-dir"],
            capture_output=True,
            text=True,
            cwd=cwd,
        )
        dir_result = subprocess.run(
            ["git", "rev-parse", "--git-dir"],
            capture_output=True,
            text=True,
            cwd=cwd,
        )
    except OSError:
        # git not installed
        return _WorktreeProbe(is_git_repo=False, is_linked=False, main_root=None, malformed=False)

    if common_result.returncode != 0 or dir_result.returncode != 0:
        # Not a git repo or git command failed
        return _WorktreeProbe(is_git_repo=False, is_linked=False, main_root=None, malformed=False)

    git_common_dir = _normalize_git_path(common_result.stdout, cwd)
    git_dir = _normalize_git_path(dir_result.stdout, cwd)
    if git_common_dir is None or git_dir is None:
        # Probe says git repo but returned unusable paths.
        return _WorktreeProbe(is_git_repo=True, is_linked=True, main_root=None, malformed=True)

    if git_dir == git_common_dir:
        return _WorktreeProbe(is_git_repo=True, is_linked=False, main_root=None, malformed=False)

    # Linked worktree. Resolve canonical main root from git-common-dir.
    # In linked layouts git-common-dir points to main_root/.git.
    if not git_common_dir.exists() or not git_common_dir.is_dir():
        return _WorktreeProbe(is_git_repo=True, is_linked=True, main_root=None, malformed=True)

    if git_common_dir.name != ".git":
        return _WorktreeProbe(is_git_repo=True, is_linked=True, main_root=None, malformed=True)

    main_root = git_common_dir.parent
    if not main_root.exists() or not main_root.is_dir():
        return _WorktreeProbe(is_git_repo=True, is_linked=True, main_root=None, malformed=True)

    return _WorktreeProbe(
        is_git_repo=True,
        is_linked=True,
        main_root=main_root,
        malformed=False,
    )


# @shell:entry - compatibility boundary returns legacy tuple shape
def is_linked_worktree() -> tuple[bool, Path | None]:
    """Detect if current directory is a linked git worktree.

    Returns:
        tuple: (True, main_worktree_root) if linked worktree, (False, None) otherwise.
    """
    probe = _probe_worktree_layout()
    if probe.is_linked:
        return (True, probe.main_root)
    return (False, None)


# @invar:allow shell_result: public CLI/MCP compatibility requires direct Path return
# @shell_complexity: precedence chain is the canonical path contract
def resolve_plan_path(explicit: Path | None = None) -> Path:
    """Resolve the plan.yaml path using the canonical precedence chain.

    Args:
        explicit: Explicitly provided path (e.g. --plan flag).
            Takes highest precedence when set.

    Returns:
        Resolved Path to plan.yaml. May not exist on disk
        (caller is responsible for existence checks).
    """
    # 1. Explicit parameter
    if explicit is not None:
        return explicit

    # 2. Canonical env var: VECTL_PLAN_PATH
    if env_canonical := os.environ.get(ENV_PLAN_PATH):
        return Path(env_canonical)

    # 3. Deprecated alias: VECTL_PLAN (warn)
    if env_deprecated := os.environ.get(ENV_PLAN_PATH_DEPRECATED):
        warnings.warn(
            "VECTL_PLAN is deprecated, use VECTL_PLAN_PATH instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        return Path(env_deprecated)

    # 4. Worktree detection
    probe = _probe_worktree_layout()
    if probe.is_linked:
        if probe.main_root is not None:
            # ADR: linked worktrees resolve deterministically to main-root plan path.
            # Do not fall back to local walk-up when this file is missing.
            return probe.main_root / "plan.yaml"
        # Fail closed for malformed/partial linked-worktree resolution.
        return Path.cwd() / "plan.yaml"

    # 5. Walk-up discovery
    current = Path.cwd()
    while True:
        candidate = current / "plan.yaml"
        if candidate.exists():
            return candidate
        if current.parent == current:  # filesystem root
            break
        current = current.parent

    # 6. Fallback: ./plan.yaml (may not exist)
    return Path("plan.yaml")


# @shell_complexity: fallback branches preserve git-common-dir state semantics
# @shell:entry - compatibility boundary returns legacy Path shape
# @invar:allow entry_point_too_thick: git-common-dir fallback must remain a single public resolver
def resolve_claims_path(plan_path: Path | None = None) -> Path:
    """Resolve the claims.json path for a plan.

    Claims are ephemeral and shared across linked worktrees via git-common-dir.
    See docs/ADR-unified-state.md.
    """
    if plan_path is None:
        plan_path = resolve_plan_path()

    try:
        git_result = subprocess.run(
            ["git", "rev-parse", "--git-common-dir"],
            capture_output=True,
            text=True,
            cwd=plan_path.parent,
        )
    except OSError:
        return plan_path.parent / ".vectl" / "claims.json"

    if git_result.returncode == 0:
        git_common_dir = Path(git_result.stdout.strip())
        if not git_common_dir.is_absolute():
            git_common_dir = plan_path.parent / git_common_dir
        return git_common_dir / "vectl" / "claims.json"

    return plan_path.parent / ".vectl" / "claims.json"
