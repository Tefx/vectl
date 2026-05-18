# @invar:allow file_size: Plan mutation compatibility module keeps lifecycle, clipboard, agents-md, and recovery APIs co-located for existing CLI/MCP imports while step add/edit internals migrate behind stable re-exports.
"""Compatibility facade for plan mutation, lifecycle, clipboard, agents-md, and recovery APIs."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from vectl.core_plan_clipboard import *  # noqa: F403
from vectl.core_plan_step_add import *  # noqa: F403
from vectl.core_plan_step_edit import *  # noqa: F403
from vectl.core_duplicate_step_id import require_unambiguous_target_step_id
from vectl.models import (
    DiffResult,
    Plan,
    PlanError,
    Step,
)
from vectl.core_plan_clipboard import _clipboard_expired
from vectl.core_plan_step_add import _slugify, _unique_phase_id, _unique_step_id
from vectl.core_plan_step_edit import _CHECKLIST_RE, _SENTINEL, _Unset, _toggle_checklist_item


def claim_step(
    plan: Plan,
    step_id: str,
    agent_name: str,
    *,
    force: bool = False,
    claims_path: Path | None = None,
) -> tuple[Plan, _lifecycle.ClaimResult]:
    from vectl import lifecycle as _lifecycle

    require_unambiguous_target_step_id(plan, step_id, operation="claim")
    return _lifecycle.claim_step(
        plan,
        step_id,
        agent_name,
        force=force,
        claims_path=claims_path,
    )


def complete_step(plan: Plan, step_id: str, evidence: str, claims_path: Path | None = None) -> Plan:
    from vectl import lifecycle as _lifecycle

    require_unambiguous_target_step_id(plan, step_id, operation="complete")
    return _lifecycle.complete_step(plan, step_id, evidence, claims_path=claims_path)


def complete_phase(plan: Plan, phase_id: str, evidence: str) -> tuple[Plan, list[str]]:
    from vectl import lifecycle as _lifecycle

    return _lifecycle.complete_phase(plan, phase_id, evidence)


def defer_step(plan: Plan, step_id: str, claims_path: Path | None = None) -> Plan:
    from vectl import lifecycle as _lifecycle

    require_unambiguous_target_step_id(plan, step_id, operation="defer")
    return _lifecycle.defer_step(plan, step_id, claims_path=claims_path)


def reject_step(plan: Plan, step_id: str, reason: str, reviewer: str = "") -> Plan:
    from vectl import lifecycle as _lifecycle

    require_unambiguous_target_step_id(plan, step_id, operation="reject")
    return _lifecycle.reject_step(plan, step_id, reason, reviewer)


def skip_step(plan: Plan, step_id: str, reason: str) -> Plan:
    from vectl import lifecycle as _lifecycle

    require_unambiguous_target_step_id(plan, step_id, operation="skip")
    return _lifecycle.skip_step(plan, step_id, reason)


def skip_phase(
    plan: Plan, phase_id: str, reason: str, force: bool = False
) -> tuple[Plan, list[str]]:
    from vectl import lifecycle as _lifecycle

    return _lifecycle.skip_phase(plan, phase_id, reason, force=force)


def get_claimed_steps(plan: Plan, agent: str | None = None) -> list[tuple[str, Step]]:
    from vectl import lifecycle as _lifecycle

    return _lifecycle.get_claimed_steps(plan, agent)


def acquire_claim(step_id: str, branch: str, agent: str, claims_path: Path) -> bool:
    from vectl import claims as _claims

    return _claims.acquire_claim(step_id, branch, agent, claims_path)


def cleanup_stale_claims(claims_path: Path, ttl_hours: float = 2.0) -> int:
    from vectl import claims as _claims

    return _claims.cleanup_stale_claims(claims_path, ttl_hours=ttl_hours)


def get_current_branch() -> str:
    from vectl import claims as _claims

    return _claims.get_current_branch()


def release_claim(step_id: str, branch: str, claims_path: Path) -> bool:
    from vectl import claims as _claims

    return _claims.release_claim(step_id, branch, claims_path)


def get_claim_info(step_id: str, branch: str, claims_path: Path) -> _claims.ClaimEntry | None:
    from vectl import claims as _claims

    return _claims.get_claim_info(step_id, branch, claims_path)


_AGENTS_MD_LEGACY_HEADER = "## Plan Tracking (vectl)"
_AGENTS_MD_BEGIN = "<!-- VECTL:AGENTS:BEGIN -->"
_AGENTS_MD_END = "<!-- VECTL:AGENTS:END -->"

_AGENTS_MD_SNIPPET = f"""\
{_AGENTS_MD_BEGIN}
## Plan Tracking (vectl)

vectl tracks this repo's implementation plan as a structured `plan.yaml`:
what to do next, who claimed it, and what counts as done (with verification evidence).

Full guide: `vectl_guide` (CLI fallback: `vectl guide`)
Quick view: `vectl_status` (CLI fallback: `vectl status`)

### MCP vs CLI
- Source of truth: `plan.yaml` (channel-agnostic).
- **Always prefer MCP tools** (`vectl_status`, `vectl_claim`, `vectl_complete`, etc.) when available.
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
- **Design Mode**: Run `vectl_guide` (CLI fallback: `vectl guide --on planning`) to learn the Architect Protocol.
- **Ambiguity = Failure**: Workers will hallucinate if steps are vague.
- **Constraint Tools**:
  - `--evidence-template`: Force workers to provide specific proof (e.g., "Paste logs here").
  - `--refs`: Pin specific files (e.g., "src/auth.py") to the worker's context.
{_AGENTS_MD_END}
"""


class AgentsTarget(str, Enum):
    """Target file for the vectl agents-md section."""

    auto = "auto"
    agents = "agents"
    claude = "claude"


# @shell_complexity: Branches preserve explicit target overrides and documented auto-detection priority without changing file selection.
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
            if _AGENTS_MD_BEGIN in content:
                return candidate

    if agents_md.exists():
        return agents_md
    if claude_md.exists():
        return claude_md

    if (directory / ".claude").is_dir():
        return claude_md

    return agents_md


def upsert_agents_md(directory: Path, target: AgentsTarget = AgentsTarget.auto) -> tuple[str, str]:
    """Create or upsert vectl section in AGENTS.md or CLAUDE.md."""
    target_path = detect_agents_target(directory, target)

    if not target_path.exists():
        target_path.write_text(_AGENTS_MD_SNIPPET, encoding="utf-8")
        return f"Created {target_path.name}", target_path.name

    content = target_path.read_text(encoding="utf-8")

    begin = content.find(_AGENTS_MD_BEGIN)
    end = content.find(_AGENTS_MD_END)
    if begin != -1 and end != -1 and begin < end:
        end_inclusive = end + len(_AGENTS_MD_END)
        new_content = content[:begin].rstrip() + "\n\n" + _AGENTS_MD_SNIPPET + "\n"
        new_content += content[end_inclusive:].lstrip()
        target_path.write_text(new_content, encoding="utf-8")
        return f"Updated {target_path.name} (replaced vectl block)", target_path.name

    if _AGENTS_MD_LEGACY_HEADER in content:
        with target_path.open("a", encoding="utf-8") as f:
            f.write("\n\n" + _AGENTS_MD_SNIPPET)
        return (
            f"Appended updated vectl block to {target_path.name} (legacy block preserved)",
            target_path.name,
        )

    with target_path.open("a", encoding="utf-8") as f:
        f.write("\n\n" + _AGENTS_MD_SNIPPET)
    return f"Appended vectl section to {target_path.name}", target_path.name


@dataclass
class RecoverResult:
    """Result of a recover operation."""

    ok: bool
    restored: bool
    diff: DiffResult
    diff_summary: str
    error: str | None = None


# @shell_complexity: Branches preserve backup validation, exception compatibility, and human diff summary construction.
def preview_recovery(plan_path: Path, backup_path: Path) -> RecoverResult:
    """Preview recovery diff without writing to disk."""
    from vectl.core_plan_queries import diff_plans
    from vectl.io import load_plan_definition
    from vectl.models import PlanIOError

    if not backup_path.exists():
        raise PlanError(f"Backup file not found: {backup_path}")

    try:
        backup_plan, _ = load_plan_definition(backup_path)
    except PlanIOError:
        raise
    except Exception as e:
        raise PlanError(f"Invalid backup file: {e}") from e

    current_plan, _ = load_plan_definition(plan_path)
    diff = diff_plans(current_plan, backup_plan)

    step_count = len(diff.step_changes)
    phase_count = len(diff.phase_changes)
    summary_parts: list[str] = []
    if step_count > 0:
        summary_parts.append(f"{step_count} step(s) changed")
    if phase_count > 0:
        summary_parts.append(f"{phase_count} phase(s) changed")
    if not summary_parts:
        summary_parts.append("No changes")

    return RecoverResult(
        ok=True,
        restored=False,
        diff=diff,
        diff_summary="Total changes: " + ", ".join(summary_parts),
    )


def apply_recovery(backup_path: Path, plan_path: Path) -> None:
    """Apply recovery by overwriting plan_path with backup contents."""
    from vectl.io import load_plan_definition, save_plan

    if not backup_path.exists():
        raise PlanError(f"Backup file not found: {backup_path}")

    backup_plan, _ = load_plan_definition(backup_path)
    save_plan(backup_plan, plan_path)
