"""YAML I/O and CAS logic."""

from __future__ import annotations

import hashlib
import os
import tempfile
from pathlib import Path
from typing import Any

import yaml

from vectl.models import CASConflictError, Plan, PlanIOError


def _file_hash(path: Path) -> str:
    """Compute SHA-256 of file contents."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_plan(path: Path | str) -> tuple[Plan, str]:
    """Load plan from YAML file.

    Returns (Plan, file_hash) for CAS.
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


def save_plan(plan: Plan, path: Path | str, expected_hash: str | None = None) -> str:
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
    content = yaml.dump(data, default_flow_style=False, allow_unicode=True, sort_keys=False)

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

    return hashlib.sha256(content.encode()).hexdigest()


def _plan_to_dict(plan: Plan) -> dict[str, Any]:
    """Convert Plan to a clean dict for YAML serialization."""
    data = plan.model_dump(mode="json", exclude_none=True, exclude_defaults=False)
    # Remove empty lists and empty strings for cleaner YAML
    return _clean_dict(data)


def _clean_dict(d: dict[str, Any]) -> dict[str, Any]:
    """Remove empty/default values for cleaner YAML output."""
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
        else:
            result[k] = v
    return result
