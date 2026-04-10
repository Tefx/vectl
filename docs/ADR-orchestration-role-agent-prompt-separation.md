# ADR: Orchestration role, agent, and prompt-family separation

## Status
Accepted

## Context

The orchestration-plane cleanup froze two default resolver identities:

- `blocked-case-coordinator`
- `blocked-case-coordinator-tacit`

But implementation drift showed a recurring failure mode:

- role IDs survived in config and routing
- resolver family policy survived in docs and code
- yet runtime prompt selection collapsed both resolver defaults into one shared
  concrete system prompt

That collapse happened because the architecture had not explicitly frozen the
separation between three different concerns:

1. **orchestration-facing role identity**
2. **runtime agent/persona identity**
3. **shared prompt-contract family**

Without that separation, implementers can satisfy the letter of the resolver
cleanup while still erasing meaningful runtime differences between the two
default resolver agents.

## Decision Drivers

- Preserve the two frozen default resolver agent identities as real runtime
  distinctions, not just names in config
- Keep the orchestration core thin and config-driven
- Preserve one shared resolver contract without inventing resolver sub-taxonomy
- Prevent future prompt-family simplification from erasing required role-level
  behavior differences

## Decision

1. **`role_id`, `agent_id`, and `prompt_family` are distinct architectural
   concepts.**
   - `role_id` is the orchestration-facing identity selected by plan or routing.
   - `agent_id` is the concrete runtime agent/persona prompt identity.
   - `prompt_family` is the shared contract scaffold for a class of roles.

2. **The architecture must not require `role_id == agent_id`.**
   They may coincide for many ordinary roles, but that equality is an allowed
   convenience, not a required invariant.

3. **Prompt rendering precedence is `agent_id` first, `prompt_family` second.**
   If a role profile names a specialized runtime agent/persona, prompt selection
   must use that agent/persona-specific implementation. Family-level templates
   are the fallback, not the authority that erases agent distinctions.

4. **The two default resolver agents share one resolver contract, not one forced
   concrete prompt.**
   `blocked-case-coordinator` and `blocked-case-coordinator-tacit` must both
   obey the same resolver-family authority constraints:
   - `ResolutionCase` input
   - `ResolutionReport` output
   - `main_worktree` execution
   - `vectl_facade_only` mutation policy
   - no ordinary claim semantics

   But they remain distinct runtime agent identities and must stay independently
   selectable.

5. **Resolver differentiation must not be implemented by reviving legacy
   taxonomy.**
   Distinct resolver prompts are allowed. Distinct legacy concepts such as
   `conflict-resolver`, `judge`, `judgment`, `judgments`, `template_id`, or
   `fallback_role` are still removed from the target architecture.

## Consequences

- Resolver defaults remain both small in vocabulary and real at runtime.
- Prompt ownership stays centralized without forcing family-level flattening.
- Config-backed role profiles gain a clearer semantic contract.
- Tests must protect role-level prompt differences where the architecture
  requires them.
- Future implementers can no longer claim compliance by keeping two role names
  while collapsing them into one identical concrete prompt.
