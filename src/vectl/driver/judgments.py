"""Judgment type definitions and context schemas.

Responsibility: Define the enum of judgment types, their context schemas
(required/optional fields), and the request/verdict data contracts.

Non-responsibility: Does NOT invoke the judge. Does NOT contain the judge
system prompt.

Architecture Reference: docs/DRIVER-ARCHITECTURE.md Section 2.9
Blueprint Reference: docs/JUDGE-AGENT-PROMPT.md
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class JudgmentType(str, Enum):
    """Judgment types for the unified Judgment Agent.

    Each type corresponds to a specific decision point in the orchestration loop.
    The type determines which evaluation rules and verdict options apply.

    Architecture: docs/DRIVER-ARCHITECTURE.md Section 2.9, JudgmentType enum
    Blueprint: docs/JUDGE-AGENT-PROMPT.md Judgment Types

    Blueprint judgment point -> JudgmentType mapping:

    | Blueprint Point | JudgmentType   | When invoked                          |
    |-----------------|----------------|---------------------------------------|
    | #1 Preflight    | PREFLIGHT      | Before dispatching an impl step       |
    | #3 Spec review  | PREFLIGHT      | High-risk step spec adequacy          |
    | #5 Fail classify| FAILURE        | Classifying failure provenance        |
    | #6 Fail dispose | FAILURE        | Determining failure disposition        |
    | #7 Fail gate    | FAILURE        | Gate-intersection reasoning            |
    | #8c Evidence    | EVIDENCE       | Validating worker evidence             |
    | #10 Evidence    | EVIDENCE       | Gate evidence completeness             |
    | #11 Anomaly     | ANOMALY        | Plan/claims anomaly auto-repair check  |
    | #15 Escalation  | ESCALATION     | Repeated failure escalation            |
    | #17 Gate assess | GATE           | Gate/test result handling              |
    | #18 Cold ctx    | COLD_CONTEXT   | Context pruning for gate dispatch      |
    | #19 Gate assess | GATE           | Freeze step handling                   |
    """

    PREFLIGHT = "preflight"  # Blueprint #1, #3
    EVIDENCE = "evidence"  # Blueprint #8c, #10
    FAILURE = "failure"  # Blueprint #5, #6, #7
    ESCALATION = "escalation"  # Blueprint #15
    GATE = "gate"  # Blueprint #17, #19
    ANOMALY = "anomaly"  # Blueprint #11
    COLD_CONTEXT = "cold_context"  # Blueprint #18


# Required and optional context fields per judgment type.
# Used by judge.py to validate requests before invocation.
# Structure: {type: {"required": [...], "optional": [...]}}
CONTEXT_SCHEMAS: dict[JudgmentType, dict[str, list[str]]] = {
    JudgmentType.PREFLIGHT: {
        "required": ["step_id", "step_description", "step_verification", "step_refs"],
        "optional": ["risk_signals", "phase_context"],
    },
    JudgmentType.EVIDENCE: {
        "required": ["step_id", "step_verification", "evidence"],
        "optional": ["step_type", "gate_evidence"],
    },
    JudgmentType.FAILURE: {
        "required": ["step_id", "error_output", "failure_count"],
        "optional": ["remaining_gates", "step_description"],
    },
    JudgmentType.ESCALATION: {
        "required": [
            "step_id",
            "failure_count",
            "failure_history",
            "step_description",
            "available_agents",
        ],
        "optional": ["runner_failures"],
    },
    JudgmentType.GATE: {
        "required": ["step_id", "gate_evidence", "blocker_issues"],
        "optional": ["remaining_gates", "phase_context"],
    },
    JudgmentType.ANOMALY: {
        "required": ["anomaly_type", "repair_scope"],
        "optional": ["dry_run_recommendation"],
    },
    JudgmentType.COLD_CONTEXT: {
        "required": ["step_id", "step_spec", "diff"],
        "optional": ["verification_criteria", "entry_points"],
    },
}
"""
Context schemas for each judgment type.

Architecture: docs/DRIVER-ARCHITECTURE.md Section 2.9, CONTEXT_SCHEMAS
Blueprint: docs/JUDGE-AGENT-PROMPT.md (per-type context descriptions)

Invariant:
    All `JudgmentRequest.context` dicts MUST contain all keys listed in
    `CONTEXT_SCHEMAS[request.type]["required"]`. The judge module validates
    this before invocation and raises `JudgmentError` on violation.
"""


# Verdict values for judgment responses.
# These are the valid values for JudgmentVerdict.verdict.
VERDICT_VALUES: frozenset[str] = frozenset(
    {
        "ACCEPT",  # Proceed with the action
        "REJECT",  # Block and require fix
        "RETRY",  # Retry with same agent
        "SWITCH_AGENT",  # Try a different agent
        "REPLAN",  # Modify the plan
        "DEFER",  # Skip for now, revisit later
        "HALT",  # Cannot proceed without intervention
    }
)
"""
Valid verdict values for JudgmentVerdict.verdict field.

Architecture: docs/DRIVER-ARCHITECTURE.md Section 2.9, JudgmentVerdict
Blueprint: docs/JUDGE-AGENT-PROMPT.md Response Format (MANDATORY)
"""


@dataclass(frozen=True)
class JudgmentRequest:
    """Input to the judgment agent.

    Architecture: docs/DRIVER-ARCHITECTURE.md Section 2.9, JudgmentRequest dataclass
    Blueprint: docs/JUDGE-AGENT-PROMPT.md (request structure)

    Attributes:
        type: The judgment type determining evaluation rules.
        step_id: The step being evaluated.
        context: Structured context with type-specific required/optional fields.
            Must contain all keys from CONTEXT_SCHEMAS[type]["required"].
        failure_history: List of previous failure outputs (for ESCALATION type).
        plan_summary: Brief summary of plan state for context.

    Invariant:
        `context` MUST contain all keys from CONTEXT_SCHEMAS[type]["required"].
        Judge module validates before invocation and raises JudgmentError.
    """

    type: JudgmentType
    step_id: str
    context: dict[str, str]
    failure_history: list[str]
    plan_summary: str


@dataclass(frozen=True)
class JudgmentVerdict:
    """Output from the judgment agent.

    Architecture: docs/DRIVER-ARCHITECTURE.md Section 2.9, JudgmentVerdict dataclass
    Blueprint: docs/JUDGE-AGENT-PROMPT.md Response Format (MANDATORY)

    Attributes:
        verdict: One of ACCEPT, REJECT, RETRY, SWITCH_AGENT, REPLAN, DEFER, HALT.
        reason: 1-3 sentence explanation for the verdict.
        suggested_action: Agent name for SWITCH_AGENT verdict, None otherwise.
        planner_instruction: Instruction for vectl-planner when REPLAN, None otherwise.

    Response format (from JUDGE-AGENT-PROMPT.md):
        ```json
        {
          "verdict": "<ACCEPT|REJECT|RETRY|SWITCH_AGENT|REPLAN|DEFER|HALT>",
          "reason": "<1-3 sentence explanation>",
          "suggested_action": "<optional: agent name for SWITCH_AGENT, null otherwise>",
          "planner_instruction": "<optional: instruction for REPLAN, null otherwise>"
        }
        ```
    """

    verdict: str  # ACCEPT | REJECT | RETRY | SWITCH_AGENT | REPLAN | DEFER | HALT
    reason: str
    suggested_action: str | None = None
    planner_instruction: str | None = None


@dataclass(frozen=True)
class ReplanVerdictContract:
    """Contract authority for REPLAN verdict handling.

    Architecture: docs/DRIVER-ARCHITECTURE.md Section 2.10 / 2.11
    Blueprint: DRIVER-BLUEPRINT.md Flow 2, Flow 3, Flow 4

    This type exists to pin the non-negotiable semantics of REPLAN before the
    runtime planner wiring is implemented in ``loop.py``.

    Invariants:
        - ``verdict`` MUST be ``"REPLAN"``.
        - ``planner_instruction`` MUST be non-empty for REPLAN.
        - Later phases MUST route REPLAN through planner dispatch; they MUST NOT
          silently collapse REPLAN into REJECT, DEFER, or HALT-only handling.
        - ``reason`` explains why replanning is required; ``planner_instruction``
          explains what the planner should change.
    """

    verdict: str
    reason: str
    planner_instruction: str


REPLAN_NON_NARROWING_RULE: str = (
    "A JudgmentVerdict with verdict='REPLAN' is planner-dispatch-capable "
    "surface area. Later phases MUST preserve planner_instruction and invoke "
    "the planner path; they MUST NOT reinterpret REPLAN as reject-only, "
    "defer-only, or halt-only behavior without an explicit architecture update."
)
"""Canonical anti-narrowing rule for REPLAN verdicts.

Architecture: docs/DRIVER-ARCHITECTURE.md Section 2.10 / 2.11
Blueprint: DRIVER-BLUEPRINT.md lines 566-568, 697-698, 728-733
"""
