"""Test contracts for driver session.

These test stubs verify the SessionPool contract defined in session.py.
Each test is a contract placeholder that will be filled during implementation.

Architecture Reference: docs/DRIVER-ARCHITECTURE.md Section 2.5
Architecture Reference: docs/DRIVER-ARCHITECTURE.md Q4 (Ownership Boundary)
Blueprint Reference: DRIVER-BLUEPRINT.md Session Pool (session.py)
"""

import time

import pytest

from src.vectl.driver.config import SessionConfig
from src.vectl.driver.session import SessionPool
from src.vectl.driver.types import SessionEntry


class TestSessionEntry:
    """Contract tests for SessionEntry dataclass."""

    def test_session_entry_required_fields(self) -> None:
        """SessionEntry MUST have session_id, runner_name, agent, step_id, completed_at.

        Contract pin from: docs/DRIVER-ARCHITECTURE.md Section 2.1
        Blueprint: DRIVER-BLUEPRINT.md Session Pool (session.py)
        """
        entry = SessionEntry(
            session_id="ses_abc123",
            runner_name="claude",
            agent="python-executor",
            step_id="core.impl",
            completed_at=time.monotonic(),
        )
        assert entry.session_id == "ses_abc123"
        assert entry.runner_name == "claude"
        assert entry.agent == "python-executor"
        assert entry.step_id == "core.impl"
        assert isinstance(entry.completed_at, float)

    def test_session_entry_completed_at_type(self) -> None:
        """SessionEntry.completed_at MUST be float (time.monotonic()).

        Contract pin from: docs/DRIVER-ARCHITECTURE.md Section 2.1
        """
        entry = SessionEntry(
            session_id="session-1",
            runner_name="opencode",
            agent="test-agent",
            step_id="test.step",
            completed_at=time.monotonic(),
        )
        assert isinstance(entry.completed_at, float)


class TestSessionPoolInit:
    """Contract tests for SessionPool initialization."""

    def test_session_pool_init_with_config(self) -> None:
        """SessionPool MUST initialize with SessionConfig.

        Contract pin from: docs/DRIVER-ARCHITECTURE.md Section 2.5
        Blueprint: DRIVER-BLUEPRINT.md Session Pool
        """
        config = SessionConfig(reuse_ttl=300, ttl_overrides={"claude": 600})
        pool = SessionPool(config)
        assert pool._default_ttl == 300
        assert pool._ttl_overrides == {"claude": 600}

    def test_session_pool_entries_empty(self) -> None:
        """SessionPool MUST start with empty entries."""
        config = SessionConfig()
        pool = SessionPool(config)
        assert pool._entries == {}


class TestSessionPoolRecord:
    """Contract tests for SessionPool.record."""

    def test_record_stores_entry(self) -> None:
        """record MUST store SessionEntry indexed by step_id.

        Contract pin from: docs/DRIVER-ARCHITECTURE.md Section 2.5
        Blueprint: DRIVER-BLUEPRINT.md Session Pool
        """
        config = SessionConfig()
        pool = SessionPool(config)

        pool.record(
            step_id="core.impl",
            session_id="ses_123",
            runner_name="claude",
            agent="python-executor",
        )

        assert "core.impl" in pool._entries
        entry = pool._entries["core.impl"]
        assert entry.session_id == "ses_123"
        assert entry.runner_name == "claude"
        assert entry.agent == "python-executor"
        assert entry.step_id == "core.impl"

    def test_record_overwrites_previous(self) -> None:
        """record MUST overwrite previous entry for same step_id."""
        config = SessionConfig()
        pool = SessionPool(config)

        pool.record("step-1", "session-A", "runner-A", "agent-A")
        pool.record("step-1", "session-B", "runner-B", "agent-B")

        entry = pool._entries["step-1"]
        assert entry.session_id == "session-B"
        assert entry.runner_name == "runner-B"


class TestSessionPoolFindReusable:
    """Contract tests for SessionPool.find_reusable."""

    def test_find_reusable_single_dependency(self) -> None:
        """find_reusable MUST return session_id for single dependency with matching runner.

        Contract pin from: docs/DRIVER-ARCHITECTURE.md Section 2.5
        Blueprint: DRIVER-BLUEPRINT.md Session Pool
        """
        config = SessionConfig(reuse_ttl=300)
        pool = SessionPool(config)

        # Record a completed session
        pool.record("core.impl", "ses_123", "claude", "python-executor")

        # Find reusable for downstream step
        session_id = pool.find_reusable(
            step_id="core.test",
            agent="python-tester",
            runner_name="claude",
            depends_on=["core.impl"],
        )

        assert session_id == "ses_123"

    def test_find_reusable_zero_dependencies(self) -> None:
        """find_reusable MUST return None for steps with 0 dependencies.

        Single-dependency limitation: steps with 0 dependencies always get fresh sessions.

        Contract pin from: docs/DRIVER-ARCHITECTURE.md Section 2.5
        Blueprint: DRIVER-BLUEPRINT.md Session Pool
        """
        config = SessionConfig()
        pool = SessionPool(config)

        pool.record("core.impl", "ses_123", "claude", "python-executor")

        session_id = pool.find_reusable(
            step_id="core.test",
            agent="python-tester",
            runner_name="claude",
            depends_on=[],
        )

        assert session_id is None

    def test_find_reusable_multiple_dependencies(self) -> None:
        """find_reusable MUST return None for steps with 2+ dependencies.

        Single-dependency limitation: steps with 2+ dependencies always get fresh sessions.

        Contract pin from: docs/DRIVER-ARCHITECTURE.md Section 2.5
        Blueprint: DRIVER-BLUEPRINT.md Session Pool
        """
        config = SessionConfig()
        pool = SessionPool(config)

        pool.record("step-1", "ses_A", "claude", "agent-A")
        pool.record("step-2", "ses_B", "claude", "agent-B")

        session_id = pool.find_reusable(
            step_id="step-3",
            agent="agent-C",
            runner_name="claude",
            depends_on=["step-1", "step-2"],
        )

        assert session_id is None

    def test_find_reusable_no_recorded_session(self) -> None:
        """find_reusable MUST return None when dependency has no recorded session."""
        config = SessionConfig()
        pool = SessionPool(config)

        session_id = pool.find_reusable(
            step_id="core.test",
            agent="python-tester",
            runner_name="claude",
            depends_on=["core.impl"],
        )

        assert session_id is None

    def test_find_reusable_runner_mismatch(self) -> None:
        """find_reusable MUST return None when runner_name doesn't match.

        Contract pin from: docs/DRIVER-ARCHITECTURE.md Section 2.5
        Runner-aware matching: session runner must match dispatch runner.
        """
        config = SessionConfig()
        pool = SessionPool(config)

        pool.record("core.impl", "ses_123", "claude", "python-executor")

        session_id = pool.find_reusable(
            step_id="core.test",
            agent="python-tester",
            runner_name="opencode",  # Different runner!
            depends_on=["core.impl"],
        )

        assert session_id is None

    def test_find_reusable_ttl_expired(self) -> None:
        """find_reusable MUST return None when TTL has expired.

        Contract pin from: docs/DRIVER-ARCHITECTURE.md Section 2.5
        Blueprint: DRIVER-BLUEPRINT.md Session Pool
        """
        config = SessionConfig(reuse_ttl=0)  # 0 second TTL = expired immediately
        pool = SessionPool(config)

        pool.record("core.impl", "ses_123", "claude", "python-executor")

        # TTL is 0, so session is immediately expired
        session_id = pool.find_reusable(
            step_id="core.test",
            agent="python-tester",
            runner_name="claude",
            depends_on=["core.impl"],
        )

        assert session_id is None

    def test_find_reusable_per_runner_ttl_override(self) -> None:
        """find_reusable MUST use per-runner TTL override when available.

        Contract pin from: docs/DRIVER-ARCHITECTURE.md Section 2.5
        Blueprint: DRIVER-BLUEPRINT.md Session Pool
        """
        config = SessionConfig(
            reuse_ttl=300,
            ttl_overrides={"claude": 0},  # 0 seconds for claude = immediate expiry
        )
        pool = SessionPool(config)

        pool.record("core.impl", "ses_123", "claude", "python-executor")

        session_id = pool.find_reusable(
            step_id="core.test",
            agent="python-tester",
            runner_name="claude",
            depends_on=["core.impl"],
        )

        assert session_id is None


class TestSessionPoolOwnershipBoundary:
    """Contract tests for Ownership Boundary: SessionPool vs _session_registry.

    Architecture: docs/DRIVER-ARCHITECTURE.md Section 2.5, Q4
    """

    def test_session_pool_is_independent(self) -> None:
        """SessionPool MUST NOT interact with decide.py's _session_registry."""
        # SessionPool has its own internal _entries dict
        config = SessionConfig()
        pool = SessionPool(config)

        # It does not reference or modify any external module-level state
        # This is verified by the structure: only _entries, _default_ttl, _ttl_overrides
        assert hasattr(pool, "_entries")
        assert hasattr(pool, "_default_ttl")
        assert hasattr(pool, "_ttl_overrides")

    def test_session_pool_runner_aware(self) -> None:
        """SessionPool MUST be runner-aware (unlike _session_registry).

        Architecture: docs/DRIVER-ARCHITECTURE.md Section 2.5, Q4

        _session_registry (decide.py) is NOT runner-aware.
        SessionPool IS runner-aware (requires runner match for reuse).
        """
        config = SessionConfig()
        pool = SessionPool(config)

        pool.record("core.impl", "ses_123", "claude", "python-executor")

        # Same dependency, different runner -> no reuse
        session_id = pool.find_reusable(
            step_id="core.test",
            agent="python-tester",
            runner_name="opencode",  # Different runner
            depends_on=["core.impl"],
        )
        assert session_id is None

        # Same runner -> reuse allowed
        session_id = pool.find_reusable(
            step_id="core.test",
            agent="python-tester",
            runner_name="claude",  # Same runner
            depends_on=["core.impl"],
        )
        assert session_id == "ses_123"

    def test_session_pool_per_runner_ttl(self) -> None:
        """SessionPool MUST support per-runner TTL overrides.

        Architecture: docs/DRIVER-ARCHITECTURE.md Section 2.5
        _session_registry does NOT have per-runner TTL.
        SessionPool HAS per-runner TTL via ttl_overrides.
        """
        config = SessionConfig(
            reuse_ttl=100,  # Default 100 units
            ttl_overrides={"claude": 200},  # Claude gets 200
        )
        pool = SessionPool(config)

        # Record session for claude
        pool.record("step-1", "ses_A", "claude", "agent-A")

        # Record session for opencode
        pool.record("step-2", "ses_B", "opencode", "agent-B")

        # Both entries are stored
        assert "step-1" in pool._entries
        assert "step-2" in pool._entries


class TestSessionPoolReconciliation:
    """Contract tests for SessionPool reconciliation with decide().

    Architecture: docs/DRIVER-ARCHITECTURE.md Section 2.5
    Blueprint: DRIVER-BLUEPRINT.md Flow 3
    """

    def test_session_pool_supplements_decide(self) -> None:
        """SessionPool provides supplementary runner-aware reuse.

        When decide() produces session="fresh", SessionPool MAY provide a
        session_id for runner-aware matching. This is safe: if the session
        is stale, the runner starts fresh (graceful degradation).

        Architecture: docs/DRIVER-ARCHITECTURE.md Section 2.5, Q4
        """
        config = SessionConfig(reuse_ttl=300)
        pool = SessionPool(config)

        # Step 1 completes with session
        pool.record("core.impl", "ses_123", "claude", "python-executor")

        # decide() may return session="fresh" for step-2
        # SessionPool can still provide a session for runner-aware reuse
        session_id = pool.find_reusable(
            step_id="core.test",
            agent="python-tester",
            runner_name="claude",
            depends_on=["core.impl"],
        )

        # Driver may use this session_id even if decide() returned "fresh"
        assert session_id == "ses_123"

    def test_session_pool_respects_decide_reuse(self) -> None:
        """When decide() returns session="reuse" with task_id, driver uses that.

        Architecture: docs/DRIVER-ARCHITECTURE.md Section 2.5, Q4

        This test documents the contract - the actual integration is in loop.py.
        SessionPool.find_reusable() returns a candidate session_id, but the
        driver MAY choose to use decide()'s task_id instead.
        """
        # SessionPool provides candidate
        config = SessionConfig()
        pool = SessionPool(config)
        pool.record("step-1", "ses_A", "claude", "agent-A")

        candidate = pool.find_reusable(
            step_id="step-2",
            agent="agent-B",
            runner_name="claude",
            depends_on=["step-1"],
        )

        # Driver has choice: use candidate OR decide()'s task_id
        # This test just verifies SessionPool returns the candidate
        assert candidate == "ses_A"
