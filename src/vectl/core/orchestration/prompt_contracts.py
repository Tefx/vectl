"""Pure prompt/materialization helpers with pinned runner-visible output.

Decision row: ``src/vectl/orchestration/prompt_materialization.py`` structural Core extraction.

>>> build_opencode_launch_argv_data("opencode", "/tmp/prompt.md", resume=False)[:3]
('opencode', 'run', '--format')

>>> build_runner_handoff_env_data("", "agent", "/tmp/work", "/tmp/prompt.md")  # doctest: +ELLIPSIS
Traceback (most recent call last):
...
deal.PreContractError: expected...
"""

from collections.abc import Mapping, Sequence
import hashlib
import json

from deal import post, pre

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


@pre(lambda bundle_data: isinstance(bundle_data, Mapping) and all(isinstance(key, str) and key for key in bundle_data.keys()))
@post(lambda result: len(result) == 64 and all(char in "0123456789abcdef" for char in result))
def compute_prompt_bundle_sha256(bundle_data: Mapping[str, object]) -> str:
    """Compute the canonical SHA-256 digest for prompt bundle data.
    
    >>> compute_prompt_bundle_sha256({"system_prompt": "s", "task_prompt": "t", "messages": ()}) == compute_prompt_bundle_sha256({"system_prompt": "s", "task_prompt": "t", "messages": ()})
    True
    """
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


@pre(lambda agent_name, task_text, output_contract: bool(agent_name.strip()) and bool(task_text.strip()) and bool(output_contract.strip()))
@post(lambda result: bool(result.strip()) and "Output Contract" in result)
def render_runner_prompt_md(agent_name: str, task_text: str, output_contract: str) -> str:
    """Render runner-visible Markdown while preserving output contract sections.
    
    >>> "## Output Contract" in render_runner_prompt_md("agent", "task", "freeform_evidence")
    True
    """
    parts = [f"# Runner Prompt: {agent_name}", "", f"- **Role**: {agent_name}", f"- **Agent**: {agent_name}", ""]
    if task_text.strip():
        parts.extend(["## Task", "", task_text.strip(), ""])
    parts.extend(["## Output Contract", ""])
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
