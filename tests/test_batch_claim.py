import pytest
from pathlib import Path
import os
import yaml
from vectl.core.batch_claim import BatchClaimRequest, BatchClaimItem, claim_many, BatchClaimResult
from vectl.models import Plan, Phase, Step, AffinityMode

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
                    {"id": "s4", "name": "Step 4", "status": "claimed", "claimed_by": "agent1"},
                    {"id": "s5", "name": "Step 5", "status": "pending", "affinity": "agent1", "affinity_mode": "hard"},
                    {"id": "s6", "name": "Step 6", "status": "pending", "affinity": "agent1", "affinity_mode": "soft"}
                ]
            }
        ]
    }
    p = tmp_path / "plan.yaml"
    p.write_text(yaml.dump(plan_dict))
    
    old_plan = os.environ.get("VECTL_PLAN_PATH")
    os.environ["VECTL_PLAN_PATH"] = str(p)
    yield p
    if old_plan is None:
        os.environ.pop("VECTL_PLAN_PATH", None)
    else:
        os.environ["VECTL_PLAN_PATH"] = old_plan


def test_claim_many_success_multi_agent(plan_file: Path) -> None:
    req = BatchClaimRequest(
        claims=[
            BatchClaimItem(step_id="p1.s1", agent="worker-a"),
            BatchClaimItem(step_id="p1.s2", agent="worker-b")
        ],
        guidance=False,
        force=False
    )
    result = claim_many(req)
    # Expected red tests: the stubs will fail the structural expectations 
    # of complete implementation. The contract requires `ok`, `claimed`, `failed`.
    # Wait, they are expected red so we assert the correct behavior that is currently missing.
    assert result.ok is True
    assert len(result.claimed) == 2
    assert result.claimed[0].step_id == "p1.s1"
    assert result.claimed[0].agent == "worker-a"
    assert result.claimed[1].step_id == "p1.s2"
    assert result.claimed[1].agent == "worker-b"
    assert len(result.failed) == 0

def test_claim_many_conflicts(plan_file: Path) -> None:
    # Try to claim s4 which is already claimed, and s1
    req = BatchClaimRequest(
        claims=[
            BatchClaimItem(step_id="p1.s1", agent="worker-a"),
            BatchClaimItem(step_id="p1.s4", agent="worker-b")
        ],
    )
    result = claim_many(req)
    if result.ok is True:
        assert False, "claim_many missing expected failure behavior"
    assert len(result.claimed) == 0
    assert len(result.failed) == 1
    assert result.failed[0].step_id == "p1.s4"
    assert "already claimed" in result.failed[0].reason.lower()

def test_claim_many_hard_affinity(plan_file: Path) -> None:
    # Try to claim s5 with worker-b
    req = BatchClaimRequest(
        claims=[
            BatchClaimItem(step_id="p1.s5", agent="worker-b")
        ],
    )
    result = claim_many(req)
    if result.ok is True:
        assert False, "claim_many missing expected failure behavior"
    assert len(result.claimed) == 0
    assert len(result.failed) == 1
    assert result.failed[0].step_id == "p1.s5"
    assert "affinity" in result.failed[0].reason.lower()

def test_claim_many_hard_affinity_with_force(plan_file: Path) -> None:
    # Try to claim s5 with worker-b but with force
    req = BatchClaimRequest(
        claims=[
            BatchClaimItem(step_id="p1.s5", agent="worker-b")
        ],
        force=True
    )
    result = claim_many(req)
    assert result.ok is True
    assert len(result.claimed) == 1
    
def test_batch_duplicate_request_ids(plan_file: Path) -> None:
    req = BatchClaimRequest(
        claims=[
            BatchClaimItem(step_id="p1.s1", agent="worker-a"),
            BatchClaimItem(step_id="p1.s1", agent="worker-b")
        ],
    )
    result = claim_many(req)
    if result.ok is True:
        assert False, "claim_many missing expected failure behavior"
    assert len(result.failed) > 0
    assert result.failed[0].step_id == "p1.s1"
    assert "duplicate" in result.failed[0].reason.lower()


def test_claim_many_rollback_on_plan_save_failure(plan_file: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from vectl.io import save_plan
    from vectl.models import PlanError
    
    # We monkeypatch save_plan to always raise PlanError("Simulated CAS failure")
    original_save_plan = save_plan
    def fake_save_plan(plan, path):
        raise PlanError("Simulated CAS failure")

    monkeypatch.setattr("vectl.io.save_plan", fake_save_plan)
    
    req = BatchClaimRequest(
        claims=[
            BatchClaimItem(step_id="p1.s1", agent="worker-a"),
            BatchClaimItem(step_id="p1.s2", agent="worker-b")
        ],
    )
    raised = False
    try:
        claim_many(req)
    except PlanError as e:
        if "Simulated CAS failure" in str(e):
            raised = True
    if not raised:
        assert False, "claim_many rollback failed to raise PlanError"
        
    # Now check if ghost claim records exist
    from vectl.claims import load_claims
    from vectl.plan_path import resolve_claims_path
    
    claims_path = resolve_claims_path(plan_file)
    claims = load_claims(claims_path)
    
    # We should have NO new claims
    assert "p1.s1" not in claims
    assert "p1.s2" not in claims
    
    # And check the plan was not mutated
    import yaml
    data = yaml.safe_load(plan_file.read_text())
    assert data["phases"][0]["steps"][0]["status"] == "pending"
