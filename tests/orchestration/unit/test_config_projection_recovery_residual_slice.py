import json
from pathlib import Path

import pytest

from vectl.orchestration.config import load_orchestration_config
from vectl.orchestration.projections import replay_events_to_artifacts
from vectl.orchestration.recovery_fallback import (
    persist_recovery_fallback_result,
    read_recovery_attempt,
    read_recovery_continuity,
    recover_with_fallback,
)


def _write_prompt_artifacts(artifact_root: Path, run_id: str, workspace: Path) -> None:
    input_dir = artifact_root / run_id / "input"
    input_dir.mkdir(parents=True)
    (input_dir / "prompt_bundle.json").write_text(
        json.dumps(
            {
                "role_id": "python-executor",
                "agent_id": "agent-1",
                "runner": "opencode",
                "system_prompt": "s",
                "task_prompt": "t",
                "prompt_bundle_sha256": "0" * 64,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    (input_dir / "runner_prompt.md").write_text("# prompt\n", encoding="utf-8")
    workspace_prompt = workspace / ".vectl" / "orch" / "runner_prompt.md"
    workspace_prompt.parent.mkdir(parents=True)
    workspace_prompt.write_text("# prompt\n", encoding="utf-8")


def test_config_explicit_relative_path_and_env_precedence_preserve_overwrite_semantics(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_file = tmp_path / "relative-vectl.yaml"
    config_file.write_text(
        """
orchestration:
  plan_path: file-plan.yaml
  runtime:
    default_runner: codex
    artifact_root: file-runs
  drive:
    max_parallelism: 2
""".strip()
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("VECTL_ORCH_RUNTIME_DEFAULT_RUNNER", "opencode")
    monkeypatch.setenv("VECTL_ORCH_DRIVE_MAX_PARALLELISM", "7")

    config, loaded_path = load_orchestration_config(plan_path="relative-vectl.yaml")

    assert loaded_path == Path("relative-vectl.yaml")
    assert config.plan_path == Path("file-plan.yaml")
    assert config.runtime.artifact_root == Path("file-runs")
    assert config.runtime.default_runner == "opencode"
    assert config.drive.max_parallelism == 7


def test_projection_payloads_preserve_new_state_precedence_and_stale_conflict(
    tmp_path: Path,
) -> None:
    result = replay_events_to_artifacts(
        (
            {"seq": 1, "kind": "runtime_start", "run_id": "run-1", "step_id": "old", "timestamp": 1.0},
            {"seq": 2, "kind": "runtime_collect", "step_id": "new", "timestamp": 3.5, "status": "success"},
        ),
        tmp_path,
        run_id="run-override",
    )

    latest = json.loads((tmp_path / "state" / "latest.json").read_text(encoding="utf-8"))
    assert result.run_states[0].active_step_id == "new"
    assert latest["run_id"] == "run-override"
    assert latest["active_step_id"] == "new"
    assert latest["last_event_seq"] == 2

    blocker = tmp_path / "blocked" / "state"
    blocker.parent.mkdir()
    blocker.write_text("not a directory", encoding="utf-8")
    stale_result = replay_events_to_artifacts(({"seq": 1, "kind": "runtime_start"},), tmp_path / "blocked")
    assert stale_result.diagnostics is not None
    assert stale_result.diagnostics.code == "PROJECTION_STALE"
    assert "state" in str(stale_result.diagnostics.artifact_path)


def test_recovery_missing_session_falls_back_and_malformed_readback_is_none(
    tmp_path: Path,
) -> None:
    run_id = "run-1"
    run_root = tmp_path / run_id
    workspace = tmp_path / "workspace"
    _write_prompt_artifacts(tmp_path, run_id, workspace)

    result = recover_with_fallback(
        run_id=run_id,
        step_id="step-1",
        agent_id="agent-1",
        runner="opencode",
        run_root=run_root,
        workspace=workspace,
        timestamp="2026-01-01T00:00:00+00:00",
    )
    persisted = persist_recovery_fallback_result(run_root=run_root, result=result)

    assert result.recovered_via == "fresh_relaunch"
    assert "session.json not found" in result.native_resume_failure_reason
    assert set(persisted) == {"continuity", "resume_attempt", "recover_attempt"}
    assert read_recovery_continuity(run_root=run_root).recovered_via == "fresh_relaunch"  # type: ignore[union-attr]
    assert read_recovery_attempt(run_root=run_root, attempt_kind="resume").fallback_relaunch_used is True  # type: ignore[union-attr]

    (run_root / "recovery" / "continuity.json").write_text("{bad json", encoding="utf-8")
    assert read_recovery_continuity(run_root=run_root) is None


def test_recovery_native_resume_keeps_truth_label_and_absolute_workspace(
    tmp_path: Path,
) -> None:
    run_id = "run-native"
    run_root = tmp_path / run_id
    workspace = tmp_path / "workspace"
    _write_prompt_artifacts(tmp_path, run_id, workspace)
    (run_root / "session.json").write_text(
        json.dumps(
            {
                "runner": "opencode",
                "session_id": "sess-1",
                "step_id": "step-1",
                "agent_id": "agent-1",
                "workspace": str(workspace.resolve()),
            }
        )
        + "\n",
        encoding="utf-8",
    )

    result = recover_with_fallback(
        run_id=run_id,
        step_id="step-1",
        agent_id="agent-1",
        runner="opencode",
        run_root=run_root,
        workspace=workspace,
        timestamp="2026-01-01T00:00:00+00:00",
    )

    assert result.recovered_via == "native_session_resume"
    assert result.continuity is not None
    assert result.continuity.session_id == "sess-1"
    assert result.recover_attempt is None
