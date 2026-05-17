"""Rendering, DAG, and diff helpers for plan queries.

Extracted from vectl.core_plan_queries while preserving public imports.
"""

from __future__ import annotations

from vectl.models import DiffResult, Phase, PhaseChange, PhaseStatus, Plan, PlanError, Step, StepChange, StepStatus
from vectl.semantics import is_step_locked as _is_step_locked_shared

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

def render_plan(plan: Plan, phase_id: str | None = None, full: bool = False) -> str:
    """Render plan as Markdown stakeholder report.

    Deliberately less detail than review_plan: omits claimed_by, claimed_at,
    rejection_history. Shows phase progress, step status, and one-line
    description summaries (unless ``full=True``).

    Args:
        plan: The plan to render.
        phase_id: If provided, render only this phase.
        full: If True, show complete step descriptions without truncation.

    Returns:
        Markdown string.

    Raises:
        PlanError: If phase_id is provided but not found.
    """
    if phase_id is not None:
        ph = plan.find_phase(phase_id)
        if ph is None:
            raise PlanError(f"Phase '{phase_id}' not found.")
        return _render_phase(plan, ph, full=full)

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
        lines.append(_render_phase(plan, ph, full=full))

    return "\n".join(lines)


# @shell_complexity: Branches preserve phase context/gate display, lock icon selection, full descriptions, summaries, and progress math.
def _render_phase(plan: Plan, ph: Phase, full: bool = False) -> str:
    """Render a single phase as Markdown section.

    Uses shared ``is_step_locked`` from *semantics* so lock icons are
    consistent across CLI, MCP, and ``render_plan`` output.

    Args:
        plan: The plan (for lock detection).
        ph: The phase to render.
        full: If True, show complete step descriptions without truncation.
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
        if full and step.description.strip():
            lines.append(f"- {si} **{step.id}** {step.name}")
            # Indent full description under the bullet
            for desc_line in step.description.strip().splitlines():
                lines.append(f"  {desc_line}")
        else:
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


def generate_mermaid_dag(plan: Plan, phase_id: str | None = None) -> str:
    """Generate Mermaid flowchart syntax from plan dependency graph.

    Two-level zoom:
    - Default (phase_id=None): nodes are phases, edges are phase depends_on.
    - With phase_id: nodes are steps within that phase, edges are step depends_on.

    Node labels include status icon and progress info.

    Args:
        plan: The plan to visualize.
        phase_id: If provided, show step-level DAG for this phase.

    Returns:
        Mermaid flowchart string (LR direction).

    Raises:
        PlanError: If phase_id is provided but not found.

    >>> from vectl.models import Plan, Phase, Step, PhaseStatus, StepStatus
    >>> p = Plan(project="test", phases=[
    ...     Phase(id="a", name="Alpha", status=PhaseStatus.DONE, steps=[
    ...         Step(id="a.1", name="S1", status=StepStatus.DONE),
    ...     ]),
    ...     Phase(id="b", name="Beta", status=PhaseStatus.PENDING, depends_on=["a"], steps=[
    ...         Step(id="b.1", name="S1", status=StepStatus.PENDING),
    ...     ]),
    ... ])
    >>> mmd = generate_mermaid_dag(p)
    >>> "flowchart TD" in mmd
    True
    >>> "a --> b" in mmd
    True
    """
    if phase_id is not None:
        return _mermaid_step_dag(plan, phase_id)
    return _mermaid_phase_dag(plan)


def _mermaid_phase_dag(plan: Plan) -> str:
    """Generate phase-level Mermaid DAG."""
    lines: list[str] = ["flowchart TD"]

    for ph in plan.phases:
        icon = _PHASE_ICON.get(ph.status, "?")
        done = sum(1 for s in ph.steps if s.status in (StepStatus.DONE, StepStatus.SKIPPED))
        total = len(ph.steps)
        label = f"{icon} {ph.name} ({done}/{total})"
        lines.append(f'  {ph.id}["{label}"]')

    for ph in plan.phases:
        for dep in ph.depends_on:
            lines.append(f"  {dep} --> {ph.id}")

    # Hint
    lines.append("")
    lines.append("%% Drill into a phase: vectl dag --phase <id>")

    return "\n".join(lines)


# @shell_complexity: Branches preserve missing-phase validation, lock icon selection, node emission, and dependency edge emission.
def _mermaid_step_dag(plan: Plan, phase_id: str) -> str:
    """Generate step-level Mermaid DAG for a single phase."""
    ph = plan.find_phase(phase_id)
    if ph is None:
        raise PlanError(f"Phase '{phase_id}' not found.")

    lines: list[str] = ["flowchart TD"]

    for step in ph.steps:
        icon = _STEP_ICON.get(step.status, "?")
        if _is_step_locked_shared(plan, ph, step):
            icon = "🔒"
        label = f"{icon} {step.name}"
        lines.append(f'  {_mermaid_node_id(step.id)}["{label}"]')

    for step in ph.steps:
        for dep in step.depends_on:
            lines.append(f"  {_mermaid_node_id(dep)} --> {_mermaid_node_id(step.id)}")

    return "\n".join(lines)


def _mermaid_node_id(step_id: str) -> str:
    """Convert step ID to valid Mermaid node ID.

    Mermaid node IDs cannot contain dots or certain special chars.
    Replace dots and hyphens with underscores.

    >>> _mermaid_node_id("core.validate-plan")
    'core_validate_plan'
    """
    return step_id.replace(".", "_").replace("-", "_")


# @shell_complexity: Branches preserve added/removed/status/modified phase and step diff classifications in one deterministic pass.
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

    phase_changes.extend(_diff_phase_changes(old_phases, new_phases))

    # Step-level changes
    old_steps: dict[str, tuple[str, Step]] = {}
    for p in old.phases:
        for s in p.steps:
            old_steps[s.id] = (p.id, s)

    new_steps: dict[str, tuple[str, Step]] = {}
    for p in new.phases:
        for s in p.steps:
            new_steps[s.id] = (p.id, s)

    step_changes.extend(_diff_step_changes(old_steps, new_steps))

    return DiffResult(phase_changes=phase_changes, step_changes=step_changes)


def _diff_phase_changes(
    old_phases: dict[str, Phase], new_phases: dict[str, Phase]
) -> list[PhaseChange]:
    """Return added, removed, and status-changed phase records."""
    changes: list[PhaseChange] = []
    for pid in new_phases:
        if pid not in old_phases:
            np = new_phases[pid]
            changes.append(PhaseChange(phase_id=pid, phase_name=np.name, kind="added"))
            continue
        op, np = old_phases[pid], new_phases[pid]
        if op.status != np.status:
            changes.append(
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
            changes.append(PhaseChange(phase_id=pid, phase_name=op.name, kind="removed"))
    return changes


def _diff_step_changes(
    old_steps: dict[str, tuple[str, Step]], new_steps: dict[str, tuple[str, Step]]
) -> list[StepChange]:
    """Return added, removed, status-changed, and modified step records."""
    changes: list[StepChange] = []
    for sid in new_steps:
        npid, ns = new_steps[sid]
        if sid not in old_steps:
            changes.append(_added_step_change(sid, npid, ns))
            continue

        _, os_ = old_steps[sid]
        if os_.status != ns.status:
            changes.append(_status_step_change(sid, npid, os_, ns))
        elif os_.name != ns.name or os_.description != ns.description:
            changes.append(_modified_step_change(sid, npid, os_, ns))

    for sid in old_steps:
        if sid not in new_steps:
            opid, os_ = old_steps[sid]
            changes.append(
                StepChange(
                    step_id=sid,
                    step_name=os_.name,
                    phase_id=opid,
                    kind="removed",
                    old_status=os_.status,
                )
            )
    return changes


def _added_step_change(step_id: str, phase_id: str, step: Step) -> StepChange:
    """Build a StepChange for an added step."""
    return StepChange(
        step_id=step_id,
        step_name=step.name,
        phase_id=phase_id,
        kind="added",
        new_status=step.status,
    )


def _status_step_change(step_id: str, phase_id: str, old: Step, new: Step) -> StepChange:
    """Build a StepChange for a step status transition."""
    return StepChange(
        step_id=step_id,
        step_name=new.name,
        phase_id=phase_id,
        kind="status_changed",
        old_status=old.status,
        new_status=new.status,
    )


def _modified_step_change(step_id: str, phase_id: str, old: Step, new: Step) -> StepChange:
    """Build a StepChange for name/description edits."""
    detail_parts: list[str] = []
    if old.name != new.name:
        detail_parts.append(f"name: {old.name!r} → {new.name!r}")
    if old.description != new.description:
        detail_parts.append("description changed")
    return StepChange(
        step_id=step_id,
        step_name=new.name,
        phase_id=phase_id,
        kind="modified",
        detail="; ".join(detail_parts),
    )
