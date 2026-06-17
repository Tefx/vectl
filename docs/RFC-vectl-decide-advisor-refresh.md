# RFC: `vectl_decide` advisor contract refresh

**Status:** Proposed  
**Date:** 2026-04-05  
**Audience:** vectl core maintainers, automation authors, MCP consumers

## 1. Summary

This RFC defines the final contract direction for `vectl_decide`.

`vectl_decide` remains part of `vectl` and remains valid outside any
built-in execution runtime. It is not redefined as a pure plan-theory function
and it is not deprecated in favor of a built-in execution runtime.

Instead, `vectl_decide` is clarified and strengthened as a:

> deterministic automation/dispatch advisor for caller-owned agents.

This RFC keeps the useful parts of the current design:

- plan-aware dispatch advice
- reusable-context advice
- failure memory
- batch completion handling

and fixes the main contract weaknesses:

- ambiguous `task_id` semantics
- missing runner/source provenance for reuse
- implicit/underspecified state ownership
- weak top-level status signaling
- over-strong hardcoded escalation behavior
- overloaded fields that obscure the final contract

## 2. Non-goals

This RFC does **not**:

- make any built-in execution runtime the only valid host for vectl
- remove `vectl_decide`
- redefine core task semantics so that `task_id == session_id`
- require `vectl_decide` to become a pure function library
- force session reuse out of `vectl_decide` immediately

## 3. Project context

### 3.1 vectl is independently usable

`vectl` is a control plane around `plan.yaml` and remains valid even when no
built-in execution runtime is present. Caller-owned automation agents may call
`vectl_decide` directly via MCP or CLI and use it as their deterministic
decision surface.

### 3.2 `vectl_decide` is an advisor, not an authority

`vectl_decide` does not mutate authoritative plan state. It returns advice that
the caller may apply using authority surfaces such as claim/complete/lifecycle
operations.

It may still perform **in-memory advisory simulation** of completion so that
successor steps can become visible within a single decision cycle. That is not
authoritative persistence to `plan.yaml`.

### 3.3 session reuse is an optimization

Session/context reuse is useful and should remain supported, but it must be
presented as an optimization hint rather than as a correctness dependency.

## 4. Current design: what is good, what is broken

### 4.1 Keep

The following current capabilities are worth preserving:

- processing `completed_results` and returning completion actions
- surfacing claim/dispatch advice for claimable plan steps
- simulating in-memory completion so successor tasks can become visible in the
  same advisory cycle
- tracking repeated failures across calls
- advising reuse when parent context is still eligible

### 4.2 Real problems

#### Problem A — `task_id` semantics are overloaded

Today, `task_id` appears in `RunningTask`, `CompletedResult`, and `Action`, but
callers may read it as:

- task/execution identity
- reuse token
- runner resume handle

This ambiguity is the biggest contract problem.

#### Problem B — reuse advice lacks runner/source provenance

The current contract can suggest reuse, but does not reliably state which
runner namespace the reuse token belongs to. This makes cross-runner misuse too
easy.

#### Problem C — state exists, but ownership is underspecified

`DecideState` is real and useful, but the public MCP-facing story is too weak.
Callers need a clearer contract for who owns it, when it is persisted, and how
it is passed back.

#### Problem D — top-level control summary is too weakly shaped

The current `continuation: bool` + `halt_reason: str | None` shape carries real
signal, but it is too weak and too easy to misread. It should evolve into a
structured top-level status model.

#### Problem E — repeated failure memory is useful, hardcoded escalation is too strong

Remembering repeated failures is valuable. Hardcoding a strong `3 fails ->
escalate` policy inside the advisor is too rigid.

## 5. Final direction

## 5.1 Design stance

The final direction is:

1. keep `vectl_decide`
2. keep it usable by caller-owned automation agents
3. preserve reusable-context and failure-memory functionality
4. make state explicit and caller-owned
5. clarify reuse provenance and token semantics
6. strengthen top-level control signaling
7. reduce policy overreach inside the advisor
8. prefer a single clean contract

## 5.2 Caller contract stance

The caller should treat `vectl_decide` as:

- **authority source for advice structure**
- **non-authority for plan mutation**
- **owner of decision logic**
- **non-owner of execution/runtime truth**

That means:

- actions are authoritative advice outputs
- top-level status is authoritative control summary
- reuse metadata is a hint with provenance, not a blind command
- the caller still owns actual dispatching and authority mutations

## 6. Recommended final contract

### 6.1 Token and provenance semantics

#### RunningTask

Keep the concept, but add runner provenance.

Final direction:

- keep `step_id`
- keep `agent`
- keep `task_id`
- add `runner`
- keep `dispatched_at` only if callers actually use it; otherwise reevaluate separately outside this RFC

Meaning:

- `task_id` = caller-visible execution/task identifier
- `runner` = runner namespace/source that owns any reuse semantics attached to that task

Concrete values at rollout:

- `"claude"`
- `"task"`

There is no implicit fallback semantics in the final contract.

This field is intentionally extensible for future runner namespaces.

Runner field contract:

- `runner` is required
- `runner` must be a non-empty string
- allowed values at rollout are `"claude"` and `"task"`
- unknown runner values are invalid input and should be rejected rather than guessed
- future runner values may only be added by an explicit contract revision

#### CompletedResult

Keep the concept, add runner provenance, and maintain status clarity.

Final direction:

- keep `step_id`
- keep `task_id`
- keep `status`
- keep `output_summary`
- add `runner`

#### Action

Keep action-based output, but stop making `task_id` carry reuse semantics.

Final direction:

- keep `action`
- keep dispatch/complete/escalate-style outputs
- remove overloaded reuse semantics from `Action.task_id`
- add `reuse_token`
- add `reuse_runner`
- do not rely on `session="fresh"|"reuse"` as part of the final contract shape

### 6.2 Make state caller-owned

`DecideState` is not a mistake. Hidden or weakly specified ownership is the
mistake.

Final direction:

- callers explicitly hold state
- callers pass state into `vectl_decide`
- callers receive updated state back
- caller-owned state is part of the primary contract, not an optional afterthought

This allows:

- stable failure memory
- stable reuse TTL behavior
- multi-loop isolation
- deterministic caller-owned automation

### 6.2.1 State serialization contract

The caller-owned state contract must be explicit.

Final direction:

- input accepts `state` / `advisor_state` as a JSON-serializable object
- output returns `next_state`
- vectl does **not** persist advisor state automatically
- callers may keep it in memory or persist it externally if they need continuity across process restarts

Minimum state shape should align with existing `DecideState` fields:

- `completion_times`
- `session_registry`
- `failure_counts`

`state=None` is not the intended long-running automation integration mode.

### 6.3 Replace weak continuation signaling with structured top-level status

Do **not** simply delete top-level loop summary. It has real value.

Instead, evolve:

- from: `continuation: bool` + `halt_reason: str | None`
- to: `status` + `reason_code` + optional human-readable `message`

Final top-level status family:

- `dispatch`
- `wait`
- `blocked`
- `done`

Final reason code family:

- `dispatch_available`
- `waiting_on_running`
- `capacity_full`
- `no_executable_steps`
- `repeated_failures`

Legacy-to-final mapping guidance:

| Current value | Target `reason_code` |
|---|---|
| `MAX_PARALLELISM_REACHED` | `capacity_full` |
| `WAITING_ON_RUNNING_SUBAGENTS` | `waiting_on_running` |
| `NO_EXECUTABLE_STEPS` | `no_executable_steps` |

Final direction:

- `status` / `reason_code` / `message` is the primary top-level control summary
- `continuation` / `halt_reason` are removed from the refreshed contract

Status / reason_code mapping:

| `reason_code` | `status` | Meaning |
|---|---|---|
| `dispatch_available` | `dispatch` | There is executable work and capacity is available |
| `waiting_on_running` | `wait` | Work is still in flight and caller should wait |
| `capacity_full` | `wait` | Parallelism limit is reached |
| `no_executable_steps` | `done` | No more executable work is currently available |
| `repeated_failures` | `blocked` | Repeated-failure policy requires attention/escalation handling |

Reason code completeness rule:

- the reason code set in this RFC is complete
- new reason codes may only be added by a future RFC or explicit contract revision
- callers may treat unknown reason codes as contract errors

### 6.4 Keep failure memory, weaken escalation rigidity

Failure memory is useful and should remain.

Final direction:

- preserve `failure_count`
- preserve repeated-failure tracking in state
- allow an action or summary to indicate attention/escalation recommendation
- avoid baking overly rigid operator policy into the advisor contract

- preserve failure memory
- expose active policy clearly
- prefer recommendation-oriented handling over a single rigid built-in escalation interpretation

### 6.5 Keep TTL, but make policy explicit

TTL is not a bug. It exists to limit stale reuse.

What is missing is visibility.

Final direction:

- keep TTL-based reuse eligibility
- expose policy metadata such as `reuse_ttl_s`
- optionally expose remaining TTL on a reuse hint

This keeps reuse as a bounded optimization rather than a hidden rule.

### 6.6 Demote decision_log to optional debug output

`decision_log` is useful for debugging and audit explanation, but callers should
not have to parse prose to act correctly.

Final direction:

- keep `decision_log`
- mark it as debug/explanatory
- keep all machine-relevant signals in structured fields

## 7. Recommended target contract

This section describes the recommended target shape conceptually. It is not a
drop-in implementation diff.

### 7.1 Input

```text
running_tasks:
  - step_id
  - agent
  - task_id
  - runner
  - dispatched_at (retain only if still justified by callers)

completed_results:
  - step_id
  - task_id
  - runner
  - status
  - output_summary

advisor_state:
  - failure memory
  - reuse registry
  - completion timestamps

max_parallelism
```

Canonical input field names:

- the canonical caller-owned state parameter name is `advisor_state`
- implementation-internal shorthand may use `state`, but external contracts should use `advisor_state`

### 7.2 Output

```text
status
reason_code
message?
actions[]
next_state
policy
decision_log?   # optional debug
```

### 7.2.1 Output field definitions

The refreshed contract should treat these fields as having the following shapes:

```text
status:
  one of: dispatch | wait | blocked | done

reason_code:
  one of:
    - dispatch_available
    - waiting_on_running
    - capacity_full
    - no_executable_steps
    - repeated_failures

message:
  optional human-readable summary string

actions:
  list of action payloads

next_state:
  full JSON-serializable replacement object for caller-owned advisor state
  shape:
    - completion_times
    - session_registry
    - failure_counts

policy:
  object with:
    - reuse_ttl_s: integer
    - escalation_threshold: integer

decision_log:
  optional debug/explanatory array
```

`next_state` replacement rule:

- `next_state` is a full replacement object, not a merge patch
- callers should replace their prior advisor state with `next_state`

### 7.3 Policy metadata

`policy` should be explicit rather than implied.

Minimum required fields:

- `reuse_ttl_s`
- `escalation_threshold`

Example:

```json
{
  "reuse_ttl_s": 300,
  "escalation_threshold": 3
}
```

### 7.4 Action design

Final direction:

- action payloads should behave like a discriminated union
- dispatch-like actions should carry work description, refs, verification, and
  reuse hint metadata
- complete-like actions should carry evidence
- escalation/attention-like actions should carry failure context and recommendation

### 7.5 Examples

#### Example: fresh dispatch recommendation

```json
{
  "action": "claim_and_dispatch",
  "step_id": "auth.user-model",
  "agent": "python-executor",
  "task_id": null,
  "reuse_runner": null,
  "reuse_token": null,
  "step_description": "Define the user model.",
  "step_verification": "uv run pytest tests/test_user_model.py",
  "step_refs": ["docs/specs/auth.md"]
}
```

#### Example: reuse recommendation

```json
{
  "action": "claim_and_dispatch",
  "step_id": "auth.session-store",
  "agent": "python-executor",
  "task_id": "exec-42",
  "reuse_runner": "task",
  "reuse_token": "ses-abc123",
  "step_description": "Implement session store wiring.",
  "step_verification": "uv run pytest tests/test_session_store.py",
  "step_refs": ["docs/specs/auth.md"]
}
```

#### Example: top-level status summary

```json
{
  "status": "wait",
  "reason_code": "waiting_on_running",
  "message": "Running work is still in flight.",
  "policy": {
    "reuse_ttl_s": 300,
    "escalation_threshold": 3
  }
}
```

## 8. Required rollout changes

This RFC defines a direct contract cut.

Required rollout changes:

1. add `runner` to `RunningTask`
2. add `runner` to `CompletedResult`
3. replace reuse semantics on `Action.task_id` with `reuse_token`
4. add `reuse_runner`
5. add explicit caller-owned state input/output
6. replace `continuation` / `halt_reason` with `status` / `reason_code` / `message`
7. keep `decision_log` only as debug/explanatory output
8. expose policy metadata such as `reuse_ttl_s` and `escalation_threshold`
9. tighten action payloads toward discriminated-union behavior

Required test updates:

- update decide tests for runner provenance and reuse token semantics
- replace removed continuation/halt_reason assertions with status/reason_code assertions
- add MCP tests for explicit state round-tripping
- update callers that currently consume `Action.task_id` as a reuse handle

## 9. Guidance for automation/dispatch consumers

Consumers should follow these rules:

1. hold advisor state explicitly
2. treat tokens as opaque
3. rely on structured fields, not prose logs
4. treat reuse as an optimization hint, not a correctness dependency
5. use top-level status as control summary, but keep authority mutations in vectl core surfaces
6. do not treat `task_id` as a reuse handle unless the contract explicitly says so

## 10. Impact on built-in execution runtimes

These changes do **not** require built-in execution runtime adoption and do **not**
invalidate standalone vectl usage.

They should generally help runtime integration alignment because they:

- clarify token provenance
- make state ownership more explicit
- strengthen structured control signaling
- reduce contract ambiguity between advice and authority

The main caution is to avoid over-shrinking `vectl_decide` into a weak
action-only helper. It should remain a usable advisor surface with a real
top-level control summary.

## 11. Final recommendations

### Keep

- `vectl_decide` as a standalone deterministic advisor
- session reuse support
- TTL-based reuse eligibility
- repeated-failure memory
- action-oriented outputs

### Change

- add runner/source provenance
- make state caller-owned
- replace weak top-level signaling with structured status
- reduce `task_id` semantic overload
- weaken hardcoded escalation policy
- demote decision logs to debug
- prefer a single clean contract over dual-field overlap

### Avoid

- deleting `vectl_decide`
- forcing it to be a pure function library
- equating `task_id` with `session_id`
- deleting top-level control summary without replacing it

## 12. Prior narrower RFCs

Earlier narrower RFCs around `vectl_decide` token semantics should not remain in
the active documentation set once this RFC is adopted. The contract direction in
this RFC is intended to stand alone and prevent agents from splitting their
interpretation across overlapping decide-related documents.
