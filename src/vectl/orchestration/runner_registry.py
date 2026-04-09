"""Runner registry for orchestration backend selection.

Authority: docs/ORCHESTRATION-PLANE-RUNNER-BACKEND.md section 9
"""

from __future__ import annotations

from dataclasses import dataclass, field

from vectl.orchestration.runners import (
    Runner,
    RunnerNotFoundError,
    SubprocessRunner,
)


@dataclass
class RunnerRegistry:
    """Registry mapping runner identifiers to backend adapters."""

    _runners: dict[str, Runner] = field(default_factory=dict)

    def register(self, runner_id: str, runner: Runner) -> None:
        """Register a runner adapter by stable runner identifier.

        Args:
            runner_id: Runner namespace key.
            runner: Runner adapter implementation.
        """
        self._runners[runner_id] = runner

    def get(self, runner_id: str) -> Runner:
        """Resolve runner adapter for ``runner_id``.

        Args:
            runner_id: Runner namespace key.

        Returns:
            Registered runner adapter.

        Raises:
            RunnerNotFoundError: If runner ID is unknown.
        """
        runner = self._runners.get(runner_id)
        if runner is None:
            available = ", ".join(sorted(self._runners.keys()))
            raise RunnerNotFoundError(
                f"Unknown runner '{runner_id}'. Available runners: {available}"
            )
        return runner


def build_default_runner_registry() -> RunnerRegistry:
    """Build default subprocess-backed runner registry."""

    registry = RunnerRegistry()
    registry.register(
        "claude",
        SubprocessRunner(
            runner_id="claude",
            command=("claude", "-p", "--output-format", "json"),
        ),
    )
    registry.register("codex", SubprocessRunner(runner_id="codex", command=("codex",)))
    registry.register("opencode", SubprocessRunner(runner_id="opencode", command=("opencode",)))
    registry.register(
        "test", SubprocessRunner(runner_id="test", command=("echo", "runner-test-placeholder"))
    )
    return registry
