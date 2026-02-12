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

## Lost?
`uvx vectl review` — Re-orient with full plan scan.
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
- `uvx vectl add-step --phase <p> --name "..." --after <dep>`
- `uvx vectl edit-step <id> --desc "..." --verify "..." --evidence-template "..."`
- `uvx vectl edit-plan --project-guidance-file <path>`

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

## 4. Verify
- `uvx vectl validate` (Mandatory before commit)
- `uvx vectl search <term>` (Check for dupes)
"""


GUIDE_MIGRATION = """\
# Migration: Existing Plan → plan.yaml

If your project already has an implementation plan (markdown file, spreadsheet,
issue tracker), use this workflow to migrate it into plan.yaml.

## Prerequisites

Run `uvx vectl init --project <name>` first. This creates an empty plan.yaml and
configures AGENTS.md.

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

4. **Drop** content that belongs in AGENTS.md, not plan.yaml
   (testing strategy, coding standards, architectural decisions).
   plan.yaml tracks *what to do*. AGENTS.md tracks *how to do it*.

5. **Archive** the old plan (e.g., move to `docs/_archive/`).
   plan.yaml is now the single source of truth.

## What Goes Where

- **Phase headings** in source doc → `uvx vectl add-phase` (or `vectl_mutate action=add-phase`)
- **Step items** under each phase → `uvx vectl add-step` (or `vectl_mutate action=add-step`)
- **Dependencies** between steps → `--depends-on` (or `depends_on=[...]`)
- **Context/descriptions** → `--description` / `--context`

| Content                        | plan.yaml | AGENTS.md / docs/ |
|--------------------------------|-----------|-------------------|
| Phases, steps, dependencies    | ✅        | —                  |
| Step descriptions & verification | ✅      | —                  |
| Status tracking                | ✅        | —                  |
| Coding standards & rules       | —         | ✅                 |
| Testing strategy & tiers       | —         | ✅                 |
| Architecture decisions         | —         | ✅ docs/           |

## Tips

- **Tip:** vectl searches parent directories for `plan.yaml` automatically.
- Use `uvx vectl review` after building to check for orphan deps or missing verifications.
- Mark already-completed steps: `uvx vectl complete <step-id> --evidence "pre-migration: done"`.
- One phase at a time. Verify as you go — don't build the entire plan in one pass.
"""


GUIDE_ALL: list[str] = [
    GUIDE_STARTUP,
    GUIDE_STUCK,
    GUIDE_REVIEW,
    GUIDE_PLANNING,
    GUIDE_MIGRATION,
]

GUIDE_TOPICS: dict[str, str] = {
    "startup": GUIDE_STARTUP,
    "stuck": GUIDE_STUCK,
    "review": GUIDE_REVIEW,
    "planning": GUIDE_PLANNING,
    "migration": GUIDE_MIGRATION,
}

VALID_TOPICS = list(GUIDE_TOPICS.keys())
