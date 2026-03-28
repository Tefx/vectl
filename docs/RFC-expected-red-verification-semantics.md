# RFC: Expected-Red Verification Semantics for Planning and Orchestration

**Status:** Draft  
**Date:** 2026-03-28  
**Author(s):** software-architect persona (draft for discussion)

## 1. Problem / Current Situation

vectl currently treats failed verification signals too uniformly inside planning and orchestration.

In the current system shape:

- steps are often interpreted as either "success" or "failure"
- failed test commands are commonly treated as step failure
- orchestrator retry/escalation logic does not reliably distinguish:
  - **operational failure** (agent/tool/runtime problem)
  - **expected product gap** (test demonstrates missing implementation)
  - **verification failure after implementation** (real quality failure)

This becomes incorrect for test-first or gap-exposing work.

A pre-implementation test step can be *successful work* even when the test command exits non-zero, if its purpose was to expose the missing behavior and record the gap. By contrast, post-implementation verification must still be green before the corresponding implementation work is considered complete.

This ambiguity is especially important because this repository is actively moving orchestration behavior from prompt convention into system logic (`vectl_decide`, plan semantics, completion rules, structured evidence).

## 2. Incident Summary

Recent orchestration behavior exposed the problem clearly:

- a worker was dispatched on a test-oriented step before implementation existed
- the resulting red test outcome was interpreted as step failure
- orchestration then retried, escalated, or pushed the worker to "fix" the red state
- in some cases this encouraged scope violation, where a worker on a test-only step attempted to modify `src/` in order to make the step green

This was not merely an agent-quality issue. The system lacked an explicit notion that some red outcomes are expected and should complete the step by documenting the gap.

## 3. Why Prompt-Only Fixes Are Insufficient

Prompt guidance can reduce confusion, but it is not a durable control plane for this distinction.

Prompt-only fixes are insufficient because:

1. **They do not change completion semantics.**  
   If the runtime still maps non-zero verification to generic failure, prompt wording only masks the problem.

2. **They do not change retry/escalation rules.**  
   The orchestrator still needs machine-readable criteria for when to retry, when to complete, and when to escalate.

3. **They do not preserve intent across tools.**  
   Planning, dispatch, completion, reporting, and future deterministic advisors all need the same meaning.

4. **They are brittle under system replacement.**  
   This repo is replacing/upgrading the orchestrator. Semantics need to live in shared contracts, not in one long prompt.

5. **They do not protect scope boundaries.**  
   Without explicit step semantics, "make it green" remains an attractive but wrong local optimization for workers.

## 4. Proposed Design Direction

### Core principle

Separate **step execution success** from **product verification outcome**.

The system should distinguish at least these cases:

| Case | Meaning | Orchestrator treatment |
|------|---------|------------------------|
| Operational failure | command/tool/agent could not perform the step as intended | retry or escalate |
| Expected red | step intentionally exposed a missing capability before implementation | complete with evidence |
| Required green failure | post-implementation verification did not pass | fail / retry / escalate according to policy |
| Required green success | verification passed as required | complete |

### Recommended semantic model

Each verification-bearing step should declare its **verification semantics** explicitly.

Recommended conceptual values:

- `expected_red` — red is acceptable when it demonstrates the intended missing behavior
- `must_green` — verification must pass for completion
- `informational` — evidence is collected, but pass/fail does not gate completion

For this repo, the most important distinction is between `expected_red` and `must_green`.

### Recommended behavioral rules

1. **Pre-implementation test steps**
   - may use `expected_red`
   - complete when they:
     - run the intended check, and
     - produce evidence of the gap, and
     - do not cross the step's write boundary

2. **Implementation and post-implementation verification steps**
   - use `must_green`
   - do not complete until required checks pass

3. **Retry/escalation logic**
   - must trigger on operational failure
   - must not trigger solely because an `expected_red` step produced red output as intended

4. **Scope discipline**
   - test-only / gap-exposing steps should not modify implementation files unless the step explicitly authorizes it
   - orchestration should preserve this as part of dispatch intent, not leave it implicit

### Architectural consequence

Expected-red is not just a prompt nuance. It is a planning and orchestration contract that should appear in:

- plan step semantics
- dispatch instructions
- completion evaluation
- retry/escalation policy
- evidence/reporting surfaces

## 5. Minimal Change Set

A minimal, low-risk path is:

### A. Add explicit step-level verification semantics
Introduce an optional step field or equivalent metadata, defaulting to current behavior:

- default: `must_green`
- opt-in: `expected_red`

### B. Teach orchestration to respect it
Update orchestration / deterministic advisory logic so that:

- `expected_red` + observed red + valid evidence => complete, not fail
- `must_green` + observed red => fail path
- command/runtime/tool failure remains operational failure in either mode

### C. Standardize evidence expectations
For `expected_red`, completion evidence should include:

- command(s) run
- observed failing output or summary
- what gap was demonstrated
- confirmation that no out-of-scope implementation change was performed

### D. Update worker-facing prompts second, not first
Prompts should reflect the new semantics, but only as a delivery mechanism for a system rule that already exists elsewhere.

This minimal set keeps schema and runtime changes small while fixing the main failure mode.

## 6. Ideal Change Set

The ideal design is more explicit and more durable.

### A. Typed verification contract in plan/runtime
Represent verification intent as structured plan data rather than prose-only conventions.

Suggested shape at the design level:

- step intent
- verification semantics
- allowed write boundary
- completion evidence requirements

### B. Structured completion outcomes
Instead of compressing everything into success/failure text, completion processing should distinguish:

- `completed_expected_red`
- `completed_green`
- `failed_operational`
- `failed_required_green`

This improves downstream reporting, retries, and analytics.

### C. Deterministic orchestration policy
`vectl_decide` or its equivalent should own the rule evaluation for:

- whether a result is terminal
- whether a retry is valid
- whether escalation is warranted
- whether the next step can be unblocked

### D. Reporting visibility
User-facing status/reporting should show expected-red explicitly so the plan does not appear "broken" when it is behaving correctly.

Example distinction:

- "Step completed: gap reproduced as expected"
- not "Step failed"

### E. Boundary-aware dispatch
Dispatch payloads should carry the step's semantic mode and write boundary so workers are less likely to repair product code from a test-only task.

## 7. Compatibility / Migration Considerations

### Backward compatibility

To preserve current behavior:

- existing steps should default to `must_green`
- expected-red behavior should be opt-in until proven stable

This avoids silently reclassifying existing verification failures.

### Migration posture

Recommended migration sequence:

1. add schema support for explicit verification semantics
2. update orchestration logic to honor it
3. update prompts/templates to explain it
4. gradually annotate known pre-implementation test steps

### Historical plans and evidence

No mandatory historical migration is required for old evidence. Past steps can remain as recorded. The important change is forward behavior.

### Mixed-mode period

During rollout, some steps will be annotated and some will not. The system should therefore behave as:

- annotated `expected_red` => new semantics
- unannotated => legacy `must_green`

## 8. Open Questions

1. What is the exact field name and home for this contract in `plan.yaml`?
2. Should expected-red be step-local only, or can phases define a default?
3. Does the system need a third mode such as `informational`, or is `expected_red` + `must_green` enough for now?
4. Should allowed write boundaries be explicit plan data, or remain in step descriptions/evidence templates?
5. How should `vectl_complete` and status rendering expose expected-red outcomes without making the plan look failed?
6. Should expected-red steps unblock downstream implementation immediately upon completion, or are there cases where human review should intervene?
7. How much of this belongs in `vectl_decide` versus generic completion validation shared by CLI and MCP?

## 9. Recommended Next Action

Create a follow-up design/implementation RFC or ADR-sized change proposal for the concrete runtime contract with:

- the exact plan schema addition
- result classification rules
- retry/escalation behavior changes
- status/reporting wording
- rollout order across planner, orchestrator, and completion paths

Recommendation: implement the **minimal change set first**, but define it in a way that does not block the **ideal change set** later.
