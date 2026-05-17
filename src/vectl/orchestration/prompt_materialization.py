"""Prompt artifact materialization and runner handoff contract.

Authority: docs/RFC-opencode-orchestration-runner.md sections 8, 8.5, 9.2

Pure path, prompt, digest, environment DTO, and argv compatibility surfaces are
owned by ``vectl.core.orchestration.prompt_contracts``. This module keeps the
filesystem/process-environment shell boundaries for artifact writes, ambient
environment merge, and recovery validation.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from vectl.core.orchestration.prompt_recovery import (
    recovery_validation_reason,
)
from vectl.core.orchestration.prompt_contracts import (
    build_default_opencode_launch_argv as _build_default_opencode_launch_argv,
    build_opencode_launch_argv,
    build_runner_handoff_env,
    compute_prompt_bundle_sha256,
    output_contract_line_list as _output_contract_lines,
    render_runner_prompt_bundle_md as _render_runner_prompt_md,
    resolve_prompt_artifact_paths,
)
from vectl.orchestration.contracts import PromptArtifactPaths, PromptBundle, RunnerHandoffEnv
from vectl.shell.orchestration.prompt_artifacts import (
    Failure,
    Result,
    read_process_environment,
    read_recovery_prompt_artifacts,
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
    """
    sha256 = compute_prompt_bundle_sha256(bundle)

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

    authority_prompt_path = Path(artifact_paths.runner_prompt_path)
    authority_prompt_path.parent.mkdir(parents=True, exist_ok=True)
    runner_prompt_content = _render_runner_prompt_md(
        role_id=role_id,
        agent_id=agent_id,
        bundle=bundle,
        output_contract=output_contract,
    )
    authority_prompt_path.write_text(runner_prompt_content)

    workspace_prompt = Path(artifact_paths.workspace_prompt_path)
    workspace_prompt.parent.mkdir(parents=True, exist_ok=True)
    workspace_prompt.write_text(runner_prompt_content)


def build_opencode_launch_env(
    *,
    handoff_env: RunnerHandoffEnv,
    parent_env: dict[str, str] | None = None,
) -> Result[dict[str, str], OSError]:
    """Build the complete process environment for OpenCode launch."""
    if parent_env is None:
        env_result = read_process_environment()
        if isinstance(env_result, Failure):
            raise env_result.error
        base = dict(env_result.value)
    else:
        base = dict(parent_env)
    base.update(handoff_env.as_dict())
    return base


@dataclass(frozen=True)
class PromptArtifactValidation:
    """Result of validating prompt artifacts for recovery.

    Authority: docs/RFC-opencode-orchestration-runner.md section 10.2
    """

    valid: bool
    reason: str
    prompt_bundle_path: str = ""
    runner_prompt_path: str = ""
    workspace_prompt_path: str = ""


def validate_prompt_artifacts_for_recovery(
    *,
    artifact_root: Path,
    run_id: str,
    workspace: Path,
) -> Result[PromptArtifactValidation, str]:
    """Validate that prompt artifacts exist and are valid for recovery fallback.

    Authority: docs/RFC-opencode-orchestration-runner.md section 10.2
    """
    paths = resolve_prompt_artifact_paths(
        artifact_root=artifact_root,
        run_id=run_id,
        workspace=workspace,
    )

    read_result = read_recovery_prompt_artifacts(paths=paths)
    if isinstance(read_result, Failure):
        return PromptArtifactValidation(
            valid=False,
            reason=str(read_result.error),
        )

    payload = read_result.value
    prompt_reason = recovery_validation_reason(
        payload.bundle_data,
        payload.prompt_content,
        payload.paths.runner_prompt_path,
    )
    if prompt_reason:
        return PromptArtifactValidation(
            valid=False,
            reason=prompt_reason,
        )

    return PromptArtifactValidation(
        valid=True,
        reason="All prompt artifacts are valid for recovery.",
        prompt_bundle_path=paths.prompt_bundle_path,
        runner_prompt_path=paths.runner_prompt_path,
        workspace_prompt_path=paths.workspace_prompt_path,
    )
