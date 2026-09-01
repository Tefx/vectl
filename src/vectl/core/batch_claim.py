from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Dict, List, Optional

from invar_runtime import Contract, post, pre


_logger = logging.getLogger(__name__)


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


@pre(Contract(lambda claims: isinstance(claims, list), "claims is list"))
@post(Contract(lambda result: isinstance(result, list), "failures is list"))
def _duplicate_request_failures(claims: list[BatchClaimItem]) -> list[ClaimFailure]:
    """Return duplicate request failures in input order.

    >>> _duplicate_request_failures([BatchClaimItem('a', 'x'), BatchClaimItem('a', 'y')])[0].reason
    'duplicate step_id in batch request'
    """
    seen: set[str] = set()
    duplicates: list[ClaimFailure] = []
    for item in claims:
        if item.step_id in seen:
            duplicates.append(
                ClaimFailure(
                    step_id=item.step_id,
                    agent=item.agent,
                    reason="duplicate step_id in batch request",
                )
            )
        seen.add(item.step_id)
    return duplicates


@pre(Contract(lambda failed: isinstance(failed, list), "failed is list"))
@post(Contract(lambda result: isinstance(result, str) and result != "", "markdown is non-empty"))
def _failure_markdown(failed: list[ClaimFailure]) -> str:
    """Render a short failure summary.

    >>> _failure_markdown([ClaimFailure('s', 'a', 'bad')])
    'Batch claim failed: s: bad'
    """
    if not failed:
        return "Batch claim failed"
    details = "; ".join(f"{failure.step_id}: {failure.reason}" for failure in failed)
    return f"Batch claim failed: {details}"


@pre(Contract(lambda claimed: isinstance(claimed, list), "claimed is list"))
@post(Contract(lambda result: isinstance(result, str) and result != "", "markdown is non-empty"))
def _success_markdown(claimed: list[BatchClaimItem]) -> str:
    """Render a short success summary.

    >>> _success_markdown([BatchClaimItem('s', 'a')])
    'Claimed 1 step(s): s'
    """
    return f"Claimed {len(claimed)} step(s): " + ", ".join(item.step_id for item in claimed)


# @invar:allow shell_boundary: batch claim contract is a public core facade over plan/claims persistence.
@pre(Contract(lambda request: isinstance(request.claims, list), "claims is list"))
@pre(Contract(lambda request: len(request.claims) > 0, "claims not empty"))
@post(
    Contract(
        lambda result: getattr(result, "ok", False) is True
        or len(getattr(result, "claimed", [])) == 0,
        "result logic",
    )
)
def claim_many(request: BatchClaimRequest) -> BatchClaimResult:
    """Explicit atomic batch claim operation.

    Must not accept `count`, auto-select steps, or embed dispatch policy.

    >>> import os, tempfile, yaml
    >>> with tempfile.TemporaryDirectory() as tmp:
    ...     plan_path = os.path.join(tmp, 'plan.yaml')
    ...     _ = open(plan_path, 'w', encoding='utf-8').write(yaml.dump({'project': 'doctest', 'phases': [{'id': 'p', 'name': 'P', 'steps': [{'id': 's', 'name': 'S'}]}]}))
    ...     old = os.environ.get('VECTL_PLAN_PATH')
    ...     os.environ['VECTL_PLAN_PATH'] = plan_path
    ...     result = claim_many(BatchClaimRequest([BatchClaimItem('p.s', 'agent')]))
    ...     outcome = (result.ok, len(result.claimed), len(result.failed))
    ...     if old is None: _ = os.environ.pop('VECTL_PLAN_PATH', None)
    ...     else: os.environ['VECTL_PLAN_PATH'] = old
    >>> outcome
    (True, 1, 0)
    """
    duplicate_failures = _duplicate_request_failures(request.claims)
    if duplicate_failures:
        return BatchClaimResult(
            ok=False,
            claimed=[],
            failed=duplicate_failures,
            affinity_warnings=[],
            markdown=_failure_markdown(duplicate_failures),
            lock_notice=None,
        )

    import vectl.io as vectl_io
    from vectl import claims as claims_api
    from vectl import core_plan_mutations
    from vectl.models import AffinityError, PlanError
    from vectl.plan_path import resolve_plan_path

    plan_path = resolve_plan_path(None)
    plan, base_hash = vectl_io.load_plan_definition(plan_path)
    working_plan = plan.model_copy(deep=True)

    failed: list[ClaimFailure] = []
    affinity_warnings: list[str] = []
    acquired_entries: list[claims_api.ClaimEntry] = []
    for item in request.claims:
        try:
            working_plan, claim_result = core_plan_mutations.claim_step(
                working_plan,
                item.step_id,
                item.agent,
                force=request.force,
                claims_path=None,
            )
            found = working_plan.find_step(item.step_id)
            if found is None or found[1].claimed_at is None:
                raise PlanError(f"Step '{item.step_id}' claim state was not recorded")
            step = found[1]
            acquired_entries.append(
                claims_api.ClaimEntry(
                    step_id=step.id,
                    branch="",
                    agent=item.agent,
                    claimed_at=step.claimed_at,
                )
            )
        except (AffinityError, PlanError) as exc:
            reason = str(exc)
            if "status: claimed" in reason:
                reason = f"Step '{item.step_id}' is already claimed"
            failed.append(ClaimFailure(item.step_id, item.agent, reason))
            continue
        warning = getattr(claim_result, "warning_message", "")
        if warning:
            affinity_warnings.append(warning)

    if failed:
        return BatchClaimResult(
            ok=False,
            claimed=[],
            failed=failed,
            affinity_warnings=affinity_warnings,
            markdown=_failure_markdown(failed),
            lock_notice=None,
        )

    branch = claims_api.get_current_branch()
    claims_path = claims_api.resolve_claims_path(plan_path)
    acquired_entries = [
        claims_api.ClaimEntry(
            step_id=entry.step_id,
            branch=branch,
            agent=entry.agent,
            claimed_at=entry.claimed_at,
        )
        for entry in acquired_entries
    ]
    acquire_result = claims_api.acquire_claims(
        [(entry.step_id, entry.agent) for entry in acquired_entries],
        branch,
        claims_path,
        claimed_at_by_step={entry.step_id: entry.claimed_at for entry in acquired_entries},
    )
    try:
        acquire_result.unwrap()
    except Exception:
        reason = acquire_result.failure()
        failed = [ClaimFailure(item.step_id, item.agent, reason) for item in request.claims]
        return BatchClaimResult(
            ok=False,
            claimed=[],
            failed=failed,
            affinity_warnings=affinity_warnings,
            markdown=_failure_markdown(failed),
            lock_notice=None,
        )

    try:
        vectl_io.save_plan(
            working_plan,
            plan_path,
            expected_hash=base_hash,
            commit_message=f"vectl: claim-many {len(request.claims)} step(s)",
        )
    except Exception:
        try:
            expected_by_key = {
                f"{entry.branch}:{entry.step_id}": entry for entry in acquired_entries
            }
            with claims_api._locked_claims_file(claims_path):
                claims = claims_api._read_claims_file(claims_path)
                if all(claims.get(key) == entry for key, entry in expected_by_key.items()):
                    for key in expected_by_key:
                        claims.pop(key)
                    claims_api._write_claims_file(claims_path, claims)
                else:
                    _logger.warning("Batch claim compensation skipped because claims no longer match")
        except Exception:
            _logger.exception("Batch claim compensation failed")
        raise

    return BatchClaimResult(
        ok=True,
        claimed=list(request.claims),
        failed=[],
        affinity_warnings=affinity_warnings,
        markdown=_success_markdown(request.claims),
        lock_notice=None,
    )


@pre(
    Contract(
        lambda claims, guidance=False, force=False: isinstance(claims, list)
        and isinstance(guidance, bool)
        and isinstance(force, bool),
        "type checks",
    )
)
@pre(Contract(lambda claims, guidance=False, force=False: len(claims) > 0, "claims not empty"))
@post(
    Contract(
        lambda result: getattr(result, "ok", False) is True
        or len(getattr(result, "claimed", [])) == 0,
        "result logic",
    )
)
def claim_steps(
    claims: List[Dict[str, str]], guidance: bool = False, force: bool = False
) -> BatchClaimResult:
    """Alternative signature taking dicts directly for claims.

    >>> import os, tempfile, yaml
    >>> with tempfile.TemporaryDirectory() as tmp:
    ...     plan_path = os.path.join(tmp, 'plan.yaml')
    ...     _ = open(plan_path, 'w', encoding='utf-8').write(yaml.dump({'project': 'doctest', 'phases': [{'id': 'p', 'name': 'P', 'steps': [{'id': 's', 'name': 'S'}]}]}))
    ...     old = os.environ.get('VECTL_PLAN_PATH')
    ...     os.environ['VECTL_PLAN_PATH'] = plan_path
    ...     result = claim_steps([{'step_id': 'p.s', 'agent': 'agent'}], guidance=False, force=False)
    ...     outcome = (result.ok, result.claimed[0].step_id)
    ...     if old is None: _ = os.environ.pop('VECTL_PLAN_PATH', None)
    ...     else: os.environ['VECTL_PLAN_PATH'] = old
    >>> outcome
    (True, 'p.s')
    """
    items = [BatchClaimItem(step_id=c["step_id"], agent=c["agent"]) for c in claims]
    return claim_many(BatchClaimRequest(claims=items, guidance=guidance, force=force))
