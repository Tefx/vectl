"""Plan mutation, lifecycle, clipboard, agents-md, and recovery behavior."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from pathlib import Path
from typing import NamedTuple

from vectl import claims as _claims
from vectl import lifecycle as _lifecycle
from vectl.io import load_plan_definition, save_plan
from vectl.models import (
    AmbiguousMatchError, Clipboard, DiffResult, DuplicateStepIdApplyResult,
    DuplicateStepIdClaimConflict, DuplicateStepIdDependsOnRewrite,
    DuplicateStepIdDiagnostic, DuplicateStepIdDiagnostics, DuplicateStepIdDryRunReport,
    DuplicateStepIdGroup, DuplicateStepIdMigrationEvidence, DuplicateStepIdPhaseRef,
    DuplicateStepIdRenameEntry, DuplicateStepIdRepairRecommendation,
    DuplicateStepIdResolutionPath, DuplicateStepIdRetryEvidence, GateCheckResult,
    NoMatchError, Phase, PhaseChange, PhaseProgress, PhaseStatus, Plan, PlanError,
    PlanIOError, PlanValidationIssue, ReviewResult, SearchMatch, Step, StepChange,
    StepStatus, format_step_selector,
)
from vectl.semantics import is_step_locked as _is_step_locked_shared

from vectl.core_duplicate_step_id import require_no_global_duplicate_step_id, require_unambiguous_target_step_id
from vectl.core_plan_queries import _detect_cycle, diff_plans

def claim_step(
    plan: Plan,
    step_id: str,
    agent_name: str,
    *,
    force: bool = False,
    claims_path: Path | None = None,
) -> tuple[Plan, _lifecycle.ClaimResult]:
    require_unambiguous_target_step_id(plan, step_id, operation="claim")
    return _lifecycle.claim_step(
        plan,
        step_id,
        agent_name,
        force=force,
        claims_path=claims_path,
    )


def complete_step(plan: Plan, step_id: str, evidence: str, claims_path: Path | None = None) -> Plan:
    require_unambiguous_target_step_id(plan, step_id, operation="complete")
    return _lifecycle.complete_step(plan, step_id, evidence, claims_path=claims_path)


def complete_phase(plan: Plan, phase_id: str, evidence: str) -> tuple[Plan, list[str]]:
    return _lifecycle.complete_phase(plan, phase_id, evidence)


def defer_step(plan: Plan, step_id: str, claims_path: Path | None = None) -> Plan:
    require_unambiguous_target_step_id(plan, step_id, operation="defer")
    return _lifecycle.defer_step(plan, step_id, claims_path=claims_path)


def reject_step(plan: Plan, step_id: str, reason: str, reviewer: str = "") -> Plan:
    require_unambiguous_target_step_id(plan, step_id, operation="reject")
    return _lifecycle.reject_step(plan, step_id, reason, reviewer)


def skip_step(plan: Plan, step_id: str, reason: str) -> Plan:
    require_unambiguous_target_step_id(plan, step_id, operation="skip")
    return _lifecycle.skip_step(plan, step_id, reason)


def skip_phase(
    plan: Plan, phase_id: str, reason: str, force: bool = False
) -> tuple[Plan, list[str]]:
    return _lifecycle.skip_phase(plan, phase_id, reason, force=force)


def get_claimed_steps(plan: Plan, agent: str | None = None) -> list[tuple[str, Step]]:
    return _lifecycle.get_claimed_steps(plan, agent)


def acquire_claim(step_id: str, branch: str, agent: str, claims_path: Path) -> bool:
    return _claims.acquire_claim(step_id, branch, agent, claims_path)


def cleanup_stale_claims(claims_path: Path, ttl_hours: float = 2.0) -> int:
    return _claims.cleanup_stale_claims(claims_path, ttl_hours=ttl_hours)


def get_current_branch() -> str:
    return _claims.get_current_branch()


def release_claim(step_id: str, branch: str, claims_path: Path) -> bool:
    return _claims.release_claim(step_id, branch, claims_path)


def get_claim_info(step_id: str, branch: str, claims_path: Path) -> _claims.ClaimEntry | None:
    return _claims.get_claim_info(step_id, branch, claims_path)


def _slugify(name: str) -> str:
    """Generate a kebab-case slug from a name.

    Lowercase, replace non-alphanumeric with hyphens, collapse, strip.

    >>> _slugify("Show Command")
    'show-command'
    >>> _slugify("Add Step & Add Steps (Bulk)")
    'add-step-add-steps-bulk'
    >>> _slugify("  hello  world  ")
    'hello-world'
    >>> _slugify("YAML IO with CAS")
    'yaml-io-with-cas'
    """
    slug = name.lower()
    slug = re.sub(r"[^a-z0-9]+", "-", slug)
    slug = slug.strip("-")
    return re.sub(r"-+", "-", slug)


def _unique_step_id(plan: Plan, phase: Phase, base_slug: str) -> str:
    """Generate a unique step ID across the full plan.

    Format: ``{phase.id}.{slug}``. Appends ``-2``, ``-3`` on collision.
    """
    existing: set[str] = set()
    for existing_phase in plan.phases:
        for step in existing_phase.steps:
            existing.add(step.id)
    candidate = f"{phase.id}.{base_slug}"
    if candidate not in existing:
        return candidate
    counter = 2
    while f"{phase.id}.{base_slug}-{counter}" in existing:
        counter += 1
    return f"{phase.id}.{base_slug}-{counter}"


def _unique_phase_id(plan: Plan, base_slug: str) -> str:
    """Generate a unique phase ID. Appends ``-2``, ``-3`` on collision."""
    existing = {p.id for p in plan.phases}
    if base_slug not in existing:
        return base_slug
    counter = 2
    while f"{base_slug}-{counter}" in existing:
        counter += 1
    return f"{base_slug}-{counter}"


_IMPORTABLE_STATUSES = frozenset({StepStatus.PENDING, StepStatus.DONE, StepStatus.SKIPPED})


def add_step(
    plan: Plan,
    phase_id: str,
    name: str,
    *,
    step_id: str | None = None,
    description: str = "",
    depends_on: list[str] | None = None,
    verification: str = "",
    evidence_template: str = "",
    refs: list[str] | None = None,
    status: StepStatus | None = None,
    evidence: str | None = None,
    skipped_reason: str | None = None,
    agent: str | None = None,
) -> tuple[Plan, str]:
    """Add a new step to a phase.

    Args:
        plan: The plan to modify.
        phase_id: ID of the phase to add the step to.
        name: Human-readable name for the step.
        step_id: Optional explicit ID. Auto-generated from name if None.
        description: Step description/specification.
        depends_on: Step IDs this step depends on (within same phase).
        verification: Verification command.
        evidence_template: Optional short template for completion evidence.
        refs: Reference file paths.
        status: Initial status. Only terminal states allowed: pending (default),
            done, skipped. Transient states (claimed, rejected) are not supported
            because they require runtime metadata (claimed_by, rejection_history).
        evidence: Evidence string. Required when status is done.
        skipped_reason: Skip reason. Required when status is skipped.
        agent: Advisory agent suggestion — which agent should work on this step.
            Not enforced; any agent can still claim any step.

    Returns:
        Tuple of (updated plan, generated step ID).

    Raises:
        PlanError: If phase not found, depends_on invalid, or status/evidence
            constraints violated.
    """
    phase = plan.find_phase(phase_id)
    if phase is None:
        raise PlanError(f"Phase '{phase_id}' not found")

    # Validate status constraints (terminal states only)
    effective_status = status or StepStatus.PENDING
    if effective_status not in _IMPORTABLE_STATUSES:
        raise PlanError(
            f"Cannot add step with status '{effective_status.value}': "
            f"only 'pending', 'done', and 'skipped' are allowed "
            f"(claimed/rejected require runtime metadata)"
        )
    if effective_status == StepStatus.DONE and not evidence:
        raise PlanError(
            "Cannot add step with status 'done' without evidence "
            "(for historical imports, describe the original verification source)"
        )
    if effective_status == StepStatus.SKIPPED and not skipped_reason:
        raise PlanError("Cannot add step with status 'skipped' without skipped_reason")

    # Locked phases allow adding steps (for planning), but not claiming them.

    # Generate or validate step ID
    if step_id is None:
        slug = _slugify(name)
        if not slug:
            raise PlanError("Cannot generate step ID: name produces empty slug")
        step_id = _unique_step_id(plan, phase, slug)
    else:
        require_no_global_duplicate_step_id(plan, step_id, operation="add-step")

    # Validate depends_on refs exist within phase
    deps = depends_on or []
    existing_step_ids = {s.id for s in phase.steps}
    for dep in deps:
        if dep not in existing_step_ids:
            raise PlanError(f"Step depends_on '{dep}' not found in phase '{phase_id}'")

    step = Step(
        id=step_id,
        name=name,
        status=effective_status,
        description=description,
        depends_on=deps,
        verification=verification,
        evidence_template=evidence_template,
        refs=refs or [],
        evidence=evidence,
        skipped_reason=skipped_reason,
        agent=agent,
    )
    phase.steps.append(step)

    # If phase was DONE and new step is not terminal, reopen.
    # Import of done/skipped steps does NOT auto-complete the phase —
    # import is a setup operation, not workflow advancement.
    if phase.status == PhaseStatus.DONE and effective_status not in (
        StepStatus.DONE,
        StepStatus.SKIPPED,
    ):
        phase.status = PhaseStatus.IN_PROGRESS

    return plan, step_id


def add_phase(
    plan: Plan,
    name: str,
    *,
    phase_id: str | None = None,
    depends_on: list[str] | None = None,
    gate: str = "",
    context: str = "",
) -> tuple[Plan, str]:
    """Add a new phase to the plan.

    Args:
        plan: The plan to modify.
        name: Human-readable name for the phase.
        phase_id: Optional explicit ID. Auto-generated from name if None.
        depends_on: Phase IDs this phase depends on.
        gate: Gate criterion text.
        context: Phase context/description.

    Returns:
        Tuple of (updated plan, generated phase ID).

    Raises:
        PlanError: If depends_on refs invalid or duplicate ID.
    """
    if phase_id is None:
        slug = _slugify(name)
        if not slug:
            raise PlanError("Cannot generate phase ID: name produces empty slug")
        phase_id = _unique_phase_id(plan, slug)
    else:
        if any(p.id == phase_id for p in plan.phases):
            raise PlanError(f"Phase ID '{phase_id}' already exists")

    # Validate depends_on refs
    deps = depends_on or []
    existing_phase_ids = {p.id for p in plan.phases}
    for dep in deps:
        if dep not in existing_phase_ids:
            raise PlanError(f"Phase depends_on '{dep}' not found")

    # Determine initial status
    if deps:
        done_phase_ids = {p.id for p in plan.phases if p.status == PhaseStatus.DONE}
        if all(dep in done_phase_ids for dep in deps):
            initial_status = PhaseStatus.PENDING
        else:
            initial_status = PhaseStatus.LOCKED
    else:
        initial_status = PhaseStatus.PENDING

    phase = Phase(
        id=phase_id,
        name=name,
        status=initial_status,
        gate=gate,
        context=context,
        depends_on=deps,
    )
    plan.phases.append(phase)

    return plan, phase_id


def add_steps_bulk(
    plan: Plan,
    phase_id: str,
    steps: list[dict[str, object]],
) -> tuple[Plan, list[str]]:
    """Add multiple steps to a phase in batch with two-pass reference resolution.

    Each entry in ``steps`` is a dict with keys matching add_step parameters:
    ``name`` (required), ``desc``, ``after`` (list of step IDs or short slugs),
    ``verify``, ``refs`` (list of paths), ``id`` (explicit step ID),
    ``status`` (pending/done/skipped), ``evidence`` (required when done),
    ``skipped_reason`` (required when skipped).

    Two-pass processing allows steps in the same batch to reference each other
    via short slug names (e.g. ``"step-a"`` instead of ``"p1.step-a"``).

    Pass 1: Parse entries, validate status constraints, and generate step IDs.
    Pass 2: Resolve dependencies (short slugs → full IDs) and add steps.
    Atomic rollback if validation fails (e.g. cycle detected).

    Import semantics: Phase status is NOT automatically updated when importing
    done/skipped steps. Import is a setup operation, not workflow advancement.
    Phase will only reopen if pending steps are added to a DONE phase.

    Args:
        plan: The plan to modify.
        phase_id: ID of the phase to add steps to.
        steps: List of step definition dicts.

    Returns:
        Tuple of (updated plan, list of generated step IDs).

    Raises:
        PlanError: If phase not found, step definitions invalid, deps invalid,
            status constraints violated, or dependency cycle detected.
    """
    phase = plan.find_phase(phase_id)
    if phase is None:
        raise PlanError(f"Phase '{phase_id}' not found")

    if not steps:
        return plan, []

    # --- Pass 1: Parse entries and pre-compute all IDs ---
    parsed: list[dict[str, object]] = []
    existing_global_ids: set[str] = set()
    for existing_phase in plan.phases:
        for existing_step in existing_phase.steps:
            existing_global_ids.add(existing_step.id)

    used_ids = set(existing_global_ids)
    batch_ids: set[str] = set()

    known_phase_ids: set[str] = {s.id for s in phase.steps}
    slug_to_id: dict[str, str] = {}  # short slug → full step ID

    for i, entry in enumerate(steps):
        name = entry.get("name")
        if not name or not isinstance(name, str):
            raise PlanError(f"Step {i}: 'name' is required and must be a string")

        desc = entry.get("desc", "")
        if not isinstance(desc, str):
            raise PlanError(f"Step {i}: 'desc' must be a string")

        after_raw = entry.get("after")
        after_list: list[str] | None = None
        if after_raw is not None:
            if isinstance(after_raw, list):
                after_list = [str(d) for d in after_raw]
            elif isinstance(after_raw, str):
                after_list = [d.strip() for d in after_raw.split(",") if d.strip()]
            else:
                raise PlanError(f"Step {i}: 'after' must be a list or comma-separated string")

        verify = entry.get("verify", "")
        if not isinstance(verify, str):
            raise PlanError(f"Step {i}: 'verify' must be a string")

        refs_raw = entry.get("refs")
        refs: list[str] | None = None
        if refs_raw is not None:
            if isinstance(refs_raw, list):
                refs = [str(r) for r in refs_raw]
            else:
                raise PlanError(f"Step {i}: 'refs' must be a list")

        # Parse and validate status (terminal states only)
        status_raw = entry.get("status")
        step_status = StepStatus.PENDING
        if status_raw is not None:
            if not isinstance(status_raw, str):
                raise PlanError(f"Step {i}: 'status' must be a string")
            try:
                step_status = StepStatus(status_raw)
            except ValueError:
                raise PlanError(
                    f"Step {i}: invalid status '{status_raw}'. "
                    f"Must be one of: pending, done, skipped"
                ) from None
            if step_status not in _IMPORTABLE_STATUSES:
                raise PlanError(
                    f"Step {i}: cannot add step with status '{step_status.value}': "
                    f"only 'pending', 'done', and 'skipped' are allowed "
                    f"(claimed/rejected require runtime metadata)"
                )

        evidence_raw = entry.get("evidence")
        step_evidence: str | None = None
        if evidence_raw is not None:
            if not isinstance(evidence_raw, str):
                raise PlanError(f"Step {i}: 'evidence' must be a string")
            step_evidence = evidence_raw

        skipped_reason_raw = entry.get("skipped_reason")
        step_skipped_reason: str | None = None
        if skipped_reason_raw is not None:
            if not isinstance(skipped_reason_raw, str):
                raise PlanError(f"Step {i}: 'skipped_reason' must be a string")
            step_skipped_reason = skipped_reason_raw

        # Validate status-dependent constraints
        if step_status == StepStatus.DONE and not step_evidence:
            raise PlanError(
                f"Step {i}: cannot add step with status 'done' without evidence "
                f"(for historical imports, describe the original verification source)"
            )
        if step_status == StepStatus.SKIPPED and not step_skipped_reason:
            raise PlanError(
                f"Step {i}: cannot add step with status 'skipped' without skipped_reason"
            )

        # Parse agent (optional advisory field)
        agent_raw = entry.get("agent")
        step_agent: str | None = None
        if agent_raw is not None:
            if not isinstance(agent_raw, str):
                raise PlanError(f"Step {i}: 'agent' must be a string")
            step_agent = agent_raw

        # Generate or validate step ID
        step_id_raw = entry.get("id")
        if step_id_raw is not None:
            step_id = str(step_id_raw)
            require_no_global_duplicate_step_id(plan, step_id, operation="add-steps")
            if step_id in batch_ids:
                raise PlanError(
                    "duplicate_step_id_write_guard: blocked\n"
                    "error_code=duplicate_step_id_write_blocked\n"
                    "blocking_reason=duplicate_step_id_exists_in_batch\n"
                    "operation=add-steps\n"
                    f"step_id={step_id}\n"
                    f"phases={phase_id}"
                )
        else:
            slug = _slugify(name)
            if not slug:
                raise PlanError(f"Step {i}: Cannot generate step ID: name produces empty slug")
            # Generate unique ID considering all existing and earlier batch entries.
            candidate = f"{phase_id}.{slug}"
            if candidate not in used_ids:
                step_id = candidate
            else:
                counter = 2
                while f"{phase_id}.{slug}-{counter}" in used_ids:
                    counter += 1
                step_id = f"{phase_id}.{slug}-{counter}"

        used_ids.add(step_id)
        batch_ids.add(step_id)
        known_phase_ids.add(step_id)

        # Build short slug mapping for intra-batch references
        prefix = f"{phase_id}."
        short_slug = step_id[len(prefix) :] if step_id.startswith(prefix) else step_id
        slug_to_id[short_slug] = step_id

        parsed.append(
            {
                "name": name,
                "step_id": step_id,
                "desc": desc,
                "after_list": after_list,
                "verify": verify,
                "refs": refs,
                "status": step_status,
                "evidence": step_evidence,
                "skipped_reason": step_skipped_reason,
                "agent": step_agent,
            }
        )

    # --- Pass 2: Resolve dependencies and create steps ---
    generated_ids: list[str] = []

    for i, p_entry in enumerate(parsed):
        after_raw_val = p_entry["after_list"]
        resolved_deps: list[str] = []

        if after_raw_val is not None:
            assert isinstance(after_raw_val, list)
            for dep_ref in after_raw_val:
                assert isinstance(dep_ref, str)
                if dep_ref in known_phase_ids:
                    # Exact match: full step ID (existing or batch)
                    resolved_deps.append(dep_ref)
                elif dep_ref in slug_to_id:
                    # Short slug match from batch
                    resolved_deps.append(slug_to_id[dep_ref])
                else:
                    raise PlanError(
                        f"Step {i}: dependency '{dep_ref}' not found in phase '{phase_id}' or batch"
                    )

        step_name = p_entry["name"]
        assert isinstance(step_name, str)
        step_desc = p_entry["desc"]
        assert isinstance(step_desc, str)
        step_verify = p_entry["verify"]
        assert isinstance(step_verify, str)
        step_id_val = p_entry["step_id"]
        assert isinstance(step_id_val, str)
        step_refs = p_entry["refs"]
        assert step_refs is None or isinstance(step_refs, list)
        p_status = p_entry["status"]
        assert isinstance(p_status, StepStatus)
        p_evidence = p_entry["evidence"]
        assert p_evidence is None or isinstance(p_evidence, str)
        p_skipped_reason = p_entry["skipped_reason"]
        assert p_skipped_reason is None or isinstance(p_skipped_reason, str)
        p_agent = p_entry["agent"]
        assert p_agent is None or isinstance(p_agent, str)

        step = Step(
            id=step_id_val,
            name=step_name,
            status=p_status,
            description=step_desc,
            depends_on=resolved_deps,
            verification=step_verify,
            refs=step_refs or [],
            evidence=p_evidence,
            skipped_reason=p_skipped_reason,
            agent=p_agent,
        )
        phase.steps.append(step)
        generated_ids.append(step_id_val)

    # --- Validate step DAG (cycle check) ---
    step_graph = {s.id: s.depends_on for s in phase.steps}
    cycle = _detect_cycle(step_graph)
    if cycle:
        # Atomic rollback: remove all steps added in this batch
        added_set = set(generated_ids)
        phase.steps = [s for s in phase.steps if s.id not in added_set]
        raise PlanError(f"Step dependency cycle in batch: {' → '.join(cycle)}")

    # Phase reopen: only if pending steps are added to a DONE phase.
    # Import of done/skipped steps does NOT auto-complete the phase.
    if phase.status == PhaseStatus.DONE:
        added_set = set(generated_ids)
        has_non_terminal = any(
            s.status not in (StepStatus.DONE, StepStatus.SKIPPED)
            for s in phase.steps
            if s.id in added_set
        )
        if has_non_terminal:
            phase.status = PhaseStatus.IN_PROGRESS

    return plan, generated_ids


class _Unset(Enum):
    """Sentinel for distinguishing 'not provided' from 'set to empty string'."""

    TOKEN = "UNSET"


_SENTINEL = _Unset.TOKEN


def edit_step(
    plan: Plan,
    step_id: str,
    *,
    name: str | _Unset = _SENTINEL,
    description: str | _Unset = _SENTINEL,
    verification: str | _Unset = _SENTINEL,
    evidence_template: str | _Unset = _SENTINEL,
    agent: str | None | _Unset = _SENTINEL,
    add_deps: list[str] | None = None,
    remove_deps: list[str] | None = None,
    depends_on: list[str] | _Unset = _SENTINEL,
    add_refs: list[str] | None = None,
    remove_refs: list[str] | None = None,
    refs: list[str] | _Unset = _SENTINEL,
    new_step_id: str | _Unset = _SENTINEL,
) -> Plan:
    """Edit an existing step's metadata.

    Uses sentinel default so callers can distinguish "not provided" from "set to empty".

    Args:
        plan: The plan to modify.
        step_id: ID of the step to edit.
        name: New name (unchanged if not provided).
        description: New description (unchanged if not provided).
        verification: New verification command (unchanged if not provided).
        evidence_template: New completion evidence template (unchanged if not provided).
        agent: New agent suggestion. Pass None to clear, string to set.
        add_deps: Step IDs to add to depends_on.
        remove_deps: Step IDs to remove from depends_on.
        depends_on: Set dependencies directly (overrides add_deps/remove_deps if provided).
        add_refs: Reference strings to add to refs.
        remove_refs: Reference strings to remove from refs.
        refs: Set refs directly (overrides add_refs/remove_refs if provided).

    Returns:
        The updated plan.

    Raises:
        PlanError: If step not found, or add_deps reference invalid step IDs.
    """
    require_unambiguous_target_step_id(plan, step_id, operation="edit-step")
    found = plan.find_step(step_id)
    if found is None:
        raise PlanError(f"Step '{step_id}' not found")
    phase, step = found

    if new_step_id is not _SENTINEL:
        target_id = str(new_step_id)
        if target_id != step.id:
            require_no_global_duplicate_step_id(plan, target_id, operation="edit-step")
            old_id = step.id
            step.id = target_id
            for dep_step in phase.steps:
                dep_step.depends_on = [
                    target_id if dep == old_id else dep for dep in dep_step.depends_on
                ]

    if name is not _SENTINEL:
        step.name = str(name)
    if description is not _SENTINEL:
        step.description = str(description)
    if verification is not _SENTINEL:
        step.verification = str(verification)
    if evidence_template is not _SENTINEL:
        step.evidence_template = str(evidence_template)
    if agent is not _SENTINEL:
        step.agent = agent if agent is None else str(agent)  # type: ignore[assignment]

    original_depends_on: list[str] | None = None
    if depends_on is not _SENTINEL or add_deps or remove_deps:
        original_depends_on = list(step.depends_on)

    if depends_on is not _SENTINEL:
        # Validate dependencies
        existing_step_ids = {s.id for s in phase.steps}
        # Note: depends_on is typed as list[str] | _Unset, cast needed for mypy/runtime check
        new_deps = list(depends_on)  # type: ignore
        for dep in new_deps:
            if dep not in existing_step_ids:
                raise PlanError(f"Step depends_on '{dep}' not found in phase '{phase.id}'")
        step.depends_on = new_deps

    if add_deps:
        existing_step_ids = {s.id for s in phase.steps}
        for dep in add_deps:
            if dep not in existing_step_ids:
                raise PlanError(f"Step depends_on '{dep}' not found in phase '{phase.id}'")
            if dep not in step.depends_on:
                step.depends_on.append(dep)

    if remove_deps:
        for dep in remove_deps:
            if dep in step.depends_on:
                step.depends_on.remove(dep)

    # Cycle detection: check when depends_on/add_deps/remove_deps mutations occurred
    if depends_on is not _SENTINEL or add_deps or remove_deps:
        step_graph = {s.id: s.depends_on for s in phase.steps}
        cycle = _detect_cycle(step_graph)
        if cycle:
            if original_depends_on is not None:
                step.depends_on = original_depends_on
            cycle_str = " → ".join(f"{phase.id}.{s}" for s in cycle)
            raise PlanError(f"Step dependency cycle: {cycle_str}")

    if refs is not _SENTINEL:
        step.refs = list(refs)  # type: ignore

    if add_refs:
        for r in add_refs:
            if r not in step.refs:
                step.refs.append(r)

    if remove_refs:
        for r in remove_refs:
            if r in step.refs:
                step.refs.remove(r)

    return plan


def edit_phase(
    plan: Plan,
    phase_id: str,
    *,
    name: str | _Unset = _SENTINEL,
    context: str | _Unset = _SENTINEL,
    gate: str | _Unset = _SENTINEL,
    depends_on: list[str] | _Unset = _SENTINEL,
    add_deps: list[str] | None = None,
    remove_deps: list[str] | None = None,
) -> Plan:
    """Edit an existing phase's metadata.

    Args:
        plan: The plan to modify.
        phase_id: ID of the phase to edit.
        name: New name (unchanged if not provided).
        context: New context (unchanged if not provided).
        gate: New gate criterion (unchanged if not provided).
        depends_on: Set dependencies directly (overrides add_deps/remove_deps if provided).
        add_deps: Phase IDs to add to depends_on.
        remove_deps: Phase IDs to remove from depends_on.

    Returns:
        The updated plan.

    Raises:
        PlanError: If phase not found, or dependencies reference invalid phase IDs.
    """
    phase = plan.find_phase(phase_id)
    if phase is None:
        raise PlanError(f"Phase '{phase_id}' not found")

    if name is not _SENTINEL:
        phase.name = str(name)
    if context is not _SENTINEL:
        phase.context = str(context)
    if gate is not _SENTINEL:
        phase.gate = str(gate)

    if depends_on is not _SENTINEL:
        existing_phase_ids = {p.id for p in plan.phases}
        new_deps = list(depends_on)  # type: ignore[arg-type]
        for dep in new_deps:
            if dep not in existing_phase_ids:
                raise PlanError(f"Phase depends_on '{dep}' not found")
            if dep == phase_id:
                raise PlanError(f"Phase '{phase_id}' cannot depend on itself")
        phase.depends_on = new_deps

    if add_deps:
        existing_phase_ids = {p.id for p in plan.phases}
        for dep in add_deps:
            if dep not in existing_phase_ids:
                raise PlanError(f"Phase depends_on '{dep}' not found")
            if dep == phase_id:
                raise PlanError(f"Phase '{phase_id}' cannot depend on itself")
            if dep not in phase.depends_on:
                phase.depends_on.append(dep)

    if remove_deps:
        for dep in remove_deps:
            if dep in phase.depends_on:
                phase.depends_on.remove(dep)

    return plan


def edit_plan(
    plan: Plan,
    *,
    project_guidance: str | _Unset = _SENTINEL,
    strategy_ref: str | _Unset = _SENTINEL,
    context: str | _Unset = _SENTINEL,
) -> Plan:
    """Edit plan-level metadata.

    Source:
        - User instruction in this conversation (2026-02-12): "No manual YAML edits",
          and "sync CLI and MCP" for claim-time guidance authoring.

    Uses the existing sentinel pattern so callers can distinguish "not provided"
    from "set to empty".

    Args:
        plan: The plan to modify.
        project_guidance: Short project-level guidance shown at claim time.
        strategy_ref: Strategy reference shown as a top-level ref.
        context: Plan context string.

    Returns:
        The updated plan.
    """

    if project_guidance is not _SENTINEL:
        plan.project_guidance = str(project_guidance)
    if strategy_ref is not _SENTINEL:
        plan.strategy_ref = str(strategy_ref)
    if context is not _SENTINEL:
        plan.context = str(context)
    return plan


def remove_step(plan: Plan, step_id: str, *, force: bool = False) -> Plan:
    """Remove a step from its phase.

    Only pending steps can be removed (no in-flight work loss).

    Args:
        plan: The plan to modify.
        step_id: ID of the step to remove.
        force: If True, also clean up dependency references from other steps.

    Returns:
        The updated plan.

    Raises:
        PlanError: If step not found or step is not pending.
    """
    require_unambiguous_target_step_id(plan, step_id, operation="remove-step")
    found = plan.find_step(step_id)
    if found is None:
        raise PlanError(f"Step '{step_id}' not found")
    phase, step = found
    qualified_id = format_step_selector(phase.id, step.id)

    if step.status != StepStatus.PENDING:
        raise PlanError(
            f"Cannot remove step '{qualified_id}' with status "
            f"'{step.status.value}' (must be pending)"
        )

    # Check no other step depends on this one
    dependents = [s for s in phase.steps if step_id in s.depends_on]
    if dependents and not force:
        dep_ids = [s.id for s in dependents]
        raise PlanError(f"Cannot remove step '{qualified_id}': step '{dep_ids[0]}' depends on it")

    # Force: clean up dependency refs
    if force:
        for s in phase.steps:
            if step_id in s.depends_on:
                s.depends_on.remove(step_id)

    phase.steps = [s for s in phase.steps if s.id != step_id]
    return plan


def unlock_phase(plan: Plan, phase_id: str) -> Plan:
    """Explicitly unlock a locked phase by validating all dependencies are done.

    This provides a manual transition from LOCKED → PENDING with validation.
    For automatic unlocking, use auto_unlock_phases().

    Args:
        plan: The plan to modify.
        phase_id: ID of the phase to unlock.

    Returns:
        The updated plan.

    Raises:
        PlanError: If phase not found, not locked, or dependencies not met.
    """
    phase = plan.find_phase(phase_id)
    if phase is None:
        raise PlanError(f"Phase '{phase_id}' not found")

    if phase.status != PhaseStatus.LOCKED:
        raise PlanError(f"Phase '{phase_id}' is not locked (status: {phase.status.value})")

    # Validate all dependencies are done
    done_phase_ids = {p.id for p in plan.phases if p.status == PhaseStatus.DONE}
    unmet = [dep for dep in phase.depends_on if dep not in done_phase_ids]
    if unmet:
        raise PlanError(f"Cannot unlock phase '{phase_id}': dependencies not done: {unmet}")

    phase.status = PhaseStatus.PENDING
    return plan


def move_step(plan: Plan, step_id: str, to_phase_id: str) -> Plan:
    """Move a step from one phase to another.

    Only pending steps can be moved. Deps are cleared (they're phase-scoped).

    Args:
        plan: The plan to modify.
        step_id: ID of the step to move.
        to_phase_id: Target phase ID.

    Returns:
        The updated plan.

    Raises:
        PlanError: If step/phase not found, step not pending, or target is same phase.
    """
    require_unambiguous_target_step_id(plan, step_id, operation="move-step")
    found = plan.find_step(step_id)
    if found is None:
        raise PlanError(f"Step '{step_id}' not found")
    from_phase, step = found
    qualified_id = format_step_selector(from_phase.id, step.id)

    if step.status != StepStatus.PENDING:
        raise PlanError(
            f"Cannot move step '{qualified_id}' with status '{step.status.value}' (must be pending)"
        )

    target = plan.find_phase(to_phase_id)
    if target is None:
        raise PlanError(f"Target phase '{to_phase_id}' not found")

    if from_phase.id == to_phase_id:
        raise PlanError(f"Step '{qualified_id}' is already in phase '{to_phase_id}'")

    # Check no other step in source phase depends on this one
    for s in from_phase.steps:
        if step_id in s.depends_on:
            raise PlanError(f"Cannot move step '{qualified_id}': step '{s.id}' depends on it")

    # Remove from source, clear deps (phase-scoped), add to target
    from_phase.steps = [s for s in from_phase.steps if s.id != step_id]
    step.depends_on = []
    target.steps.append(step)

    return plan


_CHECKLIST_RE = re.compile(r"^(\s*-\s*\[)([ xX])(\]\s*.+)$", re.MULTILINE)


def update_checklist(
    plan: Plan, step_id: str, *, check: str | None = None, append: str | None = None
) -> Plan:
    """Update a checklist in a step's description.

    Args:
        plan: The plan.
        step_id: Step to update.
        check: Keyword to fuzzy-match and toggle a checklist item.
        append: Text for a new checklist item to add.
    """
    if check is None and append is None:
        raise PlanError("Must provide either 'check' or 'append'")

    require_unambiguous_target_step_id(plan, step_id, operation="check")
    found = plan.find_step(step_id)
    if found is None:
        raise PlanError(f"Step '{step_id}' not found")
    phase, step = found

    if check is not None:
        step.description = _toggle_checklist_item(step.description, check)

    if append is not None:
        # Add new unchecked item at end of description
        step.description = step.description.rstrip() + f"\n- [ ] {append}\n"

    return plan


def _toggle_checklist_item(description: str, keyword: str) -> str:
    """Toggle a checklist item matching keyword (case-insensitive substring)."""
    matches: list[tuple[int, re.Match[str]]] = []
    for i, match in enumerate(_CHECKLIST_RE.finditer(description)):
        item_text = match.group(3)  # e.g., "] Validation logic"
        if keyword.lower() in item_text.lower():
            matches.append((i, match))

    if len(matches) == 0:
        raise NoMatchError(keyword)

    if len(matches) > 1:
        candidates = [m.group(0).strip() for _, m in matches]
        raise AmbiguousMatchError(keyword, candidates)

    _, match = matches[0]
    current = match.group(2)
    new_mark = " " if current in ("x", "X") else "x"
    start, end = match.start(), match.end()
    replacement = f"{match.group(1)}{new_mark}{match.group(3)}"
    return description[:start] + replacement + description[end:]


CLIPBOARD_SUMMARY_MAX = 80


CLIPBOARD_CONTENT_MAX = 8000


CLIPBOARD_TTL_DEFAULT_HOURS = 24


def _clipboard_expired(cb: Clipboard) -> bool:
    """Check if a clipboard entry has expired."""
    try:
        expires = datetime.fromisoformat(cb.expires_at.replace("Z", "+00:00"))
        return datetime.now(timezone.utc) > expires
    except (ValueError, AttributeError):
        # Malformed timestamp - treat as expired
        return True


def clipboard_write(
    plan: Plan,
    author: str,
    summary: str,
    content: str,
    ttl: int = CLIPBOARD_TTL_DEFAULT_HOURS,
) -> Plan:
    """Write to the plan clipboard.

    Overwrites any existing clipboard content.

    Args:
        plan: The plan to modify.
        author: Who is writing (non-empty).
        summary: One-line description (truncated to 80 chars if longer).
        content: Payload (max 8000 chars, non-empty).
        ttl: Time-to-live in hours (default 24).

    Returns:
        Modified plan with clipboard set.

    Raises:
        PlanError: If author/content is empty, or content exceeds 8000 chars.

    >>> p = Plan(project="test")
    >>> p = clipboard_write(p, "agent-1", "Summary", "Content here", ttl=12)
    >>> p.clipboard is not None
    True
    >>> p.clipboard.author
    'agent-1'
    """
    # Validate author
    if not author or not author.strip():
        raise PlanError("Clipboard author cannot be empty")

    # Validate content
    if not content or not content.strip():
        raise PlanError("Clipboard content cannot be empty or whitespace-only")

    if len(content) > CLIPBOARD_CONTENT_MAX:
        raise PlanError(
            f"Content exceeds {CLIPBOARD_CONTENT_MAX} char limit ({len(content)} chars). "
            "Shorten or split into step evidence."
        )

    # Truncate summary (soft limit - agents are bad at counting)
    if len(summary) > CLIPBOARD_SUMMARY_MAX:
        summary = summary[: CLIPBOARD_SUMMARY_MAX - 1] + "…"

    # Compute timestamps
    now = datetime.now(timezone.utc)
    expires = now + timedelta(hours=ttl)

    plan.clipboard = Clipboard(
        author=author.strip(),
        summary=summary,
        content=content,
        written_at=now.isoformat().replace("+00:00", "Z"),
        expires_at=expires.isoformat().replace("+00:00", "Z"),
    )

    return plan


def clipboard_clear(plan: Plan) -> Plan:
    """Clear the plan clipboard.

    Args:
        plan: The plan to modify.

    Returns:
        Modified plan with clipboard cleared.

    >>> p = clipboard_write(Plan(project="test"), "a", "s", "c")
    >>> p.clipboard is not None
    True
    >>> p = clipboard_clear(p)
    >>> p.clipboard is None
    True
    """
    plan.clipboard = None
    return plan


_AGENTS_MD_LEGACY_HEADER = "## Plan Tracking (vectl)"


_AGENTS_MD_BEGIN = "<!-- VECTL:AGENTS:BEGIN -->"


_AGENTS_MD_END = "<!-- VECTL:AGENTS:END -->"


_AGENTS_MD_SNIPPET = f"""\
{_AGENTS_MD_BEGIN}
## Plan Tracking (vectl)

vectl tracks this repo's implementation plan as a structured `plan.yaml`:
what to do next, who claimed it, and what counts as done (with verification evidence).

Full guide: `vectl_guide` (CLI fallback: `vectl guide`)
Quick view: `vectl_status` (CLI fallback: `vectl status`)

### MCP vs CLI
- Source of truth: `plan.yaml` (channel-agnostic).
- **Always prefer MCP tools** (`vectl_status`, `vectl_claim`, `vectl_complete`, etc.) when available.
- CLI fallback priority: `uv run vectl` > `vectl` > `uvx vectl`.
- Evidence requirements are identical across MCP and CLI.

### Claim-time Guidance
- `vectl claim` may emit a bounded Guidance block delimited by:
  - `--- VECTL:GUIDANCE:BEGIN ---`
  - `--- VECTL:GUIDANCE:END ---`
- For automation/CI: use `vectl claim --no-guidance` to keep stdout clean.

### plan.yaml — Managed File (DO NOT EDIT DIRECTLY)

`plan.yaml` is exclusively owned by vectl. Direct edits (Edit, Write, sed, or
any file tool) **will** corrupt plan state — vectl performs CAS writes, lock
recalculation, and schema validation on every save, none of which run on direct
edits.

**To modify plan state, ONLY use:**
- MCP (preferred): `vectl_claim`, `vectl_complete`, `vectl_mutate`, etc.
- CLI (fallback): `uv run vectl claim`, `vectl claim`, or `uvx vectl claim`, etc.

If a vectl command fails, report the error — do **not** edit `plan.yaml`
directly as a workaround. Use `vectl guide stuck` for troubleshooting.

### Rules
- One claimed step at a time.
- Evidence is mandatory when completing (commands run + outputs + gaps).
- Spec uncertainty: leave `# SPEC QUESTION: ...` in code, do not guess.

### Step ID Uniqueness
**Step IDs must be globally unique across ALL phases.**
- Example: `auth.login` and `api.login` are different step IDs.
- Example: Using just `login` in two phases creates a duplicate — not allowed.
- If you have legacy duplicate step IDs, use `vectl migrate-step-id --dry-run`
  to preview and `--yes` to repair.

### For Architects / Planners
- **Design Mode**: Run `vectl_guide` (CLI fallback: `vectl guide --on planning`) to learn the Architect Protocol.
- **Ambiguity = Failure**: Workers will hallucinate if steps are vague.
- **Constraint Tools**:
  - `--evidence-template`: Force workers to provide specific proof (e.g., "Paste logs here").
  - `--refs`: Pin specific files (e.g., "src/auth.py") to the worker's context.
{_AGENTS_MD_END}
"""


class AgentsTarget(str, Enum):
    """Target file for the vectl agents-md section."""

    auto = "auto"
    agents = "agents"
    claude = "claude"


def detect_agents_target(directory: Path, target: AgentsTarget = AgentsTarget.auto) -> Path:
    """Detect the best target file for the vectl agents-md section.

    Args:
        directory: Project directory to scan.
        target: Explicit override. ``auto`` uses detection heuristics.

    Priority (when ``auto``):
    1. Existing file with vectl markers → use it (stability over detection).
    2. Existing file without markers → prefer AGENTS.md > CLAUDE.md.
    3. Neither exists → .claude/ dir present → CLAUDE.md; otherwise AGENTS.md.

    Returns:
        Path to the target file (may not exist yet).
    """
    agents_md = directory / "AGENTS.md"
    claude_md = directory / "CLAUDE.md"

    if target is AgentsTarget.agents:
        return agents_md
    if target is AgentsTarget.claude:
        return claude_md

    # Auto mode: existing file with markers wins (don't break working setups)
    for candidate in (agents_md, claude_md):
        if candidate.exists():
            content = candidate.read_text(encoding="utf-8")
            if _AGENTS_MD_BEGIN in content:
                return candidate

    # Existing file without markers (append target)
    if agents_md.exists():
        return agents_md
    if claude_md.exists():
        return claude_md

    # Fresh project: auto-detect Claude Code projects
    if (directory / ".claude").is_dir():
        return claude_md

    return agents_md


def upsert_agents_md(directory: Path, target: AgentsTarget = AgentsTarget.auto) -> tuple[str, str]:
    """Create or upsert vectl section in AGENTS.md or CLAUDE.md.

    Safety policy (agreed in cli.py conversation, 2026-02-12):
    - If begin/end markers exist, replace that block.
    - If only legacy header exists (no markers), do not rewrite; append the new block.

    Target selection delegated to ``detect_agents_target()``.

    Args:
        directory: Project directory containing AGENTS.md/CLAUDE.md.
        target: Explicit target override or auto-detect.

    Returns:
        Tuple of (status_message, target_filename).
        Status message describes what was done.
    """
    target_path = detect_agents_target(directory, target)

    if not target_path.exists():
        target_path.write_text(_AGENTS_MD_SNIPPET, encoding="utf-8")
        return f"Created {target_path.name}", target_path.name

    content = target_path.read_text(encoding="utf-8")

    begin = content.find(_AGENTS_MD_BEGIN)
    end = content.find(_AGENTS_MD_END)
    if begin != -1 and end != -1 and begin < end:
        end_inclusive = end + len(_AGENTS_MD_END)
        new_content = content[:begin].rstrip() + "\n\n" + _AGENTS_MD_SNIPPET + "\n"
        new_content += content[end_inclusive:].lstrip()
        target_path.write_text(new_content, encoding="utf-8")
        return f"Updated {target_path.name} (replaced vectl block)", target_path.name

    if _AGENTS_MD_LEGACY_HEADER in content:
        with target_path.open("a", encoding="utf-8") as f:
            f.write("\n\n" + _AGENTS_MD_SNIPPET)
        return (
            f"Appended updated vectl block to {target_path.name} (legacy block preserved)",
            target_path.name,
        )

    with target_path.open("a", encoding="utf-8") as f:
        f.write("\n\n" + _AGENTS_MD_SNIPPET)
    return f"Appended vectl section to {target_path.name}", target_path.name


@dataclass
class RecoverResult:
    """Result of a recover operation."""

    ok: bool
    restored: bool
    diff: DiffResult
    diff_summary: str
    error: str | None = None


def preview_recovery(plan_path: Path, backup_path: Path) -> RecoverResult:
    """Preview recovery diff without writing to disk.

    Args:
        plan_path: Path to the current plan.yaml.
        backup_path: Path to backup plan file.

    Returns:
        RecoverResult with diff information and restored=False.

    Raises:
        PlanError: If backup file is not found or invalid.
        PlanIOError: If plan or backup cannot be loaded.
    """
    from vectl.io import load_plan_definition

    if not backup_path.exists():
        raise PlanError(f"Backup file not found: {backup_path}")

    # Load backup plan (convert non-IO validation errors for compatibility)
    try:
        backup_plan, _ = load_plan_definition(backup_path)
    except PlanIOError:
        raise
    except Exception as e:
        raise PlanError(f"Invalid backup file: {e}") from e

    current_plan, _ = load_plan_definition(plan_path)
    diff = diff_plans(current_plan, backup_plan)

    step_count = len(diff.step_changes)
    phase_count = len(diff.phase_changes)
    summary_parts: list[str] = []
    if step_count > 0:
        summary_parts.append(f"{step_count} step(s) changed")
    if phase_count > 0:
        summary_parts.append(f"{phase_count} phase(s) changed")
    if not summary_parts:
        summary_parts.append("No changes")

    return RecoverResult(
        ok=True,
        restored=False,
        diff=diff,
        diff_summary="Total changes: " + ", ".join(summary_parts),
    )


def apply_recovery(backup_path: Path, plan_path: Path) -> None:
    """Apply recovery by overwriting plan_path with backup contents.

    Args:
        backup_path: Path to backup plan file.
        plan_path: Destination plan path to overwrite.

    Raises:
        PlanError: If backup file is not found.
        PlanIOError: If backup cannot be loaded or destination cannot be written.
    """
    from vectl.io import load_plan_definition, save_plan

    if not backup_path.exists():
        raise PlanError(f"Backup file not found: {backup_path}")

    backup_plan, _ = load_plan_definition(backup_path)
    save_plan(backup_plan, plan_path)
