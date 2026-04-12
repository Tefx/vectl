"""Runner registry for orchestration backend selection.

Authority: docs/ORCHESTRATION-PLANE-RUNNER-BACKEND.md section 9
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from vectl.orchestration.contracts import OpenCodeLaunchConfig
from vectl.orchestration.runners import (
    Runner,
    RunnerNotFoundError,
    SubprocessRunner,
)

# Lazy import to avoid circular dependency; OpenCodeRunner is resolved at
# registration time via get_opencode_runner().


def get_opencode_runner(
    artifact_root: Path | None = None,
    config: OpenCodeLaunchConfig | None = None,
) -> Runner:
    """Construct a properly configured OpenCodeRunner.

    Args:
        artifact_root: Root directory for run artifacts. Defaults to
            ``.vectl/runs`` relative to current working directory.
        config: Launch configuration. Defaults to ``OpenCodeLaunchConfig()``.

    Returns:
        Configured OpenCodeRunner instance.
    """
    from vectl.orchestration.runners import OpenCodeRunner

    return OpenCodeRunner(
        artifact_root=artifact_root or Path(".vectl/runs"),
        config=config or OpenCodeLaunchConfig(),
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
    """Build default runner registry with OpenCode as a dedicated adapter.

    Authority: docs/RFC-opencode-orchestration-runner.md sections 9, 15

    The ``opencode`` runner is registered as an ``OpenCodeRunner`` (not
    a bare ``SubprocessRunner("opencode")``) so that launch, resume, and
    session semantics follow the frozen CLI contract.
    """

    registry = RunnerRegistry()
    registry.register(
        "claude",
        SubprocessRunner(
            runner_id="claude",
            command=("claude", "-p", "--output-format", "json"),
        ),
    )
    registry.register("codex", SubprocessRunner(runner_id="codex", command=("codex",)))
    registry.register("opencode", get_opencode_runner())
    registry.register(
        "test", SubprocessRunner(runner_id="test", command=("echo", "runner-test-placeholder"))
    )
    return registry
