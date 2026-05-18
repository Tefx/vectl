"""Prompt artifact and runner handoff contracts.

Authority: docs/RFC-opencode-orchestration-runner.md sections 8, 8.5, 9.2, 9.3
"""

from dataclasses import dataclass
from typing import Literal

_RUNS_INPUT_DIR: Literal["input"] = "input"
"""Relative subdirectory under a run's artifact root for input artifacts.

Authority: docs/RFC-opencode-orchestration-runner.md section 8.1

Every run's authoritative prompt artifacts must be materialized under::

    .vectl/runs/<run_id>/input/

This constant exists so that path resolution is derived from one frozen
definition rather than scattered string literals.
"""

_PROMPT_BUNDLE_FILENAME: Literal["prompt_bundle.json"] = "prompt_bundle.json"
"""Filename for the structured prompt bundle JSON artifact.

Authority: docs/RFC-opencode-orchestration-runner.md section 8.3
"""

_RUNNER_PROMPT_FILENAME: Literal["runner_prompt.md"] = "runner_prompt.md"
"""Filename for the runner-consumable flattened prompt Markdown artifact.

Authority: docs/RFC-opencode-orchestration-runner.md section 8.4
"""

_WORKSPACE_ORCH_DIR: Literal[".vectl/orch"] = ".vectl/orch"
"""Relative workspace directory for orchestration runtime artifacts.

Authority: docs/RFC-opencode-orchestration-runner.md section 8.2
"""

_RUNNER_PROMPT_WORKSPACE_RELATIVE: Literal[".vectl/orch/runner_prompt.md"] = (
    ".vectl/orch/runner_prompt.md"
)
"""Workspace-relative path to the runner-consumable prompt copy.

Authority: docs/RFC-opencode-orchestration-runner.md section 8.2, 8.5

The workspace should receive a runner-readable prompt copy at this path,
allowing runner startup to use a stable file path inside the execution
workspace.
"""


@dataclass(frozen=True)
class PromptArtifactPaths:
    """Frozen contract pinning all prompt artifact location semantics.

    Authority: docs/RFC-opencode-orchestration-runner.md section 8

    This dataclass pins the three required prompt artifact locations:

    1. **Authority prompt bundle** (structured JSON):
       ``<artifact_root>/<run_id>/input/prompt_bundle.json``
    2. **Authority runner prompt** (flattened Markdown):
       ``<artifact_root>/<run_id>/input/runner_prompt.md``
    3. **Workspace copy** (runner-consumable Markdown):
       ``<workspace>/.vectl/orch/runner_prompt.md``

    Attributes:
        prompt_bundle_path: Absolute path to the structured prompt bundle JSON
            under the run's authoritative artifact root.
        runner_prompt_path: Absolute path to the flattened runner prompt
            Markdown under the run's authoritative artifact root.
        workspace_prompt_path: Absolute path to the workspace copy of the
            runner prompt. This is the canonical location the runner reads
            at startup.
        workspace_prompt_relative: Workspace-relative path string for the
            runner prompt copy. Always equals ``.vectl/orch/runner_prompt.md``.
    """

    prompt_bundle_path: str
    runner_prompt_path: str
    workspace_prompt_path: str
    workspace_prompt_relative: str = _RUNNER_PROMPT_WORKSPACE_RELATIVE


@dataclass(frozen=True)
class RunnerHandoffEnv:
    """Frozen contract pinning the required VECTL_ORCH_* environment variables.

    Authority: docs/RFC-opencode-orchestration-runner.md section 8.5

    The process environment must include these variables for OpenCode runner
    handoff. They provide the runner with stable references to orchestration
    identity and prompt artifact locations.

    Attributes:
        VECTL_ORCH_RUN_ID: The run identifier for this orchestration execution.
        VECTL_ORCH_STEP_ID: The step being executed.
        VECTL_ORCH_AGENT_ID: The agent identifier for the execution.
        VECTL_ORCH_PROMPT_PATH: Path to the workspace-copy runner prompt file.
        VECTL_ORCH_PROMPT_BUNDLE_PATH: Path to the structured prompt bundle JSON.
    """

    VECTL_ORCH_RUN_ID: str
    VECTL_ORCH_STEP_ID: str
    VECTL_ORCH_AGENT_ID: str
    VECTL_ORCH_PROMPT_PATH: str
    VECTL_ORCH_PROMPT_BUNDLE_PATH: str

    def as_dict(self) -> dict[str, str]:
        """Render handoff environment as a plain dict suitable for ``subprocess`` env.

        Returns:
            Dictionary mapping each VECTL_ORCH_* variable name to its value.
        """
        return {
            "VECTL_ORCH_RUN_ID": self.VECTL_ORCH_RUN_ID,
            "VECTL_ORCH_STEP_ID": self.VECTL_ORCH_STEP_ID,
            "VECTL_ORCH_AGENT_ID": self.VECTL_ORCH_AGENT_ID,
            "VECTL_ORCH_PROMPT_PATH": self.VECTL_ORCH_PROMPT_PATH,
            "VECTL_ORCH_PROMPT_BUNDLE_PATH": self.VECTL_ORCH_PROMPT_BUNDLE_PATH,
        }


_OPENCODE_BOOTSTRAP_MESSAGE_START = (
    "Read the attached runner prompt file, execute the requested task in the "
    "current workspace, and then exit."
)
"""Frozen one-shot bootstrap message for OpenCode start mode.

Authority: docs/RFC-opencode-orchestration-runner.md section 9.2
"""

_OPENCODE_BOOTSTRAP_MESSAGE_RESUME: Literal[
    "Continue this session by executing the attached runner prompt in the current workspace."
] = "Continue this session by executing the attached runner prompt in the current workspace."
"""Frozen one-shot bootstrap message for OpenCode resume mode.

Authority: docs/RFC-opencode-orchestration-runner.md section 9.3
"""


@dataclass(frozen=True)
class OpenCodeLaunchConfig:
    """Frozen contract pinning the OpenCode launch command surface and flags.

    Authority: docs/RFC-opencode-orchestration-runner.md sections 9.2, 9.3

    This dataclass pins the complete OpenCode launch contract, including the
    required ``--file`` attachment. The handoff mechanism is frozen as:

        workspace prompt file + explicit env vars + ``--file`` attachment
        + bounded bootstrap message.

    Attributes:
        file_flag: The workspace-relative path passed via ``--file``.
            Always equals ``.vectl/orch/runner_prompt.md``.
        format_flag: The output format flag for OpenCode. Always ``json``.
        dir_flag_key: The directory flag name. Always ``--dir``.
        agent_flag_key: The agent selection flag name. Always ``--agent``.
        session_flag_key: The session continuation flag name. Always ``--session``.
        bootstrap_start: The bounded one-shot bootstrap message for start mode.
        bootstrap_resume: The bounded one-shot bootstrap message for resume mode.
    """

    file_flag: str = _RUNNER_PROMPT_WORKSPACE_RELATIVE
    format_flag: Literal["json"] = "json"
    dir_flag_key: Literal["--dir"] = "--dir"
    agent_flag_key: Literal["--agent"] = "--agent"
    session_flag_key: Literal["--session"] = "--session"
    bootstrap_start: str = _OPENCODE_BOOTSTRAP_MESSAGE_START
    bootstrap_resume: str = _OPENCODE_BOOTSTRAP_MESSAGE_RESUME
