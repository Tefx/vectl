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
    # Build refs section
    if refs:
        refs_section = "## Reference Files\n\n" + "\n".join(f"- {ref}" for ref in refs)
    else:
        refs_section = ""

    # Build header
    prompt = PROMPT_TEMPLATE_HEADER.format(
        agent=agent,
        step_id=step_id,
        description=description,
        verification=verification,
        refs_section=refs_section,
        worktree_path=worktree_path,
    )

    # Add session reuse context if requested
    if session_reuse:
        prompt += PROMPT_TEMPLATE_SESSION_REUSE

    # Add failure context if provided (retry path)
    if failure_context:
        prompt += PROMPT_TEMPLATE_FAILURE_CONTEXT.format(failure_context=failure_context)

    # Add plan context if provided
    if plan_context:
        prompt += PROMPT_TEMPLATE_CONTEXT.format(context=plan_context)

    # Add phase context if provided
    if phase_context:
        prompt += PROMPT_TEMPLATE_CONTEXT.format(context=phase_context)

    # Add footer with instructions
    prompt += PROMPT_TEMPLATE_FOOTER

    return prompt


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

⚠️ WORKTREE ISOLATION ENABLED

You are executing in an ISOLATED Git Worktree.
1. You MUST perform all your work within the `{worktree_path}` directory.
2. You MUST commit your changes to your isolated branch for handoff.
3. You MUST NOT modify `plan.yaml`, `.git/vectl/claims.json`, or any orchestrator state files.
4. When finished, simply return evidence. The orchestrator will merge your branch into main.

Your task is EXACTLY what is described below. Do NOT expand scope.
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
3. If you make code changes, create a local handoff commit in this assigned worktree with a clear message.
4. Return a concise handoff report with outcome, files changed, verification commands/results, commit SHA (if any), and blockers.
5. Do NOT call `vectl complete`, `vectl claim`, `vectl defer`, or any other plan-mutating command.
6. Do NOT merge or reconcile your worktree into the main repository; the orchestrator owns completion and merge authority.

When complete, report your findings.
"""
