"""CLI commands for project bootstrap and agent-instruction files."""

from __future__ import annotations

from pathlib import Path

import typer
from returns.result import Result, Success
from rich.console import Console

from vectl.agents_md import AgentsTarget, upsert_agents_md
from vectl.io import save_plan
from vectl.models import Plan

console = Console(stderr=True)
out = Console()

PlanOption = typer.Option(
    None,
    "--plan",
    "-p",
    help="Path to plan YAML file. Defaults to auto-discovery (walk-up). (env: VECTL_PLAN_PATH)",
)
AgentsMdDirOption = typer.Option(
    Path("."),
    "--dir",
    help="Directory containing AGENTS.md/CLAUDE.md to update.",
)
AgentsMdTargetOption = typer.Option(
    "auto",
    "--target",
    help="Target file: auto (detect .claude/), agents (AGENTS.md), claude (CLAUDE.md).",
)
InitTargetOption = typer.Option(
    "auto",
    "--target",
    help="Target file for agent instructions: auto, agents, claude.",
)


def agents_md_cmd(
    directory: Path = AgentsMdDirOption,
    target: str = AgentsMdTargetOption,
) -> Result[None, str]:
    """Upsert the vectl section in AGENTS.md or CLAUDE.md."""

    message, _filename = upsert_agents_md(directory, AgentsTarget(target))
    out.print(message)
    return Success(None)


# @shell_orchestration: Bootstrap helper performs bounded filesystem writes for CLI init.
def _upsert_gitattributes(directory: Path) -> Result[str, str]:
    """Configure .gitattributes with plan.yaml merge driver (idempotent)."""

    gitattributes_path = directory / ".gitattributes"
    target_line = "plan.yaml merge=vectl"
    if not gitattributes_path.exists():
        gitattributes_path.write_text(f"{target_line}\n", encoding="utf-8")
        return Success("Created .gitattributes with plan.yaml merge driver")

    content = gitattributes_path.read_text(encoding="utf-8")
    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith("plan.yaml") and "merge=vectl" in stripped:
            return Success(".gitattributes already has plan.yaml merge driver")

    with gitattributes_path.open("a", encoding="utf-8") as handle:
        handle.write(f"\n{target_line}\n")
    return Success("Updated .gitattributes with plan.yaml merge driver")


def init(
    project: str = typer.Option(..., "--project", prompt="Project name"),
    plan: Path | None = PlanOption,
    agents_target: str = InitTargetOption,
) -> Result[None, str]:
    """Create a new plan.yaml template and configure AGENTS.md / CLAUDE.md."""

    target = plan or Path("plan.yaml")
    if target.exists():
        console.print(f"[red bold]Error:[/] {target} already exists. Delete it first or use a different path.")
        raise typer.Exit(1)

    template = Plan(project=project, context=f"Implementation plan for {project}.")
    save_plan(template, target)
    out.print(f"[green]Created:[/] {target}")

    agents_message, _filename = upsert_agents_md(target.parent, AgentsTarget(agents_target))
    out.print(f"[green]Agent instructions:[/] {agents_message}")

    gitattr_result = _upsert_gitattributes(target.parent).unwrap()
    out.print(f"[green]Git attributes:[/] {gitattr_result}")

    out.print()
    out.print("[dim]→ vectl add-phase --phase-id <id> --name <name>   Add a phase[/]")
    out.print("[dim]→ vectl guide                     Full onboarding guide[/]")
    return Success(None)
