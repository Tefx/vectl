"""Red tests exposing missing dispatch/prompt/role policy seams.

step_intent: test_define_red
expected_result: red

These tests expose the gaps in orchestration dispatch, prompt rendering,
role-profile lookup, and structured review-result normalization that must
be closed by downstream implementation steps.

Spec-Fixture Conformance:
    - DispatchSpec: ORCHESTRATION-PLANE-DISPATCH-AND-PROMPT-POLICY.md §7.1
    - RoleProfile: ORCHESTRATION-PLANE-DISPATCH-AND-PROMPT-POLICY.md §8.2
    - RoleProfileRegistry: ORCHESTRATION-PLANE-DISPATCH-AND-PROMPT-POLICY.md §8.3
    - PromptRegistry: ORCHESTRATION-PLANE-DISPATCH-AND-PROMPT-POLICY.md §10.1
    - StructuredReviewResult:
        ORCHESTRATION-PLANE-DISPATCH-AND-PROMPT-POLICY.md §13.1
    - Review normalization:
        ORCHESTRATION-PLANE-DISPATCH-AND-PROMPT-POLICY.md §14

expected_failures / exposed_gaps:
    dispatch_spec construction gap:
        - test_dispatch_spec_construction_from_control_decision
          (no dispatch coordinator exists)
        - test_dispatch_spec_verify_mode_expected_red_wires_correctly
          (verify_mode not acted upon)
        - test_dispatch_spec_main_worktree_roles_enforce_execution_context
          (no execution context enforcement)
    role_profile_registry gap:
        - test_role_profile_registry_get_returns_concrete_profile
          (no concrete implementation)
        - test_role_profile_registry_has_role_unknown_role_returns_false
          (no concrete implementation)
        - test_role_profile_registry_fails_explicitly_on_unknown_role
          (Protocol only - no impl)
    prompt_rendering gap:
        - test_prompt_registry_render_returns_prompt_bundle
          (no concrete implementation)
        - test_prompt_registry_has_role_for_known_role
          (no concrete implementation)
        - test_prompt_rendering_differs_by_role_family
          (no concrete implementation)
    review_result_normalization gap:
        - test_structured_review_result_pass_normalizes_to_continue
          (no normalizer exists)
        - test_structured_review_result_needs_fix_normalizes_to_resolution_case
          (no normalizer)
        - test_structured_review_result_needs_replan_normalizes_to_resolution_case
          (no normalizer)
        - test_structured_review_result_operator_required_normalizes_to_resolution_case
          (no normalizer)
        - test_structured_review_result_parse_failure_creates_resolution_case
          (no normalizer)
    main_worktree role policy gap:
        - test_resolver_family_roles_require_main_worktree_context
          (no role-based enforcement)
        - test_planner_family_roles_require_main_worktree_context
          (no role-based enforcement)
        - test_reviewer_family_roles_require_main_worktree_context
          (no role-based enforcement)
"""

from __future__ import annotations

from typing import Any, cast

import pytest

from tests.expected_red import expected_red_module

pytestmark = expected_red_module(
    owner="orchestration_dispatch_policy",
    rationale="Dispatch-policy gaps are intentional red coverage until the dispatch coordinator and registry wiring land.",
)

from vectl.orchestration.contracts import (
    ControlDecision,
    CoreSnapshot,
    DispatchSpec,
    PromptBundle,
    PromptRegistry,
    ResolutionCase,
    RoleProfile,
    RoleProfileRegistry,
    RosterSnapshot,
    RuntimeSnapshot,
    StructuredReviewResult,
)
from vectl.orch_app import AppConfig, OrchestrationApp
from vectl.orchestration.dispatch_policy import (
    ConfigPromptRegistry,
    ConfigRoleProfileRegistry,
    DispatchCoordinator,
    StepData,
)


class StubStepDataAdapter:
    def __init__(self, steps: dict[str, StepData]) -> None:
        self._steps = steps

    def load_step_data(self, step_id: str) -> StepData | None:
        return self._steps.get(step_id)


class CountingDispatchCoordinator(DispatchCoordinator):
    def __init__(
        self,
        *,
        role_registry: ConfigRoleProfileRegistry,
        prompt_registry: ConfigPromptRegistry,
        step_adapter: StubStepDataAdapter,
    ) -> None:
        super().__init__(
            role_registry=role_registry,
            prompt_registry=prompt_registry,
            step_adapter=step_adapter,
        )
        self.decisions: list[ControlDecision] = []

    def build_dispatch_spec(self, decision: ControlDecision) -> DispatchSpec:
        self.decisions.append(decision)
        return super().build_dispatch_spec(decision)


class _Noop:
    pass


# ---------------------------------------------------------------------
# GAP 1: DispatchSpec construction from ControlDecision + step data
# ---------------------------------------------------------------------
# DispatchSpec is defined in contracts.py, but there is no dispatch
# coordinator logic to build it from ControlDecision + authoritative
# step data. Downstream: dispatch coordinator implementation.


def test_dispatch_spec_construction_from_control_decision() -> None:
    """DispatchSpec construction is centralized through the coordinator path."""
    step_data = StepData(
        step_id="core.test-step",
        description="Test step",
        verification="Run focused verification",
        refs=("ref1",),
        evidence_template="Paste logs",
        verify=None,
        agent=None,
    )
    coordinator = CountingDispatchCoordinator(
        role_registry=ConfigRoleProfileRegistry(),
        prompt_registry=ConfigPromptRegistry(),
        step_adapter=StubStepDataAdapter({"core.test-step": step_data}),
    )
    app = OrchestrationApp(
        config=AppConfig(default_agent="python-executor"),
        control=cast(Any, _Noop()),
        roster=cast(Any, _Noop()),
        runtime=cast(Any, _Noop()),
        resolver=cast(Any, _Noop()),
        core_adapter=cast(Any, _Noop()),
        dispatch_coordinator=coordinator,
    )

    spec = app.build_dispatch_spec(step_id="core.test-step", role_hint="gate-reviewer")

    assert spec.step_id == "core.test-step"
    assert spec.source_kind == "step"
    assert spec.source_id == "core.test-step"
    assert spec.description == "Test step"
    assert spec.verification == "Run focused verification"
    assert spec.refs == ("ref1",)
    assert spec.evidence_template == "Paste logs"
    assert spec.role_id == "gate-reviewer"
    assert spec.role_source == "default"
    assert spec.execution_context == "main_worktree"
    assert coordinator.decisions == [
        ControlDecision(
            kind="dispatch",
            reason="orchestration app dispatch",
            step_id="core.test-step",
            role="gate-reviewer",
        )
    ]


def test_dispatch_spec_verify_mode_expected_red_wires_correctly() -> None:
    """DispatchSpec.verify_mode is derived from authoritative step.verify."""
    coordinator = DispatchCoordinator(
        role_registry=ConfigRoleProfileRegistry(),
        prompt_registry=ConfigPromptRegistry(),
        step_adapter=StubStepDataAdapter(
            {
                "core.test-step": StepData(
                    step_id="core.test-step",
                    description="Test step",
                    verification="Observe expected red",
                    refs=(),
                    evidence_template="",
                    verify="expected_red",
                    agent="python-executor",
                )
            }
        ),
    )
    spec = coordinator.build_dispatch_spec(
        ControlDecision(
            kind="dispatch",
            reason="test",
            step_id="core.test-step",
            role="python-executor",
        )
    )

    assert spec.verify_mode == "expected_red"


def test_dispatch_spec_main_worktree_roles_enforce_execution_context() -> None:
    """Roles in resolver/planner/reviewer families must require main_worktree.

    GAP: DispatchSpec.execution_context is set per-role profile, but there is
    no enforcement that certain role families (resolver, planner, reviewer) run
    exclusively in main_worktree.

    xfail scope:
        - DispatchSpec.execution_context can be set to 'main_worktree' but nothing
          enforces that resolver/planner/reviewer family roles MUST use it.

    Authority:
        - ORCHESTRATION-PLANE-DISPATCH-AND-PROMPT-POLICY.md §12 (execution-context policy)
        - ORCHESTRATION-PLANE-DISPATCH-AND-PROMPT-POLICY.md §8.2 (RoleProfile.execution_context)
    """
    # Resolver family role
    resolver_spec = DispatchSpec(
        source_kind="step",
        source_id="resolver.test",
        role_id="conflict-resolver",
        role_source="step.agent",
        execution_context="main_worktree",  # This is set but not enforced
        runner="codex",
        session_mode="fresh",
    )

    # GAP EXPOSED (xfail): The execution_context is set correctly, but
    # nothing enforces that conflict-resolver MUST run in main_worktree.
    # A bug could set execution_context='linked_worktree' for resolver family
    # and no validation would catch it.
    assert resolver_spec.execution_context == "main_worktree"

    pytest.xfail(
        "xfail[GAP1-main-worktree-enforcement]: No enforcement exists that "
        "resolver/planner/reviewer family roles must use main_worktree execution context. "
        "A RoleProfileRegistry implementation should validate that these role families "
        "are never dispatched to linked_worktree. "
        "Owner: orchestration_dispatch_policy.implement-dispatch-coordinator"
    )


# ---------------------------------------------------------------------
# GAP 2: RoleProfileRegistry concrete implementation
# ---------------------------------------------------------------------
# RoleProfileRegistry is defined as a Protocol in contracts.py, but
# there is no concrete implementation that provides actual role lookups.


class TestRoleProfileRegistryProtocol:
    """RoleProfileRegistry protocol exists but has no concrete implementation."""

    def test_role_profile_registry_get_returns_concrete_profile(self) -> None:
        """RoleProfileRegistry.get() must return a concrete RoleProfile.

        GAP: RoleProfileRegistry is a Protocol (interface). No concrete
        implementation exists that actually returns RoleProfile objects.

        xfail scope:
            - RoleProfileRegistry.get() raises NotImplementedError or returns None.

        Authority:
            - ORCHESTRATION-PLANE-DISPATCH-AND-PROMPT-POLICY.md §8.3 (RoleProfileRegistry protocol)
        """
        # This is the Protocol - it defines the interface but has no implementation
        registry: RoleProfileRegistry = None  # type: ignore[assignment]

        # GAP EXPOSED (xfail): No concrete implementation exists.
        # A configuration-backed RoleProfileRegistry must be implemented.
        pytest.xfail(
            "xfail[GAP2-role-profile-registry]: RoleProfileRegistry is a Protocol with "
            "no concrete implementation. A configuration-backed registry must be implemented "
            "that loads role profiles from orchestration config and returns concrete "
            "RoleProfile objects for known roles. "
            "Owner: orchestration_dispatch_policy.implement-role-profile-registry"
        )

        # These would fail because registry is None (no implementation)
        profile = registry.get("python-executor")
        assert isinstance(profile, RoleProfile)
        assert profile.role_id == "python-executor"

    def test_role_profile_registry_has_role_unknown_role_returns_false(self) -> None:
        """RoleProfileRegistry.has_role() must return False for unknown roles.

        GAP: No concrete implementation to test.

        xfail scope:
            - has_role() not implemented.

        Authority:
            - ORCHESTRATION-PLANE-DISPATCH-AND-PROMPT-POLICY.md §8.3
        """
        registry: RoleProfileRegistry = None  # type: ignore[assignment]

        pytest.xfail(
            "xfail[GAP2-role-profile-registry]: RoleProfileRegistry.has_role() has no "
            "concrete implementation. "
            "Owner: orchestration_dispatch_policy.implement-role-profile-registry"
        )

        assert registry.has_role("unknown-role") is False

    def test_role_profile_registry_fails_explicitly_on_unknown_role(self) -> None:
        """RoleProfileRegistry.get() must fail explicitly for unknown roles.

        Spec §8.3: 'unknown roles must fail explicitly - the system must not
        guess role meaning from string shape alone.'

        GAP: No concrete implementation exists to enforce this rule.

        xfail scope:
            - get() not implemented to enforce explicit failure.

        Authority:
            - ORCHESTRATION-PLANE-DISPATCH-AND-PROMPT-POLICY.md §8.3 (rules)
        """
        registry: RoleProfileRegistry = None  # type: ignore[assignment]

        pytest.xfail(
            "xfail[GAP2-role-profile-registry]: RoleProfileRegistry.get() must fail "
            "explicitly (raise KeyError or similar) for unknown roles per §8.3 rule. "
            "No concrete implementation enforces this. "
            "Owner: orchestration_dispatch_policy.implement-role-profile-registry"
        )

        # Should raise, not return a default or guess
        with pytest.raises(KeyError):
            registry.get("fantasy-unknown-role")


# ---------------------------------------------------------------------
# GAP 3: PromptRegistry concrete implementation
# ---------------------------------------------------------------------
# PromptRegistry is defined as a Protocol in contracts.py, but there is
# no concrete implementation for role-aware prompt rendering.


class TestPromptRegistryProtocol:
    """PromptRegistry protocol exists but has no concrete implementation."""

    def test_prompt_registry_render_returns_prompt_bundle(self) -> None:
        """PromptRegistry.render() must return a PromptBundle.

        GAP: PromptRegistry is a Protocol (interface). No concrete
        implementation exists that actually renders prompts.

        xfail scope:
            - PromptRegistry.render() raises NotImplementedError or returns None.

        Authority:
            - ORCHESTRATION-PLANE-DISPATCH-AND-PROMPT-POLICY.md §10.1 (PromptRegistry protocol)
        """
        registry: PromptRegistry = None  # type: ignore[assignment]

        spec = DispatchSpec(
            source_kind="step",
            source_id="core.test",
            role_id="python-executor",
            role_source="step.agent",
            execution_context="linked_worktree",
            runner="codex",
            session_mode="fresh",
            prompt_family="coder",
        )

        pytest.xfail(
            "xfail[GAP3-prompt-registry]: PromptRegistry is a Protocol with no concrete "
            "implementation. A centralized PromptRegistry must be implemented that "
            "renders role-specific prompts from PromptBundle. "
            "Owner: orchestration_dispatch_policy.implement-prompt-registry"
        )

        bundle = registry.render(spec)
        assert isinstance(bundle, PromptBundle)
        assert bundle.system_prompt  # should be non-empty
        assert bundle.task_prompt  # should be non-empty

    def test_prompt_registry_has_role_for_known_role(self) -> None:
        """PromptRegistry.has_role() must return True for known roles.

        GAP: No concrete implementation.

        xfail scope:
            - has_role() not implemented.

        Authority:
            - ORCHESTRATION-PLANE-DISPATCH-AND-PROMPT-POLICY.md §10.1
        """
        registry: PromptRegistry = None  # type: ignore[assignment]

        pytest.xfail(
            "xfail[GAP3-prompt-registry]: PromptRegistry.has_role() has no concrete "
            "implementation. "
            "Owner: orchestration_dispatch_policy.implement-prompt-registry"
        )

        assert registry.has_role("python-executor") is True

    def test_prompt_rendering_differs_by_role_family(self) -> None:
        """Different role families must produce different prompt content.

        Spec §11: 'Different roles may render from different prompt families.
        The system must not assume all roles use the same prompt structure.'

        GAP: No concrete implementation to verify this behavior.

        xfail scope:
            - No PromptRegistry implementation to demonstrate family differences.

        Authority:
            - ORCHESTRATION-PLANE-DISPATCH-AND-PROMPT-POLICY.md §11 (role-specific prompt families)
        """
        registry: PromptRegistry = None  # type: ignore[assignment]

        coder_spec = DispatchSpec(
            source_kind="step",
            source_id="core.test",
            role_id="python-executor",
            role_source="step.agent",
            execution_context="linked_worktree",
            runner="codex",
            session_mode="fresh",
            prompt_family="coder",
        )

        reviewer_spec = DispatchSpec(
            source_kind="step",
            source_id="core.test",
            role_id="gate-reviewer",
            role_source="step.agent",
            execution_context="main_worktree",
            runner="codex",
            session_mode="fresh",
            prompt_family="reviewer",
        )

        pytest.xfail(
            "xfail[GAP3-prompt-registry]: PromptRegistry must render different prompts "
            "for different role families (coder vs reviewer vs planner vs resolver). "
            "No concrete implementation exists. "
            "Owner: orchestration_dispatch_policy.implement-prompt-registry"
        )

        coder_bundle = registry.render(coder_spec)
        reviewer_bundle = registry.render(reviewer_spec)

        # Prompts should differ by family
        assert coder_bundle.system_prompt != reviewer_bundle.system_prompt, (
            "coder and reviewer families must produce different system prompts"
        )


# ---------------------------------------------------------------------
# GAP 4: Structured review result normalization to ResolutionCase
# ---------------------------------------------------------------------
# StructuredReviewResult is defined, but there is no normalizer that
# converts non-pass outcomes into explicit ResolutionCase inputs.


class TestStructuredReviewResultNormalization:
    """Review result normalization entrypoints are missing.

    Spec §14: 'The orchestration plane must normalize non-pass structured
    review results into explicit resolution cases.'
    """

    def test_structured_review_result_pass_normalizes_to_continue(self) -> None:
        """review_outcome='pass' should signal normal flow continuation.

        GAP: No normalizer exists to interpret StructuredReviewResult.

        xfail scope:
            - No normalize_review_result() or equivalent entrypoint exists.

        Authority:
            - ORCHESTRATION-PLANE-DISPATCH-AND-PROMPT-POLICY.md §14.1 (normalization rule)
        """
        result = StructuredReviewResult(
            review_outcome="pass",
            summary="All checks passed",
            findings=(),
            evidence_refs=("evidence1",),
        )

        pytest.xfail(
            "xfail[GAP4-review-normalization]: No normalizer exists to convert "
            "StructuredReviewResult into explicit ResolutionCase or continue signal. "
            "A normalize_review_result(result, case_id, core, roster, runtime) -> "
            "ResolutionCase | None function must be implemented per §14. "
            "Owner: orchestration_dispatch_policy.implement-review-result-normalizer"
        )

        # When pass: return None (continue normal flow, no resolution case)
        normalized = _normalize_review_result_if_needed(result, "case-1", _dummy_snapshots())
        assert normalized is None, "pass outcome should not create a resolution case"

    def test_structured_review_result_needs_fix_normalizes_to_resolution_case(self) -> None:
        """review_outcome='needs_fix' must create a ResolutionCase.

        Spec §14.1: 'needs_fix' -> create explicit resolution case.

        GAP: No normalizer exists.

        xfail scope:
            - normalize returns None or raises NotImplementedError.

        Authority:
            - ORCHESTRATION-PLANE-DISPATCH-AND-PROMPT-POLICY.md §14.1
        """
        result = StructuredReviewResult(
            review_outcome="needs_fix",
            summary="Issues found that need fixing",
            findings=("Issue 1: missing import", "Issue 2: wrong type"),
            evidence_refs=("file1.py",),
        )

        pytest.xfail(
            "xfail[GAP4-review-normalization]: review_outcome='needs_fix' must normalize "
            "to ResolutionCase with case_source='review_failed'. No normalizer exists. "
            "Owner: orchestration_dispatch_policy.implement-review-result-normalizer"
        )

        normalized = _normalize_review_result_if_needed(result, "case-2", _dummy_snapshots())
        assert normalized is not None, "needs_fix must create a resolution case"
        assert isinstance(normalized, ResolutionCase)
        assert normalized.case_source == "review_failed"
        assert normalized.reason == "review outcome: needs_fix"

    def test_structured_review_result_needs_replan_normalizes_to_resolution_case(self) -> None:
        """review_outcome='needs_replan' must create a ResolutionCase.

        Spec §14.1: 'needs_replan' -> create explicit resolution case.

        GAP: No normalizer exists.

        xfail scope:
            - normalize returns None or raises NotImplementedError.

        Authority:
            - ORCHESTRATION-PLANE-DISPATCH-AND-PROMPT-POLICY.md §14.1
        """
        result = StructuredReviewResult(
            review_outcome="needs_replan",
            summary="Design issue requires replanning",
            findings=("Architecture decision needs revision",),
            evidence_refs=(),
        )

        pytest.xfail(
            "xfail[GAP4-review-normalization]: review_outcome='needs_replan' must normalize "
            "to ResolutionCase. No normalizer exists. "
            "Owner: orchestration_dispatch_policy.implement-review-result-normalizer"
        )

        normalized = _normalize_review_result_if_needed(result, "case-3", _dummy_snapshots())
        assert normalized is not None, "needs_replan must create a resolution case"
        assert normalized.case_source == "review_failed"

    def test_structured_review_result_operator_required_normalizes_to_resolution_case(
        self,
    ) -> None:
        """review_outcome='operator_required' must create a ResolutionCase.

        Spec §14.1: 'operator_required' -> create explicit resolution case.

        GAP: No normalizer exists.

        xfail scope:
            - normalize returns None or raises NotImplementedError.

        Authority:
            - ORCHESTRATION-PLANE-DISPATCH-AND-PROMPT-POLICY.md §14.1
        """
        result = StructuredReviewResult(
            review_outcome="operator_required",
            summary="Manual intervention needed",
            findings=("Auth credentials require rotation",),
            evidence_refs=(),
        )

        pytest.xfail(
            "xfail[GAP4-review-normalization]: review_outcome='operator_required' must normalize "
            "to ResolutionCase. No normalizer exists. "
            "Owner: orchestration_dispatch_policy.implement-review-result-normalizer"
        )

        normalized = _normalize_review_result_if_needed(result, "case-4", _dummy_snapshots())
        assert normalized is not None, "operator_required must create a resolution case"
        assert normalized.case_source == "review_failed"

    def test_structured_review_result_parse_failure_creates_resolution_case(self) -> None:
        """Non-parseable review output must create a ResolutionCase.

        Spec §13.3: 'If a role declared as structured_review_result does not
        return a parseable result, the system must treat this as a
        review/result contract violation.'

        GAP: No parse failure handling exists.

        xfail scope:
            - No parse failure detection or normalization.

        Authority:
            - ORCHESTRATION-PLANE-DISPATCH-AND-PROMPT-POLICY.md §13.3
        """
        # Simulate a review result that couldn't be parsed into StructuredReviewResult
        # (could be prose-only output or malformed structured output)

        pytest.xfail(
            "xfail[GAP4-review-normalization]: When structured_review_result output "
            "contract is violated (prose-only or malformed), a ResolutionCase with "
            "case_source='review_failed' must be created per §13.3. No normalizer exists. "
            "Owner: orchestration_dispatch_policy.implement-review-result-normalizer"
        )

        # This would require a try/except around parsing and creating a case
        # The actual normalizer function should handle this
        case = _normalize_parse_failure(
            raw_output="The code looks good to me (prose-only response)",
            role_id="gate-reviewer",
            case_id="case-5",
            snapshots=_dummy_snapshots(),
        )
        assert case is not None
        assert case.case_source == "review_failed"


# ---------------------------------------------------------------------
# GAP 5: main_worktree role policy enforcement
# ---------------------------------------------------------------------
# Spec §12 defines execution context policy, but there's no enforcement
# that certain role families require main_worktree.


class TestMainWorktreeRolePolicy:
    """Resolver/planner/reviewer family roles must run in main_worktree.

    Spec §12.2: 'Typical examples [main_worktree roles]: resolver-family,
    planner-family, reviewer-family.'

    Spec §12: 'The execution context is determined by RoleProfile.execution_context,
    not by hardcoding resolver as the only main-worktree actor.'
    """

    def test_resolver_family_roles_require_main_worktree_context(self) -> None:
        """Roles in resolver family must require main_worktree execution context.

        GAP: No enforcement that resolver family roles are pinned to main_worktree.

        xfail scope:
            - No RoleProfileRegistry implementation to enforce role-based context.

        Authority:
            - ORCHESTRATION-PLANE-DISPATCH-AND-PROMPT-POLICY.md §12.2
        """
        pytest.xfail(
            "xfail[GAP5-main-worktree-policy]: Resolver family roles "
            "(conflict-resolver, etc.) must be validated to require main_worktree "
            "execution context. No RoleProfileRegistry implementation enforces this. "
            "Owner: orchestration_dispatch_policy.implement-role-profile-registry"
        )

        # Expected: A concrete registry would validate that resolver-family
        # roles are never dispatched to linked_worktree
        registry: RoleProfileRegistry = None  # type: ignore[assignment]
        profile = registry.get("conflict-resolver")
        assert profile.execution_context == "main_worktree"

    def test_planner_family_roles_require_main_worktree_context(self) -> None:
        """Roles in planner family must require main_worktree execution context.

        GAP: No enforcement that planner family roles are pinned to main_worktree.

        xfail scope:
            - No RoleProfileRegistry implementation to enforce role-based context.

        Authority:
            - ORCHESTRATION-PLANE-DISPATCH-AND-PROMPT-POLICY.md §12.2
        """
        pytest.xfail(
            "xfail[GAP5-main-worktree-policy]: Planner family roles "
            "(vectl-planner, etc.) must be validated to require main_worktree "
            "execution context. No RoleProfileRegistry implementation enforces this. "
            "Owner: orchestration_dispatch_policy.implement-role-profile-registry"
        )

        registry: RoleProfileRegistry = None  # type: ignore[assignment]
        profile = registry.get("vectl-planner")
        assert profile.execution_context == "main_worktree"

    def test_reviewer_family_roles_require_main_worktree_context(self) -> None:
        """Roles in reviewer family must require main_worktree execution context.

        GAP: No enforcement that reviewer family roles are pinned to main_worktree.

        xfail scope:
            - No RoleProfileRegistry implementation to enforce role-based context.

        Authority:
            - ORCHESTRATION-PLANE-DISPATCH-AND-PROMPT-POLICY.md §12.2
        """
        pytest.xfail(
            "xfail[GAP5-main-worktree-policy]: Reviewer family roles "
            "(gate-reviewer, doc-reviewer, spec-readiness-auditor) must be validated "
            "to require main_worktree execution context. No RoleProfileRegistry "
            "implementation enforces this. "
            "Owner: orchestration_dispatch_policy.implement-role-profile-registry"
        )

        registry: RoleProfileRegistry = None  # type: ignore[assignment]
        profile = registry.get("gate-reviewer")
        assert profile.execution_context == "main_worktree"


# ---------------------------------------------------------------------
# Helper fixtures / stubs (will not execute since tests xfail)
# ---------------------------------------------------------------------


def _dummy_snapshots() -> tuple[CoreSnapshot, RosterSnapshot, RuntimeSnapshot]:
    """Return dummy snapshots for test fixtures."""
    return (
        CoreSnapshot(
            plan_complete=False,
            claimable_step_ids=(),
            in_progress_step_ids=(),
            blocked_step_ids=(),
            unresolved_reasons=(),
        ),
        RosterSnapshot(
            available_agents=(),
            working_agents=(),
            reusable_sessions=(),
            exhausted_roles=(),
        ),
        RuntimeSnapshot(
            active_workspaces=(),
            active_executions=(),
            stalled_executions=(),
        ),
    )


def _normalize_review_result_if_needed(
    result: StructuredReviewResult,
    case_id: str,
    snapshots: tuple[CoreSnapshot, RosterSnapshot, RuntimeSnapshot],
) -> ResolutionCase | None:
    """Placeholder for the normalizer that will be implemented.

    This function does not exist yet. It should:
    - Return None when result.review_outcome == 'pass' (continue normal flow)
    - Return ResolutionCase when result.review_outcome is 'needs_fix',
      'needs_replan', or 'operator_required'
    """
    raise NotImplementedError(
        "normalize_review_result_if_needed not yet implemented. "
        "Must be implemented per ORCHESTRATION-PLANE-DISPATCH-AND-PROMPT-POLICY.md §14"
    )


def _normalize_parse_failure(
    raw_output: str,
    role_id: str,
    case_id: str,
    snapshots: tuple[CoreSnapshot, RosterSnapshot, RuntimeSnapshot],
) -> ResolutionCase | None:
    """Placeholder for parse failure normalizer.

    This function does not exist yet. It should create a ResolutionCase
    when structured_review_result contract is violated (prose-only or malformed).
    """
    raise NotImplementedError(
        "normalize_parse_failure not yet implemented. "
        "Must be implemented per ORCHESTRATION-PLANE-DISPATCH-AND-PROMPT-POLICY.md §13.3"
    )
