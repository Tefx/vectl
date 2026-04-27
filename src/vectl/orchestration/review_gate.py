"""
Bounded post-execution review normalization.

Authority: docs/ORCHESTRATION-PLANE-INTERFACES.md section 4.5
Authority: docs/RFC-orch-drive.md section 17.1
Authority: docs/ARCHITECTURAL-DEMOLITION-REMEDIATION-PLAN.md Wave 5

This module pins the review result contract and provides the concrete default
post-execution review gate used to normalize machine-readable review output
without silently passing malformed results.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TypeAlias, cast

import yaml

from vectl.orchestration.contracts import (
    ExecutionResult,
    PlannerMutationAction,
    PlannerMutationItem,
    PlannerRequest,
    ReviewOutcome,
)

_ParsedReviewPayload: TypeAlias = dict[str, object]
_VALID_REVIEW_OUTCOMES: frozenset[str] = frozenset(
    {"pass", "needs_fix", "needs_replan", "operator_required"}
)
_VALID_PLANNER_ACTIONS: frozenset[str] = frozenset(
    {
        "add-step",
        "edit-step",
        "remove-step",
        "move-step",
        "add-phase",
        "edit-phase",
        "skip-step",
        "complete-phase",
    }
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


@dataclass(frozen=True)
class DefaultReviewGate:
    """Concrete post-execution review normalizer.

    Authority: docs/ARCHITECTURAL-DEMOLITION-REMEDIATION-PLAN.md Wave 5,
    required changes 1-4. The default gate owns the
    post-execution-to-``ReviewGateResult`` normalization layer and must convert
    malformed review output into a bounded non-pass outcome rather than a
    silent pass.

    The accepted structured review shape follows
    ``StructuredReviewResult(review_outcome, summary, findings, evidence_refs)``
    from docs/ORCHESTRATION-PLANE-DISPATCH-AND-PROMPT-POLICY.md section 13.1.
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
            Bounded ``ReviewGateResult``. Runtime failures and malformed
            structured output degrade to ``needs_fix`` or
            ``operator_required``; they never become ``pass``.
        """
        if execution_result.step_id != step_id:
            return ReviewGateResult(
                status="needs_fix",
                summary=(
                    "review result step mismatch: "
                    f"expected {step_id!r}, got {execution_result.step_id!r}"
                ),
                evidence_refs=artifact_refs,
            )

        if execution_result.status != "success":
            return _normalize_terminal_failure(
                execution_result=execution_result,
                artifact_refs=artifact_refs,
            )

        payload = _parse_review_payload(execution_result.output_summary)
        if payload is None:
            return ReviewGateResult(
                status="needs_fix",
                summary=(
                    "review output is not parseable as StructuredReviewResult; "
                    f"raw output preview: {execution_result.output_summary[:200]}"
                ),
                evidence_refs=artifact_refs,
            )

        return _payload_to_review_result(
            step_id=step_id,
            payload=payload,
            artifact_refs=artifact_refs,
        )


__all__ = [
    "DefaultReviewGate",
    "ReviewGateResult",
]


def _normalize_terminal_failure(
    *,
    execution_result: ExecutionResult,
    artifact_refs: tuple[str, ...],
) -> ReviewGateResult:
    status: ReviewOutcome = (
        "operator_required" if execution_result.operator_message else "needs_fix"
    )
    summary = (
        f"review execution ended with status={execution_result.status!r}: "
        f"{execution_result.output_summary}"
    )
    if execution_result.operator_message:
        summary = f"{summary}; operator_message={execution_result.operator_message}"
    return ReviewGateResult(
        status=status,
        summary=summary,
        evidence_refs=artifact_refs,
    )


def _parse_review_payload(raw_output: str) -> _ParsedReviewPayload | None:
    candidate = _strip_markdown_fence(raw_output.strip())
    if not candidate:
        return None

    try:
        parsed = json.loads(candidate)
    except json.JSONDecodeError:
        try:
            parsed = yaml.safe_load(candidate)
        except yaml.YAMLError:
            return None

    if not isinstance(parsed, dict):
        return None
    return cast(_ParsedReviewPayload, parsed)


def _strip_markdown_fence(raw_output: str) -> str:
    if not raw_output.startswith("```"):
        return raw_output

    lines = raw_output.splitlines()
    if len(lines) < 3 or not lines[-1].strip().startswith("```"):
        return raw_output
    return "\n".join(lines[1:-1]).strip()


def _payload_to_review_result(
    *,
    step_id: str,
    payload: _ParsedReviewPayload,
    artifact_refs: tuple[str, ...],
) -> ReviewGateResult:
    raw_outcome = payload.get("review_outcome")
    if raw_outcome not in _VALID_REVIEW_OUTCOMES:
        return ReviewGateResult(
            status="needs_fix",
            summary=(
                "review output has invalid review_outcome; "
                f"expected one of {sorted(_VALID_REVIEW_OUTCOMES)}, got {raw_outcome!r}"
            ),
            evidence_refs=artifact_refs,
        )

    summary = _coerce_summary(payload.get("summary"))
    if summary is None:
        return ReviewGateResult(
            status="needs_fix",
            summary="review output is missing non-empty summary",
            evidence_refs=artifact_refs,
        )

    evidence_refs = artifact_refs + _coerce_str_tuple(payload.get("evidence_refs"))
    status = cast(ReviewOutcome, raw_outcome)
    return ReviewGateResult(
        status=status,
        summary=summary,
        evidence_refs=evidence_refs,
        planner_request=_build_planner_request(
            step_id=step_id,
            status=status,
            summary=summary,
            evidence_refs=evidence_refs,
            payload=payload,
        ),
    )


def _coerce_summary(raw_summary: object) -> str | None:
    if not isinstance(raw_summary, str):
        return None
    summary = raw_summary.strip()
    if not summary:
        return None
    return summary


def _coerce_str_tuple(raw_value: object) -> tuple[str, ...]:
    if raw_value is None:
        return ()
    if not isinstance(raw_value, list | tuple):
        return ()
    values: list[str] = []
    for item in raw_value:
        if isinstance(item, str) and item.strip():
            values.append(item)
    return tuple(values)


def _build_planner_request(
    *,
    step_id: str,
    status: ReviewOutcome,
    summary: str,
    evidence_refs: tuple[str, ...],
    payload: _ParsedReviewPayload,
) -> PlannerRequest | None:
    if status != "needs_replan":
        return None

    raw_request = payload.get("planner_request")
    if isinstance(raw_request, dict):
        reason = _coerce_summary(raw_request.get("reason")) or summary
        affected_steps = _coerce_str_tuple(raw_request.get("affected_steps")) or (step_id,)
        request_evidence_refs = _coerce_str_tuple(raw_request.get("evidence_refs")) or evidence_refs
        constraints = _coerce_str_tuple(raw_request.get("constraints"))
        mutations = _coerce_planner_mutations(raw_request.get("mutations"))
        return PlannerRequest(
            reason=reason,
            affected_steps=affected_steps,
            evidence_refs=request_evidence_refs,
            constraints=constraints,
            mutations=mutations,
        )

    return PlannerRequest(
        reason=summary,
        affected_steps=(step_id,),
        evidence_refs=evidence_refs,
    )


def _coerce_planner_mutations(raw_value: object) -> tuple[PlannerMutationItem, ...]:
    if not isinstance(raw_value, list | tuple):
        return ()

    mutations: list[PlannerMutationItem] = []
    for raw_item in raw_value:
        if not isinstance(raw_item, dict):
            continue
        action = raw_item.get("action")
        if action not in _VALID_PLANNER_ACTIONS:
            continue
        raw_arguments = raw_item.get("arguments", {})
        arguments = raw_arguments if isinstance(raw_arguments, dict) else {}
        raw_reason = raw_item.get("reason", "")
        reason = raw_reason if isinstance(raw_reason, str) else ""
        mutations.append(
            PlannerMutationItem(
                action=cast(PlannerMutationAction, action),
                arguments=dict(arguments),
                reason=reason,
                safety_notes=_coerce_str_tuple(raw_item.get("safety_notes")),
            )
        )
    return tuple(mutations)
