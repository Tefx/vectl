"""Implementation tests for driver config - load_config and invariants.

Tests verify the implemented load_config function and config validation.
"""

from pathlib import Path

import pytest
import yaml

from src.vectl.driver.config import DriverConfig, RunnerConfig, load_config
from src.vectl.driver.errors import ConfigError


class TestLoadConfigImplementation:
    """Tests for load_config implementation."""

    def test_load_config_valid_yaml(self, temp_driver_yaml: Path) -> None:
        """load_config parses valid YAML and returns DriverConfig."""
        config = load_config(temp_driver_yaml)
        assert isinstance(config, DriverConfig)
        assert "claude" in config.runners
        assert "opencode" in config.runners
        assert config.fallback_runner == "opencode"

    def test_load_config_missing_file(self, tmp_path: Path) -> None:
        """load_config raises ConfigError for missing file."""
        missing = tmp_path / "nonexistent.yaml"
        with pytest.raises(ConfigError, match="not found"):
            load_config(missing)

    def test_load_config_empty_file(self, tmp_path: Path) -> None:
        """load_config raises ConfigError for empty file."""
        empty = tmp_path / "empty.yaml"
        empty.write_text("")
        with pytest.raises(ConfigError, match="Empty"):
            load_config(empty)

    def test_load_config_invalid_yaml(self, tmp_path: Path) -> None:
        """load_config raises ConfigError for invalid YAML syntax."""
        invalid = tmp_path / "invalid.yaml"
        invalid.write_text("runners: [invalid: yaml: syntax")
        with pytest.raises(ConfigError, match="Invalid YAML"):
            load_config(invalid)

    def test_load_config_not_dict(self, tmp_path: Path) -> None:
        """load_config raises ConfigError for non-dict YAML."""
        not_dict = tmp_path / "not_dict.yaml"
        not_dict.write_text("- item1\n- item2")
        with pytest.raises(ConfigError, match="dictionary"):
            load_config(not_dict)

    def test_load_config_missing_runners(self, tmp_path: Path) -> None:
        """load_config validates required 'runners' field."""
        missing_runners = tmp_path / "no_runners.yaml"
        missing_runners.write_text("fallback_runner: opencode")
        with pytest.raises(ConfigError, match="Invalid configuration"):
            load_config(missing_runners)

    def test_load_config_creates_parent_dirs(self, tmp_path: Path) -> None:
        """load_config works when events_file parent directories need creation."""
        config_yaml = tmp_path / "driver.yaml"
        config_yaml.write_text("""
runners:
  opencode:
    command: opencode
observability:
  events_file: .vectl/driver-events.jsonl
""")
        config = load_config(config_yaml)
        assert config.observability.events_file == ".vectl/driver-events.jsonl"


class TestConfigValidationInvariants:
    """Tests for DriverConfig validation invariants at load time."""

    def test_valid_config_passes_validation(self, driver_config_dict: dict) -> None:
        """Valid config passes invariant validation."""
        runners = {
            name: RunnerConfig(**runner_data)
            for name, runner_data in driver_config_dict["runners"].items()
        }
        config = DriverConfig(runners=runners)
        assert config.fallback_runner == "opencode"
        assert config.judge.runner == "opencode"
        assert "opencode" in config.runners

    def test_invalid_config_missing_fallback_runner(
        self, invalid_driver_config_missing_fallback: dict
    ) -> None:
        """ConfigError raised when fallback_runner not in runners."""
        from pydantic import ValidationError

        runners = {
            name: RunnerConfig(**runner_data)
            for name, runner_data in invalid_driver_config_missing_fallback["runners"].items()
        }
        with pytest.raises(ValidationError):
            # The model_validator will raise ValueError which pydantic wraps
            DriverConfig(
                runners=runners,
                fallback_runner=invalid_driver_config_missing_fallback["fallback_runner"],
            )

    def test_invalid_config_missing_judge_runner(
        self, invalid_driver_config_missing_runner: dict
    ) -> None:
        """ConfigError raised when judge.runner not in runners."""
        from pydantic import ValidationError

        runners = {
            name: RunnerConfig(**runner_data)
            for name, runner_data in invalid_driver_config_missing_runner["runners"].items()
        }
        with pytest.raises(ValidationError):
            DriverConfig(
                runners=runners,
                fallback_runner=invalid_driver_config_missing_runner["fallback_runner"],
                judge=invalid_driver_config_missing_runner["judge"],
            )

    def test_load_config_validates_invariants(
        self, tmp_path: Path, invalid_driver_config_missing_runner: dict
    ) -> None:
        """load_config validates invariants and raises ConfigError."""
        invalid_yaml = tmp_path / "invalid_runner.yaml"
        invalid_yaml.write_text(yaml.dump(invalid_driver_config_missing_runner))
        with pytest.raises(ConfigError, match="Invalid configuration"):
            load_config(invalid_yaml)


class TestRouteAgentImplementation:
    """Tests for DriverConfig.route_agent implementation."""

    def test_route_agent_exact_match(self) -> None:
        """route_agent returns exact match from agent_routing."""
        from src.vectl.driver.config import JudgeConfig

        config = DriverConfig(
            runners={"claude": RunnerConfig(command="claude")},
            agent_routing={"python-senior": "claude"},
            fallback_runner="claude",
            judge=JudgeConfig(runner="claude"),  # Must match runners
        )
        assert config.route_agent("python-senior") == "claude"

    def test_route_agent_glob_pattern(self) -> None:
        """route_agent matches glob patterns in agent_routing."""
        config = DriverConfig(
            runners={"opencode": RunnerConfig(command="opencode")},
            agent_routing={"*-tester": "opencode"},
            fallback_runner="opencode",
        )
        assert config.route_agent("python-tester") == "opencode"
        assert config.route_agent("backend-tester") == "opencode"
        assert config.route_agent("integration-tester") == "opencode"

    def test_route_agent_fallback(self) -> None:
        """route_agent falls back to fallback_runner when no match."""
        config = DriverConfig(
            runners={"opencode": RunnerConfig(command="opencode")},
            agent_routing={"python-senior": "opencode"},
            fallback_runner="opencode",
        )
        assert config.route_agent("unknown-agent") == "opencode"

    def test_route_agent_priority_exact_over_glob(self) -> None:
        """route_agent prefers exact match over glob pattern."""
        config = DriverConfig(
            runners={
                "claude": RunnerConfig(command="claude"),
                "opencode": RunnerConfig(command="opencode"),
            },
            agent_routing={
                "python-tester": "claude",
                "*-tester": "opencode",
            },
            fallback_runner="opencode",
        )
        # Exact match takes priority
        assert config.route_agent("python-tester") == "claude"
        # Glob match for non-exact
        assert config.route_agent("backend-tester") == "opencode"

    def test_route_agent_empty_routing(self) -> None:
        """route_agent returns fallback when agent_routing is empty."""
        config = DriverConfig(
            runners={"opencode": RunnerConfig(command="opencode")},
            agent_routing={},
            fallback_runner="opencode",
        )
        assert config.route_agent("any-agent") == "opencode"


class TestFileObserverImplementation:
    """Tests for FileObserver implementation."""

    def test_file_observer_creates_file(self, tmp_path: Path) -> None:
        """FileObserver creates the events file on first emit."""
        from src.vectl.driver.config import ObservabilityConfig
        from src.vectl.driver.observe import FileObserver

        events_file = tmp_path / "events.jsonl"
        config = ObservabilityConfig(events_file=str(events_file))
        observer = FileObserver(config)

        observer.emit("WAIT", reason="hello")

        assert events_file.exists()

    def test_file_observer_writes_jsonl(self, tmp_path: Path) -> None:
        """FileObserver writes valid JSONL with ts, event, version, data."""
        import json

        from src.vectl.driver.config import ObservabilityConfig
        from src.vectl.driver.observe import FileObserver

        events_file = tmp_path / "events.jsonl"
        config = ObservabilityConfig(events_file=str(events_file))
        observer = FileObserver(config)

        observer.emit("STEP_DISPATCHED", step_id="core.impl", agent="python-executor")
        observer.close()

        lines = events_file.read_text().strip().split("\n")
        assert len(lines) == 1

        data = json.loads(lines[0])
        assert "ts" in data
        assert data["event"] == "STEP_DISPATCHED"
        assert data["version"] == 1
        assert data["data"]["step_id"] == "core.impl"
        assert data["data"]["agent"] == "python-executor"

    def test_file_observer_appends_multiple_events(self, tmp_path: Path) -> None:
        """FileObserver appends multiple events to the same file."""
        import json

        from src.vectl.driver.config import ObservabilityConfig
        from src.vectl.driver.observe import FileObserver

        events_file = tmp_path / "events.jsonl"
        config = ObservabilityConfig(events_file=str(events_file))
        observer = FileObserver(config)

        observer.emit("WAIT", reason="value1")
        observer.emit("HALT", reason="value2")
        observer.close()

        lines = events_file.read_text().strip().split("\n")
        assert len(lines) == 2

        event1 = json.loads(lines[0])
        event2 = json.loads(lines[1])
        assert event1["event"] == "WAIT"
        assert event2["event"] == "HALT"

    def test_file_observer_creates_parent_dirs(self, tmp_path: Path) -> None:
        """FileObserver creates parent directories for events_file."""
        from src.vectl.driver.config import ObservabilityConfig
        from src.vectl.driver.observe import FileObserver

        events_file = tmp_path / "subdir" / "events.jsonl"
        config = ObservabilityConfig(events_file=str(events_file))
        observer = FileObserver(config)

        observer.emit("WAIT", reason="idle")
        observer.close()

        assert events_file.exists()
        assert events_file.parent.exists()

    def test_null_observer_discards_events(self) -> None:
        """NullObserver discards all events without error."""
        from src.vectl.driver.observe import NullObserver

        observer = NullObserver()
        observer.emit("WAIT", reason="value")
        observer.close()  # No error

    def test_file_observer_serializes_nested_values(self, tmp_path: Path) -> None:
        """FileObserver serializes nested dicts and lists correctly."""
        import json

        from src.vectl.driver.config import ObservabilityConfig
        from src.vectl.driver.observe import FileObserver

        events_file = tmp_path / "events.jsonl"
        config = ObservabilityConfig(events_file=str(events_file))
        observer = FileObserver(config)

        observer.emit(
            "JUDGMENT",
            context={
                "verdict": "ACCEPT",
                "reason": "Tests pass",
                "nested": {"key": "value"},
            },
            tokens=[100, 200, 300],
        )
        observer.close()

        data = json.loads(events_file.read_text())
        assert data["version"] == 1
        assert data["data"]["context"]["verdict"] == "ACCEPT"
        assert data["data"]["context"]["nested"]["key"] == "value"
        assert data["data"]["tokens"] == [100, 200, 300]


class TestDriverStateImplementation:
    """Tests for DriverState method implementations."""

    def test_as_running_tasks_empty(self) -> None:
        """DriverState.as_running_tasks returns empty list when no running tasks."""
        from src.vectl.driver.types import DriverState

        state = DriverState()
        tasks = state.as_running_tasks()
        assert tasks == []

    def test_failure_count_default_zero(self) -> None:
        """DriverState.failure_count returns 0 for unknown step."""
        from src.vectl.driver.types import DriverState

        state = DriverState()
        assert state.failure_count("unknown-step") == 0

    def test_increment_failure_updates_count(self) -> None:
        """DriverState.increment_failure updates both counters."""
        from src.vectl.driver.types import DriverState

        state = DriverState()
        state.increment_failure("step-1", "claude")
        state.increment_failure("step-1", "claude")

        assert state.failure_count("step-1") == 2
        assert state.runner_failures[("step-1", "claude")] == 2

    def test_set_agent_override(self) -> None:
        """DriverState.set_agent_override stores override."""
        from src.vectl.driver.types import DriverState

        state = DriverState()
        state.set_agent_override("step-1", "python-senior")
        assert state.agent_overrides["step-1"] == "python-senior"

    def test_get_failure_context_none(self) -> None:
        """DriverState.get_failure_context returns None for unknown step."""
        from src.vectl.driver.types import DriverState

        state = DriverState()
        assert state.get_failure_context("unknown") is None

    def test_get_failure_context_last(self) -> None:
        """DriverState.get_failure_context returns last failure output."""
        from src.vectl.driver.types import DriverState

        state = DriverState()
        state.failure_history["step-1"] = ["error 1", "error 2", "error 3"]
        assert state.get_failure_context("step-1") == "error 3"

    def test_get_failure_history_empty(self) -> None:
        """DriverState.get_failure_history returns empty list for unknown step."""
        from src.vectl.driver.types import DriverState

        state = DriverState()
        assert state.get_failure_history("unknown") == []

    def test_detect_loop_insufficient_window(self) -> None:
        """DriverState.detect_loop returns False with insufficient history."""
        from src.vectl.driver.types import DriverState

        state = DriverState()
        state.loop_detector = ["sig-1", "sig-2", "sig-3"]
        assert state.detect_loop(window=10) is False

    def test_detect_loop_no_loop(self) -> None:
        """DriverState.detect_loop returns False when signatures differ."""
        from src.vectl.driver.types import DriverState

        state = DriverState()
        state.loop_detector = ["sig-1", "sig-2", "sig-3", "sig-4", "sig-5"]
        assert state.detect_loop(window=5) is False

    def test_detect_loop_detected(self) -> None:
        """DriverState.detect_loop returns True when all signatures identical."""
        from src.vectl.driver.types import DriverState

        state = DriverState()
        state.loop_detector = ["sig-1"] * 10
        assert state.detect_loop(window=10) is True

    def test_summary(self) -> None:
        """DriverState.summary returns summary dict."""
        from src.vectl.driver.types import DriverState

        state = DriverState()
        state.failure_counts["step-1"] = 1
        state.halt_requested = False

        summary = state.summary()
        assert "running_count" in summary
        assert "completed_count" in summary
        assert "failure_count" in summary
        assert "halt_requested" in summary
