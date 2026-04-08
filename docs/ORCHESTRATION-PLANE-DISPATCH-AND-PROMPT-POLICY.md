# Orchestration Plane Dispatch and Prompt Policy

**Status:** Proposed  
**Architecture authority:** `docs/ORCHESTRATION-PLANE-ARCHITECTURE.md`  
**Interface authority:** `docs/ORCHESTRATION-PLANE-INTERFACES.md`  
**Related:** `docs/ORCHESTRATION-PLANE-RUNNER-BACKEND.md`, `docs/ORCHESTRATION-PLANE-RUNTIME-WORKTREE-LIFECYCLE.md`, `docs/ORCHESTRATION-PLANE-RESOLUTION-CONTRACT.md`, `docs/RFC-vectl-decide-advisor-refresh.md`

---

## 1. Purpose

This document defines the missing orchestration-plane policy for:

- dispatch payload construction
- role/agent allocation
- prompt assembly
- structured review/gate result normalization
- planner use as a specialized remediation subagent

The goal is to complete the `control -> runtime` gap without overloading either
`control` or `runtime` with prompt policy.

---

## 2. Problem statement

The current codebase has pieces of dispatch information, but not a complete
contract.

Existing fragments include:

- `step.agent`
- `step.description`
- `step.verification`
- `step.refs`
- `default_agent`
- `fallback_role`
- `ControlDecision(kind="dispatch", step_id, role)`

What is missing is a single policy that answers:

1. how a role is finally chosen
2. how step semantics become executable work content
3. where prompt assembly lives
4. how role-specific prompt templates work
5. how review/gate failures become explicit resolution cases

---

## 3. Design goals

The dispatch/prompt system must satisfy all of the following:

1. `control` stays thin and plan-aware only
2. `runtime` stays mechanical and does not own prompt policy
3. role-specific prompt templates are supported
4. role IDs remain open/extensible rather than hardcoded
5. resolver remains a special blocked-case path, not just another ordinary step
6. planner can be used as a specialized remediation subagent
7. review/gate outputs can be consumed mechanically by the orchestration system

---

## 4. Core decision

The orchestration plane should use a **single dispatch contract** plus two small
supporting registries.

The core components are:

1. `ControlDecision` (existing, still thin)
2. `DispatchSpec` (new, unified dispatch contract)
3. `RoleProfileRegistry` (new, role/profile lookup)
4. `PromptRegistry` (new, role-aware prompt rendering)

Resolver remains a separate path, but it may call specialized subagents using
the same underlying role/profile and runner substrate.

---

## 5. Ownership boundary

### `control` owns

- whether to dispatch, resolve, wait, or finish
- the selected step ID
- a role hint / dispatch role

### `control` does not own

- prompt content
- prompt templates
- runner-native payloads
- review normalization semantics

### `runtime` owns

- execution context (`linked_worktree` / `main_worktree`)
- launch, tracking, reconcile, cleanup

### `runtime` does not own

- prompt policy
- role allocation policy
- review semantics

### dispatch layer owns

- building the unified `DispatchSpec`
- resolving role profiles
- rendering prompt bundles

### resolver owns

- blocked/unresolved case coordination
- deciding whether to self-handle, call a specialized subagent, or escalate

---

## 6. Dispatch flow

### 6.1 Normal flow

Normal step dispatch flows as:

1. `control` emits `ControlDecision(kind="dispatch", step_id, role)`
2. dispatch coordinator loads authoritative step data
3. role profile is resolved
4. a `DispatchSpec` is built
5. `PromptRegistry` renders role-specific prompt content
6. runtime executes the resulting dispatch through the shared runner backend

### 6.2 Resolution flow

Resolver does not use ordinary claim-based step dispatch.

Instead:

1. `control` emits `resolve`
2. resolver consumes a `ResolutionCase`
3. resolver may:
   - self-handle
   - invoke a specialized subagent such as planner/coder/reviewer
   - escalate to user/operator

Resolver remains a special blocked-case path, not a normal step-dispatch path.

### 6.3 Dispatch coordinator location

`dispatch coordinator` is not a new top-level orchestration-plane component.

It is an orchestration-app-owned logic layer that sits between `control` and
`runtime`.

Its responsibility is to:

1. load authoritative step data
2. resolve role/profile information
3. build `DispatchSpec`
4. render prompt content
5. construct the runtime-facing execution request/input

Recommended home:

- orchestration-app-owned helper/module (for example under `orch_app.py` or a
  nearby orchestration helper module)

It must not be implemented by pushing prompt logic down into `control` or
`runtime`.

---

## 7. Unified dispatch contract

### 7.1 DispatchSpec

Recommended minimum shape:

```python
@dataclass(frozen=True)
class DispatchSpec:
    source_kind: Literal["step", "resolution_subtask"]
    source_id: str

    role_id: str
    role_source: Literal["step.agent", "default", "fallback", "resolver"]

    execution_context: Literal["linked_worktree", "main_worktree"]
    runner: str
    session_mode: Literal["fresh", "reuse"]
    reuse_token: str | None = None
    reuse_runner: str | None = None

    step_id: str | None = None
    description: str = ""
    verification: str | None = None
    refs: tuple[str, ...] = ()
    evidence_template: str | None = None
    verify_mode: Literal["must_green", "expected_red", "none"] = "none"

    prompt_family: str = ""
    output_contract: str = ""
    mutation_policy: Literal[
        "read_only",
        "worktree_changes",
        "vectl_facade_only",
    ] = "read_only"
```

### 7.2 Why one contract

`DispatchSpec` exists to avoid over-splitting dispatch into several overlapping
objects such as separate intent/allocation/spec/payload types.

It is the single semantic object that bridges:

- control output
- authoritative step semantics
- role/profile policy
- prompt rendering input
- runtime execution input

### 7.3 Relationship to ExecutionRequest

`DispatchSpec` does not replace `ExecutionRequest`.

Instead:

- `DispatchSpec` is the dispatch-layer semantic contract
- `ExecutionRequest` remains the runtime/runner mechanical execution contract

The dispatch coordinator is responsible for converting `DispatchSpec` into the
runtime-facing input needed by `runtime.start()` and the shared runner backend.

In short:

- `DispatchSpec` = what this role should do, under which policy
- `ExecutionRequest` = the mechanical request used to start execution

---

## 8. Role profile system

### 8.1 Open role IDs

Role IDs must remain open strings.

They must not be hardcoded as a closed enum in orchestration-plane core
contracts.

Examples may include:

- `python-executor`
- `python-senior`
- `vectl-planner`
- `gate-reviewer`
- `doc-reviewer`
- `spec-readiness-auditor`
- `conflict-resolver`

but the contract must support future roles without rewriting orchestration-plane
 logic.

### 8.2 RoleProfile

Recommended minimum shape:

```python
@dataclass(frozen=True)
class RoleProfile:
    role_id: str
    prompt_family: str
    template_id: str

    execution_context: Literal["linked_worktree", "main_worktree"]
    mutation_policy: Literal[
        "read_only",
        "worktree_changes",
        "vectl_facade_only",
    ]
    session_policy: Literal["reuse_allowed", "reuse_forbidden"]

    output_contract: Literal[
        "freeform_evidence",
        "structured_review_result",
        "structured_plan_result",
        "resolution_report",
    ]

    default_runner: str
```

### 8.3 RoleProfileRegistry

Recommended minimum protocol:

```python
class RoleProfileRegistry(Protocol):
    def get(self, role_id: str) -> RoleProfile: ...
    def has_role(self, role_id: str) -> bool: ...
```

Rules:

- unknown roles must fail explicitly
- the system must not guess role meaning from string shape alone

### 8.4 Registry configuration source

`RoleProfileRegistry` must be configuration-backed.

Minimum expectation:

- role profiles are loaded from orchestration configuration, not hardcoded as a
  closed internal enum
- the configuration source must be explicit and versioned with the
  orchestration-plane config surface
- adding a new role must be possible by adding a new role profile entry rather
  than rewriting core dispatch logic

Recommended shape at implementation time:

- orchestration config contains role-profile entries
- `RoleProfileRegistry` loads and validates those entries at startup

Related values such as:

- default agent / default role
- fallback role
- role -> runner default

must all be sourced from the same explicit orchestration configuration path or a
clearly referenced adjacent config source.

The system must not rely on hidden in-code defaults once this contract is adopted.

---

## 9. Role allocation policy

### 9.1 Normal flow precedence

For ordinary step dispatch, role allocation follows:

1. `step.agent` if present
2. orchestration default role if `step.agent` is absent
3. fallback role only when explicitly allowed by policy

### 9.2 No silent downgrade of specialized roles

The system must not silently downgrade specialized roles such as planning,
review, or conflict-resolution work into ordinary coder roles.

If a requested role cannot be satisfied under policy, the system should wait,
resolve, or surface an explicit problem rather than silently rewriting intent.

### 9.3 Resolution-flow role source

For resolution subtasks, `DispatchSpec.role_source` is `resolver`.

This marks that the dispatched specialized subagent work was spawned by resolver
handling, not by ordinary step dispatch.

It does not mean resolver itself is redefined as an ordinary role.

---

## 10. Prompt policy

### 10.1 PromptRegistry

Prompt rendering should be centralized behind a single prompt-facing interface.

Recommended protocol:

```python
@dataclass(frozen=True)
class PromptBundle:
    system_prompt: str
    task_prompt: str
    messages: tuple[dict[str, str], ...]


class PromptRegistry(Protocol):
    def render(self, spec: DispatchSpec) -> PromptBundle: ...
    def has_role(self, role_id: str) -> bool: ...
```

### 10.2 Prompt ownership

Prompt policy lives in the orchestration-plane dispatch layer.

It must not live in:

- `control`
- `runtime`
- runner backend
- vectl core

### 10.3 Why centralized prompt rendering

This preserves the architecture:

- `control` remains policy-thin
- `runtime` remains mechanical
- runner backend remains transport/mechanics only

---

## 11. Role-specific prompt families

### 11.1 General rule

Different roles may render from different prompt families.

The system must not assume all roles use the same prompt structure.

### 11.2 Coder family

Typical roles:

- `python-executor`
- `python-senior`

Shared fields:

- description
- verification
- refs
- evidence_template
- execution context

Differences between templates may include:

- scope of exploration
- depth of reasoning
- expected explanation style

### 11.3 Planner family

Typical roles:

- `vectl-planner`
- future remediation/retest planning roles

Planner-family templates should emphasize:

- structured plan output
- remediation/retest planning
- no ordinary step claim semantics

### 11.4 Reviewer family

Typical roles:

- `gate-reviewer`
- `doc-reviewer`
- `spec-readiness-auditor`

Reviewer-family templates must produce structured results that the system can
consume mechanically.

### 11.5 Resolver family

Typical roles:

- `conflict-resolver`
- future specialized resolution roles

Resolver-family templates must reflect:

- main worktree execution context
- no claim of new work
- approved vectl tool facade mutation policy
- explicit escalation when automatic closure is not possible

---

## 12. Execution-context policy

Execution context is not exclusive to resolver.

The system must support at least:

- `linked_worktree`
- `main_worktree`

### 12.1 linked_worktree roles

Typical examples:

- ordinary coder roles
- step-local implementation work

### 12.2 main_worktree roles

Typical examples:

- resolver-family roles
- planner-family roles
- reviewer-family roles

The execution context is determined by `RoleProfile.execution_context`, not by
hardcoding resolver as the only main-worktree actor.

---

## 13. Structured review/gate result contract

Review/gate outputs must not rely on prose-only interpretation.

### 13.1 StructuredReviewResult

Recommended minimum shape:

```python
@dataclass(frozen=True)
class StructuredReviewResult:
    review_outcome: Literal[
        "pass",
        "needs_fix",
        "needs_replan",
        "operator_required",
    ]
    summary: str
    findings: tuple[str, ...] = ()
    evidence_refs: tuple[str, ...] = ()
```

Equivalent serialized form may be JSON or YAML as long as it conforms to this
schema.

```yaml
review_outcome: pass | needs_fix | needs_replan | operator_required
summary: <string>
findings:
  - <string>
evidence_refs:
  - <string>
```

### 13.2 Why structured review output is required

The orchestration system must be able to decide mechanically whether a review
result means:

- continue normal flow
- repair work is needed
- replanning is needed
- user/operator input is required

Prose-only guessing is not acceptable.

### 13.3 Parse failure

If a role declared as `structured_review_result` does not return a parseable
result, the system must treat this as a review/result contract violation rather
than silently guessing from natural language.

The recommended handling is:

- do not complete the reviewed step
- create an explicit resolution case describing the contract violation
- let resolver decide whether to retry, repair, or escalate

### 13.4 Scope note on review/gate terms

In this document, `review` includes gate-style assessment roles whose outputs
must be consumed mechanically by the orchestration system.

---

## 14. Review/gate failure normalization

Review/gate failure is not required to appear as a core blocked-step state.

Instead, the orchestration plane must normalize non-pass structured review
results into explicit resolution cases.

### 14.1 Normalization rule

If `review_outcome` is:

- `pass` -> continue normal flow
- `needs_fix` -> create explicit resolution case
- `needs_replan` -> create explicit resolution case
- `operator_required` -> create explicit resolution case

This keeps `control` simple:

- `control` does not interpret review prose
- `control` only sees that unresolved/resolution work exists and routes to
  `resolve`

---

## 15. Planner as specialized remediation subagent

Planner should not be selected directly by ordinary control flow just because a
review failed.

Instead, planner is typically used as a specialized subagent invoked by resolver
when blocked/unresolved handling requires remediation or retest planning.

### 15.1 Why planner belongs under resolver for review-fail remediation

When review fails, the system often cannot deterministically decide whether the
correct next action is:

- direct fix
- direct retry
- deeper redesign
- repair + retest plan mutation

This is exactly the kind of blocked-case reasoning that belongs to resolver.

### 15.1.1 Planner is canonical, not exclusive

Planner is the canonical remediation subagent for `needs_replan`, but it is not
the only valid resolver delegation target.

Depending on the case, resolver may also invoke:

- coder-family roles for directed repair work
- reviewer-family roles for secondary judgment or verification

These are first-class supported delegation paths under resolver-owned
blocked-case coordination.

### 15.2 Planner output contract

Planner-family remediation output should also be structured.

Recommended minimum shape:

```yaml
plan_outcome: plan_ready | operator_required
summary: <string>
proposed_steps:
  - step_id: <string>
    name: <string>
    description: <string>
    verification: <string>
retest_steps:
  - step_id: <string>
    name: <string>
```

### 15.3 Planner mutation boundary

Planner does not itself own core authority semantics.

If planner output must become authoritative plan mutation, that mutation must be
applied through the approved vectl tool facade under resolver/orchestrator
control.

---

## 16. Relationship to resolver

Resolver is the blocked-case coordinator.

Resolver may:

- inspect the case itself
- self-handle exception repair
- invoke specialized subagents such as planner/coder/reviewer
- surface explicit user/operator escalation

This means planner is not an ordinary replacement for resolver. Planner is one
of the specialized subagents resolver may use.

---

## 17. Relationship to runner backend and runtime

### runtime

Runtime consumes the resulting dispatch material and executes it in the required
execution context.

Runtime does not decide prompt content.

### runner backend

Runner backend receives execution input after dispatch/prompt policy has already
been resolved.

Runner backend does not own business prompt policy.

---

## 18. Anti-patterns

The design must avoid all of the following:

- prompt assembly inside `runtime`
- prompt assembly inside `control`
- role meaning hardcoded as a closed enum in orchestration core contracts
- prose-only review outputs that require guesswork
- treating planner as direct normal-flow fallback for review failure
- building a completely separate execution stack just for resolver-spawned work
- silently downgrading specialized roles into ordinary coder roles

---

## 19. Required implementation surfaces

Likely implementation additions/updates include:

- dispatch coordinator logic in orchestration app
- `DispatchSpec` in orchestration contracts
- role/profile registry surface
- prompt registry surface
- structured review-result parser/normalizer
- planner-subagent invocation path inside resolver handling

Likely code surfaces:

- `src/vectl/orchestration/contracts.py`
- `src/vectl/orchestration/control.py` (minimal changes only)
- `src/vectl/orch_app.py`
- `src/vectl/orchestration/resolver.py`
- new role/prompt policy helpers under `src/vectl/orchestration/`

---

## 20. Verification guidance

Implementation should prove at minimum:

1. role IDs can be extended without changing orchestration-plane core contract shape
2. prompt content differs by role family
3. runtime does not own prompt assembly
4. review/gate outputs are parsed structurally rather than guessed from prose
5. non-pass review output becomes explicit resolution case input
6. resolver can invoke planner as a specialized remediation subagent
7. planner-family work can execute in `main_worktree` context without pretending to be ordinary claimed step work

Suggested test classes:

- role registry tests
- prompt rendering tests by role family
- structured review parsing tests
- review-fail -> resolution case normalization tests
- resolver -> planner delegation tests

---

## 21. Final recommendation

The orchestration plane should solve prompt management and agent dispatch with a
small, explicit design:

- one unified `DispatchSpec`
- one open `RoleProfileRegistry`
- one `PromptRegistry`
- structured review/gate outputs
- planner used as a specialized remediation subagent under resolver, not as a
  direct replacement for ordinary dispatch flow

This keeps:

- `control` thin
- `runtime` mechanical
- role expansion open-ended
- resolver strong enough to coordinate blocked-case remediation

without turning dispatch into an over-layered object forest.
