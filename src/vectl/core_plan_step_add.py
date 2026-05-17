"""Phase and step creation helpers for plan mutation compatibility."""

from __future__ import annotations

import re

from vectl.core_duplicate_step_id import require_no_global_duplicate_step_id
from vectl.core_plan_queries import _detect_cycle
from vectl.models import Phase, PhaseStatus, Plan, PlanError, Step, StepStatus


def _slugify(name: str) -> str:
    """Generate a kebab-case slug from a name.

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


# @shell_complexity: Branches preserve collision probing semantics for globally unique phase-prefixed step ids.
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


# @shell_complexity: Branches preserve add-step validation for phase lookup, import statuses, dependency refs, duplicate IDs, and DONE-phase reopen semantics.
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
    """Add a new step to a phase."""
    phase = plan.find_phase(phase_id)
    if phase is None:
        raise PlanError(f"Phase '{phase_id}' not found")

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

    if step_id is None:
        slug = _slugify(name)
        if not slug:
            raise PlanError("Cannot generate step ID: name produces empty slug")
        step_id = _unique_step_id(plan, phase, slug)
    else:
        require_no_global_duplicate_step_id(plan, step_id, operation="add-step")

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

    if phase.status == PhaseStatus.DONE and effective_status not in (
        StepStatus.DONE,
        StepStatus.SKIPPED,
    ):
        phase.status = PhaseStatus.IN_PROGRESS

    return plan, step_id


# @shell_complexity: Branches preserve phase-id generation, dependency validation, duplicate rejection, and initial lock-state semantics.
def add_phase(
    plan: Plan,
    name: str,
    *,
    phase_id: str | None = None,
    depends_on: list[str] | None = None,
    gate: str = "",
    context: str = "",
) -> tuple[Plan, str]:
    """Add a new phase to the plan."""
    if phase_id is None:
        slug = _slugify(name)
        if not slug:
            raise PlanError("Cannot generate phase ID: name produces empty slug")
        phase_id = _unique_phase_id(plan, slug)
    else:
        if any(p.id == phase_id for p in plan.phases):
            raise PlanError(f"Phase ID '{phase_id}' already exists")

    deps = depends_on or []
    existing_phase_ids = {p.id for p in plan.phases}
    for dep in deps:
        if dep not in existing_phase_ids:
            raise PlanError(f"Phase depends_on '{dep}' not found")

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


# @shell_complexity: Branches preserve typed import parsing, status/evidence guards, duplicate protection, intra-batch refs, and cycle rollback semantics.
def add_steps_bulk(
    plan: Plan,
    phase_id: str,
    steps: list[dict[str, object]],
) -> tuple[Plan, list[str]]:
    """Add multiple steps to a phase in batch with two-pass reference resolution."""
    phase = plan.find_phase(phase_id)
    if phase is None:
        raise PlanError(f"Phase '{phase_id}' not found")

    if not steps:
        return plan, []

    parsed: list[dict[str, object]] = []
    existing_global_ids: set[str] = set()
    for existing_phase in plan.phases:
        for existing_step in existing_phase.steps:
            existing_global_ids.add(existing_step.id)

    used_ids = set(existing_global_ids)
    batch_ids: set[str] = set()

    known_phase_ids: set[str] = {s.id for s in phase.steps}
    slug_to_id: dict[str, str] = {}

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

        if step_status == StepStatus.DONE and not step_evidence:
            raise PlanError(
                f"Step {i}: cannot add step with status 'done' without evidence "
                f"(for historical imports, describe the original verification source)"
            )
        if step_status == StepStatus.SKIPPED and not step_skipped_reason:
            raise PlanError(
                f"Step {i}: cannot add step with status 'skipped' without skipped_reason"
            )

        agent_raw = entry.get("agent")
        step_agent: str | None = None
        if agent_raw is not None:
            if not isinstance(agent_raw, str):
                raise PlanError(f"Step {i}: 'agent' must be a string")
            step_agent = agent_raw

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

    generated_ids: list[str] = []

    for i, p_entry in enumerate(parsed):
        after_raw_val = p_entry["after_list"]
        resolved_deps: list[str] = []

        if after_raw_val is not None:
            assert isinstance(after_raw_val, list)
            for dep_ref in after_raw_val:
                assert isinstance(dep_ref, str)
                if dep_ref in known_phase_ids:
                    resolved_deps.append(dep_ref)
                elif dep_ref in slug_to_id:
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

    step_graph = {s.id: s.depends_on for s in phase.steps}
    cycle = _detect_cycle(step_graph)
    if cycle:
        added_set = set(generated_ids)
        phase.steps = [s for s in phase.steps if s.id not in added_set]
        raise PlanError(f"Step dependency cycle in batch: {' → '.join(cycle)}")

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
