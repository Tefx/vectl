# RFC: Atomic Batch Claim

## Status

Proposed.

## Problem

Orchestrators may decide several concrete step-to-agent assignments at once, then submit several `claim` operations in parallel. Each single-step claim is a write. Parallel writes to the same vectl state can race and fail under CAS protection. CAS is doing the right thing, but the orchestration path wastes tool calls, tokens, and time by expressing one logical batch as many independent mutations.

The problem is not step selection. The orchestrator has already decided which steps should be claimed by which agents. The missing operation is an atomic commit of those exact claim requests.

## Decision

Add an explicit atomic batch claim operation with CLI and MCP parity.

- CLI: `vectl claim-many`
- MCP: `vectl_claim_many`
- Core: `claim_many` / `claim_steps`

The operation accepts only explicit claim requests:

```json
{
  "claims": [
    {"step_id": "foo", "agent": "worker-a"},
    {"step_id": "bar", "agent": "worker-b"}
  ],
  "guidance": false,
  "force": false
}
```

It must not accept `count`, auto-select steps, or embed dispatch policy. Step selection remains owned by the orchestrator / decide layer. Claim mutation only commits the caller's already-decided assignments.

## Rationale

Keep `claim` and `claim_many` separate because they have different result shapes and failure semantics.

- `claim` is a single claim request with a single result.
- `claim_many` is one atomic mutation containing several explicit claim requests and per-request reporting.

Do not overload `step_id` with `string | string[]`. A broad parameter shape makes MCP calling less predictable and forces one tool/command to return incompatible single and batch result schemas.

Do not call the operation `reserve_steps`. Existing `claim` already supports orchestration on behalf of another agent through its `agent` parameter. This is a batch form of claim, not a new reservation lifecycle.

## Contract

`claim_many` must:

1. take explicit `{step_id, agent}` requests;
2. preserve input order in the response;
3. validate every requested claim against the same rules as single-step claim;
4. reject ambiguous duplicate step IDs the same way targeted commands do;
5. honor existing affinity and `force` behavior;
6. write at most one plan mutation for the batch;
7. avoid partial plan mutation;
8. avoid ghost claim records if plan save fails;
9. expose equivalent CLI and MCP capabilities;
10. leave existing `claim` behavior unchanged.

## Non-goals

- No `count` parameter.
- No auto-selecting available steps.
- No dispatch or agent-selection policy.
- No new `reserved` lifecycle state.
- No batch `complete`, `skip`, `reject`, or `defer` as part of this change.
- No weakening of CAS protections.

## Expected CLI shape

Machine-oriented path:

```bash
vectl claim-many \
  --claims-json '[{"step_id":"foo","agent":"worker-a"},{"step_id":"bar","agent":"worker-b"}]' \
  --json \
  --no-guidance
```

Optional human-friendly path:

```bash
vectl claim-many \
  --claim worker-a:foo \
  --claim worker-b:bar \
  --json \
  --no-guidance
```

## Expected MCP shape

```python
vectl_claim_many(
    claims=[
        {"step_id": "foo", "agent": "worker-a"},
        {"step_id": "bar", "agent": "worker-b"},
    ],
    guidance=False,
    force=False,
)
```

## Response shape

The response should be structured and machine-readable. It should include:

- `ok`: whether the whole batch committed;
- `claimed`: successfully claimed requests, in input order;
- `failed`: validation/conflict failures, in input order;
- `affinity_warnings`: non-blocking affinity warnings;
- `markdown`: short human summary;
- `lock_notice`: lock recalculation summary if applicable.

If atomic semantics reject partial success, `claimed` should be empty when `ok=false`, and `failed` should explain every blocking request known from validation.

## Consistency requirement

The existing single-step path writes both plan-visible claim state and branch-scoped claim records. Batch claim must not simply loop over single-step claim plus save. The implementation must explicitly handle the two state surfaces so a failed batch does not leave partial plan changes or orphaned claim records.

Acceptable implementation strategies include:

- validate all requests before writing any claim record or plan state;
- acquire all claim records in one bounded operation before saving plan, with rollback on plan save failure;
- or change the ordering so persisted state cannot expose a committed claim record without matching plan state.

The chosen strategy must be covered by tests.

## Simple Complete Implementation Plan

1. Add core request/result models for explicit batch claim requests.
2. Add core batch claim validation that reuses existing single-step claim rules without saving per step.
3. Add claims-store batch acquisition or rollback-safe handling.
4. Add `claim_many` core operation that performs one logical mutation and preserves atomic semantics.
5. Add MCP `vectl_claim_many` using the same core operation and structured response.
6. Add CLI `vectl claim-many` with `--claims-json`, `--claim`, `--json`, `--guidance/--no-guidance`, and `--force`.
7. Add tests for success, multi-agent batches, conflicts, affinity, duplicate IDs, no partial mutation, and CLI/MCP parity.
8. Update guide/help text to mention single-step `claim` vs atomic batch `claim-many` for orchestrators.

## Verification Gate

Required evidence before completion:

- targeted core lifecycle tests pass;
- targeted claims-store consistency tests pass;
- targeted MCP tests pass;
- targeted CLI tests pass;
- full project guard or documented repo-wide regression command passes according to current project policy.
