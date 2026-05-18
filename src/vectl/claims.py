"""Claims store I/O for ephemeral branch-scoped coordination."""

from __future__ import annotations

import enum
import fcntl
import json
import os
import subprocess
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager, suppress
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


__all__ = [
    "ClaimEntry",
    "RepairAction",
    "RepairClaimsResult",
    "RepairStatus",
    "acquire_claim",
    "cleanup_stale_claims",
    "get_claim_info",
    "get_current_branch",
    "load_claims",
    "load_claims_for_branch",
    "release_claim",
    "repair_claims",
    "resolve_claims_path",
    "save_claims",
]


# @shell_orchestration: pure key formatter remains in claims shell to preserve established string API
def _claim_key(branch: str, step_id: str):
    return f"{branch}:{step_id}"


# @shell_orchestration: timestamp helper remains in claims shell to preserve persisted claim format
def _now_iso():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


# @shell_orchestration: internal parser uses None sentinel expected by stale-claim semantics
# @shell_complexity: ISO parsing handles empty, Zulu, invalid, and naive timestamps
def _parse_iso8601(timestamp: str):
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


# @shell_orchestration: stale predicate remains in claims shell to preserve boolean API
def _is_stale(entry: ClaimEntry, ttl_hours: float):
    parsed = _parse_iso8601(entry.claimed_at)
    if parsed is None:
        return True
    return datetime.now(timezone.utc) - parsed > timedelta(hours=ttl_hours)


# @shell_orchestration: validation raises PlanError per public claims loading contract
# @shell_complexity: schema validation must distinguish top-level, key, value, and model errors
def _validate_claims_mapping(raw: object):
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


# @shell_orchestration: loader raises PlanError/OSError per existing persistence contract
def _read_claims_file(claims_path: Path):
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
        with suppress(OSError):
            os.unlink(tmp_path)
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


# @shell_orchestration: public compatibility requires returning Path, not Result
def resolve_claims_path(plan_path: Path):
    """Resolve claims.json location shared by linked worktrees when possible."""
    return _resolve_claims_path(plan_path)


# @shell_orchestration: public compatibility requires returning claims mapping, not Result
def load_claims(claims_path: Path):
    """Load claims map from disk.

    Missing claims file is treated as no active claims.
    """
    if not claims_path.exists():
        return {}
    with _locked_claims_file(claims_path):
        return _read_claims_file(claims_path)


# @shell_orchestration: public compatibility requires returning branch claim mapping, not Result
def load_claims_for_branch(claims_path: Path, branch: str):
    """Load only claims entries for one branch, keyed by step ID."""
    with _locked_claims_file(claims_path):
        claims = _read_claims_file(claims_path)

    by_step: dict[str, ClaimEntry] = {}
    for entry in claims.values():
        if entry.branch != branch:
            continue
        by_step.setdefault(entry.step_id, entry)
    return by_step


def save_claims(claims: dict[str, ClaimEntry], claims_path: Path) -> None:
    """Persist claims map atomically with file lock protection."""
    with _locked_claims_file(claims_path):
        _write_claims_file(claims_path, claims)


# @shell_orchestration: public compatibility requires returning removed count, not Result
def cleanup_stale_claims(claims_path: Path, ttl_hours: float = 2.0):
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


# @shell_orchestration: public compatibility requires ClaimEntry-or-None lookup semantics
def get_claim_info(step_id: str, branch: str, claims_path: Path):
    """Get claim entry for a specific step on a branch.

    Returns the ClaimEntry if it exists, regardless of staleness.
    This function does NOT filter by staleness - use this to check if ANY claim exists.
    The caller is responsible for deciding how to handle stale claims.
    """
    key = _claim_key(branch, step_id)
    with _locked_claims_file(claims_path):
        claims = _read_claims_file(claims_path)
        return claims.get(key)


# @shell_orchestration: public compatibility requires bool success and PlanError conflict semantics
def acquire_claim(step_id: str, branch: str, agent: str, claims_path: Path):
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


# @shell_orchestration: public compatibility requires bool released/not-found semantics
def release_claim(step_id: str, branch: str, claims_path: Path):
    """Release a branch-scoped claim if present."""
    key = _claim_key(branch, step_id)
    with _locked_claims_file(claims_path):
        claims = _read_claims_file(claims_path)
        if key not in claims:
            return False
        claims.pop(key, None)
        _write_claims_file(claims_path, claims)
        return True


# @shell_orchestration: public compatibility requires fallback branch string, not Result
def get_current_branch():
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


# @shell_orchestration: static policy text helper remains in claims shell for repair payload compatibility
def _repair_policy_text():
    return (
        "plan_precedence: for current branch, ensure claims entry exists iff "
        "step is claimed in plan; "
        "for matching keys, plan claimed_by/claimed_at overwrite stale claims fields; "
        "outside branch/scope entries are preserved byte-for-byte in memory"
    )


# @shell_orchestration: plan projection remains in claims shell to preserve repair_claims mapping API
def _desired_claims_for_branch(plan: Plan, branch: str):
    plan_steps = {step.id: step for phase in plan.phases for step in phase.steps}
    desired: dict[str, ClaimEntry] = {}
    for step in plan_steps.values():
        if step.status != StepStatus.CLAIMED or step.claimed_by is None:
            continue
        key = _claim_key(branch, step.id)
        desired[key] = ClaimEntry(
            step_id=step.id,
            branch=branch,
            agent=step.claimed_by,
            claimed_at=step.claimed_at or _now_iso(),
        )
    return desired


# @shell_orchestration: repair scope calculation remains local to claims reconciliation
def _scoped_repair_keys(
    claims: dict[str, ClaimEntry],
    desired_for_branch: dict[str, ClaimEntry],
    branch: str,
    step_id: str | None,
):
    if step_id is not None:
        return {_claim_key(branch, step_id)}
    return {key for key, entry in claims.items() if entry.branch == branch} | set(
        desired_for_branch.keys()
    )


# @shell_orchestration: mutating helper returns action list expected by repair_claims
# @shell_complexity: reconciliation must handle remove, restore, update, and no-op cases
def _apply_claim_repairs(
    claims: dict[str, ClaimEntry],
    desired_for_branch: dict[str, ClaimEntry],
    scoped_keys: set[str],
):
    actions: list[RepairAction] = []
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
    return actions


# @shell_orchestration: public repair API returns structured RepairClaimsResult and raises PlanError
def repair_claims(
    plan: Plan,
    plan_path: Path,
    claims_path: Path,
    *,
    dry_run: bool = False,
    step_id: str | None = None,
):
    """Reconcile claims.json against plan.yaml for current branch.

    Deterministic policy (task authority: claim-consistency-recovery.repair-claims-command):
    - plan.yaml is source of truth for claim-visible state
    - claims entries for current branch are repaired to match plan step status/owner
    - out-of-scope claims (other branches, or non-target steps in --step mode) are preserved
    """

    if step_id is not None and plan.find_step(step_id) is None:
        raise PlanError(f"Step '{step_id}' not found")

    policy = _repair_policy_text()

    branch = get_current_branch()
    missing_file = not claims_path.exists()
    desired_for_branch = _desired_claims_for_branch(plan, branch)

    with _locked_claims_file(claims_path):
        claims = _read_claims_file(claims_path)
        scoped_keys = _scoped_repair_keys(claims, desired_for_branch, branch, step_id)
        actions = _apply_claim_repairs(claims, desired_for_branch, scoped_keys)

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
