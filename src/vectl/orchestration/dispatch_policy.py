"""
Dispatch coordinator, role-profile registry, prompt registry, and
structured review-result normalization.

Authority: docs/ORCHESTRATION-PLANE-DISPATCH-AND-PROMPT-POLICY.md
Authority: docs/ORCHESTRATION-PLANE-INTERFACES.md sections 3, 4
Authority: docs/ORCHESTRATION-PLANE-RESOLUTION-CONTRACT.md

Public surfaces:
    - DispatchCoordinator          (dispatch-spec construction from control output)
    - ConfigRoleProfileRegistry    (configuration-backed role-profile authority)
    - ConfigPromptRegistry         (configuration-backed prompt authority)
    - normalize_review_result      (review-outcome -> ResolutionCase normalizer)
    - normalize_parse_failure      (unparseable review output -> ResolutionCase)
    - step_verify_to_verify_mode  (step.verify -> DispatchSpec.verify_mode)
    - UnknownRoleError             (explicit failure for unknown role IDs)
    - ReviewResultParseError       (error for unparseable review results)
"""

# that share the authority documents listed above; splitting would widen this scoped archive/policy
# remediation beyond owned files and risk import/API churn.

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from vectl.orchestration.config import (
    DEFAULT_DISPATCH_ROLE_ID,
    default_role_profiles,
    validate_role_profiles,
)
from vectl.orchestration.contracts import (
    ControlDecision,
    CoreSnapshot,
    DispatchRoleSource,
    DispatchSpec,
    PromptBundle,
    ResolutionCase,
    RoleProfile,
    RosterSnapshot,
    RuntimeSnapshot,
    StructuredReviewResult,
)
from vectl.orchestration.core_adapter import CoreAdapter
from vectl.orchestration.step_data import StepData

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


class DispatchAuthorityError(ValueError):
    """Raised when dispatch skips required live prompt/role checkpoints.

    Authority: docs/ORCHESTRATION-PLANE-LIVE-AUTHORITY-CONTRACT-LOCK.md §3.4, §4.1
    'If any checkpoint cannot be satisfied, dispatch must fail explicitly rather
    than silently degrading to a static seam.'
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
# Configuration-backed role-profile authority
# ---------------------------------------------------------------------


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
    _default_role: str = DEFAULT_DISPATCH_ROLE_ID

    def __init__(
        self,
        profiles: tuple[RoleProfile, ...] | None = None,
        default_role: str = DEFAULT_DISPATCH_ROLE_ID,
    ) -> None:
        """Initialize registry with optional profile overrides.

        Args:
            profiles: Role profiles to register. If None, configuration defaults are used.
            default_role: Default role ID used when step.agent is absent.
        """
        source_profiles = profiles if profiles is not None else default_role_profiles()
        validation_errors = validate_role_profiles(source_profiles)
        if validation_errors:
            raise ValueError(validation_errors[0])
        profile_dict: dict[str, RoleProfile] = {}
        for p in source_profiles:
            profile_dict[p.role_id] = p
        object.__setattr__(self, "_profiles", profile_dict)
        object.__setattr__(self, "_default_role", default_role)

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
# Centralized prompt authority
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
    "### Deterministic Checklist Inventory\n{checklist_inventory}\n\n"
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
    "Produce direct vectl-facade mutation instructions for remediation and retest handling. "
    "Your output contract is: vectl_facade_mutation. "
    "Do not claim new steps or modify the plan directly; "
    "all mutations stay behind the approved vectl facade."
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
    "do not introduce conflict-specialized or reviewer-specific resolver sub-taxonomy. "
    "Escalate to operator (operator_required) when automatic closure is unsafe."
)
_RESOLVER_TACIT_SYSTEM_PROMPT = (
    "You are the Blocked-Case Coordinator (Tacit Edition). You operate from the main "
    "worktree and may use approved vectl tool surfaces only. You must not claim new steps. "
    "Produce a ResolutionReport (status, summary, evidence_refs, operator_message). "
    "Use stronger pattern recognition to discover authority-safe repair or delegation paths "
    "before concluding a case is operator-bound. Do not introduce conflict-specialized or "
    "reviewer-specific resolver sub-taxonomy."
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
_RESOLVER_TACIT_TASK_TEMPLATE = (
    "## Resolution Case: {source_id}\n"
    "### Context\n{description}\n\n"
    "### Blocked Steps\n{refs}\n\n"
    "Investigate and produce a resolution report. "
    "Look for hidden authority-safe repair or delegation paths before escalating. "
    "You may use approved vectl tools to inspect and act. "
    "Do not claim new steps or modify the plan outside the approved facade."
)

_PROMPT_CONTENT_BY_AGENT_ID: dict[str, tuple[str, str]] = {
    "blocked-case-coordinator": (_RESOLVER_SYSTEM_PROMPT, _RESOLVER_TASK_TEMPLATE),
    "blocked-case-coordinator-tacit": (
        _RESOLVER_TACIT_SYSTEM_PROMPT,
        _RESOLVER_TACIT_TASK_TEMPLATE,
    ),
}


@dataclass(frozen=True)
class ConfigPromptRegistry:
    """Configuration-backed prompt authority implementing role-family rendering.

    Authority: docs/ORCHESTRATION-PLANE-DISPATCH-AND-PROMPT-POLICY.md §10, §11

    Centralized prompt rendering boundary. Different roles render from
    different prompt families (§11). The system must not assume all roles
    use the same prompt structure (§11.1).
    """

    _role_registry: ConfigRoleProfileRegistry | None = None

    def __init__(
        self,
        role_registry: ConfigRoleProfileRegistry | None = None,
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
        profile = self._resolve_role_profile(spec.role_id)
        family = spec.prompt_family
        template_data = _build_template_data(spec)

        system_prompt, task_template = _select_prompt_content(
            family=family,
            agent_id=profile.agent_id if profile is not None else spec.role_id,
        )
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

    def _resolve_role_profile(self, role_id: str) -> RoleProfile | None:
        if self._role_registry is None:
            return None
        try:
            return self._role_registry.get(role_id)
        except KeyError:
            return None


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
        "checklist_inventory": _render_checklist_inventory(spec),
    }


def _render_checklist_inventory(spec: DispatchSpec) -> str:
    """Render worker-visible deterministic checklist receipt context.

    Authority: docs/RFC-deterministic-checklists.md §6.

    The orchestrator injects exact item IDs and the snapshot revision. Workers
    report desired final state by item_id/revision; they are not asked to do
    natural-language fuzzy mapping or mutate plan state directly.
    """
    if not spec.checklist_inventory:
        return "(no checklist inventory for this step)"

    revision = spec.checklist_inventory_revision or ""
    lines = [
        "The orchestrator owns checklist mutation. Do not call checklist mutation tools.",
        "Do not perform natural-language fuzzy matching for these receipts; use item_id exactly.",
        f"checklist_inventory_revision: {revision}",
        "items:",
    ]
    for item in spec.checklist_inventory:
        state = "checked" if item.checked else "unchecked"
        lines.extend(
            [
                f"  - field: {item.field}",
                f"    index: {item.index}",
                f"    item_id: {item.item_id}",
                f"    state: {state}",
                f"    text: {item.text}",
            ]
        )
    lines.extend(
        [
            "Return checklist receipt entries only when you intentionally request a final state change:",
            "checklist_receipt:",
            "  - step_id: <step_id>",
            "    field: <description|verification>",
            "    item_id: <exact item_id from inventory>",
            "    checklist_inventory_revision: <revision shown above>",
            "    checked: <true|false>",
        ]
    )
    return "\n".join(lines)


def _select_prompt_content(
    *,
    family: str,
    agent_id: str,
) -> tuple[str, str]:
    """Select system prompt and task template by role family.

    Authority: docs/ORCHESTRATION-PLANE-DISPATCH-AND-PROMPT-POLICY.md §11

    Args:
        family: The prompt family identifier.
        agent_id: The concrete agent/persona identifier.

    Returns:
        Tuple of (system_prompt, task_template).
    """
    prompt_by_agent = _PROMPT_CONTENT_BY_AGENT_ID.get(agent_id)
    if prompt_by_agent is not None:
        return prompt_by_agent
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
                "content": (
                    "MUTATION POLICY: vectl_facade_only. Only use approved vectl tool surfaces."
                ),
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
    profile.role_id: profile.prompt_family for profile in default_role_profiles()
}


from vectl.orchestration.dispatch_coordinator import DispatchCoordinator, normalize_parse_failure, normalize_review_result

__all__ = [
    "ConfigPromptRegistry",
    "ConfigRoleProfileRegistry",
    "DispatchAuthorityError",
    "DispatchCoordinator",
    "ReviewResultParseError",
    "StepData",
    "UnknownRoleError",
    "normalize_parse_failure",
    "normalize_review_result",
    "step_verify_to_verify_mode",
]
