"""Prompt artifact materialization and runner handoff contract.

Authority: docs/RFC-opencode-orchestration-runner.md sections 8, 8.5, 9.2

This module pins the stable contract for prompt materialization before runner
code is written. It provides:

1. **Path resolution** — deterministic artifact locations based on run_id
   and workspace path.
2. **Environment variable construction** — the required ``VECTL_ORCH_*``
   variables for runner handoff.
3. **Materialization** — writing the actual prompt artifacts to disk.
4. **Launch command construction** — building the frozen OpenCode launch
   argv from the pinned contract.

All paths, env vars, and flags are derived from frozen constants in
``contracts.py`` so that the handoff contract cannot drift between
definition and implementation.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from vectl.core.orchestration.prompt_contracts import (
    build_opencode_launch_argv_data,
    build_runner_handoff_env_data,
    compute_prompt_bundle_sha256 as compute_prompt_bundle_sha256_data,
    output_contract_lines as output_contract_lines_data,
    resolve_prompt_artifact_paths_data,
)
from vectl.orchestration.contracts import (
    OpenCodeLaunchConfig,
    PromptArtifactPaths,
    PromptBundle,
    RunnerHandoffEnv,
)

if TYPE_CHECKING:
    pass


# ---------------------------------------------------------------------
# Path Resolution
# ---------------------------------------------------------------------


def resolve_prompt_artifact_paths(
    *,
    artifact_root: Path,
    run_id: str,
    workspace: Path,
) -> PromptArtifactPaths:
    """Resolve all prompt artifact paths from frozen contract locations.

    Authority: docs/RFC-opencode-orchestration-runner.md sections 8.1, 8.2

    This function computes the three pinned artifact locations:

    1. Authority bundle: ``<artifact_root>/<run_id>/input/prompt_bundle.json``
    2. Authority prompt: ``<artifact_root>/<run_id>/input/runner_prompt.md``
    3. Workspace prompt: ``<workspace>/.vectl/orch/runner_prompt.md``

    Args:
        artifact_root: Root directory for run artifacts (e.g. ``.vectl/runs``).
        run_id: Unique run identifier.
        workspace: Absolute path to the execution workspace.

    Returns:
        Frozen ``PromptArtifactPaths`` with all three locations resolved.
    """
    data = resolve_prompt_artifact_paths_data(run_id, str(artifact_root), str(workspace))
    return PromptArtifactPaths(
        prompt_bundle_path=str(data["bundle_path"]),
        runner_prompt_path=str(data["runner_prompt_path"]),
        workspace_prompt_path=str(data["workspace_prompt_path"]),
        workspace_prompt_relative=str(data["workspace_prompt_relative"]),
    )


# ---------------------------------------------------------------------
# Environment Variable Construction
# ---------------------------------------------------------------------


def build_runner_handoff_env(
    *,
    run_id: str,
    step_id: str,
    agent_id: str,
    artifact_paths: PromptArtifactPaths,
) -> RunnerHandoffEnv:
    """Build the required VECTL_ORCH_* environment variables for runner handoff.

    Authority: docs/RFC-opencode-orchestration-runner.md section 8.5

    The runner process environment must include exactly these variables:

    - ``VECTL_ORCH_RUN_ID``
    - ``VECTL_ORCH_STEP_ID``
    - ``VECTL_ORCH_AGENT_ID``
    - ``VECTL_ORCH_PROMPT_PATH``
    - ``VECTL_ORCH_PROMPT_BUNDLE_PATH``

    Args:
        run_id: The run identifier for this orchestration execution.
        step_id: The step being executed.
        agent_id: The agent identifier for the execution.
        artifact_paths: Resolved prompt artifact paths.

    Returns:
        Frozen ``RunnerHandoffEnv`` with all five required variables.
    """
    data = dict(
        build_runner_handoff_env_data(
            run_id,
            agent_id,
            step_id,
            artifact_paths.workspace_prompt_path,
        )
    )
    data["VECTL_ORCH_PROMPT_BUNDLE_PATH"] = artifact_paths.prompt_bundle_path
    return RunnerHandoffEnv(**data)


# ---------------------------------------------------------------------
# Prompt Materialization
# ---------------------------------------------------------------------


def compute_prompt_bundle_sha256(bundle: PromptBundle) -> str:
    """Compute deterministic SHA-256 digest over a prompt bundle.

    Authority: docs/RFC-opencode-orchestration-runner.md section 8.3

    The digest covers ``system_prompt``, ``task_prompt``, and all messages
    in a deterministic order so that the same bundle always produces the
    same hash.

    Args:
        bundle: Rendered prompt bundle.

    Returns:
        Hex-encoded SHA-256 digest.
    """
    return compute_prompt_bundle_sha256_data(
        {
            "system_prompt": bundle.system_prompt,
            "task_prompt": bundle.task_prompt,
            "messages": bundle.messages,
        }
    )


def materialize_prompt_artifacts(
    *,
    bundle: PromptBundle,
    artifact_paths: PromptArtifactPaths,
    role_id: str,
    agent_id: str,
    runner: str,
    output_contract: str = "freeform_evidence",
) -> None:
    """Write the authoritative prompt artifacts to disk.

    Authority: docs/RFC-opencode-orchestration-runner.md sections 8.1, 8.3, 8.4

    This function materializes:

    1. ``input/prompt_bundle.json`` — the structured JSON bundle with
       role, agent, runner, system/task prompts, messages, and SHA-256.
    2. ``input/runner_prompt.md`` — the flattened Markdown runner prompt.
    3. ``.vectl/orch/runner_prompt.md`` — the workspace copy.

    It does NOT launch the runner. It only writes the artifacts.

    Args:
        bundle: Rendered prompt bundle from the dispatch coordinator.
        artifact_paths: Resolved prompt artifact paths.
        role_id: Role identifier for the structured bundle.
        agent_id: Agent identifier for the structured bundle.
        runner: Runner identifier for the structured bundle.
    """
    sha256 = compute_prompt_bundle_sha256(bundle)

    # 1. Write structured prompt_bundle.json
    bundle_path = Path(artifact_paths.prompt_bundle_path)
    bundle_path.parent.mkdir(parents=True, exist_ok=True)
    bundle_payload = {
        "role_id": role_id,
        "agent_id": agent_id,
        "runner": runner,
        "output_contract": output_contract,
        "system_prompt": bundle.system_prompt,
        "task_prompt": bundle.task_prompt,
        "messages": [dict(m) for m in bundle.messages],
        "prompt_bundle_sha256": sha256,
    }
    bundle_path.write_text(json.dumps(bundle_payload, indent=2, ensure_ascii=False) + "\n")

    # 2. Write flattened runner_prompt.md
    authority_prompt_path = Path(artifact_paths.runner_prompt_path)
    authority_prompt_path.parent.mkdir(parents=True, exist_ok=True)
    runner_prompt_content = _render_runner_prompt_md(
        role_id=role_id,
        agent_id=agent_id,
        bundle=bundle,
        output_contract=output_contract,
    )
    authority_prompt_path.write_text(runner_prompt_content)

    # 3. Write workspace copy
    workspace_prompt = Path(artifact_paths.workspace_prompt_path)
    workspace_prompt.parent.mkdir(parents=True, exist_ok=True)
    workspace_prompt.write_text(runner_prompt_content)


def _render_runner_prompt_md(
    *,
    role_id: str,
    agent_id: str,
    bundle: PromptBundle,
    output_contract: str = "freeform_evidence",
) -> str:
    """Render the flattened runner-consumable Markdown prompt.

    Authority: docs/RFC-opencode-orchestration-runner.md section 8.4

    The runner prompt includes:
    1. Role / agent identity
    2. System instructions
    3. Task prompt
    4. Context messages
    5. Output contract
    6. Execution/work rules

    Args:
        role_id: Role identifier.
        agent_id: Agent identifier.
        bundle: Rendered prompt bundle.

    Returns:
        Complete runner prompt as Markdown text.
    """
    parts: list[str] = []

    # 1. Identity header
    parts.append(f"# Runner Prompt: {role_id}")
    parts.append("")
    parts.append(f"- **Role**: {role_id}")
    parts.append(f"- **Agent**: {agent_id}")
    parts.append("")

    # 2. System instructions
    if bundle.system_prompt.strip():
        parts.append("## System Instructions")
        parts.append("")
        parts.append(bundle.system_prompt.strip())
        parts.append("")

    # 3. Task prompt
    if bundle.task_prompt.strip():
        parts.append("## Task")
        parts.append("")
        parts.append(bundle.task_prompt.strip())
        parts.append("")

    # 4. Context messages
    if bundle.messages:
        parts.append("## Context")
        parts.append("")
        for msg in bundle.messages:
            role = msg.get("role", "unknown")
            content = msg.get("content", "")
            parts.append(f"**[{role}]**: {content}")
            parts.append("")

    # 5. Output contract
    parts.append("## Output Contract")
    parts.append("")
    parts.extend(_output_contract_lines(output_contract))
    parts.append("")

    # 6. Execution rules
    parts.append("## Execution Rules")
    parts.append("")
    parts.append("- Complete ONLY the assigned task.")
    parts.append("- Do NOT start additional work or explore related issues.")
    parts.append("- When complete, STOP and return evidence.")
    parts.append("")

    return "\n".join(parts)


def _output_contract_lines(output_contract: str) -> list[str]:
    """Return runner-visible output contract instructions."""

    if output_contract == "resolution_report":
        return list(output_contract_lines_data(output_contract, ()))

    if output_contract == "structured_review_result":
        return list(output_contract_lines_data(output_contract, ()))

    return list(output_contract_lines_data(output_contract, ()))


# ---------------------------------------------------------------------
# Launch Command Construction
# ---------------------------------------------------------------------


def build_opencode_launch_argv(
    *,
    workspace: Path,
    agent_id: str,
    session_id: str | None = None,
    config: OpenCodeLaunchConfig | None = None,
) -> tuple[str, ...]:
    """Build the frozen OpenCode launch argv from the pinned contract.

    Authority: docs/RFC-opencode-orchestration-runner.md section 9.2, 9.3

    Start mode (no ``session_id``)::

        opencode run --format json --dir <workspace> --agent <agent_id> \\
            --file .vectl/orch/runner_prompt.md \\
            "Read the attached runner prompt file, execute the requested task \\
            in the current workspace, and then exit."

    Resume mode (with ``session_id``)::

        opencode run --format json --dir <workspace> --session <session_id> \\
            --agent <agent_id> --file .vectl/orch/runner_prompt.md \\
            "Continue this session by executing the attached runner prompt \\
            in the current workspace."

    ``--continue`` must NOT be used because it targets "last session" and
    is non-deterministic for machine recovery.

    Args:
        workspace: Absolute path to the execution workspace.
        agent_id: Agent identifier for ``--agent``.
        session_id: Optional session identifier for ``--session`` (resume).
        config: Launch config, defaults to ``OpenCodeLaunchConfig()``.

    Returns:
        Tuple of argv strings for ``subprocess.Popen``.
    """
    cfg = config or OpenCodeLaunchConfig()
    if cfg == OpenCodeLaunchConfig():
        return _build_default_opencode_launch_argv(
            workspace=workspace,
            agent_id=agent_id,
            session_id=session_id,
        )

    # Custom launch config remains a shell adapter concern because the Core
    # helper intentionally pins only the default public OpenCode contract.
    argv: list[str] = [
        "opencode",
        "run",
        "--format",
        cfg.format_flag,
        cfg.dir_flag_key,
        str(workspace.resolve()),
    ]

    if session_id is not None:
        argv.extend([cfg.session_flag_key, session_id])

    argv.extend(
        [
            cfg.agent_flag_key,
            agent_id,
            "--file",
            cfg.file_flag,
            "--",
        ]
    )

    if session_id is not None:
        argv.append(cfg.bootstrap_resume)
    else:
        argv.append(cfg.bootstrap_start)

    return tuple(argv)


def _build_default_opencode_launch_argv(
    *,
    workspace: Path,
    agent_id: str,
    session_id: str | None,
) -> tuple[str, ...]:
    """Build exact default OpenCode argv through Core prompt contract data."""
    argv = list(
        build_opencode_launch_argv_data(
            "opencode",
            str(workspace.resolve()),
            resume=session_id is not None,
            session_id=session_id,
        )
    )
    # Replace the Core helper's minimal file-only shape with the historical
    # public argv order pinned by ``OpenCodeLaunchConfig``.
    cfg = OpenCodeLaunchConfig()
    result = ["opencode", "run", "--format", cfg.format_flag, cfg.dir_flag_key, str(workspace.resolve())]
    if session_id is not None:
        result.extend([cfg.session_flag_key, session_id])
    result.extend([cfg.agent_flag_key, agent_id, "--file", cfg.file_flag, "--"])
    result.append(argv[-1])
    return tuple(result)


# @invar:allow shell_result: environment merge is true Shell boundary and public API returns dict
def build_opencode_launch_env(
    *,
    handoff_env: RunnerHandoffEnv,
    parent_env: dict[str, str] | None = None,
) -> dict[str, str]:
    """Build the complete process environment for OpenCode launch.

    Merges the required ``VECTL_ORCH_*`` handoff variables into the
    parent environment. The parent environment defaults to ``os.environ``
    if not provided.

    Args:
        handoff_env: Required runner handoff environment variables.
        parent_env: Parent environment to merge into. If None, uses
            ``os.environ``.

    Returns:
        Complete environment dict for subprocess launch.
    """
    import os

    base = dict(parent_env or os.environ)
    base.update(handoff_env.as_dict())
    return base


# ---------------------------------------------------------------------
# Recovery Validation
# ---------------------------------------------------------------------


@dataclass(frozen=True)
class PromptArtifactValidation:
    """Result of validating prompt artifacts for recovery.

    Authority: docs/RFC-opencode-orchestration-runner.md section 10.2

    Attributes:
        valid: Whether all prompt artifacts pass validation.
        reason: Human-readable description of validation outcome.
        prompt_bundle_path: Path to the validated prompt bundle, if valid.
        runner_prompt_path: Path to the validated runner prompt, if valid.
        workspace_prompt_path: Expected workspace copy path, if valid.
    """

    valid: bool
    reason: str
    prompt_bundle_path: str = ""
    runner_prompt_path: str = ""
    workspace_prompt_path: str = ""


# @invar:allow shell_result: recovery validation reads prompt artifact files and preserves public validation DTO API
def validate_prompt_artifacts_for_recovery(
    *,
    artifact_root: Path,
    run_id: str,
    workspace: Path,
) -> PromptArtifactValidation:
    """Validate that prompt artifacts exist and are valid for recovery fallback.

    Authority: docs/RFC-opencode-orchestration-runner.md section 10.2

    Minimum prompt artifact validity criteria before fresh relaunch:

    1. ``<artifact_root>/<run_id>/input/prompt_bundle.json`` exists
    2. ``prompt_bundle.json`` parses as valid JSON
    3. ``prompt_bundle.json`` contains non-empty values for:
       ``role_id``, ``agent_id``, ``runner``, ``system_prompt``,
       ``task_prompt``, ``prompt_bundle_sha256``
    4. ``<artifact_root>/<run_id>/input/runner_prompt.md`` exists
    5. ``runner_prompt.md`` is non-empty text

    Args:
        artifact_root: Root directory for run artifacts (e.g. ``.vectl/runs``).
        run_id: Unique run identifier.
        workspace: Absolute path to the execution workspace.

    Returns:
        PromptArtifactValidation indicating whether recovery is possible
        from these prompt artifacts.
    """
    paths = resolve_prompt_artifact_paths(
        artifact_root=artifact_root,
        run_id=run_id,
        workspace=workspace,
    )

    # Check 1: prompt_bundle.json must exist
    bundle_path = Path(paths.prompt_bundle_path)
    if not bundle_path.exists():
        return PromptArtifactValidation(
            valid=False,
            reason=f"prompt_bundle.json not found at {bundle_path}",
        )

    # Check 2: prompt_bundle.json must parse as valid JSON
    try:
        bundle_data = json.loads(bundle_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        return PromptArtifactValidation(
            valid=False,
            reason=f"prompt_bundle.json is not valid JSON: {exc}",
        )

    # Check 3: Required fields must be non-empty
    required_bundle_fields = (
        "role_id",
        "agent_id",
        "runner",
        "system_prompt",
        "task_prompt",
        "prompt_bundle_sha256",
    )
    for field_name in required_bundle_fields:
        value = bundle_data.get(field_name)
        if not isinstance(value, str) or not value.strip():
            return PromptArtifactValidation(
                valid=False,
                reason=(
                    f"prompt_bundle.json field {field_name!r} is missing or empty; "
                    f"recovery requires all of {required_bundle_fields}"
                ),
            )

    # Check 4: runner_prompt.md must exist
    prompt_path = Path(paths.runner_prompt_path)
    if not prompt_path.exists():
        return PromptArtifactValidation(
            valid=False,
            reason=f"runner_prompt.md not found at {prompt_path}",
        )

    # Check 5: runner_prompt.md must be non-empty
    prompt_content = prompt_path.read_text(encoding="utf-8")
    if not prompt_content.strip():
        return PromptArtifactValidation(
            valid=False,
            reason=f"runner_prompt.md is empty at {prompt_path}",
        )

    return PromptArtifactValidation(
        valid=True,
        reason="All prompt artifacts are valid for recovery.",
        prompt_bundle_path=paths.prompt_bundle_path,
        runner_prompt_path=paths.runner_prompt_path,
        workspace_prompt_path=paths.workspace_prompt_path,
    )
