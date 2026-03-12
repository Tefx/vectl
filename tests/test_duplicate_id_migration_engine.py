"""Regression tests for duplicate step-ID migration engine behavior."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from vectl.core import (
    apply_duplicate_step_id_migration,
    build_duplicate_step_id_migration_dry_run,
)
from vectl.io import load_plan_definition, save_plan
from vectl.models import CASConflictError, Phase, Plan, PlanError, Step, StepStatus


def _plan_with_duplicate_gate_and_fanout() -> Plan:
    return Plan(
        project="duplicate-migration",
        phases=[
            Phase(
                id="phase-a",
                name="Phase A",
                steps=[
                    Step(id="gate", name="A Gate"),
                    Step(id="phase-a-fanout", name="A Fanout", depends_on=["gate"]),
                ],
            ),
            Phase(
                id="phase-b",
                name="Phase B",
                steps=[
                    Step(id="phase-b.gate", name="Existing Colliding ID"),
                    Step(id="gate", name="B Gate"),
                    Step(id="phase-b-fanout", name="B Fanout", depends_on=["gate"]),
                ],
            ),
        ],
    )


def _duplicate_gate_plan_with_claim() -> Plan:
    return Plan(
        project="duplicate-migration-claim",
        phases=[
            Phase(
                id="phase-a",
                name="Phase A",
                steps=[Step(id="gate", name="A Gate")],
            ),
            Phase(
                id="phase-b",
                name="Phase B",
                steps=[
                    Step(
                        id="gate",
                        name="B Gate",
                        status=StepStatus.CLAIMED,
                        claimed_by="agent-1",
                    )
                ],
            ),
        ],
    )


def test_dry_run_produces_deterministic_duplicate_map_and_dep_rewrites() -> None:
    plan = _plan_with_duplicate_gate_and_fanout()

    report, migrated = build_duplicate_step_id_migration_dry_run(plan)

    assert report.run_mode == "dry-run"
    assert [group.step_id for group in report.duplicate_groups] == ["gate"]
    assert report.rename_map[0].phase_id == "phase-b"
    assert report.rename_map[0].old_step_id == "gate"
    assert report.rename_map[0].new_step_id == "phase-b.gate--dup2"
    assert report.affected_phases == ["phase-b"]

    rewrites = {(item.phase_id, item.step_id): item for item in report.depends_on_rewrites}
    assert ("phase-b", "phase-b-fanout") in rewrites
    assert rewrites[("phase-b", "phase-b-fanout")].old_depends_on == ["gate"]
    assert rewrites[("phase-b", "phase-b-fanout")].new_depends_on == ["phase-b.gate--dup2"]

    phase_b = migrated.find_phase("phase-b")
    assert phase_b is not None
    b_steps = [step for step in phase_b.steps]
    assert [step.id for step in b_steps] == ["phase-b.gate", "phase-b.gate--dup2", "phase-b-fanout"]
    assert b_steps[2].depends_on == ["phase-b.gate--dup2"]


def test_apply_blocks_when_duplicate_occurrence_is_claimed_in_plan(tmp_path: Path) -> None:
    plan_path = tmp_path / "plan.yaml"
    save_plan(_duplicate_gate_plan_with_claim(), plan_path)

    with pytest.raises(PlanError, match="blocking_reason=claimed_step_conflict"):
        apply_duplicate_step_id_migration(plan_path)

    loaded, _ = load_plan_definition(plan_path)
    gate_ids = [step.id for phase in loaded.phases for step in phase.steps if step.id == "gate"]
    assert len(gate_ids) == 2


def test_dry_run_reports_claim_conflicts_from_claims_store(tmp_path: Path) -> None:
    plan = Plan(
        project="duplicate-migration-claims",
        phases=[
            Phase(id="phase-a", name="Phase A", steps=[Step(id="gate", name="A Gate")]),
            Phase(id="phase-b", name="Phase B", steps=[Step(id="gate", name="B Gate")]),
        ],
    )
    claims_path = tmp_path / "claims.json"
    claims_path.write_text(
        json.dumps(
            {
                "feature/gates:gate": {
                    "step_id": "gate",
                    "branch": "feature/gates",
                    "agent": "agent-claims",
                    "claimed_at": "2026-03-13T00:00:00Z",
                }
            }
        ),
        encoding="utf-8",
    )

    report, _ = build_duplicate_step_id_migration_dry_run(
        plan,
        claims_path=claims_path,
        branch="feature/gates",
    )

    assert report.requires_manual_follow_up is True
    assert {(c.phase_id, c.step_id, c.claimed_by) for c in report.claimed_step_conflicts} == {
        ("phase-a", "gate", "agent-claims"),
        ("phase-b", "gate", "agent-claims"),
    }


def test_apply_uses_cas_and_does_not_partial_write_on_conflict(tmp_path: Path) -> None:
    plan_path = tmp_path / "plan.yaml"
    save_plan(_plan_with_duplicate_gate_and_fanout(), plan_path)
    _, old_hash = load_plan_definition(plan_path)

    concurrent = _plan_with_duplicate_gate_and_fanout()
    concurrent.context = "concurrent mutation"
    save_plan(concurrent, plan_path)

    with pytest.raises(CASConflictError):
        apply_duplicate_step_id_migration(plan_path, expected_hash=old_hash)

    loaded, _ = load_plan_definition(plan_path)
    assert loaded.context == "concurrent mutation"
    gate_ids = [step.id for phase in loaded.phases for step in phase.steps if step.id == "gate"]
    assert len(gate_ids) == 2


def test_apply_is_idempotent_after_successful_migration(tmp_path: Path) -> None:
    plan_path = tmp_path / "plan.yaml"
    save_plan(_plan_with_duplicate_gate_and_fanout(), plan_path)

    first = apply_duplicate_step_id_migration(plan_path)
    assert first.migrated is True
    assert len(first.report.rename_map) == 1
    assert first.evidence.migrated is True

    migrated_plan, _ = load_plan_definition(plan_path)
    ids = [step.id for phase in migrated_plan.phases for step in phase.steps]
    assert len(ids) == len(set(ids))

    second = apply_duplicate_step_id_migration(plan_path)
    assert second.migrated is False
    assert second.report.rename_map == []
    assert second.evidence.migrated is False
