"""Plan validation and completed-evidence guard helpers."""

from __future__ import annotations

import re
from typing import Any, TypeAlias

from vectl.core_duplicate_step_id import analyze_duplicate_step_ids
Path: TypeAlias = Any

from vectl.models import Phase, PhaseStatus, Plan, PlanValidationIssue, Step, StepStatus


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


# @shell_complexity: Branches preserve active-phase filtering, status eligibility, dependency satisfaction, and agent/rejected ordering.

def validate_plan(
    plan: Plan,
    *,
    check_refs: bool = False,
    base_path: Path | None = None,
    include_completed_evidence_guard: bool = True,
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

    duplicate_diagnostics = analyze_duplicate_step_ids(plan)

    # Per-phase checks
    for phase in plan.phases:
        # Step IDs in this phase (used for dependency validation only).
        # Duplicate-ID diagnostics are generated plan-wide by
        # analyze_duplicate_step_ids().
        step_ids: set[str] = set()
        for step in phase.steps:
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

    for conflict in duplicate_diagnostics.conflicts:
        conflict_phase_list = ", ".join(conflict.phase_ids)
        errors.append(
            PlanValidationIssue(
                f"Duplicate step ID '{conflict.step_id}' appears {conflict.occurrences} "
                f"times across phases: {conflict_phase_list}. "
                "Repair required before validation/gate checks can pass. "
                "Run `vectl migrate-step-id --dry-run` then "
                "`vectl migrate-step-id --yes`.",
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

    if include_completed_evidence_guard:
        errors.extend(_completed_evidence_guard_issues(plan))

    return errors


_EVIDENCE_GUARD_STEP_HINTS = (
    "review",
    "gate",
    "test",
    "verify",
    "verification",
    "pytest",
    "smoke",
    "regression",
    "validation",
    "acceptance",
    "runtime non-closure",
    "gate_open_allowed",
    "review_outcome",
)

_EVIDENCE_FAILURE_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("gate_open_allowed=false", re.compile(r"\bgate_open_allowed\s*[:=]\s*false\b", re.I)),
    ("NEEDS_REVISION", re.compile(r"\bNEEDS_REVISION\b", re.I)),
    (
        "runtime non-closure: fail",
        re.compile(r"\bruntime\s+non[- ]closure\s*:\s*fail\b", re.I),
    ),
    ("failed tests", re.compile(r"\b[1-9]\d*\s+failed\b", re.I)),
    ("FAIL", re.compile(r"\bFAIL(?:ED|URE)?\b")),
)

_EVIDENCE_CLOSURE_PATTERNS = (
    re.compile(r"\bgate_open_allowed\s*[:=]\s*true\b", re.I),
    re.compile(r"\breview_outcome\s*[:=]\s*(?:pass|passed|approved|accepted)\b", re.I),
    re.compile(r"\bruntime\s+non[- ]closure\s*:\s*(?:pass|closed|ok)\b", re.I),
    re.compile(r"\b(?:green verification|retest passed|all tests pass(?:ed)?|tests PASS|PASS)\b"),
    re.compile(r"\bOPEN\b"),
)


# @shell_complexity: Branches preserve candidate filtering, failure detection, expected-red disposition, later closure, and issue construction.
def _completed_evidence_guard_issues(plan: Plan) -> list[PlanValidationIssue]:
    """Flag completed verification-like steps with unclosed failure evidence."""

    issues: list[PlanValidationIssue] = []
    for phase in plan.phases:
        for index, step in enumerate(phase.steps):
            if step.status != StepStatus.DONE or not step.evidence:
                continue
            if not _is_evidence_guard_candidate(step):
                continue
            reasons = _evidence_failure_reasons(step.evidence)
            if not reasons:
                continue
            if _has_intentional_red_or_nonblocking_disposition(step):
                continue
            if _has_later_same_phase_closure(phase, after_index=index):
                continue
            issues.append(
                PlanValidationIssue(
                    "Completed evidence guard: "
                    f"step '{step.id}' in phase '{phase.id}' has unclosed failure evidence "
                    f"({', '.join(reasons)}). Add a later same-phase closure step with "
                    "green/OPEN/gate_open_allowed=true evidence, or record expected-red/non-blocking "
                    "owner/lifecycle/gate-intersection disposition."
                )
            )
    return issues


def _is_evidence_guard_candidate(step: Step) -> bool:
    """Return True for completed steps whose text is verification-like."""

    haystack = "\n".join(
        (
            step.id,
            step.name,
            step.description,
            step.verification,
            step.evidence or "",
        )
    ).lower()
    return any(token in haystack for token in _EVIDENCE_GUARD_STEP_HINTS)


def _evidence_failure_reasons(evidence: str) -> list[str]:
    """Return unique failure tokens visible in evidence text."""

    reasons: list[str] = []
    for label, pattern in _EVIDENCE_FAILURE_PATTERNS:
        if pattern.search(evidence) and label not in reasons:
            reasons.append(label)
    return reasons


def _evidence_has_closure(evidence: str) -> bool:
    """Return True when evidence mechanically closes a prior gate/test failure."""

    if re.search(r"\bgate_open_allowed\s*[:=]\s*true\b", evidence, re.I):
        return True
    if _evidence_failure_reasons(evidence):
        return False
    return any(pattern.search(evidence) for pattern in _EVIDENCE_CLOSURE_PATTERNS)


def _has_later_same_phase_closure(phase: Phase, *, after_index: int) -> bool:
    """Return True when a later completed step visibly closes the blocker."""

    for later in phase.steps[after_index + 1 :]:
        if later.status == StepStatus.DONE and later.evidence and _evidence_has_closure(later.evidence):
            return True
    return False


def _has_intentional_red_or_nonblocking_disposition(step: Step) -> bool:
    """Allow explicit expected-red/non-blocking evidence with lifecycle proof."""

    evidence = step.evidence or ""
    lower = evidence.lower()
    has_owner = bool(re.search(r"\bowner\s*[:=]|\bowned by\b", lower))
    has_lifecycle = any(token in lower for token in ("lifecycle", "disposition", "expires", "tracked"))
    has_gate_proof = any(
        token in lower
        for token in (
            "gate-intersection proof",
            "non-intersection",
            "nonintersection",
            "does not intersect",
            "no gate intersection",
            "outside gate",
        )
    )

    is_expected_red = (
        step.verify == "expected_red" or "expected-red" in lower or "expected_red" in lower
    )
    if is_expected_red and has_owner and has_lifecycle and has_gate_proof:
        return True

    is_nonblocking = "non-blocking" in lower or "not blocking" in lower
    return is_nonblocking and has_owner and has_lifecycle and has_gate_proof
