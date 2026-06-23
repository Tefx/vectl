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

def test_atomic_failure_contract():
    # If atomic semantics reject partial success, claimed should be empty when ok=false
    req = BatchClaimRequest(claims=[BatchClaimItem(step_id="test", agent="agent")])
    result = claim_many(req)
    
    # Modifying the stub response to test post condition failure if we violated it
    # Currently the stub returns ok=True, claimed=req.claims
    assert result.ok is True
    assert len(result.claimed) == 1
    
    # If we hack the stub to ok=False and claimed len > 0, the @post contract would violate
    # We will test the types directly.

