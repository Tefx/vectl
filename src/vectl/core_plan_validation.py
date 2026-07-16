"""Plan validation and completed-evidence guard helpers."""

from __future__ import annotations

import json
import re
from typing import Any, Literal, TypeAlias

from vectl.core_duplicate_step_id import analyze_duplicate_step_ids
Path: TypeAlias = Any
CompletedEvidenceScope: TypeAlias = Literal["all", "active_dependency_closure"]

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
    completed_evidence_scope: CompletedEvidenceScope = "all",
) -> list[PlanValidationIssue]:
    """Validate plan structure, DAG, and consistency.

    Args:
        plan: The plan to validate.
        check_refs: If True, check that files in refs[] exist on disk.
        base_path: Base path for resolving refs (defaults to cwd).
        include_completed_evidence_guard: Whether to inspect completed failure evidence.
        completed_evidence_scope: ``all`` blocks on every unclosed completed failure;
            ``active_dependency_closure`` blocks only when the completed step is
            relevant to current executable work and reports other failures as warnings.
    """
    if completed_evidence_scope not in {"all", "active_dependency_closure"}:
        raise ValueError(f"Unknown completed evidence scope: {completed_evidence_scope}")

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
        blocking_step_keys = (
            _active_dependency_step_keys(plan)
            if completed_evidence_scope == "active_dependency_closure"
            else None
        )
        errors.extend(
            _completed_evidence_guard_issues(plan, blocking_step_keys=blocking_step_keys)
        )

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
_ACTIVE_WORK_STEP_STATUSES = frozenset(
    {StepStatus.PENDING, StepStatus.CLAIMED, StepStatus.REJECTED}
)
_GATE_SCOPE_TAG = "vectl_gate_scope"
_GATE_SCOPE_BLOCK = re.compile(
    rf"<{_GATE_SCOPE_TAG}>\s*(.*?)\s*</{_GATE_SCOPE_TAG}>",
    re.DOTALL,
)


def _execution_active_phase_ids(plan: Plan) -> set[str]:
    """Return phases where work can execute now, including claimed-work recovery."""

    done_phase_ids = {phase.id for phase in plan.phases if phase.status == PhaseStatus.DONE}
    active: set[str] = set()
    for phase in plan.phases:
        if phase.status in {PhaseStatus.PENDING, PhaseStatus.IN_PROGRESS}:
            active.add(phase.id)
        elif (
            phase.status == PhaseStatus.LOCKED
            and phase.depends_on
            and all(dependency in done_phase_ids for dependency in phase.depends_on)
        ):
            active.add(phase.id)
        elif any(step.status == StepStatus.CLAIMED for step in phase.steps):
            active.add(phase.id)
    return active


def _phase_dependency_closure(plan: Plan, root_phase_ids: set[str]) -> set[str]:
    """Return root phases and every transitive phase dependency."""

    phase_by_id = {phase.id: phase for phase in plan.phases}
    closure = set(root_phase_ids)
    pending = list(root_phase_ids)
    while pending:
        phase = phase_by_id.get(pending.pop())
        if phase is None:
            continue
        for dependency in phase.depends_on:
            if dependency not in closure:
                closure.add(dependency)
                pending.append(dependency)
    return closure


def _gate_integrated_step_ids(description: str) -> tuple[str, ...] | None:
    """Read a gate step's description scope, returning ``None`` when malformed.

    ``Phase.gate`` remains the human-readable criterion. The machine-readable
    ``<vectl_gate_scope>`` contract is carried by the explicit gate step description.
    """

    if f"<{_GATE_SCOPE_TAG}>" not in description:
        return ()
    matches = _GATE_SCOPE_BLOCK.findall(description)
    if len(matches) != 1:
        return None
    try:
        payload = json.loads(matches[0])
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict) or payload.get("schema_version") != "vectl.gate.scope.v2":
        return None
    integrated_steps = payload.get("integrated_steps")
    if not isinstance(integrated_steps, list) or not all(
        isinstance(step_id, str) and step_id for step_id in integrated_steps
    ):
        return None
    return tuple(integrated_steps)


# @shell_complexity: Closure preserves active-phase eligibility, transitive phase/step dependencies, and explicit gate integration.
def _active_dependency_step_keys(plan: Plan) -> set[tuple[str, str]]:
    """Return completed-evidence relevance for current executable work."""

    phase_by_id = {phase.id: phase for phase in plan.phases}
    active_phase_ids = _execution_active_phase_ids(plan)
    phase_scope = _phase_dependency_closure(plan, active_phase_ids)
    keys: set[tuple[str, str]] = set()
    all_step_keys = {
        (phase.id, step.id) for phase in plan.phases for step in phase.steps
    }

    for phase_id in phase_scope - active_phase_ids:
        phase = phase_by_id.get(phase_id)
        if phase is not None:
            keys.update((phase.id, step.id) for step in phase.steps)

    def add_step_dependencies(phase: Phase, root: Step) -> None:
        step_by_id = {step.id: step for step in phase.steps}
        pending = [root]
        while pending:
            step = pending.pop()
            key = (phase.id, step.id)
            if key in keys:
                continue
            keys.add(key)
            pending.extend(
                step_by_id[dependency]
                for dependency in step.depends_on
                if dependency in step_by_id
            )

    for phase_id in active_phase_ids:
        phase = phase_by_id.get(phase_id)
        if phase is None:
            continue
        for step in phase.steps:
            if step.status in _ACTIVE_WORK_STEP_STATUSES:
                add_step_dependencies(phase, step)

        if f"<{_GATE_SCOPE_TAG}>" in phase.gate:
            keys.update(all_step_keys)

        for gate_step in phase.steps:
            integrated_step_ids = _gate_integrated_step_ids(gate_step.description)
            if integrated_step_ids is None:
                keys.update(all_step_keys)
                continue
            for integrated_step_id in integrated_step_ids:
                found = plan.find_step(integrated_step_id)
                if found is None:
                    keys.update(all_step_keys)
                    continue
                integrated_phase, integrated_step = found
                add_step_dependencies(integrated_phase, integrated_step)
                integrated_phase_scope = _phase_dependency_closure(
                    plan, {integrated_phase.id}
                )
                for dependency_phase_id in integrated_phase_scope - {integrated_phase.id}:
                    dependency_phase = phase_by_id.get(dependency_phase_id)
                    if dependency_phase is not None:
                        keys.update(
                            (dependency_phase.id, step.id) for step in dependency_phase.steps
                        )

    return keys


# @shell_complexity: Branches preserve candidate filtering, failure detection, expected-red disposition, later closure, and scoped severity.
def _completed_evidence_guard_issues(
    plan: Plan,
    *,
    blocking_step_keys: set[tuple[str, str]] | None = None,
) -> list[PlanValidationIssue]:
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
            is_warning = (
                blocking_step_keys is not None and (phase.id, step.id) not in blocking_step_keys
            )
            scope_note = (
                " (non-blocking outside active dependency closure)" if is_warning else ""
            )
            issues.append(
                PlanValidationIssue(
                    f"Completed evidence guard{scope_note}: "
                    f"step '{step.id}' in phase '{phase.id}' has unclosed failure evidence "
                    f"({', '.join(reasons)}). Add a later same-phase closure step with "
                    "green/OPEN/gate_open_allowed=true evidence, or record expected-red/non-blocking "
                    "owner/lifecycle/gate-intersection disposition.",
                    is_warning=is_warning,
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
