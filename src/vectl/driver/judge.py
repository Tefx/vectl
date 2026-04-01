"""Judgment Agent invocation.

Responsibility: Invoke the unified Judgment Agent (a stateless runner call)
for decisions that require LLM understanding. Route between rule-based fast
paths and LLM fallback. Parse structured verdicts from JSON output.

Non-responsibility: Does NOT define judgment types (that is `judgments.py`).
Does NOT own the decision of WHEN to call the judge (that is `loop.py`'s
reconcile and dispatch logic).

Architecture Reference: docs/DRIVER-ARCHITECTURE.md Section 2.10
Blueprint Reference (doc-mirror/spec): docs/JUDGE-AGENT-PROMPT.md
    Runtime authority: packaged ``judge_agent_prompt.md`` resource in ``vectl.driver``.
"""

from __future__ import annotations

import asyncio
import importlib.resources
import json
import time
from dataclasses import dataclass
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import TYPE_CHECKING, Final, Literal, Protocol

import yaml

from .errors import JudgmentParseError, JudgmentTimeoutError
from .judgments import (
    CONTEXT_SCHEMAS,
    VERDICT_VALUES,
    JudgmentRequest,
    JudgmentType,
    JudgmentVerdict,
)
from .types import JudgeContinuityPolicyOutput

if TYPE_CHECKING:
    from .config import JudgeConfig
    from .observe import Observer


JudgeSelectionMode = Literal["prompt_only", "external_agent"]


@dataclass(frozen=True)
class JudgeAgentSelectionContract:
    """Pinned judge-side agent-selection contract.

    Source:
    - docs/DRIVER-AGENT-SELECTION.md ``Judge``
    - docs/DRIVER-AGENT-SELECTION.md ``Judge: external-agent mode``
    - docs/DRIVER-AGENT-SELECTION.md ``Judge: prompt-only mode``
    - docs/DRIVER-AGENT-SELECTION.md ``Fallback and Error Policy``
    - docs/DRIVER-AGENT-SELECTION.md ``v1 Scope Definition``

    Boundary-only notes:
    - This contract locks authority and rejection semantics.
    - It intentionally does not migrate the current ``JudgeConfig`` runtime
      fields in this step.
    """

    contract_id: str
    source_step_id: str
    config_shape: tuple[str, ...]
    supported_modes: tuple[JudgeSelectionMode, ...]
    prompt_authority_rules: tuple[str, ...]
    structured_output_rules: tuple[str, ...]
    rejection_cases: tuple[str, ...]
    runtime_outcomes: tuple[str, ...]
    migration_rules: tuple[str, ...]


JUDGE_AGENT_SELECTION_CONTRACT: Final[JudgeAgentSelectionContract] = JudgeAgentSelectionContract(
    contract_id="driver-judge-agent-selection-v1",
    source_step_id="driver-agent-selection-contract.pin-contract",
    config_shape=(
        "judge.runner",
        "judge.external_agent_name",
        "judge.structured_output",
        "judge.timeout",
        "judge.preflight",
        "judge.evidence_validation",
        "judge.failure_classification",
        "judge.escalation",
        "judge.gate_assessment",
        "judge.cold_context",
        "judge.anomaly",
    ),
    supported_modes=("prompt_only", "external_agent"),
    prompt_authority_rules=(
        "external_agent mode must not inject vectl bundled judge prompt",
        "prompt_only mode must inject vectl bundled judge prompt",
        "prompt_only mode keeps bundled prompt as sole prompt authority",
        "no hybrid prompt layering is allowed",
    ),
    structured_output_rules=(
        "structured_output remains an output-contract concern in both modes",
        "verdict-shape enforcement survives even when bundled prompt is absent in external mode",
    ),
    rejection_cases=(
        "judge.external_agent_name set for runner without verified agent-selection capability",
        "named external judge agent cannot be reliably verified during preflight",
        "runner reports agent-not-found or unsupported-agent during invocation",
    ),
    runtime_outcomes=(
        "judge.external_agent_name unset/null => bundled judge prompt runtime",
        "judge.external_agent_name set + supported runner + existing agent => "
        "external judge runtime without bundled prompt",
        "judge.external_agent_name set + unsupported runner => hard config/startup error",
        "judge.external_agent_name set + missing agent => hard startup/runtime error",
    ),
    migration_rules=(
        "legacy judge.agent_name=judge migrates to judge.external_agent_name=null",
        "non-default legacy judge.agent_name may migrate to "
        "judge.external_agent_name with warning and validation",
    ),
)


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


JudgeOutcomeKind = Literal[
    "verdict",
    "timeout",
    "parse_error",
    "malformed_output",
    "empty_output",
]


@dataclass(frozen=True)
class JudgeRecoveryPolicyInput:
    """Input contract for classifying judge failures and recovery action.

    Source:
    - docs/DRIVER-ARCHITECTURE.md Section 2.10 (verdict extraction boundary,
      `FAILURE`/`ESCALATION` trigger points, and REPLAN planner wiring)
    - docs/JUDGE-AGENT-PROMPT.md TYPE: failure and TYPE: escalation verdict
      semantics (retry/fallback/halt/remediation branches)
    - docs/DRIVER-CONTINUITY-FOUNDATION.md Section 4 and Section 7 (judge
      outcomes inform continuity handoff, but do not become durable authority)

    This contract is boundary-only and intentionally excludes durable
    resume/restart controller ownership.
    """

    step_id: str
    judgment_type: JudgmentType
    outcome_kind: JudgeOutcomeKind
    attempt_key: str
    failure_count: int
    retry_budget: int
    fallback_runner_name: str | None
    verdict: JudgmentVerdict | None = None


@dataclass(frozen=True)
class JudgePromptAuthorityContract:
    """Contract pins for judge prompt loading authority migration.

    Source:
    - Step contract intent: `driver-prompt-foundation.design-and-test`
    - docs/DRIVER-ARCHITECTURE.md Section 2.10 (judge system prompt authority)
    - docs/JUDGE-AGENT-PROMPT.md (semantic authority payload)

    This contract is boundary-only and intentionally does not implement package
    resource loading. Runtime implementation is owned by downstream migration
    steps declared in ``implementation_owner_step`` and
    ``authority_migration_owner_step``.
    """

    contract_id: str
    source_step_id: str
    docs_authority_reference: str
    package_resource: str
    package_name: str
    implementation_owner_step: str
    authority_migration_owner_step: str
    exposed_gaps: tuple[str, ...]


JUDGE_PROMPT_AUTHORITY_CONTRACT = JudgePromptAuthorityContract(
    contract_id="driver-judge-prompt-authority-migration-a1",
    source_step_id="driver-prompt-foundation.design-and-test",
    docs_authority_reference="docs/JUDGE-AGENT-PROMPT.md",
    package_resource="judge_agent_prompt.md",
    package_name="vectl.driver",
    implementation_owner_step="driver-prompt-migration.package-resource-loader",
    authority_migration_owner_step="driver-prompt-migration.authority-reference-migration",
    exposed_gaps=(
        "docs mirror drift: docs/JUDGE-AGENT-PROMPT.md may diverge from packaged resource",
        "verify_docs_packaged_sync() enforces drift visibility (run as part of CI/pre-commit)",
    ),
)


def _load_packaged_judge_system_prompt(*, package_name: str, resource_name: str) -> str:
    """Boundary stub for packaged judge prompt loading.

    Args:
        package_name: Python package containing prompt resources.
        resource_name: Prompt resource filename inside ``package_name``.

    Returns:
        The loaded judge system prompt content.

    Raises:
        RuntimeError: If package/resource cannot be resolved or loaded.
    """
    try:
        package_resource = importlib.resources.files(package_name).joinpath(resource_name)
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "Judge prompt package is not importable: "
            f"package={package_name!r}. Ensure vectl is installed and package resources "
            "are included in the wheel/sdist build metadata."
        ) from exc

    if not package_resource.is_file():
        raise RuntimeError(
            "Judge prompt resource is missing from installed package: "
            f"package={package_name!r}, resource={resource_name!r}. "
            "Rebuild/reinstall with pyproject package-data including this file."
        )

    prompt_text = package_resource.read_text(encoding="utf-8")
    if not prompt_text.strip():
        raise RuntimeError(
            "Judge prompt resource is empty: "
            f"package={package_name!r}, resource={resource_name!r}. "
            "Populate the packaged prompt content before running vectl driver."
        )
    return prompt_text


def verify_docs_packaged_sync() -> tuple[bool, str]:
    """Check whether docs/JUDGE-AGENT-PROMPT.md is in sync with packaged resource.

    Returns:
        Tuple of (is_synced, diagnostic_message).
        is_synced=True means docs contains all key packaged content.
        is_synced=False with diagnostic describes the divergence.

    This function makes docs/packaged drift visible rather than silently tolerated.
    Run as part of CI/pre-commit to catch drift before it affects behavior.

    Drift detection approach:
    - Docs is the extended spec/mirror with examples and commentary
    - Packaged is the clean runtime prompt
    - We check that key content markers from packaged appear in docs
    - This allows docs to have extra content without triggering false drift

    Drifts detected (actual prompt content divergence):
    - Required sections missing from docs (would affect runtime behavior)
    - Verdict format specification mismatch
    - Missing judgment type definitions
    """
    import importlib.resources

    docs_path = Path("docs/JUDGE-AGENT-PROMPT.md")
    packaged_name = JUDGE_PROMPT_AUTHORITY_CONTRACT.package_resource
    package_name = JUDGE_PROMPT_AUTHORITY_CONTRACT.package_name

    if not docs_path.exists():
        return (
            True,
            "docs/JUDGE-AGENT-PROMPT.md does not exist (packaged resource is authoritative)",
        )

    try:
        packaged_resource = importlib.resources.files(package_name).joinpath(packaged_name)
    except ModuleNotFoundError:
        return False, f"packaged resource {package_name}/{packaged_name} not importable"

    if not packaged_resource.is_file():
        return False, f"packaged resource {package_name}/{packaged_name} is not a file"

    docs_content = docs_path.read_text(encoding="utf-8")
    packaged_content = packaged_resource.read_text(encoding="utf-8")

    # Exact match (edge case)
    if docs_content.strip() == packaged_content.strip():
        return True, "docs/JUDGE-AGENT-PROMPT.md matches packaged resource exactly"

    # Key sections that MUST appear in docs if packaged has them
    # These are the core runtime instructions that affect judge behavior
    key_sections = [
        "## Response Format (MANDATORY)",
        '"verdict": "<ACCEPT|REJECT|RETRY|SWITCH_AGENT|REPLAN|DEFER|HALT>"',
        '"suggested_action"',
        '"planner_instruction"',
        "## Judgment Types",
        "### TYPE: preflight",
        "### TYPE: evidence",
        "### TYPE: failure",
        "### TYPE: escalation",
        "### TYPE: gate",
        "### TYPE: anomaly",
        "### TYPE: cold_context",
    ]

    missing_sections: list[str] = []
    for section in key_sections:
        if section in packaged_content and section not in docs_content:
            missing_sections.append(section)

    if missing_sections:
        return False, (
            f"docs/packaged drift detected: key sections missing from docs: {missing_sections}. "
            "These sections exist in packaged but not in docs, which would affect runtime."
        )

    return True, (
        "docs/JUDGE-AGENT-PROMPT.md contains all key packaged content "
        "(docs may have extra examples/commentary; this is expected for doc-mirror)"
    )


def decide_judge_recovery_policy(
    policy_input: JudgeRecoveryPolicyInput,
) -> JudgeContinuityPolicyOutput:
    """Classify judge outcome into retry/fallback/halt/remediation policy.

    Source:
    - docs/DRIVER-ARCHITECTURE.md Section 2.10 (hard-fail parse/timeout
      handling and REPLAN/planner routing commitments)
    - docs/JUDGE-AGENT-PROMPT.md TYPE: escalation decision framework
    - docs/DRIVER-CONTINUITY-FOUNDATION.md Section 4 (Abort/failure handoff)

    Policy semantics:
    - timeout: retry within retry budget, then fallback/halt
    - parse/malformed/empty output: fallback if available, else halt
    - verdict-driven: REPLAN->planner_remediation, RETRY->retry, HALT/REJECT->halt

    The output is intentionally continuity-consumable and does not assume durable
    resume/restart ownership.
    """

    retry_budget = max(policy_input.retry_budget, 0)
    fallback_runner = policy_input.fallback_runner_name
    if fallback_runner is not None and not fallback_runner.strip():
        fallback_runner = None

    if policy_input.outcome_kind == "timeout":
        if policy_input.failure_count <= retry_budget:
            return JudgeContinuityPolicyOutput(
                step_id=policy_input.step_id,
                judgment_type=policy_input.judgment_type.value,
                judge_outcome="timeout",
                action="retry",
                provenance="judge_transport_timeout",
                continuity_recovery_reason="judge_timeout_within_retry_budget",
                attempt_key=policy_input.attempt_key,
            )
        if fallback_runner is not None:
            return JudgeContinuityPolicyOutput(
                step_id=policy_input.step_id,
                judgment_type=policy_input.judgment_type.value,
                judge_outcome="timeout",
                action="fallback",
                provenance="judge_transport_timeout",
                continuity_recovery_reason="judge_timeout_retry_budget_exhausted",
                attempt_key=policy_input.attempt_key,
                fallback_runner_name=fallback_runner,
            )
        return JudgeContinuityPolicyOutput(
            step_id=policy_input.step_id,
            judgment_type=policy_input.judgment_type.value,
            judge_outcome="timeout",
            action="halt",
            provenance="judge_transport_timeout",
            continuity_recovery_reason="judge_timeout_no_fallback_runner",
            attempt_key=policy_input.attempt_key,
            halt_reason="Judge timeout exceeded retry budget and no fallback is configured",
        )

    if policy_input.outcome_kind in {"parse_error", "malformed_output", "empty_output"}:
        if fallback_runner is not None:
            return JudgeContinuityPolicyOutput(
                step_id=policy_input.step_id,
                judgment_type=policy_input.judgment_type.value,
                judge_outcome=policy_input.outcome_kind,
                action="fallback",
                provenance="judge_output_contract_violation",
                continuity_recovery_reason="judge_output_unusable_fallback_runner_available",
                attempt_key=policy_input.attempt_key,
                fallback_runner_name=fallback_runner,
            )
        return JudgeContinuityPolicyOutput(
            step_id=policy_input.step_id,
            judgment_type=policy_input.judgment_type.value,
            judge_outcome=policy_input.outcome_kind,
            action="halt",
            provenance="judge_output_contract_violation",
            continuity_recovery_reason="judge_output_unusable_no_fallback_runner",
            attempt_key=policy_input.attempt_key,
            halt_reason=("Judge produced unusable output and no fallback runner is configured"),
        )

    verdict = policy_input.verdict
    if verdict is None:
        return JudgeContinuityPolicyOutput(
            step_id=policy_input.step_id,
            judgment_type=policy_input.judgment_type.value,
            judge_outcome="missing_verdict",
            action="halt",
            provenance="judge_policy_input_invalid",
            continuity_recovery_reason="verdict_outcome_without_verdict_payload",
            attempt_key=policy_input.attempt_key,
            halt_reason="Judge recovery policy received outcome=verdict without verdict payload",
        )

    verdict_name = verdict.verdict
    reason_lower = verdict.reason.lower()
    provenance = "judge_verdict"
    marker = "provenance="
    if marker in reason_lower:
        after = reason_lower.split(marker, 1)[1]
        token = after.split(",", 1)[0].split(";", 1)[0].split(" ", 1)[0].strip()
        if token:
            provenance = f"judge_verdict:{token}"

    if verdict_name == "REPLAN":
        instruction = verdict.planner_instruction
        if instruction is None or not instruction.strip():
            return JudgeContinuityPolicyOutput(
                step_id=policy_input.step_id,
                judgment_type=policy_input.judgment_type.value,
                judge_outcome="REPLAN",
                action="halt",
                provenance=provenance,
                continuity_recovery_reason="replan_without_planner_instruction",
                attempt_key=policy_input.attempt_key,
                halt_reason="REPLAN verdict missing planner_instruction",
            )
        return JudgeContinuityPolicyOutput(
            step_id=policy_input.step_id,
            judgment_type=policy_input.judgment_type.value,
            judge_outcome="REPLAN",
            action="planner_remediation",
            provenance=provenance,
            continuity_recovery_reason="judge_requested_planner_remediation",
            attempt_key=policy_input.attempt_key,
            planner_instruction=instruction,
        )

    if verdict_name == "RETRY":
        return JudgeContinuityPolicyOutput(
            step_id=policy_input.step_id,
            judgment_type=policy_input.judgment_type.value,
            judge_outcome="RETRY",
            action="retry",
            provenance=provenance,
            continuity_recovery_reason="judge_requested_retry",
            attempt_key=policy_input.attempt_key,
        )

    if verdict_name == "SWITCH_AGENT":
        if fallback_runner is not None:
            return JudgeContinuityPolicyOutput(
                step_id=policy_input.step_id,
                judgment_type=policy_input.judgment_type.value,
                judge_outcome="SWITCH_AGENT",
                action="fallback",
                provenance=provenance,
                continuity_recovery_reason="judge_requested_alternate_execution_route",
                attempt_key=policy_input.attempt_key,
                fallback_runner_name=fallback_runner,
            )
        return JudgeContinuityPolicyOutput(
            step_id=policy_input.step_id,
            judgment_type=policy_input.judgment_type.value,
            judge_outcome="SWITCH_AGENT",
            action="retry",
            provenance=provenance,
            continuity_recovery_reason="switch_agent_without_runner_fallback",
            attempt_key=policy_input.attempt_key,
        )

    if verdict_name in {"HALT", "REJECT"}:
        return JudgeContinuityPolicyOutput(
            step_id=policy_input.step_id,
            judgment_type=policy_input.judgment_type.value,
            judge_outcome=verdict_name,
            action="halt",
            provenance=provenance,
            continuity_recovery_reason="judge_requested_halt",
            attempt_key=policy_input.attempt_key,
            halt_reason=verdict.reason,
        )

    return JudgeContinuityPolicyOutput(
        step_id=policy_input.step_id,
        judgment_type=policy_input.judgment_type.value,
        judge_outcome=verdict_name,
        action="retry",
        provenance=provenance,
        continuity_recovery_reason="judge_verdict_default_retry",
        attempt_key=policy_input.attempt_key,
    )


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
    Blueprint Reference (doc-mirror): docs/JUDGE-AGENT-PROMPT.md
    Runtime prompt authority: packaged ``judge_agent_prompt.md`` resource in ``vectl.driver``.
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
        self._config = config
        self._observer = observer
        self._system_prompt = _load_judge_system_prompt()
        self._structured_schema_path: str | None = None
        self._runner_override: JudgeRunner | None = None

        if self._config.structured_output and self._config.runner in {"claude", "codex"}:
            self._structured_schema_path = _write_schema_tempfile(VERDICT_SCHEMA)

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
        flag_by_type: dict[JudgmentType, bool] = {
            JudgmentType.PREFLIGHT: self._config.preflight,
            JudgmentType.EVIDENCE: self._config.evidence_validation,
            JudgmentType.FAILURE: self._config.failure_classification,
            JudgmentType.ESCALATION: self._config.escalation,
            JudgmentType.GATE: self._config.gate_assessment,
            JudgmentType.ANOMALY: self._config.anomaly,
            JudgmentType.COLD_CONTEXT: self._config.cold_context,
        }
        return flag_by_type[judgment_type]

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
        if not self.is_enabled(request.type):
            verdict = JudgmentVerdict(
                verdict="DEFER",
                reason=f"Judgment type '{request.type.value}' is disabled by configuration",
                suggested_action=None,
                planner_instruction=None,
            )
            self._emit_judgment_event(request=request, verdict=verdict, latency_ms=0.0)
            return verdict

        self._validate_request(request)
        user_prompt = self._render_request(request)

        started = time.monotonic()
        try:
            raw_output = await self._invoke_runner(
                system_prompt=self._system_prompt,
                user_prompt=user_prompt,
            )
            verdict = self._parse_verdict(raw_output=raw_output, request=request)
        except JudgmentTimeoutError as exc:
            raise JudgmentTimeoutError(
                judgment_type=request.type.value,
                step_id=request.step_id,
                timeout_seconds=self._config.timeout,
            ) from exc
        except JudgmentParseError as exc:
            raise JudgmentParseError(
                judgment_type=request.type.value,
                step_id=request.step_id,
                raw_output=exc.raw_output,
            ) from exc

        self._emit_judgment_event(
            request=request,
            verdict=verdict,
            latency_ms=(time.monotonic() - started) * 1000.0,
        )
        return verdict

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
        schema = CONTEXT_SCHEMAS[request.type]
        required_fields = schema["required"]
        missing_fields = [field for field in required_fields if field not in request.context]
        if missing_fields:
            missing = ", ".join(missing_fields)
            raise ValueError(f"Missing required context fields for {request.type.value}: {missing}")

    def _render_request(self, request: JudgmentRequest) -> str:
        """Render a JudgmentRequest into a user prompt for the judge.

        Args:
            request: The judgment request to render.

        Returns:
            A formatted string suitable for the judge's structured input.

        Architecture: docs/DRIVER-ARCHITECTURE.md Section 2.10, request rendering
        """
        payload: dict[str, object] = {
            "type": request.type.value,
            "step_id": request.step_id,
            "context": request.context,
            "failure_history": request.failure_history,
            "plan_summary": request.plan_summary,
        }
        return json.dumps(payload, ensure_ascii=False, indent=2)

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
        timeout_seconds = float(self._config.timeout)

        if self._runner_override is not None:
            try:
                if self._config.structured_output:
                    verdict_obj = await asyncio.wait_for(
                        self._runner_override.dispatch_structured(
                            system_prompt=system_prompt,
                            user_prompt=user_prompt,
                            schema=VERDICT_SCHEMA,
                            timeout=timeout_seconds,
                        ),
                        timeout=timeout_seconds,
                    )
                    return json.dumps(verdict_obj, ensure_ascii=False)

                return await asyncio.wait_for(
                    self._runner_override.dispatch_text(
                        system_prompt=system_prompt,
                        user_prompt=user_prompt,
                        timeout=timeout_seconds,
                    ),
                    timeout=timeout_seconds,
                )
            except TimeoutError as exc:
                raise JudgmentTimeoutError(
                    judgment_type="unknown",
                    step_id="unknown",
                    timeout_seconds=int(timeout_seconds),
                ) from exc

        command, payload = self._build_subprocess_command(system_prompt, user_prompt)
        try:
            process = await asyncio.create_subprocess_exec(
                *command,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError as exc:
            raise JudgmentParseError(
                judgment_type="unknown",
                step_id="unknown",
                raw_output=f"judge runner not found: {self._config.runner}",
            ) from exc

        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                process.communicate(payload.encode("utf-8")),
                timeout=timeout_seconds,
            )
        except TimeoutError as exc:
            process.kill()
            raise JudgmentTimeoutError(
                judgment_type="unknown",
                step_id="unknown",
                timeout_seconds=int(timeout_seconds),
            ) from exc

        stdout_text = stdout_bytes.decode("utf-8", errors="replace").strip()
        stderr_text = stderr_bytes.decode("utf-8", errors="replace").strip()

        if process.returncode not in (0, None):
            raw = stdout_text or stderr_text or f"runner exited {process.returncode}"
            raise JudgmentParseError(
                judgment_type="unknown",
                step_id="unknown",
                raw_output=raw,
            )

        if not stdout_text:
            raise JudgmentParseError(
                judgment_type="unknown",
                step_id="unknown",
                raw_output="empty output from judge runner",
            )

        return _extract_verdict_payload(stdout_text)

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
        try:
            data = json.loads(raw_output)
        except json.JSONDecodeError as exc:
            raise JudgmentParseError(
                judgment_type=request.type.value,
                step_id=request.step_id,
                raw_output=raw_output,
            ) from exc

        if not isinstance(data, dict):
            raise JudgmentParseError(
                judgment_type=request.type.value,
                step_id=request.step_id,
                raw_output=raw_output,
            )

        expected_keys = {"verdict", "reason", "suggested_action", "planner_instruction"}
        if set(data.keys()) != expected_keys:
            raise JudgmentParseError(
                judgment_type=request.type.value,
                step_id=request.step_id,
                raw_output=raw_output,
            )

        verdict_raw = data["verdict"]
        reason_raw = data["reason"]
        suggested_raw = data["suggested_action"]
        planner_raw = data["planner_instruction"]

        if not isinstance(verdict_raw, str) or verdict_raw not in VERDICT_VALUES:
            raise JudgmentParseError(
                judgment_type=request.type.value,
                step_id=request.step_id,
                raw_output=raw_output,
            )

        if not isinstance(reason_raw, str) or not reason_raw.strip():
            raise JudgmentParseError(
                judgment_type=request.type.value,
                step_id=request.step_id,
                raw_output=raw_output,
            )

        if suggested_raw is not None and not isinstance(suggested_raw, str):
            raise JudgmentParseError(
                judgment_type=request.type.value,
                step_id=request.step_id,
                raw_output=raw_output,
            )
        if planner_raw is not None and not isinstance(planner_raw, str):
            raise JudgmentParseError(
                judgment_type=request.type.value,
                step_id=request.step_id,
                raw_output=raw_output,
            )

        if verdict_raw == "SWITCH_AGENT" and not suggested_raw:
            raise JudgmentParseError(
                judgment_type=request.type.value,
                step_id=request.step_id,
                raw_output=raw_output,
            )
        if verdict_raw != "SWITCH_AGENT" and suggested_raw is not None:
            raise JudgmentParseError(
                judgment_type=request.type.value,
                step_id=request.step_id,
                raw_output=raw_output,
            )

        if verdict_raw == "REPLAN" and not planner_raw:
            raise JudgmentParseError(
                judgment_type=request.type.value,
                step_id=request.step_id,
                raw_output=raw_output,
            )
        if verdict_raw != "REPLAN" and planner_raw is not None:
            raise JudgmentParseError(
                judgment_type=request.type.value,
                step_id=request.step_id,
                raw_output=raw_output,
            )

        return JudgmentVerdict(
            verdict=verdict_raw,
            reason=reason_raw,
            suggested_action=suggested_raw,
            planner_instruction=planner_raw,
        )

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
        self._observer.emit(
            "JUDGMENT",
            type=request.type.value,
            step_id=request.step_id,
            verdict=verdict.verdict,
            reason=verdict.reason,
            suggested_action=verdict.suggested_action,
            planner_instruction=verdict.planner_instruction,
            latency_ms=latency_ms,
        )

    def _build_subprocess_command(
        self, system_prompt: str, user_prompt: str
    ) -> tuple[list[str], str]:
        """Build subprocess command and stdin payload.

        Architecture: docs/DRIVER-ARCHITECTURE.md Section 2.10 Structured Output Strategy
        """
        runner = self._config.runner
        payload = user_prompt

        if runner == "claude":
            command = [runner, "-p", "--output-format", "json", "--system-prompt", system_prompt]
            if self._config.structured_output and self._structured_schema_path is not None:
                command.extend(["--json-schema", self._structured_schema_path])
            return command, payload

        if runner == "codex":
            runner_settings = _load_runner_settings_from_driver_yaml("codex")
            if runner_settings is None:
                raise JudgmentParseError(
                    judgment_type="unknown",
                    step_id="unknown",
                    raw_output=(
                        "Missing or invalid codex runner contract in driver.yaml; "
                        "judge codex argv must derive from configured runners.codex args"
                    ),
                )

            configured_command, configured_args, configured_prompt_mode = runner_settings
            command = [configured_command] + _render_runner_args(
                configured_args,
                workdir=str(Path.cwd()),
                agent=self._config.agent_name,
            )
            if configured_prompt_mode == "stdin_dash" and "-" not in command:
                command.append("-")

            payload = f"SYSTEM PROMPT:\n{system_prompt}\n\n{user_prompt}"
            return command, payload

        if runner == "opencode":
            command = [runner, "run", "--format", "json"]
            payload = f"SYSTEM PROMPT:\n{system_prompt}\n\n{user_prompt}"
            return command, payload

        command = [runner]
        payload = f"SYSTEM PROMPT:\n{system_prompt}\n\n{user_prompt}"
        return command, payload


def _load_judge_system_prompt() -> str:
    """Load judge system prompt from packaged runtime resource.

    Architecture: docs/DRIVER-ARCHITECTURE.md Section 2.10
    """
    contract = JUDGE_PROMPT_AUTHORITY_CONTRACT
    return _load_packaged_judge_system_prompt(
        package_name=contract.package_name,
        resource_name=contract.package_resource,
    )


def _write_schema_tempfile(schema: dict[str, object]) -> str:
    """Write verdict schema to a temporary file and return path."""
    with NamedTemporaryFile(mode="w", encoding="utf-8", suffix=".json", delete=False) as fp:
        json.dump(schema, fp)
        fp.flush()
        return fp.name


def _load_runner_settings_from_driver_yaml(
    runner_name: str,
) -> tuple[str, list[str], str | None] | None:
    """Load runner command settings from local ``driver.yaml`` when available.

    Source authority:
    - driver.yaml ``runners.<name>`` entry (runtime configuration authority)
    - docs/DRIVER-ARCHITECTURE.md Section 2.3 (configuration schema)

    Returns:
        Tuple of ``(command, args, prompt_mode)`` when found and valid;
        otherwise ``None``.
    """
    config_path = Path("driver.yaml")
    if not config_path.exists():
        return None

    try:
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except yaml.YAMLError:
        return None

    if not isinstance(raw, dict):
        return None

    runners = raw.get("runners")
    if not isinstance(runners, dict):
        return None

    runner = runners.get(runner_name)
    if not isinstance(runner, dict):
        return None

    command = runner.get("command")
    if not isinstance(command, str) or not command.strip():
        return None

    raw_args = runner.get("args", [])
    if not isinstance(raw_args, list) or any(not isinstance(arg, str) for arg in raw_args):
        return None

    prompt_mode = runner.get("prompt_mode")
    if prompt_mode is not None and not isinstance(prompt_mode, str):
        return None

    return command, list(raw_args), prompt_mode


def _render_runner_args(args: list[str], *, workdir: str, agent: str) -> list[str]:
    """Render common placeholder tokens in runner args."""
    rendered: list[str] = []
    for arg in args:
        rendered.append(arg.replace("{workdir}", workdir).replace("{agent}", agent))
    return rendered


def _extract_verdict_payload(stdout_text: str) -> str:
    """Extract verdict JSON payload from runner output.

    Strict policy:
    - If output is a single JSON object, return it unchanged.
    - If output is JSONL (OpenCode), extract `text` event content and require it to be JSON.
    - Reject loose text wrappers around JSON.
    """
    stripped = stdout_text.strip()
    if not stripped:
        raise JudgmentParseError(
            judgment_type="unknown",
            step_id="unknown",
            raw_output="empty output from judge runner",
        )

    # Single JSON object path
    try:
        data = json.loads(stripped)
    except json.JSONDecodeError:
        data = None

    if isinstance(data, dict):
        # Claude structured output envelope
        if "structured_output" in data:
            return json.dumps(data["structured_output"], ensure_ascii=False)
        if "result" in data and isinstance(data["result"], (dict, list)):
            return json.dumps(data["result"], ensure_ascii=False)
        if "result" in data and isinstance(data["result"], str):
            return data["result"]
        return stripped

    # JSONL path (OpenCode-style)
    saw_json_event = False
    text_parts: list[str] = []
    for line in stripped.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue

        if not isinstance(event, dict):
            continue

        saw_json_event = True

        if event.get("type") == "text":
            part = event.get("part", {})
            text = part.get("text")
            if isinstance(text, str) and text.strip():
                text_parts.append(text.strip())
            continue

        # Codex JSONL path: item.completed may carry structured output, result,
        # or text payloads nested under item.output.
        if event.get("type") == "item.completed":
            item = event.get("item", {})
            if isinstance(item, dict):
                output = item.get("output")
                if isinstance(output, dict):
                    structured_output = output.get("structured_output")
                    if isinstance(structured_output, dict):
                        return json.dumps(structured_output, ensure_ascii=False)
                    if isinstance(structured_output, str) and structured_output.strip():
                        return structured_output.strip()

                    result = output.get("result")
                    if isinstance(result, dict):
                        return json.dumps(result, ensure_ascii=False)
                    if isinstance(result, str) and result.strip():
                        return result.strip()

                    nested_text = output.get("text")
                    if isinstance(nested_text, str) and nested_text.strip():
                        text_parts.append(nested_text.strip())

                text = item.get("text")
                if isinstance(text, str) and text.strip():
                    text_parts.append(text.strip())

    if not text_parts:
        if saw_json_event:
            raise JudgmentParseError(
                judgment_type="unknown",
                step_id="unknown",
                raw_output=stripped,
            )
        return stripped

    candidate = text_parts[-1]
    # strict: candidate itself must be JSON, not markdown wrapper
    try:
        json.loads(candidate)
    except json.JSONDecodeError as exc:
        raise JudgmentParseError(
            judgment_type="unknown",
            step_id="unknown",
            raw_output=candidate,
        ) from exc
    return candidate


# =============================================================================
# STRUCTURED OUTPUT STRATEGY (DOCUMENTATION)
# =============================================================================

"""
Structured Output Strategy (from Architecture doc Section 2.10):

When JudgeConfig.structured_output is True, the judge uses runner-specific
mechanisms to guarantee valid JSON verdicts, eliminating parse failures:

| Runner     | Mechanism                          | Verdict extraction                    |
|-----------|-----------------------------------|---------------------------------------|
| Claude CLI | --json-schema <schema_file>       | Response structured_output = |
|            |                                   | guaranteed valid JSON |
| OpenCode   | --format json                     | Parse from JSONL text event |
|            |                                   | (best-effort) |
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

| Check                         | Mechanism                            | Judge involvement        |
|------------------------------|--------------------------------------|--------------------------|
| Evidence schema validation   | Rule-based: required fields, YAML    | None (fast reject)       |
|                              | parseable                            |                          |
| Evidence content adequacy    | Requires understanding               | EVIDENCE judgment        |
| Failure count < 3            | Rule-based threshold                 | None (automatic retry)   |
| Failure count >= 3           | Needs classification                 | ESCALATION judgment      |
| Preflight risk signals       | Keyword detection                    | PREFLIGHT judgment       |
| Gate blocker vs suggestion   | Severity classification              | GATE judgment downstream |
| Claims/plan anomaly repair   | Anomaly type classification          | ANOMALY judgment         |
| Gate dispatch context        | Inclusion/exclusion for isolation    | COLD_CONTEXT judgment    |
| pruning                      |                                      |                          |

The boundary is: **rules handle structure; the judge handles semantics.**

Implementation Notes:
    - loop.py owns the decision of WHEN to call judge.
    - judge.py owns the invocation and parsing.
    - Rule-based checks in loop.py should fast-path without judge.
    - Judge should be called ONLY when semantics are needed.

See Architecture doc Section 2.10, "Judge vs Rules Decision Boundary".
"""
