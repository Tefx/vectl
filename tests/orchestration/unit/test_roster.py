"""Runtime behavior tests for orchestration roster.

Authority:
    docs/ORCHESTRATION-PLANE-INTERFACES.md section 4.2
    docs/ORCHESTRATION-PLANE-ARCHITECTURE.md section 5.2
    docs/ORCHESTRATION-PLANE-ISOLATION-SEMANTICS.md section 5.3
"""

from __future__ import annotations

from datetime import datetime

from vectl.orchestration.contracts import IsolationMode, WorkLease
from vectl.orchestration.roster import Roster


def _lease(role: str, session_id: str | None = "ses-1") -> WorkLease:
    return WorkLease(role=role, runner="claude", agent_id="python-executor", session_id=session_id)


def test_claim_returns_none_when_role_unregistered() -> None:
    roster = Roster()

    claim = roster.claim("python-executor")

    assert claim is None


def test_register_and_claim_default_reuses_warm_resource() -> None:
    roster = Roster()
    lease = _lease("python-executor", session_id="ses-123")
    roster.register(lease, expires_at=datetime.now().timestamp() + 60.0)

    claim = roster.claim("python-executor", isolation=IsolationMode.DEFAULT)

    assert claim == lease


def test_claim_independent_never_reuses_warm_resource() -> None:
    roster = Roster()
    lease = _lease("python-executor", session_id="ses-123")
    roster.register(lease, expires_at=datetime.now().timestamp() + 60.0)

    claim = roster.claim("python-executor", isolation=IsolationMode.INDEPENDENT)

    assert claim is None
    snapshot = roster.snapshot()
    assert "python-executor" in snapshot.available_agents
    assert "python-executor" not in snapshot.working_agents


def test_expired_registration_is_not_claimable() -> None:
    roster = Roster()
    roster.register(_lease("python-executor"), expires_at=datetime.now().timestamp() - 1.0)

    claim = roster.claim("python-executor")

    assert claim is None
    assert "python-executor" not in roster.snapshot().available_agents


def test_release_moves_claimed_role_back_to_available() -> None:
    roster = Roster()
    lease = _lease("python-executor")
    roster.register(lease, expires_at=datetime.now().timestamp() + 60.0)
    claimed = roster.claim("python-executor")

    assert claimed == lease
    assert "python-executor" in roster.snapshot().working_agents

    roster.release(lease)

    post_release = roster.snapshot()
    assert "python-executor" in post_release.available_agents
    assert "python-executor" not in post_release.working_agents


def test_snapshot_reusable_sessions_reports_session_ids() -> None:
    roster = Roster()
    roster.register(
        _lease("python-executor", session_id="ses-123"), datetime.now().timestamp() + 60.0
    )
    roster.register(
        _lease("python-tester", session_id="ses-456"), datetime.now().timestamp() + 60.0
    )

    snapshot = roster.snapshot()

    assert snapshot.reusable_sessions == ("ses-123", "ses-456")
