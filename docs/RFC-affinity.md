# RFC: Agent Affinity Enforcement

**Status:** Draft
**Date:** 2026-02-18
**Author:** python-engineer (via vectl step `affinity.design-rfc`)

## Problem Statement

### The blind-tester Scenario

The `blind-tester` agent is designed to test implementations using ONLY public interfaces and documentation, enforcing information asymmetry to prevent hypothesis-encoded testing. However, if the same agent that wrote the implementation (`python-engineer`) claims the testing step, the value of blind testing is undermined — the agent already knows internal details.

### Workflow Discipline

When an organization designates specialized agents for specific workflows (e.g., `python-engineer` for implementation, `gate-reviewer` for phase gate reviews, `blind-tester` for QA), the intended separation of concerns can be violated if:

1. Agents claim steps outside their specialization
2. The same agent claims both implementation and review/test steps
3. No mechanism enforces the intended workflow

### Current Limitations

The `Step.agent` field exists for suggesting agents, but it's purely informational. There's no enforcement mechanism:

```yaml
steps:
  - id: auth.tests
    name: Integration Tests
    agent: blind-tester  # Just a suggestion, anyone can claim
```

Any agent can claim any step. This is correct default behavior (flexibility), but specialized workflows need opt-in enforcement.

## Decision Record

| # | Decision | Rationale |
|---|----------|-----------|
| 1 | **Default: suggested** — no enforcement | Zero friction for default use case. Most plans don't need strict agent assignment. |
| 2 | **Opt-in exclusive affinity** via `Step.affinity` field | Explicit declaration. Plans that need enforcement must declare it. |
| 3 | **Plan-level default** via `Plan.default_affinity` | Avoid per-step repetition for plans that want uniform enforcement. |
| 4 | **`--force` escape hatch** for emergencies | Overrides exclusive affinity with audit trail. Humans remain in control. |
| 5 | **Backward compatible** — no `agent` field means no enforcement | Existing plans unchanged. Old vectl versions ignore the new field. |

## Data Model Changes

### New Types

```python
class AffinityMode(str, Enum):
    """Agent affinity enforcement mode for steps."""
    SUGGESTED = "suggested"   # Default: warn on non-matching agent, allow claim
    EXCLUSIVE = "exclusive"   # Reject non-matching agent unless --force used
```

### Step Model Extension

Add `affinity` field to `Step`:

```python
class Step(BaseModel):
    id: str
    name: str
    status: StepStatus = StepStatus.PENDING
    # ... existing fields ...
    agent: str | None = None
    affinity: AffinityMode | None = None  # NEW: inherits from plan.default_affinity
    # ... rest of fields ...
```

**Semantics:**
- `None` (default): Inherits from `plan.default_affinity` (or `suggested` if plan default is also None)
- `suggested`: Log warning if claiming agent != step.agent, allow claim
- `exclusive`: Reject claim if claiming agent != step.agent, unless `--force`

### Plan Model Extension

Add `default_affinity` field to `Plan`:

```python
class Plan(BaseModel):
    version: int = 1
    project: str
    # ... existing fields ...
    default_affinity: AffinityMode = AffinityMode.SUGGESTED  # NEW: plan-level default
    phases: list[Phase] = Field(default_factory=list)
```

**Resolution order:**
1. `step.affinity` if set
2. `plan.default_affinity` if step affinity is None
3. `AffinityMode.SUGGESTED` if both are None

## Claim Behavior Matrix

The claim logic checks affinity when `step.agent` is set:

| `step.agent` | `step.affinity` | `agent_name matches` | CLI Result | MCP Result |
|--------------|-----------------|---------------------|------------|------------|
| `None` | any | — | Allow (no check) | Allow |
| `"blind-tester"` | `suggested` | No | **Warn** + Allow | Allow + `affinity_warning` |
| `"blind-tester"` | `suggested` | Yes | Allow | Allow |
| `"blind-tester"` | `exclusive` | No | **Reject** (exit 1) | Error + code |
| `"blind-tester"` | `exclusive` | Yes | Allow | Allow |
| `"blind-tester"` | `exclusive` | No + `--force` | Allow + audit | Allow + `force_override: true` |

### Warning Format (suggested affinity)

```
⚠️  Affinity warning: step 'auth.tests' suggests agent 'blind-tester'
   but is being claimed by 'python-engineer'.
   Proceeding (affinity: suggested)
```

### Error Format (exclusive affinity)

```
❌ Affinity violation: step 'auth.tests' has exclusive affinity for 'blind-tester'
   but is being claimed by 'python-engineer'.
   Use --force to override (will set affinity_override: true for audit trail)
```

### Exit Codes

| Scenario | Exit Code |
|----------|-----------|
| Allow (no affinity check) | 0 |
| Allow (affinity matches) | 0 |
| Allow (suggested + warned) | 0 |
| Reject (exclusive violation) | 1 |
| Allow (--force override) | 0 |

## Force Override Mechanism

When `--force` is used to bypass exclusive affinity:

1. Claim proceeds
2. `step.affinity_override = True` is set (NEW field on Step)
3. `step.affinity_override_by = <agent_name>` is set (NEW field on Step)
4. `step.affinity_override_at = <timestamp>` is set (NEW field on Step)

This creates an immutable audit trail visible in `vectl show <step>` and `vectl status`.

```yaml
steps:
  - id: auth.tests
    name: Integration Tests
    agent: blind-tester
    affinity: exclusive
    status: claimed
    claimed_by: python-engineer  # Override!
    affinity_override: true
    affinity_override_by: human-operator
    affinity_override_at: "2026-02-18T14:30:00Z"
```

### Step Model Additions

```python
class Step(BaseModel):
    # ... existing fields ...
    affinity_override: bool = False
    affinity_override_by: str | None = None
    affinity_override_at: str | None = None
```

## CLI UX

### `vectl claim` Command

```
vectl claim <step-id> --agent <name> [--force]
```

**New flag:**
- `--force`: Override exclusive affinity (sets audit trail fields)

**Output variations:**

```
# Normal claim (no agent set on step)
$ vectl claim auth.api --agent python-engineer
✓ Claimed: auth.api — API Implementation
  Agent: python-engineer

# Claim with suggested mismatch
$ vectl claim auth.tests --agent python-engineer
⚠️  Affinity warning: step 'auth.tests' suggests agent 'blind-tester'
   but is being claimed by 'python-engineer'.
   Proceeding (affinity: suggested)

✓ Claimed: auth.tests — Integration Tests
  Agent: python-engineer

# Claim rejected (exclusive)
$ vectl claim auth.tests --agent python-engineer
❌ Affinity violation: step 'auth.tests' has exclusive affinity for 'blind-tester'
   but is being claimed by 'python-engineer'.
   Use --force to override (will set affinity_override: true for audit trail)

# Claim with force override
$ vectl claim auth.tests --agent python-engineer --force
⚠️  Affinity override: step 'auth.tests' has exclusive affinity for 'blind-tester'
   Overridden by --force. Audit trail recorded.

✓ Claimed: auth.tests — Integration Tests
  Agent: python-engineer
  Affinity Override: true (by python-engineer at 2026-02-18T14:30:00Z)
```

### `vectl status` / `vectl show` Output

Steps with affinity show agent in status:

```
◉ auth.tests — Integration Tests
  Agent: blind-tester (exclusive)
  Status: claimed by python-engineer
  ⚠️ Affinity override: true (by human-operator)
```

## MCP UX

### `vectl_claim` Tool Response

Successful claim with affinity warning:

```json
{
  "ok": true,
  "claimed": {
    "step_id": "auth.tests",
    "step_name": "Integration Tests",
    "phase_id": "auth",
    "phase_name": "Auth Module",
    "claimed_by": "python-engineer"
  },
  "affinity_warning": {
    "step_agent": "blind-tester",
    "claiming_agent": "python-engineer",
    "affinity": "suggested",
    "message": "Step suggests 'blind-tester' but claimed by 'python-engineer'. Affinity is suggested, not enforced."
  }
}
```

Rejected claim (exclusive):

```json
{
  "ok": false,
  "error": "affinity_violation",
  "message": "Step 'auth.tests' has exclusive affinity for 'blind-tester' but was claimed by 'python-engineer'. Use force=true to override.",
  "details": {
    "step_id": "auth.tests",
    "step_agent": "blind-tester",
    "claiming_agent": "python-engineer",
    "affinity": "exclusive"
  }
}
```

Force override:

```json
{
  "ok": true,
  "claimed": {
    "step_id": "auth.tests",
    "step_name": "Integration Tests",
    "phase_id": "auth",
    "phase_name": "Auth Module",
    "claimed_by": "python-engineer",
    "affinity_override": true
  },
  "affinity_override": {
    "overridden_agent": "blind-tester",
    "override_by": "python-engineer",
    "message": "Exclusive affinity overridden with --force. Audit trail recorded."
  }
}
```

### New MCP Parameter

```
vectl_claim(step_id, agent, force=false)
```

- `force`: boolean, default `false` — Override exclusive affinity

## YAML Examples

### Plan with Default Affinity

```yaml
version: 1
project: my-project
default_affinity: suggested  # All steps inherit this unless overridden
phases:
  - id: auth
    name: Auth Module
    steps:
      - id: auth.impl
        name: Implementation
        agent: python-engineer  # Suggested, but not enforced
      - id: auth.tests
        name: Integration Tests
        agent: blind-tester
        affinity: exclusive     # THIS step requires blind-tester
```

### Step-Level Override

```yaml
version: 1
project: my-project
phases:
  - id: review
    name: Code Review
    steps:
      - id: review.gate
        name: Phase Gate Review
        agent: gate-reviewer
        affinity: exclusive     # Only gate-reviewer can claim
```

## Backward Compatibility

### Existing Plans (No `agent` field)

No enforcement. Agents claim steps freely as before.

```yaml
steps:
  - id: core.model
    name: Data Model
    # No agent field → no affinity check
```

### Existing Plans (With `agent` field, no `affinity`)

Default behavior: `suggested` affinity. Warning logged, claim allowed.

```yaml
steps:
  - id: core.api
    name: API Layer
    agent: python-engineer  # affinity: None → inherits suggested
```

Old vectl versions reading new plans: Pydantic ignores unknown fields, so `affinity` and `default_affinity` are silently ignored.

### Version Consideration

**No `version` bump required.** This is an additive, backward-compatible change:
- New fields have defaults
- Old behavior preserved for plans without new fields
- No breaking changes to existing APIs

## Implementation Scope

### Files Modified

| File | Changes |
|------|---------|
| `models.py` | Add `AffinityMode` enum, extend `Step` and `Plan` |
| `core.py` | Extend `claim_step()` with affinity logic |
| `cli.py` | Add `--force` flag to `claim` command, update output |
| `mcp_server.py` | Add `force` param, extend response structure |

### Files Created

| File | Purpose |
|------|---------|
| `tests/test_affinity.py` | Affinity enforcement tests (~25 tests) |

### Test Coverage

- Claim with no agent → no affinity check
- Claim with matching agent → allow
- Claim with suggested affinity mismatch → warn + allow
- Claim with exclusive affinity mismatch → reject
- Claim with exclusive + force → allow + audit trail
- Plan-level default affinity inheritance
- Step-level affinity override of plan default
- CLI exit codes
- MCP response structure
- YAML round-trip with new fields
- Backward compatibility: old plans load correctly

## Non-Goals

| Item | Reason |
|------|--------|
| **Agent registry / verification** | Agent names are free-form strings. Validation is out of scope. |
| **Multi-agent affinity** | A step can only have one suggested agent. Multiple agents = complexity without clear use case. |
| **Affinity on phases** | Phases don't have `claimed_by`. Affinity is step-level. |
| **Affinity inheritance from phase** | Explicit per-step or plan-level default. No phase-level setting. |
| **Warning suppression** | Warnings are informational. Agents can ignore them. |
| **Historical tracking** | Only current affinity state is stored. History is in git. |