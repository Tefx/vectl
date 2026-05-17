"""Canonical owner of agents-md detection and upsert logic."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Generic, TypeAlias, TypeVar


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


_T = TypeVar("_T")
_E = TypeVar("_E", bound=BaseException)


@dataclass(frozen=True)
class Success(Generic[_T]):
    value: _T


@dataclass(frozen=True)
class Failure(Generic[_E]):
    error: _E


Result: TypeAlias = Success[_T] | Failure[_E]


# @shell_complexity: Ordered AGENTS.md/CLAUDE.md precedence preserves documented target selection semantics.
def detect_agents_target(directory: Path, target: AgentsTarget = AgentsTarget.auto) -> Result[Path, OSError]:
    """Detect the best target file for the vectl agents-md section."""

    agents_md = directory / "AGENTS.md"
    claude_md = directory / "CLAUDE.md"

    if target is AgentsTarget.agents:
        return Success(agents_md)
    if target is AgentsTarget.claude:
        return Success(claude_md)

    try:
        for candidate in (agents_md, claude_md):
            if candidate.exists():
                content = candidate.read_text(encoding="utf-8")
                if AGENTS_MD_BEGIN in content:
                    return Success(candidate)
    except OSError as exc:
        return Failure(exc)

    if agents_md.exists():
        return Success(agents_md)
    if claude_md.exists():
        return Success(claude_md)

    if (directory / ".claude").is_dir():
        return Success(claude_md)

    return Success(agents_md)


# @shell_complexity: Branches preserve create, replace, legacy-append, and fresh-append user messages.
def upsert_agents_md(
    directory: Path, target: AgentsTarget = AgentsTarget.auto
) -> Result[tuple[str, str], OSError]:
    """Create or upsert vectl section in AGENTS.md or CLAUDE.md."""

    target_result = detect_agents_target(directory, target)
    if isinstance(target_result, Failure):
        raise target_result.error
    target_path = target_result.value

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
