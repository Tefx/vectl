"""Tests for legacy split-state migration into unified plan.yaml."""

from __future__ import annotations

import json
import logging
import subprocess
from pathlib import Path

import yaml

from vectl.io import load_plan_definition, save_plan
from vectl.migration import migrate_from_split_state, resolve_state_path
from vectl.models import Phase, Plan, Step, StepStatus


def _must_find_step(plan: Plan, step_id: str) -> Step:
    """Find a step by ID, asserting it exists."""
    found = plan.find_step(step_id)
    assert found is not None, f"Step {step_id} not found"
    _, step = found
    return step


def _must_find_phase(plan: Plan, phase_id: str) -> Phase:
    """Find a phase by ID, asserting it exists."""
    phase = plan.find_phase(phase_id)
    assert phase is not None, f"Phase {phase_id} not found"
    return phase


def _base_plan() -> Plan:
    return Plan(
        project="migration-test",
        phases=[
            Phase(
                id="phase-a",
                name="Phase A",
                steps=[
                    Step(id="phase-a.step1", name="Step 1", status=StepStatus.PENDING),
                    Step(id="phase-a.step2", name="Step 2", status=StepStatus.PENDING),
                ],
            ),
            Phase(
                id="phase-b",
                name="Phase B",
                steps=[Step(id="phase-b.step1", name="Step 3", status=StepStatus.PENDING)],
            ),
        ],
    )


def _write_state(plan_path: Path, payload: dict) -> Path:
    state_path = resolve_state_path(plan_path)
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(payload), encoding="utf-8")
    return state_path


def test_migrate_normal(tmp_path: Path) -> None:
    plan_path = tmp_path / "plan.yaml"
    save_plan(_base_plan(), plan_path)

    state_path = _write_state(
        plan_path,
        {
            "steps": {
                "phase-a.step1": {"status": "done", "evidence": "done evidence"},
                "phase-a.step2": {"status": "claimed", "claimed_by": "agent-1"},
                "phase-b.step1": {"status": "pending"},
            },
            "phases": {"phase-a": {"status": "in_progress", "evidence": "phase evidence"}},
        },
    )

    result = migrate_from_split_state(plan_path)
    migrated_path = state_path.with_suffix(".json.migrated")

    loaded, _ = load_plan_definition(plan_path)
    assert result.migrated_steps == 3
    assert result.migrated_phases == 1
    assert not result.skipped_orphans

    # Use _must_find_step helper to handle Optional tuple return
    step1 = _must_find_step(loaded, "phase-a.step1")
    assert step1.status == StepStatus.DONE
    assert step1.evidence == "done evidence"

    step2 = _must_find_step(loaded, "phase-a.step2")
    assert step2.status == StepStatus.CLAIMED
    assert step2.claimed_by == "agent-1"

    phase_a = _must_find_phase(loaded, "phase-a")
    assert phase_a.status.value == "in_progress"
    assert phase_a.evidence == "phase evidence"
    assert not state_path.exists()
    assert migrated_path.exists()


def test_migrate_orphan_steps(tmp_path: Path) -> None:
    plan_path = tmp_path / "plan.yaml"
    save_plan(_base_plan(), plan_path)
    _write_state(plan_path, {"steps": {"missing.step": {"status": "done"}}})

    result = migrate_from_split_state(plan_path)

    assert result.migrated_steps == 0
    assert result.skipped_orphans == ["missing.step"]
    assert len(result.warnings) == 1
    assert "Orphan step" in result.warnings[0]


def test_migrate_orphan_phases(tmp_path: Path) -> None:
    plan_path = tmp_path / "plan.yaml"
    save_plan(_base_plan(), plan_path)
    _write_state(plan_path, {"phases": {"deleted-phase": {"status": "done"}}})

    result = migrate_from_split_state(plan_path)

    assert result.migrated_phases == 0
    assert result.skipped_orphans == ["deleted-phase"]
    assert len(result.warnings) == 1
    assert "Orphan phase" in result.warnings[0]


def test_migrate_plan_has_inline_status_state_wins(tmp_path: Path) -> None:
    plan = _base_plan()
    found = plan.find_step("phase-a.step1")
    assert found is not None
    found[1].status = StepStatus.DONE

    plan_path = tmp_path / "plan.yaml"
    save_plan(plan, plan_path)
    _write_state(
        plan_path, {"steps": {"phase-a.step1": {"status": "claimed", "claimed_by": "state"}}}
    )

    result = migrate_from_split_state(plan_path)
    loaded, _ = load_plan_definition(plan_path)

    assert result.migrated_steps == 1
    step1 = _must_find_step(loaded, "phase-a.step1")
    assert step1.status == StepStatus.CLAIMED
    assert step1.claimed_by == "state"


def test_migrate_warns_on_inline_step_value_overwrite(tmp_path: Path, caplog) -> None:
    plan = _base_plan()
    found = plan.find_step("phase-a.step1")
    assert found is not None
    found[1].status = StepStatus.DONE

    plan_path = tmp_path / "plan.yaml"
    save_plan(plan, plan_path)
    _write_state(
        plan_path, {"steps": {"phase-a.step1": {"status": "claimed", "claimed_by": "state"}}}
    )

    caplog.set_level(logging.WARNING, logger="vectl.migration")
    migrate_from_split_state(plan_path)

    assert "state.json overwriting inline plan.yaml step value" in caplog.text
    assert "step_id=phase-a.step1" in caplog.text
    assert "field=status" in caplog.text


def test_migrate_empty_state(tmp_path: Path) -> None:
    plan_path = tmp_path / "plan.yaml"
    save_plan(_base_plan(), plan_path)
    state_path = _write_state(plan_path, {})

    result = migrate_from_split_state(plan_path)

    assert result.migrated_steps == 0
    assert result.migrated_phases == 0
    assert result.already_migrated is False
    assert not state_path.exists()
    assert not state_path.with_suffix(".json.migrated").exists()


def test_migrate_already_migrated(tmp_path: Path) -> None:
    plan_path = tmp_path / "plan.yaml"
    save_plan(_base_plan(), plan_path)

    state_path = resolve_state_path(plan_path)
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.with_suffix(".json.migrated").write_text("migrated", encoding="utf-8")

    result = migrate_from_split_state(plan_path)

    assert result.already_migrated is True
    assert result.migrated_steps == 0
    assert result.migrated_phases == 0
    assert result.skipped_orphans == []


def test_migrate_no_state_file(tmp_path: Path) -> None:
    plan_path = tmp_path / "plan.yaml"
    save_plan(_base_plan(), plan_path)

    result = migrate_from_split_state(plan_path)

    assert result.already_migrated is False
    assert result.migrated_steps == 0
    assert result.migrated_phases == 0


def test_migrate_idempotent(tmp_path: Path) -> None:
    plan_path = tmp_path / "plan.yaml"
    save_plan(_base_plan(), plan_path)
    state_path = _write_state(plan_path, {"steps": {"phase-a.step1": {"status": "done"}}})

    first = migrate_from_split_state(plan_path)
    second = migrate_from_split_state(plan_path)

    assert first.already_migrated is False
    assert first.migrated_steps == 1
    assert not state_path.exists()
    assert state_path.with_suffix(".json.migrated").exists()
    assert second.already_migrated is True
    assert second.migrated_steps == 0
    assert second.migrated_phases == 0


def test_migrate_clipboard(tmp_path: Path) -> None:
    plan_path = tmp_path / "plan.yaml"
    save_plan(_base_plan(), plan_path)
    _write_state(
        plan_path,
        {
            "clipboard": {
                "author": "reviewer",
                "summary": "handoff",
                "content": "carry this context",
                "written_at": "2026-03-01T09:00:00Z",
                "expires_at": "2026-03-02T09:00:00Z",
            }
        },
    )

    result = migrate_from_split_state(plan_path)
    loaded, _ = load_plan_definition(plan_path)

    assert result.already_migrated is False
    assert loaded.clipboard is not None
    assert loaded.clipboard.author == "reviewer"
    assert loaded.clipboard.content == "carry this context"


def test_migrate_creates_git_commit(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    for command in (
        ["git", "init"],
        ["git", "config", "user.email", "test@example.com"],
        ["git", "config", "user.name", "Test User"],
    ):
        result = subprocess.run(command, cwd=repo_root, capture_output=True, text=True)
        assert result.returncode == 0, result.stderr

    plan_path = repo_root / "plan.yaml"
    save_plan(_base_plan(), plan_path)
    subprocess.run(
        ["git", "add", "plan.yaml"], cwd=repo_root, capture_output=True, text=True, check=True
    )
    subprocess.run(
        ["git", "commit", "-m", "seed"], cwd=repo_root, capture_output=True, text=True, check=True
    )

    state_path = _write_state(plan_path, {"steps": {"phase-a.step1": {"status": "done"}}})
    migrate_from_split_state(plan_path)

    commit_count = subprocess.run(
        ["git", "rev-list", "--count", "HEAD"],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=True,
    )
    assert commit_count.stdout.strip() == "2"

    latest_subject = subprocess.run(
        ["git", "log", "-1", "--pretty=%s"],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=True,
    )
    assert latest_subject.stdout.strip() == "[vectl] migrate: merge state into plan"

    raw_plan = yaml.safe_load(plan_path.read_text(encoding="utf-8"))
    assert raw_plan["phases"][0]["steps"][0]["status"] == "done"
    assert state_path.with_suffix(".json.migrated").exists()
