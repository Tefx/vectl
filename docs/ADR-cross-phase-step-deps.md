# ADR: Cross-Phase Step Dependencies

**Status:** Rejected
**Date:** 2026-03-07
**Participants:** se-expert, llm-agent-expert, software-architect

## Context

A user requested cross-phase step-level dependencies. Their scenario:

- Phase A has a gate step (A.gate)
- Phase B's freeze step (B.freeze) should structurally depend on A.gate
- Current workaround: phase-level dependency (B depends_on A) + textual note in step description

Three options were proposed:

1. **Unrestricted cross-phase step deps** — `depends_on` can reference any step in the plan
2. **Explicit gate dependency** — e.g. `depends_on_phase_gate: phase-a`
3. **Auto gate wiring** — if phase B depends on phase A, auto-wire B's first step to A.gate

## Decision

**All three options rejected.** The two-level DAG (phase DAG + phase-scoped step DAG) is a load-bearing design choice that must be preserved.

## Rationale

### 1. `recalc_lock_status` Semantic Conflict (Killer Argument)

Cross-phase step deps create a contradiction: if a step in a LOCKED phase has its cross-phase dep satisfied, either:
- Step-level dep overrides the phase lock → phases lose meaning as boundaries
- Phase lock takes precedence → cross-phase dep is redundant with phase dep

Both answers break a core invariant.

### 2. Two-Level DAG Preserves Local Reasoning

- `derive_step()` is a pure function that only looks within its phase
- Cross-phase deps would require plan-wide index construction on every derive call
- `move_step()` safely clears deps because they are phase-local
- `validate_plan()` runs cycle detection per-phase (simpler, more predictable)

### 3. Agent Execution Reliability

- Cross-phase deps inflate agent context from O(steps-per-phase) to O(steps-across-phases)
- Ambiguous state (step dep satisfied but phase locked) causes unpredictable agent behavior
- Plan complexity is the single biggest determinant of agent execution quality

### 4. The Use Case is a Phase Granularity Problem

"B.freeze depends on A.gate" is solved by splitting phase B into B-prep (no dependency) and B-main (depends on A). The constraint is the feature — limiting plan complexity is a quality guarantee.

## Recommended Actions

1. Add `vectl guide` entry documenting the "split your phases" pattern
2. Add `vectl check` lint rule warning when step descriptions textually reference other phases
3. Enhance claim-time guidance to surface upstream phase gate completion
4. Track recurrence — revisit only if multiple users hit this pattern

## Future Direction (Deferred)

If phase contracts are ever needed, minimal schema change:

```python
class Phase(BaseModel):
    provides: list[str] = Field(default_factory=list)
    requires: list[str] = Field(default_factory=list)
```

Validated at `validate_plan()` time as declarative lint. No runtime DAG impact.

**Do not build until evidence of recurring need.**

## Discussion Transcript

Full multi-agent discussion (10 sayings) is reproduced below.

---

### #0 se-expert

## SE Expert Analysis: Cross-Phase Step Dependencies

I'll apply a structured evaluation from the perspective of layered abstraction design, historical precedent, and complexity budgeting.

### 1. The Abstraction Integrity Argument

vectl's current model has a clean two-level hierarchy: **phases** (coarse scheduling units) and **steps** (fine work units). Step deps are phase-scoped; phase deps are plan-scoped. This is a deliberate layered abstraction -- the same pattern as modules/functions, packages/classes, or Terraform's module/resource scoping.

**Cross-phase step deps break this layering.** Once step B.freeze can reference A.gate directly, you no longer have two clean levels -- you have a single flat DAG with cosmetic grouping. The phase boundary becomes a lie.

### 2. Historical Precedent

| Tool | Approach | Outcome |
|------|----------|---------|
| **Make** | Flat target graph, no scoping | Works for small projects; becomes unreadable at scale |
| **Bazel** | Strict package-scoped deps with explicit visibility | Scales well precisely because cross-package references require explicit export |
| **Terraform** | Module-scoped resources, cross-module via outputs | Forces you to define a clean interface between modules |

The pattern is clear: **tools that scale enforce scope boundaries and require explicit interface points between scopes.** They don't allow arbitrary cross-scope references.

### 3. Complexity Budget Analysis

Cross-phase step deps would impact:

- **DAG validation**: Must now detect cycles across phases, not just within. O(n) becomes O(N) where N = all steps in plan.
- **move_step()**: Currently clears deps because they're phase-scoped. With cross-phase deps, moving a step means either (a) clearing deps that other phases reference (breaking them), or (b) tracking and updating reverse references. Both are expensive.
- **delete/skip operations**: Same reverse-reference problem.
- **Cognitive load on AI agents**: An agent working on phase B now needs to understand the step-level state of phase A. This directly contradicts the purpose of phases as isolation boundaries.

### 4. My Verdict: Do NOT introduce cross-phase step deps (any of the three variants)

**Option 1 (direct cross-phase step deps):** Worst option. Flat DAG with cosmetic grouping. Destroys the abstraction.

**Option 2 (explicit gate dependency):** Better than Option 1, but still a special-case leak across the boundary. Adds a concept (gate) that only exists for cross-phase purposes.

**Option 3 (automatic gate wiring):** Least bad of the three, but introduces implicit behavior that planners can't see or reason about. "Magic" in a planning tool is dangerous.

### 5. What I'd Recommend Instead

The real problem is **phase granularity design**. If B.freeze truly depends on A.gate specifically (not all of phase A), then either:

1. **A.gate should be the last step of phase A** -- so that phase B depending on phase A naturally gates on it. This is a planner discipline issue, not a tool feature issue.

2. **Split phase A** into A1 (up to and including gate) and A2 (post-gate). Then B depends on A1. This keeps the abstraction clean.

3. **Add a "phase contract" concept** -- a phase can declare what it "exports" (e.g., "gate passed"), and downstream phases depend on the contract, not individual steps. This is the Terraform output / Bazel visibility pattern. It's more work but preserves layering.

Option (1) is the cheapest and solves 90% of the stated use case. Option (3) is worth exploring if the pattern recurs frequently.

### Summary

The feature request is a symptom of imprecise phase boundaries, not a missing dependency mechanism. Fixing the tool to allow arbitrary cross-scope references treats the symptom and degrades the architecture. Fix the planning discipline instead, or -- if the pattern is truly common -- add explicit phase "outputs/contracts" as a first-class concept.

---

### #1 llm-agent-expert

## LLM Agent Executor Perspective on Cross-Phase Step Dependencies

I'm coming at this from the perspective of: **how do AI agents actually execute plans, and how does plan complexity affect execution quality?**

### Core Thesis: Simpler Dependency Structures = More Reliable Agent Execution

This is not a soft preference -- it's a direct consequence of how LLM agents process structured plans. Here's why:

### 1. Context Window Pressure is Real

When an agent claims a step, it needs to load enough plan context to understand what it's doing. With phase-scoped dependencies, the agent only needs to reason about its current phase. Cross-phase step refs force the agent to load and reason about steps in other phases -- steps it may never have seen before, written in a planning session it wasn't part of.

**Concrete failure mode:** Agent claims step B.freeze, sees `depends_on: A.gate`. It now needs to understand what A.gate *produced* -- not just that it's done, but what artifacts/state it left behind. This is a context-loading tax that compounds with plan size.

### 2. Agent Error Modes with Cross-Phase Deps

I see three specific failure patterns:

- **Phantom satisfaction**: Agent checks "is A.gate done?" -- yes. But it doesn't understand *why* A.gate matters to B.freeze. The dependency is structurally satisfied but semantically misunderstood. The agent proceeds with wrong assumptions.
- **Over-caution / blocking**: Agent sees a cross-phase dep it can't fully resolve, gets confused, and either asks the user for help (interrupting flow) or skips the step entirely.
- **Drift from plan intent**: The more complex the dependency graph, the more likely the agent substitutes its own interpretation of what should happen next, ignoring the planner's intended sequencing.

### 3. The "Messy Planner" Amplification Problem

This is the maintainer's concern, and it's well-founded from the agent execution side. **Plan quality is the single biggest determinant of agent execution quality.** If you give planners the ability to write cross-phase step deps, some will create tangled dependency webs. The resulting plans will be:

- Harder for agents to reason about (more edges in the mental DAG)
- More likely to produce ambiguous execution orderings
- More fragile when steps need to be moved, skipped, or retried

This isn't hypothetical -- it's a known pattern. Every additional degree of freedom in plan structure is a degree of freedom for the planner to create plans that confuse executors.

### 4. Textual Description vs. Structural Enforcement

The question asks whether "after A.gate passes..." in a description is sufficient vs. structural `depends_on`.

**My take**: For agent executors, textual hints in descriptions are actually *more* flexible and often *sufficient*. Here's why:

- Agents are good at reading natural language context
- The vectl runtime already enforces phase ordering via phase-level `depends_on`
- A description like "Note: this step assumes the gate in phase A has passed" gives the agent semantic understanding without adding structural complexity
- Structural cross-phase deps create a false sense of precision -- the agent still needs to understand the *why*, and structure alone doesn't convey that

### 5. My Recommended Alternative: Phase Gate Checkpoints

Instead of cross-phase step deps, I'd advocate for a simpler concept:

**Phase Gate Pattern**: A convention (possibly with light tooling support) where:
- Each phase can designate a "gate" step (last step, or explicitly marked)
- Phase-level `depends_on` already ensures the gate passed before the next phase unlocks
- The gate step's completion evidence is surfaced to agents entering the dependent phase (this is the key addition)

This gives you:
- **Same semantic power**: "B can't start until A's gate passes" -- already enforced by phase deps
- **Evidence forwarding**: The gate's output/evidence is available to agents in phase B (solves the "what did A.gate produce?" problem)
- **No DAG complexity increase**: No new edge types, no cross-phase resolution logic
- **Agent-friendly**: Agent sees "Phase B (depends on Phase A). Gate evidence from A: [summary]" -- clean, understandable

### Summary Position

I'd vote **against** all three proposed cross-phase step dependency schemes, and **for** enhancing the existing phase-level dependency with better evidence forwarding. The complexity cost of cross-phase step deps is disproportionate to the benefit, especially when measured by agent execution reliability.

The maintainer's instinct ("this will let planners write messy plans") is correct and should be weighted heavily. In agentic systems, **the constraint is the feature** -- limiting plan complexity is a quality guarantee for execution.

---

### #2 se-expert

## Response to @llm-agent-expert

Strong alignment on the verdict. A few points of reinforcement and one area where I'd push further:

### Agreement: "The constraint is the feature"

This is exactly right and maps to a well-known pattern in build systems. Bazel's restrictiveness is its value proposition. The reason Bazel scales where Make doesn't is precisely because Bazel *refuses* to let you do easy things that create long-term maintenance problems. vectl should follow the same principle.

### On Textual Description vs Structural Enforcement

I partially agree but want to add nuance. You're right that natural language hints work well for agent executors. However, there's a risk: **textual dependencies are invisible to the tool's validation engine.** If someone writes "assumes A.gate passed" in a description, vectl can't verify that A.gate actually did pass before unlocking the step. The dependency exists in practice but not in the DAG.

This is exactly why I suggested the "phase contract/output" concept. It sits between "pure text hint" and "full cross-phase step dep":

- Phase A declares: `outputs: [gate-passed]` (a named signal, not a step reference)
- Phase B declares: `requires: [gate-passed]`
- vectl can validate the contract at the phase level without step-level cross-phase wiring

This gives structural validation without breaking the scope boundary.

### On Evidence Forwarding

Your "Phase Gate Checkpoint" idea is interesting and complementary. The key insight is: **the problem isn't dependency ordering (phase deps already handle that), the problem is information transfer across phase boundaries.** If agents in phase B can see what phase A's gate step produced, the semantic connection is maintained without structural coupling.

This is the module output pattern from Terraform -- the module boundary is respected, but downstream modules can consume outputs.

### Where I'd Push Further

One thing neither of us has addressed: **what happens when a planner's phases are genuinely wrong?** If someone keeps needing cross-phase step deps, the right answer isn't "add the feature" OR "use text hints" -- it's "your phases are poorly scoped."

vectl could help here with **planner-time guidance**: if a step's description mentions steps in other phases (detectable via naming patterns), the tool could warn: "This step appears to reference phase A. Consider whether your phase boundaries are correct." Lint, not runtime enforcement.

---

### #3 software-architect

## Architectural Analysis: Cross-Phase Step Dependencies

I have read the vectl codebase (models.py, core.py, semantics.py) to ground this analysis in the actual code. Here is my assessment.

### The Current Two-Level DAG is a Deliberate Architectural Choice

The current design has a clean separation:
- **Phase DAG**: coarse-grained, controls unlock/lock lifecycle via `recalc_lock_status()`
- **Step DAG**: fine-grained, scoped within a phase, controls claimability via `derive_step()`

This is not an accident -- it is a **hierarchical decomposition** that gives phases a key property: **local reasoning**. When an agent is working inside phase B, it only needs to understand phase B's internal step graph. The phase boundary is also an **invariant boundary** -- `move_step()` can safely clear deps because they are phase-local. `validate_plan()` runs cycle detection per-phase, which is simpler and more predictable.

### Impact Assessment of Flattening to One DAG

If step `depends_on` can reference steps in other phases, here is what breaks or gets significantly more complex:

| Operation | Current Complexity | With Cross-Phase Deps |
|-----------|-------------------|----------------------|
| `validate_plan()` | Per-phase cycle detection + phase-level cycle detection | Single global cycle detection across ALL steps |
| `derive_step()` | Looks up deps within `phase.steps` only | Must resolve deps across entire plan -- needs plan-wide step index |
| `move_step()` | Clears deps (safe, they were phase-local) | Must validate that cross-phase deps still make sense after move; cannot simply clear |
| `remove_step()` | Checks dependents within phase | Must scan ALL phases for cross-phase dependents |
| `recalc_lock_status()` | Phase-level granularity | Unclear interaction: if phase B is LOCKED but step B.3 depends on step A.2, what happens when A.2 completes but phase A is not yet DONE? |
| `skip_step()` | Phase-local dependent cascade | Must cascade across phase boundaries |

The last point (`recalc_lock_status`) is the most architecturally dangerous. Today, phases are either locked or unlocked as a unit. Cross-phase step deps create a **semantic conflict**: a step in a locked phase could have its cross-phase dependency satisfied, but the phase itself is still locked. You now need to decide: does the step-level dep override the phase-level lock? If yes, phases are no longer meaningful boundaries. If no, the cross-phase dep is redundant with the phase dep.

### Evaluation of the Three Proposed Options

**Option 1: Unrestricted cross-phase step deps**
- This will lead to "spaghetti plans" where phases lose their meaning as coherent work units.

**Option 2: Explicit `depends_on_phase_gate` syntax**
- Essentially equivalent to what `Phase.depends_on` already does, but at step granularity. The question becomes: what does this give you that `Phase.depends_on` does not?
- Answer: it lets you express "step B.3 needs phase A done, but steps B.1 and B.2 can start before A is done." This is actually a **phase decomposition problem** -- B.1 and B.2 should be in a separate phase or in phase A.

**Option 3: Auto-wire gates**
- Magical behavior that will surprise users. If phase B depends on phase A, B's first step auto-depends on A's gate -- but what is "first step"? This introduces implicit behavior that is hard to debug.

### My Recommendation: Phase Contract Pattern (Alternative)

Rather than cross-phase step deps, I would explore a **phase contract** concept:

```yaml
phases:
- id: A
  provides: [dead-code-triaged]
  steps: [...]

- id: B
  requires: [dead-code-triaged]
  steps: [...]
```

This keeps the two-level DAG intact but makes the **semantic intent** explicit. However, **this might be over-engineering for the stated problem.**

### Simplest Solution: Better Phase Granularity

If B depends_on A at the phase level, then B cannot start until A is DONE. A is DONE only when A.gate (and all other A steps) are complete. So **the phase-level dependency already captures this correctly**.

If the real need is "B.freeze should wait for A.gate but other B steps can start before A finishes" -- that is a signal that B should be split into two phases: B-prep (no dependency on A) and B-freeze (depends on A).

### Summary Position

1. **Do not introduce cross-phase step deps.** The complexity cost is high, and the two-level DAG's local-reasoning property is valuable.
2. **The stated use case is better solved by phase decomposition.**
3. If phase decomposition feels too heavy, the **phase contract pattern** could add semantic clarity without breaking the DAG model, but verify the need is real before building it.
4. The biggest risk is the **semantic conflict with phase locking**.

---

### #4 se-expert

## Response to @software-architect

Excellent code-grounded analysis. Three reactions:

### 1. The recalc_lock_status Conflict is the Killer Argument

This is the point I should have led with. You've identified the fundamental semantic conflict: if a step in a LOCKED phase has its cross-phase dep satisfied, do you override the phase lock? Either answer destroys a core invariant. This alone should close the discussion on Options 1-3.

To restate it as a design principle: **a feature that creates a semantic contradiction between two existing invariants (phase locking and step readiness) is not a feature -- it's a bug in the design.**

### 2. Phase Decomposition is Sufficient

Your analysis of the stated use case confirms what I suspected: `Phase B depends_on Phase A` already captures the ordering. The "need" for cross-phase step deps arises when planners want partial overlap between phases -- and partial overlap means the phases are wrongly scoped.

The cost of splitting phases is low. If splitting "feels too heavy," that's a UX signal (maybe vectl should make creating phases cheaper), not an architecture signal.

### 3. On the Phase Contract Pattern -- Honest Assessment

Both @software-architect and I converged on this idea independently, which is a good signal. But I want to be honest: **it might be premature.** The stated use case (one user, one scenario) doesn't justify adding a new first-class concept. The right sequence is:

1. **Now**: Document "phase decomposition" as the recommended pattern. Maybe add a `vectl guide` entry.
2. **If the pattern recurs**: Track how often planners hit this problem and whether phase splitting solves it.
3. **Only then**: Consider phase contracts as a feature.

This is YAGNI applied to tool design.

### Emerging Consensus

All three of us agree:
- **No to all three proposed options**
- **Phase decomposition** solves the stated use case
- **Phase contracts** are a possible future direction but premature now
- **The two-level DAG is worth preserving** as a design invariant

---

### #5 software-architect

## Response to @se-expert

Strong agreement on all three points. Additional architectural notes:

### On Making Phase Creation Cheaper (UX Signal)

Your observation that "if splitting feels too heavy, that's a UX signal" is exactly right. Looking at the current `add_phase()` in core.py, creating a new phase requires specifying id, name, and optionally gate/context/depends_on. The overhead is minimal at the code level. If users perceive it as heavy, the issue is probably cognitive -- they think of phases as "big things" rather than lightweight grouping. That is a documentation/guidance problem, not an architecture problem.

A concrete recommendation: `vectl guide` could include a pattern like "When to split a phase" with the heuristic: **if you find yourself wanting a step in phase B to depend on a specific step in phase A, that is a signal to split B into B-prep and B-main.**

### On the YAGNI Sequencing for Phase Contracts

I fully endorse the three-step sequence (document pattern now, track recurrence, build if proven). Architectural guardrail: if phase contracts are ever built, they should be **validated at plan-load time** but should NOT affect the runtime DAG. They should remain a declarative lint, not a runtime lock mechanism.

### One Risk to Flag: Phase Proliferation

If we recommend phase decomposition as the solution, we should be aware of the counter-risk: **too many fine-grained phases** can make the plan hard to read at the overview level. The mitigation: document that phases should represent coherent *deliverables* or *milestones*, not individual tasks. If a "phase" has only one step, it probably should not be a phase.

---

### #6 llm-agent-expert

## Reply to @se-expert -- Textual Deps, Phase Contracts, and Planner Lint

### The "Invisible Dependency" Problem -- Fair Critique

You're right that pure textual hints are invisible to validation. I want to refine my position: I'm not arguing that textual descriptions *replace* structural enforcement -- I'm arguing they're sufficient **given that phase-level deps already exist**. The ordering guarantee is structural (phase B waits for phase A). The semantic hint in the description is supplementary context.

But your point stands: if someone deletes A.gate or skips it, the textual hint in B.freeze becomes a lie, and nobody catches it.

### Phase Contracts/Outputs -- This Is the Right Layer

Your `outputs: [gate-passed]` / `requires: [gate-passed]` proposal is compelling from the agent perspective:

1. **Greppable** -- agent can look up what produced it
2. **Validatable** -- vectl can check "was this signal emitted?"
3. **Decoupled** -- agent doesn't need to know *which step* produced it
4. **Stable under refactoring** -- if phase A's internal steps get reorganized, the output contract still holds

From an agent context window perspective: loading a list of phase-level contracts is O(phases), while loading cross-phase step deps is O(steps-across-phases). That's a meaningful difference for large plans.

### Planner Lint -- Strongly Agree, and I'd Go Further

**When an agent struggles with a plan, the problem is almost never "the tool lacks a feature." It's "the plan's structure doesn't match the work's structure."**

I'd push the lint idea further:

- **Claim-time warning**: When an agent claims a step, if vectl detects cross-phase references in the description, emit guidance
- **Plan review check**: A `vectl check` rule that flags cross-phase textual references
- **Planner guidance**: In the architect/planner protocol, explicitly state the phase decomposition pattern

### One New Concern: Evidence Forwarding Implementation

The practical question: **how much evidence is surfaced, and where?** The agent needs a *summary* or *structured output*, not raw logs. This connects to the phase contracts idea -- the contract output should be a compact, named, queryable signal.

---

### #7 se-expert

## Brief Synthesis and Final Points

### On Phase Proliferation (@software-architect)

Good flag. The heuristic "if a phase has only one step, it probably shouldn't be a phase" is exactly right. The practical guideline: **a phase should have 2-7 steps and represent a coherent deliverable.** If splitting a phase to solve a dependency problem produces a 1-step phase, consider whether that step belongs as the last step of the upstream phase instead.

### Actionable Recommendations (my summary)

1. **Reject the feature request** with a clear explanation of why the two-level DAG is load-bearing
2. **Add a `vectl guide` entry** documenting the "split your phases" pattern
3. **Add a `vectl check` lint rule** that warns when step descriptions textually reference other phases (low-cost, high-signal)
4. **Track recurrence** -- if multiple users hit this pattern, revisit
5. **Do not build phase contracts now** -- YAGNI until evidence accumulates

Total implementation cost: documentation + one lint rule. That is the right-sized response to a single-user feature request that the architecture actively resists.

---

### #8 software-architect

## Reply to @llm-agent-expert -- Contracts, Lint, and the Invisible Dependency Gap

### 1. Phase Contracts as Declarative Lint -- Correct Architectural Layer

Today, `derive_step()` in semantics.py does a single-phase lookup. If we added cross-phase deps, this would become a plan-wide index construction on every derive call (or we would need to cache it, adding statefulness to what is currently a pure function). Keeping step deps phase-local preserves the purity of `derive_step()`.

Phase contracts would be validated at a different layer -- during `validate_plan()` or a new `check_contracts()` function. They would never touch the hot path of step derivation. **Runtime locking stays simple; structural validation gets richer.**

### 2. The "Invisible Dependency" Gap -- Real but Bounded

Today, if phase B `depends_on` phase A, and A.gate is skipped, phase A can still reach DONE status. Phase B unlocks. The *semantic* expectation ("gate passed") is violated, but the *structural* constraint (phase ordering) is maintained.

This is actually an argument FOR named contracts: `provides: [gate-passed]` could be tied to a specific step's completion (not skip), making the validation tighter than pure phase-level deps. But again -- this is a future enhancement.

### 3. Planner Lint -- Concrete Impact on Code

Looking at the existing code:

- **`vectl check`** already exists as a validation surface. Adding a "cross-phase textual reference detector" is a natural extension.
- **Claim-time guidance** already exists (`claim_guidance.py`). Adding a heuristic that flags cross-phase references is straightforward.
- **`vectl guide`** already has topic-based content. Adding a "phase decomposition patterns" topic is trivial.

None of these require model changes. They are pure behavioral additions to existing surfaces.

### Architectural Verdict on Phase Contracts (If/When Built)

Minimal schema change:

```python
class Phase(BaseModel):
    provides: list[str] = Field(default_factory=list)
    requires: list[str] = Field(default_factory=list)
```

Validation rule: every `requires` entry must appear in exactly one upstream phase's `provides`. No changes to `recalc_lock_status()`, `derive_step()`, `move_step()`, or any runtime operation. The contracts are compile-time checks, not runtime constraints.

**Not recommending building this now.** Documenting the design so that if the need is proven, the implementation path is clear.

---

### #9 llm-agent-expert

## Final Remarks from the Agent Execution Perspective

### The recalc_lock_status Argument is Decisive

From the agent perspective, this is even worse than it sounds. When an agent encounters contradictory state (step dep satisfied but phase locked), it enters an unpredictable mode. Some agents will halt and ask for help. Others will attempt "creative" workarounds. Neither is acceptable.

**Agents need unambiguous state.** "This step is blocked" or "this step is ready" -- never "it depends on how you interpret the interaction between two subsystems."

### Endorsing @se-expert's Actionable Recommendations

The five-point plan is well-calibrated. From the agent side, I'd especially emphasize items 2 and 3 (guide entry and lint rule).

### One Addition: Claim-Time Guidance

When an agent claims a step in a phase that depends on another phase, `vectl claim` guidance could include a one-line summary of what the upstream phase accomplished:

> "Phase B depends on Phase A (completed). Phase A's gate step verified: [gate step title]."

This is trivially implementable, costs nothing at runtime, and gives agents the cross-phase context they need without any structural changes.

### Consensus Confirmed

All three perspectives converge:

1. **No to cross-phase step deps** (all three variants)
2. **Phase decomposition** as the immediate solution
3. **Documentation + lint** as the right-sized investment
4. **Phase contracts deferred** until evidence of recurring need
5. **The two-level DAG is load-bearing** and should be preserved as a design invariant
