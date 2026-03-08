"""YAML I/O and CAS logic."""

from __future__ import annotations

import hashlib
import json
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
    PhaseState,
    Plan,
    PlanIOError,
    PlanState,
    StepState,
)
from vectl.plan_path import resolve_state_path

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


def load_state(path: Path | str) -> tuple[PlanState, str]:
    """Load plan state from JSON file.

    Deprecated: split-state is retired. See docs/ADR-unified-state.md.
    Will be removed after unified-state Phase 3.

    Returns `(PlanState, file_hash)` for CAS semantics.
    If the file does not exist, returns an empty state with empty hash.
    """
    path = Path(path)
    if not path.exists():
        return PlanState(plan_id=""), ""

    content = path.read_text(encoding="utf-8")
    file_hash = hashlib.sha256(content.encode()).hexdigest()
    try:
        raw = json.loads(content)
    except json.JSONDecodeError as e:
        raise PlanIOError(f"Invalid JSON in {path}: {e}") from e
    if not isinstance(raw, dict):
        raise PlanIOError(f"State file must be a JSON mapping, got {type(raw).__name__}")
    try:
        state = PlanState(**raw)
    except Exception as e:
        raise PlanIOError(f"Invalid state structure: {e}") from e
    return state, file_hash


def _state_to_json(state: PlanState) -> str:
    """Serialize state to deterministic JSON output for state.json.

    Deprecated: split-state is retired. See docs/ADR-unified-state.md.
    Will be removed after unified-state Phase 3.
    """
    payload = state.model_dump(mode="json", exclude_none=True)
    return json.dumps(payload, indent=2, ensure_ascii=False)


def _merge_state_for_cas_retry(base: PlanState, our_state: PlanState) -> PlanState:
    """Merge state updates for CAS retries using per-key LWW semantics.

    Deprecated: split-state is retired. See docs/ADR-unified-state.md.
    Will be removed after unified-state Phase 3.

    Last-writer-wins semantics are applied for step, phase, and clipboard keys:
    entries from `our_state` overwrite `base` values for matching keys.
    """
    merged_steps = dict(base.steps)
    merged_steps.update(our_state.steps)

    merged_phases = dict(base.phases)
    merged_phases.update(our_state.phases)

    return PlanState(
        plan_id=our_state.plan_id or base.plan_id,
        steps=merged_steps,
        phases=merged_phases,
        clipboard=our_state.clipboard,
    )


def save_state(
    state: PlanState, path: Path | str, expected_hash: str | None = None, max_retries: int = 3
) -> str:
    """Save state JSON atomically with CAS.

    Deprecated: split-state is retired. See docs/ADR-unified-state.md.
    Will be removed after unified-state Phase 3.

    Returns file hash on success.
    """
    path = Path(path)
    attempt = 0

    while True:
        current_hash = _file_hash(path) if path.exists() else ""
        if expected_hash is not None and path.exists() and expected_hash != current_hash:
            if attempt >= max_retries:
                raise CASConflictError(path)
            attempt += 1
            current_state, current_hash = load_state(path)
            state = _merge_state_for_cas_retry(current_state, state)
            expected_hash = current_hash
            continue

        data = _state_to_json(state)

        dir_ = path.parent
        dir_.mkdir(parents=True, exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(dir=str(dir_), suffix=".tmp")
        try:
            os.write(fd, data.encode("utf-8"))
            os.close(fd)
            os.replace(tmp_path, str(path))
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise

        return hashlib.sha256(data.encode()).hexdigest()


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


def load_plan(path: Path | str) -> tuple[Plan, str]:
    """Load plan from YAML file and merge companion state if present.

    Returns (Plan, file_hash) for CAS.

    If a companion ``state.json`` exists and contains mutable runtime state,
    the returned plan is merged with state values before returning.
    """
    path = Path(path)
    plan, file_hash = load_plan_definition(path)

    state, _ = load_state(resolve_state_path(path))
    has_state = (
        bool(state.steps)
        or bool(state.phases)
        or state.clipboard is not None
        or bool(state.plan_id)
    )
    if has_state:
        plan = merge_plan(plan, state)

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


def extract_state(plan: Plan) -> PlanState:
    """Extract mutable runtime state from a plan into a separate state object.

    Deprecated: split-state is retired. See docs/ADR-unified-state.md.
    Will be removed after unified-state Phase 3.
    """

    step_states = {
        step.id: StepState(
            status=step.status,
            claimed_by=step.claimed_by,
            claimed_at=step.claimed_at,
            done_at=step.done_at,
            evidence=step.evidence,
            skipped_reason=step.skipped_reason,
            rejection_reason=step.rejection_reason,
            rejection_history=step.rejection_history,
            affinity_override=step.affinity_override,
            affinity_override_by=step.affinity_override_by,
            affinity_override_at=step.affinity_override_at,
        )
        for phase in plan.phases
        for step in phase.steps
    }

    phase_states = {
        phase.id: PhaseState(status=phase.status, evidence=phase.evidence) for phase in plan.phases
    }

    return PlanState(
        plan_id=plan.plan_id or "",
        steps=step_states,
        phases=phase_states,
        clipboard=plan.clipboard,
    )


def strip_state(plan: Plan) -> Plan:
    """Return a copy of the plan with mutable runtime state reset to defaults.

    Deprecated: split-state is retired. See docs/ADR-unified-state.md.
    Will be removed after unified-state Phase 3.
    """

    stripped = plan.model_copy(deep=True)

    for i, phase in enumerate(stripped.phases):
        reset_phase = PhaseState()
        stripped_phase = phase.model_copy(
            update={
                "status": reset_phase.status,
                "evidence": reset_phase.evidence,
            }
        )
        for j, step in enumerate(stripped_phase.steps):
            reset_step = StepState()
            stripped_step = step.model_copy(
                update={
                    "status": reset_step.status,
                    "claimed_by": reset_step.claimed_by,
                    "claimed_at": reset_step.claimed_at,
                    "done_at": reset_step.done_at,
                    "evidence": reset_step.evidence,
                    "skipped_reason": reset_step.skipped_reason,
                    "rejection_reason": reset_step.rejection_reason,
                    "rejection_history": reset_step.rejection_history,
                    "affinity_override": reset_step.affinity_override,
                    "affinity_override_by": reset_step.affinity_override_by,
                    "affinity_override_at": reset_step.affinity_override_at,
                }
            )
            stripped_phase.steps[j] = stripped_step
        stripped.phases[i] = stripped_phase

    stripped.clipboard = None
    return stripped


def merge_plan(plan_def: Plan, state: PlanState) -> Plan:
    """Merge a state document into a plan definition.

    Deprecated: split-state is retired. See docs/ADR-unified-state.md.
    Will be removed after unified-state Phase 3.
    """

    merged = plan_def.model_copy(deep=True)

    if state.plan_id:
        merged = merged.model_copy(update={"plan_id": state.plan_id})

    for i, phase in enumerate(merged.phases):
        phase_state = state.phases.get(phase.id)
        if phase_state:
            merged_phase = phase.model_copy(
                update={
                    "status": phase_state.status,
                    "evidence": phase_state.evidence,
                }
            )
            merged.phases[i] = merged_phase

        for j, step in enumerate(merged.phases[i].steps):
            step_state = state.steps.get(step.id)
            if step_state:
                merged_step = step.model_copy(
                    update={
                        "status": step_state.status,
                        "claimed_by": step_state.claimed_by,
                        "claimed_at": step_state.claimed_at,
                        "done_at": step_state.done_at,
                        "evidence": step_state.evidence,
                        "skipped_reason": step_state.skipped_reason,
                        "rejection_reason": step_state.rejection_reason,
                        "rejection_history": step_state.rejection_history,
                        "affinity_override": step_state.affinity_override,
                        "affinity_override_by": step_state.affinity_override_by,
                        "affinity_override_at": step_state.affinity_override_at,
                    }
                )
                merged.phases[i].steps[j] = merged_step

    merged = merged.model_copy(update={"clipboard": state.clipboard})

    return merged


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
        # RFC: docs/RFC-affinity.md
        # Exclude default affinity fields for cleaner YAML.
        # affinity: None is already handled by exclude_none=True in _plan_to_dict.
        # affinity_override: False means "no override" → omit from YAML.
        elif k == "affinity_override" and v is False:
            continue
        elif k == "affinity_override_by" and v is None:
            continue
        elif k == "affinity_override_at" and v is None:
            continue
        # default_affinity: "suggested" is the default → omit from YAML
        elif k == "default_affinity" and v == "suggested":
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
