"""YAML I/O and CAS logic."""

from __future__ import annotations

import fcntl
import hashlib
import logging
import os
import subprocess
import tempfile
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, cast

import yaml
from returns.result import Result

from vectl.models import (
    CASConflictError,
    Plan,
    PlanIOError,
    Step,
)
from vectl.shell.admitted_plan_mutation import (
    PLAN_ADMISSION_CONFIG as _RAW_PLAN_ADMISSION_CONFIG,
)
from vectl.shell.admitted_plan_mutation import (
    PlanAdmissionConfig as _PlanAdmissionConfig,
)
from vectl.shell.admitted_plan_mutation import (
    admit_candidate_bytes as _admit_candidate_bytes,
)
from vectl.shell.admitted_plan_mutation import (
    freeze_plan_admission_config as _freeze_plan_admission_config_impl,
)
from vectl.shell.admitted_plan_mutation import (
    sha256_digest as _sha256_digest,
)

_PLAN_ADMISSION_CONFIG = cast(_PlanAdmissionConfig, _RAW_PLAN_ADMISSION_CONFIG)
_freeze_plan_admission_config = _freeze_plan_admission_config_impl

_PLAN_YAML_HEADER = """\
# =============================================================
# MANAGED FILE — DO NOT EDIT DIRECTLY
# This file is owned by vectl. Direct edits bypass CAS write
# protection, lock recalculation, and schema validation.
#
# Use: vectl_* MCP tools (preferred)  OR  vectl <command> (CLI fallback)
# Docs: vectl guide  OR  MCP: vectl_guide
# =============================================================
"""

_LOGGER = logging.getLogger(__name__)

# LibYAML's C implementation is substantially faster for large plans. Keep the
# pure-Python loader as a compatibility fallback for environments without it.
_PLAN_YAML_LOADER = getattr(yaml, "CSafeLoader", yaml.SafeLoader)


@dataclass(frozen=True)
class _PlanMutationReceipt:
    """Machine receipt for one applied exact-byte Plan transaction."""

    schema_version: str
    plan_applied: bool
    base_sha256: str
    candidate_sha256: str
    persisted_sha256: str
    admission_status: Literal["not_configured", "approved"]
    git_attempted: bool
    git_committed: bool


def _file_hash(path: Path) -> str | Result[str, str]:
    """Compute SHA-256 of file contents."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


# @shell_complexity: Loader keeps missing-file, YAML, mapping, and model validation diagnostics together.
def load_plan_definition(path: Path | str) -> tuple[Plan, str] | Result[tuple[Plan, str], str]:
    """Load plan definition from YAML file only.

    Returns (Plan, file_hash) for CAS.
    Does not merge companion ``state.json`` runtime state.
    """
    path = Path(path)
    if not path.exists():
        raise PlanIOError(f"Plan file not found: {path}")
    content_bytes = path.read_bytes()
    file_hash = hashlib.sha256(content_bytes).hexdigest()
    try:
        content = content_bytes.decode("utf-8")
        raw = yaml.load(content, Loader=_PLAN_YAML_LOADER)
    except (UnicodeDecodeError, yaml.YAMLError) as e:
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
) -> str | Result[str, str]:
    """Save plan to YAML file atomically.

    If expected_hash is provided, performs CAS check.
    Returns the new file hash.
    """
    path = Path(path)

    with _locked_plan_file(path):
        return _save_plan_locked(
            plan,
            path,
            expected_hash=expected_hash,
            commit_message=commit_message,
        )


@contextmanager
def _locked_plan_file(path: Path) -> Iterator[None]:
    """Serialize CAS+write access under existing Vectl metadata."""
    from vectl.plan_path import resolve_claims_path

    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = resolve_claims_path(path).parent / f"{path.name}.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+b") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def _save_plan_locked(
    plan: Plan,
    path: Path,
    expected_hash: str | None = None,
    commit_message: str | None = None,
) -> str | Result[str, str]:
    """Save plan assuming caller already holds the plan-file lock."""

    # CAS check
    if expected_hash is not None and path.exists():
        current_hash = _file_hash(path)
        if current_hash != expected_hash:
            raise CASConflictError(path)

    # Serialize
    data = _plan_to_dict(plan)
    yaml_body = yaml.dump(data, default_flow_style=False, allow_unicode=True, sort_keys=False)
    content = _PLAN_YAML_HEADER + yaml_body

    _write_plan_content(path, content)

    new_hash = hashlib.sha256(content.encode()).hexdigest()

    if commit_message is not None:
        _git_commit_plan(path, commit_message)

    return new_hash


def _serialize_plan_bytes(plan: Plan) -> bytes | Result[bytes, str]:
    """Serialize one finalized managed Plan to its only candidate byte buffer."""
    data = _plan_to_dict(plan)
    yaml_body = yaml.dump(data, default_flow_style=False, allow_unicode=True, sort_keys=False)
    return (_PLAN_YAML_HEADER + yaml_body).encode("utf-8")


# @shell_complexity: Coordinates validation, admission, CAS, persistence, backup, and Git.
def _finalize_admitted_plan_mutation(
    plan: Plan,
    path: Path | str,
    *,
    expected_hash: str,
    commit_message: str | None,
    admission_config: _PlanAdmissionConfig = _PLAN_ADMISSION_CONFIG,
) -> _PlanMutationReceipt | Result[_PlanMutationReceipt, str]:
    """Finalize and persist one admitted Plan mutation.

    The transaction hashes authoritative raw bytes for CAS, deep-copies and
    validates the candidate, and serializes the finalized managed Plan exactly
    once. Admission binds ``candidate_sha256`` to that byte buffer. The same
    bytes are atomically persisted and read back as ``persisted_sha256`` before
    backup and best-effort Git work. The receipt keeps ``git_committed``
    separate from Plan application.
    """
    from vectl.core import recalc_lock_status, validate_plan

    plan_path = Path(path)
    if not plan_path.exists():
        raise PlanIOError(f"Plan file not found: {plan_path}")

    base_bytes = plan_path.read_bytes()
    base_hash = hashlib.sha256(base_bytes).hexdigest()
    if base_hash != expected_hash:
        raise CASConflictError(plan_path)

    finalized = plan.model_copy(deep=True)
    recalc_lock_status(finalized)
    try:
        finalized = Plan.model_validate(finalized.model_dump(mode="python"))
    except Exception as exc:
        raise PlanIOError(f"Plan final validation failed: {exc}") from exc
    blocking_issue = next(
        (
            issue
            for issue in validate_plan(finalized, check_refs=False)
            if not issue.is_warning
        ),
        None,
    )
    if blocking_issue is not None:
        raise PlanIOError(f"Plan final validation failed: {blocking_issue.message}")

    candidate_bytes = cast(bytes, _serialize_plan_bytes(finalized))
    candidate_sha256 = cast(str, _sha256_digest(candidate_bytes))
    admission_status = cast(
        Literal["not_configured", "approved"],
        _admit_candidate_bytes(
            config=admission_config,
            plan_path=plan_path,
            base_bytes=base_bytes,
            candidate_bytes=candidate_bytes,
            candidate_sha256=candidate_sha256,
        ),
    )

    with _locked_plan_file(plan_path):
        current_bytes = plan_path.read_bytes() if plan_path.exists() else b""
        if hashlib.sha256(current_bytes).hexdigest() != base_hash:
            raise CASConflictError(plan_path)
        _write_plan_bytes(plan_path, candidate_bytes)
        persisted_bytes = plan_path.read_bytes()
        persisted_sha256 = cast(str, _sha256_digest(persisted_bytes))
        if persisted_sha256 != candidate_sha256:
            raise PlanIOError(
                "Persisted Plan bytes do not match the admitted candidate_sha256"
            )

    try:
        _backup_definition(plan_path)
    except OSError:
        _LOGGER.warning("Backup failed", exc_info=True)

    git_attempted = commit_message is not None
    git_committed = False
    if commit_message is not None:
        git_committed = _git_commit_plan(plan_path, commit_message) is True

    return _PlanMutationReceipt(
        schema_version="vectl.plan-mutation.receipt.v1",
        plan_applied=True,
        base_sha256=cast(str, _sha256_digest(base_bytes)),
        candidate_sha256=candidate_sha256,
        persisted_sha256=persisted_sha256,
        admission_status=admission_status,
        git_attempted=git_attempted,
        git_committed=git_committed,
    )


def _write_plan_bytes(path: Path, content: bytes) -> None:
    """Persist the one admitted byte buffer via temp file and atomic rename."""
    directory = path.parent
    directory.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(dir=str(directory), suffix=".tmp")
    try:
        with os.fdopen(descriptor, "wb") as file:
            file.write(content)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary, str(path))
    except Exception:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def _write_plan_content(path: Path, content: str) -> None:
    """Persist serialized plan content via temp file + atomic rename."""
    _write_plan_bytes(path, content.encode("utf-8"))


# @shell_complexity: Retry, subprocess, timeout, and logging branches preserve commit failure semantics.
def _git_commit_plan(plan_path: Path, message: str) -> bool | Result[bool, str]:
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


# @shell_orchestration: Serialization stays adjacent to YAML writer to preserve plan.yaml compatibility.
def _plan_to_dict(plan: Plan) -> dict[str, Any] | Result[dict[str, Any], str]:
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


# @shell_orchestration: Model default discovery is part of shell YAML compatibility serialization.
def _cleanup_default_fields() -> dict[str, Any] | Result[dict[str, Any], str]:
    """Return model fields eligible for default-value omission.

    Authority:
        docs/RFC-affinity.md

    Includes:
        - affinity-related defaults for cleaner YAML output
    """
    cleanup_defaults: dict[str, Any] = {}
    for model in (Step, Plan):
        for field_name, field in model.model_fields.items():
            if "affinity" in field_name:
                cleanup_defaults[field_name] = field.default
    return cleanup_defaults


# @shell_complexity: Recursive YAML cleanup must preserve list, mapping, empty-string, and default omission semantics.
# @shell_orchestration: Recursive cleanup stays in IO module to preserve current YAML output surface.
def _clean_dict(d: dict[str, Any]) -> dict[str, Any] | Result[dict[str, Any], str]:
    """Remove empty/default values for cleaner YAML output."""
    cleanup_default_fields = _cleanup_default_fields()

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
        # Exclude eligible default fields for cleaner YAML.
        # affinity_override: False means "no override" → omit from YAML.
        elif k in cleanup_default_fields and v == cleanup_default_fields[k]:
            continue
        else:
            result[k] = v
    return result


def _resolve_git_dir(plan_path: Path) -> Path | None | Result[Path | None, str]:
    """Resolve the Git directory for the worktree containing ``plan_path``.

    The Plan target, rather than the process CWD, determines backup authority.
    Linked callers therefore back up the integration Plan when resolution chose it.
    """
    plan_root = plan_path.parent.resolve()
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--git-dir"],
            cwd=str(plan_root),
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        result = None

    if result is not None and result.returncode == 0 and result.stdout.strip():
        git_dir = Path(result.stdout.strip())
        if not git_dir.is_absolute():
            git_dir = plan_root / git_dir
        return git_dir.resolve()

    current = plan_root
    while current != current.parent:
        git_dir = current / ".git"
        if git_dir.is_dir():
            return git_dir
        if git_dir.is_file():
            return None
        current = current.parent
    return None


def _backup_definition(plan_path: Path) -> Path | None | Result[Path | None, str]:
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
