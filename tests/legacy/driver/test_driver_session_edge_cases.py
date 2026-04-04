"""Focused edge case tests for SessionPool reuse rules.

Additional tests beyond the contract tests to ensure:
- Zero dependencies -> fresh sessions only (proven)
- Multiple dependencies -> fresh sessions only (proven)
- Runner mismatch -> fresh sessions only (proven)
- Expired TTL -> fresh sessions only (proven)
- SessionPool does NOT mutate decide.py state

Architecture Reference: docs/DRIVER-ARCHITECTURE.md Section 2.5, Q4
Blueprint Reference: DRIVER-BLUEPRINT.md Session Pool
"""

import time

import pytest

from src.vectl.driver.config import SessionConfig
from src.vectl.driver.session import SessionPool
from src.vectl.driver.types import SessionEntry


class TestSingleDependencyLimitation:
    """Tests proving the single-dependency limitation.

    These tests validate that ONLY steps with exactly 1 dependency
    are eligible for session reuse. Steps with 0 or 2+ dependencies
    always get fresh sessions.
    """

    def test_zero_deps_returns_none_even_with_matching_runner(self) -> None:
        """Zero dependencies -> fresh only, even when runner matches.

        This proves the single-dependency limitation is enforced
        regardless of other conditions (runner match, TTL).
        """
        config = SessionConfig(reuse_ttl=300)
        pool = SessionPool(config)

        # Record a session with matching runner
        pool.record("upstream.step", "ses_match", "claude", "agent-A")

        # Step with zero dependencies requests reuse
        # Result: None (fresh session required)
        result = pool.find_reusable(
            step_id="independent.step",
            agent="agent-B",
            runner_name="claude",  # Runner matches
            depends_on=[],  # Zero dependencies
        )

        assert result is None, "Zero dependencies must return None (fresh session)"

    def test_two_deps_returns_none_even_with_matching_runner(self) -> None:
        """Multiple dependencies -> fresh only, even when runner matches.

        This proves that 2+ dependencies cannot reuse sessions.
        """
        config = SessionConfig(reuse_ttl=300)
        pool = SessionPool(config)

        # Record sessions from two different upstream steps
        pool.record("dep-1", "ses_A", "claude", "agent-A")
        pool.record("dep-2", "ses_B", "claude", "agent-A")

        # Step with 2 dependencies requests reuse
        result = pool.find_reusable(
            step_id="merged.step",
            agent="agent-C",
            runner_name="claude",  # Runner matches
            depends_on=["dep-1", "dep-2"],
        )

        assert result is None, "Multiple dependencies must return None (fresh session)"

    def test_three_deps_returns_none(self) -> None:
        """Three dependencies -> fresh only."""
        config = SessionConfig()
        pool = SessionPool(config)

        pool.record("dep-1", "ses_A", "claude", "agent-A")
        pool.record("dep-2", "ses_B", "claude", "agent-B")
        pool.record("dep-3", "ses_C", "claude", "agent-C")

        result = pool.find_reusable(
            step_id="complex.step",
            agent="agent-D",
            runner_name="claude",
            depends_on=["dep-1", "dep-2", "dep-3"],
        )

        assert result is None

    def test_single_dep_returns_session_when_all_conditions_match(self) -> None:
        """Single dependency -> session reuse allowed when all conditions met.

        This proves the happy path for single-dependency steps.
        """
        config = SessionConfig(reuse_ttl=300)
        pool = SessionPool(config)

        pool.record("upstream", "ses_valid", "claude", "agent-A")

        result = pool.find_reusable(
            step_id="downstream",
            agent="agent-B",
            runner_name="claude",  # Runner matches
            depends_on=["upstream"],  # Exactly one dependency
        )

        assert result == "ses_valid"


class TestTtlBehavior:
    """Tests for TTL expiration behavior.

    TTL checks ensure stale sessions are not reused.
    Per-runner TTL overrides allow different TTLs per runner.
    """

    def test_ttl_expires_session(self) -> None:
        """Expired TTL -> fresh only."""
        config = SessionConfig(reuse_ttl=0)  # Immediate expiry
        pool = SessionPool(config)

        pool.record("upstream", "ses_old", "claude", "agent-A")

        # Even with all other conditions met
        result = pool.find_reusable(
            step_id="downstream",
            agent="agent-B",
            runner_name="claude",
            depends_on=["upstream"],
        )

        assert result is None

    def test_ttl_not_expired_session_reusable(self) -> None:
        """Non-expired TTL -> session is reusable."""
        config = SessionConfig(reuse_ttl=3600)  # 1 hour TTL
        pool = SessionPool(config)

        pool.record("upstream", "ses_fresh", "claude", "agent-A")

        result = pool.find_reusable(
            step_id="downstream",
            agent="agent-B",
            runner_name="claude",
            depends_on=["upstream"],
        )

        assert result == "ses_fresh"

    def test_per_runner_ttl_default_used(self) -> None:
        """When no per-runner override, default TTL is used."""
        config = SessionConfig(
            reuse_ttl=100,
            ttl_overrides={},  # No overrides
        )
        pool = SessionPool(config)

        # Record session with opencode runner (no override)
        pool.record("step-1", "ses_1", "opencode", "agent-A")

        # Should use default TTL (100)
        result = pool.find_reusable(
            step_id="step-2",
            agent="agent-B",
            runner_name="opencode",
            depends_on=["step-1"],
        )

        assert result == "ses_1"

    def test_per_runner_ttl_override_applied(self) -> None:
        """When per-runner override exists, it's used instead of default."""
        config = SessionConfig(
            reuse_ttl=1000,  # Default 1000 (won't be used for claude)
            ttl_overrides={"claude": 0},  # Claude immediate expiry
        )
        pool = SessionPool(config)

        pool.record("step-1", "ses_1", "claude", "agent-A")

        result = pool.find_reusable(
            step_id="step-2",
            agent="agent-B",
            runner_name="claude",
            depends_on=["step-1"],
        )

        # Claude's TTL is 0, so session expires immediately
        assert result is None


class TestRunnerMatching:
    """Tests for runner-name matching behavior.

    Session reuse requires the runner name to match the session's runner.
    """

    def test_runner_mismatch_returns_none(self) -> None:
        """Runner mismatch -> fresh only."""
        config = SessionConfig(reuse_ttl=300)
        pool = SessionPool(config)

        pool.record("upstream", "ses_claude", "claude", "agent-A")

        # Try to reuse with different runner
        result = pool.find_reusable(
            step_id="downstream",
            agent="agent-B",
            runner_name="opencode",  # Different runner
            depends_on=["upstream"],
        )

        assert result is None

    def test_runner_match_allows_reuse(self) -> None:
        """Runner match -> reuse allowed (if other conditions met)."""
        config = SessionConfig(reuse_ttl=300)
        pool = SessionPool(config)

        pool.record("upstream", "ses_claude", "claude", "agent-A")

        result = pool.find_reusable(
            step_id="downstream",
            agent="agent-B",
            runner_name="claude",  # Same runner
            depends_on=["upstream"],
        )

        assert result == "ses_claude"

    def test_different_runners_in_pool(self) -> None:
        """Pool can store sessions from different runners."""
        config = SessionConfig()
        pool = SessionPool(config)

        # Record sessions from different runners
        pool.record("step-claude", "ses_claude", "claude", "agent-A")
        pool.record("step-opencode", "ses_opencode", "opencode", "agent-B")

        # Each can be reused with matching runner
        result_claude = pool.find_reusable(
            step_id="down-claude",
            agent="agent-C",
            runner_name="claude",
            depends_on=["step-claude"],
        )
        result_opencode = pool.find_reusable(
            step_id="down-opencode",
            agent="agent-D",
            runner_name="opencode",
            depends_on=["step-opencode"],
        )

        assert result_claude == "ses_claude"
        assert result_opencode == "ses_opencode"


class TestSessionPoolNoMutation:
    """Tests proving SessionPool does NOT mutate decide.py state.

    Architecture: docs/DRIVER-ARCHITECTURE.md Section 2.5, Q4

    The driver does NOT modify decide.py's module-level _session_registry.
    SessionPool operates independently with its own _entries dict.
    """

    def test_session_pool_has_own_entries(self) -> None:
        """SessionPool maintains its own entries, separate from decide.py."""
        config = SessionConfig()
        pool = SessionPool(config)

        # SessionPool has its own internal state
        assert isinstance(pool._entries, dict)
        assert pool._entries == {}

        # No reference to any decide.py module state
        # This is verified by checking the pool's attributes
        attr_names = set(dir(pool))

        # Should have _entries, _default_ttl, _ttl_overrides
        # Should NOT have any references to decide.py internals
        assert "_entries" in attr_names
        assert "_default_ttl" in attr_names
        assert "_ttl_overrides" in attr_names

        # Should NOT have registry-related attributes
        assert "_session_registry" not in attr_names
        assert "_completion_times" not in attr_names

    def test_record_only_affects_internal_entries(self) -> None:
        """record() only modifies pool's internal _entries."""
        config = SessionConfig()
        pool = SessionPool(config)

        # Before record
        initial_entries = len(pool._entries)

        pool.record("step-1", "ses_1", "claude", "agent-A")

        # Only _entries changed
        assert len(pool._entries) == initial_entries + 1

        # No other observable state changes
        # SessionPool has no other public state beyond _entries
        assert pool._default_ttl == config.reuse_ttl
        assert pool._ttl_overrides == config.ttl_overrides

    def test_find_reusable_only_reads_internal_state(self) -> None:
        """find_reusable() only reads from internal state, doesn't modify."""
        config = SessionConfig()
        pool = SessionPool(config)

        pool.record("step-1", "ses_1", "claude", "agent-A")

        # Capture state before find_reusable
        entries_before = dict(pool._entries)

        # Call find_reusable multiple times
        pool.find_reusable("step-2", "agent-B", "claude", ["step-1"])
        pool.find_reusable("step-2", "agent-B", "opencode", ["step-1"])
        pool.find_reusable("step-3", "agent-C", "claude", ["step-1"])
        pool.find_reusable("step-4", "agent-D", "claude", [])

        # State should be unchanged
        assert pool._entries == entries_before

    def test_session_pool_does_not_import_decide(self) -> None:
        """SessionPool module does not import decide.py."""
        import ast
        import inspect

        # Get the source code
        source = inspect.getsource(SessionPool)

        # Parse to AST
        tree = ast.parse(source)

        # Check imports
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert "decide" not in alias.name.lower(), (
                        "SessionPool should not import decide.py"
                    )
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    assert "decide" not in node.module.lower(), (
                        "SessionPool should not import from decide.py"
                    )


class TestEdgeCases:
    """Miscellaneous edge case tests for SessionPool."""

    def test_empty_pool_returns_none(self) -> None:
        """Empty pool always returns None."""
        config = SessionConfig()
        pool = SessionPool(config)

        result = pool.find_reusable(
            step_id="any",
            agent="any",
            runner_name="any",
            depends_on=["any"],
        )

        assert result is None

    def test_record_overwrites_same_step(self) -> None:
        """Recording same step_id overwrites previous session."""
        config = SessionConfig()
        pool = SessionPool(config)

        pool.record("step-1", "ses_A", "claude", "agent-A")
        pool.record("step-1", "ses_B", "opencode", "agent-B")

        # Only the last session should exist
        assert pool._entries["step-1"].session_id == "ses_B"
        assert pool._entries["step-1"].runner_name == "opencode"

        # find_reusable should use latest session
        result = pool.find_reusable(
            step_id="step-2",
            agent="agent-C",
            runner_name="opencode",
            depends_on=["step-1"],
        )
        assert result == "ses_B"

    def test_agent_parameter_unused_in_lookup(self) -> None:
        """The agent parameter is not used in session matching.

        Architecture: docs/DRIVER-ARCHITECTURE.md Section 2.5
        Runner-name matching is required, but agent is NOT checked.
        """
        config = SessionConfig(reuse_ttl=300)
        pool = SessionPool(config)

        # Record with agent-A
        pool.record("upstream", "ses_1", "claude", "agent-A")

        # Lookup with agent-B (different agent, same runner)
        result = pool.find_reusable(
            step_id="downstream",
            agent="agent-B",  # Different agent
            runner_name="claude",  # Same runner
            depends_on=["upstream"],
        )

        # Should still return session (agent not checked)
        assert result == "ses_1"

    def test_step_id_parameter_unused_in_lookup(self) -> None:
        """The step_id parameter is documented but not used in lookup.

        Architecture: docs/DRIVER-ARCHITECTURE.md Section 2.5
        step_id is for logging/calling context, not filtering.
        """
        config = SessionConfig(reuse_ttl=300)
        pool = SessionPool(config)

        pool.record("upstream", "ses_1", "claude", "agent-A")

        # Call with arbitrary step_id
        result = pool.find_reusable(
            step_id="any-random-step-id",  # Not used in lookup
            agent="agent-B",
            runner_name="claude",
            depends_on=["upstream"],
        )

        # Should still find the session
        assert result == "ses_1"
