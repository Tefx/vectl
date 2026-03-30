# vectl — execution control plane for AI agents

[中文文档](README_zh.md) | [**Read the Introduction**](https://tefx.one/posts/vectl-intro/)

**Structure agents. Enforce discipline.**

[![PyPI](https://img.shields.io/pypi/v/vectl)](https://pypi.org/project/vectl/)
[![License](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](https://opensource.org/licenses/Apache-2.0)

```bash
uvx vectl --help
```

## Markdown Can't Control Your Agent

A 200-step plan in TODO.md. You expect the agent to follow the order, check off completed items, and never skip ahead.

In practice? **Agents treat TODO.md as a suggestion, not a rule.** They skip steps, forget to mark things done, or get lost in a 1000-line plan and redo work that's already finished. The more you try to hint at structure with comments, bold text, and separators, the worse it gets — because Markdown is just natural language text, and agents have zero obligation to obey it.

The core problem: **TODO.md is advice, not enforcement. You can't force an agent to claim a step before starting, or submit evidence before marking it complete.**

This is a structural defect in Markdown, not a flaw in your agent:

- **No enforcement**: you can't prevent agents from skipping steps or require proof of completion
- **No dependencies**: "Deploy DB" before "Config App" — the agent can only guess the ordering, and nothing stops it from guessing wrong
- **Multi-agent conflicts**: multiple agents online with no coordination — they can't tell which steps are parallelizable, and simultaneous edits silently overwrite each other
- **Completion by self-declaration**: the agent says "Done" and that's it — you can't require it to prove tests actually passed

These problems are tolerable at 10-20 steps. At hundreds of steps with multiple agents, TODO.md falls apart completely.

> TODO.md can't say no. vectl can.

## Control Plane, Not a Framework

Agent frameworks manage how agents think. vectl manages **what agents see, when they see it, and what they must prove**.

| Capability | Problem Solved | Mechanism |
| :--- | :--- | :--- |
| **DAG Enforcement** | Agents skip dependencies, guess ordering | Blocked steps are invisible — agents literally *cannot* claim them |
| **Evidence Required** | Agent says "Fixed" and moves on | `evidence_template` forces fill-in-the-blank proof: command, output, PR link |
| **Safe Parallelism** | Multiple agents step on each other | `claim` locking + CAS atomic writes |
| **Auto-Dispatch** | Someone must watch and assign tasks | `next` computes all unblocked steps and sorts them; rejected steps float to top |
| **Token Budget** | Agent re-reads hundreds of completed lines | Hard limits across the board: next ≤3, context ≤120 chars, evidence ≤900 chars |
| **Context Compaction** | Long conversations cause agent amnesia | `checkpoint` generates a deterministic JSON snapshot — inject into new session for instant recovery |
| **Handoff Notes** | Agents lose state between hosts/sessions | `clipboard-write/read/clear` stores short notes in `plan.yaml` (with TTL) |
| **Agent Affinity** | Different agents are good at different tasks | Steps can suggest an agent; `next` sorts by affinity |

## Quick Start

### 1. Initialize

```bash
uvx vectl init --project my-project
```

Creates `plan.yaml` and appends a vectl section to the agent instruction file (`CLAUDE.md` or `AGENTS.md`). If the file already exists, your existing content is preserved — vectl only adds its own section.

> Commit `plan.yaml` + `AGENTS.md`/`CLAUDE.md` together. The plan is the state machine; the instructions file is the agent entry point.

### 2. Connect Your Agent

Recommended: connect via MCP for structured tool access.

<details>
<summary>⚡ MCP (recommended)</summary>

```json
{
  "mcpServers": {
    "vectl": {
      "command": "uvx",
      "args": ["vectl", "mcp"]
    }
  }
}
```

vectl exposes 15 MCP tools. Agents call `vectl_status`, `vectl_claim`, `vectl_complete`, `vectl_decide`, etc. directly — structured data in, structured data out.

For OpenCode, add to your `opencode.jsonc`:

```jsonc
{
  "mcp": {
    "vectl": {
      "type": "local",
      "command": ["uvx", "vectl", "mcp"]
    }
  }
}
```

See [OpenCode MCP docs](https://opencode.ai/docs/mcp-servers/) for details.
</details>

<details>
<summary>⌨️ CLI (no MCP)</summary>

No setup needed — agents call `uvx vectl ...` directly. Works everywhere, but agents must parse text output instead of structured data.

> `uvx vectl init` already creates/updates the agent instructions file.
> To update later: `uvx vectl agents-md` (use `--target claude` if needed).
</details>

#### Agent Instruction Files

`vectl init` and `vectl agents-md` manage the agent instruction file in your repo.

That file is the *entry point* for agents: it points to `uvx vectl guide` topics and sets the rules (one claimed step at a time, evidence required, don't guess specs).

```bash
uvx vectl agents-md                 # Update AGENTS.md / CLAUDE.md with vectl section
uvx vectl agents-md --target claude # Force CLAUDE.md
```

### 3. Write the Plan

Tell your agent what you need — it will generate and modify the plan through vectl's `mutate` tool. **Do not hand-edit `plan.yaml` or let agents edit it directly** — all modifications must go through vectl tools to ensure validation, lock recalculation, and concurrency safety.

### 4. Migrate (Optional)

If your project already tracks work in a markdown file, issue tracker, or spreadsheet, tell your agent:

```
Read the migration guide (via `uvx vectl guide --on migration` or `vectl_guide` MCP tool).
Migrate our existing plan to plan.yaml.
Prefer MCP tools (`vectl_mutate`, `vectl_guide`) over CLI if available.
```

### 5. Monitor Progress

As a user, your main job is to **review progress** and **make decisions**:

```bash
uvx vectl render            # Markdown progress report
uvx vectl dashboard --open  # Visual HTML dashboard (static, no server)
```

![Dashboard Overview](docs/dashboard-overview.png)

The dashboard includes progress overview, phase status, and DAG dependency graphs. Open in any browser — no server required. The DAG view loads Mermaid.js from a CDN (network required for that tab).

Everything else is in the guide:

- Architect protocol: `uvx vectl guide --on planning`
- Getting unstuck: `uvx vectl guide --on stuck`
- Review / validation: `uvx vectl guide --on review`
- Migration: `uvx vectl guide --on migration`

## Handoffs: Clipboard (Notes) vs Checkpoint (State)

If you're switching agent hosts (Claude Code ↔ OpenCode ↔ Claude Desktop) or handing work between agents, use both:

- **Clipboard**: short, human-readable notes that live in `plan.yaml` (with TTL).
- **Checkpoint**: compact, machine-readable state snapshot for context injection.

### Clipboard (recommended for handoffs)

Use this when you want to pass **actionable notes** between agent hosts/sessions without creating extra files.

Example: Claude Code did a detailed code review, found a few small issues, and you want OpenCode to patch them.
Drop the review notes into the clipboard — the other agent reads and applies.

```bash
uvx vectl clipboard-write \
  --author "claude-code" \
  --summary "Code review: small fixes" \
  --content "
Target: src/foo.py

Issues:
- Rename X to Y (see comment in function bar)
- Add missing test for edge case Z
- Run: uv run pytest tests/test_foo.py
"

uvx vectl clipboard-read
uvx vectl clipboard-clear
```

MCP equivalent:

```python
vectl_clipboard(action="write", author="claude-code", summary="Code review: small fixes", content="...")
vectl_clipboard(action="read")
vectl_clipboard(action="clear")
```

### Checkpoint

```bash
uvx vectl checkpoint
```

Paste the JSON into the next session's system prompt.

## Data Model (`plan.yaml`)

```yaml
version: 1
project: my-project
phases:
  - id: auth
    name: Auth Module
    context: |
      All auth steps must follow OWASP guidelines.
      Test with both valid and malformed JWTs.
    depends_on: [core]
    steps:
      - id: auth.user-model
        name: User Model
        status: claimed
        claimed_by: engineer-1
```

A YAML file. In your git repo.

No database. No SaaS. `git blame` it. Review it in PRs. `git diff` it.

**Phase Context**: Set `context` on a phase to give agents guidance that applies to all steps within it. When an agent runs `vectl show <step>` or `vectl claim`, phase context appears automatically in the output.

Full schema, ID rules, and ordering semantics: [docs/DESIGN.md](docs/DESIGN.md).

## Lock Consistency

Lock status is automatically maintained — agents do not need to manage it. After any write operation (`claim`, `complete`, `mutate`, etc.), vectl recalculates lock status automatically. When a recalculation changes a phase's lock state, vectl emits an informational message:

```
[vectl] Lock status updated: phase-a (pending)
```

If you edit `plan.yaml` directly (outside of vectl commands), run `uvx vectl recalc-lock` to manually diagnose and repair any lock inconsistencies.

## Claim Recovery (Ghost Claims & Split-Brain)

When claim state diverges from plan state, you may encounter errors like:
```
Step 'foo.bar' is already claimed on branch 'main'
```
But `vectl show foo.bar` shows it unclaimed — or shows a different owner. This is a **ghost claim** or **split-brain** state.

### Symptom Recognition

| Symptom | Likely Cause |
|---------|-------------|
| Claim rejected, `show` says unclaimed | Ghost claim in `claims.json` (stale or orphaned entry) |
| Claim rejected, `show` shows different owner | Split-brain: claims.json and plan.yaml disagree |
| `status` shows claimed, but no agent is working | Stale claim entry (agent crashed/left) |

### Recovery Commands

```bash
# 1. Diagnose: preview what would change (safe, read-only)
uvx vectl repair claims --dry-run

# 2. Diagnose with JSON (machine-parseable output)
uvx vectl repair claims --dry-run --json

# 3. Repair all claims on current branch
uvx vectl repair claims

# 4. Repair only a specific step (scoped repair)
uvx vectl repair claims --step foo.bar

# 5. Verify: check status again
uvx vectl status
uvx vectl show <step-id>
```

### Flags Explained

| Flag | Purpose |
|------|---------|
| `--dry-run` | Preview changes without writing. Always run this first. |
| `--step <id>` | Repair only this step's claim. Preserves all other claims. |
| `--json` | Machine-readable output. Useful for scripting/CI. |

### Policy

`vectl repair claims` follows **plan precedence**:
- `plan.yaml` is the source of truth for claim-visible state
- For current branch, claims.json is repaired to match plan step status/owner
- Out-of-scope entries (other branches, unrelated steps) are preserved

### When Repo is Healthy (No-Op)

If the repo is consistent, `--dry-run` shows:
```
Policy: plan_precedence: ...
changed: false
actions: []
```

This means claims.json matches plan.yaml — no repair needed.

### Anima-Style Example

```bash
# You try to claim but get rejected
$ uvx vectl claim auth.user-model
Error: Step 'auth.user-model' is already claimed on branch 'main'

# But status shows something different
$ uvx vectl show auth.user-model
status: pending
claimed_by: null
```

**Recovery**:
```bash
# Preview the fix
$ uvx vectl repair claims --dry-run --json
{
  "changed": true,
  "actions": [
    {"action": "remove", "key": "main:auth.user-model", "reason": "ghost_claim_or_non_claimed_step"}
  ]
}

# Apply the fix
$ uvx vectl repair claims
[vectl] Repair claims (main)
  removed: main:auth.user-model

# Now claim works
$ uvx vectl claim auth.user-model
```

### Post-Repair Verification

After running repair, always verify:
```bash
uvx vectl status              # Confirm expected claim state
uvx vectl show <step-id>      # Check specific step
uvx vectl repair claims --dry-run  # Should now show no changes
```

---

## Continuity Artifact Recovery (`vectl repair continuity`)

When using the programmatic driver (`vectl drive`), continuity artifacts (ledger + journal) track session state for restart/resume safety. Stale artifacts can accumulate and must be managed safely.

### Startup Hygiene Stage

The driver runs a **startup hygiene stage** before active recovery:

1. **Scan** continuity artifacts from `.vectl/continuity/ledger/` and `journal/`
2. **Classify** each artifact against current plan and repaired claims
3. **Quarantine** only provably stale artifacts (never delete)
4. **Block** on ambiguous, corrupt, or divergent artifacts (require operator action)

### Artifact Classifications

| Classification | Blocks Startup | Action |
|----------------|----------------|--------|
| `safe_stale_quarantine` | No | Copy to quarantine, record in manifest |
| `blocking_divergence` | Yes | Conflicts with plan or current claims |
| `corrupt_blocking` | Yes | Unparsable or missing step identity |
| `ambiguous_blocking` | Yes | Migration/rename suspicion (no auto-remap) |

### Recovery Commands

```bash
# 1. Diagnose: preview classifications without quarantining (safe, read-only)
uvx vectl repair continuity --dry-run

# 2. Diagnose with JSON (machine-parseable output)
uvx vectl repair continuity --dry-run --json

# 3. Apply quarantine for safe-stale artifacts
uvx vectl repair continuity

# 4. Verify: check manifest and remaining blockers
uvx vectl repair continuity --dry-run --json
```

### Quarantine Behavior

- **Location**: `.vectl/continuity/quarantine/{ledger,journal}/`
- **Manifest**: `.vectl/continuity/quarantine/manifest.jsonl` (append-only, audit trail)
- **Policy**: Copy (not move) — original bytes preserved for auditability
- **Safe stale rule**: Only artifacts for steps absent from both plan and repaired claims are quarantined

### Guardrails

Startup hygiene enforces these non-negotiable rules:
- **No silent deletion** — artifacts are copied to quarantine, never deleted
- **No auto-remap** — renamed or migrated step IDs remain blocking (operator must resolve)
- **Corrupt files remain blocking** — unparsable artifacts require manual intervention
- **Current-claim disagreement remains blocking** — divergence between ledger and claims blocks startup

### Example Output

```bash
$ uvx vectl repair continuity --dry-run --json
{
  "dry_run": true,
  "branch": "main",
  "artifacts_scanned": 5,
  "classifications": [
    {
      "artifact_kind": "ledger",
      "original_path": ".vectl/continuity/ledger/step-abc.json",
      "parsed_step_id": "auth.user-model",
      "classification": "blocking_divergence",
      "reason": "blocking_divergence: current_plan_step",
      "blocks_startup_recovery": true
    },
    {
      "artifact_kind": "journal",
      "original_path": ".vectl/continuity/journal/step-xyz.jsonl",
      "parsed_step_id": "api.migrate-user",
      "classification": "safe_stale_quarantine",
      "reason": "safe_stale_quarantine: absent_from_plan_and_repaired_claims",
      "blocks_startup_recovery": false,
      "quarantine_destination": ".vectl/continuity/quarantine/journal/step-xyz.jsonl"
    }
  ],
  "blocked_count": 1,
  "quarantined_count": 1
}
```

### Post-Repair Verification

```bash
# Check quarantine manifest
uvx vectl repair continuity --dry-run --json | jq '.quarantined_artifacts'

# Verify no remaining blockers
uvx vectl repair continuity --dry-run  # Should show no blocking artifacts
```

## Programmatic Driver

For automated plan execution, use the `vectl drive` command with a driver configuration file:

```bash
# Create driver.yaml (see DRIVER-BLUEPRINT.md for full schema)
cat > driver.yaml << 'EOF'
runners:
  opencode:
    command: opencode
    args: ["run", "--format", "json"]
    stall_timeout: 300
    output_parser: opencode_jsonl

agent_routing:
  python-senior: opencode
  "*-tester": opencode

fallback_runner: opencode

orchestration:
  max_parallelism: 3

judge:
  runner: opencode
  preflight: true
  evidence_validation: true
EOF

# Run the driver
uvx vectl drive --config driver.yaml
```

The driver provides:
- **Deterministic orchestration**: Decides actions based on plan state, not LLM judgment
- **Session reuse**: Reuses agent sessions across dependent steps
- **Failure handling**: Automatic retry, escalation, and runner fallback
- **Git worktree isolation**: Each step runs in an isolated worktree
- **Judgment agent**: LLM-based evaluation for evidence validation and escalation decisions

See [DRIVER-BLUEPRINT.md](DRIVER-BLUEPRINT.md) for complete configuration reference.

> Routing contract: `agent_routing.default` is invalid and rejected during
> config validation. Use explicit exact/glob entries in `agent_routing` and
> `fallback_runner` for default behavior.

### Startup Continuity Hygiene

On startup, the driver runs a **hygiene stage** to safely manage continuity artifacts (ledger + journal) that track session state for resume/restart:

1. **Scan** artifacts from `.vectl/continuity/ledger/` and `journal/`
2. **Classify** against current plan and repaired claims
3. **Quarantine** safe-stale artifacts (copy to `.vectl/continuity/quarantine/`)
4. **Block** on corrupt, ambiguous, or divergent artifacts (require operator action)

**Critical guardrails**:
- Startup **never deletes files** — artifacts are copied to quarantine, not removed
- Startup **never auto-remaps renamed step IDs** — migration suspicion blocks startup
- Corrupt or divergent artifacts remain blocking until operator resolves

If startup detects blocking artifacts, use `vectl repair continuity --dry-run` to diagnose, then apply quarantine with `vectl repair continuity`. See [Continuity Artifact Recovery](#continuity-artifact-recovery-vectl-repair-continuity) for full details.

## Technical Details

Architecture, CAS safety, and test coverage (Hypothesis state machine verification): [docs/DESIGN.md](docs/DESIGN.md).
