"""Judgment Agent invocation.

Responsibility: Invoke the unified Judgment Agent (a stateless runner call)
for decisions that require LLM understanding. Route between rule-based fast
paths and LLM fallback. Parse structured verdicts from JSON output.

Non-responsibility: Does NOT define judgment types (that is `judgments.py`).
Does NOT own the decision of WHEN to call the judge (that is `loop.py`'s
reconcile and dispatch logic).

Architecture Reference: docs/DRIVER-ARCHITECTURE.md Section 2.10
Blueprint Reference: docs/JUDGE-AGENT-PROMPT.md
"""

from __future__ import annotations

import asyncio
import json
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from .errors import JudgmentParseError, JudgmentTimeoutError
from .judgments import (
    CONTEXT_SCHEMAS,
    VERDICT_VALUES,
    JudgmentRequest,
    JudgmentType,
    JudgmentVerdict,
)

if TYPE_CHECKING:
    from .config import JudgeConfig
    from .observe import Observer


# =============================================================================
# JUDGE VERDICT JSON SCHEMA
# =============================================================================

# JSON schema for structured output (used when structured_output is True)
# Derived from JudgmentVerdict dataclass.
VERDICT_SCHEMA: dict[str, object] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "properties": {
        "verdict": {
            "type": "string",
            "enum": list(VERDICT_VALUES),
            "description": "One of: ACCEPT, REJECT, RETRY, SWITCH_AGENT, REPLAN, DEFER, HALT",
        },
        "reason": {
            "type": "string",
            "minLength": 1,
            "description": "1-3 sentence explanation for the verdict",
        },
        "suggested_action": {
            "type": ["string", "null"],
            "description": "Agent name for SWITCH_AGENT verdict, null otherwise",
        },
        "planner_instruction": {
            "type": ["string", "null"],
            "description": "Instruction for vectl-planner when REPLAN, null otherwise",
        },
    },
    "required": ["verdict", "reason"],
    "additionalProperties": False,
}
"""
JSON schema for structured output.

Architecture: docs/DRIVER-ARCHITECTURE.md Section 2.10, Structured Output Strategy
Blueprint: docs/JUDGE-AGENT-PROMPT.md Response Format (MANDATORY)
"""


# =============================================================================
# RUNNER PROTOCOL FOR JUDGE
# =============================================================================


class JudgeRunner(Protocol):
    """Protocol for a runner capable of executing judge prompts.

    Architecture: docs/DRIVER-ARCHITECTURE.md Section 2.10, JudgeRunner Protocol
    Blueprint: DRIVER-BLUEPRINT.md Runner Protocol & Implementations

    Not all runner implementations support structured output. The judge module
    handles the fallback to prompt-guided JSON parsing when structured output
    is unavailable or disabled.
    """

    name: str
    """Runner name for logging and error messages."""

    async def dispatch_structured(
        self,
        system_prompt: str,
        user_prompt: str,
        schema: dict[str, object],
        timeout: float,
    ) -> dict[str, object]:
        """Dispatch a structured output request to the runner.

        Args:
            system_prompt: The judge system prompt (from JUDGE-AGENT-PROMPT.md).
            user_prompt: The structured judgment request (rendered from JudgmentRequest).
            schema: JSON schema for the expected verdict structure.
            timeout: Timeout in seconds.

        Returns:
            Parsed JSON verdict object (guaranteed valid JSON when structured
            output is supported).

        Raises:
            JudgmentTimeoutError: Agent did not respond within timeout.
            JudgmentParseError: Agent returned invalid output.

        Architecture: docs/DRIVER-ARCHITECTURE.md Section 2.10, Structured Output Strategy
        """
        ...

    async def dispatch_text(
        self,
        system_prompt: str,
        user_prompt: str,
        timeout: float,
    ) -> str:
        """Dispatch a text-based request (fallback when structured output unavailable).

        Args:
            system_prompt: The judge system prompt.
            user_prompt: The structured judgment request.
            timeout: Timeout in seconds.

        Returns:
            Raw text output (JSON parsing handled by caller).

        Raises:
            JudgmentTimeoutError: Agent did not respond within timeout.
            JudgmentParseError: Agent returned no output.

        Architecture: docs/DRIVER-ARCHITECTURE.md Section 2.10, Text Fallback
        """
        ...


# =============================================================================
# JUDGE CLASS CONTRACT
# =============================================================================


class Judge:
    """Unified judgment agent. Stateless per call.

    Each invocation spawns a runner process (default: opencode) with
    the judge system prompt and a structured user message. The judge
    returns a JSON verdict. Uses structured output when available
    (see Structured Output Strategy in Architecture doc).

    Architecture: docs/DRIVER-ARCHITECTURE.md Section 2.10
    Blueprint: docs/JUDGE-AGENT-PROMPT.md (full prompt)
    Blueprint: DRIVER-BLUEPRINT.md lines 241-264 (Invocation Protocol)

    Invariants:
        - Judge.__init__ MUST receive a valid JudgeConfig and Observer.
        - Observer MUST be used to emit JUDGMENT events for audit.
        - judge.judge() MUST validate context against CONTEXT_SCHEMAS before
          invoking the runner.
        - judge.judge() MUST raise JudgmentTimeoutError on timeout.
        - judge.judge() MUST raise JudgmentParseError on unparseable output.
        - Verdict parsing MUST validate against VERDICT_VALUES.

    Usage:
        config = load_config(Path("driver.yaml"))
        judge = Judge(config.judge, observer)

        request = JudgmentRequest(
            type=JudgmentType.PREFLIGHT,
            step_id="core.impl",
            context={...},
            failure_history=[],
            plan_summary="Phase 1",
        )

        verdict = await judge.judge(request)
        if verdict.verdict == "ACCEPT":
            ...
    """

    def __init__(self, config: JudgeConfig, observer: Observer) -> None:
        """Initialize the judgment agent.

        Args:
            config: JudgeConfig from driver.yaml. Contains runner name,
                timeout, structured_output flag, and enabled judgment types.
            observer: Observer for emitting JUDGMENT events for audit trail.

        Architecture: docs/DRIVER-ARCHITECTURE.md Section 2.10, Judge.__init__
        """
        raise NotImplementedError("Judge.__init__ not implemented")

    def is_enabled(self, judgment_type: JudgmentType) -> bool:
        """Check if a judgment type is enabled in config.

        Args:
            judgment_type: The judgment type to check.

        Returns:
            True if the judgment type is enabled, False otherwise.

        Architecture: docs/DRIVER-ARCHITECTURE.md Section 2.10, Judge.is_enabled

        The JudgeConfig contains boolean flags for each judgment type:
            - preflight: enable PREFLIGHT judgments
            - evidence_validation: enable EVIDENCE judgments
            - failure_classification: enable FAILURE judgments
            - escalation: enable ESCALATION judgments
            - gate_assessment: enable GATE judgments
            - cold_context: enable COLD_CONTEXT judgments
            - anomaly: enable ANOMALY judgments
        """
        raise NotImplementedError("Judge.is_enabled not implemented")

    async def judge(self, request: JudgmentRequest) -> JudgmentVerdict:
        """Invoke the judgment agent and return a parsed verdict.

        When config.structured_output is True, uses runner-specific
        structured output to eliminate parse failures (see strategy below).
        Falls back to prompt-guided JSON parsing when structured output
        is disabled or unavailable.

        Args:
            request: The judgment request containing type, step_id, context,
                failure_history, and plan_summary.

        Returns:
            JudgmentVerdict with verdict, reason, suggested_action (optional),
            and planner_instruction (optional).

        Raises:
            JudgmentTimeoutError: If the agent does not respond within
                config.timeout seconds.
            JudgmentParseError: If the agent returns unparseable output.

        Architecture: docs/DRIVER-ARCHITECTURE.md Section 2.10, Judge.judge
        Blueprint: DRIVER-BLUEPRINT.md lines 244-249 (Invocation Protocol)

        Pre-conditions:
            - request.type MUST be enabled in config (check via is_enabled).
            - request.context MUST contain all keys from CONTEXT_SCHEMAS[type]["required"].

        Post-conditions:
            - Returns a valid JudgmentVerdict.
            - Observer emits JUDGMENT event with type, step_id, verdict, reason, latency.
            - Verdict.verdict MUST be one of VERDICT_VALUES.
        """
        raise NotImplementedError("Judge.judge not implemented")

    # =========================================================================
    # PRIVATE METHODS (STUBS)
    # =========================================================================

    def _validate_request(self, request: JudgmentRequest) -> None:
        """Validate request against CONTEXT_SCHEMAS.

        Args:
            request: The judgment request to validate.

        Raises:
            ValueError: If required context fields are missing.

        Architecture: docs/DRIVER-ARCHITECTURE.md Section 2.10, context validation
        Blueprint: docs/DRIVER-ARCHITECTURE.md Invariant (lines 100-104)

        Invariant:
            All JudgmentRequest.context dicts MUST contain all keys listed in
            CONTEXT_SCHEMAS[request.type]["required"].
        """
        raise NotImplementedError("Judge._validate_request not implemented")

    def _render_request(self, request: JudgmentRequest) -> str:
        """Render a JudgmentRequest into a user prompt for the judge.

        Args:
            request: The judgment request to render.

        Returns:
            A formatted string suitable for the judge's structured input.

        Architecture: docs/DRIVER-ARCHITECTURE.md Section 2.10, request rendering
        """
        raise NotImplementedError("Judge._render_request not implemented")

    async def _invoke_runner(self, system_prompt: str, user_prompt: str) -> str:
        """Invoke the configured runner with the judge prompt.

        Args:
            system_prompt: The judge system prompt.
            user_prompt: The rendered judgment request.

        Returns:
            Raw output from the runner.

        Raises:
            JudgmentTimeoutError: If runner does not respond within timeout.
            JudgmentParseError: If runner returns no output.

        Architecture: docs/DRIVER-ARCHITECTURE.md Section 2.10, runner invocation
        """
        raise NotImplementedError("Judge._invoke_runner not implemented")

    def _parse_verdict(self, raw_output: str, request: JudgmentRequest) -> JudgmentVerdict:
        """Parse raw runner output into a JudgmentVerdict.

        Args:
            raw_output: Raw string output from the runner.
            request: The original request (for error context).

        Returns:
            A valid JudgmentVerdict.

        Raises:
            JudgmentParseError: If the output cannot be parsed or validated.

        Architecture: docs/DRIVER-ARCHITECTURE.md Section 2.10, verdict parsing
        Blueprint: docs/JUDGE-AGENT-PROMPT.md Response Format (MANDATORY)

        Post-conditions:
            - verdict.verdict MUST be in VERDICT_VALUES.
            - If verdict.verdict == "SWITCH_AGENT", suggested_action MUST be non-None.
            - If verdict.verdict == "REPLAN", planner_instruction MUST be non-None.
        """
        raise NotImplementedError("Judge._parse_verdict not implemented")

    def _emit_judgment_event(
        self,
        request: JudgmentRequest,
        verdict: JudgmentVerdict,
        latency_ms: float,
    ) -> None:
        """Emit a JUDGMENT event to the observer for audit trail.

        Args:
            request: The judgment request.
            verdict: The parsed verdict.
            latency_ms: Time taken for the judgment call in milliseconds.

        Architecture: docs/DRIVER-ARCHITECTURE.md Section 2.10, event emission
        Blueprint: DRIVER-BLUEPRINT.md line 248 (observer.emit("JUDGMENT", ...))
        Blueprint: DRIVER-BLUEPRINT.md lines 837-839 (JUDGMENT event format)
        """
        raise NotImplementedError("Judge._emit_judgment_event not implemented")


# =============================================================================
# STRUCTURED OUTPUT STRATEGY (DOCUMENTATION)
# =============================================================================

"""
Structured Output Strategy (from Architecture doc Section 2.10):

When JudgeConfig.structured_output is True, the judge uses runner-specific
mechanisms to guarantee valid JSON verdicts, eliminating parse failures:

| Runner     | Mechanism                          | Verdict extraction                    |
|-----------|-----------------------------------|---------------------------------------|
| Claude CLI | --json-schema <schema_file>       | Response structured_output = guaranteed valid JSON |
| OpenCode   | --format json                     | Parse from JSONL text event (best-effort) |
| Codex      | --output-schema <schema_file>     | Guaranteed structured output          |

The JSON schema for the verdict is derived from JudgmentVerdict dataclass
and written to a temp file at judge initialization. When structured output is
disabled (structured_output: false), the judge falls back to prompt-guided
JSON parsing from the raw text output.

Implementation Notes:
    - Claude / Codex: Write VERDICT_SCHEMA to temp file, pass to runner.
    - OpenCode: No native structured output, use prompt-guided JSON parsing.
    - Gemma: Unverified structured output support, use prompt-guided fallback.

See Architecture doc Section 2.10 and VERDICT_SCHEMA above.
"""


# =============================================================================
# JUDGE VS RULES DECISION BOUNDARY (DOCUMENTATION)
# =============================================================================

"""
Judge vs Rules Decision Boundary (from Architecture doc Section 2.10):

Reconcile determines when to invoke the judge via a two-tier check:

| Check                           | Mechanism                                      | Judge involvement              |
|--------------------------------|------------------------------------------------|--------------------------------|
| Evidence schema validation     | Rule-based: required fields, YAML parseable   | None (fast reject)             |
| Evidence content adequacy       | Requires understanding                         | EVIDENCE judgment             |
| Failure count < 3               | Rule-based threshold                          | None (automatic retry)         |
| Failure count >= 3              | Needs classification                          | ESCALATION judgment           |
| Preflight risk signals          | Keyword detection                             | PREFLIGHT judgment            |
| Gate blocker vs suggestion      | Severity classification                       | GATE judgment for downstream  |
| Claims/plan anomaly repair      | Anomaly type classification                    | ANOMALY judgment               |
| Gate dispatch context pruning   | Inclusion/exclusion for isolation             | COLD_CONTEXT judgment          |

The boundary is: **rules handle structure; the judge handles semantics.**

Implementation Notes:
    - loop.py owns the decision of WHEN to call judge.
    - judge.py owns the invocation and parsing.
    - Rule-based checks in loop.py should fast-path without judge.
    - Judge should be called ONLY when semantics are needed.

See Architecture doc Section 2.10, "Judge vs Rules Decision Boundary".
"""
