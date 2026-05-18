"""Pure prompt/materialization helpers with pinned runner-visible output.

Decision row: ``src/vectl/orchestration/prompt_materialization.py`` structural Core extraction.

>>> build_opencode_launch_argv_data("opencode", "/tmp/prompt.md", resume=False)[:3]
('opencode', 'run', '--format')

>>> build_runner_handoff_env_data("", "agent", "/tmp/work", "/tmp/prompt.md")  # doctest: +ELLIPSIS
Traceback (most recent call last):
...
deal.PreContractError: expected...
"""

import hashlib
import json
from collections.abc import Mapping, Sequence

from deal import post, pre

from vectl.orchestration.contracts import (
    OpenCodeLaunchConfig,
    PromptArtifactPaths,
    PromptBundle,
    RunnerHandoffEnv,
)

_RUNS_INPUT_DIR = "input"
_PROMPT_BUNDLE_FILENAME = "prompt_bundle.json"
_RUNNER_PROMPT_FILENAME = "runner_prompt.md"
_WORKSPACE_ORCH_DIR = ".vectl/orch"
_RUNNER_PROMPT_WORKSPACE_RELATIVE = ".vectl/orch/runner_prompt.md"
_OPENCODE_BOOTSTRAP_MESSAGE_START = (
    "Read the attached runner prompt file, execute the requested task in the "
    "current workspace, and then exit."
)
_OPENCODE_BOOTSTRAP_MESSAGE_RESUME = (
    "Continue this session by executing the attached runner prompt in the current workspace."
)
_RESOLUTION_REPORT_CONTRACT_LINES = (
    "Return ONLY one JSON object. Do not include Markdown, prose, or code fences.",
    "Schema:",
    "{",
    '  "status": "unblocked|waiting|operator_required|halt",',
    '  "summary": "non-empty human-readable summary",',
    '  "evidence_refs": ["evidence reference strings"],',
    '  "operator_message": null,',
    '  "planner_request": null | {',
    '    "reason": "why plan mutation is required",',
    '    "affected_steps": ["step ids"],',
    '    "evidence_refs": ["evidence refs"],',
    '    "constraints": ["bounded constraints"],',
    '    "mutations": [',
    '      {"action": "add-step|edit-step|remove-step|move-step|add-phase|edit-phase|skip-step|complete-phase", "arguments": {}, "reason": "why"}',
    "    ]",
    "  }",
    "}",
    "Use status=operator_required when automatic closure is unsafe or unverifiable.",
)
_STRUCTURED_REVIEW_CONTRACT_LINES = (
    "Return ONLY one JSON object. Do not include Markdown, prose, or code fences.",
    "Schema:",
    "{",
    '  "review_outcome": "pass|needs_fix|needs_replan|operator_required",',
    '  "summary": "non-empty human-readable summary",',
    '  "findings": ["finding strings"],',
    '  "evidence_refs": ["evidence reference strings"],',
    '  "planner_request": null | {',
    '    "reason": "required only when review_outcome is needs_replan",',
    '    "affected_steps": ["step ids"],',
    '    "evidence_refs": ["evidence refs"],',
    '    "constraints": ["bounded constraints"],',
    '    "mutations": [',
    '      {"action": "add-step|edit-step|remove-step|move-step|add-phase|edit-phase|skip-step|complete-phase", "arguments": {}, "reason": "why"}',
    "    ]",
    "  }",
    "}",
    "Use review_outcome=needs_replan when passing the gate requires adding, editing, or skipping plan work.",
)
_YAML_CONTRACT_LINES = (
    "Return YAML exactly:",
    "```yaml",
    'status: "SUCCESS|FAIL"',
    "evidence: |",
    "  <filled evidence with files changed and verification outputs>",
    'error: "<if FAIL, raw error; else empty>"',
    "```",
)


@pre(lambda path_value: bool(str(path_value).strip()))
@post(lambda result: bool(result.strip()))
def _resolve_path_text(path_value: object) -> str:
    """Return the historical resolved path text when the object supports it.

    >>> _resolve_path_text("/ws")
    '/ws'
    """
    resolver = getattr(path_value, "resolve", None)
    if callable(resolver):
        return str(resolver())
    return str(path_value)


@pre(lambda run_id, agent_name, workspace_path: bool(run_id.strip()) and bool(agent_name.strip()) and bool(workspace_path.strip()))
@post(lambda result: isinstance(result, Mapping) and {"bundle_path", "runner_prompt_path", "workspace_prompt_path"}.issubset(result.keys()))
def resolve_prompt_artifact_paths_data(
    run_id: str,
    agent_name: str,
    workspace_path: str,
) -> Mapping[str, str]:
    """Resolve stable prompt artifact path strings from already-known inputs.
    
    >>> resolve_prompt_artifact_paths_data("run-1", "/tmp/runs", "/tmp/work")
    {'bundle_path': '/tmp/runs/run-1/input/prompt_bundle.json', 'runner_prompt_path': '/tmp/runs/run-1/input/runner_prompt.md', 'workspace_prompt_path': '/tmp/work/.vectl/orch/runner_prompt.md', 'workspace_prompt_relative': '.vectl/orch/runner_prompt.md'}
    """
    artifact_input_dir = f"{agent_name.rstrip('/')}/{run_id}/{_RUNS_INPUT_DIR}"
    return {
        "bundle_path": f"{artifact_input_dir}/{_PROMPT_BUNDLE_FILENAME}",
        "runner_prompt_path": f"{artifact_input_dir}/{_RUNNER_PROMPT_FILENAME}",
        "workspace_prompt_path": (
            f"{workspace_path.rstrip('/')}/{_WORKSPACE_ORCH_DIR}/{_RUNNER_PROMPT_FILENAME}"
        ),
        "workspace_prompt_relative": _RUNNER_PROMPT_WORKSPACE_RELATIVE,
    }


@pre(lambda run_id, agent_name, workspace_path, prompt_path: bool(run_id.strip()) and bool(agent_name.strip()) and bool(workspace_path.strip()) and bool(prompt_path.strip()))
@post(lambda result: isinstance(result, Mapping) and {"VECTL_ORCH_RUN_ID", "VECTL_ORCH_AGENT_ID", "VECTL_ORCH_PROMPT_PATH"}.issubset(result.keys()))
def build_runner_handoff_env_data(
    run_id: str,
    agent_name: str,
    workspace_path: str,
    prompt_path: str,
) -> Mapping[str, str]:
    """Build runner handoff environment values without reading process env.
    
    >>> build_runner_handoff_env_data("run-1", "agent", "step", "/tmp/prompt.md")
    {'VECTL_ORCH_RUN_ID': 'run-1', 'VECTL_ORCH_STEP_ID': 'step', 'VECTL_ORCH_AGENT_ID': 'agent', 'VECTL_ORCH_PROMPT_PATH': '/tmp/prompt.md', 'VECTL_ORCH_PROMPT_BUNDLE_PATH': ''}
    """
    return {
        "VECTL_ORCH_RUN_ID": run_id,
        "VECTL_ORCH_STEP_ID": workspace_path,
        "VECTL_ORCH_AGENT_ID": agent_name,
        "VECTL_ORCH_PROMPT_PATH": prompt_path,
        "VECTL_ORCH_PROMPT_BUNDLE_PATH": "",
    }


@pre(
    lambda bundle_data: (
        isinstance(bundle_data, PromptBundle)
        or (
            isinstance(bundle_data, Mapping)
            and all(isinstance(key, str) and key for key in bundle_data.keys())
        )
    )
)
@post(lambda result: len(result) == 64 and all(char in "0123456789abcdef" for char in result))
def compute_prompt_bundle_sha256(bundle_data: Mapping[str, object] | PromptBundle) -> str:
    """Compute the canonical SHA-256 digest for prompt bundle data.
     
    >>> compute_prompt_bundle_sha256({"system_prompt": "s", "task_prompt": "t", "messages": ()}) == compute_prompt_bundle_sha256({"system_prompt": "s", "task_prompt": "t", "messages": ()})
    True
    >>> compute_prompt_bundle_sha256(PromptBundle(system_prompt="s", task_prompt="t", messages=()))
    '12dce344856d5b2d37cc4945201f16fc9512e61d917421d0575f301d68390ca9'
    """
    if isinstance(bundle_data, PromptBundle):
        bundle_data = {
            "system_prompt": bundle_data.system_prompt,
            "task_prompt": bundle_data.task_prompt,
            "messages": bundle_data.messages,
        }
    digest = hashlib.sha256()
    digest.update(str(bundle_data.get("system_prompt", "")).encode("utf-8"))
    digest.update(b"\x00")
    digest.update(str(bundle_data.get("task_prompt", "")).encode("utf-8"))
    raw_messages = bundle_data.get("messages", ())
    messages = raw_messages if isinstance(raw_messages, Sequence) and not isinstance(raw_messages, str) else ()
    for raw_message in messages:
        message = raw_message if isinstance(raw_message, Mapping) else {}
        digest.update(b"\x00")
        digest.update(str(message.get("role", "")).encode("utf-8"))
        digest.update(b":")
        digest.update(str(message.get("content", "")).encode("utf-8"))
    return digest.hexdigest()


@pre(
    lambda role_id,
    agent_id,
    system_prompt,
    task_prompt,
    messages,
    output_contract="freeform_evidence": bool(role_id.strip())
    and bool(agent_id.strip())
    and "\x00" not in system_prompt
    and "\x00" not in task_prompt
    and bool(output_contract.strip())
    and isinstance(messages, Sequence)
)
@post(lambda result: bool(result.strip()) and "Output Contract" in result)
def render_runner_prompt_md(
    role_id: str,
    agent_id: str,
    system_prompt: str,
    task_prompt: str,
    messages: Sequence[Mapping[str, str]],
    output_contract: str = "freeform_evidence",
) -> str:
    """Render runner-visible Markdown while preserving output contract sections.
     
    >>> render_runner_prompt_md(role_id="role", agent_id="agent", system_prompt="sys", task_prompt="task", messages=())[0:21]
    '# Runner Prompt: role'
    >>> "**[user]**: ctx" in render_runner_prompt_md(role_id="role", agent_id="agent", system_prompt="", task_prompt="task", messages=({"role": "user", "content": "ctx"},))
    True
    """
    parts: list[str] = []
    parts.append(f"# Runner Prompt: {role_id}")
    parts.append("")
    parts.append(f"- **Role**: {role_id}")
    parts.append(f"- **Agent**: {agent_id}")
    parts.append("")

    if system_prompt.strip():
        parts.append("## System Instructions")
        parts.append("")
        parts.append(system_prompt.strip())
        parts.append("")

    if task_prompt.strip():
        parts.append("## Task")
        parts.append("")
        parts.append(task_prompt.strip())
        parts.append("")

    if messages:
        parts.append("## Context")
        parts.append("")
        for msg in messages:
            role = msg.get("role", "unknown")
            content = msg.get("content", "")
            parts.append(f"**[{role}]**: {content}")
            parts.append("")

    parts.append("## Output Contract")
    parts.append("")
    parts.extend(output_contract_lines(output_contract, ()))
    parts.extend([
        "",
        "## Execution Rules",
        "",
        "- Complete ONLY the assigned task.",
        "- Do NOT start additional work or explore related issues.",
        "- When complete, STOP and return evidence.",
        "",
    ])
    return "\n".join(parts)


@pre(lambda output_format, required_fields: bool(output_format.strip()) and all(field.strip() for field in required_fields))
@post(lambda result: isinstance(result, tuple) and all(line.strip() for line in result))
def output_contract_lines(output_format: str, required_fields: Sequence[str]) -> tuple[str, ...]:
    """Return pinned output-contract instruction lines.
    
    >>> output_contract_lines("yaml", ("status",))[0]
    'Return YAML exactly:'
    """
    if output_format == "resolution_report":
        return _RESOLUTION_REPORT_CONTRACT_LINES
    if output_format == "structured_review_result":
        return _STRUCTURED_REVIEW_CONTRACT_LINES
    fields = tuple(required_fields) or ("status", "evidence", "error")
    if output_format == "json":
        return ("Return ONLY one JSON object.", f"Required fields: {json.dumps(fields)}")
    return _YAML_CONTRACT_LINES


@pre(lambda output_contract: bool(output_contract.strip()))
@post(lambda result: isinstance(result, list) and all(line.strip() for line in result))
def output_contract_line_list(output_contract: str) -> list[str]:
    """Return output contract instructions with legacy list return type.

    >>> output_contract_line_list("resolution_report")[0]
    'Return ONLY one JSON object. Do not include Markdown, prose, or code fences.'
    """
    return list(output_contract_lines(output_contract, ()))


@pre(lambda executable, prompt_path, resume=False, session_id=None: bool(executable.strip()) and bool(prompt_path.strip()) and (not resume or bool(str(session_id or "").strip())))
@post(lambda result: isinstance(result, tuple) and len(result) >= 2 and all(part.strip() for part in result))
def build_opencode_launch_argv_data(
    executable: str,
    prompt_path: str,
    resume: bool = False,
    session_id: str | None = None,
) -> tuple[str, ...]:
    """Build runner-visible OpenCode argv data for start or resume paths.
    
    >>> build_opencode_launch_argv_data("opencode", "/tmp/prompt.md")[-1]
    'Read the attached runner prompt file, execute the requested task in the current workspace, and then exit.'
    """
    argv = [executable, "run", "--format", "json", "--file", prompt_path, "--"]
    if resume:
        argv.extend(["--session", str(session_id)])
    argv.append(_OPENCODE_BOOTSTRAP_MESSAGE_RESUME if resume else _OPENCODE_BOOTSTRAP_MESSAGE_START)
    return tuple(argv)


@pre(
    lambda artifact_root, run_id, workspace: bool(str(artifact_root).strip())
    and bool(run_id.strip())
    and bool(str(workspace).strip())
)
@post(
    lambda result: result.prompt_bundle_path.endswith("input/prompt_bundle.json")
    and result.runner_prompt_path.endswith("input/runner_prompt.md")
    and result.workspace_prompt_relative == _RUNNER_PROMPT_WORKSPACE_RELATIVE
)
def resolve_prompt_artifact_paths(
    artifact_root: object,
    run_id: str,
    workspace: object,
) -> PromptArtifactPaths:
    """Resolve public prompt artifact paths without filesystem access.

    >>> resolve_prompt_artifact_paths(artifact_root="/runs", run_id="run-1", workspace="/ws").workspace_prompt_relative
    '.vectl/orch/runner_prompt.md'
    """
    data = resolve_prompt_artifact_paths_data(run_id, str(artifact_root), str(workspace))
    return PromptArtifactPaths(
        prompt_bundle_path=str(data["bundle_path"]),
        runner_prompt_path=str(data["runner_prompt_path"]),
        workspace_prompt_path=str(data["workspace_prompt_path"]),
        workspace_prompt_relative=str(data["workspace_prompt_relative"]),
    )


@pre(
    lambda run_id, step_id, agent_id, artifact_paths: bool(run_id.strip())
    and bool(step_id.strip())
    and bool(agent_id.strip())
    and bool(artifact_paths.workspace_prompt_path.strip())
    and bool(artifact_paths.prompt_bundle_path.strip())
)
@post(
    lambda result: result.VECTL_ORCH_PROMPT_PATH.strip() != ""
    and result.VECTL_ORCH_PROMPT_BUNDLE_PATH.strip() != ""
)
def build_runner_handoff_env(
    run_id: str,
    step_id: str,
    agent_id: str,
    artifact_paths: PromptArtifactPaths,
) -> RunnerHandoffEnv:
    """Build public runner handoff env DTO from resolved artifact paths.

    >>> paths = resolve_prompt_artifact_paths(artifact_root="/runs", run_id="run-1", workspace="/ws")
    >>> build_runner_handoff_env(run_id="run-1", step_id="s", agent_id="a", artifact_paths=paths).VECTL_ORCH_PROMPT_BUNDLE_PATH
    '/runs/run-1/input/prompt_bundle.json'
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


@pre(
    lambda role_id,
    agent_id,
    bundle,
    output_contract="freeform_evidence": bool(role_id.strip())
    and bool(agent_id.strip())
    and "\x00" not in bundle.system_prompt
    and "\x00" not in bundle.task_prompt
    and bool(output_contract.strip())
)
@post(lambda result: bool(result.strip()) and result.startswith("# Runner Prompt:"))
def render_runner_prompt_bundle_md(
    role_id: str,
    agent_id: str,
    bundle: PromptBundle,
    output_contract: str = "freeform_evidence",
) -> str:
    """Render a PromptBundle into the exact runner Markdown envelope.

    >>> bundle = PromptBundle(system_prompt="sys", task_prompt="task", messages=())
    >>> "## System Instructions" in render_runner_prompt_bundle_md(role_id="r", agent_id="a", bundle=bundle)
    True
    """
    return render_runner_prompt_md(
        role_id=role_id,
        agent_id=agent_id,
        system_prompt=bundle.system_prompt,
        task_prompt=bundle.task_prompt,
        messages=bundle.messages,
        output_contract=output_contract,
    )


@pre(
    lambda workspace, agent_id, session_id=None: bool(str(workspace).strip())
    and bool(agent_id.strip())
    and (session_id is None or bool(session_id.strip()))
)
@post(
    lambda result: "--agent" in result
    and "--file" in result
    and _RUNNER_PROMPT_WORKSPACE_RELATIVE in result
)
def build_default_opencode_launch_argv(
    workspace: object,
    agent_id: str,
    session_id: str | None,
) -> tuple[str, ...]:
    """Build the historical default OpenCode argv order.

    >>> build_default_opencode_launch_argv(workspace='/ws', agent_id='agent', session_id=None)[:6]
    ('opencode', 'run', '--format', 'json', '--dir', '/ws')
    >>> '--session' in build_default_opencode_launch_argv(workspace='/ws', agent_id='agent', session_id='sess')
    True
    """
    cfg = OpenCodeLaunchConfig()
    workspace_path = _resolve_path_text(workspace)
    result = ["opencode", "run", "--format", cfg.format_flag, cfg.dir_flag_key, workspace_path]
    if session_id is not None:
        result.extend([cfg.session_flag_key, session_id])
    result.extend([cfg.agent_flag_key, agent_id, "--file", cfg.file_flag, "--"])
    result.append(cfg.bootstrap_resume if session_id is not None else cfg.bootstrap_start)
    return tuple(result)


@pre(
    lambda workspace, agent_id, session_id=None, config=None: bool(str(workspace).strip())
    and bool(agent_id.strip())
    and (session_id is None or bool(session_id.strip()))
    and (
        config is None
        or (
            bool(config.format_flag.strip())
            and bool(config.dir_flag_key.strip())
            and bool(config.agent_flag_key.strip())
            and bool(config.session_flag_key.strip())
            and bool(config.file_flag.strip())
        )
    )
)
@post(lambda result: isinstance(result, tuple) and result[0:2] == ("opencode", "run"))
def build_opencode_launch_argv(
    workspace: object,
    agent_id: str,
    session_id: str | None = None,
    config: OpenCodeLaunchConfig | None = None,
) -> tuple[str, ...]:
    """Build OpenCode launch argv while preserving custom config compatibility.

    >>> build_opencode_launch_argv(workspace='/ws', agent_id='agent')[-1]
    'Read the attached runner prompt file, execute the requested task in the current workspace, and then exit.'
    """
    cfg = config or OpenCodeLaunchConfig()
    if cfg == OpenCodeLaunchConfig():
        return build_default_opencode_launch_argv(
            workspace=workspace,
            agent_id=agent_id,
            session_id=session_id,
        )

    argv: list[str] = [
        "opencode",
        "run",
        "--format",
        cfg.format_flag,
        cfg.dir_flag_key,
        _resolve_path_text(workspace),
    ]

    if session_id is not None:
        argv.extend([cfg.session_flag_key, session_id])

    argv.extend([cfg.agent_flag_key, agent_id, "--file", cfg.file_flag, "--"])
    argv.append(cfg.bootstrap_resume if session_id is not None else cfg.bootstrap_start)
    return tuple(argv)
