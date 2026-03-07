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
from pathlib import Path

# Canonical environment variable name
ENV_PLAN_PATH = "VECTL_PLAN_PATH"

# Deprecated alias (kept for backward compatibility)
ENV_PLAN_PATH_DEPRECATED = "VECTL_PLAN"


def is_linked_worktree() -> tuple[bool, Path | None]:
    """Detect if current directory is a linked git worktree.

    Returns:
        tuple: (True, main_worktree_root) if linked worktree, (False, None) otherwise.
    """
    try:
        # Get git-common-dir and git-dir from current working directory
        common_result = subprocess.run(
            ["git", "rev-parse", "--git-common-dir"],
            capture_output=True,
            text=True,
            cwd=Path.cwd(),
        )
        dir_result = subprocess.run(
            ["git", "rev-parse", "--git-dir"],
            capture_output=True,
            text=True,
            cwd=Path.cwd(),
        )
    except OSError:
        # git not installed
        return (False, None)

    if common_result.returncode != 0 or dir_result.returncode != 0:
        # Not a git repo or git command failed
        return (False, None)

    git_common_dir = Path(common_result.stdout.strip())
    git_dir = Path(dir_result.stdout.strip())

    # If git_dir == git_common_dir, this is the main repo, not a linked worktree
    if git_dir == git_common_dir:
        return (False, None)

    # This is a linked worktree - get the main worktree root
    try:
        # Use git-common-dir as the reference for the main worktree
        main_result = subprocess.run(
            ["git", "-C", str(git_common_dir), "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
        )
        if main_result.returncode == 0:
            main_root = Path(main_result.stdout.strip())
            return (True, main_root)
    except OSError:
        pass

    return (False, None)


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

    # 4. Worktree detection: check if we're in a linked worktree
    is_worktree, main_root = is_linked_worktree()
    if is_worktree and main_root is not None:
        return main_root / "plan.yaml"

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


def resolve_state_path(plan_path: Path | None = None) -> Path:
    """Resolve the state.json path for a plan.

    Resolution strategy:
      1. Resolve git-common-dir via `git rev-parse --git-common-dir`.
      2. If git fails, fall back to `.vectl/state.json` under the plan directory.
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
        return plan_path.parent / ".vectl" / "state.json"

    if git_result.returncode == 0:
        git_common_dir = Path(git_result.stdout.strip())
        if not git_common_dir.is_absolute():
            git_common_dir = plan_path.parent / git_common_dir
        return git_common_dir / "vectl" / "state.json"

    return plan_path.parent / ".vectl" / "state.json"
