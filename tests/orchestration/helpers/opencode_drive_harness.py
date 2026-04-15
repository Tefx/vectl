"""Real OpenCode drive acceptance harness helpers.

Authority:
    docs/RFC-orch-drive.md sections 5.8, 6, 7.2, 7.3
    docs/ORCHESTRATION-PLANE-CLI-CONFIG-OBSERVABILITY-DESIGN.md sections 7, 8, 9

This helper module intentionally wires the real orchestration application,
real git worktree lifecycle, and real OpenCode runner surface. It does not
provide a mock-only acceptance seam.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
import yaml

from tests.live_smoke.helpers import (
    OPENCODE_MODEL,
    OPENCODE_MODEL_ENVAR,
    LiveRunnerPreflight,
    SubprocessResult,
    is_live_runner_opted_in,
    is_long_live_runner_opted_in,
    run_subprocess,
)
from vectl.orch_app import AppConfig, build_orchestration_app
from vectl.orchestration.config import load_orchestration_config

PROJECT_ROOT = Path(__file__).resolve().parents[3]
MATRIX_FIXTURE_PATH = (
    PROJECT_ROOT / "tests" / "orchestration" / "fixtures" / "opencode_drive_acceptance_matrix.yaml"
)

REQUIRED_SCENARIO_IDS: tuple[str, ...] = (
    "linear_dag_auto_drain",
    "multi_phase_dag_auto_drain",
    "parallel_ready_frontier_dispatch",
    "bounded_parallelism_enforcement",
    "real_worktree_child_runs_with_real_merge_back",
    "long_running_parallel_branch_execution",
    "runtime_failure_resolver_continue",
    "merge_conflict_resolver_continue_or_operator_boundary",
    "review_needs_replan_planner_mutation_continue",
    "drive_resume_and_drive_recover",
    "pause_unpause_stop_under_parallel_child_runs",
    "phase_plan_auto_close",
)


@dataclass(frozen=True)
class DriveAcceptanceScenario:
    """Matrix entry for one real OpenCode drive acceptance scenario."""

    scenario_id: str
    title: str
    coverage: tuple[str, ...]
    execution_mode: str
    runner: str
    authoritative_surface: str
    notes: str


def load_drive_acceptance_matrix() -> tuple[DriveAcceptanceScenario, ...]:
    """Load the authoritative scenario matrix fixture."""

    payload = yaml.safe_load(MATRIX_FIXTURE_PATH.read_text(encoding="utf-8"))
    rows = payload.get("scenarios", ())
    scenarios: list[DriveAcceptanceScenario] = []
    for row in rows:
        scenarios.append(
            DriveAcceptanceScenario(
                scenario_id=str(row["scenario_id"]),
                title=str(row["title"]),
                coverage=tuple(str(item) for item in row.get("coverage", ())),
                execution_mode=str(row["execution_mode"]),
                runner=str(row["runner"]),
                authoritative_surface=str(row["authoritative_surface"]),
                notes=str(row["notes"]),
            )
        )
    return tuple(scenarios)


def default_orchestrator_env() -> dict[str, str]:
    """Build an environment for real OpenCode drive acceptance runs."""

    env = dict(os.environ)
    env.setdefault(OPENCODE_MODEL_ENVAR, OPENCODE_MODEL)
    env.update(
        {
            "GIT_AUTHOR_NAME": "vectl-drive-live",
            "GIT_AUTHOR_EMAIL": "vectl-drive-live@test",
            "GIT_COMMITTER_NAME": "vectl-drive-live",
            "GIT_COMMITTER_EMAIL": "vectl-drive-live@test",
        }
    )
    return env


class OpenCodeDriveHarness:
    """Real-drive harness for opt-in OpenCode acceptance scenarios."""

    def __init__(self, root: Path, *, env: dict[str, str] | None = None) -> None:
        self.root = root
        self.env = default_orchestrator_env() if env is None else dict(env)

    def preflight(self) -> None:
        """Require explicit live opt-in and a usable OpenCode surface."""

        if not is_live_runner_opted_in() or not is_long_live_runner_opted_in():
            pytest.skip(
                "real OpenCode drive acceptance requires RUN_LIVE_RUNNER_TESTS=1 "
                "and RUN_LONG_LIVE_RUNNER_TESTS=1",
                allow_module_level=False,
            )
        LiveRunnerPreflight("opencode")()

    def run_vectl(
        self,
        args: list[str],
        *,
        timeout: float = 1200.0,
        scenario: str,
    ) -> SubprocessResult:
        """Execute the real vectl CLI in the isolated scenario repo."""

        return run_subprocess(
            ["uv", "run", "--project", str(PROJECT_ROOT), "vectl", *args],
            cwd=str(self.root),
            env=self.env,
            timeout=timeout,
            runner_name="opencode",
            scenario=scenario,
        )

    def run_git(
        self,
        args: list[str],
        *,
        timeout: float = 60.0,
        scenario: str,
    ) -> SubprocessResult:
        """Execute git in the isolated scenario repo."""

        return run_subprocess(
            ["git", *args],
            cwd=str(self.root),
            env=self.env,
            timeout=timeout,
            runner_name="git",
            scenario=scenario,
        )

    def write_plan(self, plan: dict[str, Any]) -> None:
        """Persist a test plan fixture."""

        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / "plan.yaml").write_text(
            yaml.dump(plan, sort_keys=False),
            encoding="utf-8",
        )

    def write_orchestration_config(self, *, max_parallelism: int = 4) -> None:
        """Persist vectl.yaml for real OpenCode drive execution."""

        payload = {
            "orchestration": {
                "plan_path": "plan.yaml",
                "runtime": {
                    "default_runner": "opencode",
                    "artifact_root": ".vectl/runs",
                    "workspace_root": ".vectl/workspaces",
                },
                "drive": {
                    "max_parallelism": max_parallelism,
                    "collect_poll_interval_ms": 250,
                    "resolver_timeout_seconds": 300,
                    "planner_timeout_seconds": 300,
                },
                "role_profile_overrides": {
                    "python-executor": {"default_runner": "opencode"},
                    "blocked-case-coordinator": {"default_runner": "opencode"},
                },
            }
        }
        (self.root / "vectl.yaml").write_text(
            yaml.dump(payload, sort_keys=False),
            encoding="utf-8",
        )

    def init_git_repo(self, tracked_files: tuple[str, ...] = ("plan.yaml", "vectl.yaml")) -> None:
        """Initialize a real git repository for worktree-backed runs."""

        self.run_git(["init", "-b", "main"], scenario="drive_git_init").assert_success()
        self.run_git(["add", *tracked_files], scenario="drive_git_add").assert_success()
        self.run_git(
            ["commit", "-m", "init drive acceptance fixture"],
            scenario="drive_git_commit",
        ).assert_success()

    def build_live_app(self) -> Any:
        """Build the real OrchestrationApp against the fixture repo."""

        orchestration_config, _ = load_orchestration_config(self.root / "vectl.yaml")
        return build_orchestration_app(
            AppConfig(
                plan_path=self.root / "plan.yaml",
                orchestration_config=orchestration_config,
                run_store_root=self.root / ".vectl" / "runs",
            )
        )

    def start_drive(self, *, agent: str = "python-executor", max_parallelism: int = 4) -> Any:
        """Start the real drive via the composed application surface."""

        previous_cwd = Path.cwd()
        os.chdir(self.root)
        try:
            app = self.build_live_app()
            return app.start_drive(agent=agent, max_parallelism=max_parallelism)
        finally:
            os.chdir(previous_cwd)

    def run_drive_loop(self, drive_id: str) -> Any:
        """Execute one real drive loop pass."""

        previous_cwd = Path.cwd()
        os.chdir(self.root)
        try:
            app = self.build_live_app()
            return app.run_drive_loop(drive_id)
        finally:
            os.chdir(previous_cwd)

    def resume_drive(self, drive_id: str) -> Any:
        """Resume the real drive via the composed application surface."""

        previous_cwd = Path.cwd()
        os.chdir(self.root)
        try:
            app = self.build_live_app()
            return app.resume_drive(drive_id)
        finally:
            os.chdir(previous_cwd)

    def recover_drive(self, drive_id: str, *, dry_run: bool) -> Any:
        """Recover the real drive via the composed application surface."""

        previous_cwd = Path.cwd()
        os.chdir(self.root)
        try:
            app = self.build_live_app()
            return app.recover_drive(drive_id, dry_run=dry_run)
        finally:
            os.chdir(previous_cwd)

    def control_pause(self, drive_id: str, *, reason: str | None = None) -> Any:
        """Queue a real drive pause request."""

        previous_cwd = Path.cwd()
        os.chdir(self.root)
        try:
            app = self.build_live_app()
            return app.control_drive_pause(drive_id=drive_id, reason=reason)
        finally:
            os.chdir(previous_cwd)

    def control_unpause(self, drive_id: str, *, reason: str | None = None) -> Any:
        """Queue a real drive unpause request."""

        previous_cwd = Path.cwd()
        os.chdir(self.root)
        try:
            app = self.build_live_app()
            return app.control_drive_unpause(drive_id=drive_id, reason=reason)
        finally:
            os.chdir(previous_cwd)

    def control_stop(self, drive_id: str, *, reason: str | None = None, force: bool = False) -> Any:
        """Queue a real drive stop request."""

        previous_cwd = Path.cwd()
        os.chdir(self.root)
        try:
            app = self.build_live_app()
            return app.control_drive_stop(drive_id=drive_id, reason=reason, force=force)
        finally:
            os.chdir(previous_cwd)

    def load_plan(self) -> dict[str, Any]:
        """Load the current plan fixture."""

        return yaml.safe_load((self.root / "plan.yaml").read_text(encoding="utf-8"))


__all__ = [
    "DriveAcceptanceScenario",
    "MATRIX_FIXTURE_PATH",
    "OpenCodeDriveHarness",
    "PROJECT_ROOT",
    "REQUIRED_SCENARIO_IDS",
    "default_orchestrator_env",
    "load_drive_acceptance_matrix",
]
