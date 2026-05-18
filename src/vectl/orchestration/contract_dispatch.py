"""Dispatch, role, prompt, and review contract dataclasses.

Authority: docs/ORCHESTRATION-PLANE-DISPATCH-AND-PROMPT-POLICY.md
Authority: docs/ORCHESTRATION-PLANE-RESOLUTION-CONTRACT.md
Authority: docs/ADR-worktree-support.md section "Core Design"
"""

from dataclasses import dataclass
from typing import Literal

from vectl.core_checklist import ChecklistInventoryRevision, ChecklistItem

from vectl.orchestration.contract_literals import (
    DispatchRoleSource,
    DispatchSourceKind,
    ExecutionContext,
    MutationPolicy,
    ResolverClaimFlow,
    ResolverExecutionSite,
    ResolverMutationSurface,
    ReviewOutcome,
    RoleOutputContract,
    SessionMode,
)

@dataclass(frozen=True)
class ResolverAuthorityContract:
    """Pinned resolver authority boundary for blocked/unresolved handling.

    Attributes:
        execution_site: Resolver runs from the canonical main worktree.
        mutation_surface: Resolver mutations must go through the approved vectl
            facade only.
        claim_flow: Claiming remains reserved for normal flow, not resolver flow.
    """

    execution_site: ResolverExecutionSite = "main_worktree"
    mutation_surface: ResolverMutationSurface = "approved_vectl_facade_only"
    claim_flow: ResolverClaimFlow = "normal_flow_only"


@dataclass(frozen=True)
class DispatchSpec:
    """Unified dispatch-layer semantic contract.

    Authority: docs/ORCHESTRATION-PLANE-DISPATCH-AND-PROMPT-POLICY.md section 7.1

    This contract bridges control output, authoritative step semantics,
    role/profile policy, prompt rendering input, and runtime execution input.
    It does not replace ``ExecutionRequest``; it precedes and informs it.
    """

    source_kind: DispatchSourceKind
    source_id: str
    role_id: str
    role_source: DispatchRoleSource
    execution_context: ExecutionContext
    runner: str
    session_mode: SessionMode
    reuse_token: str | None = None
    reuse_runner: str | None = None
    step_id: str | None = None
    description: str = ""
    verification: str | None = None
    refs: tuple[str, ...] = ()
    evidence_template: str | None = None
    verify_mode: Literal["must_green", "expected_red", "none"] = "none"
    prompt_family: str = ""
    output_contract: str = ""
    mutation_policy: MutationPolicy = "read_only"
    checklist_inventory_revision: ChecklistInventoryRevision | None = None
    checklist_inventory: tuple[ChecklistItem, ...] = ()


@dataclass(frozen=True)
class RoleProfile:
    """Open role/profile contract for dispatch and prompt policy.

    Authority: docs/ORCHESTRATION-PLANE-DISPATCH-AND-PROMPT-POLICY.md section 8.2

    ``execution_context`` stays open to both ``linked_worktree`` and
    ``main_worktree`` so planner, reviewer, and resolver-family roles can run in
    the main worktree without redefining resolver as the only such actor.
    """

    role_id: str
    agent_id: str
    prompt_family: str
    execution_context: ExecutionContext
    mutation_policy: MutationPolicy
    session_policy: Literal["reuse_allowed", "reuse_forbidden"]
    output_contract: RoleOutputContract
    default_runner: str


@dataclass(frozen=True)
class PromptBundle:
    """Rendered prompt material for a dispatch spec.

    Authority: docs/ORCHESTRATION-PLANE-DISPATCH-AND-PROMPT-POLICY.md section 10.1
    """

    system_prompt: str
    task_prompt: str
    messages: tuple[dict[str, str], ...]


@dataclass(frozen=True)
class StructuredReviewResult:
    """Machine-readable review/gate outcome contract.

    Authority: docs/ORCHESTRATION-PLANE-DISPATCH-AND-PROMPT-POLICY.md section 13.1

    Non-pass outcomes are intended to normalize into explicit resolution cases
    rather than prose-only control decisions.
    """

    review_outcome: ReviewOutcome
    summary: str
    findings: tuple[str, ...] = ()
    evidence_refs: tuple[str, ...] = ()
