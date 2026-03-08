"""YAML I/O and CAS logic."""

from __future__ import annotations

import hashlib
import logging
import os
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

import yaml

from vectl.models import (
    CASConflictError,
    Plan,
    PlanIOError,
    Step,
)

_PLAN_YAML_HEADER = """\
# =============================================================
# MANAGED FILE — DO NOT EDIT DIRECTLY
# This file is owned by vectl. Direct edits bypass CAS write
# protection, lock recalculation, and schema validation.
#
# Use: uvx vectl <command>  OR  vectl_* MCP tools
# Docs: uvx vectl guide
# =============================================================
"""

_LOGGER = logging.getLogger(__name__)


def _file_hash(path: Path) -> str:
    """Compute SHA-256 of file contents."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_plan_definition(path: Path | str) -> tuple[Plan, str]:
    """Load plan definition from YAML file only.

    Returns (Plan, file_hash) for CAS.
    Does not merge companion ``state.json`` runtime state.
    """
    path = Path(path)
    if not path.exists():
        raise PlanIOError(f"Plan file not found: {path}")
    content = path.read_text(encoding="utf-8")
    file_hash = hashlib.sha256(content.encode()).hexdigest()
    try:
        raw = yaml.safe_load(content)
    except yaml.YAMLError as e:
        raise PlanIOError(f"Invalid YAML in {path}: {e}") from e
    if not isinstance(raw, dict):
        raise PlanIOError(f"Plan file must be a YAML mapping, got {type(raw).__name__}")
    try:
        plan = Plan(**raw)
    except Exception as e:
        raise PlanIOError(f"Invalid plan structure: {e}") from e
    return plan, file_hash


def save_plan(
    plan: Plan,
    path: Path | str,
    expected_hash: str | None = None,
    commit_message: str | None = None,
) -> str:
    """Save plan to YAML file atomically.

    If expected_hash is provided, performs CAS check.
    Returns the new file hash.
    """
    path = Path(path)

    # CAS check
    if expected_hash is not None and path.exists():
        current_hash = _file_hash(path)
        if current_hash != expected_hash:
            raise CASConflictError(path)

    # Serialize
    data = _plan_to_dict(plan)
    yaml_body = yaml.dump(data, default_flow_style=False, allow_unicode=True, sort_keys=False)
    content = _PLAN_YAML_HEADER + yaml_body

    # Atomic write: temp file + rename
    dir_ = path.parent
    dir_.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=str(dir_), suffix=".tmp")
    try:
        os.write(fd, content.encode("utf-8"))
        os.close(fd)
        os.replace(tmp_path, str(path))
    except Exception:
        # Clean up temp file on error
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise

    new_hash = hashlib.sha256(content.encode()).hexdigest()

    if commit_message is not None:
        _git_commit_plan(path, commit_message)

    return new_hash


def _git_commit_plan(plan_path: Path, message: str) -> bool:
    """Best-effort git commit for ``plan_path`` after a successful write."""

    command = ["git", "commit", "--only", "--no-verify", plan_path.name, "-m", message]

    for attempt in range(2):
        try:
            result = subprocess.run(
                command,
                cwd=str(plan_path.parent),
                capture_output=True,
                text=True,
                timeout=10,
            )
        except FileNotFoundError as exc:
            _LOGGER.warning("Git unavailable for %s: %s", plan_path, exc)
            return False
        except (subprocess.TimeoutExpired, OSError) as exc:
            _LOGGER.warning("Git commit failed for %s: %s", plan_path, exc)
            return False

        if result.returncode == 0:
            return True

        stderr = result.stderr or ""
        if attempt == 0 and result.returncode == 1 and "index.lock" in stderr:
            time.sleep(0.1)
            continue

        output = stderr.strip() or (result.stdout or "").strip() or "no output"
        _LOGGER.warning(
            "Git commit failed for %s with exit code %s: %s",
            plan_path,
            result.returncode,
            output,
        )
        return False

    _LOGGER.warning("Git commit failed for %s due to persistent index.lock contention", plan_path)
    return False


def _plan_to_dict(plan: Plan) -> dict[str, Any]:
    """Convert Plan to a clean dict for YAML serialization.

    Per ADR-unified-state.md: all mutable runtime state (status, done_at, claimed_by,
    claimed_at, evidence, skipped_reason, rejection_reason, rejection_history,
    affinity_override, affinity_override_by, affinity_override_at) is stored inline
    in plan.yaml. This function serializes all fields - state separation (state.json)
    is no longer used; done_at=None is excluded via exclude_none=True.
    """
    data = plan.model_dump(mode="json", exclude_none=True, exclude_defaults=False)
    # Remove empty lists and empty strings for cleaner YAML
    return _clean_dict(data)


def _clean_dict(d: dict[str, Any]) -> dict[str, Any]:
    """Remove empty/default values for cleaner YAML output."""
    # Get field defaults from Pydantic models for affinity cleanup logic
    step_fields = Step.model_fields
    plan_fields = Plan.model_fields

    # Step affinity fields and their defaults
    step_affinity_override = step_fields.get("affinity_override")
    step_affinity_override_by = step_fields.get("affinity_override_by")
    step_affinity_override_at = step_fields.get("affinity_override_at")

    # Plan default_affinity field and its default
    plan_default_affinity = plan_fields.get("default_affinity")

    # Set of affinity field names to check (derived from model)
    affinity_fields = {
        name: field.default
        for name, field in (
            ("affinity_override", step_affinity_override),
            ("affinity_override_by", step_affinity_override_by),
            ("affinity_override_at", step_affinity_override_at),
            ("default_affinity", plan_default_affinity),
        )
        if field is not None
    }

    result: dict[str, Any] = {}
    for k, v in d.items():
        if isinstance(v, list):
            if not v:
                continue
            cleaned = []
            for item in v:
                if isinstance(item, dict):
                    cleaned.append(_clean_dict(item))
                else:
                    cleaned.append(item)
            result[k] = cleaned
        elif isinstance(v, dict):
            cleaned_dict = _clean_dict(v)
            if cleaned_dict:
                result[k] = cleaned_dict
        elif v == "" and k not in ("project", "name", "id", "description"):
            continue
        # RFC: docs/RFC-affinity.md
        # Exclude default affinity fields for cleaner YAML.
        # affinity_override: False means "no override" → omit from YAML.
        elif k in affinity_fields and v == affinity_fields[k]:
            continue
        else:
            result[k] = v
    return result


def _resolve_git_dir(plan_path: Path) -> Path | None:
    """Resolve the .git directory for a plan file's parent directory.

    Returns None if not in a git repo or if in a linked worktree.
    """
    from vectl.plan_path import is_linked_worktree

    # Check if we're in a linked worktree - don't backup there
    is_linked, _ = is_linked_worktree()
    if is_linked:
        return None

    # Find .git directory
    current = plan_path.parent.resolve()
    while current != current.parent:
        git_dir = current / ".git"
        if git_dir.exists() and git_dir.is_dir():
            return git_dir
        current = current.parent
    return None


def _backup_definition(plan_path: Path) -> Path | None:
    """Create a backup of the plan definition in .git/vectl/plan.yaml.bak.

    Returns the backup path if successful, None if skipped.

    Raises:
        OSError: If backup fails due to permissions or IO issues.
    """
    # Check if we're in a linked worktree - skip backup
    git_dir = _resolve_git_dir(plan_path)
    if git_dir is None:
        return None

    # Create backup directory
    vectl_dir = git_dir / "vectl"
    vectl_dir.mkdir(parents=True, exist_ok=True)

    # Read current plan content
    content = plan_path.read_text(encoding="utf-8")

    # Atomic write: temp file + rename
    backup_path = vectl_dir / "plan.yaml.bak"
    dir_ = vectl_dir
    fd, tmp_path = tempfile.mkstemp(dir=str(dir_), suffix=".tmp")
    try:
        os.write(fd, content.encode("utf-8"))
        os.close(fd)
        os.replace(tmp_path, str(backup_path))
    except Exception:
        # Clean up temp file on error
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise

    return backup_path
