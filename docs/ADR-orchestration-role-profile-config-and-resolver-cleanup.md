# ADR: Orchestration role-profile config and resolver cleanup

## Status
Accepted

## Context

The orchestration-plane design accumulated mixed vocabulary from earlier drafts.
Some documents still treated legacy terms such as `conflict-resolver`,
`fallback_role`, `template_id`, and `judge`/`judgment`/`judgments` as live target
concepts.

That drift made the architecture ambiguous in exactly the places implementers
need clarity:

- whether ordinary roles are owned by plan data or config
- which resolver agents are canonical defaults
- whether planner mutates plans by editing `plan.yaml` or by using approved vectl
  surfaces
- whether historical `plan.yaml` vocabulary is still architecture authority

## Decision Drivers

- Freeze the final architecture vocabulary before further implementation
- Keep orchestration core thin and config-driven
- Preserve vectl authority boundaries
- Remove stale concepts that add branching without adding real capability

## Decision

1. **Plans reference roles; configuration defines role profiles.**
   Ordinary role profiles are configuration-owned. Orchestration core may load,
   validate, and consume role profiles, but it must not hardcode ordinary role
   profile definitions.

2. **Resolver defaults are frozen to two agent IDs only.**
   The only default resolver agents in the target architecture are:
   - `blocked-case-coordinator`
   - `blocked-case-coordinator-tacit`

3. **The following terms are removed from the target architecture.**
   They may appear only as historical references when explicitly marked as
   legacy/non-authoritative:
   - `conflict-resolver`
   - `resolver_conflict`
   - `judge`
   - `judgment`
   - `judgments`
   - `template_id`
   - `fallback_role`

4. **Planner remains orchestrator-invoked.**
   Planner is used only through orchestration-plane routing, typically under
   resolver-owned blocked-case handling. If planner output causes plan mutation,
   that mutation must be applied through the approved vectl facade. Planner never
   edits `plan.yaml` directly.

5. **Historical `plan.yaml` is not terminology authority for this architecture.**
   Existing `plan.yaml` contents remain untouched historical state. They are not
   authoritative for new orchestration-plane terminology, contracts, or role
   vocabulary.

## Consequences

- Role semantics are split cleanly: plan selects role IDs; config defines what
  those IDs mean.
- Resolver vocabulary becomes smaller and less ambiguous.
- Planner mutation authority stays aligned with vectl ownership.
- Historical plan data can coexist with the cleaned architecture without being
  mistaken for forward-looking contract authority.
