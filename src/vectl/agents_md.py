"""Canonical owner of agents-md detection and upsert logic."""

from __future__ import annotations

from enum import Enum
from pathlib import Path


AGENTS_MD_LEGACY_HEADER = "## Plan Tracking (vectl)"
AGENTS_MD_BEGIN = "<!-- VECTL:AGENTS:BEGIN -->"
AGENTS_MD_END = "<!-- VECTL:AGENTS:END -->"

AGENTS_MD_SNIPPET = f"""\
{AGENTS_MD_BEGIN}
## Plan Tracking (vectl)

vectl tracks this repo's implementation plan as a structured `plan.yaml`:
what to do next, who claimed it, and what counts as done (with verification evidence).

Full guide: `vectl_guide` (CLI fallback: `vectl guide`)
Quick view: `vectl_status` (CLI fallback: `vectl status`)

### MCP vs CLI
- Source of truth: `plan.yaml` (channel-agnostic).
- **Always prefer MCP tools** (`vectl_status`, `vectl_claim`, etc.) when available.
- CLI fallback priority: `uv run vectl` > `vectl` > `uvx vectl`.
- Evidence requirements are identical across MCP and CLI.

### Claim-time Guidance
- `vectl claim` may emit a bounded Guidance block delimited by:
  - `--- VECTL:GUIDANCE:BEGIN ---`
  - `--- VECTL:GUIDANCE:END ---`
- For automation/CI: use `vectl claim --no-guidance` to keep stdout clean.

### plan.yaml — Managed File (DO NOT EDIT DIRECTLY)

`plan.yaml` is exclusively owned by vectl. Direct edits (Edit, Write, sed, or
any file tool) **will** corrupt plan state — vectl performs CAS writes, lock
recalculation, and schema validation on every save, none of which run on direct
edits.

**To modify plan state, ONLY use:**
- MCP (preferred): `vectl_claim`, `vectl_complete`, `vectl_mutate`, etc.
- CLI (fallback): `uv run vectl claim`, `vectl claim`, or `uvx vectl claim`, etc.

If a vectl command fails, report the error — do **not** edit `plan.yaml`
directly as a workaround. Use `vectl guide stuck` for troubleshooting.

### Rules
- One claimed step at a time.
- Evidence is mandatory when completing (commands run + outputs + gaps).
- Spec uncertainty: leave `# SPEC QUESTION: ...` in code, do not guess.

### Step ID Uniqueness
**Step IDs must be globally unique across ALL phases.**
- Example: `auth.login` and `api.login` are different step IDs.
- Example: Using just `login` in two phases creates a duplicate — not allowed.
- If you have legacy duplicate step IDs, use `vectl migrate-step-id --dry-run`
  to preview and `--yes` to repair.

### For Architects / Planners
- **Design Mode**: Run `vectl_guide` (CLI fallback: `vectl guide --on planning`).
- **Ambiguity = Failure**: Workers will hallucinate if steps are vague.
- **Constraint Tools**:
  - `--evidence-template`: Force workers to provide specific proof.
  - `--refs`: Pin specific files (e.g., `src/auth.py`) to worker context.
{AGENTS_MD_END}
"""


class AgentsTarget(str, Enum):
    """Target file for the vectl agents-md section."""

    auto = "auto"
    agents = "agents"
    claude = "claude"


def detect_agents_target(directory: Path, target: AgentsTarget = AgentsTarget.auto) -> Path:
    """Detect the best target file for the vectl agents-md section."""

    agents_md = directory / "AGENTS.md"
    claude_md = directory / "CLAUDE.md"

    if target is AgentsTarget.agents:
        return agents_md
    if target is AgentsTarget.claude:
        return claude_md

    for candidate in (agents_md, claude_md):
        if candidate.exists():
            content = candidate.read_text(encoding="utf-8")
            if AGENTS_MD_BEGIN in content:
                return candidate

    if agents_md.exists():
        return agents_md
    if claude_md.exists():
        return claude_md

    if (directory / ".claude").is_dir():
        return claude_md

    return agents_md


def upsert_agents_md(
    directory: Path, target: AgentsTarget = AgentsTarget.auto
) -> tuple[str, str]:
    """Create or upsert vectl section in AGENTS.md or CLAUDE.md."""

    target_path = detect_agents_target(directory, target)

    if not target_path.exists():
        target_path.write_text(AGENTS_MD_SNIPPET, encoding="utf-8")
        return f"Created {target_path.name}", target_path.name

    content = target_path.read_text(encoding="utf-8")
    begin = content.find(AGENTS_MD_BEGIN)
    end = content.find(AGENTS_MD_END)
    if begin != -1 and end != -1 and begin < end:
        end_inclusive = end + len(AGENTS_MD_END)
        new_content = content[:begin].rstrip() + "\n\n" + AGENTS_MD_SNIPPET + "\n"
        new_content += content[end_inclusive:].lstrip()
        target_path.write_text(new_content, encoding="utf-8")
        return f"Updated {target_path.name} (replaced vectl block)", target_path.name

    if AGENTS_MD_LEGACY_HEADER in content:
        with target_path.open("a", encoding="utf-8") as handle:
            handle.write("\n\n" + AGENTS_MD_SNIPPET)
        return (
            f"Appended updated vectl block to {target_path.name} "
            "(legacy block preserved)",
            target_path.name,
        )

    with target_path.open("a", encoding="utf-8") as handle:
        handle.write("\n\n" + AGENTS_MD_SNIPPET)
    return f"Appended vectl section to {target_path.name}", target_path.name
