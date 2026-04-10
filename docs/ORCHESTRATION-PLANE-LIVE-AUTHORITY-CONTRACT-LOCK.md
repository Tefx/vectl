# Orchestration Plane Live Authority Contract Lock

**Status:** Contract lock  
**Architecture authority:** `docs/ORCHESTRATION-PLANE-ARCHITECTURE.md`, `docs/ADR-orchestration-role-agent-prompt-separation.md`  
**Interface authority:** `docs/ORCHESTRATION-PLANE-INTERFACES.md`  
**Related:** `docs/ORCHESTRATION-PLANE-DISPATCH-AND-PROMPT-POLICY.md`, `docs/ORCHESTRATION-PLANE-RESOLUTION-CONTRACT.md`, `docs/ORCHESTRATION-PLANE-RESOLVER-COORDINATION.md`, `docs/ORCHESTRATION-PLANE-ORCH-APP-ROUTING.md`

---

## 1. Purpose

This document locks the live-authority invariants that downstream
implementation must preserve.

These rules exist to prevent a specific failure mode:

> orchestration names the right concepts in docs and types, but runtime behavior
> is quietly driven by flattened prompts, static role maps, or precomputed
> shortcuts that bypass the live dispatch path.

The lock applies to five surfaces only:

- ordinary dispatch
- prompt resolution seam
- roster participation in dispatch decisions
- resolver dynamic mediation
- prompt registry anti-flattening behavior

If an implementation satisfies the type shapes but violates any rule in this
document, it is non-conformant.

---

## 2. Locked invariants summary

| Surface | Locked invariant | Why this exists |
| --- | --- | --- |
| Ordinary dispatch | Dispatch must be built from live authoritative step data and live role/profile lookup at dispatch time | Prevent stale or precompiled dispatch behavior |
| Prompt resolution seam | Prompt rendering happens exactly at the dispatch seam from `DispatchSpec` into `PromptRegistry.render(...)` | Prevent prompt logic from leaking into `control`, `runtime`, or runners |
| Roster participation | `roster` may supply availability and leases, but may not choose step meaning, role meaning, or blocked-state meaning | Prevent hidden control logic in the resource layer |
| Resolver mediation | Resolver may dynamically inspect, repair, delegate, or escalate only after normal flow does not close | Prevent resolver from becoming a shadow controller or a static remediation graph |
| Prompt registry | `agent_id`-specific prompt selection wins over family fallback; family templates must not flatten distinct concrete agent identities | Preserve required runtime differences, especially resolver variants |

---

## 3. Ordinary dispatch live-authority invariant

### 3.1 Required live path

For ordinary step dispatch, the implementation path must remain:

1. `control` returns `ControlDecision(kind="dispatch", step_id, role)`
2. orchestration app performs approved claim in the authoritative surface
3. dispatch coordinator re-reads the authoritative step record for `step_id`
4. dispatch coordinator resolves the live role source (`step.agent`, default role, or explicit control-provided override when the architecture allows it)
5. dispatch coordinator resolves the current `RoleProfile`
6. dispatch coordinator builds `DispatchSpec`
7. `PromptRegistry.render(spec)` renders the prompt bundle
8. runtime receives the runtime-facing execution input derived from that live `DispatchSpec`

Each hop above is required because each hop reasserts an ownership boundary.

### 3.2 Mandatory rule

The executable dispatch payload must be derived from the current authoritative
step state and current role/profile state at the moment dispatch is prepared.

An implementation must not treat earlier planner output, cached prompt text,
startup-time role maps, or ad hoc dispatch blobs as equivalent authority.

### 3.3 Forbidden shortcuts

The following are forbidden for ordinary dispatch:

- storing pre-rendered prompt text on a step and treating it as dispatch authority
- constructing runtime payloads from a static `step_id -> prompt` table
- constructing runtime payloads from a static `role_id -> system_prompt` table
- letting runtime infer `role_id`, `agent_id`, `prompt_family`, or `mutation_policy`
- skipping the step re-read because claim already succeeded
- using stale role-profile config loaded once at process boot as the sole dispatch authority

### 3.4 Required live-path checkpoints

At minimum, implementation must expose or preserve checkpoints for:

- authoritative claim accepted for the exact `step_id`
- authoritative step reread performed after claim and before `DispatchSpec` assembly
- live role/profile resolution performed before prompt render
- `DispatchSpec` assembled before runtime start
- prompt bundle rendered from the live spec, not from runtime-local fallback logic

If any checkpoint cannot be satisfied, dispatch must fail explicitly rather than
silently degrading to a static seam.

---

## 4. Prompt resolution seam lock

### 4.1 Required seam

The prompt resolution seam is:

> `DispatchSpec` -> `PromptRegistry.render(spec)` -> `PromptBundle`

That seam is the only place where orchestration semantics are translated into
prompt content.

### 4.2 Ownership lock

- `control` may decide `dispatch` vs `resolve`; it does not assemble prompt text
- orchestration app / dispatch coordinator may build `DispatchSpec`; it does not hardcode family-specific prompt text inline
- `PromptRegistry` owns prompt selection and rendering
- `runtime` consumes already-resolved prompt input; it does not decide prompt policy
- runner backends transport prompt input; they do not reinterpret orchestration prompt policy

### 4.3 Forbidden static seams

The following are forbidden:

- `control` returning fully rendered prompts
- `runtime` selecting prompts from `role_id`
- runner adapters rewriting prompt choice based on runner-specific defaults
- resolver bypassing `PromptRegistry` when it delegates a subagent task
- embedding prompt-family branching in unrelated modules merely because they already see `role_id`

### 4.4 Implementation consequence

If a new role or persona variant is added, the change point must be role-profile
and prompt-registry configuration/logic at the dispatch seam, not scattered
conditional branches across `control`, `runtime`, and runner code.

---

## 5. Roster participation lock

### 5.1 What roster may contribute

`roster` may contribute only live resource facts and lease outcomes, including:

- compatible runner/session availability
- reusable session identifiers
- exhaustion or unavailability facts
- a `WorkLease` or equivalent resource allocation result

### 5.2 What roster must not decide

`roster` must not:

- decide which step should run next
- reinterpret why a step needs a role
- downgrade a requested specialized role into a more convenient available role
- decide that blocked state is actually ordinary wait or vice versa
- own prompt selection

### 5.3 Required checkpoint

If `roster` participates in dispatch preparation, the checkpoint boundary must
remain visible:

> `control` decides the work intent first; `roster` then answers whether an
> appropriate resource can satisfy that intent.

If no suitable lease exists, the system must surface unavailability explicitly.
It must not rewrite intent to fit supply.

---

## 6. Resolver dynamic mediation boundary

### 6.1 Required entry rule

Resolver is entered only when normal flow does not close.

Resolver must not be used as a convenience dispatcher for ordinary work or as a
default policy router for every review result.

### 6.2 Dynamic mediation rule

Resolver remains dynamic and case-driven.

For each `ResolutionCase`, resolver may choose among the bounded actions already
declared elsewhere in the architecture:

- inspect
- repair through approved authority surfaces
- delegate a specialized subagent
- escalate to operator

That choice must be based on the live case snapshots and live allowed surfaces,
not on a static remediation script baked into normal dispatch logic.

### 6.3 Delegation boundary

If resolver delegates specialized work, that delegated work must re-enter the
same live dispatch seam using `source_kind="resolution_subtask"`.

Resolver must not fabricate a parallel execution stack with separate prompt
selection, separate role resolution, or direct runner-native prompt blobs.

### 6.4 Forbidden shortcuts

The following are forbidden:

- resolver directly claiming ordinary plan work
- resolver mutating authoritative state through file edits or hidden private seams
- resolver spawning ad hoc runner calls that bypass dispatch-spec and prompt-registry resolution
- predeclaring one static remediation path per `case_source` and treating that as sufficient blocked-case reasoning
- treating `operator_required` as ordinary `wait`

### 6.5 Required live-path checkpoints

Implementation must preserve checkpoints for:

- explicit `ResolutionCase` creation
- resolver invocation against current snapshots
- explicit `ResolutionReport` return
- mandatory control-side refresh of authoritative state after resolver returns
- delegated subagent dispatch, if any, flowing through the standard live dispatch seam

---

## 7. Prompt registry anti-flattening lock

### 7.1 Anti-flattening rule

`PromptRegistry` may centralize rendering, but it must not flatten distinct
concrete agent identities into one shared concrete prompt when the role profile
declares a distinct `agent_id`.

### 7.2 Required precedence

Prompt selection precedence is locked as:

1. concrete `agent_id` prompt/persona implementation
2. shared `prompt_family` fallback

Family-level templates are a fallback scaffold. They are not permission to erase
concrete runtime distinctions.

### 7.3 Special lock for resolver defaults

`blocked-case-coordinator` and `blocked-case-coordinator-tacit`:

- share one resolver-family contract
- may share family-level scaffolding
- must remain independently selectable concrete prompt/persona identities

An implementation is non-conformant if both names survive in config while
resolution always renders the same concrete system prompt for both.

### 7.4 Forbidden shortcuts

The following are forbidden:

- one canonical resolver-family system prompt substituted for all resolver variants regardless of `agent_id`
- a flattened `prompt_family -> prompt` cache that bypasses `agent_id` precedence
- renaming two variants in config while wiring both to one concrete persona implementation without an explicit architectural decision
- using prompt-family sameness as a reason to drop required runtime observability of which concrete agent/persona was selected

### 7.5 Required live-path checkpoints

Implementation must preserve evidence for:

- which `agent_id` was selected for dispatch
- whether selection came from concrete `agent_id` or family fallback
- which `prompt_family` scaffold applied, if any
- the distinction between role-profile identity and concrete prompt identity

---

## 8. Runtime surfaces covered by this lock

This contract lock applies to the following runtime-facing surfaces:

- ordinary step dispatch
- resolver-spawned subagent dispatch
- role-profile lookup used during dispatch preparation
- prompt registry render path
- roster lease path used to satisfy an already-chosen work intent
- control refresh after resolution

It does not authorize new runtime surfaces. It only constrains how existing
surfaces must cooperate.

---

## 9. Failure handling rule

When a live-authority checkpoint fails, the system must fail closed.

Accepted examples:

- explicit dispatch failure
- explicit resolution case
- explicit operator escalation

Forbidden example:

- silently substituting a static prompt, static role, or static remediation path
  so the run can continue “best effort”

---

## 10. Conformance checklist for downstream implementation

Downstream implementation is conformant only if all of the following remain
true:

- ordinary dispatch rereads step authority before prompt rendering
- role/profile lookup is live at dispatch time
- prompt rendering occurs only at the dispatch seam
- roster answers supply; it does not rewrite demand
- resolver delegates through the same live dispatch seam when it uses subagents
- control refreshes authoritative state after resolver returns
- prompt registry preserves `agent_id` distinctions and does not flatten them into family-only prompt selection

Any shortcut that breaks one of those checkpoints is an architectural defect,
not a harmless optimization.
