"""Focused core tests for recovery behavior."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from vectl.claims import load_claims
from vectl.core import (
    apply_recovery,
    claim_step,
    complete_step,
    defer_step,
    preview_recovery,
    validate_plan,
)
from vectl.io import load_plan_definition, save_plan
from vectl.models import (
    Phase,
    PhaseStatus,
    Plan,
    PlanError,
    Step,
    StepStatus,
)


def _write_plan(path: Path, step_name: str) -> None:
    plan = Plan(
        project="recover-test",
        phases=[
            Phase(
                id="p1",
                name="Phase 1",
                steps=[Step(id="p1.s1", name=step_name)],
            )
        ],
    )
    save_plan(plan, path)


def test_recover_restores_from_backup(tmp_path: Path) -> None:
    plan_path = tmp_path / "plan.yaml"
    backup_path = tmp_path / "plan.yaml.bak"

    _write_plan(plan_path, "Current Name")
    _write_plan(backup_path, "Backup Name")

    result = preview_recovery(plan_path, backup_path)
    apply_recovery(backup_path, plan_path)

    restored_plan, _ = load_plan_definition(plan_path)
    restored = restored_plan.find_step("p1.s1")
    assert restored is not None
    assert restored[1].name == "Backup Name"
    assert len(result.diff.step_changes) == 1
    assert "Total changes:" in result.diff_summary


def test_preview_recovery_does_not_write_plan(tmp_path: Path) -> None:
    plan_path = tmp_path / "plan.yaml"
    backup_path = tmp_path / "plan.yaml.bak"

    _write_plan(plan_path, "Current Name")
    _write_plan(backup_path, "Backup Name")
    before = plan_path.read_text(encoding="utf-8")

    result = preview_recovery(plan_path, backup_path)

    after = plan_path.read_text(encoding="utf-8")
    assert before == after
    assert result.restored is False
    assert len(result.diff.step_changes) == 1


def test_apply_recovery_writes_backup_content(tmp_path: Path) -> None:
    plan_path = tmp_path / "plan.yaml"
    backup_path = tmp_path / "plan.yaml.bak"

    _write_plan(plan_path, "Current Name")
    _write_plan(backup_path, "Backup Name")

    apply_recovery(backup_path, plan_path)

    restored_plan, _ = load_plan_definition(plan_path)
    restored = restored_plan.find_step("p1.s1")
    assert restored is not None
    assert restored[1].name == "Backup Name"


def test_recover_no_backup_error(tmp_path: Path) -> None:
    plan_path = tmp_path / "plan.yaml"
    missing_backup = tmp_path / "missing-plan.yaml.bak"
    _write_plan(plan_path, "Current Name")

    with pytest.raises(PlanError, match="Backup file not found"):
        apply_recovery(missing_backup, plan_path)


# ---------------------------------------------------------------------------
# Validate plan tests
# ---------------------------------------------------------------------------


def test_validate_plan_validates_dag() -> None:
    """Validate plan structure correctly validates DAG structure."""
    plan = Plan(
        project="dag-test",
        phases=[
            Phase(
                id="core",
                name="Core",
                steps=[
                    Step(id="s1", name="Step 1"),
                    Step(id="s2", name="Step 2", depends_on=["s1"]),
                ],
            ),
        ],
    )

    errors = validate_plan(plan)

    assert len(errors) == 0


def test_validate_plan_detects_duplicate_phase_ids() -> None:
    """Validate plan detects duplicate phase IDs."""
    plan = Plan(
        project="duplicate-test",
        phases=[
            Phase(id="core", name="Core"),
            Phase(id="core", name="Core Duplicate"),  # Duplicate!
        ],
    )

    errors = validate_plan(plan)

    assert len(errors) == 1
    assert "Duplicate phase ID" in errors[0].message


def test_validate_plan_detects_duplicate_step_ids() -> None:
    """Validate plan detects duplicate step IDs within a phase."""
    plan = Plan(
        project="duplicate-test",
        phases=[
            Phase(
                id="core",
                name="Core",
                steps=[
                    Step(id="s1", name="Step 1"),
                    Step(id="s1", name="Step 1 Duplicate"),  # Duplicate!
                ],
            ),
        ],
    )

    errors = validate_plan(plan)

    assert len(errors) == 1
    assert "Duplicate step ID" in errors[0].message


def _claimable_plan() -> Plan:
    return Plan(
        project="claim-test",
        phases=[
            Phase(
                id="p1",
                name="Phase 1",
                status=PhaseStatus.PENDING,
                steps=[Step(id="p1.s1", name="Step 1")],
            )
        ],
    )


def test_claim_step_with_claims_path_writes_claim_entry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plan = _claimable_plan()
    claims_path = tmp_path / "claims.json"

    monkeypatch.setattr("vectl.core.get_current_branch", lambda: "feature/test-branch")

    updated_plan, _ = claim_step(plan, "p1.s1", "agent-1", claims_path=claims_path)
    assert updated_plan.phases[0].steps[0].status == StepStatus.CLAIMED

    claims = load_claims(claims_path)
    assert "feature/test-branch:p1.s1" in claims
    claim = claims["feature/test-branch:p1.s1"]
    assert claim.agent == "agent-1"
    assert claim.branch == "feature/test-branch"
    assert claim.step_id == "p1.s1"


def test_claim_step_calls_cleanup_stale_claims(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plan = _claimable_plan()
    claims_path = tmp_path / "claims.json"
    called: dict[str, Path] = {}

    def _cleanup_spy(path: Path, ttl_hours: float = 2.0) -> int:
        called["path"] = path
        return 0

    monkeypatch.setattr("vectl.core.cleanup_stale_claims", _cleanup_spy)
    monkeypatch.setattr("vectl.core.get_current_branch", lambda: "feature/test-branch")

    claim_step(plan, "p1.s1", "agent-1", claims_path=claims_path)

    assert called["path"] == claims_path


def test_complete_step_sets_done_at_timestamp() -> None:
    plan = _claimable_plan()
    claim_step(plan, "p1.s1", "agent-1")

    updated_plan = complete_step(plan, "p1.s1", "evidence text")
    done_step = updated_plan.phases[0].steps[0]

    assert done_step.status == StepStatus.DONE
    assert done_step.evidence == "evidence text"
    assert done_step.done_at is not None
    parsed_done_at = datetime.fromisoformat(done_step.done_at)
    assert parsed_done_at.tzinfo is not None
    assert parsed_done_at.utcoffset() == timezone.utc.utcoffset(parsed_done_at)


def test_complete_step_releases_claim_from_claims_json(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plan = _claimable_plan()
    claims_path = tmp_path / "claims.json"

    monkeypatch.setattr("vectl.core.get_current_branch", lambda: "feature/test-branch")

    claim_step(plan, "p1.s1", "agent-1", claims_path=claims_path)
    claims_before = load_claims(claims_path)
    assert "feature/test-branch:p1.s1" in claims_before

    complete_step(plan, "p1.s1", "evidence text", claims_path=claims_path)

    claims_after = load_claims(claims_path)
    assert "feature/test-branch:p1.s1" not in claims_after


def test_defer_step_releases_claim_from_claims_json(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plan = _claimable_plan()
    claims_path = tmp_path / "claims.json"

    monkeypatch.setattr("vectl.core.get_current_branch", lambda: "feature/test-branch")

    claim_step(plan, "p1.s1", "agent-1", claims_path=claims_path)
    claims_before = load_claims(claims_path)
    assert "feature/test-branch:p1.s1" in claims_before

    defer_step(plan, "p1.s1", claims_path=claims_path)

    claims_after = load_claims(claims_path)
    assert "feature/test-branch:p1.s1" not in claims_after
