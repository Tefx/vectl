# Driver Agent Selection: Planner and Judge

## Status
Implemented

## Purpose

Define a single, non-hybrid selection contract for the driver's planner and
judge surfaces so users can predict whether vectl is relying on a bundled prompt
or on a real external named agent.

## Scope

In scope:
- planner agent selection
- judge agent selection
- fallback and error policy
- config semantics
- runtime semantics
- migration from current fields

Out of scope:
- worker/implementation agent routing in general
- runner-specific prompt quality tuning
- hybrid prompt layering

## Problem Statement

The current configuration shape mixes two different concepts:

1. choosing a real external runner agent via `--agent <name>`
2. injecting a bundled system prompt from vectl

That ambiguity is especially risky for judge because prompt layering can cause
two authorities to compete:
- the external runner's named agent persona
- vectl's bundled judge prompt

The desired architecture is simpler:
- no hybrid mode
- external agent mode is explicit
- prompt-only mode is explicit by configuration shape
- external-agent mode must fail fast if unavailable

## Design Goals

1. Make prompt authority unambiguous.
2. Avoid silent fallback when a user explicitly selected an external agent.
3. Keep planner and judge selection conceptually consistent.
4. Preserve the current strength of real planner agents like
   `vectl-planner` / `vectl-planner-slim`.
5. Keep judge safe by default through a single bundled prompt authority.

## Decision Summary

Use one shared selection concept for planner and judge:

- if `external_agent` is present, vectl uses external-agent mode
- if `external_agent` is absent/null, vectl uses prompt-only mode

No separate `mode` field is needed.
No `prompt_profile` field is needed.
No hybrid behavior is allowed.

## Config Contract

This proposal introduces a new nested `PlannerConfig` rather than reusing the
current flat `planner_agent_name` field.

Proposed runtime model shape:

```python
class PlannerConfig(BaseModel):
    runner: str = "opencode"
    external_agent_name: str | None = None

class JudgeConfig(BaseModel):
    runner: str = "opencode"
    external_agent_name: str | None = None
    structured_output: bool = True
    timeout: int = 60
    ...

class DriverConfig(BaseModel):
    planner: PlannerConfig = Field(default_factory=PlannerConfig)
    judge: JudgeConfig = Field(default_factory=JudgeConfig)
    ...
```

The intent is to make planner and judge structurally symmetric and to avoid
continuing with one flat field and one nested field for equivalent behavior.

### Planner

```yaml
planner:
  runner: opencode
  external_agent_name: vectl-planner-slim   # optional; absence means prompt-only
```

Prompt-only form:

```yaml
planner:
  runner: opencode
  external_agent_name: null
```

### Judge

```yaml
judge:
  runner: codex
  external_agent_name: null            # optional; absence/null means prompt-only
  structured_output: true
  timeout: 60
  preflight: true
  evidence_validation: true
  failure_classification: true
  escalation: true
  gate_assessment: true
  cold_context: true
  anomaly: true
```

## Runtime Semantics

### Planner: external-agent mode

Condition:
- `planner.external_agent_name` is non-empty

Behavior:
- use `planner.runner`
- pass `--agent <planner.external_agent_name>` only if the runner supports
  agent selection
- do not inject a bundled planner system prompt
- planner behavior authority belongs to the external named agent

### Planner: prompt-only mode

Condition:
- `planner.external_agent_name` is absent or null

Behavior:
- use `planner.runner`
- do not pass `--agent`
- inject vectl's bundled planner prompt
- planner behavior authority belongs to vectl's packaged prompt

### Judge: external-agent mode

Condition:
- `judge.external_agent_name` is non-empty

Behavior:
- use `judge.runner`
- pass `--agent <judge.external_agent_name>` only if the runner supports agent
  selection
- do not inject vectl's bundled judge system prompt
- keep verdict-shape enforcement and structured output rules as output-contract
  concerns only

### Judge: prompt-only mode

Condition:
- `judge.external_agent_name` is absent or null

Behavior:
- use `judge.runner`
- do not pass `--agent`
- inject vectl's bundled judge system prompt
- bundled prompt remains the only prompt authority

## Capability Rules

Runners must declare whether they support external agent selection.

This proposal adds explicit capability metadata to runner config:

```python
class RunnerConfig(BaseModel):
    command: str
    args: list[str] = Field(default_factory=list)
    supports_agent_selection: bool = False
    ...
```

Examples based on current evidence:
- opencode: supports external agent selection via `--agent`
- codex: may support agent placeholder usage only if vectl has a verified runner
  contract for it

If a runner does not support external agent selection, vectl must reject any
configuration that sets `external_agent_name` for that surface.

## Fallback and Error Policy

### Hard rule

If the user selected an external agent, vectl must either use that exact agent
or fail. It must not silently degrade to:
- the runner's default agent
- prompt-only mode
- a different external agent

### Validation stages

#### Config-load validation
Reject configuration when:
- `planner.runner` / `judge.runner` is missing from `runners`
- `external_agent_name` is set for a runner that does not support agent selection

#### Startup/preflight validation
Before first planner/judge invocation in external-agent mode, validate that the
named agent exists for that runner.

If existence cannot be verified reliably, vectl should fail closed rather than
run with uncertain persona selection.

#### Invocation-time behavior
If the runner reports agent-not-found or unsupported-agent behavior:
- surface a hard error
- emit an explicit observability event
- stop that planner/judge path
- do not auto-fallback

## Observability Contract

Planner and judge events should expose selection semantics explicitly:

- `surface`: `planner` or `judge`
- `runner`
- `selection_mode`: `external_agent` or `prompt_only`
- `external_agent_name`: string or null
- `prompt_source`: bundled resource name or null

This is required so logs can distinguish:
- real external persona selection
- bundled prompt operation

## Defaults for This Project

### Planner default

```yaml
planner:
  runner: opencode
  external_agent_name: vectl-planner-slim
```

Rationale:
- real external planner agents already exist and were verified in this repo
- planner is a natural fit for external-agent mode

### Judge default

```yaml
judge:
  runner: codex
  external_agent_name: null
```

Rationale:
- the bundled judge prompt is already the canonical behavior spec
- default `judge` was shown not to be a real external opencode agent
- prompt-only judge minimizes user surprise

## Migration Guidance

### Deprecated fields

- `planner_agent_name`
- `judge.agent_name`

> ⚠️ **Critical semantic shift**
>
> `judge.agent_name` behaved as a logical/default label under the old design.
> `judge.external_agent_name` means the user explicitly wants a real external
> runner agent. `null` now means prompt-only mode. This is not a trivial rename.

### Migration rules

#### Planner

Old:

```yaml
planner_agent_name: vectl-planner-slim
```

New:

```yaml
planner:
  runner: <resolved planner runner>
  external_agent_name: vectl-planner-slim
```

If old `planner_agent_name` was absent or null, migrate to:

```yaml
planner:
  runner: opencode
  external_agent_name: null
```

#### Judge

Old:

```yaml
judge:
  agent_name: judge
```

New default migration:

```yaml
judge:
  external_agent_name: null
```

Reason:
- `judge` behaved as a logical label, not as a reliable real external agent

If an existing config uses a non-default custom `judge.agent_name`, migration may
interpret it as intended external-agent mode and rewrite it to
`judge.external_agent_name`, but should emit a warning and require
capability/existence validation.

## Runtime Matrix

| Surface | `external_agent_name` | Runner supports agent selection | Agent exists | Result | Observability |
|--------|-------------------|----------------------------------|-------------|--------|---------------|
| planner | unset/null | n/a | n/a | prompt-only planner | `selection_mode=prompt_only`, `external_agent_name=null` |
| planner | set | yes | yes | external planner agent | `selection_mode=external_agent`, `external_agent_name=<name>` |
| planner | set | no | n/a | hard config/startup error | emit explicit planner selection error |
| planner | set | yes | no | hard startup/runtime error | emit explicit planner missing-agent error |
| judge | unset/null | n/a | n/a | bundled judge prompt | `selection_mode=prompt_only`, `external_agent_name=null` |
| judge | set | yes | yes | external judge agent, no bundled prompt | `selection_mode=external_agent`, `external_agent_name=<name>` |
| judge | set | no | n/a | hard config/startup error | emit explicit judge selection error |
| judge | set | yes | no | hard startup/runtime error | emit explicit judge missing-agent error |

## Trade-offs

### Gains
- no hidden prompt layering
- less user confusion
- clearer logs and debugging
- predictable semantics across planner and judge

### Costs
- less permissive than today's fuzzy behavior
- requires capability metadata and agent-existence probing
- judge external-agent mode gives users more rope to choose poor personas

## v1 Scope Definition

| Feature | Status |
|--------|--------|
| judge external-agent mode | MUST |
| judge prompt-only mode | MUST |
| planner external-agent mode | MUST |
| planner prompt-only mode | MAY (deferred; explicit `null` should be rejected until shipped) |
| runner capability metadata | MUST |
| agent existence probing | MUST |

## Open Questions

1. What module owns the external-agent existence probe and cache?
2. Which runners besides opencode should be declared agent-selection-capable at
   first release?
