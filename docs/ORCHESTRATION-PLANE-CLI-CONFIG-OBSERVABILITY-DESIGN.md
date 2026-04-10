# Orchestration Plane CLI, Config, and Observability Design

> Canonical specification for orchestration-plane CLI commands, configuration system, and observability contracts.

**Status:** Design specification  
**Architecture authority:** `docs/ORCHESTRATION-PLANE-ARCHITECTURE.md`, `docs/ADR-orchestration-role-profile-config-and-resolver-cleanup.md`  
**Implementation authority:** `docs/ORCHESTRATION-PLANE-IMPLEMENTATION-DESIGN.md`  
**Related docs:** `docs/ORCHESTRATION-PLANE-RESOLUTION-CONTRACT.md`, `docs/ORCHESTRATION-PLANE-ISOLATION-SEMANTICS.md`

---

## 1. Purpose

This document defines:

1. The CLI command surface for the `vectl orch` command group
2. The configuration system (loading, precedence, validation, frozen snapshots)
3. Observability contracts (events, projection, artifact schemas)

---

## 2. Scope

### 2.1 In Scope

- CLI command families and their arguments
- Configuration file format and discovery order
- Environment variable override mechanism
- Tool registry and allowlist validation
- Frozen configuration snapshots
- Event envelope schema and integrity
- Projection replay and state artifacts
- Per-step and per-case artifact schemas

### 2.2 Out of Scope

- Legacy `vectl` core commands (status, show, claim, etc.)
- Runner-specific configuration
- LLM invocation details

---

## 3. Design Principles

1. **Explicit over implicit**: All config sources must be traceable
2. **Deny-by-default**: Tool authorization requires explicit allowlist
3. **Immutable snapshots**: Run configuration is frozen at start time
4. **Observable by design**: All state changes emit events
5. **Mechanical sympathy**: Projection replay must be deterministic
6. **Role profiles are config-owned**: orchestration code must not invent
   ordinary role profiles in code

---

## 4. Document References

This document uses section-based citations. The following sections define canonical references:

- Section 7: CLI Command Surface
- Section 8: Configuration System
- Section 9: Observability Contracts
- Section 10: Event Registry

---

## 5. CLI Overview

The `vectl orch` command group provides orchestration-plane operations.

### 5.1 Command Group Structure

```
vectl orch
├── run [STEP_ID]                 # Start a new orchestrated run (auto-selects next if omitted)
├── resume [RUN_ID|--latest]      # Resume an existing run
├── recover [RUN_ID|--latest]     # Recover and diagnose a run
├── runs [--status STATUS]        # List runs
├── prune [--older-than DAYS]     # Clean up old runs
├── status [RUN_ID]               # Show run status
├── events [RUN_ID]               # Show event stream
├── logs [--run RUN_ID]           # Show logs
├── artifacts [RUN_ID]            # List artifacts
├── actions [--run RUN_ID]        # Show pending actions
├── case-list [RUN_ID]            # List cases
├── case-show <CASE_ID>           # Show case details
├── case-respond <CASE_ID>        # Respond to a case
├── pause [RUN_ID]                # Queue a pause request for run dispatch
├── unpause [RUN_ID]              # Queue an unpause request for run dispatch
├── stop [RUN_ID]                 # Queue a stop request for a run
├── config-show                   # Show current config (or expanded config with --effective)
├── config-validate [PATH]        # Validate current orchestration config
└── config-tools                  # List canonical tools

# Subcommand variants (equivalent to flat commands above)
vectl orch inspect status
vectl orch inspect events
vectl orch inspect logs
vectl orch inspect artifacts
vectl orch inspect actions
vectl orch case list
vectl orch case show
vectl orch case respond
vectl orch control pause
vectl orch control unpause
vectl orch control stop
vectl orch config show
vectl orch config validate
vectl orch config tools
vectl orch migration validate-cutover
vectl orch migration advance-state
```

### 5.2 Common Per-Command Flags

Most `vectl orch` subcommands accept one or more of these flags:

- `--plan PATH`: Explicit plan/config target path
- `--json`: Output as JSON (where applicable)
- `--jsonl`: Output as JSON Lines (streaming surfaces only)

These are subcommand flags, not top-level `vectl orch` global flags.

---

## 6. Exit Codes

| Code | Meaning |
|------|---------|
| 0 | Success |
| 1 | Error (general) |
| 2 | Not found |
| 3 | Validation error |
| 4 | Recovery required |
| 5 | Internal error |
| 127 | Command not registered |

---

## 7. CLI Command Surface

### 7.1 Run Lifecycle Family

#### `vectl orch run [STEP_ID]`

Start or resume an orchestration run.

**Arguments:**
- `STEP_ID`: Step ID to run (optional, auto-selects next if omitted)

**Flags:**
- `--agent AGENT`: Agent name for execution
- `--dry-run`: Validate without executing
- `--json`: Output run ID as JSON
- `--output MODE`: Output mode (human, json, jsonl)
- `--plan PATH`: Path to the plan/config target

**Behavior:**
1. Load and validate configuration
2. Freeze config and write snapshot
3. Initialize run artifacts
4. Begin execution

**Exit codes:** 0, 1, 3, 5

---

#### `vectl orch resume [RUN_ID|--latest]`

Resume an existing run.

**Arguments:**
- `RUN_ID`: Run identifier (optional if `--latest`)

**Flags:**
- `--latest`: Use most recently updated non-terminal run
- `--dry-run`: Validate without resuming
- `--json`: Output as JSON
- `--output MODE`: Output mode (human, json, jsonl)
- `--plan PATH`: Path to the plan/config target

**Behavior:**
1. Resolve run ID (explicit or --latest)
2. Load frozen config snapshot
3. Restore projected state via replay
4. Resume dispatch

**Exit codes:** 0, 1, 2, 4, 5

---

#### `vectl orch recover [RUN_ID|--latest]`

Recover orchestration state from continuity artifacts.

**Arguments:**
- `RUN_ID`: Run identifier (optional if `--latest`)

**Flags:**
- `--latest`: Use most recently updated run
- `--step STEP_ID`: Step ID to recover
- `--dry-run`: Diagnose without modifying
- `--json`: Output diagnostic report
- `--output MODE`: Output mode (human, json, jsonl)
- `--plan PATH`: Path to the plan/config target
- `--yes, -y`: Skip confirmation prompt

**Behavior:**
1. Load run state
2. Validate event stream integrity
3. Detect gaps or corruption
4. Report or repair / resume from durable artifacts

**Exit codes:** 0, 1, 2, 4, 5

---

#### `vectl orch runs [--status STATUS]`

List runs from the run store index.

**Flags:**
- `--step STEP_ID`: Filter by step ID
- `--status STATUS`: Filter by status (running, paused, completed, failed)
- `--limit N, -n N`: Maximum runs to show (default: 100)
- `--json`: Output as JSON array
- `--output MODE`: Output mode (human, json, jsonl)
- `--plan PATH`: Path to the plan/config target

**Output columns:**
- Run ID
- Plan path
- Status
- Started at
- Updated at

---

#### `vectl orch prune [--older-than DAYS]`

Prune old runs and artifacts.

**Flags:**
- `--older-than DAYS`: Only remove runs older than N days
- `--before TIMESTAMP`: Unix timestamp threshold
- `--dry-run`: Show what would be removed
- `--force, -y`: Skip confirmation prompt
- `--json`: Output report as JSON
- `--output MODE`: Output mode (human, json, jsonl)
- `--plan PATH`: Path to the plan/config target

**Behavior:**
1. Query run index for candidates
2. Apply retention policy
3. Remove prunable artifact directories
4. Update index

---

### 7.2 Inspect Family

#### `vectl orch status [RUN_ID|--latest]`

Show projected run status.

**Flags:**
- `--latest`: Use most recently updated run
- `--step STEP_ID`: Filter by step ID
- `--watch`: Poll for updates
- `--json`: Output as JSON
- `--output MODE`: Output mode (human, json, jsonl)
- `--plan PATH`: Path to the plan/config target

**Output:**
- Run ID
- Status
- Active step (if any)
- Open case count
- Last event sequence

---

#### `vectl orch events [RUN_ID|--latest]`

Inspect orchestration events.

**Flags:**
- `--latest`: Use most recently updated run
- `--step STEP_ID`: Filter by step ID
- `--follow`: Tail for new events
- `--limit N, -n N`: Maximum events to show (default: 100)
- `--jsonl`: Output as JSON Lines
- `--json`: Output as JSON
- `--output MODE`: Output mode (human, json, jsonl)
- `--plan PATH`: Path to the plan/config target

**Output:** Event envelopes as JSON Lines

---

#### `vectl orch logs [--run RUN_ID|--latest]`

Show operator-friendly logs.

**Flags:**
- `--run RUN_ID`: Specific run ID
- `--latest`: Use most recently updated run
- `--step STEP_ID`: Filter by step ID
- `--tail N`: Show last N lines (default: 100)
- `--follow`: Follow log updates
- `--json`: Output as JSON
- `--output MODE`: Output mode (human, json, jsonl)
- `--plan PATH`: Path to the plan/config target

---

#### `vectl orch artifacts [RUN_ID|--latest]`

List artifact paths and layout.

**Flags:**
- `--latest`: Use most recently updated run
- `--step STEP_ID`: Filter by step ID
- `--kind KIND`: Filter by artifact kind
- `--json`: Output as JSON
- `--output MODE`: Output mode (human, json, jsonl)
- `--plan PATH`: Path to the plan/config target

---

#### `vectl orch actions [--run RUN_ID|--latest]`

Show pending/applied/rejected operator requests.

**Flags:**
- `--run RUN_ID`: Specific run ID
- `--latest`: Use most recently updated run
- `--status STATUS`: Filter by status
- `--json`: Output as JSON
- `--output MODE`: Output mode (human, json, jsonl)
- `--plan PATH`: Path to the plan/config target

---

### 7.3 Case Family

#### `vectl orch case-list [RUN_ID|--latest]`

List open or historical cases.

**Flags:**
- `--latest`: Use most recently updated run
- `--status STATUS`: Filter by status (open, resolved, halt)
- `--watch`: Poll for updates
- `--json`: Output as JSON
- `--output MODE`: Output mode (human, json, jsonl)
- `--plan PATH`: Path to the plan/config target

**Alias:** `vectl orch case list`

---

#### `vectl orch case-show <CASE_ID>`

Inspect one case bundle.

**Arguments:**
- `CASE_ID`: Case identifier

**Flags:**
- `--watch`: Poll for updates
- `--json`: Output as JSON
- `--output MODE`: Output mode (human, json, jsonl)
- `--plan PATH`: Path to plan.yaml

**Alias:** `vectl orch case show`

---

#### `vectl orch case-respond <CASE_ID> [--action ACTION]`

Submit bounded operator response.

**Arguments:**
- `CASE_ID`: Case identifier

**Flags:**
- `--action ACTION`: Response action token
- `--data JSON`: Response payload
- `--reason TEXT`: Human-readable reason
- `--response TEXT`: Legacy response text alias
- `--json`: Output as JSON
- `--output MODE`: Output mode (human, json, jsonl)
- `--plan PATH`: Path to plan.yaml

**Alias:** `vectl orch case respond`

**Note:** Requires either `--action` or `--response` flag

---

### 7.4 Control Family

#### `vectl orch pause [RUN_ID|--latest]`

Queue a pause request so dispatch can stop.

**Flags:**
- `--latest`: Use most recently updated run
- `--step STEP_ID`: Specific step to pause
- `--reason TEXT`: Pause reason
- `--json`: Output as JSON
- `--output MODE`: Output mode (human, json, jsonl)
- `--plan PATH`: Path to the plan/config target

**Alias:** `vectl orch control pause`

---

#### `vectl orch unpause [RUN_ID|--latest]`

Queue an unpause request so dispatch can resume.

**Flags:**
- `--latest`: Use most recently updated run
- `--step STEP_ID`: Specific step to unpause
- `--reason TEXT`: Unpause reason
- `--json`: Output as JSON
- `--output MODE`: Output mode (human, json, jsonl)
- `--plan PATH`: Path to the plan/config target

**Alias:** `vectl orch control unpause`

---

#### `vectl orch stop [RUN_ID|--latest]`

Queue a stop request for a run.

**Flags:**
- `--latest`: Use most recently updated run
- `--reason TEXT, -r TEXT`: Stop reason
- `--force`: Immediate stop request token
- `--json`: Output as JSON
- `--output MODE`: Output mode (human, json, jsonl)
- `--plan PATH`: Path to the plan/config target

**Behavior:**
1. Resolve run ID
2. Persist a `control.stop` request for the selected run
3. Return success once the request is queued; terminal state change is asynchronous

**Alias:** `vectl orch control stop`

---

### 7.5 Config Family

#### `vectl orch config-show`

Show current orchestration configuration.

**Flags:**
- `--effective`: Show expanded effective merged config
- `--json`: Output as JSON
- `--output MODE`: Output mode (human, json, jsonl)
- `--plan PATH`: Path to the plan/config target

**Alias:** `vectl orch config show`

---

#### `vectl orch config-validate [PATH]`

Validate current orchestration configuration.

**Arguments:**
- `PATH`: Optional path argument mapped to the plan/config target used for validation

**Flags:**
- `--json`: Output as JSON
- `--output MODE`: Output mode (human, json, jsonl)
- `--plan PATH`: Path to the plan/config target

**Behavior:**
1. Resolve the plan/config target from `PATH` or `--plan`
2. Load effective orchestration configuration
3. Validate configuration rules
4. Report pass/fail

**Exit codes:** 0 (valid), 3 (validation error)

**Alias:** `vectl orch config validate`

---

#### `vectl orch config-tools`

List canonical tool registry for allowlist authoring.

**Flags:**
- `--json`: Output as JSON
- `--output MODE`: Output mode (human, json, jsonl)
- `--plan PATH`: Path to the plan/config target

**Alias:** `vectl orch config tools`

---

### 7.6 Migration Family

#### `vectl orch migration validate-cutover`

Validate orchestration-plane cutover readiness.

**Flags:**
- `--json`: Output as JSON
- `--output MODE`: Output mode (human, json, jsonl)
- `--plan PATH`: Path to plan.yaml

**Alias:** `vectl orch cutover-validate`

---

#### `vectl orch migration advance-state`

Advance migration state for a legacy run.

**Flags:**
- `--run RUN_ID`: Run ID to advance (required)
- `--to STATE`: Target migration state (parallel, preferred, deprecated, retired)
- `--json`: Output as JSON
- `--output MODE`: Output mode (human, json, jsonl)
- `--plan PATH`: Path to plan.yaml

---

## 8. Configuration System

### 8.1 Config File Discovery Order

Configuration is discovered in this priority order (first wins):

1. **Explicit**: `--config PATH` CLI flag
2. **Environment**: `VECTL_CONFIG` environment variable
3. **CWD Local**: `./vectl.yaml` in current working directory
4. **Repo Root**: `vectl.yaml` at repository root
5. **User Config**: `~/.config/vectl/vectl.yaml`

If no config file is found, built-in defaults are used.

However, ordinary role-profile definitions remain configuration-owned. The core
may not synthesize ordinary role profiles as hidden code defaults.

### 8.2 Config Precedence

Once discovered, configuration values are merged with this precedence (highest first):

1. **CLI flags** (not modeled in config system; caller applies)
2. **Environment variables** (`VECTL_ORCH_*`)
3. **Config file** (discovered per §8.1)
4. **Built-in defaults**

### 8.3 Environment Variable Format

Environment variables use the prefix `VECTL_ORCH_`.

**Format:**
```
VECTL_ORCH_<COMPONENT>_<SETTING>
```

**Examples:**
```bash
VECTL_ORCH_RUNTIME_ARTIFACT_ROOT=/var/vectl/runs
VECTL_ORCH_RESOLVER_ENABLED=false
VECTL_ORCH_OBSERVABILITY_RETENTION_DAYS=7
```

**Key transformation:**
- Remove prefix `VECTL_ORCH_`
- Lowercase
- Underscores become dots for nesting: `RUNTIME_ARTIFACT_ROOT` → `runtime.artifact_root`

### 8.4 Config File Schema

```yaml
orchestration:
  plan_path: plan.yaml

  role_profiles:
    python-executor:
      agent_id: python-executor
      prompt_family: coder
      execution_context: linked_worktree
      mutation_policy: worktree_changes
      session_policy: reuse_allowed
      output_contract: freeform_evidence
      default_runner: codex
    blocked-case-coordinator:
      agent_id: blocked-case-coordinator
      prompt_family: resolver
      execution_context: main_worktree
      mutation_policy: vectl_facade_only
      session_policy: reuse_forbidden
      output_contract: resolution_report
      default_runner: codex
    blocked-case-coordinator-tacit:
      agent_id: blocked-case-coordinator-tacit
      prompt_family: resolver
      execution_context: main_worktree
      mutation_policy: vectl_facade_only
      session_policy: reuse_forbidden
      output_contract: resolution_report
      default_runner: codex
  defaults:
    ordinary_role: python-executor
    resolver_role: blocked-case-coordinator

  roster:
    default_ttl_seconds: 300.0
    max_reuse_window_seconds: 600.0
  
  runtime:
    default_runner: codex
    artifact_root: .vectl/runs
    workspace_root: .vectl/workspaces
    isolation_default: default
    cleanup_policy: on-success
  
  control:
    idle_poll_interval_ms: 1000
    max_resolution_attempts_per_case: 1
    action_ack_timeout_seconds: 5.0
  
  resolver:
    enabled: true
    timeout_seconds: 600.0
    max_tool_calls_per_invocation: 100
    max_tool_argument_bytes: 65536
    tool_allowlist:
      core: [status, show, claim, complete, defer]
      orchestration: [read_events, read_state, read_case]
  
  continuity:
    resume_enabled: true
    stale_artifact_policy: quarantine
    replay_safety: conservative
  
  observability:
    events_jsonl: true
    text_log: true
    projected_state: true
    heartbeat_stale_threshold_seconds: 120
    per_step_artifacts: true
    per_case_artifacts: true
    redact_env_keys: [OPENAI_API_KEY, ANTHROPIC_API_KEY]
    max_log_megabytes: 100
    retention_days: 30
  
  operator:
    control_channel: filesystem
    default_output: human
    max_pending_actions: 100
```

### 8.5 Validation Rules

Configuration is validated against these rules:

| Field | Rule |
|-------|------|
| `runtime.cleanup_policy` | Must be: `never`, `on-success`, `always` |
| `runtime.isolation_default` | Must be: `default`, `workspace`, `independent` |
| `resolver.timeout_seconds` | Must be > 0 |
| `resolver.max_tool_calls_per_invocation` | Must be > 0 |
| `resolver.max_tool_argument_bytes` | Must be > 0 |
| `control.action_ack_timeout_seconds` | Must be > 0 |
| `observability.heartbeat_stale_threshold_seconds` | Must be > 0 |
| `observability.retention_days` | Must be >= 0 |
| `observability.max_log_megabytes` | Must be > 0 |
| `operator.max_pending_actions` | Must be > 0 |
| `resolver.tool_allowlist` | Entries must validate against canonical registry |
| `orchestration.role_profiles` | Must be present for any role used by orchestration |
| `orchestration.defaults.resolver_role` | Must be `blocked-case-coordinator` or `blocked-case-coordinator-tacit` |

### 8.6 Tool Registry

The canonical tool registry defines authorized tool families for resolver use.

**Registry Table:**

| Family | Tools |
|--------|-------|
| `core` | `status`, `show`, `claim`, `complete`, `defer` |
| `orchestration` | `read_events`, `read_state`, `read_case` |

**Allowlist Format:**

```yaml
resolver:
  tool_allowlist:
    core: [status, show, claim, complete, defer]
    orchestration: [read_events, read_state, read_case]
```

Empty allowlist means deny-all (deny-by-default).

**Validation:**
- Unknown families are rejected
- Unknown tools for a family are rejected
- Wildcard patterns are rejected

### 8.7 Provenance Tracking

Configuration values carry provenance metadata indicating their source.

**Sources:**
- `flag`: CLI flag override
- `env`: Environment variable
- `file`: Config file
- `default`: Built-in default

This enables `--effective` output showing where each value originated.

### 8.8 Frozen Config Snapshots

At run start, configuration is frozen and written to:

```
.vectl/runs/<run_id>/config.snapshot.yaml
```

**Purpose:**
- Immutable record of run configuration
- Authoritative for resume operations
- Reproducibility

**Format:**
Same as config file schema, with all values fully resolved (no env references).

Historical `plan.yaml` remains untouched run input/history. It is not
authoritative for role-profile vocabulary or orchestration contract terms.

**Metadata:**
- `created_at`: Timestamp when snapshot was created
- `snapshot_path`: Path where written (optional)

---

## 9. Observability Contracts

### 9.1 Path Normalization

Step IDs are normalized to filesystem-safe `step_key`:

- Percent-encode bytes outside `[A-Za-z0-9._-]`
- Preserve case
- Lossless decode

**Examples:**
- `build/core` → `build%2Fcore`
- `test.step` → `test.step`
- `UPPER.CASE` → `UPPER.CASE`

### 9.2 Projection Replay

#### 9.2.1 State Artifacts

Projection replays events into derived state files:

**`state/latest.json`**
- Full projected state
- Includes: run_id, status, active_step_id, open_case_count, etc.

**`state/summary.json`**
- Reduced operator summary
- Includes: version, run_id, status, counters

**`state/metrics.json`**
- Runtime metrics
- Includes: dispatch_count, resolution_count, etc.

#### 9.2.2 Replay Semantics

1. Events are replayed in `seq` order
2. Each event handler updates projection state
3. Final state is written to `state/latest.json`
4. On resume, state is restored via replay

### 9.3 Event Envelope Schema

#### 9.3.1 Envelope Fields

```json
{
  "seq": 42,
  "prev_hash": "sha256:...",
  "entry_hash": "sha256:...",
  "timestamp": "2024-01-15T10:30:00Z",
  "kind": "control_dispatch",
  "step_id": "build.compile",
  "run_id": "01ABC...",
  "payload": {...}
}
```

**Fields:**

| Field | Type | Description |
|-------|------|-------------|
| `seq` | `int` | Monotonic sequence number |
| `prev_hash` | `string \| null` | Hash of previous event |
| `entry_hash` | `string` | Hash of this event's canonical JSON |
| `timestamp` | `ISO8601` | Event emission time |
| `kind` | `string` | Event type discriminator |
| `step_id` | `string \| null` | Associated step (null for run-level) |
| `run_id` | `string` | Associated run |
| `payload` | `object` | Event-specific data |

#### 9.3.2 Hash Algorithm

- Algorithm: SHA-256
- Input: Canonical JSON serialization (sorted keys, no whitespace)
- Output: Hex-encoded 64-character string

#### 9.3.3 Step Artifact Schemas

**`steps/<step_key>/request.json`**
- Execution request details
- step_id, session_id, runner, context

**`steps/<step_key>/result.json`**
- Execution result
- step_id, status, output_summary, artifacts

#### 9.3.4 Case Artifact Schemas

**`cases/<case_id>/case.json`**
- ResolutionCase canonical form
- case_id, step_id, status, prompt_ref

**`cases/<case_id>/prompt.json`**
- Resolver invocation payload
- case_id, prompt/prompt_hash, allowed_tools, snapshots

**`cases/<case_id>/capability.json`**
- Runner capability snapshot
- runner_id, capability_version, supports_resume, supports_restart

**`cases/<case_id>/report.json`**
- ResolutionReport canonical form

**`cases/<case_id>/transcript.jsonl`**
- Canonical transcript with integrity fields
- seq, prev_hash, entry_hash, tool_call_id

**`cases/<case_id>/transcript.log`**
- Human-readable rendering (derived from jsonl)

---

## 10. Event Registry

### 10.1 Event Kinds (Taxonomy)

**Run Family:**
- `run_started`: Run initialization
- `run_status_changed`: Status transition
- `run_final`: Run completion

**Control Family:**
- `control_dispatch`: Work dispatched
- `control_wait`: Waiting for condition
- `control_ack`: Action acknowledged
- `control_pause`: Run paused
- `control_resume`: Run resumed
- `control_stop`: Run stopped

**Roster Family:**
- `roster_claim`: Resource claimed
- `roster_release`: Resource released
- `roster_register`: Resource registered

**Runtime Family:**
- `runtime_prepare`: Workspace preparation
- `runtime_start`: Execution started
- `runtime_finish`: Execution completed

**Resolver Family:**
- `resolver_invoked`: Resolution case created
- `resolver_returned`: Resolution report received

**Projection Family:**
- `projection_updated`: State projection changed

**Operator Family:**
- `operator_action_requested`: Operator intervention needed
- `operator_action_responded`: Operator responded

### 10.2 Run Registry

**`runs/index.jsonl`**
- Append-only run index
- Fields: run_id, plan_path, status, started_at, updated_at, artifact_root

**`runs/<run_id>/cases.jsonl`**
- Cases index for run
- Fields: case_id, run_id, status, updated_at, case_path

**`runs/<run_id>/heartbeat.json`**
- Liveness heartbeat
- Fields: run_id, pid, host_id, started_at, last_heartbeat_at

**Liveness Values:**
- `alive`: Heartbeat recent
- `stale`: Heartbeat exceeded threshold
- `unknown`: No heartbeat

### 10.3 Projection Events

Projection must emit events for state updates.

See §9.2 for projection replay semantics.

---

## 11. Implementation Notes

### 11.1 Config Loading Implementation

The `load_orchestration_config()` function implements §8.1-§8.3.

### 11.2 Config Freezing Implementation

The `freeze_config()` function implements §8.8.

### 11.3 Event Emission Implementation

Events are emitted via the `EventSink` protocol defined in `events.py`.

### 11.4 Tool Registry Implementation

The `tool_registry.py` module implements §8.4.

---

## 12. Summary

This specification defines:

1. **CLI surface**: Command families (§7), exit codes (§6)
2. **Config system**: Discovery (§8.1), precedence (§8.2), validation (§8.5), snapshots (§8.8)
3. **Observability**: Events (§9.3), projection (§9.2), artifacts (§9.3), registry (§10)

Implementation must maintain compatibility with these contracts.
