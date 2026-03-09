"""Guide content shared between CLI and MCP server.

Each topic is a Markdown string. The GUIDE_TOPICS dict maps topic names
to their content, and GUIDE_ALL lists them in presentation order.
"""

from __future__ import annotations

GUIDE_STARTUP = """\
# Agent Startup

**Authority:** `plan.yaml` is the SINGLE source of truth.
Conflict? Update plan or escalate. Do not guess.

## Workflow
1. `uvx vectl status`           — Overview & progress
2. `uvx vectl next`             — Find available work
3. `uvx vectl claim <id>`       — Lock a step (one at a time)
4. **Execute**              — Follow description + refs exactly
5. `uvx vectl complete <id> --evidence "..."` — Submit results
6. `uvx vectl next`             — Loop

## Claim-time Guidance
`uvx vectl claim` may emit a bounded Guidance block delimited by:
- `--- VECTL:GUIDANCE:BEGIN ---`
- `--- VECTL:GUIDANCE:END ---`

Use the included evidence template as the skeleton for completion evidence.
For automation/CI, disable guidance with: `uvx vectl claim --no-guidance`.

## Evidence (Strict)
Required: Files changed, Verification (cmd + result), Gaps (skipped).
> Ex: "Fixed auth.py. Ran pytest (PASS). No integration tests."

## Tools
Prefer MCP tools (vectl_*) if available. Rules are identical.

## Agent Instructions (AGENTS.md / CLAUDE.md)
`vectl init` injects a vectl section into AGENTS.md or CLAUDE.md.
To update an existing project to the latest template:
```
uvx vectl agents-md          # auto-detect target file
uvx vectl agents-md --target claude   # force CLAUDE.md
```
"""

GUIDE_STUCK = """\
# Unblocking Strategy

## Rejected? (High Priority)
1. `uvx vectl next`             — Rejected items float to top
2. `uvx vectl show <id>`        — Read **rejection_reason**
3. `uvx vectl claim <id>`       — Re-claim & Fix
4. `uvx vectl complete`         — Submit with new evidence

## Blocked? (Deps Missing)
1. `uvx vectl show <id>`        — Identify missing deps
2. `uvx vectl search <term>`    — Find the blocker
3. **Pivot**                — Work on parallel steps

> **Lock status is automatic.** Completing upstream steps unlocks
> downstream phases on the next write — no manual intervention needed.

## Lock Status Wrong? (After Manual YAML Edit)
If you (or a tool) edited `plan.yaml` directly and lock status looks wrong:
```
uvx vectl recalc-lock           # fix immediately
uvx vectl recalc-lock --dry-run # preview changes first
```
Under normal agent workflow this is never needed — lock status recalculates
on every `claim`, `complete`, or `mutate`.

## Claim Consistency Issues? (Ghost Claims / Split-Brain)
When claim state diverges from plan state:

```
uvx vectl repair claims --dry-run     # diagnose (read-only)
uvx vectl repair claims               # repair all
uvx vectl repair claims --step <id>   # repair specific step
uvx vectl repair claims --dry-run --json  # machine-readable
```

See `uvx vectl guide recovery` for full runbook.
"""

GUIDE_REVIEW = """\
# Review & Validation

## Situational Awareness
1. `uvx vectl review`           — Deep scan (L1:Valid → L4:Specs)
2. `uvx vectl review --all`     — Full history (audit trail)

## Gatekeeping
1. `uvx vectl validate`         — Check structural integrity
2. `uvx vectl gate-check <ph>`  — Check phase completion
3. `uvx vectl validate --check-refs` — Audit file references

> Always review before starting a complex phase.
"""

GUIDE_PLANNING = """\
# Architect Protocol

## 1. Orient
`uvx vectl status` → `uvx vectl review` (Context load)

## 2. Mutate
- `uvx vectl add-phase --name "..."`
- `uvx vectl add-step --phase <p> --name "..." --after <dep> --evidence-template "..."`
- `uvx vectl edit-step <id> --desc "..." --verify "..." --refs "..."`
- `uvx vectl edit-plan --project-guidance-file <path>`
- `uvx vectl move-step <step> --target-phase <phase>` — Move step between phases
- `uvx vectl skip-phase <phase> -r superseded` — Clean up empty/merged phases

## 3. Intelligent Guidance (The "Why")
Your goal is to make the *next* agent (the Worker) succeed without guessing.
Ambiguity = Hallucination.

### A. Evidence Templates (`--evidence-template`)
**Prevent lazy completions.** Don't let workers say "Done". Force them to prove it.
- **Bad**: (No template) → Worker says "Fixed the bug."
- **Good**: Template requires specific command output.
  ```text
  ## Verification
  - Command: `pytest tests/test_auth.py`
  - Output: <paste output here>
  - [ ] Confirmed 0 failures
  ```

### B. Context Pinning (`--refs`)
**Stop searching, start working.** If you know which files need editing, tell the worker.
- Use `--refs "src/auth.py,tests/test_auth.py"`
- Result: Worker receives these file paths immediately upon claiming.
- Benefit: Reduces token usage (no `find_file` loops) and prevents focus drift.

## 4. Restructuring Phases
When reorganizing phases for better dependency granularity:

1. **Move steps** to target phase: `uvx vectl move-step <step> --target-phase <target>`
2. **Skip the now-empty phase**: `uvx vectl skip-phase <empty> -r superseded`
3. **For locked phases**: Empty ones skip freely; non-empty need `--force`

> Why? Lock protects work. Zero steps = zero work = nothing to protect.

## 5. Verify
- `uvx vectl validate` (Mandatory before commit)
- `uvx vectl search <term>` (Check for dupes)
"""


GUIDE_MIGRATION = """\
# Migration: Existing Plan → plan.yaml

If your project already has an implementation plan (markdown file, spreadsheet,
issue tracker), use this workflow to migrate it into plan.yaml.

## Prerequisites

Run `uvx vectl init --project <name>` first. This creates an empty plan.yaml and
configures AGENTS.md (or CLAUDE.md for Claude Code projects).

## Rules

1. **Preserve language.** Use the same language as the original plan.
   If the source is in Chinese, plan.yaml content stays in Chinese.

2. **Respect structure.** Map the original plan's hierarchy into phases/steps
   as directly as possible. Do not reorganize, merge, or split unless the
   user requests it.

3. **Copy, don't rephrase.** Use the original titles and descriptions verbatim.
   Do not summarize, reword, or "improve" the text during migration.

4. **Surface issues, don't silently fix.** If you detect dependency errors,
   missing references, or structural inconsistencies, ask the user whether
   to auto-fix. Only apply fixes after explicit confirmation.

## Workflow

1. **Read** the existing plan in full. Identify phases and steps.

2. **Build** plan.yaml incrementally.

   **CLI Users** (terminal):
   - `uvx vectl add-phase --phase-id <id> --name "<name>"`
   - `uvx vectl add-step --phase-id <id> --step-id <id> --name "<name>"`
   - `uvx vectl add-step ... --description "<desc>" --depends-on "dep1,dep2"`

   **MCP Agents** (Claude/Cursor):
   - Use `vectl_mutate` tool with `action="add-phase"` or `action="add-step"`.
   - Arguments map 1:1 (e.g., `--depends-on` → `depends_on=["dep1", "dep2"]`).

3. **Verify** with `uvx vectl render` — compare against the original plan.
   - Every phase accounted for?
   - Every step accounted for?
   - Dependencies correct?

4. **Drop** content that belongs in AGENTS.md / CLAUDE.md, not plan.yaml
   (testing strategy, coding standards, architectural decisions).
   plan.yaml tracks *what to do*. AGENTS.md / CLAUDE.md tracks *how to do it*.

5. **Archive** the old plan (e.g., move to `docs/_archive/`).
   plan.yaml is now the single source of truth.

## What Goes Where

- **Phase headings** in source doc → `uvx vectl add-phase` (or `vectl_mutate action=add-phase`)
- **Step items** under each phase → `uvx vectl add-step` (or `vectl_mutate action=add-step`)
- **Dependencies** between steps → `--depends-on` (or `depends_on=[...]`)
- **Context/descriptions** → `--description` / `--context`

| Content                        | plan.yaml | AGENTS.md / CLAUDE.md / docs/ |
|--------------------------------|-----------|-------------------------------|
| Phases, steps, dependencies    | ✅        | —                              |
| Step descriptions & verification | ✅      | —                              |
| Status tracking                | ✅        | —                              |
| Coding standards & rules       | —         | ✅                             |
| Testing strategy & tiers       | —         | ✅                             |
| Architecture decisions         | —         | ✅ docs/                       |

## Tips

- **Tip:** vectl searches parent directories for `plan.yaml` automatically.
- Use `uvx vectl review` after building to check for orphan deps or missing verifications.
- Mark already-completed steps: `uvx vectl complete <step-id> --evidence "pre-migration: done"`.
- One phase at a time. Verify as you go — don't build the entire plan in one pass.
"""


GUIDE_RECOVERY = """\
# Claim Recovery Runbook

Ghost claims and split-brain claim state can block work. This guide covers
symptom recognition and the recovery workflow.

## Symptom Recognition

| Symptom | Likely Cause |
|---------|-------------|
| Claim rejected, `show` says unclaimed | Ghost claim in `claims.json` (stale/orphaned) |
| Claim rejected, `show` shows different owner | Split-brain: claims.json vs plan.yaml disagree |
| `status` shows claimed, but no agent working | Stale claim (agent crashed/left) |

**Example error**:
```
$ uvx vectl claim auth.user-model
Error: Step 'auth.user-model' is already claimed on branch 'main'

$ uvx vectl show auth.user-model
status: pending
claimed_by: null
```

## Recovery Commands

```bash
# 1. Preview changes (safe, read-only)
uvx vectl repair claims --dry-run

# 2. Machine-readable output (CI/scripting)
uvx vectl repair claims --dry-run --json

# 3. Repair all claims on current branch
uvx vectl repair claims

# 4. Repair specific step only (scoped)
uvx vectl repair claims --step auth.user-model
```

## Flags

| Flag | Purpose |
|------|---------|
| `--dry-run` | Preview without writing. **Always run first.** |
| `--step <id>` | Scope repair to single step. Other claims untouched. |
| `--json` | JSON output for automation. |

## Policy: Plan Precedence

`vectl repair claims` follows **plan precedence**:
- `plan.yaml` is source of truth for claim-visible state
- Claims entries for current branch are repaired to match plan step status/owner
- Out-of-scope entries (other branches, unrelated steps) preserved

## When Repo is Healthy (No-Op)

If consistent, `--dry-run` returns:
```json
{"changed": false, "actions": []}
```

No repair needed — claims.json already matches plan.yaml.

## Full Recovery Sequence

```bash
# Step 1: Diagnose
uvx vectl repair claims --dry-run --json

# Step 2: If changes needed, review them
# Look for "ghost_claim_or_non_claimed_step" or "stale_claim_entry"

# Step 3: Apply repair
uvx vectl repair claims

# Step 4: Verify
uvx vectl status
uvx vectl show <step-id>
uvx vectl repair claims --dry-run  # should show no changes
```

## MCP Equivalent

```python
vectl_repair_claims(dry_run=True)           # diagnose
vectl_repair_claims(dry_run=False)          # repair
vectl_repair_claims(step_id="auth.user")   # scoped
```
"""


GUIDE_ALL: list[str] = [
    GUIDE_STARTUP,
    GUIDE_STUCK,
    GUIDE_RECOVERY,
    GUIDE_REVIEW,
    GUIDE_PLANNING,
    GUIDE_MIGRATION,
]

GUIDE_TOPICS: dict[str, str] = {
    "startup": GUIDE_STARTUP,
    "stuck": GUIDE_STUCK,
    "recovery": GUIDE_RECOVERY,
    "review": GUIDE_REVIEW,
    "planning": GUIDE_PLANNING,
    "migration": GUIDE_MIGRATION,
}

VALID_TOPICS = list(GUIDE_TOPICS.keys())
