"""Claims store I/O for ephemeral branch-scoped coordination."""

from __future__ import annotations

import enum
import fcntl
import json
import os
import subprocess
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from pydantic import BaseModel

from vectl.models import Plan, PlanError, StepStatus
from vectl.plan_path import resolve_claims_path as _resolve_claims_path


class RepairStatus(str, enum.Enum):
    """Message types for repair operations.

    Provides consistent structured output for both MCP and CLI interfaces.
    """

    REPAIR_ATTEMPTED = "repair_attempted"
    REPAIR_SUCCEEDED = "repair_succeeded"
    REPAIR_SKIPPED = "repair_skipped"
    RETRY_EXHAUSTED = "retry_exhausted"


class ClaimEntry(BaseModel):
    """Single claim record keyed by ``<branch>:<step_id>``."""

    step_id: str
    branch: str
    agent: str
    claimed_at: str


@dataclass(frozen=True)
class RepairAction:
    """Single deterministic reconciliation action."""

    action: str
    key: str
    reason: str
    before: ClaimEntry | None
    after: ClaimEntry | None


@dataclass(frozen=True)
class RepairClaimsResult:
    """Result payload for `repair claims` reconciliation."""

    dry_run: bool
    branch: str
    plan_path: str
    claims_path: str
    policy: str
    step_scope: str | None
    missing_claims_file: bool
    changed: bool
    actions: tuple[RepairAction, ...]
    status: RepairStatus = RepairStatus.REPAIR_SUCCEEDED

    def to_dict(self) -> dict[str, object]:
        return {
            "dry_run": self.dry_run,
            "branch": self.branch,
            "plan_path": self.plan_path,
            "claims_path": self.claims_path,
            "policy": self.policy,
            "step_scope": self.step_scope,
            "missing_claims_file": self.missing_claims_file,
            "changed": self.changed,
            "status": self.status.value,
            "actions": [
                {
                    "action": action.action,
                    "key": action.key,
                    "reason": action.reason,
                    "before": action.before.model_dump(mode="json") if action.before else None,
                    "after": action.after.model_dump(mode="json") if action.after else None,
                }
                for action in self.actions
            ],
        }


def _claim_key(branch: str, step_id: str) -> str:
    return f"{branch}:{step_id}"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


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
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _is_stale(entry: ClaimEntry, ttl_hours: float) -> bool:
    parsed = _parse_iso8601(entry.claimed_at)
    if parsed is None:
        return True
    return datetime.now(timezone.utc) - parsed > timedelta(hours=ttl_hours)


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


def get_claim_info(step_id: str, branch: str, claims_path: Path) -> ClaimEntry | None:
    """Get claim entry for a specific step on a branch.

    Returns the ClaimEntry if it exists, regardless of staleness.
    This function does NOT filter by staleness - use this to check if ANY claim exists.
    The caller is responsible for deciding how to handle stale claims.
    """
    key = _claim_key(branch, step_id)
    with _locked_claims_file(claims_path):
        claims = _read_claims_file(claims_path)
        return claims.get(key)


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


def repair_claims(
    plan: Plan,
    plan_path: Path,
    claims_path: Path,
    *,
    dry_run: bool = False,
    step_id: str | None = None,
) -> RepairClaimsResult:
    """Reconcile claims.json against plan.yaml for current branch.

    Deterministic policy (task authority: claim-consistency-recovery.repair-claims-command):
    - plan.yaml is source of truth for claim-visible state
    - claims entries for current branch are repaired to match plan step status/owner
    - out-of-scope claims (other branches, or non-target steps in --step mode) are preserved
    """

    if step_id is not None and plan.find_step(step_id) is None:
        raise PlanError(f"Step '{step_id}' not found")

    policy = (
        "plan_precedence: for current branch, ensure claims entry exists iff "
        "step is claimed in plan; "
        "for matching keys, plan claimed_by/claimed_at overwrite stale claims fields; "
        "outside branch/scope entries are preserved byte-for-byte in memory"
    )

    branch = get_current_branch()
    missing_file = not claims_path.exists()
    plan_steps = {step.id: step for phase in plan.phases for step in phase.steps}

    desired_for_branch: dict[str, ClaimEntry] = {}
    for step in plan_steps.values():
        if step.status != StepStatus.CLAIMED:
            continue
        if step.claimed_by is None:
            continue
        key = _claim_key(branch, step.id)
        desired_for_branch[key] = ClaimEntry(
            step_id=step.id,
            branch=branch,
            agent=step.claimed_by,
            claimed_at=step.claimed_at or _now_iso(),
        )

    actions: list[RepairAction] = []

    with _locked_claims_file(claims_path):
        claims = _read_claims_file(claims_path)

        scoped_keys: set[str]
        if step_id is not None:
            scoped_keys = {_claim_key(branch, step_id)}
        else:
            scoped_keys = {key for key, entry in claims.items() if entry.branch == branch} | set(
                desired_for_branch.keys()
            )

        for key in sorted(scoped_keys):
            before = claims.get(key)
            desired = desired_for_branch.get(key)

            if before is not None and desired is None:
                claims.pop(key, None)
                actions.append(
                    RepairAction(
                        action="remove",
                        key=key,
                        reason="ghost_claim_or_non_claimed_step",
                        before=before,
                        after=None,
                    )
                )
                continue

            if before is None and desired is not None:
                claims[key] = desired
                actions.append(
                    RepairAction(
                        action="restore",
                        key=key,
                        reason="plan_claimed_missing_in_claims",
                        before=None,
                        after=desired,
                    )
                )
                continue

            if before is not None and desired is not None and before != desired:
                claims[key] = desired
                actions.append(
                    RepairAction(
                        action="update",
                        key=key,
                        reason="stale_claim_entry_plan_precedence",
                        before=before,
                        after=desired,
                    )
                )

        changed = len(actions) > 0
        if changed and not dry_run:
            _write_claims_file(claims_path, claims)

    # Determine repair status based on outcome
    if not actions:
        repair_status = RepairStatus.REPAIR_SUCCEEDED
    else:
        repair_status = RepairStatus.REPAIR_ATTEMPTED

    return RepairClaimsResult(
        dry_run=dry_run,
        branch=branch,
        plan_path=str(plan_path.resolve()),
        claims_path=str(claims_path.resolve()),
        policy=policy,
        step_scope=step_id,
        missing_claims_file=missing_file,
        changed=changed,
        actions=tuple(actions),
        status=repair_status,
    )
