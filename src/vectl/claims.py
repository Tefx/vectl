"""Claims store I/O for ephemeral branch-scoped coordination."""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Iterator

import fcntl
from pydantic import BaseModel

from vectl.models import PlanError
from vectl.plan_path import resolve_claims_path as _resolve_claims_path


class ClaimEntry(BaseModel):
    """Single claim record keyed by ``<branch>:<step_id>``."""

    step_id: str
    branch: str
    agent: str
    claimed_at: str


def _claim_key(branch: str, step_id: str) -> str:
    return f"{branch}:{step_id}"


def _now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _parse_iso8601(timestamp: str) -> datetime | None:
    value = timestamp.strip()
    if not value:
        return None
    if value.endswith("Z"):
        value = f"{value[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _is_stale(entry: ClaimEntry, ttl_hours: float) -> bool:
    parsed = _parse_iso8601(entry.claimed_at)
    if parsed is None:
        return True
    return datetime.now(UTC) - parsed > timedelta(hours=ttl_hours)


def _validate_claims_mapping(raw: object) -> dict[str, ClaimEntry]:
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise PlanError("claims.json must be a JSON object")

    parsed: dict[str, ClaimEntry] = {}
    for key, value in raw.items():
        if not isinstance(key, str) or ":" not in key:
            raise PlanError(f"Invalid claim key format: {key!r}")
        if not isinstance(value, dict):
            raise PlanError(f"Claim entry must be an object for key: {key}")
        try:
            parsed[key] = ClaimEntry(**value)
        except Exception as exc:
            raise PlanError(f"Invalid claim entry for key {key}: {exc}") from exc
    return parsed


def _read_claims_file(claims_path: Path) -> dict[str, ClaimEntry]:
    if not claims_path.exists():
        return {}
    text = claims_path.read_text(encoding="utf-8")
    if not text.strip():
        return {}
    try:
        raw = json.loads(text)
    except json.JSONDecodeError as exc:
        raise PlanError(f"Invalid JSON in claims file: {exc}") from exc
    return _validate_claims_mapping(raw)


def _write_claims_file(claims_path: Path, claims: dict[str, ClaimEntry]) -> None:
    payload = {key: entry.model_dump(mode="json") for key, entry in claims.items()}
    data = json.dumps(payload, indent=2, sort_keys=True)

    directory = claims_path.parent
    fd, tmp_path = tempfile.mkstemp(dir=str(directory), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as tmp_file:
            tmp_file.write(data)
            tmp_file.flush()
            os.fsync(tmp_file.fileno())
        os.replace(tmp_path, claims_path)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


@contextmanager
def _locked_claims_file(claims_path: Path) -> Iterator[None]:
    claims_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = claims_path.parent / f"{claims_path.name}.lock"
    with lock_path.open("a+b") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def resolve_claims_path(plan_path: Path) -> Path:
    """Resolve claims.json location shared by linked worktrees when possible."""
    return _resolve_claims_path(plan_path)


def load_claims(claims_path: Path) -> dict[str, ClaimEntry]:
    """Load claims map from disk.

    Missing claims file is treated as no active claims.
    """
    if not claims_path.exists():
        return {}
    with _locked_claims_file(claims_path):
        return _read_claims_file(claims_path)


def save_claims(claims: dict[str, ClaimEntry], claims_path: Path) -> None:
    """Persist claims map atomically with file lock protection."""
    with _locked_claims_file(claims_path):
        _write_claims_file(claims_path, claims)


def cleanup_stale_claims(claims_path: Path, ttl_hours: float = 2.0) -> int:
    """Remove expired claim entries and return removed count."""
    with _locked_claims_file(claims_path):
        claims = _read_claims_file(claims_path)
        stale_keys = [key for key, entry in claims.items() if _is_stale(entry, ttl_hours)]
        if not stale_keys:
            return 0
        for key in stale_keys:
            claims.pop(key, None)
        _write_claims_file(claims_path, claims)
        return len(stale_keys)


def acquire_claim(step_id: str, branch: str, agent: str, claims_path: Path) -> bool:
    """Acquire a branch-scoped claim for a step.

    Raises:
        PlanError: If the claim key already exists and is not stale.
    """
    key = _claim_key(branch, step_id)
    with _locked_claims_file(claims_path):
        claims = _read_claims_file(claims_path)

        stale_keys = [k for k, v in claims.items() if _is_stale(v, ttl_hours=2.0)]
        for stale_key in stale_keys:
            claims.pop(stale_key, None)

        if key in claims:
            raise PlanError(f"Step '{step_id}' is already claimed on branch '{branch}'")

        claims[key] = ClaimEntry(
            step_id=step_id,
            branch=branch,
            agent=agent,
            claimed_at=_now_iso(),
        )
        _write_claims_file(claims_path, claims)
        return True


def release_claim(step_id: str, branch: str, claims_path: Path) -> bool:
    """Release a branch-scoped claim if present."""
    key = _claim_key(branch, step_id)
    with _locked_claims_file(claims_path):
        claims = _read_claims_file(claims_path)
        if key not in claims:
            return False
        claims.pop(key, None)
        _write_claims_file(claims_path, claims)
        return True


def get_current_branch() -> str:
    """Return current git branch name, or ``unknown`` if unavailable."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True,
            text=True,
        )
    except OSError:
        return "unknown"

    if result.returncode != 0:
        return "unknown"

    branch = result.stdout.strip()
    return branch or "unknown"
