"""Dispatch coordinator runtime helpers.

Extracted from dispatch_policy compatibility module.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from vectl.models import Step
from vectl.orchestration.contracts import (
    ControlDecision,
    CoreSnapshot,
    DispatchRoleSource,
    DispatchSpec,
    PromptBundle,
    ResolutionCase,
    RosterSnapshot,
    RuntimeSnapshot,
    StructuredReviewResult,
)
from vectl.orchestration.core_adapter import CoreAdapter
from vectl.orchestration.dispatch_policy import (ConfigPromptRegistry, ConfigRoleProfileRegistry, DispatchAuthorityError, ReviewResultParseError, _build_context_messages, _build_template_data, _select_prompt_content, step_verify_to_verify_mode)
from vectl.orchestration.step_data import StepData

@dataclass(frozen=True)
class DispatchCoordinator:
    """Build DispatchSpec from ControlDecision, step data, and role registry.

    Authority: docs/ORCHESTRATION-PLANE-DISPATCH-AND-PROMPT-POLICY.md §6.1-6.3

    The dispatch coordinator is an orchestration-app-owned logic layer that sits
    between control and runtime (§6.3). It:

    1. Loads authoritative step data
    2. Resolves role/profile information
    3. Builds DispatchSpec
    4. Renders prompt content (via ConfigPromptRegistry)
    5. Constructs the runtime-facing execution request

    It must not be implemented by pushing prompt logic into control or runtime.
    """

    role_registry: ConfigRoleProfileRegistry
    prompt_registry: ConfigPromptRegistry
    core_adapter: CoreAdapter
    runner: str = "codex"

    def render_prompt_bundle(self, spec: DispatchSpec) -> PromptBundle:
        """Render the authoritative prompt bundle for a live dispatch spec.

        Authority:
            docs/ORCHESTRATION-PLANE-LIVE-AUTHORITY-CONTRACT-LOCK.md §3.1, §3.4, §4.1
            docs/ORCHESTRATION-PLANE-ORCH-APP-ROUTING.md §12.1

        Args:
            spec: Dispatch spec to validate and render.

        Returns:
            Prompt bundle rendered from the authoritative live dispatch spec.

        Raises:
            DispatchAuthorityError: If prompt/role checkpoints are missing.
        """
        profile = self.role_registry.get(spec.role_id)
        if spec.prompt_family != profile.prompt_family:
            raise DispatchAuthorityError(
                f"role {spec.role_id!r} requires prompt_family={profile.prompt_family!r} "
                f"but got {spec.prompt_family!r}"
            )
        if spec.output_contract != profile.output_contract:
            raise DispatchAuthorityError(
                f"role {spec.role_id!r} requires output_contract={profile.output_contract!r} "
                f"but got {spec.output_contract!r}"
            )
        if spec.mutation_policy != profile.mutation_policy:
            raise DispatchAuthorityError(
                f"role {spec.role_id!r} requires mutation_policy={profile.mutation_policy!r} "
                f"but got {spec.mutation_policy!r}"
            )
        if not self.prompt_registry.has_role(spec.role_id):
            raise DispatchAuthorityError(
                f"prompt registry has no authoritative support for role {spec.role_id!r}"
            )

        prompt_bundle = self.prompt_registry.render(spec)
        if not prompt_bundle.system_prompt.strip() or not prompt_bundle.task_prompt.strip():
            raise DispatchAuthorityError(
                f"prompt registry returned incomplete prompt bundle for role {spec.role_id!r}"
            )
        return prompt_bundle

    def build_dispatch_spec(
        self,
        decision: ControlDecision,
    ) -> DispatchSpec:
        """Build a DispatchSpec from a dispatch ControlDecision.

        Authority: docs/ORCHESTRATION-PLANE-DISPATCH-AND-PROMPT-POLICY.md §6.1

        Normal dispatch flow:
            1. Load authoritative step data (if step dispatch)
            2. Resolve role profile
            3. Build complete DispatchSpec

        Supports both legacy `kind='dispatch'` (single-step) and expanded
        `kind='dispatch_batch'` (multi-step) decisions. For batch decisions,
        only the first step is processed (batch dispatch is the scheduler's
        responsibility to iterate).

        Args:
            decision: ControlDecision with kind='dispatch' or 'dispatch_batch',
                step_ids, and role_bindings.

        Returns:
            Complete DispatchSpec with all fields populated from authoritative data.

        Raises:
            UnknownRoleError: If the resolved role ID is not in the registry.
            ValueError: If decision is not a dispatch decision.
        """
        if decision.kind not in ("dispatch", "dispatch_batch"):
            raise ValueError(
                f"DispatchCoordinator.build_dispatch_spec requires dispatch decision, "
                f"got kind={decision.kind!r}"
            )

        if not decision.step_ids:
            raise ValueError("DispatchCoordinator.build_dispatch_spec requires non-empty step_ids")

        # For both dispatch (single) and dispatch_batch (multi), process first step.
        step_id = decision.step_ids[0]
        role_override = decision.role_bindings.get(step_id)

        # 1. Load authoritative step data
        step_data = self.core_adapter.load_step_data_for_dispatch(step_id)
        if step_data is None:
            raise ValueError(f"Step data not found for step_id={step_id!r}")

        # 2. Resolve role (§9.1 precedence: step.agent > dispatch-request
        #    default > registry default)
        resolved_role, role_source = self.role_registry.resolve_role(
            step_data.agent,
            default_role_override=role_override,
        )

        # 3. Look up role profile
        profile = self.role_registry.get(resolved_role)

        # 4. Map verify mode from step data
        verify_mode = step_verify_to_verify_mode(step_data.verify)

        # 5. Build DispatchSpec
        return DispatchSpec(
            source_kind="step",
            source_id=step_id,
            role_id=resolved_role,
            role_source=role_source,
            execution_context=profile.execution_context,
            runner=profile.default_runner,
            session_mode="fresh",
            step_id=step_id,
            description=step_data.description,
            verification=step_data.verification or None,
            refs=step_data.refs,
            evidence_template=step_data.evidence_template or None,
            verify_mode=verify_mode,
            prompt_family=profile.prompt_family,
            output_contract=profile.output_contract,
            mutation_policy=profile.mutation_policy,
            checklist_inventory_revision=step_data.checklist_inventory_revision,
            checklist_inventory=step_data.checklist_inventory,
        )

    def build_resolution_subtask_spec(
        self,
        *,
        case_id: str,
        role_id: str,
        description: str,
        refs: tuple[str, ...] = (),
    ) -> DispatchSpec:
        """Build a DispatchSpec for resolution-subtask dispatch.

        Authority: docs/ORCHESTRATION-PLANE-DISPATCH-AND-PROMPT-POLICY.md §6.2, §15

        Resolution subtasks are dispatched by resolver for specialized
        subagent work (planner, reviewer, coder). This is NOT ordinary step
        dispatch — role_source is always "resolver".

        Args:
            case_id: The resolution case identifier.
            role_id: The subagent role (e.g., vectl-planner, gate-reviewer).
            description: Task description for the subagent.
            refs: Reference artifacts.

        Returns:
            DispatchSpec for the resolution subtask.

        Raises:
            UnknownRoleError: If role_id is not registered.
        """
        profile = self.role_registry.get(role_id)

        return DispatchSpec(
            source_kind="resolution_subtask",
            source_id=case_id,
            role_id=role_id,
            role_source="resolver",
            execution_context=profile.execution_context,
            runner=profile.default_runner,
            session_mode="fresh",
            description=description,
            refs=refs,
            prompt_family=profile.prompt_family,
            output_contract=profile.output_contract,
            mutation_policy=profile.mutation_policy,
        )


# ---------------------------------------------------------------------
# Review Result Normalization
# ---------------------------------------------------------------------


def normalize_review_result(
    result: StructuredReviewResult,
    case_id: str,
    core: CoreSnapshot,
    roster: RosterSnapshot,
    runtime: RuntimeSnapshot,
) -> ResolutionCase | None:
    """Normalize a structured review result per §14.1.

    Authority: docs/ORCHESTRATION-PLANE-DISPATCH-AND-PROMPT-POLICY.md §14

    Normalization rules (§14.1):
        - pass -> return None (continue normal flow)
        - needs_fix -> create explicit ResolutionCase(case_source='review_failed')
        - needs_replan -> create explicit ResolutionCase(case_source='review_failed')
        - operator_required -> create explicit ResolutionCase(case_source='review_failed')

    This keeps control simple: control does not interpret review prose; it
    only sees that unresolved/resolution work exists.

    Args:
        result: The structured review result to normalize.
        case_id: Unique identifier for the resolution case.
        core: Current core snapshot.
        roster: Current roster snapshot.
        runtime: Current runtime snapshot.

    Returns:
        ResolutionCase if non-pass (needs explicit resolution), None if pass.
    """
    if result.review_outcome == "pass":
        return None

    # All non-pass outcomes become explicit resolution cases (§14.1)
    return ResolutionCase(
        case_id=case_id,
        case_source="review_failed",
        reason=f"review outcome: {result.review_outcome}",
        summary=result.summary,
        core=core,
        roster=roster,
        runtime=runtime,
        artifact_refs=result.evidence_refs,
    )


def normalize_parse_failure(
    raw_output: str,
    role_id: str,
    case_id: str,
    core: CoreSnapshot,
    roster: RosterSnapshot,
    runtime: RuntimeSnapshot,
) -> ResolutionCase:
    """Create a ResolutionCase for unparseable review output.

    Authority: docs/ORCHESTRATION-PLANE-DISPATCH-AND-PROMPT-POLICY.md §13.3

    When a role declared as structured_review_result does not return a parseable
    result, this creates an explicit resolution case describing the contract
    violation. The system must not silently guess from natural language.

    The recommended handling is:
        - do not complete the reviewed step
        - create an explicit resolution case describing the contract violation
        - let resolver decide whether to retry, repair, or escalate

    Args:
        raw_output: The raw unparseable output.
        role_id: The role that produced the unparseable output.
        case_id: Unique identifier for the resolution case.
        core: Current core snapshot.
        roster: Current roster snapshot.
        runtime: Current runtime snapshot.

    Returns:
        ResolutionCase for the parse failure contract violation.
    """
    return ResolutionCase(
        case_id=case_id,
        case_source="review_failed",
        reason=(
            f"review/result contract violation: role {role_id!r} declared "
            "structured_review_result but returned unparseable output"
        ),
        summary=(
            f"Role {role_id!r} declared output_contract=structured_review_result but "
            f"produced output that could not be parsed as StructuredReviewResult. "
            f"Raw output preview: {raw_output[:200]}"
        ),
        core=core,
        roster=roster,
        runtime=runtime,
        artifact_refs=(),
    )
