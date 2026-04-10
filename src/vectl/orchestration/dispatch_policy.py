"""
Dispatch coordinator, role-profile registry, prompt registry, and
structured review-result normalization.

Authority: docs/ORCHESTRATION-PLANE-DISPATCH-AND-PROMPT-POLICY.md
Authority: docs/ORCHESTRATION-PLANE-INTERFACES.md sections 3, 4
Authority: docs/ORCHESTRATION-PLANE-RESOLUTION-CONTRACT.md

Public surfaces:
    - DispatchCoordinator          (dispatch-spec construction from control output)
    - ConfigRoleProfileRegistry    (configuration-backed RoleProfileRegistry)
    - ConfigPromptRegistry         (configuration-backed PromptRegistry)
    - normalize_review_result      (review-outcome -> ResolutionCase normalizer)
    - normalize_parse_failure      (unparseable review output -> ResolutionCase)
    - step_verify_to_verify_mode  (step.verify -> DispatchSpec.verify_mode)
    - UnknownRoleError             (explicit failure for unknown role IDs)
    - ReviewResultParseError       (error for unparseable review results)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Protocol

from vectl.orchestration.contracts import (
    ControlDecision,
    CoreSnapshot,
    DispatchRoleSource,
    DispatchSourceKind,
    DispatchSpec,
    ExecutionContext,
    MutationPolicy,
    PromptBundle,
    PromptRegistry,
    ResolutionCase,
    ResolutionCaseSource,
    ReviewOutcome,
    RoleOutputContract,
    RoleProfile,
    RoleProfileRegistry,
    RosterSnapshot,
    RuntimeSnapshot,
    SessionMode,
    StructuredReviewResult,
)
from vectl.orchestration.core_adapter import CoreAdapter, PlanCoreAdapter


# ---------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------


class UnknownRoleError(KeyError):
    """Raised when a role ID is not found in the registry.

    Authority: docs/ORCHESTRATION-PLANE-DISPATCH-AND-PROMPT-POLICY.md §8.3
    'unknown roles must fail explicitly - the system must not guess role
    meaning from string shape alone.'
    """


class ReviewResultParseError(ValueError):
    """Raised when structured review output cannot be parsed.

    Authority: docs/ORCHESTRATION-PLANE-DISPATCH-AND-PROMPT-POLICY.md §13.3
    'If a role declared as structured_review_result does not return a
    parseable result, the system must treat this as a review/result contract
    violation rather than silently guessing from natural language.'
    """


# ---------------------------------------------------------------------
# Step verify-mode mapping
# ---------------------------------------------------------------------

# Maps vectl.models.Step.verify (Literal["expected_red", "must_green"] | None)
# to DispatchSpec.verify_mode (Literal["must_green", "expected_red", "none"]).
_VERIFY_MODE_MAP: dict[str | None, Literal["must_green", "expected_red", "none"]] = {
    None: "none",
    "expected_red": "expected_red",
    "must_green": "must_green",
}


def step_verify_to_verify_mode(
    step_verify: str | None,
) -> Literal["must_green", "expected_red", "none"]:
    """Map vectl Step.verify to DispatchSpec.verify_mode.

    Authority: docs/ORCHESTRATION-PLANE-DISPATCH-AND-PROMPT-POLICY.md §7.1

    Args:
        step_verify: Value from Step.verify field.

    Returns:
        DispatchSpec.verify_mode value.
    """
    return _VERIFY_MODE_MAP.get(step_verify, "none")


# ---------------------------------------------------------------------
# Configuration-backed RoleProfileRegistry
# ---------------------------------------------------------------------


@dataclass(frozen=True)
class _RoleFamilyPolicy:
    """Centralized invariants for known prompt/role families."""

    execution_context: ExecutionContext
    mutation_policy: MutationPolicy | None = None
    session_policy: Literal["reuse_allowed", "reuse_forbidden"] | None = None
    output_contract: RoleOutputContract | None = None


# Central policy authority for canonical role families.
#
# This keeps the main-worktree family contract encoded in one place rather than
# scattered across individual role entries.
_ROLE_FAMILY_POLICY: dict[str, _RoleFamilyPolicy] = {
    "coder": _RoleFamilyPolicy(
        execution_context="linked_worktree",
        mutation_policy="worktree_changes",
        session_policy="reuse_allowed",
        output_contract="freeform_evidence",
    ),
    "planner": _RoleFamilyPolicy(
        execution_context="main_worktree",
        mutation_policy="vectl_facade_only",
        session_policy="reuse_forbidden",
        output_contract="structured_plan_result",
    ),
    "reviewer": _RoleFamilyPolicy(
        execution_context="main_worktree",
        mutation_policy="read_only",
        session_policy="reuse_allowed",
        output_contract="structured_review_result",
    ),
    "resolver": _RoleFamilyPolicy(
        execution_context="main_worktree",
        mutation_policy="vectl_facade_only",
        session_policy="reuse_forbidden",
        output_contract="resolution_report",
    ),
}


def _validate_role_profile(profile: RoleProfile) -> None:
    """Validate canonical family invariants for concrete profiles."""

    policy = _ROLE_FAMILY_POLICY.get(profile.prompt_family)
    if policy is None:
        return

    mismatches: list[str] = []
    if profile.execution_context != policy.execution_context:
        mismatches.append(
            f"execution_context={profile.execution_context!r} expected {policy.execution_context!r}"
        )
    if policy.mutation_policy is not None and profile.mutation_policy != policy.mutation_policy:
        mismatches.append(
            f"mutation_policy={profile.mutation_policy!r} expected {policy.mutation_policy!r}"
        )
    if policy.session_policy is not None and profile.session_policy != policy.session_policy:
        mismatches.append(
            f"session_policy={profile.session_policy!r} expected {policy.session_policy!r}"
        )
    if policy.output_contract is not None and profile.output_contract != policy.output_contract:
        mismatches.append(
            f"output_contract={profile.output_contract!r} expected {policy.output_contract!r}"
        )

    if mismatches:
        joined = ", ".join(mismatches)
        raise ValueError(
            f"Role profile {profile.role_id!r} violates {profile.prompt_family!r} family policy: {joined}"
        )


# Default role profiles per ORCHESTRATION-PLANE-DISPATCH-AND-PROMPT-POLICY.md §8, §11, §12.
# These provide sensible defaults; configuration can add/override roles.

_DEFAULT_ROLE_PROFILES: tuple[RoleProfile, ...] = (
    # Coder family (§11.2): linked_worktree, worktree_changes, reuse_allowed
    RoleProfile(
        role_id="python-executor",
        prompt_family="coder",
        template_id="coder_executor",
        execution_context="linked_worktree",
        mutation_policy="worktree_changes",
        session_policy="reuse_allowed",
        output_contract="freeform_evidence",
        default_runner="codex",
    ),
    RoleProfile(
        role_id="python-senior",
        prompt_family="coder",
        template_id="coder_senior",
        execution_context="linked_worktree",
        mutation_policy="worktree_changes",
        session_policy="reuse_allowed",
        output_contract="freeform_evidence",
        default_runner="codex",
    ),
    # Planner family (§11.3): main_worktree, vectl_facade_only, reuse_forbidden
    RoleProfile(
        role_id="vectl-planner",
        prompt_family="planner",
        template_id="planner_remediation",
        execution_context="main_worktree",
        mutation_policy="vectl_facade_only",
        session_policy="reuse_forbidden",
        output_contract="structured_plan_result",
        default_runner="codex",
    ),
    # Reviewer family (§11.4): main_worktree, read_only, reuse_allowed
    RoleProfile(
        role_id="gate-reviewer",
        prompt_family="reviewer",
        template_id="reviewer_gate",
        execution_context="main_worktree",
        mutation_policy="read_only",
        session_policy="reuse_allowed",
        output_contract="structured_review_result",
        default_runner="codex",
    ),
    RoleProfile(
        role_id="doc-reviewer",
        prompt_family="reviewer",
        template_id="reviewer_doc",
        execution_context="main_worktree",
        mutation_policy="read_only",
        session_policy="reuse_allowed",
        output_contract="structured_review_result",
        default_runner="codex",
    ),
    RoleProfile(
        role_id="spec-readiness-auditor",
        prompt_family="reviewer",
        template_id="reviewer_spec_readiness",
        execution_context="main_worktree",
        mutation_policy="read_only",
        session_policy="reuse_allowed",
        output_contract="structured_review_result",
        default_runner="codex",
    ),
    # Resolver family (§11.5): main_worktree, vectl_facade_only, reuse_forbidden.
    # ADR-orchestration-role-profile-config-and-resolver-cleanup.md freezes the
    # canonical default resolver agents to blocked-case-coordinator and
    # blocked-case-coordinator-tacit; legacy conflict/judge taxonomy is removed.
    RoleProfile(
        role_id="blocked-case-coordinator",
        prompt_family="resolver",
        template_id="resolver_blocked_case",
        execution_context="main_worktree",
        mutation_policy="vectl_facade_only",
        session_policy="reuse_forbidden",
        output_contract="resolution_report",
        default_runner="codex",
    ),
    RoleProfile(
        role_id="blocked-case-coordinator-tacit",
        prompt_family="resolver",
        template_id="resolver_blocked_case",
        execution_context="main_worktree",
        mutation_policy="vectl_facade_only",
        session_policy="reuse_forbidden",
        output_contract="resolution_report",
        default_runner="codex",
    ),
)


@dataclass(frozen=True)
class ConfigRoleProfileRegistry:
    """Configuration-backed lookup for open-ended role IDs.

    Authority: docs/ORCHESTRATION-PLANE-DISPATCH-AND-PROMPT-POLICY.md §8.3, §8.4

    Role profiles are loaded from orchestration configuration (defaults) and
    can be extended/overridden by explicit config entries. Unknown roles
    fail explicitly via UnknownRoleError.

    Adding a new role requires only adding a new RoleProfile entry — no
    orchestration-plane core contract changes are needed.
    """

    _profiles: dict[str, RoleProfile] = field(default_factory=dict)
    _default_role: str = "python-executor"
    _fallback_role: str = "python-executor"

    def __init__(
        self,
        profiles: tuple[RoleProfile, ...] | None = None,
        default_role: str = "python-executor",
        fallback_role: str = "python-executor",
    ) -> None:
        """Initialize registry with optional profile overrides.

        Args:
            profiles: Role profiles to register. If None, defaults are used.
            default_role: Default role ID used when step.agent is absent.
            fallback_role: Fallback role ID used only when explicitly allowed.
            default_role: Role to use when step.agent is missing (§9.1 precedence 2).
            fallback_role: Role to fall back to when explicitly allowed (§9.1 precedence 3).
        """
        source_profiles = profiles if profiles is not None else _DEFAULT_ROLE_PROFILES
        profile_dict: dict[str, RoleProfile] = {}
        for p in source_profiles:
            _validate_role_profile(p)
            profile_dict[p.role_id] = p
        object.__setattr__(self, "_profiles", profile_dict)
        object.__setattr__(self, "_default_role", default_role)
        object.__setattr__(self, "_fallback_role", fallback_role)

    def get(self, role_id: str) -> RoleProfile:
        """Look up a role profile by ID.

        Authority: docs/ORCHESTRATION-PLANE-DISPATCH-AND-PROMPT-POLICY.md §8.3

        Unknown roles fail explicitly — the system must not guess role
        meaning from string shape alone.

        Args:
            role_id: The role ID to look up.

        Returns:
            The RoleProfile for the given role ID.

        Raises:
            UnknownRoleError: If role_id is not registered.
        """
        profile = self._profiles.get(role_id)
        if profile is None:
            known = ", ".join(sorted(self._profiles.keys()))
            raise UnknownRoleError(
                f"Unknown role ID {role_id!r}; known roles: {known}. "
                "Role IDs must be explicitly registered — no string-shape guessing."
            )
        return profile

    def has_role(self, role_id: str) -> bool:
        """Check whether a role ID is registered.

        Args:
            role_id: The role ID to check.

        Returns:
            True if the role ID is known, False otherwise.
        """
        return role_id in self._profiles

    @property
    def default_role(self) -> str:
        """Return the configured default role ID."""
        return self._default_role

    @property
    def fallback_role(self) -> str:
        """Return the configured fallback role ID."""
        return self._fallback_role

    def resolve_role(
        self,
        step_agent: str | None,
        *,
        default_role_override: str | None = None,
    ) -> tuple[str, DispatchRoleSource]:
        """Resolve role allocation per §9.1 precedence.

        Authority: docs/ORCHESTRATION-PLANE-DISPATCH-AND-PROMPT-POLICY.md §9

        Precedence:
            1. step.agent if present
            2. default role if step.agent is absent
            3. fallback role only when explicitly allowed by policy

        The current implementation does not silently downgrade specialized
        roles (§9.2 anti-pattern).

        Args:
            step_agent: The step.agent value (may be None).
            default_role_override: Optional dispatch-time default role to use
                only when step.agent is absent. This preserves the canonical
                precedence boundary while allowing the orchestration app to
                request a non-global default without constructing a second
                registry/coordinator policy source.

        Returns:
            Tuple of (resolved_role_id, role_source).
        """
        if step_agent is not None and step_agent.strip():
            return (step_agent, "step.agent")
        if default_role_override is not None and default_role_override.strip():
            return (default_role_override, "default")
        return (self._default_role, "default")


# ---------------------------------------------------------------------
# Centralized PromptRegistry
# ---------------------------------------------------------------------

# Role-family prompt templates per §11.
# These are the minimum viable prompt structure per family.

_CODER_SYSTEM_PROMPT = (
    "You are a focused implementation agent. Execute the assigned step. "
    "Produce evidence of completion. Do not modify the plan."
)
_CODER_TASK_TEMPLATE = (
    "## Step: {step_id}\n"
    "### Description\n{description}\n\n"
    "### Verification\n{verification}\n\n"
    "### References\n{refs}\n\n"
    "### Evidence Template\n{evidence_template}\n\n"
    "### Verify Mode\n{verify_mode}\n\n"
    "Complete the step. Output structured evidence for vectl step completion."
)

_PLANNER_SYSTEM_PROMPT = (
    "You are a remediation planning agent. Produce a structured plan for "
    "resolving blocked or failed work. You operate under resolver coordination — "
    "you are not an ordinary step executor and must not claim new work."
)
_PLANNER_TASK_TEMPLATE = (
    "## Remediation Planning Task: {source_id}\n"
    "### Context\n{description}\n\n"
    "### Blocked Work\n{refs}\n\n"
    "Produce a structured plan outcome with proposed_steps and retest_steps. "
    "Your output contract is: structured_plan_result. "
    "Do not claim new steps or modify the plan directly."
)

_REVIEWER_SYSTEM_PROMPT = (
    "You are a structured review agent. Evaluate the assigned work and produce "
    "a machine-readable review result. Your output must be parseable as "
    "StructuredReviewResult (review_outcome, summary, findings, evidence_refs)."
)
_REVIEWER_TASK_TEMPLATE = (
    "## Review Task: {source_id}\n"
    "### Description\n{description}\n\n"
    "### References\n{refs}\n\n"
    "### Evidence Template\n{evidence_template}\n\n"
    "Produce a structured review result. review_outcome must be one of: "
    "pass, needs_fix, needs_replan, operator_required."
)

_RESOLVER_SYSTEM_PROMPT = (
    "You are the Blocked-Case Coordinator. You operate from the main worktree "
    "and may use approved vectl tool surfaces only. You must not claim new steps. "
    "Produce a ResolutionReport (status, summary, evidence_refs, operator_message). "
    "You are part of the canonical blocked-case coordinator resolver family used "
    "for all blocked-case sources — "
    "do not introduce conflict-specialized or judge-specific resolver sub-taxonomy. "
    "Escalate to operator (operator_required) when automatic closure is unsafe."
)
_RESOLVER_TASK_TEMPLATE = (
    "## Resolution Case: {source_id}\n"
    "### Context\n{description}\n\n"
    "### Blocked Steps\n{refs}\n\n"
    "Investigate and produce a resolution report. "
    "You may use approved vectl tools to inspect and act. "
    "Do not claim new steps or modify the plan outside the approved facade. "
    "Escalate to operator when automatic closure is unsafe or unverifiable."
)


@dataclass(frozen=True)
class ConfigPromptRegistry:
    """Configuration-backed PromptRegistry implementing role-family-specific rendering.

    Authority: docs/ORCHESTRATION-PLANE-DISPATCH-AND-PROMPT-POLICY.md §10, §11

    Centralized prompt rendering boundary. Different roles render from
    different prompt families (§11). The system must not assume all roles
    use the same prompt structure (§11.1).
    """

    _role_registry: RoleProfileRegistry | None = None

    def __init__(
        self,
        role_registry: RoleProfileRegistry | None = None,
    ) -> None:
        """Initialize prompt registry with optional role registry for lookup.

        Args:
            role_registry: Optional role registry for role-aware rendering.
                When provided, enables has_role() to delegate to the role registry.
        """
        object.__setattr__(self, "_role_registry", role_registry)

    def render(self, spec: DispatchSpec) -> PromptBundle:
        """Render role-specific prompt bundle for a dispatch spec.

        Authority: docs/ORCHESTRATION-PLANE-DISPATCH-AND-PROMPT-POLICY.md §10.1

        Prompt rendering is centralized behind this single interface.
        Different role families produce different system prompts and
        task templates (§11).

        Args:
            spec: The dispatch specification to render prompts for.

        Returns:
            PromptBundle with system_prompt, task_prompt, and messages.
        """
        family = spec.prompt_family
        template_data = _build_template_data(spec)

        system_prompt, task_template = _select_prompt_family(family)
        task_prompt = task_template.format_map(template_data)

        # Include role-specific context as messages
        messages: tuple[dict[str, str], ...] = _build_context_messages(spec)

        return PromptBundle(
            system_prompt=system_prompt,
            task_prompt=task_prompt,
            messages=messages,
        )

    def has_role(self, role_id: str) -> bool:
        """Check whether a role has prompt support.

        Delegates to role registry if available, otherwise checks built-in
        prompt families.

        Args:
            role_id: The role ID to check.

        Returns:
            True if the role has prompt support.
        """
        if self._role_registry is not None:
            return self._role_registry.has_role(role_id)
        # Fallback: check if the role family is known
        return role_id in _ROLE_FAMILY_MAP


def _build_template_data(spec: DispatchSpec) -> dict[str, str]:
    """Build formatting data from DispatchSpec for prompt template rendering.

    Args:
        spec: Dispatch specification.

    Returns:
        Dict with template substitution values.
    """
    return {
        "source_id": spec.source_id,
        "step_id": spec.step_id or spec.source_id,
        "role_id": spec.role_id,
        "description": spec.description or "(no description)",
        "verification": spec.verification or "(no verification criteria)",
        "refs": "\n".join(spec.refs) if spec.refs else "(no references)",
        "evidence_template": spec.evidence_template or "(no evidence template)",
        "verify_mode": spec.verify_mode,
        "prompt_family": spec.prompt_family,
        "output_contract": spec.output_contract or "freeform_evidence",
        "mutation_policy": spec.mutation_policy,
        "execution_context": spec.execution_context,
    }


def _select_prompt_family(
    family: str,
) -> tuple[str, str]:
    """Select system prompt and task template by role family.

    Authority: docs/ORCHESTRATION-PLANE-DISPATCH-AND-PROMPT-POLICY.md §11

    Args:
        family: The prompt family identifier.

    Returns:
        Tuple of (system_prompt, task_template).
    """
    if family == "coder":
        return (_CODER_SYSTEM_PROMPT, _CODER_TASK_TEMPLATE)
    if family == "planner":
        return (_PLANNER_SYSTEM_PROMPT, _PLANNER_TASK_TEMPLATE)
    if family == "reviewer":
        return (_REVIEWER_SYSTEM_PROMPT, _REVIEWER_TASK_TEMPLATE)
    if family == "resolver":
        return (_RESOLVER_SYSTEM_PROMPT, _RESOLVER_TASK_TEMPLATE)
    # Unknown family — use coder as base but note the family mismatch
    return (_CODER_SYSTEM_PROMPT, _CODER_TASK_TEMPLATE)


def _build_context_messages(spec: DispatchSpec) -> tuple[dict[str, str], ...]:
    """Build context messages for prompt bundle.

    Args:
        spec: Dispatch specification.

    Returns:
        Tuple of message dicts with role/content.
    """
    messages: list[dict[str, str]] = []
    if spec.execution_context == "main_worktree":
        messages.append(
            {
                "role": "system",
                "content": "EXECUTION CONTEXT: main_worktree. Work in the canonical main worktree.",
            }
        )
    if spec.mutation_policy == "vectl_facade_only":
        messages.append(
            {
                "role": "system",
                "content": "MUTATION POLICY: vectl_facade_only. Only use approved vectl tool surfaces.",
            }
        )
    elif spec.mutation_policy == "read_only":
        messages.append(
            {
                "role": "system",
                "content": "MUTATION POLICY: read_only. Do not modify any files.",
            }
        )
    return tuple(messages)


# Role ID -> prompt family mapping for has_role fallback
_ROLE_FAMILY_MAP: dict[str, str] = {
    profile.role_id: profile.prompt_family for profile in _DEFAULT_ROLE_PROFILES
}


# ---------------------------------------------------------------------
# Dispatch Coordinator
# ---------------------------------------------------------------------


class StepDataAdapter(Protocol):
    """Protocol for loading authoritative step data for dispatch construction.

    Authority: docs/ORCHESTRATION-PLANE-DISPATCH-AND-PROMPT-POLICY.md §6.1

    The dispatch coordinator loads step data through this adapter to keep
    core/plan awareness out of the coordinator itself.
    """

    def load_step_data(self, step_id: str) -> StepData | None:
        """Load authoritative step data for the given step ID.

        Args:
            step_id: The step ID to load.

        Returns:
            Step data if found, else None.
        """
        ...


@dataclass(frozen=True)
class StepData:
    """Authoritative step data used by the dispatch coordinator.

    Attributes:
        step_id: The step identifier.
        description: Step description from the plan.
        verification: Step verification criteria.
        refs: Step references.
        evidence_template: Step evidence template.
        verify: Step verification mode (expected_red/must_green/None).
        agent: Step agent assignment (may be None).
    """

    step_id: str
    description: str
    verification: str
    refs: tuple[str, ...]
    evidence_template: str
    verify: str | None
    agent: str | None


@dataclass(frozen=True)
class DispatchCoordinator:
    """Build DispatchSpec from ControlDecision, step data, and role registry.

    Authority: docs/ORCHESTRATION-PLANE-DISPATCH-AND-PROMPT-POLICY.md §6.1-6.3

    The dispatch coordinator is an orchestration-app-owned logic layer that sits
    between control and runtime (§6.3). It:

    1. Loads authoritative step data
    2. Resolves role/profile information
    3. Builds DispatchSpec
    4. Renders prompt content (via PromptRegistry)
    5. Constructs the runtime-facing execution request

    It must not be implemented by pushing prompt logic into control or runtime.
    """

    role_registry: ConfigRoleProfileRegistry
    prompt_registry: PromptRegistry
    step_adapter: StepDataAdapter
    runner: str = "codex"

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

        Args:
            decision: ControlDecision with kind='dispatch', step_id, and role.

        Returns:
            Complete DispatchSpec with all fields populated from authoritative data.

        Raises:
            UnknownRoleError: If the resolved role ID is not in the registry.
            ValueError: If decision is not a dispatch decision.
        """
        if decision.kind != "dispatch":
            raise ValueError(
                f"DispatchCoordinator.build_dispatch_spec requires dispatch decision, "
                f"got kind={decision.kind!r}"
            )

        step_id = decision.step_id
        if step_id is None:
            raise ValueError(
                "DispatchCoordinator.build_dispatch_spec requires step_id on dispatch decision"
            )

        # 1. Load authoritative step data
        step_data = self.step_adapter.load_step_data(step_id)
        if step_data is None:
            raise ValueError(f"Step data not found for step_id={step_id!r}")

        # 2. Resolve role (§9.1 precedence: step.agent > dispatch-request default > registry default)
        resolved_role, role_source = self.role_registry.resolve_role(
            step_data.agent,
            default_role_override=decision.role,
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


class CoreStepDataAdapter:
    """StepDataAdapter implementation backed by PlanCoreAdapter.

    Loads authoritative step data from the plan through vectl core.
    Uses the concrete PlanCoreAdapter which has a _plan_path attribute.
    """

    def __init__(self, core_adapter: PlanCoreAdapter) -> None:
        self._core_adapter = core_adapter

    def load_step_data(self, step_id: str) -> StepData | None:
        """Load step data from authoritative vectl core.

        Args:
            step_id: Step identifier.

        Returns:
            StepData if found, else None.
        """
        from vectl.io import load_plan_definition

        try:
            plan, _ = load_plan_definition(self._core_adapter._plan_path)
        except Exception:
            return None

        found = plan.find_step(step_id)
        if found is None:
            return None

        _, step = found
        return StepData(
            step_id=step.id,
            description=step.description,
            verification=step.verification,
            refs=tuple(step.refs),
            evidence_template=step.evidence_template,
            verify=step.verify,
            agent=step.agent,
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


__all__ = [
    "ConfigPromptRegistry",
    "ConfigRoleProfileRegistry",
    "CoreStepDataAdapter",
    "DispatchCoordinator",
    "ReviewResultParseError",
    "StepDataAdapter",
    "StepData",
    "UnknownRoleError",
    "normalize_parse_failure",
    "normalize_review_result",
    "step_verify_to_verify_mode",
]
