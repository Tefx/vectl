# vectl — DAG-enforced todo list for AI agents

[中文文档](README_zh.md)

TODO.md can't say no. vectl can.

[![PyPI](https://img.shields.io/pypi/v/vectl)](https://pypi.org/project/vectl/)

```bash
uvx vectl --help
```

## Why vectl?

| Passive Markdown Plans | vectl |
| :--- | :--- |
| ❌ **Token explosion**: Agents re-read the entire plan every call — finished steps included | ✅ `next` returns only actionable steps |
| ❌ **State drift**: Multiple agents edit the same file — silent overwrites, stale state | ✅ CAS-safe atomic writes — conflicts detected, never silent |
| ❌ **No ordering**: Agents pick what to work on — dependencies skipped, work duplicated | ✅ DAG-enforced execution — blocked steps are invisible |
| ❌ **No verification**: "Done" = a checkbox ticked, no proof | ✅ Evidence required on completion |
| ❌ **Context pollution**: Completed steps stay in context forever, diluting attention | ✅ Agents see only what matters now |

## Core Philosophy

1. **Active Gating**: Agents cannot skip phases or ignore dependencies. The tool enforces the DAG.
2. **Context Efficiency**: Agents only see relevant steps (next actionable items), saving tokens.
3. **Atomic State**: Updates are CAS-safe file operations. No stale tracker drift.
4. **Affordance-Driven Output**: Every command output includes hints for what to do next.
5. **Minimal Tool Calls**: The most common workflow (`claim → work → complete`) requires
   the fewest possible interactions.

## Quick Start

### 1. Initialize

```bash
uvx vectl init --project my-project
```

This creates `plan.yaml` and adds a vectl section to your `AGENTS.md` (creates one if needed).

### 2. Connect Your Agent

<details>
<summary>⚡ Claude Desktop / Cursor</summary>

```json
{
  "mcpServers": {
    "vectl": {
      "command": "uvx",
      "args": ["vectl", "mcp"],
      "env": { "VECTL_PLAN_PATH": "/absolute/path/to/plan.yaml" }
    }
  }
}
```
</details>

<details>
<summary>⚡ OpenCode</summary>

Add to your `opencode.jsonc`:

```jsonc
{
  "mcp": {
    "vectl": {
      "type": "local",
      "command": ["uvx", "vectl", "mcp"],
      "environment": { "VECTL_PLAN_PATH": "/absolute/path/to/plan.yaml" }
    }
  }
}
```
See [OpenCode MCP docs](https://opencode.ai/docs/mcp-servers/) for details.
</details>

<details>
<summary>⌨️ CLI Only (no MCP)</summary>

No setup needed — agents call `uvx vectl ...` directly.

> **Note**: `uvx vectl init` (Step 1) already creates or updates your `AGENTS.md` with the section below.
> You only need the manual template if you skipped `init`.

<details>
<summary>📋 AGENTS.md template (click to expand)</summary>

```md
## Plan Tracking (vectl)

vectl tracks this repo's implementation plan as a structured `plan.yaml`:
what to do next, who claimed it, and what counts as done (with verification evidence).

Full guide: `uvx vectl guide`
Quick view: `uvx vectl status`

### CLI vs MCP
- Source of truth: `plan.yaml` (channel-agnostic).
- If MCP is available (IDE / Claude host), prefer MCP tools for plan operations.
- Otherwise use CLI (`uvx vectl ...`).
- Evidence requirements are identical across CLI and MCP.

### Rules
- One claimed step at a time.
- Evidence is mandatory when completing (commands run + outputs + gaps).
- Spec uncertainty: leave `# SPEC QUESTION: ...` in code, do not guess.
```
</details>
</details>

### 3. Migrate (Optional)

If your project already tracks work in a markdown file, issue tracker, or spreadsheet, tell your agent:

```
Read the migration guide (via `uvx vectl guide --on migration` or `vectl_guide` MCP tool).
Migrate our existing plan to plan.yaml.
Prefer MCP tools (`vectl_mutate`, `vectl_guide`) over CLI if available.
```

### 4. The Workflow

```bash
# ORIENT: Where are we?
uvx vectl status                    # Plan-wide progress dashboard

# PICK: What's available?
uvx vectl next                      # Show claimable steps

# CLAIM: I'm working on this.
uvx vectl claim <step-id> --agent me  # Lock step, get full spec

# WORK: (you write code, run tests)

# COMPLETE: I proved it works.
uvx vectl complete <step-id> --evidence "commit abc123, pytest passed"

# REPEAT: What's unlocked now?
uvx vectl next                      # See what the completion unlocked
```

Every command output ends with hints for the next action:

```
$ uvx vectl complete auth.user-model -e "commit abc: model + tests"

Completed: auth.user-model

Next available:
  ○ pending  auth.session-token — Session Token  (auth)
  ○ pending  auth.permissions — Permission Model  (auth)

→ vectl claim <id> --agent <name>
→ vectl show <id>
```

For all 33 commands (plan mutation, review, admin): `uvx vectl --help` or `uvx vectl guide`.

### Human Oversight

```bash
uvx vectl render                    # Export plan as markdown
uvx vectl diff                      # Changes since last commit
uvx vectl log --last 5              # Recent plan mutations
```

## Data Model (`plan.yaml`)

```yaml
version: 1
project: my-project
phases:
  - id: auth
    name: Auth Module
    depends_on: [core]
    steps:
      - id: auth.user-model
        name: User Model
        status: claimed
        claimed_by: engineer-1
```

Full schema, ID rules, and ordering semantics: [docs/DESIGN.md](docs/DESIGN.md).

## Technical Details

Architecture, CAS safety, and test coverage: [docs/DESIGN.md](docs/DESIGN.md).
