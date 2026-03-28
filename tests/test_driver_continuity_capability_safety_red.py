"""Expected-red tests for continuity capability and replay safety.

Source authority:
- docs/DRIVER-CONTINUITY-FOUNDATION.md Section 4 (Restart/Resume/Replay)
- docs/DRIVER-CONTINUITY-FOUNDATION.md Section 6 (capability mismatch => fresh)
- Step: driver-continuity-capability-safety.design-and-test
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import pytest

from src.vectl.driver.loop import handle_dispatch
from src.vectl.driver.runner_continuity import (
    RUNNER_CONTINUITY_CAPABILITIES,
    evaluate_resume_or_replay_safety,
)
from src.vectl.driver.session import SessionPool
from src.vectl.driver.types import DriverState, ReplaySafetyEnvelope, ReplayTokenSemantics
from src.vectl.driver.worktree import Success, WorktreeBinding
from tests.fixtures.helpers.continuity_capability_safety import (
    FakePlan,
    RecordingObserver,
    RecordingRunner,
    continuity_test_config,
    create_worktree_binding,
)
from vectl.models import Action


@pytest.mark.anyio
async def test_non_resumable_runner_reuse_request_forced_fresh_expected_red(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Gemini reuse requests must be downgraded to fresh dispatch.

    Source: docs/DRIVER-CONTINUITY-FOUNDATION.md Section 4 (resume requires
    capability support) and runner_continuity capability table (gemini is
    non-resumable in bootstrap posture).
    """

    config = continuity_test_config(primary_runner="gemini")
    state = DriverState()
    observer = RecordingObserver()
    session_pool = SessionPool(config.session)
    runner = RecordingRunner("gemini")

    monkeypatch.setattr("src.vectl.driver.loop.load_plan_definition", lambda _p: (FakePlan(), "h"))
    monkeypatch.setattr("src.vectl.driver.loop.claim_step", lambda plan, *_a, **_k: (plan, None))
    monkeypatch.setattr("src.vectl.driver.loop.save_plan", lambda *_a, **_k: None)

    async def _create_worktree(*_a: object, **_k: object) -> Success[WorktreeBinding]:
        return Success(WorktreeBinding(**create_worktree_binding("core.impl")))

    monkeypatch.setattr("src.vectl.driver.loop.create_worktree", _create_worktree)
    monkeypatch.setattr("src.vectl.driver.loop.render_prompt", lambda **_k: "prompt")

    await handle_dispatch(
        action=Action(
            action="claim_and_dispatch",
            step_id="core.impl",
            agent="python-executor",
            session="reuse",
            task_id="resume-index-3",
        ),
        state=state,
        config=config,
        runners=cast(dict[str, Any], {"gemini": runner, "opencode": RecordingRunner("opencode")}),
        judge=cast(Any, object()),
        session_pool=session_pool,
        observer=observer,
        plan_path=Path("plan.yaml"),
    )

    assert [call["session_id"] for call in runner.calls] == [None]


def test_duplicate_replay_is_rejected_expected_red() -> None:
    """Duplicate attempt replay must be flagged by continuity policy.

    Source: docs/DRIVER-CONTINUITY-FOUNDATION.md Section 4 (replay is
    envelope-gated) and Section 5 (durable continuity facts, not ad hoc retries).
    """

    capability = next(c for c in RUNNER_CONTINUITY_CAPABILITIES if c.runner_name == "claude")
    envelope = ReplaySafetyEnvelope(
        step_id="core.impl",
        attempt_key="attempt-001",
        runner_name="claude",
        session_id="session-001",
        idempotency_scope="step",
        tool_call_fingerprint="fp-1",
    )

    decision = evaluate_resume_or_replay_safety(
        capability=capability,
        envelope=envelope,
        requested_token=None,
        seen_attempt_keys=frozenset({"attempt-001"}),
    )

    assert decision.duplicate_replay is True
    assert decision.replay_safe is False


def test_replay_token_semantics_drift_forces_fresh_expected_red() -> None:
    """Token semantics drift must force fresh continuity posture.

    Source: docs/DRIVER-CONTINUITY-FOUNDATION.md Section 6 (capability/token
    mismatch cannot authorize resume).
    """

    capability = next(c for c in RUNNER_CONTINUITY_CAPABILITIES if c.runner_name == "opencode")
    envelope = ReplaySafetyEnvelope(
        step_id="core.test",
        attempt_key="attempt-009",
        runner_name="opencode",
        session_id="ses-9",
        idempotency_scope="step",
        tool_call_fingerprint="fp-9",
    )
    drifted_token = ReplayTokenSemantics(
        token="tok-abc",
        token_kind="session",
        semantics_version="runner-v2",
        bound_runner_name="opencode",
        bound_step_id="core.test",
        capability_snapshot_id="runner-v1",
    )

    decision = evaluate_resume_or_replay_safety(
        capability=capability,
        envelope=envelope,
        requested_token=drifted_token,
        seen_attempt_keys=frozenset(),
    )

    assert decision.replay_safe is False
    assert "drift" in decision.recovery_reason.lower()


def test_guessed_runner_name_branching_is_rejected_expected_red() -> None:
    """Resume/replay must reject guessed runner-name branching.

    Source: docs/DRIVER-CONTINUITY-FOUNDATION.md Section 3 (authority matrix)
    and Section 6 (transport affordance does not authorize policy branch).
    """

    capability = next(c for c in RUNNER_CONTINUITY_CAPABILITIES if c.runner_name == "claude")
    envelope = ReplaySafetyEnvelope(
        step_id="core.verify",
        attempt_key="attempt-100",
        runner_name="claude",
        session_id="ses-100",
        idempotency_scope="step",
        tool_call_fingerprint="fp-100",
    )
    token = ReplayTokenSemantics(
        token="tok-guess",
        token_kind="opaque",
        semantics_version="bootstrap-v1",
        bound_runner_name="guessed-claude",
        bound_step_id="core.verify",
        capability_snapshot_id="bootstrap-v1",
    )

    decision = evaluate_resume_or_replay_safety(
        capability=capability,
        envelope=envelope,
        requested_token=token,
        seen_attempt_keys=frozenset(),
    )

    assert decision.replay_safe is False
    assert "runner" in decision.recovery_reason.lower()
