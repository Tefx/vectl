# RFC: Claim Limits

**Status:** Draft
**Date:** 2026-02-21
**Author:** software-architect

## Problem Statement

### The Batch Claim Problem

Agents may attempt to claim multiple steps at once with phrases like "let me batch claim and quickly implement these tests." This behavior causes several issues:

1. **Blocking cascades** — If Agent A claims steps 1-5, Agent B with affinity for step 3 is blocked
2. **Context overflow** — Multiple steps mean multiple contexts, leading to lost details
3. **Commitment trap** — Claiming creates an implicit contract; failing one taints the batch
4. **Attention drift** — Agent may lose focus on the original goal while juggling contexts
5. **Single point of failure** — If agent crashes or gets stuck, all claimed steps stall

### The Instance Identity Problem

A related issue: agent names are class names, not instance identifiers. Multiple instances of `python-engineer` all share the same name:

```
┌─────────────────────────────────────────────────────────────┐
│                   python-engineer (class name)              │
├─────────────────────────────────────────────────────────────┤
│  Instance A  ──claim──▶  step 1, step 2, step 3             │
│  Instance B  ──claim──▶  step 4                              │
│  Instance C  ──claim──▶  step 5, step 6                      │
└─────────────────────────────────────────────────────────────┘
                    ↓
          All are "python-engineer"
          Per-name limit = limit for entire role pool
```

This means:
- A per-name limit constrains ALL instances of a role
- Individual instances cannot be tracked or limited separately
- No way to implement "per-instance" limits

### Current Limitations

Today, there is no limit on how many steps an agent can claim:

```python
# Any agent can claim unlimited steps
def vectl_claim(agent: str, step_id: str | None = None, ...):
    # No limit check exists
    step.claimed_by = agent
```

## Decision Record

| # | Decision | Rationale |
|---|----------|-----------|
| 1 | **Default limit: 2 claims per agent name** | Prevents hoarding while allowing lookahead. Tunable via config. |
| 2 | **Instance identity via optional `instance_id`** | Enables per-instance tracking without breaking existing agents. |
| 3 | **Per-instance limits, not per-role limits** | Each agent instance has its own limit; multiple instances don't share quota. |
| 4 | **Configurable at plan level** | `max_claims_per_agent` on Plan allows project-specific tuning. |
| 5 | **Clear error message with actionable guidance** | Tell agent what to do (complete or defer existing claims). |
| 6 | **Backward compatible** — No `instance_id` means legacy behavior (per-name counting) | Old agents continue working; new agents get instance identity. |

## Options Considered

### Option A: Per-Role (Class Name) Limits

Limit total claims across all instances sharing a role name.

```python
# Limit: max 4 claims for "python-engineer" total
# Instance A: claims 2 steps
# Instance B: can only claim 2 more (shared pool)
```

**Pros:** Simple, no instance tracking needed
**Cons:** Penalizes legitimate multi-instance scenarios; blocks parallel work

### Option B: Per-Instance Limits (CHOSEN)

Limit claims per unique agent instance. Instance identity is established via `instance_id`.

```python
# Limit: max 2 claims per instance
# Instance A (id: "py-eng-a7f3"): claims 2 steps → blocked
# Instance B (id: "py-eng-b2d1"): claims 2 steps → allowed
```

**Pros:** Enables parallel work by multiple instances; fair allocation
**Cons:** Requires instance identity mechanism

### Option C: Combined (Role Cap + Instance Cap)

Dual limits: role-level ceiling + per-instance limits.

```python
# Role cap: max 10 claims for "python-engineer" total
# Instance cap: max 2 claims per instance
```

**Pros:** Prevents role-level hoarding while enabling instances
**Cons:** Complex; overkill for current needs

### Decision

**Choose Option B (Per-Instance Limits)** with Option A as fallback for agents without `instance_id`.

## Data Model Changes

### New Constants

```python
# src/vectl/core.py

DEFAULT_MAX_CLAIMS_PER_AGENT = 2
```

### Plan Model Extension

```python
# src/vectl/models.py

class Plan(BaseModel):
    # ... existing fields ...
    
    # RFC: docs/RFC-claim-limits.md
    # Maximum claims per agent instance. None uses DEFAULT_MAX_CLAIMS_PER_AGENT.
    max_claims_per_agent: int | None = None
```

### Step Model Extension

```python
# src/vectl/models.py

class Step(BaseModel):
    # ... existing fields ...
    claimed_by: str | None = None
    
    # RFC: docs/RFC-claim-limits.md
    # Unique identifier for the agent instance that claimed this step.
    # Enables per-instance claim limits. None for legacy claims (per-name counting).
    claimed_by_instance: str | None = None
```

### Claim Result Extension

```python
# src/vectl/mcp_server.py

class ClaimUsageData(BaseModel):
    """Claim usage information for agent feedback."""
    current: int       # Current claim count for this instance
    limit: int         # Maximum allowed
    remaining: int     # Remaining quota


class ClaimStepData(BaseModel):
    # ... existing fields ...
    claimed_by: str
    claimed_by_instance: str | None = None  # NEW
```

## Instance Identity Mechanics

### Generating Instance IDs

When an agent claims without providing `instance_id`:

```python
def _generate_instance_id(agent: str) -> str:
    """Generate a unique instance ID for an agent."""
    import uuid
    short_uuid = uuid.uuid4().hex[:8]
    return f"{agent}-{short_uuid}"
```

Example: `python-engineer-a7f3b2c1`

### Claim Flow with Instance ID

```
┌─────────────────────────────────────────────────────────────────┐
│                    First Claim (No instance_id)                 │
├─────────────────────────────────────────────────────────────────┤
│  Agent calls: vectl_claim(agent="python-engineer", step="x.1")  │
│                                                                 │
│  System:                                                        │
│  1. No instance_id provided                                     │
│  2. Generate: instance_id = "python-engineer-a7f3b2c1"          │
│  3. Check: claims for this instance = 0 (under limit)           │
│  4. Claim step, store claimed_by_instance                       │
│                                                                 │
│  Response:                                                      │
│  {                                                              │
│    "ok": true,                                                  │
│    "claimed": {                                                 │
│      "step_id": "x.1",                                          │
│      "claimed_by": "python-engineer",                           │
│      "claimed_by_instance": "python-engineer-a7f3b2c1"  ← RETURN│
│    },                                                           │
│    "claim_usage": {"current": 1, "limit": 2, "remaining": 1}    │
│  }                                                              │
└─────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────┐
│                Second Claim (With instance_id)                  │
├─────────────────────────────────────────────────────────────────┤
│  Agent calls:                                                   │
│    vectl_claim(                                                 │
│      agent="python-engineer",                                   │
│      instance_id="python-engineer-a7f3b2c1",                    │
│      step="x.2"                                                 │
│    )                                                            │
│                                                                 │
│  System:                                                        │
│  1. Count claims with claimed_by_instance = "...a7f3b2c1"       │
│  2. Found: 1 claim (under limit of 2)                           │
│  3. Claim step                                                  │
│                                                                 │
│  Response:                                                      │
│  {                                                              │
│    "ok": true,                                                  │
│    "claim_usage": {"current": 2, "limit": 2, "remaining": 0}    │
│  }                                                              │
└─────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────┐
│                Third Claim (Limit Exceeded)                     │
├─────────────────────────────────────────────────────────────────┤
│  Agent calls:                                                   │
│    vectl_claim(                                                 │
│      agent="python-engineer",                                   │
│      instance_id="python-engineer-a7f3b2c1",                    │
│      step="x.3"                                                 │
│    )                                                            │
│                                                                 │
│  System:                                                        │
│  1. Count claims with claimed_by_instance = "...a7f3b2c1"       │
│  2. Found: 2 claims (AT limit)                                  │
│  3. Reject with clear error                                     │
│                                                                 │
│  Response:                                                      │
│  {                                                              │
│    "ok": false,                                                 │
│    "error": "claim_limit_exceeded",                             │
│    "markdown": "**Claim Limit Exceeded**\n..."                  │
│  }                                                              │
└─────────────────────────────────────────────────────────────────┘
```

### Legacy Behavior (No Instance Tracking)

Agents that never provide `instance_id` fall back to per-name counting (Option A behavior):

```python
def count_claims(plan: Plan, agent: str, instance_id: str | None) -> int:
    """Count claims, handling both instance-aware and legacy modes."""
    count = 0
    for phase in plan.phases:
        for step in phase.steps:
            if step.status != StepStatus.CLAIMED:
                continue
            if step.claimed_by != agent:
                continue
            
            if instance_id is not None:
                # Instance mode: only count this instance's claims
                if step.claimed_by_instance == instance_id:
                    count += 1
            else:
                # Legacy mode: count all claims by this agent name
                # (includes claims with or without instance_id)
                count += 1
    return count
```

**Important:** Legacy mode is less fair — all calls without `instance_id` share one quota.

## Claim Behavior Matrix

| `instance_id` | Existing Claims | Limit | Result |
|---------------|-----------------|-------|--------|
| None (first call) | Any | — | Generate new instance_id, claim |
| None (subsequent) | Any | Counted by name | May hit limit (legacy) |
| Provided (valid) | 0 for instance | < limit | Claim |
| Provided (valid) | 1 for instance | = 2 | Claim |
| Provided (valid) | 2 for instance | = 2 | **Reject** |
| Provided (new) | 0 for instance | < limit | Claim (new instance) |

## Error Response

```json
{
  "ok": false,
  "error": "claim_limit_exceeded",
  "error_code": "claim_limit_exceeded",
  "markdown": "**Claim Limit Exceeded**\n\nAgent 'python-engineer' (instance: python-engineer-a7f3b2c1) has 2 active claim(s).\nMaximum allowed: 2.\n\n→ Use `vectl_status` to see your claimed steps.\n→ Use `vectl_complete` to finish current work.\n→ Use `vectl_lifecycle action=defer` to release claims."
}
```

## CLI UX

### Claim with Limit Display

```
$ vectl claim auth.api --agent python-engineer
✓ Claimed: auth.api — API Implementation
  Agent: python-engineer
  Instance: python-engineer-a7f3b2c1

  📊 Claim usage: 1/2 (1 remaining)

$ vectl claim auth.tests --agent python-engineer --instance python-engineer-a7f3b2c1
✓ Claimed: auth.tests — Integration Tests
  Agent: python-engineer
  Instance: python-engineer-a7f3b2c1

  📊 Claim usage: 2/2 (limit reached)

$ vectl claim auth.docs --agent python-engineer --instance python-engineer-a7f3b2c1
❌ Claim Limit Exceeded
  Agent 'python-engineer' (instance: python-engineer-a7f3b2c1) has 2 active claims.
  Maximum allowed: 2.

  → Use `vectl status` to see your claimed steps.
  → Use `vectl complete` to finish current work.
  → Use `vectl lifecycle defer` to release claims.
```

### Status with Claim Usage

```
$ vectl status --agent python-engineer --instance python-engineer-a7f3b2c1

## Phase Overview
...

## Claimed by python-engineer (instance: a7f3b2c1)
◉ auth.api — API Implementation [claimed]
◉ auth.tests — Integration Tests [claimed]

📊 Claim usage: 2/2 (limit reached)
```

## MCP UX

### `vectl_claim` Tool

```python
def vectl_claim(
    agent: str,
    step_id: str | None = None,
    guidance: bool = True,
    force: bool = False,
    instance_id: str | None = None,  # NEW: optional instance identifier
) -> dict:
    """Claim a step.
    
    Args:
        agent: Agent role/class name.
        step_id: Step to claim. If omitted, auto-selects first available.
        guidance: Include claim-time guidance.
        force: Override exclusive affinity violations.
        instance_id: Unique instance identifier. If omitted, one is generated.
                     Use the returned instance_id for subsequent claims to
                     share the claim limit with the same instance.
    
    Returns:
        Claim result with instance_id and claim_usage fields.
    """
```

### Response Structure

```json
{
  "ok": true,
  "markdown": "**Claimed:** auth.api — API Implementation\n...",
  "claimed": {
    "step_id": "auth.api",
    "step_name": "API Implementation",
    "phase_id": "auth",
    "phase_name": "Auth Module",
    "claimed_by": "python-engineer",
    "claimed_by_instance": "python-engineer-a7f3b2c1"
  },
  "claim_usage": {
    "current": 1,
    "limit": 2,
    "remaining": 1
  }
}
```

## YAML Examples

### Plan with Custom Limit

```yaml
version: 1
project: my-project
max_claims_per_agent: 3  # Allow up to 3 claims per instance (default is 2)
phases:
  - id: auth
    name: Auth Module
    steps:
      - id: auth.impl
        name: Implementation
      - id: auth.tests
        name: Tests
      - id: auth.docs
        name: Documentation
```

### Step with Instance Tracking (After Claims)

```yaml
steps:
  - id: auth.impl
    name: Implementation
    status: claimed
    claimed_by: python-engineer
    claimed_by_instance: python-engineer-a7f3b2c1
    claimed_at: "2026-02-21T10:30:00Z"
  - id: auth.tests
    name: Tests
    status: claimed
    claimed_by: python-engineer
    claimed_by_instance: python-engineer-a7f3b2c1
    claimed_at: "2026-02-21T10:35:00Z"
```

## Implementation Scope

### Files Modified

| File | Changes |
|------|---------|
| `models.py` | Add `max_claims_per_agent` to Plan, `claimed_by_instance` to Step |
| `core.py` | Add `ClaimLimitError`, `count_claims()`, modify `claim_step()` |
| `mcp_server.py` | Add `instance_id` param, `claim_usage` response, error handling |
| `cli.py` | Add `--instance` flag, update output formatting |

### Files Created

| File | Purpose |
|------|---------|
| `tests/test_claim_limits.py` | Claim limit enforcement tests |

### Test Coverage

**Instance Identity:**
- First claim generates instance_id
- Subsequent claims with same instance_id share quota
- Different instance_ids have independent quotas
- Legacy mode (no instance_id) counts by agent name

**Limit Enforcement:**
- Reject when at limit
- Allow claim after completing a step
- Allow claim after deferring a step
- Plan-level `max_claims_per_agent` override

**Error Cases:**
- Clear error message with actionable guidance
- MCP error_code field populated

**Backward Compatibility:**
- Old plans (no `max_claims_per_agent`) use default
- Steps without `claimed_by_instance` work correctly
- Old clients (no `instance_id` param) work in legacy mode

## Open Questions

### Q1: Should `force` bypass claim limits?

**Current proposal:** NO. Claim limits are system-level constraints, distinct from affinity enforcement.

- Affinity is about workflow correctness (who *should* do this)
- Limits are about resource fairness (how much can anyone do)

`force` bypasses affinity for emergency situations, but should NOT bypass limits.

**Alternative:** Add separate `--ignore-limit` flag for administrators.

### Q2: Should claim limits be enforced per-phase?

**Current proposal:** NO. Global limit per agent instance.

**Alternative:** Per-phase limits (e.g., max 2 claims in phase A, max 2 in phase B).

This adds complexity without clear benefit. Defer until use case is demonstrated.

### Q3: How to handle stale claims?

**Current proposal:** Manual defer via `vectl_lifecycle`.

**Future:** Automatic stale claim detection via heartbeat:
```python
class Step(BaseModel):
    claimed_by_instance: str | None = None
    last_heartbeat: str | None = None  # Future: auto-release stale claims
```

This is out of scope for the initial implementation.

## Non-Goals

| Item | Reason |
|------|--------|
| **Agent registry / authentication** | Identity is self-declared via instance_id. No verification. |
| **Cross-session instance persistence** | instance_id is per-session, not stored in agent state. |
| **Per-phase limits** | Complexity without demonstrated need. |
| **Dynamic limit adjustment** | Static plan-level config is sufficient for now. |
| **Claim expiration / heartbeat** | Manual defer is sufficient. Auto-release is future work. |
| **Admin override for limits** | `max_claims_per_agent` in plan is the override mechanism. |

## Summary

This RFC introduces claim limits to prevent agent hoarding and context overflow:

1. **Default limit of 2 claims** per agent instance (configurable per-plan)
2. **Instance identity** via optional `instance_id` for per-instance tracking
3. **Clear error messages** with actionable guidance
4. **Backward compatible** with legacy agents

The design balances simplicity (easy to understand) with flexibility (per-plan config, instance tracking) while avoiding complexity (no authentication, no per-phase limits).