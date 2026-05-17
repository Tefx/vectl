# @invar:allow file_size: Plan query compatibility module keeps validation, review, render, DAG, and diff APIs co-located for existing CLI/MCP imports.
"""Plan query, review, render, diff, and DAG helpers."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
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

from vectl.core_duplicate_step_id import analyze_duplicate_step_ids

# @shell_complexity: Branches preserve ordered validation diagnostics for phase IDs, DAGs, statuses, duplicate IDs, refs, and evidence guard.
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


# @shell_complexity: DFS branches preserve neighbor filtering, back-edge reconstruction, recursive traversal, and no-cycle sentinel semantics.
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
def get_next_steps(plan: Plan, agent: str | None = None) -> list[Step]:
    """Get all claimable steps across active phases.

    Returns steps that are:
    - In an active (pending/in_progress) phase whose dependencies are all done
    - Status is pending or rejected (rejected = needs rework, prioritized)
    - All step dependencies within the phase are done/skipped

    Ordering (highest priority first):
    1. Rejected steps (need rework)
    2. Steps with ``step.agent`` matching the given *agent* (if provided)
    3. Steps with no agent suggestion (``step.agent is None``)
    4. Steps suggested for a different agent

    Args:
        plan: The plan to query.
        agent: If provided, prioritize steps whose ``agent`` field matches.
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

    def _sort_key(s: Step) -> tuple[int, int, str]:
        # Priority 0: rejected (needs rework)
        status_rank = 0 if s.status == StepStatus.REJECTED else 1
        # Agent affinity: 0 = matches, 1 = unassigned, 2 = different agent
        if agent is None or s.agent is None:
            agent_rank = 1
        elif s.agent == agent:
            agent_rank = 0
        else:
            agent_rank = 2
        return (status_rank, agent_rank, s.id)

    result.sort(key=_sort_key)
    return result


# @shell_complexity: Branches preserve locked-but-eligible inclusion without mutating phase status.
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

    Note:
        This function only applies Rule 4 (LOCKED → PENDING). It does NOT
        enforce Rule 3 (PENDING → LOCKED) or the non-regression rules for
        DONE and IN_PROGRESS phases. For full bidirectional consistency
        enforcement, prefer :func:`recalc_lock_status` instead.
    """
    done_phase_ids = {p.id for p in plan.phases if p.status == PhaseStatus.DONE}
    unlocked: list[str] = []
    for phase in plan.phases:
        if phase.status == PhaseStatus.LOCKED:
            if phase.depends_on and all(dep in done_phase_ids for dep in phase.depends_on):
                phase.status = PhaseStatus.PENDING
                unlocked.append(phase.id)
    return unlocked


# @shell_complexity: Branches encode documented non-regression and locked/pending recalculation rules in one ordered pass.
def recalc_lock_status(plan: Plan) -> list[str]:
    """Recalculate LOCKED/PENDING status for all phases based on dependency state.

    Applies the following rules in order (first matching rule wins):
    1. DONE phases stay DONE — never regressed.
    2. IN_PROGRESS phases stay IN_PROGRESS — active work is never interrupted.
    3. PENDING phases with at least one unmet dependency → LOCKED.
    4. LOCKED phases with all dependencies DONE → PENDING.

    A phase has "unmet dependencies" when at least one phase in its
    ``depends_on`` list is not in DONE status. A phase with an empty
    ``depends_on`` list has no dependencies and is therefore never locked
    by this function (rule 3 is a no-op for dependency-free phases).

    Source: vectl plan step core-recalc (task specification).

    Args:
        plan: The plan to update in place.

    Returns:
        List of phase IDs whose status was changed by this call, in plan order.
    """
    done_phase_ids: set[str] = {p.id for p in plan.phases if p.status == PhaseStatus.DONE}
    changed: list[str] = []

    for phase in plan.phases:
        # Rule 1: DONE phases are immutable.
        if phase.status == PhaseStatus.DONE:
            continue

        # Rule 2: IN_PROGRESS phases are not interrupted.
        if phase.status == PhaseStatus.IN_PROGRESS:
            continue

        if phase.status == PhaseStatus.PENDING:
            # Rule 3: PENDING with unmet deps → LOCKED.
            # Only applies when the phase actually declares dependencies.
            if phase.depends_on and not all(dep in done_phase_ids for dep in phase.depends_on):
                phase.status = PhaseStatus.LOCKED
                changed.append(phase.id)

        elif phase.status == PhaseStatus.LOCKED:
            # Rule 4: LOCKED with all deps DONE → PENDING.
            # A LOCKED phase with no declared deps is a data anomaly;
            # we unlock it unconditionally (no dep constraint to enforce).
            if not phase.depends_on or all(dep in done_phase_ids for dep in phase.depends_on):
                phase.status = PhaseStatus.PENDING
                changed.append(phase.id)

    return changed


def format_lock_changes(changed: list[str], plan: Plan) -> str:
    """Format a human-readable lock-status change message.

    Args:
        changed: Phase IDs returned by recalc_lock_status().
        plan: The plan (after recalc mutation) used to look up new statuses.

    Returns:
        A non-empty informational string when changes occurred, e.g.
        "[vectl] Lock status updated: phase-a (pending), phase-b (locked)".
        Empty string when ``changed`` is empty.
    """
    if not changed:
        return ""
    phase_index = {p.id: p for p in plan.phases}
    parts = ", ".join(
        f"{pid} ({phase_index[pid].status.value.lower()})" if pid in phase_index else pid
        for pid in changed
    )
    return f"[vectl] Lock status updated: {parts}"


# @shell_complexity: Branches preserve regex/substr matching, phase/status filtering, phase-field matches, step-field matches, and snippets.
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


# @shell_complexity: Branches preserve regex and substring match-line extraction, truncation, and first-line fallback.
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


# @shell_complexity: Branches preserve validation invocation, progress totals, active phase filtering, and ref reverse-index construction.
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


# @shell_complexity: Branches preserve selected-phase rendering, summary table construction, per-phase detail, and progress math.
from vectl.core_plan_rendering import (
    _first_line,
    _mermaid_node_id,
    _mermaid_phase_dag,
    _mermaid_step_dag,
    _render_phase,
    diff_plans,
    generate_mermaid_dag,
    render_plan,
)
