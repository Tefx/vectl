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


# @invar:allow shell_result: environment merge is true Shell boundary and public API returns dict
def build_opencode_launch_env(
    *,
    handoff_env: RunnerHandoffEnv,
    parent_env: dict[str, str] | None = None,
) -> dict[str, str]:
    """Build the complete process environment for OpenCode launch."""
    import os

    base = dict(parent_env or os.environ)
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


# @invar:allow shell_result: recovery validation reads prompt artifact files and preserves public validation DTO API
# @shell_complexity: ordered recovery checks preserve public failure reason precedence
def validate_prompt_artifacts_for_recovery(
    *,
    artifact_root: Path,
    run_id: str,
    workspace: Path,
) -> PromptArtifactValidation:
    """Validate that prompt artifacts exist and are valid for recovery fallback.

    Authority: docs/RFC-opencode-orchestration-runner.md section 10.2
    """
    paths = resolve_prompt_artifact_paths(
        artifact_root=artifact_root,
        run_id=run_id,
        workspace=workspace,
    )

    bundle_path = Path(paths.prompt_bundle_path)
    if not bundle_path.exists():
        return PromptArtifactValidation(
            valid=False,
            reason=f"prompt_bundle.json not found at {bundle_path}",
        )

    try:
        bundle_data = json.loads(bundle_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        return PromptArtifactValidation(
            valid=False,
            reason=f"prompt_bundle.json is not valid JSON: {exc}",
        )

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

    prompt_path = Path(paths.runner_prompt_path)
    if not prompt_path.exists():
        return PromptArtifactValidation(
            valid=False,
            reason=f"runner_prompt.md not found at {prompt_path}",
        )

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
