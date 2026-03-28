# vectl driver — Technical Blueprint

> Programmatic orchestration engine for vectl plans.
> Replaces the LLM-based orchestrator with a deterministic Python driver
> that delegates to headless CLI agents and uses a unified Judgment Agent
> for decisions requiring LLM understanding.

## Design Rationale (from production data)

Production orchestrator session `ses_2f37a157` (313 steps completed):
- Orchestrator consumed **7.69M tokens (70% of total)**
- 2,782 tool calls, 8.9 per step — all mechanically executable
- 72% of lifecycle operations were **claimed-but-idle recovery** (caused by
  the LLM session dying — a problem programs don't have)
- Only ~9% of lifecycle operations genuinely required LLM judgment
- User sent "continue" **6+ times** to restart stalled orchestrator
- 6 compaction events (1.4M tokens) — self-rescue overhead

**Conclusion**: The orchestrator spends 91% of its effort on work a program
does deterministically, and 9% on judgment a focused LLM agent handles better
with less noise.

---

## Scope

### Full parity with LLM orchestrator

- Deterministic main loop: `decide() -> execute actions -> wait -> loop`
- Multi-runner dispatch: claude CLI, opencode, codex, gemini CLI (direct invocation, no wrapper scripts)
- Configurable agent-to-runner routing (YAML, glob patterns)
- Git worktree lifecycle: create, merge (squash), cleanup, auto-resolve trivial conflicts
- Session reuse pool (TTL-based, runner-scoped, per-runner configurable)
- Unified Judgment Agent for all 11 LLM-required decision points
- Rule-based handling for 8 programmatically-solvable decision points
- Decision table for failure escalation (rules first, LLM fallback)
- Structured event log (JSONL) for full decision auditability
- Runner fallback (2 consecutive failures -> switch runner)
- Graceful shutdown (SIGINT/SIGTERM)
- Startup recovery: `repair_claims()`, orphan worktree detection
- Planner auto-invocation on spec inadequacy
- Gate failure -> batched fix+retest chain creation
- Evidence validation (rule-based schema + LLM content judgment)
- CLI entry: `vectl drive [--config PATH]`

### Out of scope (future)

- nervus integration (future: nervus dispatches `vectl drive --step X`)
- Dashboard TUI (use JSONL log + terminal progress lines)
- Telegram notifications (add as optional observer plugin)

---

## System Sketch

```
                    ┌──────────────────────────────────────────────┐
                    │            vectl drive (asyncio)              │
                    │                                               │
  plan.yaml ───────>│  loop.py   <── decide() direct Python call   │
  claims.json ─────>│    |                                         │
                    │    |── execute_actions()                      │
                    │    |     |── claim_step()                     │
                    │    |     |── worktree.create()                │
                    │    |     |── judge.preflight()  ──────┐       │
                    │    |     |── dispatch.render_prompt() │       │
                    │    |     └── runner.dispatch()        │       │
                    │    |                                  │       │
                    │    |── wait_for_any()                 │       │
                    │    |     └── asyncio.wait(FIRST_DONE) │       │
                    │    |                                  ▼       │
                    │    └── reconcile()              ┌──────────┐  │
                    │          |── judge.validate()   │ Judgment │  │
                    │          |── complete_step()     │  Agent   │  │
                    │          |── worktree.merge()   │ (~300 ln │  │
                    │          |── handle_failure()   │  prompt)  │  │
                    │          └── judge.escalate()   └──────────┘  │
                    │                                               │
                    │  observe.py ──> events.jsonl                  │
                    └────┬──────┬──────┬──────┬─────────────────────┘
                         │      │      │      │
                    ┌────┴─┐ ┌──┴──┐ ┌─┴──┐ ┌─┴────┐
                    │claude│ │open │ │code│ │gemini│
                    │ -p   │ │code │ │x   │ │CLI   │
                    │      │ │CLI  │ │CLI │ │      │
                    └──────┘ └─────┘ └────┘ └──────┘
```

- **Client**: `vectl drive` CLI command (Typer)
- **Backend**: asyncio Python process (~800 lines)
- **LLM layer**: Unified Judgment Agent (stateless, ~300 line prompt, called per judgment)
- **Tools/Integrations**: git (worktrees), runner CLIs
- **Storage**: plan.yaml (CAS via vectl.io), claims.json, events.jsonl

---

## Module Map

```
src/vectl/driver/
    __init__.py          # Package marker
    loop.py              # Main event loop + state machine         (~250 lines)
    runners.py           # Runner Protocol + implementations       (~150 lines)
    parsers.py           # Output parsers for runner CLIs          (~100 lines)
    worktree.py          # Git worktree lifecycle                  (~120 lines)
    dispatch.py          # Prompt template rendering               (~100 lines)
    judge.py             # Judgment Agent invocation + routing      (~120 lines)
    judgments.py         # Judgment type definitions + context schemas (~80 lines)
    session.py           # Session reuse pool                      (~60 lines)
    observe.py           # Event emitter + JSONL writer            (~60 lines)
    config.py            # YAML config loader + Pydantic models    (~80 lines)
    types.py             # Driver-internal data types              (~80 lines)
    errors.py            # Driver error hierarchy                  (~40 lines)
    __main__.py          # `python -m vectl.driver` entry          (~10 lines)
```

CLI integration (last step):
```python
# In src/vectl/cli.py
@app.command()
def drive(config: Path = typer.Option("driver.yaml")):
    """Auto-execute plan with programmatic orchestration."""
    from vectl.driver.loop import run
    run(config)
```

---

## Decision Architecture: 23 Judgment Points

### Tier 1: Fully Programmatic (8 points)

These need ZERO LLM involvement:

| # | Decision | Implementation |
|---|----------|----------------|
| 2 | Plan durability barrier | `git status` + `git commit` |
| 4 | Meta-plan freeze gate | Phase ID pattern match + dep check |
| 12 | Running-task liveness | PID check + stall timeout (real, not heuristic) |
| 13 | NO_EXECUTABLE_STEPS recovery | Decision table: unreconciled results -> stale claims -> repair -> retry |
| 20 | Merge conflict detection | `git merge` return code |
| 21 | Silent mode output | N/A (program has no output mode problem) |
| 22 | RESUME signal | N/A (program has no user interaction loop) |
| 8a | Evidence schema validation | YAML parse + required field check + length/pattern rules |

### Tier 2: Decision Table (4 points)

Rule-based with enumerated cases, LLM fallback only on unmatched:

| # | Decision | Rules | LLM Fallback |
|---|----------|-------|--------------|
| 14 | Batched fix grouping | Group by file scope + blocker severity | Issue classification |
| 16 | Symbolic loop root-cause | Loop counter thresholds | "Why did previous fix fail?" |
| 23 | Planner auto-invocation | Risk keyword detection + preflight signals | "Is spec adequate?" |
| 8b | Evidence format repair | Regex repair for known patterns | Unrecognized format |

### Tier 3: Judgment Agent Required (11 points)

These genuinely need LLM understanding:

| # | Decision | Frequency | Context Size |
|---|----------|-----------|-------------|
| 1 | Pre-dispatch acceptance preflight | Per impl step (~40%) | Step desc + verification + risk signals (~2K) |
| 3 | Contract-first dispatch gate | Per feature step (~20%) | Step + upstream DAG summary (~3K) |
| 5 | Failure provenance classification | Per failure (~15%) | Error + step desc + files changed (~3K) |
| 6 | Gate-intersection reasoning | Per issue found | Issue + remaining gates list (~2K) |
| 7 | Downstream-blocker promotion | Per gate result | Issue + late-stage gate list (~2K) |
| 8c | Evidence content adequacy | Per completion with verification | Step + evidence text (~3K) |
| 10 | Verification-step purity check | Per verification step | Evidence + changed files (~2K) |
| 11 | Plan anomaly repair decision | Per anomaly (~rare) | Anomaly type + repair scope (~1K) |
| 15 | Repeated remediation strategy | Per repeated failure | Failure history + step desc (~3K) |
| 17 | Runnable surface gate augmentation | Per gate in surface phase | Phase + step descriptions (~2K) |
| 18 | Cold context dispatch isolation | Per gate dispatch | Gate scope + pruning targets (~2K) |
| 19 | Freeze hard-block evaluation | Per freeze failure (~rare) | Failure reason + policy (~1K) |

**Estimated frequency per 20-step plan**: ~45 judgment calls
**Estimated tokens per call**: ~5K input + ~500 output = ~5.5K
**Total judgment cost**: ~250K tokens (vs 7.69M for current orchestrator = **97% reduction**)

---

## Judgment Agent Design (judge.py)

### Single Unified Prompt (~300 lines)

Instead of 11+ separate sidecar prompts, ONE agent prompt covers all judgment
types. The prompt is extracted from the existing orchestrator's judgment rules
(the ~30% that represents real decision logic).

```python
JUDGE_SYSTEM_PROMPT = """
You are a plan execution judge. You receive structured judgment requests
and return structured verdicts. You do NOT execute work — only evaluate.

## Judgment Types

### PREFLIGHT
Evaluate if a step description is adequate for a worker to execute.
High-risk signals: migration, CAS, merge, state, backward compatibility.
...

### EVIDENCE_VALIDATION
Evaluate if worker evidence satisfies step verification criteria.
Accept: concrete proof (test output, commit hash, before/after).
Reject: vague claims, missing verification, scope violations.
...

### FAILURE_CLASSIFICATION
Classify failure as: introduced_now | pre_existing.
Disposition as: local_blocker | downstream_blocker | non_blocking.
...

### ESCALATION
After repeated failures, decide: retry_same | retry_different | replan | defer | halt.
...

### GATE_ASSESSMENT
Evaluate gate evidence for: wiring audit completeness, escape hatch audit,
integration proof, blocker vs suggestion classification.
...

(~300 lines total, extracted from orchestrator sections 3.2, 3.3, 3.5, 6.1, 7.5, 7.6)
"""
```

### Invocation Protocol

```python
@dataclass
class JudgmentRequest:
    type: JudgmentType          # PREFLIGHT | EVIDENCE | FAILURE | ESCALATION | GATE | ...
    step_id: str
    context: dict[str, str]     # Type-specific context fields
    failure_history: list[str]  # Previous failures for this step (if any)
    plan_summary: str           # Abbreviated plan context

@dataclass
class JudgmentVerdict:
    verdict: str                # ACCEPT | REJECT | RETRY | SWITCH_AGENT | REPLAN | DEFER | HALT
    reason: str                 # Explanation (logged for audit)
    suggested_action: str | None  # E.g., suggested agent for SWITCH_AGENT
    planner_instruction: str | None  # If REPLAN, what to tell the planner

class Judge:
    """Unified judgment agent. Stateless per call."""

    async def judge(self, request: JudgmentRequest) -> JudgmentVerdict:
        prompt = self._render_request(request)
        result = await self._invoke_llm(prompt)
        verdict = self._parse_verdict(result)
        self.observer.emit("JUDGMENT", request.type, verdict)
        return verdict

    async def _invoke_llm(self, user_prompt: str) -> str:
        proc = await asyncio.create_subprocess_exec(
            "claude", "-p",
            "--system-prompt", JUDGE_SYSTEM_PROMPT,
            "--output-format", "json",
            "--dangerously-skip-permissions",
            "--no-session-persistence",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate(user_prompt.encode())
        data = json.loads(stdout)
        return data["result"]
```

### Context Schema per Judgment Type

```python
CONTEXT_SCHEMAS = {
    JudgmentType.PREFLIGHT: {
        "required": ["step_description", "step_verification", "risk_signals"],
        "optional": ["upstream_steps", "phase_context"],
    },
    JudgmentType.EVIDENCE: {
        "required": ["step_description", "step_verification", "evidence"],
        "optional": ["step_type", "changed_files"],
    },
    JudgmentType.FAILURE: {
        "required": ["step_description", "error_output", "files_touched"],
        "optional": ["failure_count", "previous_errors"],
    },
    JudgmentType.ESCALATION: {
        "required": ["step_description", "failure_count", "failure_history"],
        "optional": ["available_agents", "plan_context"],
    },
    JudgmentType.GATE: {
        "required": ["gate_evidence", "phase_type", "remaining_gates"],
        "optional": ["wiring_checklist", "escape_hatch_policy"],
    },
    JudgmentType.ANOMALY: {
        "required": ["anomaly_type", "repair_scope", "dry_run_recommendation"],
        "optional": ["affected_steps", "plan_structure_hash"],
    },
    JudgmentType.COLD_CONTEXT: {
        "required": ["gate_step_id", "gate_scope", "pruning_targets"],
        "optional": ["side_effect_level", "adversarial_stance"],
    },
}
```

---

## Runner Protocol & Implementations

### Verified CLI Capabilities (tested 2026-03-28)

| Capability | Claude CLI | OpenCode | Codex | Gemini CLI |
|------------|-----------|----------|-------|------------|
| Headless mode | `-p` | `run` | `exec` | `-p` |
| Stdin prompt | pipe to `-p` | pipe to `run` | `-` flag | pipe + `-p` |
| JSON output | `--output-format json` | `--format json` | `--json` (JSONL) | `--output-format json` |
| Agent flag | `--agent NAME` | `--agent NAME` | N/A | N/A |
| Working dir | `cwd=` param in subprocess | `--dir PATH` | `-C DIR` | N/A |
| Session resume | `--resume UUID` | `--session SES_ID` | `exec resume UUID` | `--resume INDEX` |
| Auto-approve | `--dangerously-skip-permissions` | config-based | `--dangerously-bypass-*` | `--yolo` |
| Session persist | `--no-session-persistence` to disable | default on | `--ephemeral` to disable | default on |
| Cost in output | `total_cost_usd` | `step_finish.tokens` | `turn.completed.usage` | unverified |
| Cache visibility | `cache_read_input_tokens` | `cache.read` | `cached_input_tokens` | unverified |

### Output Parsers (3 formats)

```python
class ClaudeOutputParser:
    """Single JSON object from `claude -p --output-format json`."""
    def parse(self, stdout: str) -> RunnerResult:
        data = json.loads(stdout)
        # structured_output is present when --json-schema is used (guaranteed valid JSON)
        output = data.get("structured_output") or data.get("result", "")
        return RunnerResult(
            status=SUCCESS if data["subtype"] == "success" else FAIL,
            session_id=data["session_id"],
            output=json.dumps(output) if isinstance(output, dict) else output,
            cost_usd=data.get("total_cost_usd"),
            tokens=data.get("usage", {}),
        )

class OpenCodeOutputParser:
    """JSONL stream: step_start -> text -> step_finish."""
    def parse(self, stdout: str) -> RunnerResult:
        events = [json.loads(line) for line in stdout.strip().split("\n")]
        text_parts = [e["part"]["text"] for e in events if e["type"] == "text"]
        finish = next((e for e in events if e["type"] == "step_finish"), None)
        return RunnerResult(
            status=self._classify(finish),
            session_id=events[0].get("sessionID") if events else None,
            output="\n".join(text_parts),
            tokens=finish["part"].get("tokens", {}) if finish else {},
        )

class CodexOutputParser:
    """JSONL stream: thread.started -> item.completed -> turn.completed."""
    def parse(self, stdout: str) -> RunnerResult:
        events = [json.loads(line) for line in stdout.strip().split("\n")]
        items = [e["item"]["text"] for e in events if e["type"] == "item.completed"]
        turn = next((e for e in events if e["type"] == "turn.completed"), None)
        return RunnerResult(
            status=SUCCESS if items else FAIL,
            session_id=next((e["thread_id"] for e in events if e["type"] == "thread.started"), None),
            output="\n".join(items),
            tokens=turn.get("usage", {}) if turn else {},
        )
```

### Runner Protocol

```python
class RunnerStatus(Enum):
    SUCCESS = "success"
    FAIL = "fail"
    STALL = "stall"
    TRANSPORT_ERROR = "transport_error"

@dataclass
class RunnerResult:
    status: RunnerStatus
    session_id: str | None
    output: str
    elapsed_seconds: float
    exit_code: int | None
    cost_usd: float | None = None
    tokens: dict | None = None

class Runner(Protocol):
    name: str
    async def dispatch(self, prompt: str, agent: str, workdir: str,
                       session_id: str | None = None) -> RunnerHandle: ...

class RunnerHandle(Protocol):
    session_id: str | None
    pid: int | None
    async def wait(self, timeout: float | None = None) -> RunnerResult: ...
    async def kill(self) -> None: ...
    def is_alive(self) -> bool: ...
```

---

## Configuration Schema (driver.yaml)

```yaml
runners:
  claude:
    command: "claude"
    args: ["-p", "--output-format", "json", "--dangerously-skip-permissions"]
    prompt_mode: stdin
    stall_timeout: 300
    resume_flag: "--resume"
    session_id_regex: "^[0-9a-f]{8}-[0-9a-f]{4}-"
    output_parser: claude_json
    persist_session: true        # Don't pass --no-session-persistence

  opencode:
    command: "opencode"
    args: ["run", "--format", "json", "--dir", "{workdir}"]
    prompt_mode: stdin
    stall_timeout: 600
    resume_flag: "--session"
    session_id_regex: "^ses_[a-z0-9]+"
    output_parser: opencode_jsonl
    persist_session: true

  codex:
    command: "codex"
    args: ["exec", "--json", "--dangerously-bypass-approvals-and-sandbox", "-C", "{workdir}"]
    prompt_mode: stdin_dash      # Uses "-" to read from stdin
    stall_timeout: 600
    resume_command: ["codex", "exec", "resume", "--json", "--dangerously-bypass-approvals-and-sandbox"]
    session_id_regex: "^[0-9a-f]{8}-"
    output_parser: codex_jsonl

  gemini:
    command: "gemini"
    args: ["-p", "--output-format", "json", "--approval-mode", "yolo"]
    prompt_mode: stdin
    stall_timeout: 600
    output_parser: gemini_json
    experimental: true           # Auth issues, not fully verified

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
  # Per-runner TTL overrides
  ttl_overrides:
    claude: 600                  # Claude prompt cache has longer effective window

judge:
  runner: opencode               # Default runner for all judgment calls
  model: null                    # Override model (optional)
  structured_output: true        # Use model structured output where available
  timeout: 60                    # Judgment call timeout (seconds)
  # Judgment types to enable (all true for full parity)
  preflight: true
  evidence_validation: true
  failure_classification: true
  escalation: true
  gate_assessment: true
  cold_context: true
  anomaly: true
  # Skip judgment for these step types (fast path)
  skip_preflight_for: ["*.define", "*.gate", "*.verify"]

observability:
  events_file: ".vectl/driver-events.jsonl"
  log_level: INFO
  print_progress: true
  cost_tracking: true
```

---

## Key Flows

### Flow 1: Main Loop (loop.py)

```
run(config_path):
    config = load_config(config_path)
    state  = DriverState()
    runners = init_runners(config)
    judge  = Judge(config)
    observer = Observer(config)

    # Startup recovery
    plan_path = resolve_plan_path(config.plan_path)
    claims_path = resolve_claims_path(plan_path)
    plan, _ = load_plan_definition(plan_path)
    repair_claims(plan, plan_path, claims_path)
    cleanup_orphan_worktrees()

    LOOP:
        # 1. Decide (direct Python call — zero tokens)
        decide_result = decide(
            running_tasks     = state.as_running_tasks(),
            completed_results = state.drain_completed(),
            max_parallelism   = config.orchestration.max_parallelism,
        )
        observer.emit("DECIDE", decide_result)

        # 2. Execute actions
        for action in decide_result.actions:
            match action.action:
                case "claim_and_dispatch":
                    await handle_dispatch(action, state, config, runners, judge, observer)
                case "complete":
                    await handle_complete(action, state, judge, observer)
                case "wait":
                    observer.emit("WAIT", action)
                case "escalate":
                    await handle_escalate(action, state, judge, observer)

        # 3. Continuation
        if not decide_result.continuation:
            recovered = attempt_recovery(decide_result.halt_reason, state)
            if not recovered:
                if state.running_tasks:
                    pass  # Fall through to wait
                else:
                    break

        # 4. Wait for any running task
        if state.running_tasks:
            completed = await state.wait_for_any()
            await state.reconcile(completed, judge, observer)
        elif not decide_result.continuation:
            break

        # 5. Infinite loop guard
        if state.detect_loop():
            observer.emit("HALT", "SUSPECTED_INFINITE_LOOP")
            break

    observer.emit("FINAL", state.summary())
```

### Flow 2: Dispatch with Preflight

```
handle_dispatch(action, state, config, runners, judge, observer):
    step_id = action.step_id
    agent   = action.agent or "default"

    # 1. Preflight judgment (for impl steps with risk signals)
    if config.judge.preflight and is_impl_step(step_id) and has_risk_signals(action):
        verdict = await judge.judge(JudgmentRequest(
            type=PREFLIGHT,
            step_id=step_id,
            context={"step_description": action.step_description,
                     "step_verification": action.step_verification,
                     "risk_signals": detect_risk_signals(action)},
        ))
        if verdict.verdict == "REPLAN":
            await dispatch_planner(step_id, verdict.planner_instruction, state, runners)
            return  # Will be re-dispatched after planner strengthens spec
        elif verdict.verdict == "REJECT":
            observer.emit("PREFLIGHT_REJECT", step_id, verdict.reason)
            return  # Skip this step for now

    # 2. Claim (CAS-safe, with claims_path)
    plan_path = resolve_plan_path()
    claims_path = resolve_claims_path(plan_path)
    plan, plan_hash = load_plan_definition(plan_path)
    plan, _ = claim_step(plan, step_id, agent_name=agent, claims_path=claims_path)
    save_plan(plan, plan_path, plan_hash, f"[vectl-driver] claim {step_id}")

    # 3. Create worktree
    worktree_path, branch = worktree.create(step_id)

    # 4. Resolve runner (with fallback)
    runner_name = config.route_agent(agent)
    if state.runner_failures.get((step_id, runner_name), 0) >= 2:
        runner_name = config.fallback_runner
        observer.emit("RUNNER_FALLBACK", step_id, runner_name)
    runner = runners[runner_name]

    # 5. Session reuse
    session_id = None
    if action.session == "reuse" and action.task_id:
        session_id = action.task_id
        observer.emit("SESSION_REUSE_HIT", step_id, session_id)

    # 6. Render prompt (template, not LLM-generated)
    prompt = dispatch.render_prompt(
        step_id=step_id, agent=agent,
        description=action.step_description or "",
        verification=action.step_verification or "",
        refs=action.step_refs or [],
        worktree_path=worktree_path,
        session_reuse=action.session == "reuse",
        failure_context=state.get_failure_context(step_id),
    )

    # 7. Dispatch
    handle = await runner.dispatch(prompt, agent, worktree_path, session_id)
    observer.emit("STEP_DISPATCHED", step_id, agent, runner_name)

    # 8. Register
    state.register(step_id, agent, runner_name, handle, worktree_path)
```

### Flow 3: Reconcile with Evidence Judgment

```
reconcile(completed: CompletedEntry, judge, observer):
    step_id = completed.step_id
    plan_path = resolve_plan_path()
    claims_path = resolve_claims_path(plan_path)

    if completed.status == SUCCESS:
        evidence = parse_evidence(completed.output)

        # Evidence judgment (rule-based first, then LLM if needed)
        verdict = ACCEPT
        if not validate_evidence_schema(evidence):
            verdict = REJECT  # Rule-based: missing fields, too short, etc.
        elif has_verification_criteria(step_id):
            # LLM judgment for content adequacy
            verdict_result = await judge.judge(JudgmentRequest(
                type=EVIDENCE,
                step_id=step_id,
                context={"step_description": ..., "step_verification": ...,
                         "evidence": evidence, "changed_files": ...},
            ))
            verdict = verdict_result.verdict

        if verdict == ACCEPT:
            # Complete + merge (serialized)
            plan, plan_hash = load_plan_definition(plan_path)
            plan = complete_step(plan, step_id, evidence, claims_path=claims_path)
            save_plan(plan, plan_path, plan_hash, f"[vectl-driver] complete {step_id}")
            observer.emit("STEP_COMPLETED", step_id, completed.elapsed)

            async with merge_lock:
                merge_result = worktree.merge(step_id, completed.worktree_path)
                if merge_result.status == "conflict":
                    if merge_result.trivial:
                        worktree.auto_resolve(merge_result)
                    else:
                        await dispatch_conflict_resolver(step_id, ...)
                else:
                    worktree.cleanup(step_id, completed.worktree_path)

            session_pool.record(step_id, completed.session_id,
                                completed.runner_name, completed.agent)
            state.mark_completed(step_id, "SUCCESS", evidence)

        elif verdict == REJECT:
            # Reload plan (CAS-safe) then defer for rework
            plan, plan_hash = load_plan_definition(plan_path)
            plan = defer_step(plan, step_id, claims_path=claims_path)
            save_plan(plan, plan_path, plan_hash, f"[vectl-driver] defer {step_id} (rejected)")
            state.mark_completed(step_id, "FAIL", f"Evidence rejected: {verdict_result.reason}")

    elif completed.status in (FAIL, STALL, TRANSPORT_ERROR):
        state.increment_failure(step_id, completed.runner_name)
        count = state.failure_count(step_id)

        # Reload plan (CAS-safe) for all mutation paths
        plan, plan_hash = load_plan_definition(plan_path)

        if count < 3:
            # Rule-based: defer for automatic retry
            plan = defer_step(plan, step_id, claims_path=claims_path)
            save_plan(plan, plan_path, plan_hash, f"[vectl-driver] defer {step_id} (retry {count})")
            state.mark_completed(step_id, "FAIL", completed.output)
        else:
            # LLM escalation judgment
            verdict = await judge.judge(JudgmentRequest(
                type=ESCALATION,
                step_id=step_id,
                context={"failure_count": count, "last_error": completed.output,
                         "step_description": ...},
                failure_history=state.get_failure_history(step_id),
            ))
            match verdict.verdict:
                case "RETRY":
                    plan = defer_step(plan, step_id, claims_path=claims_path)
                    save_plan(plan, plan_path, plan_hash, f"[vectl-driver] defer {step_id} (escalation retry)")
                case "SWITCH_AGENT":
                    plan = defer_step(plan, step_id, claims_path=claims_path)
                    save_plan(plan, plan_path, plan_hash, f"[vectl-driver] defer {step_id} (switch agent)")
                    state.set_agent_override(step_id, verdict.suggested_action)
                case "REPLAN":
                    await dispatch_planner(step_id, verdict.planner_instruction, ...)
                case "HALT":
                    observer.emit("HALT", f"Step {step_id} failed {count}x: {verdict.reason}")
                    state.halt_requested = True
```

### Flow 4: Gate Failure -> Fix+Retest Chain

```
handle_gate_failure(step_id, gate_evidence, judge, observer):
    # 1. Classify issues (rule-based + LLM)
    issues = parse_gate_issues(gate_evidence)

    # Rule-based classification
    blockers = [i for i in issues if i.severity in ("blocker", "should_fix")]
    suggestions = [i for i in issues if i.severity == "suggestion"]

    if not blockers:
        return  # No action needed

    # 2. LLM: assess downstream impact
    verdict = await judge.judge(JudgmentRequest(
        type=GATE,
        step_id=step_id,
        context={"gate_evidence": gate_evidence,
                 "blocker_issues": [i.to_dict() for i in blockers],
                 "remaining_gates": get_remaining_gates(plan)},
    ))

    # 3. Create fix+retest chain via planner
    if verdict.verdict in ("REPLAN", "ACCEPT_WITH_FIX"):
        await dispatch_planner(
            step_id,
            f"Create fix step for blockers in {step_id}: {verdict.reason}",
            state, runners,
        )
        # Defer gate for re-run after fix
        defer_step(plan, step_id)
```

---

## Worktree Lifecycle (worktree.py)

```
create(step_id) -> (worktree_path, branch_name):
    branch = f"vectl/step-{step_id}"
    path   = f".vectl/worktrees/{step_id}"
    IF path exists: return (path, branch)          # Retry: reuse
    git branch {branch}
    git worktree add {path} {branch}
    return (path, branch)

merge(step_id, worktree_path) -> MergeResult:
    branch = f"vectl/step-{step_id}"
    result = git merge --squash {branch}
    IF exit_code == 0:
        git commit -m "[vectl-driver] {step_id}"
        return MergeResult(status="clean")

    conflicts = git diff --name-only --diff-filter=U

    # Auto-resolve trivial conflicts
    trivial = {"plan.yaml", "claims.json", "*.lock", "*.lockb"}
    if all(any(fnmatch(f, p) for p in trivial) for f in conflicts):
        git checkout --ours {conflicts}
        git add {conflicts}
        git commit -m "[vectl-driver] {step_id} (auto-resolved)"
        return MergeResult(status="auto_resolved")

    git merge --abort
    return MergeResult(status="conflict", files=conflicts, trivial=False)

cleanup(step_id, worktree_path):
    git worktree remove --force {worktree_path}
    git branch -d vectl/step-{step_id}
```

---

## Session Pool (session.py)

```python
@dataclass
class SessionEntry:
    session_id: str
    runner_name: str
    agent: str
    step_id: str
    completed_at: float         # time.monotonic()

class SessionPool:
    def __init__(self, config: SessionConfig):
        self._entries: dict[str, SessionEntry] = {}
        self._default_ttl = config.reuse_ttl
        self._ttl_overrides = config.ttl_overrides

    def record(self, step_id, session_id, runner_name, agent):
        self._entries[step_id] = SessionEntry(
            session_id=session_id, runner_name=runner_name,
            agent=agent, step_id=step_id,
            completed_at=time.monotonic())

    def find_reusable(self, step_id, agent, runner_name, depends_on) -> str | None:
        if len(depends_on) != 1: return None
        entry = self._entries.get(depends_on[0])
        if not entry: return None
        if entry.runner_name != runner_name: return None
        ttl = self._ttl_overrides.get(runner_name, self._default_ttl)
        if time.monotonic() - entry.completed_at > ttl: return None
        return entry.session_id
```

---

## Observability (observe.py)

### Event Types

| Event | Trigger | Key Fields |
|-------|---------|------------|
| `DECIDE` | Every `decide()` call | running_count, claimable, capacity, actions |
| `STEP_DISPATCHED` | Worker launched | step_id, agent, runner, session_reuse |
| `STEP_COMPLETED` | Worker SUCCESS + evidence accepted | step_id, elapsed, tokens, cost |
| `STEP_FAILED` | Worker FAIL/STALL | step_id, failure_type, attempt, error |
| `JUDGMENT` | Judge invoked | type, step_id, verdict, reason, latency |
| `MERGE_COMPLETED` | Squash merge done | step_id, files_changed |
| `MERGE_CONFLICTED` | Merge conflict | step_id, conflicting_files |
| `RUNNER_FALLBACK` | Runner switched | step_id, from_runner, to_runner |
| `SESSION_REUSE_HIT` | Session reused | step_id, session_id, age_seconds |
| `SESSION_REUSE_MISS` | Reuse attempted but missed | step_id, reason |
| `COST_CHECKPOINT` | Every 10 steps or 5 min | cumulative_tokens, cumulative_cost |
| `RECOVERY` | Auto-repair applied | type (stale_claim, orphan_worktree) |
| `HALT` | Orchestration stopped | reason |
| `FINAL` | Plan complete | total_steps, total_time, total_cost |

### Audit Trail

Every judgment call is logged with full input/output:
```jsonl
{"ts":1711612345.0,"event":"JUDGMENT","step_id":"core.impl","data":{"type":"EVIDENCE","context":{"step_description":"...","evidence":"..."},"verdict":"ACCEPT","reason":"Tests pass with output","latency_ms":3200}}
```

This enables: `vectl-replay --log events.jsonl --step core.impl`

---

## Quality Priorities

- [x] **Reliability** — deterministic loop, real locks, PID-based liveness
- [x] **Simplicity** — ~800 lines total, config-driven, no framework
- [ ] Performance / Latency (acceptable: ~45 judgment calls add ~3 min total)
- [ ] Maintainability
- [ ] Extensibility
- [ ] Security / Privacy

---

## Failure Modes

### Runner failures
| Mode | Detection | Mitigation |
|------|-----------|------------|
| Worker stall | No output for stall_timeout | Kill, defer, retry |
| Process crash | Non-zero exit, no result | TRANSPORT_ERROR, retry |
| Malformed output | Parser failure | TRANSPORT_ERROR, retry once |
| Runner not found | FileNotFoundError | Halt with config error |
| Session resume fails | Worker starts fresh | Accept (graceful degradation) |

### Git failures
| Mode | Detection | Mitigation |
|------|-----------|------------|
| Trivial conflict | Conflicts only in plan.yaml/lockfiles | Auto-resolve (--ours) |
| Non-trivial conflict | Conflicts in source files | Dispatch conflict-resolver agent |
| Branch exists | git branch fails | Delete stale, retry |
| Worktree exists | Directory exists | Reuse (retry scenario) |

### Plan failures
| Mode | Detection | Mitigation |
|------|-----------|------------|
| Claim conflict | ClaimConflictError | repair_claims(), retry |
| CAS conflict | CASConflictError | Reload plan, retry |
| Circular dep | validate_plan() | Halt with diagnostic |

### Driver failures
| Mode | Detection | Mitigation |
|------|-----------|------------|
| Infinite loop | Same decide() output 10x | Halt SUSPECTED_INFINITE_LOOP |
| All stalled | All handles timed out | Kill all, re-decide |
| Judge unavailable | Judge call times out (60s) | Skip judgment, accept optimistically, log warning |
| SIGINT/SIGTERM | Signal handler | Kill workers, save event log, cleanup worktrees |
| Crash | Process dies | Restart fresh; plan.yaml + git are durable |

---

## Development Phases

### Phase 1: Core loop + single runner + judgment agent
```
errors.py       → Driver error hierarchy
types.py        → Driver-internal data types
config.py       → YAML config, Pydantic validation
observe.py      → Event emitter + JSONL
session.py      → Session pool
worktree.py     → Git worktree lifecycle
dispatch.py     → Prompt templates (extracted from orchestrator)
parsers.py      → Output parsers for runner CLIs
runners.py      → Runner Protocol + ClaudeRunner (direct claude -p)
                  + OpenCodeRunner (opencode run --format json)
judgments.py    → Judgment type definitions + context schemas
judge.py        → Unified judgment agent invocation
loop.py         → Main loop (decide -> dispatch -> wait -> reconcile)
__main__.py     → python -m vectl.driver entry
```

### Phase 2: Multi-runner + hardening
```
runners.py      → Add CodexRunner, GeminiRunner
config.py       → Agent routing with glob matching
loop.py         → Runner fallback, infinite loop guard
worktree.py     → Auto-resolve trivial conflicts
loop.py         → Startup repair_claims() + orphan cleanup
```

### Phase 3: Full judgment coverage + gate chains
```
judge.py        → All 11 judgment types implemented
loop.py         → Gate failure -> planner dispatch -> fix+retest chain
loop.py         → Preflight check for high-risk steps
loop.py         → Conflict-resolver agent dispatch
```

### Phase 4: CLI integration
```
cli.py          → Add `vectl drive` command
```

---

## Dependencies

### New pip dependencies: NONE
Uses only stdlib asyncio + subprocess + dataclasses.
All other deps (pydantic, pyyaml, typer, rich) already in vectl.

### External runtime
- `claude` CLI (direct invocation via `claude -p`, no wrapper scripts)
- `opencode` CLI (default runner for workers and judgment agent)
- `codex` / `gemini` CLI (optional, user-installed, config-injected)
- `git` (system)
