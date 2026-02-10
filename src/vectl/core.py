"""Core logic: validation, state machine, checklist."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path

from vectl.models import (
    AmbiguousMatchError,
    DiffResult,
    GateCheckResult,
    NoMatchError,
    Phase,
    PhaseChange,
    PhaseProgress,
    PhaseStatus,
    Plan,
    PlanError,
    RejectionEntry,
    ReviewResult,
    SearchMatch,
    SkipReason,
    Step,
    StepChange,
    StepStatus,
    PlanValidationIssue,
)

# Lazy import to avoid circular — semantics imports models, core imports models.
# is_step_locked is only used in render, which is late-bound.
from vectl.semantics import is_step_locked as _is_step_locked_shared


# ---------------------------------------------------------------------------
# DAG Validation
# ---------------------------------------------------------------------------


def validate_plan(
    plan: Plan, *, check_refs: bool = False, base_path: Path | None = None
) -> list[PlanValidationIssue]:
    """Validate plan structure, DAG, and consistency.

    Args:
        plan: The plan to validate.
        check_refs: If True, check that files in refs[] exist on disk.
        base_path: Base path for resolving refs (defaults to cwd).
    """
    errors: list[PlanValidationIssue] = []

    # Phase ID uniqueness
    phase_ids: set[str] = set()
    for phase in plan.phases:
        if phase.id in phase_ids:
            errors.append(PlanValidationIssue(f"Duplicate phase ID: '{phase.id}'"))
        phase_ids.add(phase.id)

    # Phase DAG: valid depends_on refs
    for phase in plan.phases:
        for dep in phase.depends_on:
            if dep not in phase_ids:
                errors.append(
                    PlanValidationIssue(f"Phase '{phase.id}' depends on unknown phase '{dep}'")
                )

    # Phase DAG: cycle detection
    phase_cycle = _detect_cycle({p.id: p.depends_on for p in plan.phases})
    if phase_cycle:
        errors.append(PlanValidationIssue(f"Phase DAG cycle detected: {' → '.join(phase_cycle)}"))

    # Per-phase checks
    for phase in plan.phases:
        # Step ID uniqueness within phase
        step_ids: set[str] = set()
        for step in phase.steps:
            if step.id in step_ids:
                errors.append(
                    PlanValidationIssue(f"Duplicate step ID '{step.id}' in phase '{phase.id}'")
                )
            step_ids.add(step.id)

        # Step DAG: valid depends_on refs (within same phase)
        for step in phase.steps:
            for dep in step.depends_on:
                if dep not in step_ids:
                    errors.append(
                        PlanValidationIssue(
                            f"Step '{step.id}' depends on unknown step '{dep}' (phase '{phase.id}')"
                        )
                    )

        # Step DAG: cycle detection
        step_cycle = _detect_cycle({s.id: s.depends_on for s in phase.steps})
        if step_cycle:
            errors.append(
                PlanValidationIssue(
                    f"Step DAG cycle in phase '{phase.id}': {' → '.join(step_cycle)}"
                )
            )

        # Status consistency
        if phase.status == PhaseStatus.LOCKED:
            for step in phase.steps:
                if step.status not in (StepStatus.PENDING, StepStatus.SKIPPED):
                    errors.append(
                        PlanValidationIssue(
                            f"Step '{step.id}' has status '{step.status.value}' "
                            f"but phase '{phase.id}' is locked"
                        )
                    )

        # Phase done but steps not all done/skipped
        if phase.status == PhaseStatus.DONE:
            for step in phase.steps:
                if step.status not in (StepStatus.DONE, StepStatus.SKIPPED):
                    errors.append(
                        PlanValidationIssue(
                            f"Phase '{phase.id}' is done but step '{step.id}' "
                            f"has status '{step.status.value}'"
                        )
                    )

    # Refs check (optional)
    if check_refs and base_path is not None:
        for phase in plan.phases:
            for step in phase.steps:
                for ref in step.refs:
                    # Strip fragment (e.g., "file.md#section")
                    file_part = ref.split("#")[0]
                    if file_part and not (base_path / file_part).exists():
                        errors.append(
                            PlanValidationIssue(
                                f"Step '{step.id}' ref '{ref}' — file not found",
                                is_warning=True,
                            )
                        )

    return errors


def _detect_cycle(graph: dict[str, list[str]]) -> list[str] | None:
    """Detect a cycle in a directed graph. Returns cycle path or None."""
    WHITE, GRAY, BLACK = 0, 1, 2
    color: dict[str, int] = {node: WHITE for node in graph}
    parent: dict[str, str | None] = {node: None for node in graph}

    def dfs(node: str) -> list[str] | None:
        color[node] = GRAY
        for neighbor in graph.get(node, []):
            if neighbor not in color:
                continue
            if color[neighbor] == GRAY:
                # Found cycle — reconstruct path
                cycle = [neighbor, node]
                cur = node
                prev = parent[cur]
                while prev is not None and prev != neighbor:
                    cycle.append(prev)
                    cur = prev
                    prev = parent[cur]
                cycle.reverse()
                return cycle
            if color[neighbor] == WHITE:
                parent[neighbor] = node
                result = dfs(neighbor)
                if result:
                    return result
        color[node] = BLACK
        return None

    for node in graph:
        if color[node] == WHITE:
            result = dfs(node)
            if result:
                return result
    return None


# ---------------------------------------------------------------------------
# State Machine & Query Logic
# ---------------------------------------------------------------------------


def get_next_steps(plan: Plan) -> list[Step]:
    """Get all claimable steps across active phases.

    Returns steps that are:
    - In an active (pending/in_progress) phase whose dependencies are all done
    - Status is pending or rejected (rejected = needs rework, prioritized)
    - All step dependencies within the phase are done/skipped

    Rejected steps appear first (priority).
    """
    active_phase_ids = _get_active_phase_ids(plan)
    result: list[Step] = []

    for phase in plan.phases:
        if phase.id not in active_phase_ids:
            continue
        done_step_ids = {
            s.id for s in phase.steps if s.status in (StepStatus.DONE, StepStatus.SKIPPED)
        }
        for step in phase.steps:
            if step.status not in (StepStatus.PENDING, StepStatus.REJECTED):
                continue
            # All deps satisfied?
            if all(dep in done_step_ids for dep in step.depends_on):
                result.append(step)

    # Rejected first, then pending
    result.sort(key=lambda s: (0 if s.status == StepStatus.REJECTED else 1, s.id))
    return result


def get_claimed_steps(plan: Plan, agent: str | None = None) -> list[tuple[str, Step]]:
    """Get all currently claimed steps, optionally filtered by agent.

    Args:
        plan: The plan to query.
        agent: If provided, only return steps claimed by this agent.

    Returns:
        List of (phase_id, step) tuples for claimed steps.
    """
    results: list[tuple[str, Step]] = []
    for phase in plan.phases:
        for step in phase.steps:
            if step.status != StepStatus.CLAIMED:
                continue
            if agent is not None and step.claimed_by != agent:
                continue
            results.append((phase.id, step))
    return results


def claim_step(plan: Plan, step_id: str, agent_name: str) -> Plan:
    """Claim a step for work."""
    found = plan.find_step(step_id)
    if found is None:
        raise PlanError(f"Step '{step_id}' not found")
    phase, step = found

    if step.status not in (StepStatus.PENDING, StepStatus.REJECTED):
        raise PlanError(f"Step '{step_id}' cannot be claimed (status: {step.status.value})")

    # Check phase is active
    active_ids = _get_active_phase_ids(plan)
    if phase.id not in active_ids:
        raise PlanError(f"Step '{step_id}' is in inactive phase '{phase.id}'")

    # Check step deps
    done_ids = {s.id for s in phase.steps if s.status in (StepStatus.DONE, StepStatus.SKIPPED)}
    unmet = [dep for dep in step.depends_on if dep not in done_ids]
    if unmet:
        raise PlanError(f"Step '{step_id}' has unmet dependencies: {unmet}")

    step.status = StepStatus.CLAIMED
    step.claimed_by = agent_name
    step.claimed_at = datetime.now(timezone.utc).isoformat()

    # Auto-update phase to in_progress (handles both PENDING and LOCKED-but-eligible)
    if phase.status in (PhaseStatus.PENDING, PhaseStatus.LOCKED):
        phase.status = PhaseStatus.IN_PROGRESS

    return plan


def complete_step(plan: Plan, step_id: str, evidence: str) -> Plan:
    """Mark a step as done with evidence."""
    found = plan.find_step(step_id)
    if found is None:
        raise PlanError(f"Step '{step_id}' not found")
    phase, step = found

    if step.status != StepStatus.CLAIMED:
        raise PlanError(
            f"Step '{step_id}' cannot be completed (status: {step.status.value}, "
            f"must be claimed first)"
        )

    step.status = StepStatus.DONE
    step.evidence = evidence

    # Auto-update phase if all steps done/skipped
    if all(s.status in (StepStatus.DONE, StepStatus.SKIPPED) for s in phase.steps):
        phase.status = PhaseStatus.DONE
        # Cascade: unlock downstream phases whose deps are now satisfied
        auto_unlock_phases(plan)

    return plan


def complete_phase(plan: Plan, phase_id: str, evidence: str) -> tuple[Plan, list[str]]:
    """Mark a phase as DONE with evidence.

    This is intended for historical migration/import workflows where steps may
    already be terminal (done/skipped) without having been claimed.

    Source: Eidos migrate phase needs explicit phase completion to avoid
    dependency deadlocks during import (user instruction: extend vectl first,
    then migrate; plus expert recommendation to keep phase completion explicit).

    Args:
        plan: The plan to modify.
        phase_id: Phase ID to complete.
        evidence: Evidence/audit note for why the phase is considered complete.

    Returns:
        Tuple of (updated plan, list of phase IDs unlocked by this completion).

    Raises:
        PlanError: If phase not found, has unmet dependencies, or contains
            any non-terminal steps.
    """
    if not evidence:
        raise PlanError("Phase evidence is required for completion")

    phase = plan.find_phase(phase_id)
    if phase is None:
        raise PlanError(f"Phase '{phase_id}' not found")
    if phase.status == PhaseStatus.DONE:
        raise PlanError(f"Phase '{phase_id}' is already done")

    # Require explicit dependency completion to preserve lock semantics.
    unmet_deps: list[str] = []
    for dep_id in phase.depends_on:
        dep = plan.find_phase(dep_id)
        if dep is None or dep.status != PhaseStatus.DONE:
            unmet_deps.append(dep_id)
    if unmet_deps:
        raise PlanError(f"Phase '{phase_id}' depends_on phases not done: {unmet_deps}")

    non_terminal = [s for s in phase.steps if s.status not in (StepStatus.DONE, StepStatus.SKIPPED)]
    if non_terminal:
        details = [f"{s.id}({s.status.value})" for s in non_terminal]
        raise PlanError(f"Phase '{phase_id}' has non-terminal steps: {details}")

    phase.status = PhaseStatus.DONE
    phase.evidence = evidence

    unlocked = auto_unlock_phases(plan)
    return plan, unlocked


def defer_step(plan: Plan, step_id: str) -> Plan:
    """Return a claimed step to pending."""
    found = plan.find_step(step_id)
    if found is None:
        raise PlanError(f"Step '{step_id}' not found")
    _, step = found

    if step.status != StepStatus.CLAIMED:
        raise PlanError(f"Step '{step_id}' cannot be deferred (status: {step.status.value})")

    step.status = StepStatus.PENDING
    step.claimed_by = None
    step.claimed_at = None
    return plan


def reject_step(plan: Plan, step_id: str, reason: str, reviewer: str = "") -> Plan:
    """Reject a completed step, moving it back for rework."""
    found = plan.find_step(step_id)
    if found is None:
        raise PlanError(f"Step '{step_id}' not found")
    phase, step = found

    if step.status != StepStatus.DONE:
        raise PlanError(
            f"Step '{step_id}' cannot be rejected (status: {step.status.value}, must be done)"
        )

    step.status = StepStatus.REJECTED
    step.rejection_reason = reason
    step.rejection_history.append(
        RejectionEntry(
            reason=reason,
            timestamp=datetime.now(timezone.utc).isoformat(),
            reviewer=reviewer,
        )
    )
    step.evidence = None

    # Phase can't be done if a step is rejected
    if phase.status == PhaseStatus.DONE:
        phase.status = PhaseStatus.IN_PROGRESS

    return plan


def skip_step(plan: Plan, step_id: str, reason: str) -> Plan:
    """Skip a step with a reason.

    The reason must be a valid SkipReason value: superseded, irrelevant,
    absorbed, or deprioritized.
    """
    # Validate reason against enum
    valid_reasons = [r.value for r in SkipReason]
    if reason not in valid_reasons:
        raise PlanError(
            f"Invalid skip reason '{reason}'. Must be one of: {', '.join(valid_reasons)}"
        )

    found = plan.find_step(step_id)
    if found is None:
        raise PlanError(f"Step '{step_id}' not found")
    phase, step = found

    if step.status not in (StepStatus.PENDING, StepStatus.CLAIMED, StepStatus.REJECTED):
        raise PlanError(f"Step '{step_id}' cannot be skipped (status: {step.status.value})")

    step.status = StepStatus.SKIPPED
    step.skipped_reason = reason

    # Auto-update phase if all steps done/skipped
    if all(s.status in (StepStatus.DONE, StepStatus.SKIPPED) for s in phase.steps):
        phase.status = PhaseStatus.DONE
        # Cascade: unlock downstream phases whose deps are now satisfied
        auto_unlock_phases(plan)

    return plan


def skip_phase(plan: Plan, phase_id: str, reason: str) -> tuple[Plan, list[str]]:
    """Skip all remaining steps in a phase.

    For each step in the phase:
    - PENDING / REJECTED → SKIPPED with reason
    - CLAIMED → deferred to PENDING first, then SKIPPED
    - DONE / SKIPPED → left unchanged

    After processing, phase auto-completes if all steps are DONE/SKIPPED,
    triggering ``auto_unlock_phases`` for downstream phases.

    Args:
        plan: The plan to modify.
        phase_id: ID of the phase to skip.
        reason: Must be a valid SkipReason value.

    Returns:
        Tuple of (updated plan, list of step IDs that were skipped).

    Raises:
        PlanError: If phase not found, invalid reason, or phase is LOCKED/DONE.
    """
    # Validate reason against enum
    valid_reasons = [r.value for r in SkipReason]
    if reason not in valid_reasons:
        raise PlanError(
            f"Invalid skip reason '{reason}'. Must be one of: {', '.join(valid_reasons)}"
        )

    phase = plan.find_phase(phase_id)
    if phase is None:
        raise PlanError(f"Phase '{phase_id}' not found")

    if phase.status == PhaseStatus.LOCKED:
        raise PlanError(f"Phase '{phase_id}' is locked — unlock it first or wait for dependencies")

    if phase.status == PhaseStatus.DONE:
        raise PlanError(f"Phase '{phase_id}' is already done")

    skipped_ids: list[str] = []
    for step in phase.steps:
        if step.status in (StepStatus.DONE, StepStatus.SKIPPED):
            continue
        # Claimed → defer first
        if step.status == StepStatus.CLAIMED:
            step.claimed_by = None
            step.claimed_at = None
        # PENDING, REJECTED, or just-deferred CLAIMED → SKIPPED
        step.status = StepStatus.SKIPPED
        step.skipped_reason = reason
        skipped_ids.append(step.id)

    # Auto-update phase if all steps done/skipped
    if phase.steps and all(s.status in (StepStatus.DONE, StepStatus.SKIPPED) for s in phase.steps):
        phase.status = PhaseStatus.DONE
        auto_unlock_phases(plan)

    return plan, skipped_ids


def _get_active_phase_ids(plan: Plan) -> set[str]:
    """Get IDs of phases that are active or eligible (deps satisfied).

    Pure query — does NOT mutate phase status. Returns phase IDs where:
    - Status is PENDING or IN_PROGRESS, OR
    - Status is LOCKED but all dependencies are DONE (eligible for unlock).
    """
    done_phase_ids = {p.id for p in plan.phases if p.status == PhaseStatus.DONE}
    active: set[str] = set()
    for phase in plan.phases:
        if phase.status in (PhaseStatus.LOCKED,):
            # Eligible: locked but all deps done — include without mutating
            if phase.depends_on and all(dep in done_phase_ids for dep in phase.depends_on):
                active.add(phase.id)
            continue
        if phase.status in (PhaseStatus.PENDING, PhaseStatus.IN_PROGRESS):
            active.add(phase.id)
    return active


def auto_unlock_phases(plan: Plan) -> list[str]:
    """Explicitly unlock all eligible locked phases (LOCKED → PENDING).

    Mutator function — call when state changes should be persisted.
    Returns list of phase IDs that were unlocked.
    """
    done_phase_ids = {p.id for p in plan.phases if p.status == PhaseStatus.DONE}
    unlocked: list[str] = []
    for phase in plan.phases:
        if phase.status == PhaseStatus.LOCKED:
            if phase.depends_on and all(dep in done_phase_ids for dep in phase.depends_on):
                phase.status = PhaseStatus.PENDING
                unlocked.append(phase.id)
    return unlocked


# ---------------------------------------------------------------------------
# Slug Generation
# ---------------------------------------------------------------------------


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


def _unique_step_id(phase: Phase, base_slug: str) -> str:
    """Generate a unique step ID within a phase.

    Format: ``{phase.id}.{slug}``. Appends ``-2``, ``-3`` on collision.
    """
    existing = {s.id for s in phase.steps}
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


# ---------------------------------------------------------------------------
# Plan Mutation: add_step / add_phase
# ---------------------------------------------------------------------------


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
    refs: list[str] | None = None,
    status: StepStatus | None = None,
    evidence: str | None = None,
    skipped_reason: str | None = None,
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
        refs: Reference file paths.
        status: Initial status. Only terminal states allowed: pending (default),
            done, skipped. Transient states (claimed, rejected) are not supported
            because they require runtime metadata (claimed_by, rejection_history).
        evidence: Evidence string. Required when status is done.
        skipped_reason: Skip reason. Required when status is skipped.

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
        step_id = _unique_step_id(phase, slug)
    else:
        if any(s.id == step_id for s in phase.steps):
            raise PlanError(f"Step ID '{step_id}' already exists in phase '{phase_id}'")

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
        refs=refs or [],
        evidence=evidence,
        skipped_reason=skipped_reason,
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
    known_ids: set[str] = {s.id for s in phase.steps}  # track collisions
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
                )
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

        # Generate or validate step ID
        step_id_raw = entry.get("id")
        if step_id_raw is not None:
            step_id = str(step_id_raw)
            if step_id in known_ids:
                raise PlanError(f"Step ID '{step_id}' already exists in phase '{phase_id}'")
        else:
            slug = _slugify(name)
            if not slug:
                raise PlanError(f"Step {i}: Cannot generate step ID: name produces empty slug")
            # Generate unique ID considering both existing steps and earlier batch entries
            candidate = f"{phase_id}.{slug}"
            if candidate not in known_ids:
                step_id = candidate
            else:
                counter = 2
                while f"{phase_id}.{slug}-{counter}" in known_ids:
                    counter += 1
                step_id = f"{phase_id}.{slug}-{counter}"

        known_ids.add(step_id)

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
                if dep_ref in known_ids:
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


# ---------------------------------------------------------------------------
# Architect: Edit, Remove, Move Step
# ---------------------------------------------------------------------------


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
    add_deps: list[str] | None = None,
    remove_deps: list[str] | None = None,
    depends_on: list[str] | _Unset = _SENTINEL,
) -> Plan:
    """Edit an existing step's metadata.

    Uses sentinel default so callers can distinguish "not provided" from "set to empty".

    Args:
        plan: The plan to modify.
        step_id: ID of the step to edit.
        name: New name (unchanged if not provided).
        description: New description (unchanged if not provided).
        verification: New verification command (unchanged if not provided).
        add_deps: Step IDs to add to depends_on.
        remove_deps: Step IDs to remove from depends_on.
        depends_on: Set dependencies directly (overrides add_deps/remove_deps if provided).

    Returns:
        The updated plan.

    Raises:
        PlanError: If step not found, or add_deps reference invalid step IDs.
    """
    found = plan.find_step(step_id)
    if found is None:
        raise PlanError(f"Step '{step_id}' not found")
    phase, step = found

    if name is not _SENTINEL:
        step.name = str(name)
    if description is not _SENTINEL:
        step.description = str(description)
    if verification is not _SENTINEL:
        step.verification = str(verification)

    if depends_on is not _SENTINEL:
        # Validate dependencies
        existing_step_ids = {s.id for s in phase.steps}
        # Note: depends_on is typed as list[str] | _Unset, cast needed for mypy/runtime check
        new_deps = list(depends_on)  # type: ignore
        for dep in new_deps:
            if dep not in existing_step_ids:
                raise PlanError(f"Step depends_on '{dep}' not found in phase '{phase.id}'")
        step.depends_on = new_deps
        return plan

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

    return plan


def edit_phase(
    plan: Plan,
    phase_id: str,
    *,
    name: str | _Unset = _SENTINEL,
    context: str | _Unset = _SENTINEL,
    gate: str | _Unset = _SENTINEL,
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
        add_deps: Phase IDs to add to depends_on.
        remove_deps: Phase IDs to remove from depends_on.

    Returns:
        The updated plan.

    Raises:
        PlanError: If phase not found, or add_deps reference invalid phase IDs.
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
    found = plan.find_step(step_id)
    if found is None:
        raise PlanError(f"Step '{step_id}' not found")
    phase, step = found

    if step.status != StepStatus.PENDING:
        raise PlanError(
            f"Cannot remove step '{step_id}' with status '{step.status.value}' (must be pending)"
        )

    # Check no other step depends on this one
    dependents = [s for s in phase.steps if step_id in s.depends_on]
    if dependents and not force:
        dep_ids = [s.id for s in dependents]
        raise PlanError(f"Cannot remove step '{step_id}': step '{dep_ids[0]}' depends on it")

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
    found = plan.find_step(step_id)
    if found is None:
        raise PlanError(f"Step '{step_id}' not found")
    from_phase, step = found

    if step.status != StepStatus.PENDING:
        raise PlanError(
            f"Cannot move step '{step_id}' with status '{step.status.value}' (must be pending)"
        )

    target = plan.find_phase(to_phase_id)
    if target is None:
        raise PlanError(f"Target phase '{to_phase_id}' not found")

    if from_phase.id == to_phase_id:
        raise PlanError(f"Step '{step_id}' is already in phase '{to_phase_id}'")

    # Check no other step in source phase depends on this one
    for s in from_phase.steps:
        if step_id in s.depends_on:
            raise PlanError(f"Cannot move step '{step_id}': step '{s.id}' depends on it")

    # Remove from source, clear deps (phase-scoped), add to target
    from_phase.steps = [s for s in from_phase.steps if s.id != step_id]
    step.depends_on = []
    target.steps.append(step)

    return plan


# ---------------------------------------------------------------------------
# Checklist Operations
# ---------------------------------------------------------------------------


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

    found = plan.find_step(step_id)
    if found is None:
        raise PlanError(f"Step '{step_id}' not found")
    _, step = found

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


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------


def search_plan(
    plan: Plan,
    pattern: str,
    *,
    phase_id: str | None = None,
    state: str | None = None,
    use_regex: bool = False,
) -> list[SearchMatch]:
    """Search across phases and steps for a pattern.

    Searches phase context/gate and step name/description/context fields.
    Returns matches grouped by phase, in plan order.

    Args:
        plan: The plan to search.
        pattern: Search pattern (substring or regex).
        phase_id: Restrict to a single phase.
        state: Filter by phase or step status (e.g. 'pending', 'done').
        use_regex: Treat pattern as regex instead of case-insensitive substring.
    """
    if use_regex:
        try:
            compiled = re.compile(pattern, re.IGNORECASE)
        except re.error as e:
            raise PlanError(f"Invalid regex pattern: {e}") from e

        def match_fn(text: str) -> bool:
            return compiled.search(text) is not None
    else:
        lower_pattern = pattern.lower()

        def match_fn(text: str) -> bool:
            return lower_pattern in text.lower()

    results: list[SearchMatch] = []

    for phase in plan.phases:
        if phase_id is not None and phase.id != phase_id:
            continue
        if state is not None and phase.status.value != state:
            # Phase-level state filter — but still search steps if they match
            pass

        # Search phase-level fields
        phase_state_ok = state is None or phase.status.value == state
        if phase_state_ok:
            for field_name, field_val in [("context", phase.context), ("gate", phase.gate)]:
                if field_val and match_fn(field_val):
                    # Extract matching line as snippet
                    snippet = _extract_snippet(field_val, pattern, use_regex)
                    results.append(
                        SearchMatch(
                            phase_id=phase.id,
                            step_id=None,
                            field=field_name,
                            snippet=snippet,
                        )
                    )

        # Search step-level fields
        for step in phase.steps:
            if state is not None and step.status.value != state:
                continue
            for field_name, field_val in [
                ("name", step.name),
                ("description", step.description),
                ("verification", step.verification),
            ]:
                if field_val and match_fn(field_val):
                    snippet = _extract_snippet(field_val, pattern, use_regex)
                    results.append(
                        SearchMatch(
                            phase_id=phase.id,
                            step_id=step.id,
                            field=field_name,
                            snippet=snippet,
                        )
                    )

    return results


def _extract_snippet(text: str, pattern: str, use_regex: bool, max_len: int = 80) -> str:
    """Extract the line containing the match, truncated to max_len."""
    if use_regex:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            # Find the line containing the match
            start = text.rfind("\n", 0, match.start()) + 1
            end = text.find("\n", match.end())
            if end == -1:
                end = len(text)
            line = text[start:end].strip()
            if len(line) > max_len:
                return line[: max_len - 1] + "…"
            return line
    else:
        lower_text = text.lower()
        lower_pattern = pattern.lower()
        idx = lower_text.find(lower_pattern)
        if idx >= 0:
            start = text.rfind("\n", 0, idx) + 1
            end = text.find("\n", idx)
            if end == -1:
                end = len(text)
            line = text[start:end].strip()
            if len(line) > max_len:
                return line[: max_len - 1] + "…"
            return line
    # Fallback: first line
    first_line = text.strip().split("\n")[0]
    if len(first_line) > max_len:
        return first_line[: max_len - 1] + "…"
    return first_line


# ---------------------------------------------------------------------------
# Review & Gate Check (pure data, no formatting)
# ---------------------------------------------------------------------------


def review_plan(
    plan: Plan,
    *,
    check_refs: bool = False,
    base_path: Path | None = None,
    include_done: bool = False,
) -> ReviewResult:
    """Compute structured review data for a plan.

    Args:
        plan: The plan to review.
        check_refs: Whether to check that ref files exist on disk.
        base_path: Base directory for ref file checks.
        include_done: Include DONE/LOCKED phases in active_phases list.

    Returns:
        ReviewResult with validation issues, phase progress, active phases,
        and spec coverage reverse index.
    """
    issues = validate_plan(plan, check_refs=check_refs, base_path=base_path)

    progress: list[PhaseProgress] = []
    total_done = 0
    total_steps = 0
    for ph in plan.phases:
        done = sum(1 for s in ph.steps if s.status in (StepStatus.DONE, StepStatus.SKIPPED))
        total = len(ph.steps)
        total_done += done
        total_steps += total
        progress.append(
            PhaseProgress(
                phase_id=ph.id,
                name=ph.name,
                status=ph.status,
                done=done,
                total=total,
            )
        )

    active_phases = [
        ph
        for ph in plan.phases
        if ph.status in (PhaseStatus.PENDING, PhaseStatus.IN_PROGRESS)
        or (include_done and ph.status in (PhaseStatus.DONE, PhaseStatus.LOCKED))
    ]

    ref_index: dict[str, list[str]] = {}
    for ph in plan.phases:
        for step in ph.steps:
            for ref in step.refs:
                ref_index.setdefault(ref, []).append(step.id)

    return ReviewResult(
        validation_issues=issues,
        phase_progress=progress,
        active_phases=active_phases,
        ref_index=ref_index,
        total_done=total_done,
        total_steps=total_steps,
    )


def gate_check(plan: Plan, phase_id: str) -> GateCheckResult:
    """Check gate readiness for a phase (pure data, no subprocess execution).

    Args:
        plan: The plan containing the phase.
        phase_id: ID of the phase to check.

    Returns:
        GateCheckResult with step completion status, pending steps,
        gate criterion, and downstream locked phases.

    Raises:
        PlanError: If phase_id is not found.
    """
    ph = plan.find_phase(phase_id)
    if ph is None:
        raise PlanError(f"Phase '{phase_id}' not found.")

    total = len(ph.steps)
    done = sum(1 for s in ph.steps if s.status in (StepStatus.DONE, StepStatus.SKIPPED))
    pending = [s for s in ph.steps if s.status not in (StepStatus.DONE, StepStatus.SKIPPED)]

    downstream = [
        dp.id for dp in plan.phases if phase_id in dp.depends_on and dp.status == PhaseStatus.LOCKED
    ]

    return GateCheckResult(
        phase_id=ph.id,
        phase_name=ph.name,
        steps_complete=len(pending) == 0,
        done_count=done,
        total_count=total,
        pending_steps=pending,
        gate_criterion=ph.gate,
        gate_script=ph.gate_script,
        downstream_locked=downstream,
    )


# ---------------------------------------------------------------------------
# Render (yaml → markdown export)
# ---------------------------------------------------------------------------

_PHASE_ICON = {
    PhaseStatus.LOCKED: "🔒",
    PhaseStatus.PENDING: "○",
    PhaseStatus.IN_PROGRESS: "▶",
    PhaseStatus.DONE: "✓",
}

_STEP_ICON = {
    StepStatus.PENDING: "○",
    StepStatus.CLAIMED: "◉",
    StepStatus.DONE: "✓",
    StepStatus.SKIPPED: "⊘",
    StepStatus.REJECTED: "✗",
}


def render_plan(plan: Plan, phase_id: str | None = None) -> str:
    """Render plan as Markdown stakeholder report.

    Deliberately less detail than review_plan: omits claimed_by, claimed_at,
    rejection_history. Shows phase progress, step status, and one-line
    description summaries.

    Args:
        plan: The plan to render.
        phase_id: If provided, render only this phase.

    Returns:
        Markdown string.

    Raises:
        PlanError: If phase_id is provided but not found.
    """
    if phase_id is not None:
        ph = plan.find_phase(phase_id)
        if ph is None:
            raise PlanError(f"Phase '{phase_id}' not found.")
        return _render_phase(plan, ph)

    lines: list[str] = [f"# {plan.project}\n"]

    # Summary table
    lines.append("| Status | Phase | Name | Progress |")
    lines.append("|--------|-------|------|----------|")
    for ph in plan.phases:
        done = sum(1 for s in ph.steps if s.status in (StepStatus.DONE, StepStatus.SKIPPED))
        total = len(ph.steps)
        pct = (done / total * 100) if total > 0 else 0
        icon = _PHASE_ICON.get(ph.status, "?")
        lines.append(
            f"| {icon} {ph.status.value} | {ph.id} | {ph.name} | {done}/{total} ({pct:.0f}%) |"
        )
    lines.append("")

    # Per-phase detail
    for ph in plan.phases:
        lines.append(_render_phase(plan, ph))

    return "\n".join(lines)


def _render_phase(plan: Plan, ph: Phase) -> str:
    """Render a single phase as Markdown section.

    Uses shared ``is_step_locked`` from *semantics* so lock icons are
    consistent across CLI, MCP, and ``render_plan`` output.
    """
    done = sum(1 for s in ph.steps if s.status in (StepStatus.DONE, StepStatus.SKIPPED))
    total = len(ph.steps)
    pct = (done / total * 100) if total > 0 else 0
    icon = _PHASE_ICON.get(ph.status, "?")

    lines: list[str] = [f"## {icon} {ph.id} — {ph.name} ({done}/{total}, {pct:.0f}%)\n"]

    if ph.context:
        lines.append(f"> {ph.context.strip()}\n")
    if ph.gate:
        lines.append(f"**Gate:** {ph.gate}\n")

    for step in ph.steps:
        si = _STEP_ICON.get(step.status, "?")
        if _is_step_locked_shared(plan, ph, step):
            si = "🔒"
        summary = _first_line(step.description)
        suffix = f" — {summary}" if summary else ""
        lines.append(f"- {si} **{step.id}** {step.name}{suffix}")

    lines.append("")
    return "\n".join(lines)


def _first_line(text: str, max_len: int = 72) -> str:
    """Extract first non-empty line, truncated."""
    for line in text.strip().splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("- ["):
            continue
        if len(stripped) > max_len:
            return stripped[: max_len - 1] + "…"
        return stripped
    return ""


# ---------------------------------------------------------------------------
# Diff (compare two plan states)
# ---------------------------------------------------------------------------


def diff_plans(old: Plan, new: Plan) -> DiffResult:
    """Compare two plan states and produce a structured diff.

    Pure function: no I/O, no git. Callers provide the two Plan objects.

    Args:
        old: Previous plan state.
        new: Current plan state.

    Returns:
        DiffResult with phase and step changes.
    """
    phase_changes: list[PhaseChange] = []
    step_changes: list[StepChange] = []

    old_phases = {p.id: p for p in old.phases}
    new_phases = {p.id: p for p in new.phases}

    # Phase-level changes
    for pid in new_phases:
        if pid not in old_phases:
            np = new_phases[pid]
            phase_changes.append(PhaseChange(phase_id=pid, phase_name=np.name, kind="added"))
        else:
            op, np = old_phases[pid], new_phases[pid]
            if op.status != np.status:
                phase_changes.append(
                    PhaseChange(
                        phase_id=pid,
                        phase_name=np.name,
                        kind="status_changed",
                        old_status=op.status,
                        new_status=np.status,
                    )
                )

    for pid in old_phases:
        if pid not in new_phases:
            op = old_phases[pid]
            phase_changes.append(PhaseChange(phase_id=pid, phase_name=op.name, kind="removed"))

    # Step-level changes
    old_steps: dict[str, tuple[str, Step]] = {}
    for p in old.phases:
        for s in p.steps:
            old_steps[s.id] = (p.id, s)

    new_steps: dict[str, tuple[str, Step]] = {}
    for p in new.phases:
        for s in p.steps:
            new_steps[s.id] = (p.id, s)

    for sid in new_steps:
        npid, ns = new_steps[sid]
        if sid not in old_steps:
            step_changes.append(
                StepChange(
                    step_id=sid,
                    step_name=ns.name,
                    phase_id=npid,
                    kind="added",
                    new_status=ns.status,
                )
            )
        else:
            opid, os_ = old_steps[sid]
            if os_.status != ns.status:
                step_changes.append(
                    StepChange(
                        step_id=sid,
                        step_name=ns.name,
                        phase_id=npid,
                        kind="status_changed",
                        old_status=os_.status,
                        new_status=ns.status,
                    )
                )
            elif os_.name != ns.name or os_.description != ns.description:
                detail_parts: list[str] = []
                if os_.name != ns.name:
                    detail_parts.append(f"name: {os_.name!r} → {ns.name!r}")
                if os_.description != ns.description:
                    detail_parts.append("description changed")
                step_changes.append(
                    StepChange(
                        step_id=sid,
                        step_name=ns.name,
                        phase_id=npid,
                        kind="modified",
                        detail="; ".join(detail_parts),
                    )
                )

    for sid in old_steps:
        if sid not in new_steps:
            opid, os_ = old_steps[sid]
            step_changes.append(
                StepChange(
                    step_id=sid,
                    step_name=os_.name,
                    phase_id=opid,
                    kind="removed",
                    old_status=os_.status,
                )
            )

    return DiffResult(phase_changes=phase_changes, step_changes=step_changes)
