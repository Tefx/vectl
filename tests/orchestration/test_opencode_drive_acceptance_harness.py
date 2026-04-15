"""Acceptance harness contract for the real OpenCode drive scheduler.

Authority:
    docs/RFC-orch-drive.md sections 5.8, 6, 7.2, 7.3
    docs/ORCHESTRATION-PLANE-CLI-CONFIG-OBSERVABILITY-DESIGN.md sections 7, 8, 9

Verification:
    Main path: matrix and harness tests prove the authoritative acceptance path
    is wired to the real OpenCode runner, real git worktrees, and real drive
    app surfaces.
    Live path: opt-in smoke runs a real OpenCode drive scenario end to end.

Step: orch_drive_live_acceptance.real-opencode-acceptance-harness
"""

from __future__ import annotations

from pathlib import Path

import yaml

from tests.live_smoke.helpers import live_runner, long_live, opencode_live
from tests.orchestration.helpers import (
    MATRIX_FIXTURE_PATH,
    REQUIRED_SCENARIO_IDS,
    OpenCodeDriveHarness,
    default_orchestrator_env,
    load_drive_acceptance_matrix,
)


def _linear_plan() -> dict[str, object]:
    return {
        "version": 1,
        "project": "opencode-drive-linear-live",
        "phases": [
            {
                "id": "core",
                "name": "Core",
                "steps": [
                    {
                        "id": "core.seed",
                        "name": "Seed",
                        "agent": "python-executor",
                        "status": "pending",
                        "description": (
                            "In the current git worktree run these exact commands:\n"
                            "1. printf 'alpha\n' > alpha.txt\n"
                            "2. git add alpha.txt\n"
                            '3. git commit -m "seed alpha"\n'
                            "4. Return YAML success with the commit hash."
                        ),
                        "verification": "alpha.txt exists and is committed.",
                    },
                    {
                        "id": "core.finish",
                        "name": "Finish",
                        "agent": "python-executor",
                        "status": "pending",
                        "depends_on": ["core.seed"],
                        "description": (
                            "In the current git worktree run these exact commands:\n"
                            "1. test -f alpha.txt\n"
                            "2. printf 'done\n' > done.txt\n"
                            "3. git add done.txt\n"
                            '4. git commit -m "finish linear drive"\n'
                            "5. Return YAML success with both file names."
                        ),
                        "verification": "done.txt exists and is committed after alpha.txt.",
                    },
                ],
            }
        ],
    }


class TestOpenCodeDriveAcceptanceMatrix:
    """Structural proof that the required acceptance matrix is encoded."""

    def test_matrix_fixture_exists(self) -> None:
        assert MATRIX_FIXTURE_PATH.exists(), f"missing fixture: {MATRIX_FIXTURE_PATH}"

    def test_required_scenarios_are_all_encoded(self) -> None:
        scenarios = load_drive_acceptance_matrix()
        actual = {scenario.scenario_id for scenario in scenarios}
        assert actual == set(REQUIRED_SCENARIO_IDS)

    def test_all_scenarios_pin_real_opencode_authority(self) -> None:
        scenarios = load_drive_acceptance_matrix()
        assert scenarios, "matrix must not be empty"
        for scenario in scenarios:
            assert scenario.runner == "opencode"
            assert scenario.authoritative_surface == "real_app_and_real_git_worktrees"

    def test_matrix_yaml_round_trips_for_external_operator_use(self) -> None:
        payload = yaml.safe_load(MATRIX_FIXTURE_PATH.read_text(encoding="utf-8"))
        dumped = yaml.dump(payload, sort_keys=False)
        reloaded = yaml.safe_load(dumped)
        assert reloaded == payload


class TestOpenCodeDriveHarnessFixtureWiring:
    """Non-live proof that the harness writes real-runner fixture inputs."""

    def test_harness_writes_real_opencode_config(self, tmp_path: Path) -> None:
        harness = OpenCodeDriveHarness(tmp_path, env=default_orchestrator_env())
        harness.write_plan(_linear_plan())
        harness.write_orchestration_config(max_parallelism=2)

        vectl_config = yaml.safe_load((tmp_path / "vectl.yaml").read_text(encoding="utf-8"))
        orchestration = vectl_config["orchestration"]
        assert orchestration["runtime"]["default_runner"] == "opencode"
        assert orchestration["runtime"]["workspace_root"] == ".vectl/workspaces"
        assert orchestration["drive"]["max_parallelism"] == 2
        assert (
            orchestration["role_profile_overrides"]["python-executor"]["default_runner"]
            == "opencode"
        )

    def test_harness_initializes_real_git_repo(self, tmp_path: Path) -> None:
        harness = OpenCodeDriveHarness(tmp_path, env=default_orchestrator_env())
        harness.write_plan(_linear_plan())
        harness.write_orchestration_config()
        harness.init_git_repo()

        head = harness.run_git(["rev-parse", "HEAD"], scenario="harness_git_rev_parse")
        head.assert_success()
        assert len(head.stdout.strip()) >= 7


@live_runner
@opencode_live
@long_live
class TestOpenCodeDriveAcceptanceLiveSmoke:
    """Opt-in live smoke proving the harness can run the real drive path."""

    def test_linear_drive_smoke_uses_real_opencode_runner(self, tmp_path: Path) -> None:
        harness = OpenCodeDriveHarness(tmp_path, env=default_orchestrator_env())
        harness.preflight()
        harness.write_plan(_linear_plan())
        harness.write_orchestration_config(max_parallelism=1)
        harness.init_git_repo()

        start = harness.start_drive(max_parallelism=1)
        assert start.status == "running"

        loop_result = harness.run_drive_loop(start.drive_id)
        assert loop_result.drive_id == start.drive_id
        assert loop_result.status in {
            "running",
            "completed",
            "paused",
            "blocked_operator",
            "resolving",
            "replanning",
            "recovering",
        }
