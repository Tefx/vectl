# Module Specification: vectl/driver/

> Architecture specification for the programmatic orchestration engine.
> Replaces the LLM-based orchestrator with a deterministic Python driver.

**Status**: Implemented
**Blueprint**: `DRIVER-BLUEPRINT.md`
**Certainty**: [Proven] for all interfaces derived from existing vectl code;
[Likely] for judgment and runner protocols (derived from blueprint + verified CLI capabilities).

---

## 1. Module Map

```
src/vectl/driver/
    __init__.py          # Package marker + public re-exports
    types.py             # Driver-internal data types (dataclasses)       (~80 lines)
    errors.py            # Driver error hierarchy                         (~40 lines)
    config.py            # YAML config loader + Pydantic models           (~80 lines)
    observe.py           # Event emitter + JSONL writer                   (~60 lines)
    session.py           # Session reuse pool                             (~60 lines)
    worktree.py          # Git worktree lifecycle                         (~120 lines)
    dispatch.py          # Prompt template rendering                      (~100 lines)
    parsers.py           # Output parsers for runner CLI results          (~100 lines)
    runners.py           # Runner Protocol + implementations              (~150 lines)
    judgments.py         # Judgment type definitions + context schemas     (~80 lines)
    judge.py             # Judgment Agent invocation + routing             (~120 lines)
    loop.py              # Main event loop + state machine                (~250 lines)
    entrypoint.py        # Shared entrypoint adapter contract             (~120 lines)
    __main__.py          # `python -m vectl.driver` runtime entry         (~10 lines)
```

### 1.1 Module Dependency Graph

```
                    ┌─────────────────────────────────────────────┐
                    │               vectl (frozen)                 │
                    │  models  core  decide  io  plan_path  claims │
                    └─────────┬───────────────────────────────────┘
                              │ (import only, never modify)
    ┌─────────────────────────┼───────────────────────────────────┐
    │  vectl.driver           │                                   │
    │                         ▼                                   │
    │   config.py ◄────── loop.py ──────► observe.py              │
    │                      │ │ │                                  │
    │          ┌───────────┘ │ └──────────────────┐               │
    │          ▼             ▼                    ▼               │
    │     runners.py    judge.py            worktree.py           │
    │          │    │         │                    │               │
    │          ▼    ▼         ▼                    │               │
    │     dispatch.py  parsers.py                 │               │
    │          │                                  │               │
    │          └──────────┐  ┌────────────────────┘               │
    │                     ▼  ▼                                    │
    │                  session.py                                  │
    │                     │                                       │
    │     types.py ◄──────┼── (imported by all driver modules)    │
    │     errors.py ◄─────┘── (imported by all driver modules)    │
    │                                                             │
    └─────────────────────────────────────────────────────────────┘
```

### 1.2 Dependency Direction Rules

| Source | May import from | Must NOT import from |
|--------|----------------|---------------------|
| `loop.py` | All driver modules, all vectl core modules | Nothing (top of driver) |
| `runners.py` | `types`, `errors`, `config`, `dispatch`, `observe`, `parsers` | `loop`, `judge` |
| `parsers.py` | `types` | `loop`, `runners`, `judge`, `config` |
| `judge.py` | `types`, `errors`, `judgments`, `config`, `observe` | `loop`, `runners` |
| `worktree.py` | `types`, `errors` | `loop`, `runners`, `judge` |
| `dispatch.py` | `types`, `config` | `loop`, `runners`, `judge` |
| `session.py` | `types`, `config` | Everything except `types`, `config` |
| `observe.py` | `types` | Everything except `types` |
| `config.py` | `types` (if needed) | All other driver modules |
| `types.py` | `vectl.models` only | All driver modules |
| `errors.py` | Nothing | All modules |
| `judgments.py` | `types` | All other driver modules |

---

## 2. Module Specifications

### 2.1 types.py -- Driver Internal Data Types

**Responsibility**: Define all mutable and immutable value types used internally
by the driver. Single canonical location for driver-specific data contracts.

**Non-responsibility**: Does NOT define vectl-core types (Plan, Step, Action, etc.).
Does NOT define config schema types (those live in `config.py`).

```python
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from enum import Enum


class RunnerStatus(str, Enum):
    """Outcome of a runner execution."""
    SUCCESS = "success"
    FAIL = "fail"
    STALL = "stall"
    TRANSPORT_ERROR = "transport_error"


@dataclass
class RunnerResult:
    """Parsed output from a completed runner process."""
    status: RunnerStatus
    session_id: str | None
    output: str
    elapsed_seconds: float
    exit_code: int | None
    cost_usd: float | None = None
    tokens: dict[str, int] | None = None


@dataclass
class RunningEntry:
    """Bookkeeping for one in-flight runner process."""
    step_id: str
    agent: str
    runner_name: str
    handle: RunnerHandle               # Protocol reference (see runners.py)
    worktree_path: str
    dispatched_at: float               # time.monotonic()


@dataclass
class CompletedEntry:
    """A runner that has finished; pending reconciliation."""
    step_id: str
    agent: str
    runner_name: str
    result: RunnerResult
    worktree_path: str
    elapsed_seconds: float


@dataclass
class DriverState:
    """All mutable in-memory state for the driver loop.

    This is the SINGLE owner of running-task bookkeeping.
    No other module may maintain parallel running-task state.
    """
    running: dict[str, RunningEntry] = field(default_factory=dict)
    # step_id -> RunningEntry

    completed_queue: list[CompletedEntry] = field(default_factory=list)
    # Drained each loop iteration

    failure_counts: dict[str, int] = field(default_factory=dict)
    # step_id -> consecutive failure count

    failure_history: dict[str, list[str]] = field(default_factory=dict)
    # step_id -> [error_output_1, error_output_2, ...]

    runner_failures: dict[tuple[str, str], int] = field(default_factory=dict)
    # (step_id, runner_name) -> consecutive failure count for runner fallback

    agent_overrides: dict[str, str] = field(default_factory=dict)
    # step_id -> overridden agent name (from judge SWITCH_AGENT verdict)

    halt_requested: bool = False

    loop_detector: list[str] = field(default_factory=list)
    # Last N serialized decide() action signatures for loop detection

    merge_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    # Serializes all git merge operations (real lock, not LLM instruction)

    def as_running_tasks(self) -> list[RunningTask]:
        """Convert to vectl.models.RunningTask list for decide() input."""
        ...

    def drain_completed(self) -> list[CompletedResult] | None:
        """Drain completed_queue into vectl.models.CompletedResult list for decide() input."""
        ...

    async def wait_for_any(self) -> CompletedEntry:
        """Wait for any running handle to complete. Returns first done."""
        ...

    def register(self, step_id: str, agent: str, runner_name: str,
                 handle: RunnerHandle, worktree_path: str) -> None:
        """Register a newly dispatched runner."""
        ...

    def mark_completed(self, step_id: str, status: str, output: str) -> None:
        """Move entry from running to completed_queue.

        Args:
            step_id: The step to mark completed.
            status: Runner outcome status (e.g. "success", "fail").
            output: Runner output text for evidence/error context.
        """
        ...

    def increment_failure(self, step_id: str, runner_name: str) -> None:
        """Increment failure counters for step and runner."""
        ...

    def failure_count(self, step_id: str) -> int:
        """Return consecutive failure count for a step."""
        ...

    def get_failure_context(self, step_id: str) -> str | None:
        """Return last failure output for prompt enrichment, or None."""
        ...

    def get_failure_history(self, step_id: str) -> list[str]:
        """Return all failure outputs for a step."""
        ...

    def set_agent_override(self, step_id: str, agent: str) -> None:
        """Override the agent for a step (judge SWITCH_AGENT verdict)."""
        ...

    def detect_loop(self, window: int = 10) -> bool:
        """Return True if the last `window` decide() signatures are identical."""
        ...

    def summary(self) -> dict[str, object]:
        """Return summary dict for FINAL event."""
        ...


@dataclass
class MergeResult:
    """Outcome of a git merge operation."""
    status: str          # "clean" | "auto_resolved" | "conflict"
    files: list[str] = field(default_factory=list)
    trivial: bool = True


@dataclass
class SessionEntry:
    """A completed session eligible for reuse."""
    session_id: str
    runner_name: str
    agent: str
    step_id: str
    completed_at: float  # time.monotonic()
```

**Invariant**: `RunningEntry` instances exist in `DriverState.running` if and only
if a runner process is alive. When a handle completes, the entry MUST be moved to
`completed_queue` within the same loop iteration.

**Invariant**: `DriverState.merge_lock` is the sole serialization mechanism for
git merge operations. All code paths that call `worktree.merge()` MUST hold this lock.

---

### 2.2 errors.py -- Driver Error Hierarchy

**Responsibility**: Define all driver-specific exceptions. Provides structured error
context for each failure domain.

**Non-responsibility**: Does NOT re-export vectl-core exceptions (PlanError, CASConflictError, etc.).
Callers that need to catch both driver and core errors import from both modules.

```python
class DriverError(Exception):
    """Base for all driver errors."""


class ConfigError(DriverError):
    """Invalid or missing driver configuration."""


class RunnerError(DriverError):
    """Runner dispatch or execution failure."""
    def __init__(self, runner_name: str, step_id: str, message: str) -> None: ...


class RunnerNotFoundError(RunnerError):
    """Configured runner command not found on PATH."""


class RunnerOutputError(RunnerError):
    """Runner produced unparseable output."""


class WorktreeError(DriverError):
    """Git worktree operation failure."""
    def __init__(self, step_id: str, message: str) -> None: ...


class JudgmentError(DriverError):
    """Judgment agent invocation failure."""
    def __init__(self, judgment_type: str, step_id: str, message: str) -> None: ...


class JudgmentTimeoutError(JudgmentError):
    """Judgment agent did not respond within timeout."""


class JudgmentParseError(JudgmentError):
    """Judgment agent returned unparseable output."""


class LoopHaltError(DriverError):
    """Driver loop detected a halt condition."""
    def __init__(self, reason: str) -> None: ...
```

**Error propagation rules**:

| Error | Raised by | Caught by | Behavior on catch |
|-------|-----------|-----------|-------------------|
| `ConfigError` | `config.py` | `loop.py` (startup) | Halt with diagnostic |
| `RunnerNotFoundError` | `runners.py` | `loop.py` | Halt with config error |
| `RunnerError` | `runners.py` | `loop.py` reconcile path | Treat as TRANSPORT_ERROR, increment failure |
| `RunnerOutputError` | `runners.py` | `loop.py` reconcile path | Treat as TRANSPORT_ERROR, retry once |
| `WorktreeError` | `worktree.py` | `loop.py` dispatch/reconcile | Log, attempt cleanup, continue |
| `JudgmentTimeoutError` | `judge.py` | `loop.py` | Skip judgment, accept optimistically, log warning |
| `JudgmentParseError` | `judge.py` | `loop.py` | Skip judgment, accept optimistically, log warning |
| `LoopHaltError` | `loop.py` | `loop.py` (top-level) | Clean shutdown |
| `CASConflictError` | `vectl.io` | `loop.py` | Reload plan, retry operation |
| `PlanError` | `vectl.core` | `loop.py` | Log, skip step, continue |

---

### 2.3 config.py -- Configuration

**Responsibility**: Load driver.yaml, validate via Pydantic, provide typed config
to all other modules.

**Non-responsibility**: Does NOT own runtime state. Does NOT resolve plan paths
(uses `vectl.plan_path` for that).

**Dependencies**: `pydantic`, `pyyaml`, `types` (if referencing driver types).

```python
from pydantic import BaseModel, Field


class RunnerConfig(BaseModel):
    command: str
    args: list[str] = Field(default_factory=list)
    prompt_mode: str = "stdin"             # "stdin" | "stdin_dash"
    stall_timeout: int = 300
    resume_flag: str | None = None
    resume_command: list[str] | None = None
    session_id_regex: str | None = None
    output_parser: str = "claude_json"     # "claude_json" | "opencode_jsonl" | "codex_jsonl" | "gemini_json"
    persist_session: bool = True
    experimental: bool = False


class SessionConfig(BaseModel):
    reuse_ttl: int = 300
    ttl_overrides: dict[str, int] = Field(default_factory=dict)


class JudgeConfig(BaseModel):
    runner: str = "opencode"             # All sidecar/subagent calls default to opencode
    model: str | None = None
    structured_output: bool = True       # Use model structured output (see note below)
    timeout: int = 60                    # Per-judgment timeout in seconds
    preflight: bool = True               # JudgmentType.PREFLIGHT
    evidence_validation: bool = True     # JudgmentType.EVIDENCE
    failure_classification: bool = True  # JudgmentType.FAILURE
    escalation: bool = True              # JudgmentType.ESCALATION
    gate_assessment: bool = True         # JudgmentType.GATE
    cold_context: bool = True            # JudgmentType.COLD_CONTEXT
    anomaly: bool = True                 # JudgmentType.ANOMALY
    skip_preflight_for: list[str] = Field(default_factory=list)


class OrchestrationConfig(BaseModel):
    max_parallelism: int = 5
    merge_strategy: str = "squash"


class ObservabilityConfig(BaseModel):
    events_file: str = ".vectl/driver-events.jsonl"
    log_level: str = "INFO"
    print_progress: bool = True
    cost_tracking: bool = True


class DriverConfig(BaseModel):
    plan_path: str | None = None  # null = auto-detect via resolve_plan_path()
    runners: dict[str, RunnerConfig]
    agent_routing: dict[str, str] = Field(default_factory=dict)
    fallback_runner: str = "opencode"
    orchestration: OrchestrationConfig = Field(default_factory=OrchestrationConfig)
    session: SessionConfig = Field(default_factory=SessionConfig)
    judge: JudgeConfig = Field(default_factory=JudgeConfig)
    observability: ObservabilityConfig = Field(default_factory=ObservabilityConfig)

    def route_agent(self, agent: str) -> str:
        """Resolve agent name to runner name via agent_routing config.

        Supports exact match and glob patterns (fnmatch).
        Falls back to self.fallback_runner.
        """
        ...
```

**Invariant**: `DriverConfig.runners` MUST contain the runner referenced by
`DriverConfig.judge.runner`. Validated at load time; raises `ConfigError` on violation.

**Invariant**: `DriverConfig.fallback_runner` MUST reference a key in `DriverConfig.runners`.

**Lifecycle call convention**: All `claim_step`, `complete_step`, and `defer_step`
calls MUST pass `claims_path=resolve_claims_path(plan_path)` to ensure claims.json
is resolved relative to the correct plan location.

**Example `driver.yaml`**:

```yaml
# plan_path: null  # omit for auto-detect via resolve_plan_path()

runners:
  claude:
    command: claude
    args: ["-p", "--output-format", "json", "--dangerously-skip-permissions"]
    prompt_mode: stdin                  # Prompt piped via stdin
    stall_timeout: 300
    resume_flag: "--resume"             # --resume UUID
    session_id_regex: "^[0-9a-f]{8}-[0-9a-f]{4}-"
    output_parser: claude_json          # Single JSON object with session_id, result, usage
    persist_session: true               # Don't pass --no-session-persistence
  opencode:
    command: opencode
    args: ["run", "--format", "json", "--dir", "{workdir}"]
    prompt_mode: stdin
    stall_timeout: 600
    resume_flag: "--session"            # --session SES_ID
    session_id_regex: "^ses_[a-z0-9]+"
    output_parser: opencode_jsonl       # JSONL stream: step_start, text, step_finish
    persist_session: true
  codex:
    command: codex
    args: ["exec", "--json", "--dangerously-bypass-approvals-and-sandbox", "-C", "{workdir}"]
    prompt_mode: stdin_dash             # Uses "-" to read stdin
    stall_timeout: 600
    resume_command: ["codex", "exec", "resume", "--json", "--dangerously-bypass-approvals-and-sandbox"]
    session_id_regex: "^[0-9a-f]{8}-"
    output_parser: codex_jsonl          # JSONL stream: thread.started, item.completed, turn.completed
  gemini:
    command: gemini
    args: ["-p", "--output-format", "json", "--approval-mode", "yolo"]
    prompt_mode: stdin
    stall_timeout: 600
    output_parser: gemini_json
    experimental: true                  # Auth issues unresolved

agent_routing:
  python-senior: claude
  frontend-engineer: claude
  "*-tester": opencode
  "*-reviewer": opencode
  "*-verifier": opencode
  "*-auditor": opencode
  "*-planner": opencode

fallback_runner: opencode

orchestration:
  max_parallelism: 5
  merge_strategy: squash

session:
  reuse_ttl: 300
  ttl_overrides:
    claude: 600

judge:
  runner: opencode
  structured_output: true
  timeout: 60
  preflight: true
  evidence_validation: true
  failure_classification: true
  escalation: true
  gate_assessment: true
  cold_context: true
  anomaly: true
  skip_preflight_for: ["*.define", "*.gate", "*.verify"]

observability:
  events_file: .vectl/driver-events.jsonl
  log_level: INFO
  print_progress: true
  cost_tracking: true
```

---

### 2.4 observe.py -- Event Emitter

**Responsibility**: Append structured events to a JSONL file. Optionally print
human-readable progress lines to stderr.

**Non-responsibility**: Does NOT interpret events. Does NOT aggregate metrics
(that is the replay tool's job).

```python
from typing import Protocol


class Observer(Protocol):
    def emit(self, event_type: str, /, **data: object) -> None:
        """Append a timestamped event. Thread-safe (called from asyncio, single-threaded)."""
        ...

    def close(self) -> None:
        """Flush and close the JSONL file handle."""
        ...
```

**Event schema** (all events share this envelope):

```python
@dataclass
class Event:
    ts: float           # time.time()
    event: str          # Event type name (see event table in blueprint)
    data: dict[str, object]
```

---

### 2.5 session.py -- Session Reuse Pool

**Responsibility**: Track completed sessions and find reusable ones based on
step dependency, runner match, and TTL.

**Non-responsibility**: Does NOT make dispatch decisions. Does NOT interact with
`decide.py`'s `DecideState` directly -- see ownership boundary below.

```python
class SessionPool:
    def __init__(self, config: SessionConfig) -> None: ...

    def record(self, step_id: str, session_id: str,
               runner_name: str, agent: str) -> None:
        """Record a completed session for potential reuse."""
        ...

    def find_reusable(self, step_id: str, agent: str,
                      runner_name: str, depends_on: list[str]) -> str | None:
        """Find a reusable session_id, or None.

        Reuse requires:
        1. Exactly one dependency in depends_on
        2. That dependency has a recorded session
        3. That session's runner matches runner_name
        4. TTL has not expired

        Accepted limitation: Only single-dependency steps are eligible
        for session reuse. Steps with 0 or 2+ dependencies always get
        fresh sessions. This is a deliberate simplification -- multi-dep
        session selection would require heuristics (most recent? longest
        lived?) with unclear benefit. The single-dep case covers the
        majority of linear chains (contract -> impl -> test).
        """
        ...
```

#### Ownership Boundary: SessionPool vs DecideState

`decide.py` receives decide-side memory via an explicit `state: DecideState`
parameter. The driver runtime passes `DriverState.decide_state` and does NOT
write to decide-memory fields directly.

The driver's `SessionPool` is a **parallel, runner-aware** pool that adds
runner-name matching and per-runner TTL overrides -- capabilities that `decide()`
does not have. The two mechanisms serve different purposes:

| Concern | `DecideState` (decide.py) | `SessionPool` (driver) |
|---------|--------------------------|------------------------|
| Owner | `DriverState` + `decide()` mutation contract | `loop.py` via `session.py` |
| Written by | `decide()` when processing `CompletedResult` | `loop.py` reconcile path after successful merge |
| Read by | `decide()` during `should_reuse_session()` | `loop.py` dispatch path, BEFORE calling `decide()` |
| Runner-aware | No | Yes (runner-name match required) |
| Per-runner TTL | No | Yes |
| Purpose | decide() internal: determine session field on Action | Driver: pre-resolve session_id for dispatch optimization |

**Reconciliation**: When `decide()` produces an `Action` with `session="reuse"` and
a `task_id`, the driver uses that `task_id`. When `decide()` produces `session="fresh"`,
the driver MAY override by checking `SessionPool.find_reusable()` for a runner-aware
match. This is safe because session reuse is best-effort: if the session is stale,
the runner starts fresh (graceful degradation).

**Decision**: [ADR] Decide-side memory lives in `DecideState` and is passed
explicitly from `DriverState`. `SessionPool` remains a supplementary
runner-aware layer for dispatch-time optimization.

---

### 2.6 worktree.py -- Git Worktree Lifecycle

**Responsibility**: Create, merge (squash), auto-resolve trivial conflicts, and
clean up git worktrees for step isolation.

**Non-responsibility**: Does NOT make merge conflict resolution decisions beyond
trivial auto-resolve. Non-trivial conflicts are reported to the caller for
judgment-based resolution.

```python
async def create(step_id: str, base_dir: str = ".vectl/worktrees") -> tuple[str, str]:
    """Create a worktree for a step. Returns (worktree_path, branch_name).

    Idempotent: if worktree already exists, returns existing path.
    Branch naming: vectl/step-{step_id}

    Raises:
        WorktreeError: If git operations fail.
    """
    ...

async def merge(step_id: str, worktree_path: str,
                strategy: str = "squash") -> MergeResult:
    """Merge step branch back to main via squash merge.

    Returns MergeResult with status:
    - "clean": merge succeeded, committed
    - "auto_resolved": trivial conflicts resolved (plan.yaml, lockfiles)
    - "conflict": non-trivial conflicts, merge aborted

    Raises:
        WorktreeError: If git operations fail unexpectedly.
    """
    ...

async def cleanup(step_id: str, worktree_path: str) -> None:
    """Remove worktree and delete branch.

    Best-effort: logs warnings on failure but does not raise.
    """
    ...

async def cleanup_orphans(base_dir: str = ".vectl/worktrees") -> list[str]:
    """Detect and remove worktrees with no corresponding running task.

    Called at startup for recovery.
    Returns list of cleaned-up step_ids.
    """
    ...

# Trivial conflict patterns (auto-resolve with --ours)
TRIVIAL_PATTERNS: frozenset[str] = frozenset({
    "plan.yaml", "claims.json", "*.lock", "*.lockb",
})
```

---

### 2.7 dispatch.py -- Prompt Template Rendering

**Responsibility**: Render deterministic prompt strings from step metadata for
runner dispatch. Provides all context a worker needs without LLM generation.

**Non-responsibility**: Does NOT invoke runners. Does NOT generate prompts via LLM.
Does NOT own prompt templates as external files (templates are inline string constants
for simplicity and auditability).

```python
def render_prompt(
    *,
    step_id: str,
    agent: str,
    description: str,
    verification: str,
    refs: list[str],
    worktree_path: str,
    session_reuse: bool,
    failure_context: str | None = None,
    plan_context: str = "",
    phase_context: str = "",
) -> str:
    """Render a dispatch prompt for a worker agent.

    Template includes:
    - Step identity and description
    - Verification criteria
    - Reference file paths
    - Working directory (worktree)
    - Session context (if reusing)
    - Previous failure context (if retrying)
    - vectl completion instructions (claim is already done)
    """
    ...
```

**Data flow for prompt templates**: The `Action` dataclass from `decide()` carries
`step_description`, `step_verification`, and `step_refs`. The driver passes these
directly to `render_prompt()`. Additional context (failure history, phase context)
comes from `DriverState` and the loaded `Plan`. No additional plan reads are needed
for prompt rendering.

---

### 2.8 runners.py -- Runner Protocol and Implementations

**Responsibility**: Define the `Runner` and `RunnerHandle` protocols. Provide
concrete implementations for each supported CLI runner. Parse runner output into
`RunnerResult`.

**Non-responsibility**: Does NOT decide which runner to use (that is `config.route_agent()`).
Does NOT manage runner lifecycle across the loop (that is `DriverState`).

```python
from typing import Protocol


class RunnerHandle(Protocol):
    """Handle to a running CLI subprocess."""
    session_id: str | None
    pid: int | None

    async def wait(self, timeout: float | None = None) -> RunnerResult:
        """Wait for process completion. Raises RunnerError on timeout."""
        ...

    async def kill(self) -> None:
        """Kill the subprocess. Idempotent."""
        ...

    def is_alive(self) -> bool:
        """Return True if the process is still running."""
        ...


class Runner(Protocol):
    """Protocol for dispatching work to a CLI agent."""
    name: str

    async def dispatch(
        self,
        prompt: str,
        agent: str,
        workdir: str,
        session_id: str | None = None,
    ) -> RunnerHandle:
        """Launch a CLI subprocess and return a handle.

        Args:
            prompt: The rendered prompt text.
            agent: Agent name (for --agent flag if supported).
            workdir: Working directory (worktree path).
            session_id: If provided, resume this session.

        Raises:
            RunnerNotFoundError: If the runner command is not on PATH.
            RunnerError: If subprocess creation fails.
        """
        ...
```

**Concrete implementations** (one per output parser format):

| Class | Runner config `output_parser` | Output format |
|-------|------------------------------|---------------|
| `ClaudeRunner` | `claude_json` | Single JSON object (direct `claude -p --output-format json`) |
| `OpenCodeRunner` | `opencode_jsonl` | JSONL stream (step_start, text, step_finish) |
| `CodexRunner` | `codex_jsonl` | JSONL stream (thread.started, item.completed, turn.completed) |
| `GeminiRunner` | `gemini_json` | Single JSON object (experimental) |

Each implementation is constructed from a `RunnerConfig` and contains an output
parser method that converts raw stdout into `RunnerResult`.

**Session resume**: Each runner translates `session_id` into its CLI-specific
resume mechanism (e.g., `--resume UUID` for Claude, `--session SES_ID` for OpenCode,
`exec resume UUID` for Codex). The `RunnerConfig.resume_flag` or `resume_command`
field controls this translation.

---

### 2.9 judgments.py -- Judgment Type Definitions

**Responsibility**: Define the enum of judgment types, their context schemas
(required/optional fields), and the request/verdict data contracts.

**Non-responsibility**: Does NOT invoke the judge. Does NOT contain the judge
system prompt.

```python
from enum import Enum
from dataclasses import dataclass


class JudgmentType(str, Enum):
    PREFLIGHT = "preflight"       # Blueprint #1, #3
    EVIDENCE = "evidence"         # Blueprint #8c, #10
    FAILURE = "failure"           # Blueprint #5, #6, #7
    ESCALATION = "escalation"     # Blueprint #15
    GATE = "gate"                 # Blueprint #17, #19
    ANOMALY = "anomaly"           # Blueprint #11
    COLD_CONTEXT = "cold_context" # Blueprint #18


# Blueprint judgment point -> JudgmentType mapping:
#
# | Blueprint Point | JudgmentType   | When invoked                          |
# |-----------------|----------------|---------------------------------------|
# | #1 Preflight    | PREFLIGHT      | Before dispatching an impl step       |
# | #3 Spec review  | PREFLIGHT      | High-risk step spec adequacy          |
# | #5 Fail classify| FAILURE        | Classifying failure provenance        |
# | #6 Fail dispose | FAILURE        | Determining failure disposition        |
# | #7 Fail gate    | FAILURE        | Gate-intersection reasoning            |
# | #8c Evidence    | EVIDENCE       | Validating worker evidence             |
# | #10 Evidence    | EVIDENCE       | Gate evidence completeness             |
# | #11 Anomaly     | ANOMALY        | Plan/claims anomaly auto-repair check  |
# | #15 Escalation  | ESCALATION     | Repeated failure escalation            |
# | #17 Gate assess | GATE           | Gate/test result handling              |
# | #18 Cold ctx    | COLD_CONTEXT   | Context pruning for gate dispatch      |
# | #19 Gate assess | GATE           | Freeze step handling                   |


# Required and optional context fields per judgment type.
# Used by judge.py to validate requests before invocation.
CONTEXT_SCHEMAS: dict[JudgmentType, dict[str, list[str]]] = {
    JudgmentType.PREFLIGHT: {
        "required": ["step_id", "step_description", "step_verification", "step_refs"],
        "optional": ["risk_signals", "phase_context"],
    },
    JudgmentType.EVIDENCE: {
        "required": ["step_id", "step_verification", "evidence"],
        "optional": ["step_type", "gate_evidence"],
    },
    JudgmentType.FAILURE: {
        "required": ["step_id", "error_output", "failure_count"],
        "optional": ["remaining_gates", "step_description"],
    },
    JudgmentType.ESCALATION: {
        "required": ["step_id", "failure_count", "failure_history", "step_description", "available_agents"],
        "optional": ["runner_failures"],
    },
    JudgmentType.GATE: {
        "required": ["step_id", "gate_evidence", "blocker_issues"],
        "optional": ["remaining_gates", "phase_context"],
    },
    JudgmentType.ANOMALY: {
        "required": ["anomaly_type", "repair_scope"],
        "optional": ["dry_run_recommendation"],
    },
    JudgmentType.COLD_CONTEXT: {
        "required": ["step_id", "step_spec", "diff"],
        "optional": ["verification_criteria", "entry_points"],
    },
}
# Structure: {type: {"required": [...], "optional": [...]}}


@dataclass(frozen=True)
class JudgmentRequest:
    """Input to the judgment agent."""
    type: JudgmentType
    step_id: str
    context: dict[str, str]
    failure_history: list[str]
    plan_summary: str


@dataclass(frozen=True)
class JudgmentVerdict:
    """Output from the judgment agent."""
    verdict: str
    # ACCEPT | REJECT | RETRY | SWITCH_AGENT | REPLAN | DEFER | HALT
    reason: str
    suggested_action: str | None = None
    planner_instruction: str | None = None
```

**Invariant**: All `JudgmentRequest.context` dicts MUST contain all keys listed
in `CONTEXT_SCHEMAS[request.type]["required"]`. The judge module validates this
before invocation and raises `JudgmentError` on violation.

---

### 2.10 judge.py -- Judgment Agent

**Responsibility**: Invoke the unified Judgment Agent (a stateless `claude -p` call)
for decisions that require LLM understanding. Route between rule-based fast paths
and LLM fallback. Parse structured verdicts from the agent's JSON output.

**Non-responsibility**: Does NOT define judgment types (that is `judgments.py`).
Does NOT own the decision of WHEN to call the judge (that is `loop.py`'s
reconcile and dispatch logic).

```python
class Judge:
    """Unified judgment agent. Stateless per call.

    Each invocation spawns a runner process (default: opencode) with
    the judge system prompt and a structured user message. The judge
    returns a JSON verdict. Uses structured output when available
    (see Structured Output Strategy below).
    """

    def __init__(self, config: JudgeConfig, observer: Observer) -> None: ...

    async def judge(self, request: JudgmentRequest) -> JudgmentVerdict:
        """Invoke the judgment agent and return a parsed verdict.

        When config.structured_output is True, uses runner-specific
        structured output to eliminate parse failures (see strategy below).
        Falls back to prompt-guided JSON parsing when structured output
        is disabled or unavailable.

        Raises:
            JudgmentTimeoutError: If the agent does not respond within
                config.timeout seconds.
            JudgmentParseError: If the agent returns unparseable output.
        """
        ...

    def is_enabled(self, judgment_type: JudgmentType) -> bool:
        """Check if a judgment type is enabled in config."""
        ...
```

#### Structured Output Strategy

When `JudgeConfig.structured_output` is `True`, the judge uses runner-specific
mechanisms to guarantee valid JSON verdicts, eliminating parse failures:

| Runner | Mechanism | Verdict extraction |
|--------|-----------|-------------------|
| Claude CLI | `--json-schema <schema_file>` flag | Response `structured_output` field = guaranteed valid JSON |
| OpenCode | `--format json` flag | Parse from JSONL text event (prompt-guided, best-effort) |
| Codex | `--output-schema <schema_file>` flag | Guaranteed structured output |

The JSON schema for the verdict is derived from the `JudgmentVerdict` dataclass
and written to a temp file at judge initialization. When structured output is
disabled (`structured_output: false`), the judge falls back to prompt-guided
JSON parsing from the raw text output.

#### Judge Interaction with Main Loop

The judge is called **synchronously within the async loop** (awaited inline).
It is NOT an event or callback. The calling pattern is:

```
loop.py: handle_dispatch()
    IF config.judge.preflight AND is_impl_step AND has_risk_signals:
        verdict = await judge.judge(preflight_request)
        IF verdict == REPLAN: dispatch_planner(instruction), return
        IF verdict == REJECT: skip step, return
    IF is_gate_step AND config.judge.cold_context:
        verdict = await judge.judge(cold_context_request)
        # Use verdict to prune context for gate dispatch

loop.py: reconcile()
    IF success AND has_verification:
        IF NOT validate_evidence_schema(evidence):  # Rule-based fast path
            verdict = REJECT
        ELSE:
            verdict = await judge.judge(evidence_request)  # LLM path

    IF failure AND count >= 3:
        verdict = await judge.judge(escalation_request)

loop.py: startup recovery
    IF repair_claims() reports anomalies AND config.judge.anomaly:
        verdict = await judge.judge(anomaly_request)
        IF verdict == HALT: shutdown, return
```

**Rationale**: Inline awaiting (not callbacks or events) because:
1. The loop must block on the verdict before proceeding -- the verdict determines
   the next action (dispatch vs skip, accept vs reject)
2. Judge calls are infrequent (~45 per 20-step plan) so the latency (~3s each)
   does not bottleneck the loop
3. Simpler control flow than event-driven alternatives

#### Judge vs Rules Decision Boundary

Reconcile determines when to invoke the judge via a two-tier check:

| Check | Mechanism | Judge involvement |
|-------|-----------|-------------------|
| Evidence schema validation | Rule-based: required fields present, minimum length, YAML parseable | None (fast reject) |
| Evidence content adequacy | Requires understanding: does evidence actually prove verification? | `EVIDENCE` judgment |
| Failure count < 3 | Rule-based threshold | None (automatic retry) |
| Failure count >= 3 | Needs classification: retry? switch agent? replan? | `ESCALATION` judgment |
| Preflight risk signals | Keyword detection: "migration", "CAS", "backward compat" | `PREFLIGHT` judgment |
| Gate blocker vs suggestion | Severity classification in parsed issues | `GATE` judgment for downstream impact |
| Claims/plan anomaly repair | Anomaly type classification: safe auto-repair vs structural | `ANOMALY` judgment |
| Gate dispatch context pruning | Determine cold-context inclusion/exclusion for isolation | `COLD_CONTEXT` judgment |

The boundary is: **rules handle structure; the judge handles semantics.**

#### Explicit Judgment Coverage Matrix

This matrix is normative. Its purpose is to prevent silent narrowing of
`REPLAN`, `GATE`, `FAILURE`, `ESCALATION`, `ANOMALY`, and `COLD_CONTEXT`
obligations during phased implementation.

| Judgment Type | Invocation site | Trigger point | First decision owner | Runtime behavior owner |
|---------------|-----------------|---------------|----------------------|------------------------|
| `PREFLIGHT` | `handle_dispatch()` | Before dispatching an implementation step when `config.judge.preflight` is enabled, the step is implementation-shaped, and risk signals are present | Rules first (`is_impl_step`, risk-signal detection, skip lists), then judge for adequacy semantics | **Later phase**: runtime invocation in `loop.py` + `judge.py`. **This phase** owns only the contract and trigger commitment. |
| `EVIDENCE` | `reconcile()` success path | After worker success when step has verification requirements and rule-based evidence schema checks pass | Rules first (required fields, minimum structure, parseability), then judge for adequacy/content semantics | **Later phase**: runtime invocation and reject/accept handling in `reconcile()`. **This phase** owns only the contract and required trigger point. |
| `FAILURE` | `reconcile()` failure path; gate handling reconciliation | When a failure must be classified for provenance/disposition, especially for gate-intersection reasoning and non-blocking claims | Rules first may collect raw failure facts; judge owns provenance/disposition semantics | **Later phase**: explicit runtime classification path must be added in `reconcile()`/gate handling before any non-blocking or downstream-blocker decision is accepted. **This phase** reserves the obligation and forbids omission. |
| `ESCALATION` | `reconcile()` failure path; `handle_escalate()` | After repeated failure reaches escalation threshold (documented as `count >= 3`) | Rules first (failure counting / threshold), then judge for retry vs switch-agent vs replan vs defer vs halt | **Later phase**: runtime escalation dispatch and verdict handling. **This phase** owns the thresholded trigger commitment and verdict surface. |
| `GATE` | Gate handling / gate-result reconciliation | When parsed gate or freeze evidence contains blocker issues, severity classification ambiguity, downstream-blocker promotion, or freeze hard-block conditions | Rules first may parse issue structure and detect explicit hard-block invariants; judge handles blocker semantics and downstream impact | **Later phase**: runtime gate-assessment path in gate handling/reconcile. **This phase** owns the obligation that gate semantics are not collapsed into pure rule checks. |
| `ANOMALY` | Startup recovery | After `repair_claims()` or orphan recovery surfaces an anomaly and `config.judge.anomaly` is enabled | Rules first identify anomaly shape and candidate repair scope; judge decides safe auto-repair vs halt for structural/semantic corruption | **Later phase**: runtime startup-recovery judgment path in `run()`. **This phase** owns anomaly contract scope and safe-vs-unsafe boundary. |
| `COLD_CONTEXT` | `handle_dispatch()` for gate/freeze steps | Before dispatching a gate/freeze step when cold-context isolation is enabled | Rules first identify gate/freeze step and assemble candidate artifacts; judge decides include/exclude pruning semantics | **Later phase**: runtime context-pruning and gate dispatch assembly. **This phase** owns the isolation obligation and required invocation point. |

##### Ownership Mapping by Phase

| Concern | This phase (`contract-judgment-runtime-minimal`) | Later phase |
|--------|-----------------------------------------------|-------------|
| Judgment vocabulary | Owns the canonical list of judgment types, request/verdict contracts, and context schemas | Must consume without narrowing |
| Invocation commitments | Owns the explicit trigger matrix above | Must implement each committed trigger point |
| Rule-vs-judge boundary | Owns the declared boundary: rules for structure, judge for semantics | Must preserve boundary in code |
| Runtime invocation | Does **not** implement any judge calls in this phase | Owns `loop.py` / `judge.py` runtime behavior |
| Deferred behavior | Must name every deferred runtime path explicitly and keep it bounded | Must implement only the deferred paths named here, not reinterpret scope |

##### Deferred Runtime Behavior (Bounded)

The following behaviors are intentionally deferred out of this phase, but they are
bounded by the coverage matrix and may not be silently dropped:

1. `PREFLIGHT` runtime call and verdict handling in `handle_dispatch()`.
2. `EVIDENCE` runtime call and accept/reject handling in `reconcile()` success path.
3. `FAILURE` provenance/disposition runtime classification before accepting any
   `non_blocking` or `downstream_blocker` conclusion.
4. `ESCALATION` runtime call once repeated-failure threshold is reached.
5. `GATE` runtime assessment for blocker promotion, batched-fix instruction, and
   freeze hard-block evaluation.
6. `ANOMALY` runtime assessment during startup recovery before auto-repair of
   structural or semantic anomalies.
7. `COLD_CONTEXT` runtime pruning before gate/freeze dispatch.

No later phase may remove one of these judgment paths without an explicit ADR or
architecture update to this section.

---

### 2.11 loop.py -- Main Event Loop

**Responsibility**: The top-level async entry point. Owns the decide-execute-wait-reconcile
cycle. Coordinates all other modules. Handles startup recovery and graceful shutdown.

**Non-responsibility**: Does NOT contain runner implementations, prompt templates,
git operations, or judgment logic. Delegates all domain work to the appropriate module.

```python
async def run(config_path: Path) -> None:
    """Main driver entry point.

    1. Load config
    2. Resolve plan_path (config.plan_path or resolve_plan_path())
    3. Initialize runners, judge, observer, session pool, state
    4. Startup recovery:
       a. claims_path = resolve_claims_path(plan_path)
       b. plan = load_plan(plan_path)
       c. repair_claims(plan, plan_path, claims_path)
          -> RepairClaimsResult
       d. cleanup_orphans() for stale worktrees
    5. Enter decide-execute-wait-reconcile loop
    6. Clean shutdown on completion, SIGINT, or SIGTERM
    """
    ...

async def handle_dispatch(
    action: Action,
    state: DriverState,
    config: DriverConfig,
    runners: dict[str, Runner],
    judge: Judge,
    session_pool: SessionPool,
    observer: Observer,
    plan_path: Path,
) -> None:
    """Handle a claim_and_dispatch action from decide().

    1. Optional preflight judgment
    2. Reload plan from disk (CAS-safe)
    3. claim_step(plan, step_id, agent_name,
       claims_path=resolve_claims_path(plan_path))
       -> (Plan, ClaimResult)
    4. Save plan (CAS write)
    5. Create worktree
    6. Resolve runner (with fallback on repeated failures)
    7. Session reuse resolution
    8. Render prompt
    9. Dispatch runner
    10. Register in state
    """
    ...

async def handle_complete(
    action: Action,
    state: DriverState,
    judge: Judge,
    session_pool: SessionPool,
    observer: Observer,
    plan_path: Path,
) -> None:
    """Handle a complete action from decide().

    1. Reload plan from disk (CAS-safe)
    2. complete_step(plan, step_id, evidence,
       claims_path=resolve_claims_path(plan_path))
    3. Save plan (CAS write)

    Legacy-shim disposition (A1 authority):
    - main runtime path **uses** ``wait_for_any() -> reconcile()`` as the sole
      completion sink; ``decide(action="complete")`` is ignored at runtime.
    - this function exists only for internal compatibility; no runtime code path
      invokes it directly.
    """
    ...

async def reconcile(
    completed: CompletedEntry,
    state: DriverState,
    judge: Judge,
    session_pool: SessionPool,
    observer: Observer,
    plan_path: Path,
) -> None:
    """Reconcile a completed runner result.

    CRITICAL: Both paths reload the plan from disk before mutating.
    CAS conflicts (from concurrent external writes) are handled by
    catching CASConflictError, re-loading, and retrying the operation.

    Success path:
    1. Parse evidence
    2. Validate evidence (rules then judge)
    3. Reload plan from disk (CAS-safe)
    4. complete_step(plan, step_id, evidence,
       claims_path=resolve_claims_path(plan_path))
    5. Save plan (CAS write)
    6. Merge worktree (under merge_lock)
    7. Record session for reuse
    8. Cleanup worktree

    Failure path:
    1. Increment failure counter
    2. Reload plan from disk (CAS-safe)
    3. If count < 3: defer_step(plan, step_id,
       claims_path=resolve_claims_path(plan_path))
    4. Save plan (CAS write)
    5. If count >= 3: judge escalation verdict
       -> RETRY | SWITCH_AGENT | REPLAN | HALT
    """
    ...

async def handle_escalate(
    action: Action,
    state: DriverState,
    judge: Judge,
    observer: Observer,
) -> None:
    """Handle an escalate action from decide()."""
    ...

def attempt_recovery(halt_reason: str | None, state: DriverState) -> bool:
    """Attempt programmatic recovery from a halt condition.

    Returns True if recovery succeeded and loop should continue.
    """
    ...

async def dispatch_planner(
    instruction: str,
    config: DriverConfig,
    runners: dict[str, Runner],
    observer: Observer,
    plan_path: Path,
) -> None:
    """Invoke the planner agent to modify the plan.

    Used when a judge verdict is REPLAN. The planner receives
    the instruction from JudgmentVerdict.planner_instruction
    and modifies plan.yaml accordingly.

    Args:
        instruction: The planner instruction from the judge verdict.
        config: Driver config (for runner resolution).
        runners: Available runners.
        observer: Event emitter.
        plan_path: Path to plan.yaml.
    """
    ...

#### REPLAN / Planner Wiring Contract (Bounded)

The following trigger points are the exact ``loop.py`` surfaces allowed to emit
planner dispatch from a judge REPLAN verdict:

| Trigger | Judgment Type | Trigger Surface | Planner Instruction Source | Constraint |
|---------|---------------|-----------------|----------------------------|------------|
| `handle_dispatch.preflight_replan` | `PREFLIGHT` | Before claim/worktree/runner dispatch | `JudgmentVerdict.planner_instruction` | MUST preserve REPLAN as planner-capable; MUST NOT downgrade to reject-only behavior |
| `reconcile.evidence_replan` | `EVIDENCE` | Success path after evidence semantics review | `JudgmentVerdict.planner_instruction` | MUST preserve plan-strengthening semantics; reject-only is insufficient |
| `reconcile.failure_classification_replan` | `FAILURE` | Failure classification before blocker disposition is finalized | `JudgmentVerdict.planner_instruction` | MUST preserve planner path for remediation/decomposition |
| `reconcile.escalation_replan` | `ESCALATION` | Repeated-failure branch once threshold is reached | `JudgmentVerdict.planner_instruction` | MUST NOT collapse REPLAN into retry/switch-agent only |
| `run.startup_recovery_anomaly_replan` | `ANOMALY` | Startup recovery before unsafe auto-repair | `JudgmentVerdict.planner_instruction` | MUST preserve planner-authored repair path; MUST NOT collapse to halt-only |

##### Anti-Narrowing Rule

Later phases MUST treat ``REPLAN`` as a planner-dispatch-capable verdict with a
non-empty ``planner_instruction``. They MUST NOT silently reinterpret REPLAN as
``REJECT``, ``DEFER``, or ``HALT`` without an explicit ADR or architecture update.

##### Planner Instruction Handling

- The judge owns *why* replanning is needed via ``JudgmentVerdict.reason``.
- The judge owns *what to tell the planner* via
  ``JudgmentVerdict.planner_instruction``.
- ``loop.py`` owns routing that instruction to planner dispatch.
- The planner owns plan mutation after receiving the instruction.

##### Deferred Runtime Scope

This architecture step pins the callable surface and trigger matrix only. The
runtime implementation of planner dispatch remains deferred to
`driver-judgment-expansion-replan`. That later phase MUST implement only the
bounded trigger points listed above and may not narrow the set without updating
this section.

async def dispatch_conflict_resolver(
    step_id: str,
    conflict_files: list[str],
    config: DriverConfig,
    runners: dict[str, Runner],
    observer: Observer,
) -> bool:
    """Invoke a conflict resolver agent for non-trivial merge conflicts.

    Returns True if conflicts were resolved, False if resolution failed.

    Args:
        step_id: The step whose merge conflicted.
        conflict_files: List of file paths with conflicts.
        config: Driver config (for runner resolution).
        runners: Available runners.
        observer: Event emitter.
    """
    ...

async def shutdown(state: DriverState, observer: Observer) -> None:
    """Graceful shutdown sequence.

    1. Set state.halt_requested = True
    2. Wait for all running handles (with timeout = max stall_timeout)
    3. Kill any remaining processes
    4. Cleanup orphan worktrees
    5. Emit FINAL event with state.summary()
    6. observer.close()
    """
    ...
```

---

### 2.12 entrypoint.py + runtime entrypoint unification contract

**Responsibility**: Pin one shared adapter surface used by both runtime entrypoints:
`vectl drive` (Typer) and `python -m vectl.driver` (argparse process surface).

**Non-responsibility**: Does NOT implement runtime execution in this phase. This
section is contract-only; implementation is deferred.

```python
class RuntimeEntrypointAdapter(Protocol):
    def normalize_config(self, invocation: EntrypointInvocation) -> Path: ...
    def invoke_async(self, config_path: Path) -> None: ...
    def map_error_to_exit_code(self, error: BaseException) -> int: ...


def run_drive_cli_entrypoint(*, config: Path, adapter: RuntimeEntrypointAdapter) -> int: ...


def run_driver_module_entrypoint(
    *, argv: Sequence[str] | None, adapter: RuntimeEntrypointAdapter
) -> int: ...
```

#### Supported invocation forms (normative)

All forms delegate to `vectl.driver.entrypoint.SharedRuntimeEntrypointAdapter`
for config normalization, async runtime invocation, and exit-code mapping.

| Form | Usage | Notes |
|------|-------|-------|
| `vectl drive --config <path>` | Typer CLI | Config passed as Typer option |
| `python -m vectl.driver <path>` | Module entrypoint | Positional config argument |
| `python -m vectl.driver --config <path>` | Module entrypoint | Optional `--config` flag |

**Argument parsing**: The module entrypoint (`__main__.py`) accepts both:
- Positional `CONFIG` argument (backwards-compatible)
- `--config CONFIG` flag (preferred explicit form)

The two forms are mutually exclusive; combining them results in a usage error.

**Exit code semantics**: Both entrypoints share the same exit-code mapping
via `EntrypointUsageError` classification:

| Category | Exit code |
|----------|-----------|
| Success | `0` |
| Runtime/config execution error | `1` |
| Argument parsing / usage error | `2` |

#### Wiring requirement

Both `src/vectl/cli.py::drive` and `src/vectl/driver/__main__.py::main` MUST
delegate to the same shared adapter path in `src/vectl/driver/entrypoint.py`.

The shared adapter implementation in `entrypoint.py` provides:
- `normalize_config()`: Canonicalizes config path with user-home expansion
- `invoke_async()`: Runs `loop.run(config_path)` via `asyncio.run()`
- `map_error_to_exit_code()`: Maps exceptions to shared exit-code matrix

---

## 3. Data Flow

### 3.1 Main Loop Data Flow

```
                          plan.yaml (on disk, CAS-protected)
                                    |
                                    v
    ┌───────────────── decide() ──────────────────┐
    │  Input:                                      │
    │    running_tasks  <── DriverState.as_running_tasks()
    │    completed      <── DriverState.drain_completed()
    │    max_parallelism <── config                │
    │  Output:                                     │
    │    DecideOutput {actions, continuation}       │
    └──────────────────┬──────────────────────────┘
                       │
                       v
    ┌─── execute_actions (per Action) ────────────┐
    │                                              │
    │  claim_and_dispatch:                         │
    │    Action ──> judge.preflight? ──>           │
    │    reload plan ──> claim_step(plan, ...,     │
    │      claims_path) ──> save_plan (CAS) ──>   │
    │    worktree.create ──> dispatch.render ──>   │
    │    runner.dispatch ──> state.register        │
    │                                              │
  │  complete: (legacy shim)                     │
     │    [A1 contract: ignored at runtime]         │
     │    Runtime uses reconcile() only             │
     │                                              │
    │  wait:                                       │
    │    (no-op, logged)                           │
    │                                              │
    │  escalate:                                   │
    │    Action ──> judge.escalation ──>           │
    │    state update                              │
    └──────────────────┬──────────────────────────┘
                       │
                       v
    ┌─── wait_for_any ────────────────────────────┐
    │  asyncio.wait(handles, FIRST_COMPLETED)      │
    │  Returns: CompletedEntry                     │
    └──────────────────┬──────────────────────────┘
                       │
                       v
    ┌─── reconcile ───────────────────────────────┐
    │  CompletedEntry ──>                          │
    │    success: evidence_validate ──>            │
    │             (rules then judge) ──>           │
    │             reload plan (CAS) ──>            │
    │             complete_step(plan, ...,         │
    │               claims_path) ──>               │
    │             save_plan (CAS) ──>              │
    │             worktree.merge (under lock) ──>  │
    │             session_pool.record ──>          │
    │             worktree.cleanup                 │
    │    failure: increment_failure ──>            │
    │             reload plan (CAS) ──>            │
    │             defer_step(plan, ...,            │
    │               claims_path) ──>               │
    │             save_plan (CAS) ──>              │
    │             (rules or judge) ──>             │
    │             escalate if count >= 3           │
    └──────────────────┬──────────────────────────┘
                       │
                       v
                    (loop back to decide)
```

### 3.2 Data Contract: Runner -> Main Loop

The runner returns data to the loop through a two-stage contract:

**Stage 1: RunnerHandle.wait() -> RunnerResult**

The `RunnerHandle` is an async handle to a subprocess. `wait()` blocks until the
process exits, then the runner's output parser converts raw stdout into a
`RunnerResult`. This happens inside `DriverState.wait_for_any()`.

**Stage 2: RunnerResult -> CompletedEntry**

`DriverState` wraps the `RunnerResult` with bookkeeping (step_id, agent, runner_name,
worktree_path, elapsed time) into a `CompletedEntry`. This is what `reconcile()` receives.

**Stage 3: CompletedEntry -> CompletedResult (for decide())**

When the loop calls `decide()` on the NEXT iteration, `DriverState.drain_completed()`
converts `CompletedEntry` instances into `vectl.models.CompletedResult` instances,
which is the contract `decide()` expects. The mapping is:

```
CompletedResult.step_id     = CompletedEntry.step_id
CompletedResult.task_id     = CompletedEntry.result.session_id or ""
CompletedResult.status      = "SUCCESS" if RunnerStatus.SUCCESS else "FAIL"
CompletedResult.output_summary = CompletedEntry.result.output[:500]
```

---

## 4. State Ownership Matrix

| State | Owner Module | Mutated By | Read By | Lifetime |
|-------|-------------|------------|---------|----------|
| `DriverState.running` | `types.py` (defined), `loop.py` (managed) | `loop.py` dispatch + reconcile | `loop.py`, `decide()` (via `as_running_tasks()`) | Process |
| `DriverState.completed_queue` | `types.py` / `loop.py` | `wait_for_any()` | `drain_completed()` (legacy transitional seam) | Per-iteration |
| `DriverState.failure_counts` | `types.py` / `loop.py` | `reconcile()` | `reconcile()`, `dispatch()` | Process |
| `DriverState.merge_lock` | `types.py` | `reconcile()` (acquire/release) | `reconcile()` | Process |
| `DriverState.decide_state` | `types.py` (defined), `decide()` | `decide()` internally | `decide()` via explicit parameter | Process |
| `SessionPool._entries` | `session.py` | `reconcile()` via `record()` | `dispatch()` via `find_reusable()` | Process |
| `plan.yaml` | `vectl.io` | `loop.py` via `save_plan()` | `decide()` via `load_plan_definition()`, `loop.py` | Durable (disk) |
| `claims.json` | `vectl.claims` | `claim_step()`, `complete_step()`, `defer_step()` | `repair_claims()` | Durable (disk) |
| `events.jsonl` | `observe.py` | `observer.emit()` | External tools only | Durable (disk, append-only) |
| Git worktrees | `worktree.py` | `create()`, `merge()`, `cleanup()` | `dispatch()` (path), `reconcile()` (merge) | Per-step |

### Completion Authority (A1 Contract)

Runtime completion authority is **reconcile-only**. The sole completion sink is:
```
wait_for_any() → reconcile()
```

`handle_complete()` is legacy/internal compatibility only and MUST NOT be invoked
from the main runtime path. `decide(action="complete")` is ignored at runtime.
This is enforced by `loop.COMPLETION_AUTHORITY_A1_CONTRACT`.

---

## 5. Specific Design Questions Answered

### Q1: How does the Judge module interact with the main loop?

**Answer**: Synchronous inline `await` calls. The judge is NOT a callback, event
emitter, or background service. The loop calls `await judge.judge(request)` at
specific decision points (preflight, evidence validation, escalation, gate assessment)
and blocks on the verdict before proceeding. This is appropriate because:

- Verdicts determine the immediate next action (no async decoupling benefit)
- Judge calls are infrequent (~45 per 20-step plan, ~3s each)
- Control flow remains linear and debuggable

See Section 2.10 for the full interaction pattern.

### Q2: What is the exact data contract between Runner and the main loop?

**Answer**: Three-stage pipeline:

1. `Runner.dispatch()` returns `RunnerHandle` (async handle to subprocess)
2. `RunnerHandle.wait()` returns `RunnerResult` (parsed output with status, session_id, output text, cost)
3. `DriverState` wraps `RunnerResult` into `CompletedEntry` (adds step_id, agent, runner_name, worktree_path)
4. On next `decide()` call, `drain_completed()` converts to `CompletedResult` (vectl.models contract)

See Section 3.2 for the full mapping.

### Q3: How does reconcile() know when to call Judge vs when rules suffice?

**Answer**: Two-tier decision boundary:

- **Rules handle structure**: evidence schema validation (required fields, minimum length, YAML parse), failure count thresholds (< 3 = auto retry), preflight risk keyword detection
- **Judge handles semantics**: evidence content adequacy (does it prove the verification criteria?), failure escalation (retry same? switch agent? replan?), gate downstream impact assessment

The boundary is hard-coded in `reconcile()` as a conditional chain:
1. Rule check first (fast, deterministic)
2. If rules pass but semantic judgment is needed AND the judgment type is enabled in config, invoke judge
3. If judge is disabled for that type, or judge times out, accept optimistically with a warning log

See Section 2.10, "Judge vs Rules Decision Boundary" table.

### Q4: What is the ownership boundary between SessionPool and DecideState?

**Answer**: They are parallel, non-overlapping mechanisms:

- `DecideState` is passed explicitly to `decide()` via the `state` parameter.
  The driver does NOT write to decide-memory directly; it feeds data through
  the `CompletedResult` contract.
- `SessionPool` is driver-internal state, populated by `reconcile()` after
  successful merge. It adds runner-name matching and per-runner TTL that
  `decide()` cannot express.
- The driver feeds completed results through `CompletedResult` to keep `decide()`
  state current via the explicit `DecideState` parameter.
- If `decide()` produces `session="reuse"` with a `task_id`, the driver uses
  that. If `session="fresh"`, the driver MAY supplement with `SessionPool.find_reusable()`.

See Section 2.5 ownership boundary table.

### Q5: How do prompt templates get the data they need?

**Answer**: The `Action` dataclass from `decide()` carries `step_description`,
`step_verification`, and `step_refs` (populated from the `Step` model during
`decide()`). The driver passes these directly to `dispatch.render_prompt()`.
Additional context comes from:

- `DriverState.get_failure_context(step_id)` -- previous failure output for retry prompts
- `Plan.context` / `Phase.context` -- loaded from plan.yaml, passed as plan/phase context
- `worktree_path` -- from the just-created worktree

No additional plan reads or LLM calls are needed for prompt rendering. The prompt
is a deterministic string template, not LLM-generated.

---

## 6. Trade-offs

### Accepted trade-offs

| Decision | Gain | Cost |
|----------|------|------|
| Parallel SessionPool alongside DecideState | Runner-aware reuse without modifying frozen core | Two session tracking mechanisms; slight conceptual overhead |
| Inline `await judge.judge()` instead of event-driven | Linear control flow, easy debugging | Judge latency blocks the current action (acceptable: ~3s, infrequent) |
| All state in DriverState (no persistence) | Simplicity, crash-restart-safe (plan.yaml + git are durable) | Lose failure history on crash (acceptable: restart re-evaluates from plan state) |
| Rule-based evidence validation before judge | Fast rejection of obviously bad evidence, saves ~40% of judge calls | Rules may reject borderline-valid evidence; config toggle mitigates |
| Single merge_lock for all worktree merges | Correct serialization, simple | Sequential merge bottleneck under high parallelism (acceptable: merges are fast, ~1s) |
| Templates as inline strings, not external files | Single source of truth, no file loading, easy to audit | Harder to customize without code changes (acceptable: driver is developer-facing) |
| Single-dependency session reuse only | Simple, deterministic reuse logic; covers majority of linear chains (contract -> impl -> test) | Steps with 0 or 2+ dependencies always get fresh sessions; no multi-dep heuristic |

### Risks

| Risk | Likelihood | Mitigation |
|------|-----------|------------|
| Judge LLM produces unparseable output | Low (with structured_output), Medium (without) | Structured output via `--json-schema`/`--output-schema` eliminates most parse failures; fallback: timeout + parse error -> accept optimistically, log warning |
| `decide()` state drifts from DriverState | Low | Driver is single-threaded; state fed exclusively through CompletedResult contract |
| Worktree accumulation on repeated crashes | Medium | Startup `cleanup_orphans()` + `repair_claims()` |
| Runner stall not detected | Low | Real PID check + configurable stall_timeout per runner |

---

## 7. Implementation Handoff

**Design scope**: `vectl/driver/` subpackage -- 13 modules, ~1250 lines estimated.

**Key deliverables**:
- Module map with dependency arrows (Section 1)
- Protocol definitions for Runner, RunnerHandle, Observer (Sections 2.4, 2.8)
- Data model for all driver-internal types (Section 2.1)
- Error hierarchy with propagation rules (Section 2.2)
- Config schema (Section 2.3)
- State ownership matrix (Section 4)

**Implementation order** (suggested):

1. **errors.py + types.py** -- Foundation types with no dependencies. Everything else imports these.
2. **config.py** -- Config loading. Needed by all modules that read configuration.
3. **observe.py** -- Event emitter. Lightweight, needed by everything that logs.
4. **session.py** -- Session pool. Small, self-contained, tested in isolation.
5. **worktree.py** -- Git worktree lifecycle. Depends only on types/errors. Can be tested with real git repos.
6. **dispatch.py** -- Prompt templates. Depends only on types/config. Pure string rendering, easy to test.
7. **parsers.py** -- Output parsers. Depends only on types. Converts raw runner stdout to RunnerResult.
8. **judgments.py** -- Judgment type definitions. Pure data, no logic.
9. **runners.py** -- Runner Protocol + implementations. Depends on types, errors, config, dispatch, parsers. Requires real CLI binaries for integration tests; unit-test with mock processes.
10. **judge.py** -- Judgment agent. Depends on judgments, config, observe. Test with mocked subprocess.
11. **loop.py** -- Main loop. Wires everything together. Integration test against a real plan.yaml.
12. **__main__.py + CLI integration** -- Entry points. Last because they just wire to `loop.run()`.

**Watch for**:
- `decide()` loads plan.yaml internally via `resolve_plan_path()`. The driver MUST ensure the working directory is correct (the project root, not a worktree) when calling `decide()`.
- `save_plan()` uses `fcntl.flock` -- single-process safety only. Since the driver is the sole writer in its process, this is fine, but be aware that external `vectl` CLI calls during a drive could hit CAS conflicts.
- The `merge_lock` MUST be a real `asyncio.Lock`, not a `threading.Lock` -- the loop is single-threaded async, not multi-threaded.
- `Runner.dispatch()` implementations MUST set `stdin=PIPE` and write the prompt, then close stdin. Do not leave stdin open or the subprocess will hang.
- The default judge runner is `opencode`. When using `claude` as judge runner, add `--no-session-persistence` and `--dangerously-skip-permissions`. Each runner uses its own non-interactive flags (opencode: `--format json`; codex: `--dangerously-bypass-approvals-and-sandbox`; gemini: `--approval-mode yolo`).
- Session reuse is implemented in `session.py` with runner-aware matching and per-runner TTL overrides.

**Open questions**:
- ~~Exact system prompt text for the Judgment Agent~~ -- Resolved: see `JUDGE-AGENT-PROMPT.md`.
- Whether `gemini` runner should be included in Phase 1 or deferred (marked `experimental: true` in blueprint). Recommend deferring to avoid unverified auth issues blocking Phase 1.
- Exact risk-signal keyword list for preflight judgment routing. Implementation detail, but should be extracted from production orchestrator logs for accuracy.

**NOT addressed** (explicitly out of scope per Section 0):
- Implementation code for any module
- Algorithm for loop detection (window-based signature comparison)
- Exact JSONL event format (field names, nesting)
- Template content for dispatch prompts
- Test strategy and test file layout
