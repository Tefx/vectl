# RFC: Full OpenCode Runner Integration for Orchestration

**Status:** Draft  
**Date:** 2026-04-12  
**Author(s):** ChatGPT (draft for discussion)

## 1. Problem / Current Situation

vectl currently exposes orchestration runner selection through:

- `ExecutionRequest.runner`
- `runtime.start()` / `runtime.collect()`
- runner registration in `runner_registry.py`

The repository already allows `opencode` to be selected as a runner, but the
current implementation is not a real orchestration-grade OpenCode integration.

Today, `opencode` is wired as:

```python
registry.register("opencode", SubprocessRunner(runner_id="opencode", command=("opencode",)))
```

This creates a structural mismatch between orchestration expectations and actual
runner behavior.

Current consequences:

1. `vectl orch run` starts `opencode` as a bare command instead of a headless,
   task-carrying execution.
2. no real prompt/task payload is handed to the runner; orchestration only passes
   work refs and a prompt bundle digest.
3. polling sees an interactive or long-lived process rather than a bounded task,
   causing `stall` outcomes such as:

   > `Runner did not complete within poll window for step ...`

4. reusable session semantics are not truly implemented for OpenCode.
5. `recover` cannot perform real continuity recovery for OpenCode because it lacks
   persisted runner-native session and execution artifacts.

This means the current repository does **not** yet provide:

> a real, complete, usable orchestration backend for OpenCode.

## 2. Design Goal

Implement OpenCode as a first-class orchestration runner backend with complete
support for:

- real headless execution
- durable prompt handoff
- reusable session lifecycle
- cancellation
- recovery
- session-aware continuity
- evidence/artifact preservation
- orchestration observability

The result should make this statement true:

> `runtime.default_runner=opencode` is sufficient for real orchestration work,
> not just config validation or dry-run smoke.

## 3. Non-goals

This RFC does **not** propose:

- replacing the orchestration plane with OpenCode-native control logic
- introducing a second execution stack outside the shared runner backend
- requiring a human/operator approval step for recovery fallback
- implementing wildcard runner policies or multi-runner prompt templates
- locking the system to ACP/server mode only

## 4. Core Decisions

### 4.1 OpenCode must use a dedicated runner adapter

OpenCode must no longer be wired through bare `SubprocessRunner("opencode")`.

Instead, vectl should implement a dedicated `OpenCodeRunner` adapter.

Rationale:

- OpenCode requires task-carrying startup semantics, not just process launch
- session lifecycle matters for continuity/reuse
- artifacts and evidence need to be richer than generic stdout/stderr only

### 4.2 Prompt materialization is mandatory

The orchestration plane must persist real prompt/task artifacts before runner
launch.

Hash-only prompt references are insufficient for execution, recovery, and audit.

At minimum, each execution must preserve:

- a structured authoritative prompt bundle artifact
- a runner-consumable prompt artifact

### 4.3 Session support is required

OpenCode integration is not complete without native session lifecycle support.

The runner integration must support:

- start new session-backed work
- continue an existing session
- persist `session_id`
- recover using persisted session metadata when available
- reuse session metadata when orchestration policy allows reuse

### 4.4 Recovery fallback is automatic

If native session recovery is not possible, orchestration must automatically
fall back to a fresh relaunch using preserved prompt artifacts.

This fallback does **not** require human approval.

Rationale:

- unattended recovery is a core requirement for automation
- the system must preserve continuity truthfully, but it must not stop simply
  because native session reuse is unavailable

However, this fallback must be explicit and auditable. It must not pretend to be
native session continuity.

### 4.5 Continuity truth must be preserved

The system must distinguish:

- `recovered_via=native_session_resume`
- `recovered_via=fresh_relaunch`

Both are valid automated recoveries, but they are not semantically identical.

## 5. Functional Scope

### In scope

- dedicated `OpenCodeRunner`
- session-aware launch / resume / poll / cancel
- prompt artifact materialization and handoff
- durable session metadata
- automated recover fallback to fresh relaunch
- stdout/stderr/evidence artifacts
- orchestration-plane observability for OpenCode runs
- reusable session integration where policy allows it

### Out of scope

- changing core orchestration dispatch policy
- changing role-selection semantics
- introducing human-approval recovery gates for OpenCode fallback
- re-architecting the entire runtime around a long-lived daemon

## 6. Required Behavioral Model

### 6.1 Launch modes

OpenCode runner execution must support these modes:

| Mode | Meaning |
|------|---------|
| `start` | Start a fresh execution; create a new session if needed |
| `resume` | Continue an existing session-backed execution |
| `recover` | Reconstruct execution from durable artifacts, preferring native session continuation and falling back to fresh relaunch |

### 6.2 Session policy interaction

OpenCode runner behavior must respect orchestration session policy:

- `reuse_allowed` → orchestration may provide an existing session for continued work
- `reuse_forbidden` → orchestration must force fresh execution semantics

### 6.3 Recovery order

For OpenCode-backed recovery, orchestration must use this order:

1. validate persisted run/session artifacts
2. if native session continuation is possible, resume natively
3. otherwise automatically relaunch from preserved prompt artifacts
4. persist which path was used

This fallback is required automation behavior, not an operator-only escape hatch.

## 7. Contract Changes

### 7.1 `ExecutionRequest`

`ExecutionRequest` must carry enough information for a real OpenCode launch.

Recommended shape extension:

```python
@dataclass(frozen=True)
class ExecutionRequest:
    step_id: str
    role: str
    agent_id: str
    runner: str
    work_refs: tuple[str, ...]
    prompt_bundle_path: str
    runner_prompt_path: str
    request_mode: Literal["start", "resume", "recover"] = "start"
    session_policy: Literal["reuse_allowed", "reuse_forbidden"] = "reuse_forbidden"
    session_id: str | None = None
```

### 7.2 `RunnerHandle`

The runner handle must remain runner-aware and session-aware:

```python
@dataclass(frozen=True)
class RunnerHandle:
    runner: str
    run_id: str
    session_id: str | None
```

### 7.3 `RunnerPollResult`

OpenCode runner polling should continue to use normalized statuses:

- `running`
- `success`
- `fail`
- `stall`
- `transport_error`

but should attach richer `evidence_refs` pointing to launch/session/stdout/stderr artifacts.

### 7.4 Contract migration rule

This RFC requires a clean contract update, not a compatibility bridge.

Implementation rule:

1. extend `ExecutionRequest` in one atomic change
2. update every call site that constructs `ExecutionRequest`
3. update every runner/runtime path that consumes the request
4. update tests in the same change

There is no temporary adapter or dual-shape compatibility mode.

The repository may update existing callers directly.

## 8. Prompt and Artifact Model

### 8.1 Authority copy

For every run, orchestration must write durable prompt artifacts under the run root.

Recommended minimum files:

```text
.vectl/runs/<run_id>/input/prompt_bundle.json
.vectl/runs/<run_id>/input/runner_prompt.md
```

### 8.2 Workspace copy

The workspace should receive a runner-readable prompt copy:

```text
<workspace>/.vectl/orch/runner_prompt.md
```

This allows runner startup to use a stable file path inside the execution workspace.

### 8.3 Structured prompt bundle

`prompt_bundle.json` should preserve, at minimum:

```json
{
  "role_id": "python-senior-tacit",
  "agent_id": "python-senior-tacit",
  "runner": "opencode",
  "system_prompt": "...",
  "task_prompt": "...",
  "messages": [...],
  "prompt_bundle_sha256": "..."
}
```

### 8.4 Runner-consumable prompt

`runner_prompt.md` should be a bounded, deterministic, flattened instruction file
for OpenCode consumption. It should include:

1. role / agent identity
2. system instructions
3. task prompt
4. context messages
5. output contract
6. execution/work rules

### 8.5 Required runner handoff mechanism

The runner handoff mechanism must be explicit and stable.

OpenCodeRunner must receive the materialized prompt through **both** of these channels:

1. **Canonical workspace path**  
   The runner must be able to read:

   ```text
   .vectl/orch/runner_prompt.md
   ```

   relative to the execution workspace.

2. **Runner bootstrap environment variables**  
   The process environment must include at least:

   ```text
   VECTL_ORCH_RUN_ID
   VECTL_ORCH_STEP_ID
   VECTL_ORCH_AGENT_ID
   VECTL_ORCH_PROMPT_PATH
   VECTL_ORCH_PROMPT_BUNDLE_PATH
   ```

3. **OpenCode file attachment**  
   OpenCode must be launched with:

   ```text
   --file .vectl/orch/runner_prompt.md
   ```

   so the runner prompt is explicitly attached to the OpenCode request.

The OpenCode process must be started with a bounded one-shot bootstrap message
that instructs it to read the canonical prompt file from the workspace.

This RFC freezes the handoff contract as:

> workspace prompt file + explicit env vars + `--file` attachment + bounded bootstrap message.

## 9. OpenCode Runner Adapter

### 9.1 Adapter requirement

Implement a concrete `OpenCodeRunner` in the orchestration runner substrate.

### 9.2 Launch behavior

`launch()` must start OpenCode in a non-interactive, bounded execution mode.

The runner must not invoke bare `opencode` and rely on the TUI default.

The one-shot OpenCode launch contract is frozen as:

```text
opencode run \
  --format json \
  --dir <workspace> \
  --agent <agent_id> \
  --file .vectl/orch/runner_prompt.md \
  "Read the attached runner prompt file, execute the requested task in the current workspace, and then exit."
```

The launch implementation must:

1. set the required `VECTL_ORCH_*` environment variables
2. run in the prepared workspace
3. pass a bounded bootstrap message that points OpenCode at `.vectl/orch/runner_prompt.md`
4. pass `--agent <agent_id>` as the authoritative agent selection mechanism

If OpenCode returns a launch failure because the requested agent is unknown,
that is a `RunnerLaunchError(reason="request_invalid")`, not a silent fallback trigger.

### 9.3 Resume behavior

`resume()` must perform real session-based continuation when a valid `session_id`
exists and the OpenCode CLI/session contract supports it.

The native continuation contract is frozen as:

```text
opencode run \
  --format json \
  --dir <workspace> \
  --session <session_id> \
  --agent <agent_id> \
  --file .vectl/orch/runner_prompt.md \
  "Continue this session by executing the attached runner prompt in the current workspace."
```

`--continue` must not be used by orchestration because it targets "last session"
and is therefore non-deterministic for machine recovery.

`--session <session_id>` is the required deterministic continuation surface.

If resume cannot proceed because the session is invalid or unavailable, it must
raise a typed `RunnerResumeError` and let orchestration recovery policy decide
whether to auto-fallback to fresh relaunch.

### 9.4 Poll behavior

`poll()` must remain non-blocking.

It should:

1. return `running` while execution is still active
2. return terminal normalized status when the execution completes or fails
3. preserve stdout/stderr refs as evidence
4. preserve session metadata refs if updated during execution

### 9.5 Cancel behavior

`cancel()` must attempt graceful termination first and escalate to hard kill if
required by the configured timeout policy.

Cancellation should terminate the current execution process, but must not silently
discard the persisted session metadata needed for later recovery.

## 10. Recovery Semantics

### 10.1 Native resume first

When durable session metadata exists and the session is valid, `recover` should
resume the original OpenCode session.

Minimum session validation criteria before native resume:

1. `session.json` exists
2. `session.json` parses successfully
3. `session.json` contains non-empty values for:
   - `runner`
   - `session_id`
   - `step_id`
   - `agent_id`
   - `workspace`
4. `runner == "opencode"`
5. the authoritative prompt artifacts still exist and parse/read successfully:
   - `input/prompt_bundle.json`
   - `input/runner_prompt.md`

If any of these checks fail, native session resume is unavailable.

### 10.2 Automatic fresh relaunch fallback

If native session recovery is not possible, the system must automatically relaunch
OpenCode from the preserved prompt artifacts.

This fallback:

- is automatic
- does not require user approval
- must preserve explicit evidence that it was a fresh relaunch

Fresh relaunch is allowed only if the authoritative prompt artifacts are still valid.

Minimum prompt artifact validity criteria before fresh relaunch:

1. `.vectl/runs/<run_id>/input/prompt_bundle.json` exists
2. `prompt_bundle.json` parses as valid JSON
3. `prompt_bundle.json` contains non-empty values for:
   - `role_id`
   - `agent_id`
   - `runner`
   - `system_prompt`
   - `task_prompt`
   - `prompt_bundle_sha256`
4. `.vectl/runs/<run_id>/input/runner_prompt.md` exists
5. `runner_prompt.md` is non-empty text
6. if the implementation persists a digest for `runner_prompt.md`, that digest matches

If both native session resume and prompt-based relaunch are unavailable, recovery must fail explicitly rather than fabricating continuity.

### 10.3 Recovery truthfulness

The system must not collapse these two recovery paths into the same label.

Required evidence fields should distinguish:

- `recovered_via=native_session_resume`
- `recovered_via=fresh_relaunch`

Authoritative persistence location:

```text
.vectl/runs/<run_id>/recovery/continuity.json
```

Required field:

```json
{
  "recovered_via": "native_session_resume | fresh_relaunch"
}
```

This value should also be mirrored into:

- recovery attempt artifacts
- relevant recovery event payloads
- final human-readable recovery summary

### 10.4 Recovery artifacts

Recommended minimum artifacts:

```text
.vectl/runs/<run_id>/recovery/continuity.json
.vectl/runs/<run_id>/recovery/resume_attempt.json
.vectl/runs/<run_id>/recovery/recover_attempt.json
```

`resume_attempt.json` and `recover_attempt.json` should record, at minimum:

- whether native session validation succeeded
- why validation failed if it failed
- whether fallback relaunch was used
- resulting execution/session identifiers

## 11. Reuse and Roster Integration

OpenCode session support must connect to orchestration roster/session reuse
instead of bypassing it.

Minimum requirements:

- reusable leases may carry OpenCode `session_id`
- later compatible work may reuse that session if policy allows
- session reuse metadata must remain visible in orchestration state

This is required for reusable session semantics to be real rather than decorative.

## 12. Required Code Changes

### New / expanded implementation surfaces

- `src/vectl/orchestration/runners.py`
- `src/vectl/orchestration/runner_registry.py`
- `src/vectl/orchestration/contracts.py`
- `src/vectl/orchestration/runtime.py`
- `src/vectl/orch_app.py`
- continuity/run-store artifact handling surfaces as needed

### Required concrete changes

1. add OpenCode-specific runner adapter
2. extend `ExecutionRequest` with runner-usable prompt/session fields
3. materialize prompt artifacts before runtime launch
4. register `opencode` via `OpenCodeRunner`, not `SubprocessRunner("opencode")`
5. persist session metadata and evidence refs
6. implement native resume + automatic fresh relaunch fallback in recovery paths
7. implement the fixed prompt handoff mechanism (workspace path + env vars + bootstrap message)

## 13. Verification Requirements

Implementation is not complete until all of the following are demonstrated.

### 13.1 Unit / substrate tests

- runner registry resolves OpenCode adapter correctly
- prompt artifacts are materialized correctly
- `ExecutionRequest` carries required fields
- OpenCode runner launch/poll/cancel behave correctly under controlled shims
- typed errors are raised for launch/resume/poll/cancel failures

### 13.2 Integration tests

- runtime can launch OpenCode-backed work and collect terminal result
- stop/cancel reaches the active OpenCode execution
- recovery prefers native session continuation when possible
- recovery automatically falls back to fresh relaunch when session resume is unavailable
- fresh relaunch fallback is marked explicitly in evidence

### 13.3 Live smoke requirements

At least one real OpenCode live orchestration smoke test must prove:

1. `vectl orch run` completes a real task with `runtime.default_runner=opencode`
2. `status`, `events`, `logs`, and `artifacts` show a coherent execution story
3. `recover --dry-run` and at least one real recovery path behave coherently

### 13.4 Regression requirement

The failure previously observed:

> `Runner did not complete within poll window for step ...`

must no longer occur for ordinary OpenCode one-shot orchestration execution.

## 14. Alternatives Rejected

### 14.1 Just change `("opencode",)` to `("opencode", "run")`

Rejected.

That would still fail to solve:

- prompt handoff
- session continuity
- recovery artifacts
- truthful recovery mode reporting

### 14.2 Ignore session support and rely only on relaunch

Rejected.

That would not satisfy complete orchestration continuity or reuse semantics.

### 14.3 Require human approval before fallback relaunch

Rejected.

That would make unattended recovery weaker and would violate the requirement
that automation remain truly autonomous when a safe prompt-based relaunch path exists.

## 15. Resolved CLI Contract Decisions

This RFC resolves the previously open runner-contract questions as follows.

### 15.1 One-shot launch contract

Use `opencode run` with:

- `--format json`
- `--dir <workspace>`
- `--agent <agent_id>`
- `--file .vectl/orch/runner_prompt.md`
- bounded bootstrap message

### 15.2 Session continuation contract

Use `opencode run --session <session_id>` for deterministic session continuation.

Do **not** use `--continue` in orchestration, because it is "last session"
oriented and therefore ambiguous for machine recovery.

### 15.3 Fork policy

Fork-based recovery is **not** a first-class path in this RFC.

The required recovery paths are only:

1. native session resume
2. automatic fresh relaunch from preserved prompt artifacts

`--fork` may be explored in a future RFC if OpenCode session semantics require it,
but it is intentionally excluded from this initial complete implementation.

## 16. Decision Summary

This RFC recommends:

1. implement a dedicated `OpenCodeRunner`
2. materialize prompt artifacts for execution and recovery
3. extend execution contracts with real prompt/session fields
4. support real OpenCode session lifecycle
5. make recovery automatic, preferring native session resume and falling back to fresh relaunch without human approval
6. preserve explicit recovery truth (`native_session_resume` vs `fresh_relaunch`)
7. freeze `opencode run --session <session_id>` as the deterministic session continuation contract
8. explicitly exclude `--continue` and `--fork` from required orchestration recovery semantics in this RFC
9. verify the integration with unit, integration, and real live smoke evidence

This is the smallest design that is still genuinely complete for a real,
session-aware, automation-grade OpenCode orchestration backend.
