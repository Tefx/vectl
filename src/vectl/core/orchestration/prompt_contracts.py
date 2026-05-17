"""Contracts for pure prompt/materialization helper extraction.

Decision row: ``src/vectl/orchestration/prompt_materialization.py`` structural Core extraction.

>>> build_opencode_launch_argv_data("opencode", "/tmp/prompt.md", resume=False)
Traceback (most recent call last):
...
NotImplementedError: contract stub: build_opencode_launch_argv_data

>>> build_runner_handoff_env_data("", "agent", "/tmp/work", "/tmp/prompt.md")  # doctest: +ELLIPSIS
Traceback (most recent call last):
...
deal.PreContractError: expected...
"""

from collections.abc import Mapping, Sequence

from deal import post, pre


@pre(lambda run_id, agent_name, workspace_path: bool(run_id.strip()) and bool(agent_name.strip()) and bool(workspace_path.strip()))
@post(lambda result: isinstance(result, Mapping) and {"bundle_path", "runner_prompt_path", "workspace_prompt_path"}.issubset(result.keys()))
def resolve_prompt_artifact_paths_data(
    run_id: str,
    agent_name: str,
    workspace_path: str,
) -> Mapping[str, str]:
    """Resolve stable prompt artifact path strings from already-known inputs.
    
    >>> resolve_prompt_artifact_paths_data("run-1", "agent", "/tmp/work")
    Traceback (most recent call last):
    ...
    NotImplementedError: contract stub: resolve_prompt_artifact_paths_data
    """
    raise NotImplementedError("contract stub: resolve_prompt_artifact_paths_data")


@pre(lambda run_id, agent_name, workspace_path, prompt_path: bool(run_id.strip()) and bool(agent_name.strip()) and bool(workspace_path.strip()) and bool(prompt_path.strip()))
@post(lambda result: isinstance(result, Mapping) and {"VECTL_RUN_ID", "VECTL_AGENT", "VECTL_PROMPT_PATH"}.issubset(result.keys()))
def build_runner_handoff_env_data(
    run_id: str,
    agent_name: str,
    workspace_path: str,
    prompt_path: str,
) -> Mapping[str, str]:
    """Build runner handoff environment values without reading process env.
    
    >>> build_runner_handoff_env_data("run-1", "agent", "/tmp/work", "/tmp/prompt.md")
    Traceback (most recent call last):
    ...
    NotImplementedError: contract stub: build_runner_handoff_env_data
    """
    raise NotImplementedError("contract stub: build_runner_handoff_env_data")


@pre(lambda bundle_data: isinstance(bundle_data, Mapping) and all(isinstance(key, str) and key for key in bundle_data.keys()))
@post(lambda result: len(result) == 64 and all(char in "0123456789abcdef" for char in result))
def compute_prompt_bundle_sha256(bundle_data: Mapping[str, object]) -> str:
    """Compute the canonical SHA-256 digest for prompt bundle data.
    
    >>> compute_prompt_bundle_sha256({"run_id": "run-1"})
    Traceback (most recent call last):
    ...
    NotImplementedError: contract stub: compute_prompt_bundle_sha256
    """
    raise NotImplementedError("contract stub: compute_prompt_bundle_sha256")


@pre(lambda agent_name, task_text, output_contract: bool(agent_name.strip()) and bool(task_text.strip()) and bool(output_contract.strip()))
@post(lambda result: bool(result.strip()) and "Output Contract" in result)
def render_runner_prompt_md(agent_name: str, task_text: str, output_contract: str) -> str:
    """Render runner-visible Markdown while preserving output contract sections.
    
    >>> render_runner_prompt_md("agent", "task", "Output Contract")
    Traceback (most recent call last):
    ...
    NotImplementedError: contract stub: render_runner_prompt_md
    """
    raise NotImplementedError("contract stub: render_runner_prompt_md")


@pre(lambda output_format, required_fields: bool(output_format.strip()) and all(field.strip() for field in required_fields))
@post(lambda result: isinstance(result, tuple) and all(line.strip() for line in result))
def output_contract_lines(output_format: str, required_fields: Sequence[str]) -> tuple[str, ...]:
    """Return pinned output-contract instruction lines.
    
    >>> output_contract_lines("yaml", ("status",))
    Traceback (most recent call last):
    ...
    NotImplementedError: contract stub: output_contract_lines
    """
    raise NotImplementedError("contract stub: output_contract_lines")


@pre(lambda executable, prompt_path, resume=False, session_id=None: bool(executable.strip()) and bool(prompt_path.strip()) and (not resume or bool(str(session_id or "").strip())))
@post(lambda result: isinstance(result, tuple) and len(result) >= 2 and all(part.strip() for part in result))
def build_opencode_launch_argv_data(
    executable: str,
    prompt_path: str,
    resume: bool = False,
    session_id: str | None = None,
) -> tuple[str, ...]:
    """Build runner-visible OpenCode argv data for start or resume paths.
    
    >>> build_opencode_launch_argv_data("opencode", "/tmp/prompt.md")
    Traceback (most recent call last):
    ...
    NotImplementedError: contract stub: build_opencode_launch_argv_data
    """
    raise NotImplementedError("contract stub: build_opencode_launch_argv_data")
