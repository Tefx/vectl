# RFC: `vectl_decide` — Deterministic Orchestration Advisor

> NOTE: This RFC is a **pre-reset reference document**. It captures an earlier
> deterministic-decision extraction design around `vectl_decide` and the legacy
> `src/vectl/driver/` implementation path. It is still useful as historical and
> implementation reference, but it is **not** the authoritative target
> architecture after the orchestration-plane reset. For the current target
> architecture, see `docs/ORCHESTRATION-PLANE-ARCHITECTURE.md` and
> `docs/ADR-orchestration-plane-reset.md`.

**Status:** Draft
**Date:** 2026-03-15
**Author:** tefx + Claude Opus 4.6

## Problem

The vectl-worktree-orchestrator is an LLM agent that spends 131-156K tokens
of context per turn to produce ~215 tokens of output (600:1 ratio). Most of
this reasoning is deterministic work the LLM does poorly:

1. **Continuation guard violations** — Orchestrator sometimes emits `progress`
   or `final` when executable work remains. Multiple hard rules and guards in
   the prompt exist solely to prevent this, yet violations persist.

2. **Session reuse decisions** — Require time perception (cache TTL awareness),
   ref overlap calculation, and depth counting. LLMs cannot reliably do any of
   these.

3. **Dispatch prompt assembly** — Orchestrator must include preamble, git
   warnings, task boundary, evidence template, and step details in every
   dispatch. It sometimes forgets components.

4. **Multi-step decision chains** — A single dispatch cycle requires
   `vectl_status` → reason → `vectl_claim` → reason → build prompt → dispatch.
   Each reasoning step is a full LLM turn at 131K+ context.

### Quantitative Evidence (from opencode production logs)

| Metric | Value |
|--------|-------|
| Orchestrator avg context/turn | 145,671 tokens |
| Orchestrator avg output/turn | 223 tokens |
| Cache hit rate | 96.1% (worktree variant) |
| 65% of turns produce | < 200 output tokens |
| Cost per turn (with cache) | ~$0.065 |
| Wall time per turn | ~8 seconds |
| Orchestrator % of total wall time | 3.8% |
| Orchestrator % of total cost | 23% |

**Key insight**: The cost/time savings from reducing turns are moderate (~$10
and ~20 min per session). The primary value is **behavioral reliability** —
eliminating classes of LLM errors that no amount of prompt engineering can
fully prevent.

## Design

### Core Concept

`vectl_decide` is a new MCP tool in the vectl server. It reads plan state,
applies deterministic rules, and returns a structured action list. The
orchestrator LLM executes actions mechanically, reserving its judgment for
exception handling and user interaction only.

```
Before:  Orchestrator (LLM) reads state → reasons → decides → acts
After:   Orchestrator (LLM) calls vectl_decide → receives actions → acts
```

### What `vectl_decide` Does

- Reads plan state (DAG, step statuses, dependencies)
- Computes claimable steps respecting DAG ordering
- Makes deterministic continuation/next-action decisions within the scope of
  this pre-reset RFC design
- Evaluates continuation guard (can_continue / must_stop)
- Returns structured action list with step metadata
- Logs decisions deterministically

### What `vectl_decide` Does NOT Do

- Does not execute actions (no claim, no complete, no dispatch)
- Does not generate dispatch prompts (returns structured data; orchestrator
  assembles prompt from its own templates)
- Does not replace the orchestrator for exception handling, user interaction,
  or escalation decisions
- Does not interact with opencode sessions or task tool

### Input Schema

```python
@mcp.tool()
def vectl_decide(
    running_tasks: list[RunningTask],
    completed_results: list[CompletedResult] | None = None,
    max_parallelism: int = 5,
) -> DecideOutput:
    ...
```

```python
class RunningTask(BaseModel):
    step_id: str
    agent: str
    task_id: str          # opencode session ID, for reuse tracking
    dispatched_at: float  # time.time() when dispatched — orchestrator
                          # must record this from vectl_decide's response

class CompletedResult(BaseModel):
    step_id: str
    task_id: str          # opencode session ID
    status: str           # SUCCESS | FAIL
    output_summary: str   # brief text from sub-agent (for evidence)
```

### Output Schema

```python
class DecideOutput(BaseModel):
    actions: list[Action]
    continuation: bool           # True = orchestrator must continue looping
    halt_reason: str | None      # If continuation=False, why
    decision_log: list[Decision]

class Action(BaseModel):
    action: Literal[
        "claim_and_dispatch",
        "complete",
        "wait",
        "escalate",
    ]
    # For claim_and_dispatch:
    step_id: str | None = None
    agent: str | None = None
    session: Literal["fresh", "reuse"] | None = None
    task_id: str | None = None       # Only if session=reuse
    step_description: str | None = None
    step_verification: str | None = None
    step_refs: list[str] | None = None
    # For complete:
    evidence: str | None = None
    # For wait/escalate:
    reason: str | None = None
    context: str | None = None

class Decision(BaseModel):
    decision: str    # SESSION_FRESH | SESSION_REUSE | CLAIM | COMPLETE | WAIT
    step_id: str | None = None
    why: str
```

### Session Reuse Logic

`vectl_decide` maintains in-memory state in the MCP server process:

```python
# Module-level state (persists for MCP server lifetime)
_completion_times: dict[str, float] = {}    # step_id → completion timestamp
_session_registry: dict[str, str] = {}      # step_id → task_id

REUSE_TTL = 300  # 5 minutes — aligned with Anthropic prompt cache TTL
```

**Reuse rules (narrow and deterministic):**

```python
def should_reuse(step, parent_step_id) -> tuple[bool, str | None]:
    """Returns (should_reuse, task_id_or_none)."""
    if not parent_step_id:
        return False, None
    if parent_step_id not in _completion_times:
        return False, None

    elapsed = time.time() - _completion_times[parent_step_id]
    if elapsed > REUSE_TTL:
        return False, None  # cache is cold, reuse costs more

    task_id = _session_registry.get(parent_step_id)
    if not task_id:
        return False, None

    return True, task_id
```

**Reuse fires only for:**
- Fix step immediately following its parent implementation step
- Retry of a transiently-failed step

**Everything else → fresh session.** No ref overlap calculation, no depth
counting, no heuristics.

**MCP server restart → state cleared → all decisions default to FRESH.**
Safe degradation.

### Continuation Guard

Replaces the prompt-based continuation guard (Section 3.1.1) with
deterministic code:

```python
def compute_continuation(plan, running_count, max_parallelism):
    claimable = get_next_steps(plan)
    capacity = max_parallelism - running_count

    if capacity > 0 and len(claimable) > 0:
        return True, None  # MUST continue
    if running_count > 0:
        return False, "WAITING_ON_RUNNING_SUBAGENTS"
    if len(claimable) == 0:
        return False, "NO_EXECUTABLE_STEPS"
    return False, "AT_MAX_PARALLELISM"
```

This eliminates the entire class of "premature stop" bugs that prompted
the creation of RESUME SIGNALS, Phase Auto-Advance Rule, Final-Success
Guard, and Pre-Progress Continuation Check in the orchestrator prompts.

## Orchestrator Prompt Impact

### Sections that can be simplified/removed:

| Section | Current | After vectl_decide |
|---------|---------|-------------------|
| 3.1.1 Continuation guard | ~60 lines of hard rules | "If `vectl_decide` returns `continuation: true`, continue. Otherwise stop with `halt_reason`." |
| 3.1.1 Final-Success Guard | ~15 lines | Subsumed by `continuation` field |
| Resume Signals | ~15 lines | Still needed (user interaction) |
| Phase Auto-Advance | ~10 lines | Subsumed by `continuation` field |
| 5 Agent Selection | ~20 lines | `vectl_decide` returns `agent` per step |
| 10 Dispatch template | ~40 lines of template | Keep template, but step data comes pre-extracted from `vectl_decide` |
| 10.5 Session Reuse | ~40 lines | "Follow `session` field from `vectl_decide`" |

**Estimated prompt reduction: ~200 lines removed, ~800 → ~500 lines.**

### New orchestrator loop (pseudocode):

```
loop:
  result = vectl_decide(running_tasks, completed_results)

  for action in result.actions:
    match action.action:
      "claim_and_dispatch":
        vectl_claim(action.step_id)
        prompt = build_dispatch_prompt(action)  # orchestrator's template
        task_result = task(prompt, agent=action.agent,
                          task_id=action.task_id)  # None if fresh
        record(step_id=action.step_id,
               task_id=task_result.task_id,
               dispatched_at=time.time())

      "complete":
        vectl_complete(action.step_id, action.evidence)

      "wait":
        emit progress(reason=action.reason)

      "escalate":
        # LLM uses its judgment here
        handle_escalation(action)

  if result.continuation:
    continue loop
  else:
    emit final(halt_reason=result.halt_reason)
```

## Data Model

No changes to plan.yaml schema. `vectl_decide` reads existing plan state
via `vectl.core.get_next_steps()` and `vectl.io.load_plan_definition()`.

In-memory state (MCP server process only):
- `_completion_times: dict[str, float]` — step completion timestamps
- `_session_registry: dict[str, str]` — step → task_id mapping

No persistence needed. Safe to lose on restart.

## Upgrade Path

1. **Phase 1**: Implement `vectl_decide` as a new MCP tool alongside existing
   tools. Orchestrator prompt updated to call it but retains fallback logic.
   Existing tools (`vectl_status`, `vectl_claim`, etc.) remain unchanged.

2. **Phase 2**: After validation, simplify orchestrator prompt by removing
   redundant guard sections. `vectl_decide` becomes the authoritative source
   for continuation and session decisions.

3. **Phase 3** (optional): If `vectl_decide` proves reliable, further reduce
   orchestrator prompt to ~300 lines. The orchestrator becomes a thin
   execution layer over `vectl_decide`'s decisions.

## Trade-offs

| For | Against |
|-----|---------|
| Eliminates continuation guard violations | New tool to maintain |
| Deterministic session reuse with time awareness | Adds coupling between vectl and orchestration concerns |
| Reduces orchestrator prompt complexity | `vectl_decide` needs to stay in sync with plan schema changes |
| Fewer LLM turns per dispatch cycle | Moderate cost savings only (~$10/session with 96% cache) |
| Decision logging is guaranteed | MCP server restart loses in-memory state |

## Open Questions

1. **Should `vectl_decide` also suggest the agent type**, or should this
   remain in the plan's step definition? Currently steps have an `agent`
   field in the plan — `vectl_decide` can simply pass it through.

2. **Escalation criteria** — What exactly triggers `escalate` vs `wait`?
   Proposed: 3 consecutive failures on same step → escalate. Otherwise retry
   or wait.

3. **Integration testing** — How to test `vectl_decide` without a running
   opencode instance? The tool only reads plan state and returns data, so
   unit tests against plan fixtures should suffice.
