"""Focused tests for driver types.

Tests verify the data contracts defined in types.py:
- Enum and dataclass structure
- Field types and defaults
- Invariants documented in architecture

Architecture Reference: docs/DRIVER-ARCHITECTURE.md Section 2.1
Blueprint Reference: DRIVER-BLUEPRINT.md Module Map (types.py)
"""

import asyncio

import pytest

from src.vectl.driver.types import (
    CompletedEntry,
    DriverState,
    MergeResult,
    RunnerResult,
    RunnerStatus,
    RunningEntry,
    SessionEntry,
)


class TestRunnerStatus:
    """Contract tests for RunnerStatus enum."""

    def test_runner_status_values(self) -> None:
        """RunnerStatus MUST have exactly four values: SUCCESS, FAIL, STALL, TRANSPORT_ERROR.

        Contract pin from: docs/DRIVER-ARCHITECTURE.md Section 2.1
        """
        assert RunnerStatus.SUCCESS.value == "success"
        assert RunnerStatus.FAIL.value == "fail"
        assert RunnerStatus.STALL.value == "stall"
        assert RunnerStatus.TRANSPORT_ERROR.value == "transport_error"
        assert len(RunnerStatus) == 4

    def test_runner_status_is_str_enum(self) -> None:
        """RunnerStatus MUST inherit from str, Enum for JSON serialization.

        Contract pin from: docs/DRIVER-ARCHITECTURE.md Section 2.1
        """
        assert isinstance(RunnerStatus.SUCCESS, str)
        assert RunnerStatus.SUCCESS == "success"  # str comparison works


class TestRunnerResult:
    """Contract tests for RunnerResult dataclass."""

    def test_runner_result_fields(self) -> None:
        """RunnerResult MUST have status, session_id, output, elapsed_seconds, exit_code.

        Optional fields: cost_usd, tokens (default None).

        Contract pin from: docs/DRIVER-ARCHITECTURE.md Section 2.1
        Blueprint: DRIVER-BLUEPRINT.md Runner Protocol & Implementations
        """
        result = RunnerResult(
            status=RunnerStatus.SUCCESS,
            session_id="abc123",
            output="test output",
            elapsed_seconds=1.5,
            exit_code=0,
        )
        assert result.status == RunnerStatus.SUCCESS
        assert result.session_id == "abc123"
        assert result.output == "test output"
        assert result.elapsed_seconds == 1.5
        assert result.exit_code == 0
        assert result.cost_usd is None
        assert result.tokens is None

    def test_runner_result_status_type(self) -> None:
        """RunnerResult.status MUST be RunnerStatus enum.

        Contract pin from: docs/DRIVER-ARCHITECTURE.md Section 2.1
        """
        result = RunnerResult(
            status=RunnerStatus.FAIL,
            session_id=None,
            output="error",
            elapsed_seconds=0.5,
            exit_code=1,
        )
        assert isinstance(result.status, RunnerStatus)

    def test_runner_result_tokens_type(self) -> None:
        """RunnerResult.tokens MUST be dict[str, int] | None.

        Contract pin from: docs/DRIVER-ARCHITECTURE.md Section 2.1
        """
        result = RunnerResult(
            status=RunnerStatus.SUCCESS,
            session_id="xyz",
            output="",
            elapsed_seconds=1.0,
            exit_code=0,
            tokens={"input": 100, "output": 50},
        )
        assert result.tokens == {"input": 100, "output": 50}
        assert isinstance(result.tokens, dict)

    def test_runner_result_optional_fields_can_be_set(self) -> None:
        """RunnerResult optional fields (cost_usd, tokens) can be set."""
        result = RunnerResult(
            status=RunnerStatus.SUCCESS,
            session_id="session-1",
            output="done",
            elapsed_seconds=2.0,
            exit_code=0,
            cost_usd=0.05,
            tokens={"input": 200, "output": 100, "cache_read": 150},
        )
        assert result.cost_usd == 0.05
        assert result.tokens["cache_read"] == 150


class TestRunningEntry:
    """Contract tests for RunningEntry dataclass."""

    def test_running_entry_fields(self, mock_runner_handle) -> None:
        """RunningEntry MUST have step_id, agent, runner_name, handle, worktree_path, dispatched_at.

        Contract pin from: docs/DRIVER-ARCHITECTURE.md Section 2.1
        Blueprint: DRIVER-BLUEPRINT.md Flow 2 (state.register)
        """
        import time

        entry = RunningEntry(
            step_id="core.impl",
            agent="python-executor",
            runner_name="claude",
            handle=mock_runner_handle,
            worktree_path=".vectl/worktrees/core.impl",
            dispatched_at=time.monotonic(),
        )
        assert entry.step_id == "core.impl"
        assert entry.agent == "python-executor"
        assert entry.runner_name == "claude"
        assert entry.worktree_path == ".vectl/worktrees/core.impl"
        assert isinstance(entry.dispatched_at, float)

    def test_running_entry_dispatched_at_type(self, mock_runner_handle) -> None:
        """RunningEntry.dispatched_at MUST be float (time.monotonic()).

        Contract pin from: docs/DRIVER-ARCHITECTURE.md Section 2.1
        """
        import time

        entry = RunningEntry(
            step_id="test.step",
            agent="test-agent",
            runner_name="test-runner",
            handle=mock_runner_handle,
            worktree_path="/tmp/test",
            dispatched_at=time.monotonic(),
        )
        assert isinstance(entry.dispatched_at, float)


class TestCompletedEntry:
    """Contract tests for CompletedEntry dataclass."""

    def test_completed_entry_fields(self) -> None:
        """CompletedEntry MUST have step_id, agent, runner_name, result, worktree_path, elapsed_seconds.

        Contract pin from: docs/DRIVER-ARCHITECTURE.md Section 2.1
        Blueprint: DRIVER-BLUEPRINT.md Flow 3 (reconcile input)
        """
        result = RunnerResult(
            status=RunnerStatus.SUCCESS,
            session_id="session-2",
            output="completed",
            elapsed_seconds=3.0,
            exit_code=0,
        )
        entry = CompletedEntry(
            step_id="core.test",
            agent="python-tester",
            runner_name="opencode",
            result=result,
            worktree_path=".vectl/worktrees/core.test",
            elapsed_seconds=3.5,
        )
        assert entry.step_id == "core.test"
        assert entry.agent == "python-tester"
        assert entry.runner_name == "opencode"
        assert entry.result is result
        assert entry.worktree_path == ".vectl/worktrees/core.test"
        assert entry.elapsed_seconds == 3.5

    def test_completed_entry_result_type(self) -> None:
        """CompletedEntry.result MUST be RunnerResult.

        Contract pin from: docs/DRIVER-ARCHITECTURE.md Section 2.1
        """
        result = RunnerResult(
            status=RunnerStatus.FAIL,
            session_id=None,
            output="",
            elapsed_seconds=0.1,
            exit_code=1,
        )
        entry = CompletedEntry(
            step_id="step",
            agent="agent",
            runner_name="runner",
            result=result,
            worktree_path="/path",
            elapsed_seconds=0.2,
        )
        assert isinstance(entry.result, RunnerResult)


class TestDriverState:
    """Contract tests for DriverState dataclass."""

    def test_driver_state_running_type(self) -> None:
        """DriverState.running MUST be dict[str, RunningEntry].

        Contract pin from: docs/DRIVER-ARCHITECTURE.md Section 2.1
        Invariant: RunningEntry instances exist in running if and only if runner is alive.
        """
        state = DriverState()
        assert isinstance(state.running, dict)
        assert len(state.running) == 0  # Default is empty dict

    def test_driver_state_completed_queue_type(self) -> None:
        """DriverState.completed_queue MUST be list[CompletedEntry].

        Contract pin from: docs/DRIVER-ARCHITECTURE.md Section 2.1
        Invariant: Drained each loop iteration.
        """
        state = DriverState()
        assert isinstance(state.completed_queue, list)
        assert len(state.completed_queue) == 0  # Default is empty list

    def test_driver_state_failure_counts_type(self) -> None:
        """DriverState.failure_counts MUST be dict[str, int].

        Contract pin from: docs/DRIVER-ARCHITECTURE.md Section 2.1
        Maps: step_id -> consecutive failure count.
        """
        state = DriverState()
        assert isinstance(state.failure_counts, dict)
        state.failure_counts["step-1"] = 2
        assert state.failure_counts["step-1"] == 2

    def test_driver_state_failure_history_type(self) -> None:
        """DriverState.failure_history MUST be dict[str, list[str]].

        Contract pin from: docs/DRIVER-ARCHITECTURE.md Section 2.1
        Maps: step_id -> [error_output_1, error_output_2, ...].
        """
        state = DriverState()
        assert isinstance(state.failure_history, dict)
        state.failure_history["step-1"] = ["error 1", "error 2"]
        assert len(state.failure_history["step-1"]) == 2

    def test_driver_state_runner_failures_type(self) -> None:
        """DriverState.runner_failures MUST be dict[tuple[str, str], int].

        Contract pin from: docs/DRIVER-ARCHITECTURE.md Section 2.1
        Maps: (step_id, runner_name) -> consecutive failure count for runner fallback.
        """
        state = DriverState()
        assert isinstance(state.runner_failures, dict)
        state.runner_failures[("step-1", "claude")] = 2
        assert state.runner_failures[("step-1", "claude")] == 2

    def test_driver_state_agent_overrides_type(self) -> None:
        """DriverState.agent_overrides MUST be dict[str, str].

        Contract pin from: docs/DRIVER-ARCHITECTURE.md Section 2.1
        Maps: step_id -> overridden agent name (from judge SWITCH_AGENT verdict).
        """
        state = DriverState()
        assert isinstance(state.agent_overrides, dict)
        state.agent_overrides["step-1"] = "python-senior"
        assert state.agent_overrides["step-1"] == "python-senior"

    def test_driver_state_merge_lock_type(self) -> None:
        """DriverState.merge_lock MUST be asyncio.Lock.

        Contract pin from: docs/DRIVER-ARCHITECTURE.md Section 2.1
        Invariant: Sole serialization mechanism for git merge operations.
        Architecture Note 7: MUST be asyncio.Lock, NOT threading.Lock.
        """
        state = DriverState()
        assert isinstance(state.merge_lock, asyncio.Lock)

    def test_driver_state_default_values(self) -> None:
        """DriverState defaults: running={}, completed_queue=[], halt_requested=False."""
        state = DriverState()
        assert state.running == {}
        assert state.completed_queue == []
        assert state.failure_counts == {}
        assert state.failure_history == {}
        assert state.runner_failures == {}
        assert state.agent_overrides == {}
        assert state.halt_requested is False
        assert state.loop_detector == []

    def test_driver_state_loop_detector_type(self) -> None:
        """DriverState.loop_detector MUST be list[str].

        Contract pin from: docs/DRIVER-ARCHITECTURE.md Section 2.1
        Purpose: Last N serialized decide() action signatures for loop detection.
        """
        state = DriverState()
        assert isinstance(state.loop_detector, list)
        state.loop_detector.append("action-sig-1")
        assert state.loop_detector == ["action-sig-1"]


class TestMergeResult:
    """Contract tests for MergeResult dataclass."""

    def test_merge_result_status_values(self) -> None:
        """MergeResult.status MUST be "clean" | "auto_resolved" | "conflict".

        Contract pin from: docs/DRIVER-ARCHITECTURE.md Section 2.1
        Blueprint: DRIVER-BLUEPRINT.md Worktree Lifecycle (worktree.py)
        """
        # Test all three valid status values
        for status in ["clean", "auto_resolved", "conflict"]:
            result = MergeResult(status=status)
            assert result.status == status

    def test_merge_result_trivial_default(self) -> None:
        """MergeResult.trivial MUST default to True.

        Contract pin from: docs/DRIVER-ARCHITECTURE.md Section 2.1
        """
        result = MergeResult(status="clean")
        assert result.trivial is True

    def test_merge_result_files_default(self) -> None:
        """MergeResult.files MUST default to empty list."""
        result = MergeResult(status="clean")
        assert result.files == []

    def test_merge_result_can_set_files(self) -> None:
        """MergeResult.files can be set explicitly."""
        result = MergeResult(status="conflict", files=["src/a.py", "src/b.py"], trivial=False)
        assert result.files == ["src/a.py", "src/b.py"]
        assert result.trivial is False


class TestSessionEntry:
    """Contract tests for SessionEntry dataclass."""

    def test_session_entry_fields(self) -> None:
        """SessionEntry MUST have session_id, runner_name, agent, step_id, completed_at.

        Contract pin from: docs/DRIVER-ARCHITECTURE.md Section 2.1
        Blueprint: DRIVER-BLUEPRINT.md Session Pool (session.py)
        """
        import time

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
        import time

        entry = SessionEntry(
            session_id="session-1",
            runner_name="opencode",
            agent="test-agent",
            step_id="test.step",
            completed_at=time.monotonic(),
        )
        assert isinstance(entry.completed_at, float)


class TestRunnerStatusRoundTrip:
    """Serialization tests for RunnerStatus."""

    def test_runner_status_json_serializable(self) -> None:
        """RunnerStatus values MUST be JSON-serializable via value."""
        import json

        status_dict = {"status": RunnerStatus.SUCCESS.value}
        json_str = json.dumps(status_dict)
        parsed = json.loads(json_str)
        assert parsed["status"] == "success"

    def test_runner_status_all_values_serializable(self) -> None:
        """All RunnerStatus values MUST be JSON-serializable."""
        import json

        for status in RunnerStatus:
            data = {"status": status.value}
            json_str = json.dumps(data)
            parsed = json.loads(json_str)
            assert parsed["status"] == status.value


class TestRunnerResultRoundTrip:
    """Dataclass serialization tests for RunnerResult."""

    def test_runner_result_to_dict_roundtrip(self) -> None:
        """RunnerResult SHOULD serialize to dict and round-trip via dataclasses.asdict."""
        from dataclasses import asdict

        result = RunnerResult(
            status=RunnerStatus.SUCCESS,
            session_id="session-123",
            output="test output",
            elapsed_seconds=1.5,
            exit_code=0,
            cost_usd=0.05,
            tokens={"input": 100, "output": 50},
        )
        d = asdict(result)
        assert d["status"] == RunnerStatus.SUCCESS
        assert d["session_id"] == "session-123"
        assert d["output"] == "test output"
        assert d["elapsed_seconds"] == 1.5
        assert d["exit_code"] == 0
        assert d["cost_usd"] == 0.05
        assert d["tokens"] == {"input": 100, "output": 50}


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def mock_runner_handle():
    """Mock RunnerHandle for testing RunningEntry."""

    class MockRunnerHandle:
        """Minimal mock implementing RunnerHandle protocol."""

        @property
        def session_id(self) -> str | None:
            return "mock-session"

        @property
        def pid(self) -> int | None:
            return 12345

    return MockRunnerHandle()
