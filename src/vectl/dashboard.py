"""Dashboard data serialization and HTML generation.

This module provides static HTML dashboard generation for vectl plans.
See docs/RFC-dashboard.md for the full specification.

Source: RFC-dashboard.md, lines 196-242
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from importlib.resources import files

from vectl.core import generate_mermaid_dag
from vectl.models import Phase, Plan, Step, StepStatus
from vectl.semantics import is_step_locked


def _load_template() -> str:
    """Load the HTML template from the templates directory.

    Returns:
        The HTML template string with placeholders.

    Raises:
        FileNotFoundError: If template file is not found.
    """
    template_path = files("vectl.templates").joinpath("dashboard.html")
    return template_path.read_text(encoding="utf-8")


# Load template at module import time for performance.
# Exposed as DASHBOARD_HTML_TEMPLATE for backward compatibility and testing.
DASHBOARD_HTML_TEMPLATE = _load_template()


def build_dashboard_data(plan: Plan) -> dict:
    """Serialize Plan into a JSON-safe dict for dashboard rendering.

    Pre-computes summary stats, per-phase progress, and Mermaid DAG strings.

    Args:
        plan: The plan to serialize.

    Returns:
        JSON-safe dict with structure per RFC-dashboard.md section "Data Serialization".

    Example:
        >>> from vectl.models import PhaseStatus, Plan, Phase, Step
        >>> p = Plan(project="test", phases=[
        ...     Phase(id="a", name="Alpha", status=PhaseStatus.DONE, steps=[
        ...         Step(id="a.1", name="S1", status=StepStatus.DONE),
        ...     ]),
        ... ])
        >>> data = build_dashboard_data(p)
        >>> data["project"]
        'test'
        >>> data["summary"]["total_phases"]
        1
        >>> data["phases"][0]["progress"]["pct"]
        100
    """
    # Compute summary stats
    total_phases = len(plan.phases)
    total_steps = sum(len(phase.steps) for phase in plan.phases)

    step_counts = {"done": 0, "claimed": 0, "pending": 0, "rejected": 0, "skipped": 0}
    for phase in plan.phases:
        for step in phase.steps:
            step_counts[step.status.value] += 1

    # Build phases data
    phases_data = []
    for phase in plan.phases:
        phase_data = _build_phase_data(plan, phase)
        phases_data.append(phase_data)

    # Generate Mermaid DAG strings
    phase_dag = generate_mermaid_dag(plan)
    step_dags = {}
    for phase in plan.phases:
        try:
            step_dags[phase.id] = generate_mermaid_dag(plan, phase_id=phase.id)
        except Exception:
            # Skip phases with no steps or other issues
            pass

    return {
        "project": plan.project,
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "summary": {
            "total_phases": total_phases,
            "total_steps": total_steps,
            **step_counts,
        },
        "phases": phases_data,
        "mermaid": {
            "phase_dag": phase_dag,
            "step_dags": step_dags,
        },
    }


def _build_phase_data(plan: Plan, phase: Phase) -> dict:
    """Build data dict for a single phase.

    Args:
        plan: The parent plan (for locked state checks).
        phase: The phase to serialize.

    Returns:
        Dict with phase metadata, progress, and steps.
    """
    total = len(phase.steps)
    done = sum(1 for s in phase.steps if s.status == StepStatus.DONE)
    skipped = sum(1 for s in phase.steps if s.status == StepStatus.SKIPPED)
    # Progress considers done + skipped as "complete"
    complete = done + skipped
    pct = round(complete / total * 100) if total > 0 else 0

    steps_data = [_build_step_data(plan, phase, step) for step in phase.steps]

    return {
        "id": phase.id,
        "name": phase.name,
        "status": phase.status.value,
        "gate": phase.gate,
        "context": phase.context,
        "depends_on": phase.depends_on,
        "evidence": phase.evidence,
        "progress": {"done": done, "total": total, "pct": pct},
        "steps": steps_data,
    }


def _build_step_data(plan: Plan, phase: Phase, step: Step) -> dict:
    """Build data dict for a single step.

    Args:
        plan: The parent plan (for locked state checks).
        phase: The parent phase.
        step: The step to serialize.

    Returns:
        Dict with step metadata and detail fields.
    """
    locked = is_step_locked(plan, phase, step)

    # Serialize rejection_history as list of dicts
    rejection_history = [
        {
            "reason": entry.reason,
            "reviewer": entry.reviewer,
            "timestamp": entry.timestamp,
        }
        for entry in step.rejection_history
    ]

    return {
        "id": step.id,
        "name": step.name,
        "status": step.status.value,
        "locked": locked,
        "description": step.description,
        "verification": step.verification,
        "depends_on": step.depends_on,
        "agent": step.agent,
        "claimed_by": step.claimed_by,
        "claimed_at": step.claimed_at,
        "evidence": step.evidence,
        "refs": step.refs,
        "rejection_history": rejection_history,
        "skipped_reason": step.skipped_reason,
    }


def generate_dashboard(plan: Plan) -> str:
    """Generate a complete single-file HTML dashboard from a plan.

    This function combines the data serialization and HTML template
    to produce a standalone HTML file that can be opened in any browser.

    Args:
        plan: The plan to generate a dashboard for.

    Returns:
        Complete HTML string with embedded CSS, JavaScript, and data.

    Example:
        >>> from vectl.models import Plan, Phase, Step
        >>> p = Plan(project="demo", phases=[
        ...     Phase(id="a", name="Alpha", steps=[
        ...         Step(id="a.1", name="S1", status=StepStatus.DONE),
        ...     ]),
        ... ])
        >>> html = generate_dashboard(p)
        >>> "<!DOCTYPE html>" in html
        True
        >>> "demo" in html
        True
    """
    # Build the data
    data = build_dashboard_data(plan)

    # Serialize to JSON (compact, no ASCII escaping for international text)
    json_str = json.dumps(data, ensure_ascii=False, indent=None)

    # Escape </script> sequences to prevent HTML injection attacks
    # This is a security measure to prevent XSS via data injection
    json_str = json_str.replace("</script>", "<\\/script>")
    json_str = json_str.replace("</SCRIPT>", "<\\/SCRIPT>")

    # Calculate overall progress percentage
    summary = data["summary"]
    total = summary["total_steps"]
    done_plus_skipped = summary["done"] + summary["skipped"]
    progress_pct = round(done_plus_skipped / total * 100) if total > 0 else 0

    # Build template replacements
    replacements = {
        "{{json_data}}": json_str,
        "{{project}}": plan.project,
        "{{progress_pct}}": str(progress_pct),
        "{{summary.done}}": str(summary["done"]),
        "{{summary.claimed}}": str(summary["claimed"]),
        "{{summary.pending}}": str(summary["pending"]),
        "{{summary.rejected}}": str(summary["rejected"]),
        "{{summary.skipped}}": str(summary["skipped"]),
        # These are rendered by JavaScript, but we provide empty defaults
        # for progressive enhancement / no-JS fallback
        "{{phase_nav_items}}": "",
        "{{phase_cards}}": "",
        "{{step_dag_options}}": "",
    }

    # Apply all replacements
    html = DASHBOARD_HTML_TEMPLATE
    for placeholder, value in replacements.items():
        html = html.replace(placeholder, value)

    return html
