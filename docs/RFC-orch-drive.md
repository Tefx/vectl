# RFC: Full Plan-DAG Orchestration Driver with Parallel Frontier Scheduling

**Status:** Draft  
**Date:** 2026-04-14  
**Author(s):** ChatGPT (draft for discussion)  
**Architecture authority:** `docs/ORCHESTRATION-PLANE-ARCHITECTURE.md`  
**Interface authority:** `docs/ORCHESTRATION-PLANE-INTERFACES.md`  
**Implementation authority:** `docs/ORCHESTRATION-PLANE-IMPLEMENTATION-DESIGN.md`

## 1. Problem / Current Situation

vectl currently has strong orchestration primitives, but it does not yet have a
complete plan-DAG orchestrator.

What exists today is valuable but narrower than the intended orchestration
plane:

- `vectl orch run` can launch a real execution for one step.
- `vectl orch resume` / `recover` can operate on a single run.
- worktree preparation, reconcile, merge, recovery, continuity, events, logs,
  artifacts, and case surfaces all exist.
- the repository now has real OpenCode runner integration and real live smoke
  coverage for single-run execution.

However, the repository still lacks the behavior that users naturally mean by
"orchestrate a plan":

1. `orch run` does not drain the DAG. It launches one step and returns.
2. when multiple claimable steps exist, `orch run` rejects with ambiguity
   instead of scheduling the frontier.
3. same-plan active runs are explicitly blocked by admission policy, so native
   same-plan parallel scheduling is not possible.
4. non-closure currently creates a case and ends the run instead of entering a
   scheduler-owned resolver loop.
5. `needs_replan` is not yet a scheduler-owned planner loop; today the plan can
   be mutated only by explicit external vectl commands.
6. phase and plan closure are not guaranteed to auto-close purely from the
   orchestration loop.

This creates a structural mismatch between the architecture language used in the
orchestration docs and the behavior users actually experience.

Today, the most accurate description is:

> orch is a DAG-aware single-run operator with real runtime primitives, not yet
> a full automatic plan-DAG scheduler.

That gap is no longer acceptable if orch is expected to be the authoritative
execution surface for real repositories.

## 2. Design Goal

Implement a complete orchestration driver that can:

- own a full plan-level orchestration session
- continuously reevaluate authoritative state until the plan reaches a terminal
  status
- schedule ready DAG frontier steps automatically
- support bounded same-plan parallel execution
- use real worktrees and real reconcile/merge for every execution
- invoke resolver automatically when normal flow does not close safely
- invoke planner automatically when replan is required
- preserve recovery, observability, and operator control semantics under
  parallel execution
- auto-close phases and the plan when authoritative step state proves closure

When complete, the following statement must become true:

> `vectl orch drive` can fully orchestrate a real plan DAG, including parallel
> frontier execution, resolver escalation, planner-driven replan, recovery, and
> final plan closure, using real OpenCode runs.

## 3. Non-goals

This RFC does **not** propose:

- multiple independent orchestration drivers concurrently controlling the same
  `plan.yaml`
- distributed multi-host scheduling for one plan
- speculative branch execution outside authoritative DAG readiness
- heuristic optimization layers such as dynamic cost-based scheduling,
  preemption, or fairness weighting beyond deterministic stable ordering
- replacing `vectl core` authority with orchestration-owned lifecycle state
- allowing planner or resolver to edit `plan.yaml` directly
- making mock/shim runner tests sufficient for completion of orchestration
  functionality

## 4. Why the Naive Alternatives Are Not Good Enough

### 4.1 "Just make `orch run` auto-pick one step"

This only removes one ambiguity error. It still does not:

- drain the DAG
- support parallel frontier scheduling
- resolve non-closure automatically
- integrate planner into the execution loop
- define multi-run operator semantics

### 4.2 "Use an external shell loop around `vectl next` + `vectl orch run`"

This fails because the external loop cannot become the authoritative scheduler
for:

- same-plan parallel admission
- drive-level recovery
- barrier state
- resolver/planner continuation
- multi-run control semantics
- drive-level observability and audit

It would recreate the scheduler outside the product boundary and guarantee more
drift later.

### 4.3 "Allow many independent `orch run`s against one plan"

This destroys authoritative scheduling. Multiple independent runners would race
on:

- claim/frontier ownership
- plan mutation timing
- recovery interpretation
- operator control targeting
- `--latest` selector meaning

The result would be nondeterministic and unauditable.

## 5. Core Decisions

### 5.1 Keep `orch run` as a single-run primitive; add `orch drive`

`vectl orch run` remains the explicit primitive for launching one standalone
execution. A new `vectl orch drive` command becomes the authoritative full-plan
orchestrator.

Why:

- it preserves the existing single-run mental model and scripts
- it prevents command overloading where a "run" sometimes means one step and
  sometimes means an entire scheduler session
- it gives the full scheduler its own durable state model and operator surface

### 5.2 Introduce a parent/child execution model

The orchestration layer gains two execution scopes:

- **Drive**: a plan-level orchestration session
- **Child run**: one concrete execution launched by the drive for a step,
  resolver subtask, or planner subtask

Drive state owns:

- frontier scheduling
- parallelism limits
- barrier state
- blocked/open case state
- recovery cursor
- aggregate observability

Child run state owns:

- workspace/worktree
- runner session
- per-execution logs/artifacts
- reconcile result

This does **not** create a fifth top-level architecture component. `drive` is a
durable orchestration session and composition layer that coordinates `control`,
`roster`, `runtime`, and `resolver`. Plan-aware decision authority remains with
`control`.

### 5.3 One active drive per plan; multiple active child runs per drive

For a single `plan.yaml`:

- there may be at most one active drive
- that drive may own many active child runs, subject to capacity limits

This preserves one authoritative scheduler while allowing same-plan parallel
execution inside that scheduler.

### 5.4 Use barrier semantics for exceptional flow

Normal scheduling may dispatch in parallel. Exceptional flow enters a barrier.

Barrier entry conditions include:

- runtime failure, stall, or transport error
- reconcile non-closure
- structured review non-pass
- planner-needed state
- recovery gate block
- operator pause

Barrier behavior:

1. stop new frontier admission
2. allow active child runs to reach a stable terminal set
3. run resolver/planner/operator transition logic on a stable snapshot
4. reopen normal scheduling only after the barrier resolves safely

### 5.5 Resolver is part of the scheduler loop

A non-closure must not simply create a case and end orchestration. The driver
must be able to invoke resolver, consume a machine-readable `ResolutionReport`,
refresh state, and continue when the report proves continuation is safe.

### 5.6 Planner is part of the scheduler loop

`needs_replan` must become a scheduler-owned path:

1. enter replan barrier
2. invoke planner child run
3. parse machine-readable mutation bundle
4. apply mutations through vectl facade only
5. refresh authoritative state
6. continue drive execution

### 5.7 Operator surfaces default to drive scope

All top-level orch inspection and control commands must default to the drive,
not to an arbitrary child run. Child-run drill-down remains available, but it
must be explicit.

### 5.8 Real OpenCode E2E is the authoritative acceptance layer

The orchestration feature is not complete until its critical behaviors are
proven by real OpenCode execution, not only by fake/shim runner tests.

## 6. Functional Scope

### In scope

- drive command surface and persistence
- batch frontier dispatch
- same-plan child-run parallelism
- barrier lifecycle
- automatic resolver continuation
- automatic planner continuation
- multi-run drive-level observability and control
- phase/plan auto-close
- full real OpenCode acceptance coverage

### Out of scope

- cross-host distributed scheduling
- preemptive scheduling or time slicing
- cost-aware scheduling heuristics
- queueing across multiple different plans
- planner/resolver model selection optimization

## 7. Command Model

### 7.1 Existing single-run primitives remain

```bash
vectl orch run [STEP_ID] [--agent ROLE] [--json]
vectl orch resume [RUN_ID|--latest] [--json]
vectl orch recover [RUN_ID|--latest] [--dry-run|--yes] [--json]
```

These remain valid only for standalone single-run workflows. If an active drive
exists for the same plan, public single-run mutating commands must reject with a
clear error explaining that drive ownership is authoritative.

Required rejection contract:

- exit code: `2`
- no `--force` override is permitted
- error text must include the active `drive_id`
- error text must instruct the operator to use a drive-scoped command instead

### 7.2 New drive commands

```bash
vectl orch drive [--agent ROLE] [--max-parallelism N] [--json]
vectl orch drive-status [DRIVE_ID|--latest] [--json]
vectl orch drive-runs [DRIVE_ID|--latest] [--json]
vectl orch drive-resume [DRIVE_ID|--latest] [--json]
vectl orch drive-recover [DRIVE_ID|--latest] [--dry-run|--yes] [--json]
```

`--max-parallelism` contract:

- default: `4`
- minimum: `1`
- hard maximum: `32`
- invalid values must fail fast with exit code `2`

Rationale: the default must be large enough to exercise real DAG frontier
parallelism while remaining small enough to avoid accidental workstation
resource exhaustion during routine use.

For CLI ergonomics, existing inspection/control commands may continue to live
under `vectl orch`, but their default object becomes the drive.

### 7.3 Drive-scoped control

```bash
vectl orch status [DRIVE_ID|--latest] [--json]
vectl orch events [DRIVE_ID|--latest] [--json]
vectl orch logs [DRIVE_ID|--latest] [--json]
vectl orch artifacts [DRIVE_ID|--latest] [--json]
vectl orch actions [DRIVE_ID|--latest] [--json]

vectl orch pause [DRIVE_ID|--latest] [--reason TEXT]
vectl orch unpause [DRIVE_ID|--latest]
vectl orch stop [DRIVE_ID|--latest] [--reason TEXT] [--force]
```

### 7.3.1 `stop --force` semantics

`stop --force` is stronger than graceful drive stop.

Required behavior:

- request cancellation of all active **step** child runs through the runner
  cancellation surface where supported
- preserve child-run artifacts even when cancellation is requested
- do **not** abruptly kill resolver or planner child runs; allow them to reach a
  terminal report so the operator has audit evidence
- if a runner does not support cancellation, classify the affected child run as
  `stall` after stabilization and transition the drive through the normal barrier
  path
- once all active child runs are terminal or classified, transition the drive to
  `stopped`

### 7.4 Child-run drill-down

The following selectors must be added for explicit child-run inspection:

```bash
--child-run-id <RUN_ID>
```

Example:

```bash
vectl orch logs --child-run-id 01ABC... --json
```

Selector composition rules:

- `--child-run-id` is exclusive with drive selectors such as positional
  `DRIVE_ID` or `--latest`
- a child-run selector that does not belong to the selected drive must fail with
  exit code `2`
- child-run inspection never changes drive latest-selection rules

### 7.5 `--latest` semantics

`--latest` must be deterministic and drive-aware:

- by default, select the most recently updated drive for the plan
- if the caller asks for a child run, require explicit child selector
- do not silently redirect drive-level queries to an arbitrary child run

## 8. Data Model

### 8.1 `DriveRecord`

`DriveRecord` is the durable parent orchestration state.

Required fields:

- `drive_id`
- `plan_path`
- `status`
- `started_at`
- `updated_at`
- `finished_at | None`
- `agent`
- `max_parallelism`
- `active_child_run_ids`
- `frontier_step_ids`
- `blocked_case_ids`
- `barrier | None`
- `operator_pause_state`
- `summary`

Persistence rule:

- `DriveRecord` embeds `DriveBarrier | None` as the authoritative barrier field
- barrier subfields must not be duplicated as top-level sibling fields on the
  drive record

### 8.2 `DriveStatus`

Allowed values:

- `running`
- `paused`
- `resolving`
- `replanning`
- `blocked_operator`
- `recovering`
- `completed`
- `halted`
- `failed_unrecoverable`
- `stopped`

### 8.2.1 Valid `DriveStatus` transitions

`active drive` is defined as any drive whose status is not one of:

- `completed`
- `halted`
- `failed_unrecoverable`
- `stopped`

Valid transitions:

| From | Trigger | To |
|---|---|---|
| `running` | operator pause | `paused` |
| `running` | barrier entered for non-closure | `resolving` |
| `running` | barrier entered for replan | `replanning` |
| `running` | recovery requested | `recovering` |
| `running` | all phases closed | `completed` |
| `running` | hard halt decision | `halted` |
| `running` | unrecoverable invariant break | `failed_unrecoverable` |
| `running` | operator stop | `stopped` |
| `paused` | operator unpause | `running` |
| `paused` | operator stop | `stopped` |
| `paused` | recovery requested | `recovering` |
| `resolving` | resolver returns `unblocked` and barrier clears | `running` |
| `resolving` | resolver returns `waiting` | `resolving` |
| `resolving` | resolver returns `operator_required` | `blocked_operator` |
| `resolving` | resolver returns `halt` | `halted` |
| `resolving` | resolver requests planner | `replanning` |
| `replanning` | planner bundle applied and barrier clears | `running` |
| `replanning` | planner requests operator intervention | `blocked_operator` |
| `replanning` | planner emits halt-worthy fatal result | `halted` |
| `replanning` | planner/adapter invariant break | `failed_unrecoverable` |
| `blocked_operator` | operator resumes with barrier cleared | `running` |
| `blocked_operator` | operator stop | `stopped` |
| `blocked_operator` | recovery requested | `recovering` |
| `recovering` | state restored and no barrier remains | `running` |
| `recovering` | restored state requires resolver | `resolving` |
| `recovering` | restored state requires planner | `replanning` |
| `recovering` | operator attention required | `blocked_operator` |
| `recovering` | unrecoverable corruption detected | `failed_unrecoverable` |

Any transition not listed above is invalid and **MUST raise**.

### 8.3 `ChildRunRef`

Every child run must persist:

- `run_id`
- `drive_id`
- `kind`: `step | resolver | planner`
- `step_id | case_id | planner_request_id`
- `status`
- `workspace`
- `runner`
- `session_id`
- `artifact_root`

Allowed `status` values:

- `pending`
- `running`
- `success`
- `fail`
- `stall`
- `transport_error`
- `cancelled`

### 8.4 `DriveBarrier`

Barrier must capture:

- `reason`
- `entered_at`
- `case_ids`
- `pending_resolver_run_id`
- `pending_planner_run_id`
- `active_child_run_ids_at_entry`

### 8.4.1 Barrier reason mapping

| Entry condition | `BarrierReason` |
|---|---|
| child run `fail`, `stall`, `transport_error` | `runtime_failure` |
| reconcile status `merge_conflict` | `merge_conflict` |
| reconcile status `aborted` | `runtime_failure` |
| structured review non-pass or parse failure | `review_failed` |
| resolver or review requests planner mutation | `planner_needed` |
| recovery gate blocks dispatch or complete | `recovery_gate` |
| operator pause | `operator_pause` |

### 8.5 `PlannerMutationBundle`

Planner output must be machine-readable and limited to vectl-supported facade
mutations.

Required fields:

- `status`
- `summary`
- `mutations[]`
- `affected_steps[]`
- `evidence_refs[]`
- `safety_notes[]`

### 8.5.1 `PlannerMutationBundle.mutations[]` item schema

Every mutation item must map one-to-one to an approved vectl facade mutation.

```json
{
  "action": "add-step | edit-step | remove-step | move-step | add-phase | edit-phase | skip-step | complete-phase",
  "arguments": {"...": "action-specific payload"},
  "reason": "why this mutation is required",
  "safety_notes": ["bounded caveats or follow-up constraints"]
}
```

Supported actions and required `arguments` keys:

| action | Required `arguments` keys |
|---|---|
| `add-step` | `phase_id`, `step_id`, `name`, `description` |
| `edit-step` | `step_id`, `changes` |
| `remove-step` | `step_id` |
| `move-step` | `step_id`, `target_phase` |
| `add-phase` | `phase_id`, `name` |
| `edit-phase` | `phase_id`, `changes` |
| `skip-step` | `step_id`, `reason` |
| `complete-phase` | `phase_id`, `reason` |

`changes` is a partial object restricted to fields supported by the approved
vectl facade for that entity. Planner output must not fabricate raw YAML patch
operations.

Allowed `changes` keys:

| mutation | Allowed `changes` keys |
|---|---|
| `edit-step` | `name`, `description`, `depends_on`, `refs`, `verification`, `agent` |
| `edit-phase` | `name`, `depends_on`, `context` |

Example:

```json
{
  "status": "applyable",
  "summary": "Insert a fix step before the blocked verification step.",
  "mutations": [
    {
      "action": "add-step",
      "arguments": {
        "phase_id": "core",
        "step_id": "core.fix",
        "name": "Repair generated artifact",
        "description": "Rewrite artifact.txt with VERSION=2 and commit it."
      },
      "reason": "The current frontier needs an intermediate repair before verification can pass.",
      "safety_notes": [
        "Do not delete existing completed steps.",
        "Keep the new step directly before core.verify."
      ]
    },
    {
      "action": "edit-step",
      "arguments": {
        "step_id": "core.verify",
        "changes": {
          "depends_on": ["core.fix"]
        }
      },
      "reason": "Verification must wait for the new repair step.",
      "safety_notes": []
    }
  ],
  "affected_steps": ["core.fix", "core.verify"],
  "evidence_refs": ["planner://analysis"],
  "safety_notes": ["Apply through vectl facade only."]
}
```

### 8.6 Concrete JSON examples

#### 8.6.1 `DriveRecord`

```json
{
  "drive_id": "drv_01K...",
  "plan_path": "/repo/plan.yaml",
  "status": "running",
  "started_at": 1776124800.0,
  "updated_at": 1776124812.0,
  "finished_at": null,
  "agent": "python-executor",
  "max_parallelism": 4,
  "active_child_run_ids": ["run_01A", "run_01B"],
  "frontier_step_ids": ["core.verify", "core.snapshot"],
  "blocked_case_ids": [],
  "barrier": null,
  "operator_pause_state": "active",
  "summary": "2 child runs active; frontier width=2"
}
```

#### 8.6.2 `ChildRunRef`

```json
{
  "run_id": "run_01A",
  "drive_id": "drv_01K...",
  "kind": "step",
  "step_id": "core.verify",
  "status": "running",
  "workspace": ".vectl/workspaces/core.verify",
  "runner": "opencode",
  "session_id": "ses_01ABC",
  "artifact_root": ".vectl/runs/run_01A"
}
```

#### 8.6.3 `DriveBarrier`

```json
{
  "reason": "merge_conflict",
  "entered_at": 1776124820.0,
  "case_ids": ["case_01M"],
  "pending_resolver_run_id": "run_resolve_01",
  "pending_planner_run_id": null,
  "active_child_run_ids_at_entry": ["run_01B"]
}
```

#### 8.6.4 `ResolutionReport` with `planner_request`

```json
{
  "status": "unblocked",
  "summary": "Root cause identified: step core.verify requires a prerequisite fix that does not exist in the current plan.",
  "evidence_refs": ["artifact://resolver/analysis.json"],
  "operator_message": null,
  "planner_request": {
    "reason": "Step core.verify needs a new prerequisite step inserted before it in phase core.",
    "affected_steps": ["core.verify"],
    "evidence_refs": ["artifact://review/gate_output.json"],
    "constraints": ["Preserve completed steps", "Do not modify phase ordering outside core"]
  }
}
```

## 9. Scheduler Decision Model

### 9.1 Input surfaces

Every scheduler decision must read:

- authoritative core snapshot
- roster snapshot
- runtime snapshot
- active drive state
- open case state
- recovery gate state
- operator pause state

### 9.2 Decision kinds

The control layer must emit one of:

- `dispatch_batch`
- `wait`
- `resolve`
- `replan`
- `done`
- `halt`

### 9.2.1 Full `ControlDecision` contract

The target decision shape supersedes the current single-step `dispatch` model.

```python
@dataclass(frozen=True)
class ControlDecision:
    kind: Literal["dispatch_batch", "resolve", "replan", "wait", "done", "halt"]
    reason: str
    step_ids: tuple[str, ...] = ()
    role_bindings: dict[str, str] = field(default_factory=dict)  # step_id -> role_id
    case_ids: tuple[str, ...] = ()
    planner_request: PlannerRequest | None = None
    capacity_used: int = 0
    capacity_remaining: int = 0
    barrier_required: bool = False
```

Decision invariants:

- `dispatch_batch`: `step_ids` must be non-empty and `role_bindings` must cover
  every `step_id`
- `resolve`: `case_ids` must be non-empty
- `replan`: `planner_request` must be present
- `wait`: `step_ids` and `case_ids` must be empty
- `done`: no frontier and no active child runs remain
- `halt`: barrier is terminal and no new work may be admitted

Migration rule:

- the existing implementation-local `dispatch` kind is a transitional alias for
  one-element `dispatch_batch`
- the target interface authority is the expanded form above

### 9.3 `dispatch_batch`

This decision must contain:

- `step_ids[]`
- `role_bindings`
- `capacity_used`
- `capacity_remaining`
- deterministic frontier ordering proof

### 9.4 Deterministic frontier ordering

Ready steps must be ordered deterministically:

1. authoritative claimable frontier only
2. stable phase-local ordering by `step_id`
3. truncated by available capacity

This RFC deliberately rejects hidden priority heuristics. If priority is needed
later, it must become explicit plan/config data and receive its own RFC.

### 9.5 `vectl_decide` integration and deprecation plan

`vectl_decide` remains useful as a deterministic decision helper during the
migration, but `PlanAwareControl` becomes the authoritative orchestration
decision surface for drive scheduling.

Required migration policy:

1. existing `vectl_decide` outputs may be adapted internally while drive support
   lands
2. `PlanAwareControl` + `ControlDecision` become the public contract authority
3. once parity is achieved, any old decide-only scheduler path must be removed or
   explicitly marked deprecated

No parallel long-term decision stacks are allowed.

## 10. Driver Loop

### 10.1 High-level loop

The drive loop must behave as follows:

```text
load or create active drive
while drive not terminal:
  refresh snapshots
  evaluate control
  if dispatch_batch:
    admit child runs up to capacity
    start them
  collect terminal child runs
  route terminal outputs through reconcile/review gates
  if any non-closure exists:
    enter barrier
  if barrier active:
    stabilize children
    resolve via resolver/planner/operator policy
  auto-close completed phases
  if all phases complete:
    mark plan and drive complete
persist final drive state
```

The drive loop must always run from the repository root for integration-context
checks, even when individual child runs execute in separate worktrees.

### 10.2 Capacity model

For normal step runs:

```text
capacity_remaining = max_parallelism - active_step_child_runs
```

`active_step_child_runs` is a computed projection over active child runs where
`ChildRunRef.kind == "step"`. Resolver and planner child runs are excluded from
that count.

Resolver and planner child runs do not participate in normal frontier capacity.
They are barrier work, not ordinary plan work.

Additional barrier-work limits:

- at most one resolver child run may be active for a drive at any time
- at most one planner child run may be active for a drive at any time

### 10.3 Drive terminal states

Drive may terminate only in:

- `completed`
- `blocked_operator`
- `halted`
- `failed_unrecoverable`
- `stopped`

`running`, `paused`, `resolving`, `replanning`, and `recovering` are explicitly
non-terminal.

### 10.4 Timeout defaults

Unless explicitly overridden by configuration:

- drive collect poll interval: `0.25s`
- resolver child run timeout: `300s`
- planner child run timeout: `300s`
- barrier child-run stabilization wait: `120s`
- child-run heartbeat stale threshold: `90s`

If these thresholds are exceeded:

- the driver must emit a barrier event
- classify the affected child run as non-healthy for scheduling purposes
- transition through resolver, recovery, or operator policy rather than waiting
  forever

Barrier stabilization hard-stop rule:

- if barrier stabilization exceeds the wait threshold and one or more child runs
  are still non-terminal, the driver must classify those child runs as `stall`,
  preserve their artifacts, emit `drive_barrier_entered` with a timeout note,
  and transition the drive to `blocked_operator`

## 11. Runtime and Worktree Model

### 11.1 Every child run uses a real worktree/workspace

No execution in the drive may use a fake or non-worktree execution path when the
step isolation policy requires real worktree semantics.

### 11.2 Parallel workspace isolation

Each child run must have:

- isolated workspace path
- isolated artifact directory
- isolated log stream
- isolated reconcile state

### 11.3 Reconcile ownership

Reconcile is per child run, but the drive owns the aggregate scheduling effect:

- a child run may complete reconcile independently
- a failed reconcile enters drive barrier
- the drive decides whether scheduling may continue

### 11.4 Batch dispatch runtime integration

`dispatch_batch` does **not** require a separate `Runtime.start_batch()` API.
The authoritative contract is:

1. order `step_ids` deterministically
2. for each step in order, call `runtime.prepare()` then `runtime.start()`
3. `runtime.start()` returns the child-run execution identifier, and that value
   becomes the authoritative `ChildRunRef.run_id`
4. each successful start creates a durable child-run record immediately
5. if a later start fails, already-started child runs remain valid and continue
6. no further starts from that batch may be attempted after the first start
   failure
7. the drive enters barrier handling for the failed start before reopening
   frontier dispatch
8. steps that were selected for the batch but not successfully started return to
   the frontier pool after the barrier resolves

This preserves simple runtime mechanics while still giving the scheduler batch
semantics.

## 12. Resolver Semantics

### 12.1 Resolver is entered by barrier policy

If a step or reconcile path does not close safely, the driver must create a
`ResolutionCase` and enter barrier mode.

`case_id` must use the normal vectl case identifier convention (`case_` prefix
with a globally unique suffix) so that case artifacts, events, and operator
responses can correlate deterministically.

### 12.2 Resolver output contract

Resolver returns a bounded `ResolutionReport`:

- `unblocked`
- `waiting`
- `operator_required`
- `halt`

Optional extension field:

- `planner_request`

`planner_request` schema:

```json
{
  "reason": "why replanning is required",
  "affected_steps": ["core.verify"],
  "evidence_refs": ["artifact://review"],
  "constraints": ["Preserve completed steps", "Do not widen scope outside phase core"]
}
```

If `planner_request` is present, `status` may still be `unblocked` or
`operator_required`, but drive policy must prefer the replanning transition.

### 12.3 Resolver continuation rules

- `unblocked`: refresh state, clear barrier, continue normal scheduling
- `waiting`: stay in resolving/waiting barrier; do not dispatch new work
- `operator_required`: drive enters `blocked_operator`
- `halt`: drive enters `halted`
- `planner_request` present: transition from resolving to replanning barrier

If `planner_request` is present alongside status `unblocked`, the drive must
transition directly from `resolving` to `replanning` without reopening the
frontier in between.

## 13. Planner Semantics

### 13.1 Planner trigger sources

Planner may be triggered by:

- review outcome `needs_replan`
- resolver `planner_request`

### 13.2 Planner input

Planner child run must receive:

- current plan path
- affected step ids
- current frontier
- causal evidence
- barrier reason
- relevant case ids

### 13.3 Planner output

Planner output must be strict JSON matching `PlannerMutationBundle`.

It must not rely on free-form prose to describe mutations.

### 13.4 Planner apply rule

All mutations must be applied through approved vectl facade surfaces only.

Direct `plan.yaml` edits remain forbidden.

Approved facade surface allowlist:

- `add-step`
- `edit-step`
- `remove-step`
- `move-step`
- `add-phase`
- `edit-phase`
- `skip-step`
- `complete-phase`
- `repair claims` (only when explicitly emitted as a follow-up maintenance action)

### 13.5 Planner continuation rules

- valid applyable bundle: apply, refresh, reopen scheduling
- invalid bundle: enter `blocked_operator` with planner failure case
- facade apply failure: enter `blocked_operator` or `failed_unrecoverable`

Status-to-transition mapping:

| `PlannerMutationBundle.status` | Drive transition |
|---|---|
| `applyable` | attempt facade apply; on success return to `running` |
| `operator_required` | transition to `blocked_operator` |
| `halt` | transition to `halted` |

Facade apply disposition rule:

- ordinary facade apply failure enters `blocked_operator`
- only corruption-level or invariant-breaking apply failure may enter
  `failed_unrecoverable`

## 14. Admission and Lease Policy

### 14.1 Same-plan drive admission

At most one active drive may exist for one plan identity.

Plan identity is the canonical realpath-resolved absolute path to `plan.yaml`.

Terminal-drive creation policy:

- a new drive **may** be created for a plan after the prior drive reaches a
  terminal status
- terminal drives remain immutable orchestration history
- same-plan admission checks only active drives, not historical terminal drives
- `orch drive` therefore means: resolve the active drive if one exists,
  otherwise create a new drive

### 14.2 Child-run admission

Within an active drive, multiple child runs may be admitted if:

- their steps are in the authoritative ready frontier
- capacity remains
- no active barrier forbids dispatch
- no lease conflict exists

### 14.3 Lease ownership

Claims/leases must be scheduler-owned and child-run-addressable.

The logical owner is:

```text
drive_id + step_id + run_id
```

### 14.4 Mutation invalidation

Planner mutation may invalidate unstarted leases. If a mutation supersedes a
ready step that has not yet started, the lease must be released before frontier
reopens.

## 15. Recovery and Resume Semantics

### 15.1 Drive resume

Drive resume must restore:

- active child run set
- barrier state
- operator pause state
- resolver/planner pending state
- aggregate progress summary

### 15.2 Drive recovery

Drive recovery must support:

- interrupted scheduler process
- interrupted child runs
- interrupted resolver/planner child runs
- interrupted reconcile state

### 15.3 Recovery order

Recovery must prefer truthful continuation:

1. restore persisted drive state
2. restore child-run facts
3. reconcile live/runtime facts to persisted records
4. re-enter barrier if needed
5. only reopen scheduling after state is coherent

#### 15.3.1 Conflict resolution policy

When persisted and live facts disagree, use the following precedence rules:

| Conflict | Winning truth | Reason |
|---|---|---|
| persisted child run=`running`, live process missing, no terminal artifact | live runtime fact | the process is gone; persisted running state is stale |
| persisted child run=`running`, live terminal artifact exists | persisted + terminal artifact | committed-but-unobserved completion beats stale in-memory status |
| persisted step lease exists, core marks step `done` | core authority | core lifecycle truth is authoritative |
| persisted drive frontier includes step, core no longer claimable | core authority | frontier must be recomputed from live authoritative state |
| persisted barrier absent, open case exists | open case state | safety requires entering barrier |
| persisted active child set differs from live child set | union, then normalize by liveness checks | do not drop possibly real children before explicit classification |

Truthfulness rule:

- live authoritative core and live terminal artifacts beat stale convenience
  caches
- persisted state may only outrank live runtime absence when it contains durable
  proof of a completion that the live process can no longer report

### 15.4 OpenCode requirement

For real runner acceptance, recovery and resume must use real OpenCode session
continuity where available, and truthful fresh relaunch fallback when native
resume is unavailable.

## 16. Operator Control and Observability

### 16.1 Default scope is drive

The following default to drive scope:

- status
- runs
- events
- logs
- artifacts
- actions
- pause/unpause/stop/resume/recover

### 16.2 Child-run drill-down is explicit

Child-run inspection requires an explicit selector such as `--child-run-id`.

### 16.3 Required drive-level event families

The orchestration plane must emit at least:

- `drive_started`
- `drive_status_changed`
- `drive_barrier_entered`
- `drive_barrier_cleared`
- `planner_invoked`
- `planner_applied`
- `resolver_invoked`
- `resolver_returned`
- `child_run_admitted`
- `child_run_final`
- `drive_final`

Minimum event envelope for all drive-level events:

```json
{
  "event_id": "evt_...",
  "timestamp": "2026-04-14T12:00:00Z",
  "kind": "drive_status_changed",
  "drive_id": "drv_01K...",
  "child_run_id": null,
  "step_id": null,
  "payload": {}
}
```

Ordering rule:

- events within one drive must be durably appended in causal order
- child-run events must reference their owning `drive_id`
- `drive_final` must be the terminal event for a drive

### 16.4 Required drive artifacts

At minimum:

- `drive/summary.json`
- `drive/frontier.json`
- `drive/drive_record.json`
- `drive/child_runs.json`
- `drive/child_runs.jsonl`
- `drive/cases.json`
- `drive/planner_invocations.jsonl`
- `drive/resolver_invocations.jsonl`

Directory layout (relative to repository root):

```text
.vectl/
  drives/
    <drive_id>/
      summary.json
      frontier.json
      drive_record.json
      child_runs.json
      child_runs.jsonl
      cases.json
      planner_invocations.jsonl
      resolver_invocations.jsonl
```

Formats:

- `summary.json`, `frontier.json`, `child_runs.json`, `cases.json`: canonical JSON
- `planner_invocations.jsonl`, `resolver_invocations.jsonl`: append-only JSONL

## 17. Phase and Plan Closure Rules

### 17.1 Step completion

Child-run success is not sufficient by itself. The step is complete only after:

- a bounded review gate consumes the terminal execution output and returns one
  of: `pass`, `needs_fix`, `needs_replan`, `operator_required`
- reconcile closes as `merged` or `noop`
- completion gate allows `complete_step`
- authoritative core mutation succeeds

Review gate ownership:

- the driver invokes the review gate after terminal execution output exists and
  before authoritative completion
- review gate output is machine-readable and may trigger resolver or planner
  barrier transitions

#### 17.1.1 Review outcome continuation rules

| `ReviewGateResult.status` | Required drive behavior |
|---|---|
| `pass` | continue through reconcile and completion gates |
| `needs_fix` | enter barrier with reason `review_failed`; keep the current step incomplete; after barrier resolution, control may re-dispatch the **same step** as a fresh child run if no authoritative plan mutation is required |
| `needs_replan` | enter barrier with reason `review_failed` and transition to planner-owned replanning |
| `operator_required` | transition to `blocked_operator` without further automatic dispatch |

`needs_fix` is therefore not a terminal failure and not an implicit plan
mutation. It is a bounded review failure that preserves the current step id and
requires either resolver-guided local unblocking or operator action before the
step may run again.

### 17.2 Phase closure

When all steps in a phase are done, the orchestration system must auto-close the
phase through the approved lifecycle surface.

### 17.3 Plan closure

When all phases are done and no active child runs, barriers, or unresolved
operator states remain, the drive must transition to `completed`.

## 18. Required Code Changes

Required implementation surfaces include:

- `src/vectl/orchestration/contracts.py`
- `src/vectl/orchestration/control.py`
- `src/vectl/orchestration/driver.py` (new)
- `src/vectl/orchestration/runtime.py`
- `src/vectl/orchestration/run_store.py`
- `src/vectl/orchestration/resolver.py`
- `src/vectl/orchestration/dispatch_policy.py`
- `src/vectl/orchestration/roster.py`
- `src/vectl/orchestration/projections.py`
- `src/vectl/orchestration/inspection_queries.py`
- `src/vectl/orch_app.py`
- `src/vectl/cli.py`

## 19. Verification Requirements

### 19.1 General rule

Mock/shim/unit tests remain useful, but they are not sufficient to declare the
orchestration feature complete.

### 19.2 Authoritative acceptance rule

Completion must be proven by **real OpenCode** orchestration tests.

### 19.3 Required real-runner acceptance matrix

The following must all pass with real OpenCode:

1. linear DAG auto-drain to completion
2. multi-phase DAG auto-drain to completion
3. parallel ready frontier dispatch
4. bounded parallelism enforcement
5. real worktree child runs and real merge back to integration root
6. long-running parallel branch execution
7. runtime failure -> resolver -> continue
8. merge conflict -> resolver -> continue or operator boundary
9. review `needs_replan` -> planner -> facade mutation -> continue
10. drive resume after interruption
11. drive recover after interruption
12. drive pause/unpause/stop under parallel child runs
13. phase/plan auto-close

### 19.4 Regression rule

No orchestration milestone may be marked complete until the real OpenCode matrix
above is green.

## 20. Alternatives Rejected

### 20.1 Make `orch run` silently become a full scheduler

Rejected because it overloads the meaning of "run" and collapses the primitive
and scheduler surfaces into one ambiguous command.

### 20.2 Keep same-plan active-run exclusion and fake parallelism externally

Rejected because the scheduler would then live outside the product and would not
own authoritative recovery, barrier, planner, or control semantics.

### 20.3 Allow multiple active drives per plan

Rejected because it destroys authoritative scheduler ownership and makes
frontier/claim/recovery state nondeterministic.

### 20.4 Continue treating resolver/planner as external manual steps

Rejected because it preserves the current gap: orchestration primitives exist,
but the full orchestration loop does not.

## 21. Decision Summary

This RFC freezes the target orchestration model as follows:

- `orch run` remains a single-run primitive
- `orch drive` becomes the authoritative full-plan scheduler
- one plan may have one active drive
- one drive may own many active child runs
- normal flow may dispatch in parallel up to `max_parallelism`
- exceptional flow enters a barrier before resolver/planner/operator handling
- resolver and planner are both part of the scheduler loop
- planner output must be machine-readable and applied via vectl facade only
- operator surfaces default to drive scope
- phase and plan closure must be automatic
- completion is measured by real OpenCode orchestration acceptance, not by fake
  runner coverage alone

## 22. Open Questions

None. This RFC intentionally freezes the orchestration design boundary so that
implementation can proceed without further ambiguity about scope, ownership, or
acceptance criteria.
