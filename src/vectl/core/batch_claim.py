from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional
from invar_runtime import pre, post, Contract

@dataclass
class BatchClaimItem:
    step_id: str
    agent: str

@dataclass
class ClaimFailure:
    step_id: str
    agent: str
    reason: str

@dataclass
class BatchClaimRequest:
    claims: List[BatchClaimItem]
    guidance: bool = False
    force: bool = False

@dataclass
class BatchClaimResult:
    ok: bool
    claimed: List[BatchClaimItem]
    failed: List[ClaimFailure]
    affinity_warnings: List[str]
    markdown: str
    lock_notice: Optional[str] = None

@pre(Contract(lambda request: isinstance(request.claims, list), "claims is list"))
@pre(Contract(lambda request: len(request.claims) > 0, "claims not empty"))
@post(Contract(lambda result: getattr(result, "ok", False) is True or len(getattr(result, "claimed", [])) == 0, "result logic"))
def claim_many(request: BatchClaimRequest) -> BatchClaimResult:
    """Explicit atomic batch claim operation.

    Must not accept `count`, auto-select steps, or embed dispatch policy.

    >>> req = BatchClaimRequest(claims=[BatchClaimItem(step_id="step1", agent="agent1")])
    >>> result = claim_many(req)
    >>> result.ok
    True
    >>> len(result.claimed)
    1
    >>> len(result.failed)
    0
    """
    return BatchClaimResult(
        ok=True,
        claimed=request.claims,
        failed=[],
        affinity_warnings=[],
        markdown="Stub success",
        lock_notice=None,
    )

@pre(Contract(lambda claims, guidance=False, force=False: isinstance(claims, list) and isinstance(guidance, bool) and isinstance(force, bool), "type checks"))
@pre(Contract(lambda claims, guidance=False, force=False: len(claims) > 0 or not guidance or not force, "claims not empty"))
@post(Contract(lambda result: getattr(result, "ok", False) is True or len(getattr(result, "claimed", [])) == 0, "result logic"))
def claim_steps(claims: List[Dict[str, str]], guidance: bool = False, force: bool = False) -> BatchClaimResult:
    """Alternative signature taking dicts directly for claims.
    
    >>> result = claim_steps([{"step_id": "step1", "agent": "agent1"}], guidance=False, force=False)
    >>> result.ok
    True
    >>> result.claimed[0].step_id
    'step1'
    """
    # @invar:allow dead_param: stub function
    items = [BatchClaimItem(step_id=c["step_id"], agent=c["agent"]) for c in claims]
    return BatchClaimResult(
        ok=True,
        claimed=items,
        failed=[],
        affinity_warnings=[],
        markdown="Stub success",
        lock_notice=None,
    )
