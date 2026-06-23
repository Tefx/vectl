import pytest

from vectl.core.batch_claim import (
    BatchClaimItem,
    BatchClaimRequest,
    BatchClaimResult,
    ClaimFailure,
    claim_many,
    claim_steps,
)

def test_batch_claim_request_fields():
    req = BatchClaimRequest(
        claims=[BatchClaimItem(step_id="test", agent="agent")],
        guidance=True,
        force=True
    )
    assert req.guidance is True
    assert req.force is True
    
    # Prove non-goals are absent from signature
    with pytest.raises(TypeError):
        BatchClaimRequest(claims=[], count=2)  # type: ignore

    with pytest.raises(TypeError):
        BatchClaimRequest(claims=[], auto_select=True)  # type: ignore
        
    with pytest.raises(TypeError):
        BatchClaimRequest(claims=[], dispatch_policy="any")  # type: ignore
        
    with pytest.raises(TypeError):
        BatchClaimRequest(claims=[], reserve_steps=5)  # type: ignore

    with pytest.raises(TypeError):
        BatchClaimRequest(claims=[], defer=True)  # type: ignore

    with pytest.raises(TypeError):
        BatchClaimRequest(claims=[], skip=True)  # type: ignore

    with pytest.raises(TypeError):
        BatchClaimRequest(claims=[], reject=True)  # type: ignore

    with pytest.raises(TypeError):
        BatchClaimRequest(claims=[], complete=True)  # type: ignore

    with pytest.raises(TypeError):
        BatchClaimRequest(claims=[], ignore_cas=True)  # type: ignore

def test_batch_claim_rejects_reserved_lifecycle_value():
    # BCM-G2-NO-RESERVED-LIFECYCLE
    # Prove batch claim never accepts or emits `reserved` lifecycle/status.
    
    # 1. Enforce that BatchClaimItem cannot take a status override (it has no status field)
    with pytest.raises(TypeError):
        BatchClaimItem(step_id="foo", agent="bar", status="reserved")  # type: ignore
        
    from vectl.models import StepStatus
    # 2. Enforce Enum does NOT contain reserved, so it's impossible to serialize
    assert "reserved" not in [s.value for s in StepStatus]

    # 3. Ensure the result structure does not emit "reserved" objects
    result = BatchClaimResult(ok=False, claimed=[], failed=[], affinity_warnings=[], markdown="", lock_notice="")
    assert not hasattr(result, "reserved")

def test_batch_claim_result_fields():
    result = BatchClaimResult(
        ok=False,
        claimed=[],
        failed=[ClaimFailure(step_id="foo", agent="bar", reason="testing")],
        affinity_warnings=["warning 1"],
        markdown="Failed batch",
        lock_notice="Lock notice"
    )
    
    assert hasattr(result, "ok")
    assert hasattr(result, "claimed")
    assert hasattr(result, "failed")
    assert hasattr(result, "affinity_warnings")
    assert hasattr(result, "markdown")
    assert hasattr(result, "lock_notice")
    
    assert result.ok is False
    assert result.claimed == []
    assert len(result.failed) == 1


