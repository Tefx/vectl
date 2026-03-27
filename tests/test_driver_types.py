"""Test contracts for driver types.

These test stubs verify the data contracts defined in types.py.
Each test is a contract placeholder that will be filled during implementation.

Architecture Reference: docs/DRIVER-ARCHITECTURE.md Section 2.1
Blueprint Reference: DRIVER-BLUEPRINT.md Module Map (types.py)
"""

import pytest


class TestRunnerStatus:
    """Contract tests for RunnerStatus enum."""

    def test_runner_status_values(self) -> None:
        """RunnerStatus MUST have exactly four values: SUCCESS, FAIL, STALL, TRANSPORT_ERROR.

        Contract pin from: docs/DRIVER-ARCHITECTURE.md Section 2.1
        """
        # This will be implemented when types.py implementation is complete
        # Expected: RunnerStatus.SUCCESS, RunnerStatus.FAIL, RunnerStatus.STALL, RunnerStatus.TRANSPORT_ERROR
        raise NotImplementedError("Contract: RunnerStatus enum values")

    def test_runner_status_is_str_enum(self) -> None:
        """RunnerStatus MUST inherit from str, Enum for JSON serialization.

        Contract pin from: docs/DRIVER-ARCHITECTURE.md Section 2.1
        """
        raise NotImplementedError("Contract: RunnerStatus inherits str, Enum")


class TestRunnerResult:
    """Contract tests for RunnerResult dataclass."""

    def test_runner_result_fields(self) -> None:
        """RunnerResult MUST have status, session_id, output, elapsed_seconds, exit_code.

        Optional fields: cost_usd, tokens (default None).

        Contract pin from: docs/DRIVER-ARCHITECTURE.md Section 2.1
        Blueprint: DRIVER-BLUEPRINT.md Runner Protocol & Implementations
        """
        raise NotImplementedError("Contract: RunnerResult required and optional fields")

    def test_runner_result_status_type(self) -> None:
        """RunnerResult.status MUST be RunnerStatus enum.

        Contract pin from: docs/DRIVER-ARCHITECTURE.md Section 2.1
        """
        raise NotImplementedError("Contract: RunnerResult.status is RunnerStatus")

    def test_runner_result_tokens_type(self) -> None:
        """RunnerResult.tokens MUST be dict[str, int] | None.

        Contract pin from: docs/DRIVER-ARCHITECTURE.md Section 2.1
        """
        raise NotImplementedError("Contract: RunnerResult.tokens is dict[str, int] | None")


class TestRunningEntry:
    """Contract tests for RunningEntry dataclass."""

    def test_running_entry_fields(self) -> None:
        """RunningEntry MUST have step_id, agent, runner_name, handle, worktree_path, dispatched_at.

        Contract pin from: docs/DRIVER-ARCHITECTURE.md Section 2.1
        Blueprint: DRIVER-BLUEPRINT.md Flow 2 (state.register)
        """
        raise NotImplementedError("Contract: RunningEntry required fields")

    def test_running_entry_dispatched_at_type(self) -> None:
        """RunningEntry.dispatched_at MUST be float (time.monotonic()).

        Contract pin from: docs/DRIVER-ARCHITECTURE.md Section 2.1
        """
        raise NotImplementedError("Contract: RunningEntry.dispatched_at is float")


class TestCompletedEntry:
    """Contract tests for CompletedEntry dataclass."""

    def test_completed_entry_fields(self) -> None:
        """CompletedEntry MUST have step_id, agent, runner_name, result, worktree_path, elapsed_seconds.

        Contract pin from: docs/DRIVER-ARCHITECTURE.md Section 2.1
        Blueprint: DRIVER-BLUEPRINT.md Flow 3 (reconcile input)
        """
        raise NotImplementedError("Contract: CompletedEntry required fields")

    def test_completed_entry_result_type(self) -> None:
        """CompletedEntry.result MUST be RunnerResult.

        Contract pin from: docs/DRIVER-ARCHITECTURE.md Section 2.1
        """
        raise NotImplementedError("Contract: CompletedEntry.result is RunnerResult")


class TestDriverState:
    """Contract tests for DriverState dataclass."""

    def test_driver_state_running_type(self) -> None:
        """DriverState.running MUST be dict[str, RunningEntry].

        Contract pin from: docs/DRIVER-ARCHITECTURE.md Section 2.1
        Invariant: RunningEntry instances exist in running if and only if runner is alive.
        """
        raise NotImplementedError("Contract: DriverState.running is dict[str, RunningEntry]")

    def test_driver_state_completed_queue_type(self) -> None:
        """DriverState.completed_queue MUST be list[CompletedEntry].

        Contract pin from: docs/DRIVER-ARCHITECTURE.md Section 2.1
        Invariant: Drained each loop iteration.
        """
        raise NotImplementedError("Contract: DriverState.completed_queue is list[CompletedEntry]")

    def test_driver_state_failure_counts_type(self) -> None:
        """DriverState.failure_counts MUST be dict[str, int].

        Contract pin from: docs/DRIVER-ARCHITECTURE.md Section 2.1
        Maps: step_id -> consecutive failure count.
        """
        raise NotImplementedError("Contract: DriverState.failure_counts is dict[str, int]")

    def test_driver_state_failure_history_type(self) -> None:
        """DriverState.failure_history MUST be dict[str, list[str]].

        Contract pin from: docs/DRIVER-ARCHITECTURE.md Section 2.1
        Maps: step_id -> [error_output_1, error_output_2, ...].
        """
        raise NotImplementedError("Contract: DriverState.failure_history is dict[str, list[str]]")

    def test_driver_state_runner_failures_type(self) -> None:
        """DriverState.runner_failures MUST be dict[tuple[str, str], int].

        Contract pin from: docs/DRIVER-ARCHITECTURE.md Section 2.1
        Maps: (step_id, runner_name) -> consecutive failure count for runner fallback.
        """
        raise NotImplementedError(
            "Contract: DriverState.runner_failures is dict[tuple[str, str], int]"
        )

    def test_driver_state_agent_overrides_type(self) -> None:
        """DriverState.agent_overrides MUST be dict[str, str].

        Contract pin from: docs/DRIVER-ARCHITECTURE.md Section 2.1
        Maps: step_id -> overridden agent name (from judge SWITCH_AGENT verdict).
        """
        raise NotImplementedError("Contract: DriverState.agent_overrides is dict[str, str]")

    def test_driver_state_merge_lock_type(self) -> None:
        """DriverState.merge_lock MUST be asyncio.Lock.

        Contract pin from: docs/DRIVER-ARCHITECTURE.md Section 2.1
        Invariant: Sole serialization mechanism for git merge operations.
        Architecture Note 7: MUST be asyncio.Lock, NOT threading.Lock.
        """
        raise NotImplementedError("Contract: DriverState.merge_lock is asyncio.Lock")

    def test_driver_state_as_running_tasks(self) -> None:
        """DriverState.as_running_tasks() MUST return list[RunningTask].

        Contract pin from: docs/DRIVER-ARCHITECTURE.md Section 2.1
        Blueprint: DRIVER-BLUEPRINT.md Flow 1 (decide_result = decide(...))
        """
        raise NotImplementedError(
            "Contract: DriverState.as_running_tasks returns list[RunningTask]"
        )

    def test_driver_state_drain_completed(self) -> None:
        """DriverState.drain_completed() MUST return list[CompletedResult] | None.

        Contract pin from: docs/DRIVER-ARCHITECTURE.md Section 2.1
        Blueprint: DRIVER-BLUEPRINT.md Flow 1 (completed_results = state.drain_completed())
        """
        raise NotImplementedError(
            "Contract: DriverState.drain_completed returns list[CompletedResult] | None"
        )

    def test_driver_state_wait_for_any(self) -> None:
        """DriverState.wait_for_any() MUST be async and return CompletedEntry.

        Contract pin from: docs/DRIVER-ARCHITECTURE.md Section 2.1
        Blueprint: DRIVER-BLUEPRINT.md Flow 1 (completed = await state.wait_for_any())
        """
        raise NotImplementedError(
            "Contract: DriverState.wait_for_any is async and returns CompletedEntry"
        )

    def test_driver_state_loop_detector_type(self) -> None:
        """DriverState.loop_detector MUST be list[str].

        Contract pin from: docs/DRIVER-ARCHITECTURE.md Section 2.1
        Purpose: Last N serialized decide() action signatures for loop detection.
        """
        raise NotImplementedError("Contract: DriverState.loop_detector is list[str]")


class TestMergeResult:
    """Contract tests for MergeResult dataclass."""

    def test_merge_result_status_values(self) -> None:
        """MergeResult.status MUST be "clean" | "auto_resolved" | "conflict".

        Contract pin from: docs/DRIVER-ARCHITECTURE.md Section 2.1
        Blueprint: DRIVER-BLUEPRINT.md Worktree Lifecycle (worktree.py)
        """
        raise NotImplementedError("Contract: MergeResult.status values")

    def test_merge_result_trivial_default(self) -> None:
        """MergeResult.trivial MUST default to True.

        Contract pin from: docs/DRIVER-ARCHITECTURE.md Section 2.1
        """
        raise NotImplementedError("Contract: MergeResult.trivial defaults to True")


class TestSessionEntry:
    """Contract tests for SessionEntry dataclass."""

    def test_session_entry_fields(self) -> None:
        """SessionEntry MUST have session_id, runner_name, agent, step_id, completed_at.

        Contract pin from: docs/DRIVER-ARCHITECTURE.md Section 2.1
        Blueprint: DRIVER-BLUEPRINT.md Session Pool (session.py)
        """
        raise NotImplementedError("Contract: SessionEntry required fields")

    def test_session_entry_completed_at_type(self) -> None:
        """SessionEntry.completed_at MUST be float (time.monotonic()).

        Contract pin from: docs/DRIVER-ARCHITECTURE.md Section 2.1
        """
        raise NotImplementedError("Contract: SessionEntry.completed_at is float")
