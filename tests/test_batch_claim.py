"""Behavioral coverage for atomic batch claim persistence."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
import yaml

from vectl.core.batch_claim import BatchClaimItem, BatchClaimRequest, claim_many


@pytest.fixture
def plan_file(tmp_path: Path):
    plan_dict = {
        "project": "batch-claim-test",
        "phases": [
            {
                "id": "p1",
                "name": "Phase 1",
                "status": "pending",
                "steps": [
                    {"id": "s1", "name": "Step 1", "status": "pending"},
                    {"id": "s2", "name": "Step 2", "status": "pending"},
                    {"id": "s3", "name": "Step 3", "status": "pending"},
                    {
                        "id": "s4",
                        "name": "Step 4",
                        "status": "claimed",
                        "claimed_by": "agent1",
                    },
                    {
                        "id": "s5",
                        "name": "Step 5",
                        "status": "pending",
                        "agent": "agent1",
                        "affinity": "exclusive",
                    },
                ],
            }
        ],
    }
    path = tmp_path / "plan.yaml"
    path.write_text(yaml.safe_dump(plan_dict), encoding="utf-8")

    old_plan = os.environ.get("VECTL_PLAN_PATH")
    os.environ["VECTL_PLAN_PATH"] = str(path)
    yield path
    if old_plan is None:
        os.environ.pop("VECTL_PLAN_PATH", None)
    else:
        os.environ["VECTL_PLAN_PATH"] = old_plan


def test_claim_many_saves_once_and_preserves_input_order(
    plan_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from vectl.io import save_plan

    save_calls = 0

    def count_save(plan, path, **kwargs):
        nonlocal save_calls
        save_calls += 1
        return save_plan(plan, path, **kwargs)

    monkeypatch.setattr("vectl.io.save_plan", count_save)

    result = claim_many(
        BatchClaimRequest(
            claims=[
                BatchClaimItem(step_id="p1.s1", agent="worker-a"),
                BatchClaimItem(step_id="p1.s2", agent="worker-b"),
            ]
        )
    )

    assert save_calls == 1
    assert result.ok is True
    assert [(item.step_id, item.agent) for item in result.claimed] == [
        ("p1.s1", "worker-a"),
        ("p1.s2", "worker-b"),
    ]
    assert result.failed == []


def test_claim_many_validates_all_requests_before_persisting(plan_file: Path) -> None:
    result = claim_many(
        BatchClaimRequest(
            claims=[
                BatchClaimItem(step_id="p1.s1", agent="worker-a"),
                BatchClaimItem(step_id="p1.s4", agent="worker-b"),
            ]
        )
    )

    assert result.ok is False
    assert result.claimed == []
    assert [(item.step_id, item.reason) for item in result.failed] == [
        ("p1.s4", "Step 'p1.s4' is already claimed"),
    ]
    assert yaml.safe_load(plan_file.read_text())["phases"][0]["steps"][0]["status"] == "pending"


def test_claim_many_rejects_duplicate_request_ids(plan_file: Path) -> None:
    result = claim_many(
        BatchClaimRequest(
            claims=[
                BatchClaimItem(step_id="p1.s1", agent="worker-a"),
                BatchClaimItem(step_id="p1.s1", agent="worker-b"),
            ]
        )
    )

    assert result.ok is False
    assert result.claimed == []
    assert "duplicate" in result.failed[0].reason.lower()


def test_claim_many_enforces_exclusive_affinity(plan_file: Path) -> None:
    result = claim_many(
        BatchClaimRequest(claims=[BatchClaimItem(step_id="p1.s5", agent="worker-b")])
    )

    assert result.ok is False
    assert result.claimed == []
    assert "affinity" in result.failed[0].reason.lower()


def test_claim_many_force_overrides_exclusive_affinity(plan_file: Path) -> None:
    result = claim_many(
        BatchClaimRequest(
            claims=[BatchClaimItem(step_id="p1.s5", agent="worker-b")], force=True
        )
    )

    assert result.ok is True
    assert len(result.claimed) == 1


def test_claim_many_uses_canonical_ids_and_one_timestamp(
    plan_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from vectl.claims import load_claims, repair_claims
    from vectl.io import load_plan_definition
    from vectl.plan_path import resolve_claims_path

    monkeypatch.setattr("vectl.claims.get_current_branch", lambda: "batch-test")
    result = claim_many(
        BatchClaimRequest(claims=[BatchClaimItem(step_id="p1.s1", agent="worker-a")])
    )

    assert result.ok is True
    plan, _ = load_plan_definition(plan_file)
    step = plan.phases[0].steps[0]
    claim = load_claims(resolve_claims_path(plan_file))["batch-test:s1"]
    assert claim.step_id == step.id == "s1"
    assert claim.agent == step.claimed_by == "worker-a"
    assert claim.claimed_at == step.claimed_at
    assert (
        repair_claims(
            plan,
            plan_file,
            resolve_claims_path(plan_file),
            dry_run=True,
            step_id="p1.s1",
        ).changed
        is False
    )


def test_claim_many_cas_conflict_preserves_concurrent_plan_and_cleans_claims(
    plan_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from vectl import claims
    from vectl.io import CASConflictError
    from vectl.plan_path import resolve_claims_path

    monkeypatch.setattr("vectl.claims.get_current_branch", lambda: "batch-test")
    original_acquire = claims.acquire_claims

    def acquire_then_change_plan(*args, **kwargs):
        result = original_acquire(*args, **kwargs)
        plan_data = yaml.safe_load(plan_file.read_text())
        plan_data["project"] = "concurrent-change"
        plan_file.write_text(yaml.safe_dump(plan_data), encoding="utf-8")
        return result

    monkeypatch.setattr("vectl.claims.acquire_claims", acquire_then_change_plan)

    with pytest.raises(CASConflictError):
        claim_many(BatchClaimRequest(claims=[BatchClaimItem(step_id="p1.s1", agent="worker-a")]))

    assert yaml.safe_load(plan_file.read_text())["project"] == "concurrent-change"
    assert claims.load_claims(resolve_claims_path(plan_file)) == {}


def test_claim_many_save_failure_keeps_replacement_claim(
    plan_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from vectl.claims import ClaimEntry, load_claims, save_claims
    from vectl.models import PlanError
    from vectl.plan_path import resolve_claims_path

    claims_path = resolve_claims_path(plan_file)
    monkeypatch.setattr("vectl.claims.get_current_branch", lambda: "batch-test")

    def replace_then_fail(*args, **kwargs):
        save_claims(
            {
                "batch-test:s1": ClaimEntry(
                    step_id="s1",
                    branch="batch-test",
                    agent="replacement",
                    claimed_at="2025-01-01T00:00:00Z",
                )
            },
            claims_path,
        )
        raise PlanError("save failed")

    monkeypatch.setattr("vectl.io.save_plan", replace_then_fail)

    with pytest.raises(PlanError, match="save failed"):
        claim_many(BatchClaimRequest(claims=[BatchClaimItem(step_id="p1.s1", agent="worker-a")]))

    stored = load_claims(claims_path)["batch-test:s1"]
    assert stored.agent == "replacement"


def test_claim_many_attempts_one_best_effort_commit_and_uses_metadata_lock(
    plan_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    attempted_messages: list[str] = []

    def fake_git_commit(path: Path, message: str) -> bool:
        attempted_messages.append(message)
        return False

    monkeypatch.setattr("vectl.io._git_commit_plan", fake_git_commit)
    result = claim_many(
        BatchClaimRequest(claims=[BatchClaimItem(step_id="p1.s1", agent="worker-a")])
    )

    assert result.ok is True
    assert attempted_messages == ["vectl: claim-many 1 step(s)"]
    assert not (plan_file.parent / "plan.yaml.lock").exists()
    assert (plan_file.parent / ".vectl" / "plan.yaml.lock").exists()
