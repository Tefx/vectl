"""Opt-in full orchestrator E2E live scenarios for vectl + OpenCode.

These scenarios are intentionally excluded from default development loops.
They require both live-runner gates:

- RUN_LIVE_RUNNER_TESTS=1
- RUN_LONG_LIVE_RUNNER_TESTS=1

Coverage focus:
- real ``vectl orch run`` dispatch against the OpenCode runner
- real git worktree changes committed inside the execution workspace
- real reconcile merge into the integration worktree
- operator-driven replan via public ``vectl`` plan commands
- failure due to dirty integration context, then operator resolution
- observability surfaces (status/runs/events/logs/artifacts/actions/recover)
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest
import yaml

from tests.live_smoke.helpers import (
    OPENCODE_MODEL,
    OPENCODE_MODEL_ENVAR,
    LiveRunnerPreflight,
    is_live_runner_opted_in,
    is_long_live_runner_opted_in,
    live_runner,
    long_live,
    opencode_live,
    run_subprocess,
    skip_if_auth_missing,
    skip_if_binary_missing,
)
from vectl.orch_app import AppConfig, build_orchestration_app
from vectl.orchestration.config import load_orchestration_config

PROJECT_ROOT = Path(__file__).resolve().parents[2]


SEED_DESCRIPTION = """In the current git worktree run these exact commands:
1. printf 'VERSION=1\n' > artifact.txt
2. git add artifact.txt
3. git commit -m "seed artifact"
4. Do NOT call any vectl commands.
5. Return YAML success with evidence mentioning the commit hash.
"""


FIX_DESCRIPTION = """In the current git worktree run these exact commands:
1. printf 'VERSION=2\n' > artifact.txt
2. git add artifact.txt
3. git commit -m "apply replan fix"
4. Do NOT call any vectl commands.
5. Return YAML success with evidence mentioning the commit hash.
"""


PROBE_DESCRIPTION = """In the current git worktree run these exact commands:
1. test "$(cat artifact.txt)" = "VERSION=2"
2. printf 'VERIFIED_VERSION_2\n' > verification_receipt.txt
3. git add verification_receipt.txt
4. git commit -m "record verification receipt"
5. Do NOT call any vectl commands.
6. Return YAML success with evidence mentioning both files.
"""


def _json_stdout(result: Any) -> Any:
    assert result.stdout.strip(), result.format_diagnostics()
    return json.loads(result.stdout)


def _run_vectl(
    args: list[str],
    *,
    cwd: Path,
    env: dict[str, str],
    timeout: float = 1200.0,
    scenario: str,
) -> Any:
    return run_subprocess(
        ["uv", "run", "--project", str(PROJECT_ROOT), "vectl", *args],
        cwd=str(cwd),
        env=env,
        timeout=timeout,
        runner_name="opencode",
        scenario=scenario,
    )


def _run_git(
    args: list[str],
    *,
    cwd: Path,
    env: dict[str, str] | None = None,
    timeout: float = 60.0,
    scenario: str,
) -> Any:
    return run_subprocess(
        ["git", *args],
        cwd=str(cwd),
        env=env,
        timeout=timeout,
        runner_name="git",
        scenario=scenario,
    )


def _load_plan(root: Path) -> dict[str, Any]:
    return yaml.safe_load((root / "plan.yaml").read_text(encoding="utf-8"))


def _find_step(plan: dict[str, Any], step_id: str) -> dict[str, Any]:
    for phase in plan.get("phases", []):
        for step in phase.get("steps", []):
            if step.get("id") == step_id:
                return step
    raise AssertionError(f"step not found: {step_id}")


def _write_fixture(root: Path) -> None:
    plan = {
        "version": 1,
        "project": "vectl-orchestrator-live-e2e",
        "phases": [
            {
                "id": "core",
                "name": "Core",
                "steps": [
                    {
                        "id": "core.seed",
                        "name": "Seed Artifact",
                        "agent": "python-executor",
                        "status": "pending",
                        "description": SEED_DESCRIPTION,
                        "verification": "artifact.txt exists and is committed with VERSION=1.",
                    },
                    {
                        "id": "core.probe",
                        "name": "Probe Final State",
                        "agent": "python-executor",
                        "status": "pending",
                        "depends_on": ["core.seed"],
                        "description": "placeholder probe before replan",
                        "verification": "placeholder",
                    },
                ],
            }
        ],
    }
    (root / "plan.yaml").write_text(yaml.dump(plan, sort_keys=False), encoding="utf-8")
    (root / "tracked_dirty.txt").write_text("clean\n", encoding="utf-8")
    (root / "vectl.yaml").write_text(
        yaml.dump(
            {
                "orchestration": {
                    "plan_path": "plan.yaml",
                    "runtime": {
                        "default_runner": "opencode",
                        "artifact_root": ".vectl/runs",
                    },
                    "role_profile_overrides": {
                        "python-executor": {
                            "default_runner": "opencode",
                        }
                    },
                }
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )


def _init_git_repo(root: Path, env: dict[str, str]) -> None:
    _run_git(["init", "-b", "main"], cwd=root, env=env, scenario="e2e_git_init").assert_success()
    _run_git(
        ["add", "plan.yaml", "vectl.yaml", "tracked_dirty.txt"],
        cwd=root,
        env=env,
        scenario="e2e_git_add",
    ).assert_success()
    _run_git(
        ["commit", "-m", "init live orchestrator fixture"],
        cwd=root,
        env=env,
        scenario="e2e_git_commit",
    ).assert_success()


def _assert_merge_commit_present(root: Path) -> None:
    head_subject = _run_git(
        ["show", "--format=%s", "-s", "HEAD"],
        cwd=root,
        scenario="e2e_git_show_head",
    )
    head_subject.assert_success()
    assert "Merge commit" in head_subject.stdout, head_subject.format_diagnostics()


def _build_live_app(root: Path) -> Any:
    orchestration_config, _ = load_orchestration_config(root / "vectl.yaml")
    app = build_orchestration_app(
        AppConfig(
            plan_path=root / "plan.yaml",
            orchestration_config=orchestration_config,
            run_store_root=root / ".vectl" / "runs",
        )
    )
    original_collect = app._collect_and_route_terminal

    def extended_collect(**kwargs: Any) -> Any:
        kwargs.setdefault("max_poll_iterations", 1800)
        kwargs.setdefault("poll_interval_seconds", 0.1)
        return original_collect(**kwargs)

    app._collect_and_route_terminal = extended_collect  # type: ignore[method-assign]
    return app


def _run_live_app(root: Path, *, agent: str = "python-executor") -> Any:
    previous_cwd = Path.cwd()
    os.chdir(root)
    try:
        app = _build_live_app(root)
        return app.run(agent=agent)
    finally:
        os.chdir(previous_cwd)


@pytest.fixture
def orchestrator_live_env() -> dict[str, str]:
    env = dict(os.environ)
    env.setdefault(OPENCODE_MODEL_ENVAR, OPENCODE_MODEL)
    env.update(
        {
            "GIT_AUTHOR_NAME": "vectl-live",
            "GIT_AUTHOR_EMAIL": "vectl-live@test",
            "GIT_COMMITTER_NAME": "vectl-live",
            "GIT_COMMITTER_EMAIL": "vectl-live@test",
        }
    )
    return env


@live_runner
@opencode_live
@long_live
class TestVectlOrchestratorE2ELive:
    """Full orchestrator E2E scenarios with real OpenCode and git worktrees."""

    def test_opt_in_gates(self) -> None:
        if not is_live_runner_opted_in():
            pytest.skip("requires RUN_LIVE_RUNNER_TESTS=1", allow_module_level=True)
        if not is_long_live_runner_opted_in():
            pytest.skip("requires RUN_LONG_LIVE_RUNNER_TESTS=1", allow_module_level=True)

    def test_full_orchestrator_flow_replan_resolve_and_merge(
        self,
        tmp_path: Path,
        orchestrator_live_env: dict[str, str],
    ) -> None:
        """Exercise real orchestration, failure handling, replan, and merge.

        Scenario:
        1. Run an initial step that commits changes in a worktree and reconciles.
        2. Replan using public vectl commands to insert a fix step.
        3. Force a real reconcile abort with dirty tracked state in the integration root.
        4. Resolve the issue (clean + defer) and rerun successfully.
        5. Finish the replanned probe step and inspect observability surfaces.
        """
        if not is_live_runner_opted_in() or not is_long_live_runner_opted_in():
            pytest.skip("full live orchestrator suite not opted in", allow_module_level=True)

        preflight = LiveRunnerPreflight("opencode")
        if not preflight.check_binary():
            skip_if_binary_missing("opencode")
        if not preflight.check_auth():
            skip_if_auth_missing("opencode")

        _write_fixture(tmp_path)
        _init_git_repo(tmp_path, orchestrator_live_env)

        # Step 1: real worktree write + commit + reconcile merge.
        seed_result = _run_live_app(tmp_path)
        assert seed_result.success, seed_result.message
        assert seed_result.step_id == "core.seed"
        assert (tmp_path / "artifact.txt").read_text(encoding="utf-8").strip() == "VERSION=1"
        _assert_merge_commit_present(tmp_path)

        # Replan via public plan commands: insert a fix step and retarget probe.
        add_fix = _run_vectl(
            [
                "add-step",
                "--phase",
                "core",
                "--id",
                "core.fix",
                "--name",
                "Apply Replan Fix",
                "--agent",
                "python-executor",
                "--after",
                "core.seed",
                "--desc",
                FIX_DESCRIPTION,
                "--verify",
                "artifact.txt exists and is committed with VERSION=2.",
            ],
            cwd=tmp_path,
            env=orchestrator_live_env,
            scenario="e2e_add_fix_step",
        )
        add_fix.assert_success("failed to add replanned fix step")
        edit_probe = _run_vectl(
            [
                "edit-step",
                "core.probe",
                "--add-dep",
                "core.fix",
                "--desc",
                PROBE_DESCRIPTION,
                "--verify",
                "verification_receipt.txt is committed and artifact.txt stays at VERSION=2.",
            ],
            cwd=tmp_path,
            env=orchestrator_live_env,
            scenario="e2e_edit_probe_step",
        )
        edit_probe.assert_success("failed to retarget probe step during replan")
        next_after_replan = _run_vectl(
            ["next"],
            cwd=tmp_path,
            env=orchestrator_live_env,
            scenario="e2e_next_after_replan",
        )
        next_after_replan.assert_success()
        assert "core.fix" in (next_after_replan.stdout + next_after_replan.stderr)

        # Force a real reconcile abort by dirtying a tracked file in the integration root.
        (tmp_path / "tracked_dirty.txt").write_text("dirty\n", encoding="utf-8")
        failed_fix_result = _run_live_app(tmp_path)
        assert failed_fix_result.success is False
        assert failed_fix_result.step_id == "core.fix"
        assert failed_fix_result.message is not None
        assert "reconcile non-closure: aborted" in failed_fix_result.message

        failed_runs = _run_vectl(
            ["orch", "runs", "--json"],
            cwd=tmp_path,
            env=orchestrator_live_env,
            scenario="e2e_runs_after_failure",
        )
        failed_runs.assert_success()
        runs_payload = _json_stdout(failed_runs)
        assert isinstance(runs_payload, list) and runs_payload, failed_runs.format_diagnostics()
        assert runs_payload[0]["status"] == "fail"
        assert "reconcile non-closure: aborted" in runs_payload[0]["output_summary"]

        probe_show = _run_vectl(
            ["show", "core.fix"],
            cwd=tmp_path,
            env=orchestrator_live_env,
            scenario="e2e_show_failed_fix_step",
        )
        probe_show.assert_success()
        assert "claimed" in (probe_show.stdout + probe_show.stderr).lower()

        # Resolve: clear the dirty tracked file and release the stale claim.
        _run_git(
            ["checkout", "--", "tracked_dirty.txt"],
            cwd=tmp_path,
            env=orchestrator_live_env,
            scenario="e2e_git_restore_dirty_file",
        ).assert_success("failed to restore dirty integration file")
        defer_fix = _run_vectl(
            ["defer", "core.fix"],
            cwd=tmp_path,
            env=orchestrator_live_env,
            scenario="e2e_defer_fix_after_failure",
        )
        defer_fix.assert_success("failed to defer claimed fix step after abort")
        repair_claims = _run_vectl(
            ["repair", "claims"],
            cwd=tmp_path,
            env=orchestrator_live_env,
            scenario="e2e_repair_claims_after_defer",
        )
        repair_claims.assert_success("failed to repair claims after defer")
        next_after_resolve = _run_vectl(
            ["next"],
            cwd=tmp_path,
            env=orchestrator_live_env,
            scenario="e2e_next_after_resolve",
        )
        next_after_resolve.assert_success()
        assert "core.fix" in (next_after_resolve.stdout + next_after_resolve.stderr)

        # Rerun fix cleanly; this should now merge VERSION=2.
        fix_result = _run_live_app(tmp_path)
        assert fix_result.success, fix_result.message
        assert fix_result.step_id == "core.fix"
        assert (tmp_path / "artifact.txt").read_text(encoding="utf-8").strip() == "VERSION=2"
        _assert_merge_commit_present(tmp_path)

        # Final probe step validates final state and commits a verification receipt.
        probe_result = _run_live_app(tmp_path)
        assert probe_result.success, probe_result.message
        assert probe_result.step_id == "core.probe"
        assert (tmp_path / "verification_receipt.txt").read_text(
            encoding="utf-8"
        ).strip() == "VERIFIED_VERSION_2"
        _assert_merge_commit_present(tmp_path)

        # Final plan must be complete after replan + resolution.
        plan = _load_plan(tmp_path)
        assert _find_step(plan, "core.seed")["status"] == "done"
        assert _find_step(plan, "core.fix")["status"] == "done"
        assert _find_step(plan, "core.probe")["status"] == "done"

        render_result = _run_vectl(
            ["render"],
            cwd=tmp_path,
            env=orchestrator_live_env,
            scenario="e2e_render_final_plan",
        )
        render_result.assert_success()
        assert "core.fix" in render_result.stdout

        dag_result = _run_vectl(
            ["dag", "--phase", "core"],
            cwd=tmp_path,
            env=orchestrator_live_env,
            scenario="e2e_dag_final_plan",
        )
        dag_result.assert_success()
        assert "core_fix" in dag_result.stdout

        # Observability surfaces: ensure they stay live and machine-readable.
        status_result = _run_vectl(
            ["orch", "status", "--latest", "--json"],
            cwd=tmp_path,
            env=orchestrator_live_env,
            scenario="e2e_status_latest",
        )
        status_result.assert_success()
        status_payload = _json_stdout(status_result)
        assert status_payload["view_type"] == "status"

        events_result = _run_vectl(
            ["orch", "events", "--latest", "--json"],
            cwd=tmp_path,
            env=orchestrator_live_env,
            scenario="e2e_events_latest",
        )
        events_result.assert_success()
        assert isinstance(_json_stdout(events_result), list)

        logs_result = _run_vectl(
            ["orch", "logs", "--latest", "--json"],
            cwd=tmp_path,
            env=orchestrator_live_env,
            scenario="e2e_logs_latest",
        )
        logs_result.assert_success()
        assert isinstance(_json_stdout(logs_result), list)

        artifacts_result = _run_vectl(
            ["orch", "artifacts", "--latest", "--json"],
            cwd=tmp_path,
            env=orchestrator_live_env,
            scenario="e2e_artifacts_latest",
        )
        artifacts_result.assert_success()
        assert isinstance(_json_stdout(artifacts_result), list)

        actions_result = _run_vectl(
            ["orch", "actions", "--latest", "--json"],
            cwd=tmp_path,
            env=orchestrator_live_env,
            scenario="e2e_actions_latest",
        )
        actions_result.assert_success()
        assert isinstance(_json_stdout(actions_result), list)

        recover_result = _run_vectl(
            ["orch", "recover", "--latest", "--dry-run", "--json"],
            cwd=tmp_path,
            env=orchestrator_live_env,
            timeout=1200.0,
            scenario="e2e_recover_dry_run",
        )
        recover_result.assert_success()
        recover_payload = _json_stdout(recover_result)
        assert recover_payload["success"] is True
        recovery_report = recover_payload.get("recovery_report") or {}
        assert recovery_report.get("outcome") in {"recovered", "no_artifacts"}
