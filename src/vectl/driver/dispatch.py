"""Deterministic prompt template rendering.

Responsibility: Render deterministic prompt strings from step metadata for
runner dispatch. Provides all context a worker needs without LLM generation.

Non-responsibility: Does NOT invoke runners. Does NOT generate prompts via LLM.
Does NOT own prompt templates as external files (templates are inline string constants
for simplicity and auditability).

Architecture Reference: docs/DRIVER-ARCHITECTURE.md Section 2.7
Architecture Reference: docs/DRIVER-ARCHITECTURE.md Q5 (Prompt Data Flow)
Blueprint Reference: DRIVER-BLUEPRINT.md Dispatch with Preflow flow (dispatch.py)
"""

from __future__ import annotations


def render_prompt(
    *,
    step_id: str,
    agent: str,
    description: str,
    verification: str,
    refs: list[str],
    worktree_path: str,
    session_reuse: bool,
    failure_context: str | None = None,
    plan_context: str = "",
    phase_context: str = "",
) -> str:
    """Render a dispatch prompt for a worker agent.

    Architecture: docs/DRIVER-ARCHITECTURE.md Section 2.7, render_prompt
    Blueprint: DRIVER-BLUEPRINT.md Dispatch with Preflow flow

    Template includes:
    - Step identity and description
    - Verification criteria
    - Reference file paths
    - Working directory (worktree)
    - Session context (if reusing)
    - Previous failure context (if retrying)
    - vectl completion instructions (claim is already done)

    Data flow (Architecture Q5):
    - Action dataclass from decide() carries step_description, step_verification,
      step_refs (populated from Step model during decide())
    - Driver passes these directly to render_prompt()
    - Additional context from:
      - DriverState.get_failure_context(step_id) -- previous failure output
      - Plan.context / Phase.context -- loaded from plan.yaml
      - worktree_path -- from the just-created worktree

    No additional plan reads or LLM calls are needed for prompt rendering.
    The prompt is a deterministic string template, not LLM-generated.

    Args:
        step_id: The step identifier (e.g., "core.impl", "core.test").
        agent: The agent name to dispatch (e.g., "python-executor", "claude").
        description: The step description from plan.yaml.
        verification: The verification criteria from plan.yaml.
        refs: List of reference file paths from plan.yaml.
        worktree_path: Path to the git worktree for this step.
        session_reuse: Whether to reuse a previous session (from SessionPool.find_reusable).
        failure_context: Previous failure output for retry prompts, or None.
        plan_context: Global plan context from Plan.context.
        phase_context: Phase-specific context from Phase.context.

    Returns:
        A deterministic prompt string ready for runner dispatch.

    Implementation note:
        This function MUST remain deterministic. The same inputs always produce
        the same output string. No LLM calls, no random values, no timestamps
        in the prompt content (timestamps are for events, not prompts).
    """
    raise NotImplementedError


# Template constants defined inline for auditability (per Architecture doc Section 2.7)
# Templates are string constants, not external files.

PROMPT_TEMPLATE_HEADER = """You are {agent} working on step {step_id}.

## Task

{description}

## Verification Criteria

{verification}

{refs_section}

## Working Directory

{worktree_path}
"""

PROMPT_TEMPLATE_SESSION_REUSE = """

## Session Context

You are resuming a previous session. Continue from where you left off.
"""

PROMPT_TEMPLATE_FAILURE_CONTEXT = """

## Previous Attempt

The previous attempt failed. Here is the failure output:

{failure_context}

Address the issues above in your implementation.
"""

PROMPT_TEMPLATE_CONTEXT = """
## Context

{context}
"""

PROMPT_TEMPLATE_FOOTER = """

## Instructions

1. Implement the task described above.
2. Ensure all verification criteria pass.
3. Commit your changes with a clear message.
4. Report completion via vectl complete with evidence.

When complete, report your findings.
"""
