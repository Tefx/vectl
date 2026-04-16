"""
Bounded post-execution review normalization.

Authority: docs/ORCHESTRATION-PLANE-INTERFACES.md section 4.5
Authority: docs/RFC-orch-drive.md section 17.1

This module pins the ReviewGate protocol and ReviewGateResult contract.
Contract-only: signatures, result types, and docstrings. No review logic
beyond placeholders.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from vectl.orchestration.contracts import (
    ExecutionResult,
    PlannerRequest,
    ReviewOutcome,
)


@dataclass(frozen=True)
class ReviewGateResult:
    """Bounded output from the post-execution review gate.

    Authority: docs/ORCHESTRATION-PLANE-INTERFACES.md section 3.10
    Authority: docs/RFC-orch-drive.md section 17.1

    The review gate normalizes post-execution output into one of four
    bounded outcomes. Non-pass outcomes drive barrier transitions.

    Continuation rules (RFC-orch-drive.md section 17.1.1):
        - ``pass``: continue through reconcile and completion gates
        - ``needs_fix``: enter barrier with reason ``review_failed``;
          step remains incomplete; after barrier resolution, control may
          re-dispatch the same step as a fresh child run
        - ``needs_replan``: enter barrier with reason ``review_failed``
          and transition to planner-owned replanning
        - ``operator_required``: transition to ``blocked_operator``
          without further automatic dispatch

    ``needs_fix`` is not a terminal failure and not an implicit plan
    mutation. It is a bounded review failure that preserves the current
    step id and requires either resolver-guided local unblocking or
    operator action before the step may run again.

    Attributes:
        status: One of ``pass``, ``needs_fix``, ``needs_replan``,
            ``operator_required``.
        summary: Human-readable explanation of the review outcome.
        evidence_refs: Artifact references supporting the review outcome.
        planner_request: Optional planner mutation request when
            ``status='needs_replan'``. When present alongside
            ``needs_replan``, drive policy must transition to
            planner-owned replanning.
    """

    status: ReviewOutcome
    summary: str
    evidence_refs: tuple[str, ...] = ()
    planner_request: PlannerRequest | None = None


class ReviewGate(Protocol):
    """Bounded post-execution review normalization contract.

    Authority: docs/ORCHESTRATION-PLANE-INTERFACES.md section 4.5

    The review gate is invoked after terminal execution output exists
    and before authoritative step completion. It reduces post-execution
    review into one bounded ``ReviewGateResult``.

    Owns:
        - parsing or validating machine-readable review outputs
        - reducing post-execution review into one bounded result

    Does NOT own:
        - frontier scheduling
        - plan mutation
        - resolver reasoning
        - runtime mechanics

    Error contract:
        - malformed review output must become ``needs_fix`` or
          ``operator_required``; it must not silently pass
        - the review gate must not directly mutate authoritative plan state
    """

    def evaluate(
        self,
        step_id: str,
        execution_result: ExecutionResult,
        artifact_refs: tuple[str, ...] = (),
    ) -> ReviewGateResult:
        """Evaluate post-execution review for a completed child run.

        Args:
            step_id: Step identifier for the completed execution.
            execution_result: Terminal execution result from runtime.
            artifact_refs: Optional artifact references that may contain
                review evidence or structured review output.

        Returns:
            Bounded ``ReviewGateResult`` indicating whether the step
            may proceed through completion gates, needs local fix,
            requires replan, or requires operator attention.
        """
        ...  # contract-only


__all__ = [
    "ReviewGate",
    "ReviewGateResult",
]
