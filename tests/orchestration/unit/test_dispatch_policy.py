"""Tests for dispatch policy: coordinator, role registry, prompt registry, review normalization.

Authority: docs/ORCHESTRATION-PLANE-DISPATCH-AND-PROMPT-POLICY.md
Authority: docs/ORCHESTRATION-PLANE-INTERFACES.md sections 3, 4
Authority: docs/ORCHESTRATION-PLANE-RESOLUTION-CONTRACT.md

Covers:
    - DispatchSpec construction from ControlDecision + step data
    - ConfigRoleProfileRegistry configuration-backed lookup
    - ConfigRoleProfileRegistry unknown-role explicit failure
    - ConfigPromptRegistry role-family-specific rendering
    - Review result normalization (pass -> None, non-pass -> ResolutionCase)
    - Parse failure normalization
    - Execution context routing by RoleProfile (main_worktree for resolver/planner/reviewer)
    - step_verify_to_verify_mode mapping
"""

from __future__ import annotations

from pathlib import Path

import pytest

from vectl.orchestration.config import OrchestrationConfig
from vectl.orchestration.contracts import (
    ControlDecision,
    CoreSnapshot,
    DispatchSpec,
    PromptBundle,
    ResolutionCase,
    RoleProfile,
    RosterSnapshot,
    RuntimeSnapshot,
    StructuredReviewResult,
)
from vectl.orchestration.dispatch_policy import (
    ConfigPromptRegistry,
    ConfigRoleProfileRegistry,
    DispatchCoordinator,
    StepData,
    UnknownRoleError,
    normalize_parse_failure,
    normalize_review_result,
    step_verify_to_verify_mode,
)

# ---------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------


def _dummy_snapshots() -> tuple[CoreSnapshot, RosterSnapshot, RuntimeSnapshot]:
    """Return minimal snapshots for test fixtures."""
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


def _make_step_data(
    step_id: str = "core.test-step",
    description: str = "Test step",
    verification: str = "Test verification",
    refs: tuple[str, ...] = ("ref1",),
    evidence_template: str = "Test evidence template",
    verify: str | None = None,
    agent: str | None = None,
) -> StepData:
    """Create a StepData for testing."""
    return StepData(
        step_id=step_id,
        description=description,
        verification=verification,
        refs=refs,
        evidence_template=evidence_template,
        verify=verify,
        agent=agent,
    )


class StubCoreAdapter:
    """Test stub that provides known step data through the core seam."""

    def __init__(self, steps: dict[str, StepData] | None = None) -> None:
        self._steps = steps or {}

    def load_step_data_for_dispatch(self, step_id: str) -> StepData | None:
        return self._steps.get(step_id)

    def add_step(self, step: StepData) -> None:
        self._steps[step.step_id] = step


# ---------------------------------------------------------------------
# Tests: step_verify_to_verify_mode
# ---------------------------------------------------------------------


class TestStepVerifyToVerifyMode:
    """Verify step.verify -> DispatchSpec.verify_mode mapping."""

    def test_none_maps_to_none(self) -> None:
        """Step.verify=None should map to verify_mode='none'."""
        assert step_verify_to_verify_mode(None) == "none"

    def test_expected_red_maps_correctly(self) -> None:
        """Step.verify='expected_red' should map to verify_mode='expected_red'."""
        assert step_verify_to_verify_mode("expected_red") == "expected_red"

    def test_must_green_maps_correctly(self) -> None:
        """Step.verify='must_green' should map to verify_mode='must_green'."""
        assert step_verify_to_verify_mode("must_green") == "must_green"


# ---------------------------------------------------------------------
# Tests: ConfigRoleProfileRegistry
# ---------------------------------------------------------------------


class TestConfigRoleProfileRegistry:
    """Configuration-backed ConfigRoleProfileRegistry tests.

    Authority: ORCHESTRATION-PLANE-DISPATCH-AND-PROMPT-POLICY.md §8.3, §8.4
    """

    def test_get_returns_concrete_profile(self) -> None:
        """ConfigRoleProfileRegistry.get() must return a concrete RoleProfile."""
        registry = ConfigRoleProfileRegistry()
        profile = registry.get("python-executor")
        assert isinstance(profile, RoleProfile)
        assert profile.role_id == "python-executor"
        assert profile.agent_id == "python-executor"
        assert profile.prompt_family == "coder"
        assert profile.execution_context == "linked_worktree"
        assert profile.mutation_policy == "worktree_changes"
        assert profile.default_runner == "codex"

    def test_get_returns_different_profiles_for_different_roles(self) -> None:
        """Different roles must yield different profiles."""
        registry = ConfigRoleProfileRegistry()

        coder = registry.get("python-executor")
        planner = registry.get("vectl-planner")
        reviewer = registry.get("gate-reviewer")
        resolver = registry.get("blocked-case-coordinator")
        tacit_resolver = registry.get("blocked-case-coordinator-tacit")

        assert coder.execution_context == "linked_worktree"
        assert planner.execution_context == "main_worktree"
        assert reviewer.execution_context == "main_worktree"
        assert resolver.execution_context == "main_worktree"
        assert tacit_resolver.execution_context == "main_worktree"
        assert resolver.agent_id == "blocked-case-coordinator"
        assert tacit_resolver.agent_id == "blocked-case-coordinator-tacit"
        assert tacit_resolver.prompt_family == "resolver"

    def test_has_role_returns_true_for_known_role(self) -> None:
        """has_role() must return True for known roles."""
        registry = ConfigRoleProfileRegistry()
        assert registry.has_role("python-executor") is True
        assert registry.has_role("vectl-planner") is True
        assert registry.has_role("gate-reviewer") is True
        assert registry.has_role("blocked-case-coordinator") is True
        assert registry.has_role("blocked-case-coordinator-tacit") is True

    def test_has_role_returns_false_for_unknown_role(self) -> None:
        """has_role() must return False for unknown roles.

        Authority: §8.3 'unknown roles must fail explicitly'
        """
        registry = ConfigRoleProfileRegistry()
        assert registry.has_role("fantasy-unknown-role") is False
        assert registry.has_role("") is False

    def test_get_fails_explicitly_on_unknown_role(self) -> None:
        """get() must raise UnknownRoleError for unknown roles.

        Authority: §8.3 'unknown roles must fail explicitly - the system
        must not guess role meaning from string shape alone.'
        """
        registry = ConfigRoleProfileRegistry()
        with pytest.raises(UnknownRoleError, match="Unknown role ID"):
            registry.get("fantasy-unknown-role")

    def test_resolve_role_with_step_agent(self) -> None:
        """resolve_role prefers step.agent when present (§9.1 precedence 1)."""
        registry = ConfigRoleProfileRegistry()
        role, source = registry.resolve_role(step_agent="vectl-planner")
        assert role == "vectl-planner"
        assert source == "step.agent"

    def test_resolve_role_without_step_agent(self) -> None:
        """resolve_role falls back to default role when step.agent is absent (§9.1 precedence 2)."""
        registry = ConfigRoleProfileRegistry()
        role, source = registry.resolve_role(step_agent=None)
        assert role == "python-executor"
        assert source == "default"

    def test_resolve_role_with_empty_step_agent(self) -> None:
        """resolve_role treats empty string step.agent as absent."""
        registry = ConfigRoleProfileRegistry()
        role, source = registry.resolve_role(step_agent="")
        assert role == "python-executor"
        assert source == "default"

    def test_resolver_family_roles_require_main_worktree(self) -> None:
        """Resolver-family roles must require main_worktree execution context.

        Authority: §12.2 'Typical examples [main_worktree roles]: resolver-family'
        """
        registry = ConfigRoleProfileRegistry()
        for role_id in ("blocked-case-coordinator", "blocked-case-coordinator-tacit"):
            profile = registry.get(role_id)
            assert profile.execution_context == "main_worktree"
            assert profile.mutation_policy == "vectl_facade_only"

    def test_default_resolver_profiles_preserve_cleaned_taxonomy(self) -> None:
        """Default resolver profiles stay on blocked-case coordinator taxonomy only.

        Authority: ADR-orchestration-role-profile-config-and-resolver-cleanup.md §Decision
        """
        registry = ConfigRoleProfileRegistry()
        resolver_role_ids = {
            role_id
            for role_id in (
                "blocked-case-coordinator",
                "blocked-case-coordinator-tacit",
            )
            if registry.has_role(role_id)
        }

        assert resolver_role_ids == {
            "blocked-case-coordinator",
            "blocked-case-coordinator-tacit",
        }
        combined_surface = " ".join(sorted(resolver_role_ids)).lower()
        assert "conflict" not in combined_surface

    def test_planner_family_roles_require_main_worktree(self) -> None:
        """Planner-family roles must require main_worktree execution context.

        Authority: §12.2 'Typical examples [main_worktree roles]: planner-family'
        """
        registry = ConfigRoleProfileRegistry()
        profile = registry.get("vectl-planner")
        assert profile.execution_context == "main_worktree"
        assert profile.mutation_policy == "vectl_facade_only"

    def test_reviewer_family_roles_require_main_worktree(self) -> None:
        """Reviewer-family roles must require main_worktree execution context.

        Authority: §12.2 'Typical examples [main_worktree roles]: reviewer-family'
        """
        registry = ConfigRoleProfileRegistry()
        profile = registry.get("gate-reviewer")
        assert profile.execution_context == "main_worktree"
        assert profile.mutation_policy == "read_only"

    def test_coder_family_roles_use_linked_worktree(self) -> None:
        """Coder-family roles must use linked_worktree execution context.

        Authority: §12.1 'Typical examples [linked_worktree roles]: ordinary coder roles'
        """
        registry = ConfigRoleProfileRegistry()
        profile = registry.get("python-executor")
        assert profile.execution_context == "linked_worktree"
        assert profile.mutation_policy == "worktree_changes"

    def test_custom_profiles_extend_registry(self) -> None:
        """Custom profiles can be added without changing core contracts.

        Authority: §8.1 'role IDs must remain open strings' and §8.4
        'adding a new role must be possible by adding a new role profile entry'
        """
        custom_role = RoleProfile(
            role_id="custom-agent",
            agent_id="custom-agent",
            prompt_family="coder",
            execution_context="linked_worktree",
            mutation_policy="worktree_changes",
            session_policy="reuse_allowed",
            output_contract="freeform_evidence",
            default_runner="custom-runner",
        )
        registry = ConfigRoleProfileRegistry(profiles=(custom_role,))
        assert registry.has_role("custom-agent") is True
        profile = registry.get("custom-agent")
        assert profile.role_id == "custom-agent"
        assert profile.default_runner == "custom-runner"

    def test_main_worktree_family_policy_is_validated_for_custom_profiles(self) -> None:
        """Canonical main-worktree family policy is enforced centrally."""
        invalid_reviewer = RoleProfile(
            role_id="custom-reviewer",
            agent_id="custom-reviewer",
            prompt_family="reviewer",
            execution_context="linked_worktree",
            mutation_policy="read_only",
            session_policy="reuse_allowed",
            output_contract="structured_review_result",
            default_runner="codex",
        )

        with pytest.raises(ValueError, match="violates 'reviewer' family policy"):
            ConfigRoleProfileRegistry(profiles=(invalid_reviewer,))

    def test_default_role_is_configurable(self) -> None:
        """Default role can be overridden via configuration."""
        registry = ConfigRoleProfileRegistry(default_role="python-senior")
        role, source = registry.resolve_role(step_agent=None)
        assert role == "python-senior"
        assert source == "default"

    def test_registry_defaults_are_loaded_from_orchestration_config(self) -> None:
        """Default registry authority comes from orchestration config, not dispatch code."""
        config = OrchestrationConfig()
        registry = ConfigRoleProfileRegistry(
            profiles=config.role_profiles,
            default_role=config.dispatch.default_role_id,
        )

        assert registry.default_role == config.dispatch.default_role_id
        assert registry.has_role("blocked-case-coordinator") is True
        assert registry.has_role("blocked-case-coordinator-tacit") is True


# ---------------------------------------------------------------------
# Tests: ConfigPromptRegistry
# ---------------------------------------------------------------------


class TestConfigPromptRegistry:
    """Configuration-backed ConfigPromptRegistry tests.

    Authority: ORCHESTRATION-PLANE-DISPATCH-AND-PROMPT-POLICY.md §10, §11
    """

    def test_render_returns_prompt_bundle(self) -> None:
        """ConfigPromptRegistry.render() must return a PromptBundle."""
        registry = ConfigPromptRegistry()
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
        bundle = registry.render(spec)
        assert isinstance(bundle, PromptBundle)
        assert bundle.system_prompt
        assert bundle.task_prompt

    def test_has_role_for_known_role(self) -> None:
        """has_role() must return True for known roles in the family map."""
        registry = ConfigPromptRegistry()
        assert registry.has_role("python-executor") is True
        assert registry.has_role("vectl-planner") is True
        assert registry.has_role("gate-reviewer") is True
        assert registry.has_role("blocked-case-coordinator") is True

    def test_has_role_delegates_to_role_registry_when_provided(self) -> None:
        """When a role_registry is provided, has_role delegates to it."""
        role_registry = ConfigRoleProfileRegistry()
        prompt_registry = ConfigPromptRegistry(role_registry=role_registry)
        assert prompt_registry.has_role("python-executor") is True
        assert prompt_registry.has_role("unknown-role") is False

    def test_rendering_differs_by_role_family(self) -> None:
        """Different role families must produce different prompt content.

        Authority: §11.1 'Different roles may render from different prompt families.
        The system must not assume all roles use the same prompt structure.'
        """
        registry = ConfigPromptRegistry()

        coder_spec = DispatchSpec(
            source_kind="step",
            source_id="core.test",
            role_id="python-executor",
            role_source="step.agent",
            execution_context="linked_worktree",
            runner="codex",
            session_mode="fresh",
            prompt_family="coder",
            description="Implement feature X",
            verification="Tests pass",
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
            description="Review feature X",
            verification="Code review passed",
        )

        coder_bundle = registry.render(coder_spec)
        reviewer_bundle = registry.render(reviewer_spec)

        # System prompts must differ between families
        assert coder_bundle.system_prompt != reviewer_bundle.system_prompt, (
            "coder and reviewer families must produce different system prompts"
        )

    def test_coder_family_prompt_contains_step_data(self) -> None:
        """Coder family prompts must include step description and verification."""
        registry = ConfigPromptRegistry()
        spec = DispatchSpec(
            source_kind="step",
            source_id="core.test-step",
            role_id="python-executor",
            role_source="step.agent",
            execution_context="linked_worktree",
            runner="codex",
            session_mode="fresh",
            prompt_family="coder",
            description="Implement the feature",
            verification="All tests pass",
        )
        bundle = registry.render(spec)
        assert "Implement the feature" in bundle.task_prompt
        assert "All tests pass" in bundle.task_prompt

    def test_planner_family_prompt_includes_remediation_context(self) -> None:
        """Planner family prompts must emphasize remediation and no claim semantics.

        Authority: §11.3 'Planner-family templates should emphasize: structured
        plan output, remediation/retest planning, no ordinary step claim semantics'
        """
        registry = ConfigPromptRegistry()
        spec = DispatchSpec(
            source_kind="resolution_subtask",
            source_id="case-123",
            role_id="vectl-planner",
            role_source="resolver",
            execution_context="main_worktree",
            runner="codex",
            session_mode="fresh",
            prompt_family="planner",
            description="Plan remediation for failed step",
        )
        bundle = registry.render(spec)
        assert (
            "remediation" in bundle.system_prompt.lower()
            or "planning" in bundle.system_prompt.lower()
        )
        assert (
            "vectl_facade_mutation" in bundle.task_prompt
            or "vectl facade" in bundle.task_prompt.lower()
        )

    def test_resolver_family_prompt_includes_facade_constraint(self) -> None:
        """Resolver family prompts must reflect vectl facade only mutation policy.

        Authority: §11.5 'Resolver-family templates must reflect:
        main worktree execution context, no claim of new work,
        approved vectl tool facade mutation policy'
        """
        registry = ConfigPromptRegistry()
        spec = DispatchSpec(
            source_kind="resolution_subtask",
            source_id="case-456",
            role_id="blocked-case-coordinator",
            role_source="resolver",
            execution_context="main_worktree",
            runner="codex",
            session_mode="fresh",
            prompt_family="resolver",
            mutation_policy="vectl_facade_only",
        )
        bundle = registry.render(spec)
        # System prompt should reference main worktree / vectl facade
        system_lower = bundle.system_prompt.lower()
        assert "vectl" in system_lower or "approved" in system_lower or "facade" in system_lower

    def test_tacit_resolver_role_renders_distinct_prompt_content(self) -> None:
        """Tacit resolver must not collapse to the standard resolver prompt."""
        role_registry = ConfigRoleProfileRegistry()
        registry = ConfigPromptRegistry(role_registry=role_registry)

        standard = registry.render(
            DispatchSpec(
                source_kind="resolution_subtask",
                source_id="case-standard",
                role_id="blocked-case-coordinator",
                role_source="resolver",
                execution_context="main_worktree",
                runner="codex",
                session_mode="fresh",
                prompt_family="resolver",
                mutation_policy="vectl_facade_only",
            )
        )
        tacit = registry.render(
            DispatchSpec(
                source_kind="resolution_subtask",
                source_id="case-tacit",
                role_id="blocked-case-coordinator-tacit",
                role_source="resolver",
                execution_context="main_worktree",
                runner="codex",
                session_mode="fresh",
                prompt_family="resolver",
                mutation_policy="vectl_facade_only",
            )
        )

        assert standard.system_prompt != tacit.system_prompt
        assert standard.task_prompt != tacit.task_prompt
        assert "tacit edition" in tacit.system_prompt.lower()
        assert "repair or delegation paths" in tacit.system_prompt.lower()

    def test_agent_id_precedence_preserves_tacit_prompt_for_future_role_id(self) -> None:
        """Prompt selection must use agent_id before prompt_family fallback."""
        future_role = RoleProfile(
            role_id="future-resolver-role",
            agent_id="blocked-case-coordinator-tacit",
            prompt_family="resolver",
            execution_context="main_worktree",
            mutation_policy="vectl_facade_only",
            session_policy="reuse_forbidden",
            output_contract="resolution_report",
            default_runner="codex",
        )
        role_registry = ConfigRoleProfileRegistry(profiles=(future_role,))
        registry = ConfigPromptRegistry(role_registry=role_registry)

        bundle = registry.render(
            DispatchSpec(
                source_kind="resolution_subtask",
                source_id="case-future-role",
                role_id="future-resolver-role",
                role_source="resolver",
                execution_context="main_worktree",
                runner="codex",
                session_mode="fresh",
                prompt_family="resolver",
                mutation_policy="vectl_facade_only",
            )
        )

        assert "tacit edition" in bundle.system_prompt.lower()
        assert "repair or delegation paths" in bundle.system_prompt.lower()

    def test_main_worktree_context_in_messages(self) -> None:
        """main_worktree execution context must appear in context messages."""
        registry = ConfigPromptRegistry()
        spec = DispatchSpec(
            source_kind="step",
            source_id="core.test",
            role_id="gate-reviewer",
            role_source="step.agent",
            execution_context="main_worktree",
            runner="codex",
            session_mode="fresh",
            prompt_family="reviewer",
        )
        bundle = registry.render(spec)
        context_msgs = [m for m in bundle.messages if m["role"] == "system"]
        assert any("main_worktree" in m["content"] for m in context_msgs)

    def test_vectl_facade_only_in_messages(self) -> None:
        """vectl_facade_only mutation policy must appear in context messages."""
        registry = ConfigPromptRegistry()
        spec = DispatchSpec(
            source_kind="step",
            source_id="core.test",
            role_id="blocked-case-coordinator",
            role_source="resolver",
            execution_context="main_worktree",
            runner="codex",
            session_mode="fresh",
            prompt_family="resolver",
            mutation_policy="vectl_facade_only",
        )
        bundle = registry.render(spec)
        context_msgs = [m for m in bundle.messages if m["role"] == "system"]
        assert any(
            "vectl_facade_only" in m["content"] or "vectl facade" in m["content"].lower()
            for m in context_msgs
        )


# ---------------------------------------------------------------------
# Tests: DispatchCoordinator
# ---------------------------------------------------------------------


class TestDispatchCoordinator:
    """DispatchSpec construction from ControlDecision + step data + role registry.

    Authority: ORCHESTRATION-PLANE-DISPATCH-AND-PROMPT-POLICY.md §6.1
    """

    def test_build_dispatch_spec_from_control_decision(self) -> None:
        """DispatchCoordinator must build DispatchSpec from ControlDecision + step data.

        Authority: §6.1 'Normal flow: 1. control emits ControlDecision, 2. dispatch
        coordinator loads authoritative step data, 3. role profile is resolved,
        4. a DispatchSpec is built'
        """
        step_data = _make_step_data(
            step_id="core.test-step",
            description="Implement the feature",
            verification="All tests pass",
            refs=("docs/spec.md",),
            evidence_template="Paste test output here",
            verify="must_green",
            agent="python-executor",
        )
        core_adapter = StubCoreAdapter({"core.test-step": step_data})
        role_registry = ConfigRoleProfileRegistry()
        prompt_registry = ConfigPromptRegistry()

        coordinator = DispatchCoordinator(
            role_registry=role_registry,
            prompt_registry=prompt_registry,
            core_adapter=core_adapter,
        )

        decision = ControlDecision(
            kind="dispatch",
            reason="Claimable work available",
            step_ids=("core.test-step",),
            role_bindings={"core.test-step": "python-executor"},
        )

        spec = coordinator.build_dispatch_spec(decision)

        assert spec.source_kind == "step"
        assert spec.source_id == "core.test-step"
        assert spec.role_id == "python-executor"
        assert spec.role_source == "step.agent"
        assert spec.step_id == "core.test-step"
        assert spec.description == "Implement the feature"
        assert spec.verification == "All tests pass"
        assert spec.refs == ("docs/spec.md",)
        assert spec.evidence_template == "Paste test output here"
        assert spec.verify_mode == "must_green"
        assert spec.execution_context == "linked_worktree"
        assert spec.prompt_family == "coder"
        assert spec.output_contract == "freeform_evidence"
        assert spec.mutation_policy == "worktree_changes"
        assert spec.runner == "codex"

    def test_build_dispatch_spec_default_role_when_no_agent(self) -> None:
        """When step.agent is None, dispatch uses the default role (§9.1 precedence 2)."""
        step_data = _make_step_data(step_id="core.no-agent", agent=None)
        core_adapter = StubCoreAdapter({"core.no-agent": step_data})
        role_registry = ConfigRoleProfileRegistry()
        prompt_registry = ConfigPromptRegistry()

        coordinator = DispatchCoordinator(
            role_registry=role_registry,
            prompt_registry=prompt_registry,
            core_adapter=core_adapter,
        )

        decision = ControlDecision(
            kind="dispatch",
            reason="Test default role",
            step_ids=("core.no-agent",),
            role_bindings={},
        )

        spec = coordinator.build_dispatch_spec(decision)
        assert spec.role_id == "python-executor"
        assert spec.role_source == "default"

    def test_build_dispatch_spec_role_from_step_agent(self) -> None:
        """When step.agent is present, dispatch uses that role (§9.1 precedence 1)."""
        step_data = _make_step_data(step_id="core.planner-step", agent="vectl-planner")
        core_adapter = StubCoreAdapter({"core.planner-step": step_data})
        role_registry = ConfigRoleProfileRegistry()
        prompt_registry = ConfigPromptRegistry()

        coordinator = DispatchCoordinator(
            role_registry=role_registry,
            prompt_registry=prompt_registry,
            core_adapter=core_adapter,
        )

        # Note: decision.role is separate from step.agent. The coordinator
        # uses step.agent per §9.1 precedence. If step.agent is present, that wins.
        decision = ControlDecision(
            kind="dispatch",
            reason="Test step.agent role",
            step_ids=("core.planner-step",),
            role_bindings={"core.planner-step": "vectl-planner"},
        )

        spec = coordinator.build_dispatch_spec(decision)
        assert spec.role_id == "vectl-planner"
        assert spec.role_source == "step.agent"
        # Planner roles require main_worktree
        assert spec.execution_context == "main_worktree"

    def test_build_dispatch_spec_rejects_non_dispatch_decision(self) -> None:
        """build_dispatch_spec must reject non-dispatch ControlDecisions."""
        role_registry = ConfigRoleProfileRegistry()
        prompt_registry = ConfigPromptRegistry()
        core_adapter = StubCoreAdapter()

        coordinator = DispatchCoordinator(
            role_registry=role_registry,
            prompt_registry=prompt_registry,
            core_adapter=core_adapter,
        )

        for kind in ("resolve", "wait", "done"):
            with pytest.raises(ValueError, match="dispatch decision"):
                coordinator.build_dispatch_spec(ControlDecision(kind=kind, reason="test"))

    def test_build_dispatch_spec_rejects_missing_step_id(self) -> None:
        """build_dispatch_spec must reject dispatch decisions without step_id."""
        role_registry = ConfigRoleProfileRegistry()
        prompt_registry = ConfigPromptRegistry()
        core_adapter = StubCoreAdapter()

        coordinator = DispatchCoordinator(
            role_registry=role_registry,
            prompt_registry=prompt_registry,
            core_adapter=core_adapter,
        )

        with pytest.raises(ValueError, match="step_id"):
            coordinator.build_dispatch_spec(
                ControlDecision(kind="dispatch", reason="test", step_ids=(), role_bindings={})
            )

    def test_build_dispatch_spec_rejects_unknown_role(self) -> None:
        """build_dispatch_spec must raise UnknownRoleError for unknown roles."""
        step_data = _make_step_data(step_id="core.bad-role", agent="unknown-agent")
        core_adapter = StubCoreAdapter({"core.bad-role": step_data})
        role_registry = ConfigRoleProfileRegistry()
        prompt_registry = ConfigPromptRegistry()

        coordinator = DispatchCoordinator(
            role_registry=role_registry,
            prompt_registry=prompt_registry,
            core_adapter=core_adapter,
        )

        decision = ControlDecision(
            kind="dispatch",
            reason="Test",
            step_ids=("core.bad-role",),
            role_bindings={"core.bad-role": "unknown-agent"},
        )

        with pytest.raises(UnknownRoleError, match="Unknown role ID"):
            coordinator.build_dispatch_spec(decision)

    def test_build_resolution_subtask_spec(self) -> None:
        """build_resolution_subtask_spec must produce a resolution subtask DispatchSpec.

        Authority: §6.2 'Resolver does not use ordinary claim-based step dispatch'
        Authority: §15 'Planner as specialized remediation subagent under resolver'
        """
        role_registry = ConfigRoleProfileRegistry()
        prompt_registry = ConfigPromptRegistry()
        core_adapter = StubCoreAdapter()

        coordinator = DispatchCoordinator(
            role_registry=role_registry,
            prompt_registry=prompt_registry,
            core_adapter=core_adapter,
        )

        spec = coordinator.build_resolution_subtask_spec(
            case_id="case-789",
            role_id="vectl-planner",
            description="Plan remediation for blocked step",
            refs=("evidence1", "evidence2"),
        )

        assert spec.source_kind == "resolution_subtask"
        assert spec.source_id == "case-789"
        assert spec.role_id == "vectl-planner"
        assert spec.role_source == "resolver"
        assert spec.execution_context == "main_worktree"
        assert spec.prompt_family == "planner"
        assert spec.output_contract == "vectl_facade_mutation"
        assert spec.mutation_policy == "vectl_facade_only"
        assert spec.description == "Plan remediation for blocked step"
        assert spec.refs == ("evidence1", "evidence2")

    def test_build_resolution_subtask_spec_rejects_unknown_role(self) -> None:
        """build_resolution_subtask_spec must raise UnknownRoleError for unknown roles."""
        role_registry = ConfigRoleProfileRegistry()
        prompt_registry = ConfigPromptRegistry()
        core_adapter = StubCoreAdapter()

        coordinator = DispatchCoordinator(
            role_registry=role_registry,
            prompt_registry=prompt_registry,
            core_adapter=core_adapter,
        )

        with pytest.raises(UnknownRoleError):
            coordinator.build_resolution_subtask_spec(
                case_id="case-000",
                role_id="unknown-phantom-role",
                description="Bad role",
            )

    def test_verify_mode_mapping_in_dispatch_spec(self) -> None:
        """DispatchSpec.verify_mode must be correctly mapped from Step.verify."""
        step_data = _make_step_data(
            step_id="core.verify-step",
            verify="expected_red",
        )
        core_adapter = StubCoreAdapter({"core.verify-step": step_data})
        role_registry = ConfigRoleProfileRegistry()
        prompt_registry = ConfigPromptRegistry()

        coordinator = DispatchCoordinator(
            role_registry=role_registry,
            prompt_registry=prompt_registry,
            core_adapter=core_adapter,
        )

        decision = ControlDecision(
            kind="dispatch",
            reason="Test verify_mode",
            step_ids=("core.verify-step",),
            role_bindings={"core.verify-step": "python-executor"},
        )

        spec = coordinator.build_dispatch_spec(decision)
        assert spec.verify_mode == "expected_red"

    def test_prompt_rendering_is_centralized(self) -> None:
        """Prompt rendering must be centralized (§10.1).

        The DispatchCoordinator doesn't render prompts itself, but the prompt
        registry is available for orchestration-app-level rendering.
        """
        role_registry = ConfigRoleProfileRegistry()
        prompt_registry = ConfigPromptRegistry()
        core_adapter = StubCoreAdapter()

        coordinator = DispatchCoordinator(
            role_registry=role_registry,
            prompt_registry=prompt_registry,
            core_adapter=core_adapter,
        )

        # Verify prompt registry is accessible
        spec = DispatchSpec(
            source_kind="step",
            source_id="core.test",
            role_id="python-executor",
            role_source="step.agent",
            execution_context="linked_worktree",
            runner="codex",
            session_mode="fresh",
            prompt_family="coder",
            description="Test step",
        )

        bundle = coordinator.prompt_registry.render(spec)
        assert isinstance(bundle, PromptBundle)
        assert bundle.system_prompt


# ---------------------------------------------------------------------
# Tests: Review Result Normalization
# ---------------------------------------------------------------------


class TestReviewResultNormalization:
    """StructuredReviewResult normalization tests.

    Authority: ORCHESTRATION-PLANE-DISPATCH-AND-PROMPT-POLICY.md §14
    """

    def test_pass_outcome_returns_none(self) -> None:
        """review_outcome='pass' should return None (continue normal flow, §14.1)."""
        core, roster, runtime = _dummy_snapshots()
        result = StructuredReviewResult(
            review_outcome="pass",
            summary="All checks passed",
            findings=(),
            evidence_refs=("evidence1",),
        )
        normalized = normalize_review_result(result, "case-1", core, roster, runtime)
        assert normalized is None, "pass outcome should not create a resolution case"

    def test_needs_fix_creates_resolution_case(self) -> None:
        """review_outcome='needs_fix' must create ResolutionCase with case_source='review_failed'.

        Authority: §14.1 'needs_fix -> create explicit resolution case'
        """
        core, roster, runtime = _dummy_snapshots()
        result = StructuredReviewResult(
            review_outcome="needs_fix",
            summary="Issues found that need fixing",
            findings=("Issue 1: missing import", "Issue 2: wrong type"),
            evidence_refs=("file1.py",),
        )
        normalized = normalize_review_result(result, "case-2", core, roster, runtime)
        assert normalized is not None, "needs_fix must create a resolution case"
        assert isinstance(normalized, ResolutionCase)
        assert normalized.case_source == "review_failed"
        assert normalized.reason == "review outcome: needs_fix"
        assert normalized.case_id == "case-2"
        assert normalized.summary == "Issues found that need fixing"
        assert "file1.py" in normalized.artifact_refs

    def test_needs_replan_creates_resolution_case(self) -> None:
        """review_outcome='needs_replan' must create ResolutionCase.

        Authority: §14.1 'needs_replan -> create explicit resolution case'
        """
        core, roster, runtime = _dummy_snapshots()
        result = StructuredReviewResult(
            review_outcome="needs_replan",
            summary="Design issue requires replanning",
            findings=("Architecture decision needs revision",),
            evidence_refs=(),
        )
        normalized = normalize_review_result(result, "case-3", core, roster, runtime)
        assert normalized is not None
        assert normalized.case_source == "review_failed"
        assert normalized.reason == "review outcome: needs_replan"

    def test_operator_required_creates_resolution_case(self) -> None:
        """review_outcome='operator_required' must create ResolutionCase.

        Authority: §14.1 'operator_required -> create explicit resolution case'
        """
        core, roster, runtime = _dummy_snapshots()
        result = StructuredReviewResult(
            review_outcome="operator_required",
            summary="Manual intervention needed",
            findings=("Auth credentials require rotation",),
            evidence_refs=(),
        )
        normalized = normalize_review_result(result, "case-4", core, roster, runtime)
        assert normalized is not None
        assert normalized.case_source == "review_failed"
        assert normalized.reason == "review outcome: operator_required"

    def test_normalize_preserves_evidence_refs(self) -> None:
        """normalize_review_result must preserve evidence_refs from the review result."""
        core, roster, runtime = _dummy_snapshots()
        result = StructuredReviewResult(
            review_outcome="needs_fix",
            summary="Test",
            findings=("Finding 1",),
            evidence_refs=("ev1", "ev2"),
        )
        normalized = normalize_review_result(result, "case-5", core, roster, runtime)
        assert normalized is not None
        assert normalized.artifact_refs == ("ev1", "ev2")


class TestParseFailureNormalization:
    """Parse failure normalization tests.

    Authority: ORCHESTRATION-PLANE-DISPATCH-AND-PROMPT-POLICY.md §13.3
    """

    def test_parse_failure_creates_resolution_case(self) -> None:
        """Unparseable review output must create a ResolutionCase with case_source='review_failed'.

        Authority: §13.3 'If a role declared as structured_review_result does not
        return a parseable result, the system must treat this as a review/result
        contract violation.'
        """
        core, roster, runtime = _dummy_snapshots()
        case = normalize_parse_failure(
            raw_output="The code looks good to me (prose-only response)",
            role_id="gate-reviewer",
            case_id="case-5",
            core=core,
            roster=roster,
            runtime=runtime,
        )
        assert case is not None
        assert isinstance(case, ResolutionCase)
        assert case.case_source == "review_failed"
        assert case.case_id == "case-5"
        assert "gate-reviewer" in case.reason
        assert case.reason is not None
        assert case.summary is not None
        reason_lower = case.reason.lower()
        summary_lower = case.summary.lower()
        assert "unparseable" in reason_lower or "contract violation" in reason_lower
        assert "prose-only" in summary_lower or "unparseable" in summary_lower

    def test_parse_failure_includes_raw_output_preview(self) -> None:
        """Parse failure summary must include a preview of the raw output."""
        core, roster, runtime = _dummy_snapshots()
        long_output = "A" * 500
        case = normalize_parse_failure(
            raw_output=long_output,
            role_id="doc-reviewer",
            case_id="case-6",
            core=core,
            roster=roster,
            runtime=runtime,
        )
        # Preview should be truncated
        assert "AAA" in (case.summary or "")  # First 200 chars will be in preview


# ---------------------------------------------------------------------
# Tests: Planner as Remediation Subagent
# ---------------------------------------------------------------------


class TestPlannerSubagentRouting:
    """Planner must not be routed as ordinary fallback (§15).

    Authority: ORCHESTRATION-PLANE-DISPATCH-AND-PROMPT-POLICY.md §15
    'Planner should not be selected directly by ordinary control flow just
    because a review failed. Instead, planner is typically used as a
    specialized subagent invoked by resolver.'
    """

    def test_planner_dispatch_through_resolver_not_step(self) -> None:
        """Planner subtask must have source_kind='resolution_subtask' and role_source='resolver'.

        This ensures planner is dispatched through resolver coordination,
        not as an ordinary step claim (§15).
        """
        role_registry = ConfigRoleProfileRegistry()
        prompt_registry = ConfigPromptRegistry()
        core_adapter = StubCoreAdapter()

        coordinator = DispatchCoordinator(
            role_registry=role_registry,
            prompt_registry=prompt_registry,
            core_adapter=core_adapter,
        )

        spec = coordinator.build_resolution_subtask_spec(
            case_id="case-planner-1",
            role_id="vectl-planner",
            description="Plan remediation",
        )

        # Resolution subtask dispatch: source_kind != "step"
        assert spec.source_kind == "resolution_subtask"
        # Role source is "resolver", not "step.agent" or "default"
        assert spec.role_source == "resolver"
        # Planner operates in main_worktree (§12.2)
        assert spec.execution_context == "main_worktree"
        # Planner mutation policy is vectl_facade_only (§11.5)
        assert spec.mutation_policy == "vectl_facade_only"
        # Planner output is vectl_facade_mutation (§15.3 / gate policy)
        assert spec.output_contract == "vectl_facade_mutation"

    def test_no_silent_downgrade_of_planner_to_coder(self) -> None:
        """Planner must not be silently downgraded to coder role (§9.2).

        The system must not silently downgrade specialized roles.
        """
        role_registry = ConfigRoleProfileRegistry()
        profile = role_registry.get("vectl-planner")

        # Planner must have different profile than coder
        coder_profile = role_registry.get("python-executor")
        assert profile.execution_context != coder_profile.execution_context, (
            "Planner must not be downgraded to coder execution context"
        )
        assert profile.prompt_family != coder_profile.prompt_family, (
            "Planner must not use same prompt family as coder"
        )


# ---------------------------------------------------------------------
# Tests: core adapter dispatch boundary hygiene
# ---------------------------------------------------------------------


class TestCoreAdapterDispatchBoundary:
    """Verify PlanCoreAdapter exposes the stable dispatch step-data seam.

    Authority: docs/ORCHESTRATION-PLANE-IMPLEMENTATION-DESIGN.md §3.6
    """

    def test_core_adapter_loads_step_data_for_dispatch(self, tmp_path: Path) -> None:
        """PlanCoreAdapter must provide dispatch step data through its public seam."""
        from vectl.io import save_plan
        from vectl.models import Phase, Plan, Step
        from vectl.orchestration.core_adapter import PlanCoreAdapter

        plan_path = tmp_path / "plan.yaml"
        plan = Plan(
            project="adapter-boundary-test",
            phases=[
                Phase(
                    id="core",
                    name="Core",
                    steps=[
                        Step(
                            id="core.example",
                            name="Example",
                            description="Test step",
                            verification="pass",
                            refs=("README.md",),
                            evidence_template="## Evidence",
                            verify="expected_red",
                            agent="python-executor",
                        ),
                    ],
                )
            ],
        )
        save_plan(plan, plan_path)
        core_adapter = PlanCoreAdapter(plan_path)

        result = core_adapter.load_step_data_for_dispatch("core.example")

        assert result is not None
        assert result.step_id == "core.example"
        assert result.description == "Test step"
        assert result.verify == "expected_red"

    def test_adapter_returns_none_for_missing_step(self, tmp_path: Path) -> None:
        """PlanCoreAdapter returns None for nonexistent dispatch step data."""
        from vectl.io import save_plan
        from vectl.models import Phase, Plan, Step
        from vectl.orchestration.core_adapter import PlanCoreAdapter

        plan_path = tmp_path / "plan.yaml"
        plan = Plan(
            project="adapter-boundary-test",
            phases=[Phase(id="core", name="Core", steps=[Step(id="core.exists", name="Exists")])],
        )
        save_plan(plan, plan_path)
        core_adapter = PlanCoreAdapter(plan_path)

        result = core_adapter.load_step_data_for_dispatch("core.nonexistent")

        assert result is None
