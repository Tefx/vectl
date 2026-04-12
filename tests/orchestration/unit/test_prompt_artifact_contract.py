"""Prompt artifact contract tests.

Authority: docs/RFC-opencode-orchestration-runner.md sections 8, 8.5, 9.2

These tests prove that all required paths, env vars, and handoff fields
are pinned *before* runner implementation code is written. They are
contract-lock tests, not behavioral integration tests.

Step: opencode_runner_contracts.prompt-artifact-contract
Intent: contract_lock
"""

from __future__ import annotations

import json
from dataclasses import MISSING, fields, is_dataclass
from pathlib import Path
from typing import Literal, get_args, get_origin

import pytest

from vectl.orchestration.contracts import (
    OpenCodeLaunchConfig,
    PromptArtifactPaths,
    PromptBundle,
    RunnerHandoffEnv,
    _PROMPT_BUNDLE_FILENAME,
    _RUNNER_PROMPT_FILENAME,
    _RUNNER_PROMPT_WORKSPACE_RELATIVE,
    _RUNS_INPUT_DIR,
    _WORKSPACE_ORCH_DIR,
)
from vectl.orchestration.prompt_materialization import (
    build_opencode_launch_argv,
    build_opencode_launch_env,
    build_runner_handoff_env,
    compute_prompt_bundle_sha256,
    materialize_prompt_artifacts,
    resolve_prompt_artifact_paths,
    validate_prompt_artifacts_for_recovery,
    PromptArtifactValidation,
)


# ---------------------------------------------------------------------
# 1. PromptArtifactPaths contract tests
# ---------------------------------------------------------------------


class TestPromptArtifactPathsContract:
    """Prove PromptArtifactPaths pins the three required artifact locations.

    Authority: docs/RFC-opencode-orchestration-runner.md section 8
    """

    def test_prompt_artifact_paths_is_frozen_dataclass(self) -> None:
        """PromptArtifactPaths must be immutable."""
        assert is_dataclass(PromptArtifactPaths)
        with pytest.raises(AttributeError):
            paths = PromptArtifactPaths(
                prompt_bundle_path="/a",
                runner_prompt_path="/b",
                workspace_prompt_path="/c",
            )
            paths.prompt_bundle_path = "/changed"  # type: ignore[misc]

    def test_prompt_artifact_paths_fields_match_spec(self) -> None:
        """PromptArtifactPaths must have exactly the 4 documented fields.

        Authority: RFC §8.1, §8.2

        Fields:
            - prompt_bundle_path: authority bundle JSON
            - runner_prompt_path: authority runner prompt Markdown
            - workspace_prompt_path: workspace-copy runner prompt Markdown
            - workspace_prompt_relative: workspace-relative path string
        """
        expected = {
            "prompt_bundle_path",
            "runner_prompt_path",
            "workspace_prompt_path",
            "workspace_prompt_relative",
        }
        actual = {f.name for f in fields(PromptArtifactPaths)}
        assert actual == expected, (
            f"PromptArtifactPaths field mismatch. Expected: {expected}, Got: {actual}"
        )

    def test_authority_bundle_under_input_dir(self) -> None:
        """Authority prompt_bundle_path must end with input/prompt_bundle.json.

        Authority: RFC §8.1 — ``.vectl/runs/<run_id>/input/prompt_bundle.json``
        """
        paths = resolve_prompt_artifact_paths(
            artifact_root=Path("/runs"),
            run_id="run-123",
            workspace=Path("/ws"),
        )
        assert paths.prompt_bundle_path.endswith(f"{_RUNS_INPUT_DIR}/{_PROMPT_BUNDLE_FILENAME}"), (
            f"prompt_bundle_path={paths.prompt_bundle_path} does not end with {_RUNS_INPUT_DIR}/{_PROMPT_BUNDLE_FILENAME}"
        )

    def test_authority_runner_prompt_under_input_dir(self) -> None:
        """Authority runner_prompt_path must end with input/runner_prompt.md.

        Authority: RFC §8.1 — ``.vectl/runs/<run_id>/input/runner_prompt.md``
        """
        paths = resolve_prompt_artifact_paths(
            artifact_root=Path("/runs"),
            run_id="run-123",
            workspace=Path("/ws"),
        )
        assert paths.runner_prompt_path.endswith(f"{_RUNS_INPUT_DIR}/{_RUNNER_PROMPT_FILENAME}"), (
            f"runner_prompt_path={paths.runner_prompt_path} does not end with {_RUNS_INPUT_DIR}/{_RUNNER_PROMPT_FILENAME}"
        )

    def test_workspace_prompt_under_orch_dir(self) -> None:
        """Workspace prompt must be under .vectl/orch/runner_prompt.md.

        Authority: RFC §8.2 — ``<workspace>/.vectl/orch/runner_prompt.md``
        """
        paths = resolve_prompt_artifact_paths(
            artifact_root=Path("/runs"),
            run_id="run-123",
            workspace=Path("/ws"),
        )
        expected_suffix = f"{_WORKSPACE_ORCH_DIR}/{_RUNNER_PROMPT_FILENAME}"
        assert paths.workspace_prompt_path.endswith(expected_suffix), (
            f"workspace_prompt_path={paths.workspace_prompt_path} does not end with {expected_suffix}"
        )

    def test_workspace_prompt_relative_is_frozen_constant(self) -> None:
        """workspace_prompt_relative must always equal the frozen constant.

        Authority: RFC §8.2, §8.5
        """
        paths = resolve_prompt_artifact_paths(
            artifact_root=Path("/runs"),
            run_id="run-456",
            workspace=Path("/another/ws"),
        )
        assert paths.workspace_prompt_relative == ".vectl/orch/runner_prompt.md"

    def test_run_id_appears_in_authority_paths(self) -> None:
        """run_id must appear in both authority paths.

        Authority: RFC §8.1 — paths are under ``.vectl/runs/<run_id>/input/``
        """
        run_id = "run-unique-abc"
        paths = resolve_prompt_artifact_paths(
            artifact_root=Path("/runs"),
            run_id=run_id,
            workspace=Path("/ws"),
        )
        assert run_id in paths.prompt_bundle_path
        assert run_id in paths.runner_prompt_path


# ---------------------------------------------------------------------
# 2. RunnerHandoffEnv contract tests
# ---------------------------------------------------------------------


class TestRunnerHandoffEnvContract:
    """Prove RunnerHandoffEnv pins the 5 required VECTL_ORCH_* env vars.

    Authority: docs/RFC-opencode-orchestration-runner.md section 8.5
    """

    def test_runner_handoff_env_is_frozen_dataclass(self) -> None:
        """RunnerHandoffEnv must be immutable."""
        assert is_dataclass(RunnerHandoffEnv)
        with pytest.raises(AttributeError):
            env = RunnerHandoffEnv(
                VECTL_ORCH_RUN_ID="r1",
                VECTL_ORCH_STEP_ID="s1",
                VECTL_ORCH_AGENT_ID="a1",
                VECTL_ORCH_PROMPT_PATH="/p",
                VECTL_ORCH_PROMPT_BUNDLE_PATH="/b",
            )
            env.VECTL_ORCH_RUN_ID = "changed"  # type: ignore[misc]

    def test_runner_handoff_env_fields_match_spec(self) -> None:
        """RunnerHandoffEnv must have exactly the 5 documented fields.

        Authority: RFC §8.5 — the process environment must include at least:
            VECTL_ORCH_RUN_ID
            VECTL_ORCH_STEP_ID
            VECTL_ORCH_AGENT_ID
            VECTL_ORCH_PROMPT_PATH
            VECTL_ORCH_PROMPT_BUNDLE_PATH
        """
        expected = {
            "VECTL_ORCH_RUN_ID",
            "VECTL_ORCH_STEP_ID",
            "VECTL_ORCH_AGENT_ID",
            "VECTL_ORCH_PROMPT_PATH",
            "VECTL_ORCH_PROMPT_BUNDLE_PATH",
        }
        actual = {f.name for f in fields(RunnerHandoffEnv)}
        assert actual == expected, (
            f"RunnerHandoffEnv field mismatch. Expected: {expected}, Got: {actual}"
        )

    def test_runner_handoff_env_as_dict_produces_required_keys(self) -> None:
        """as_dict() must produce exactly the 5 VECTL_ORCH_* keys."""
        env = RunnerHandoffEnv(
            VECTL_ORCH_RUN_ID="run-1",
            VECTL_ORCH_STEP_ID="phase.step",
            VECTL_ORCH_AGENT_ID="python-senior-tacit",
            VECTL_ORCH_PROMPT_PATH="/ws/.vectl/orch/runner_prompt.md",
            VECTL_ORCH_PROMPT_BUNDLE_PATH="/runs/run-1/input/prompt_bundle.json",
        )
        d = env.as_dict()
        assert set(d.keys()) == {
            "VECTL_ORCH_RUN_ID",
            "VECTL_ORCH_STEP_ID",
            "VECTL_ORCH_AGENT_ID",
            "VECTL_ORCH_PROMPT_PATH",
            "VECTL_ORCH_PROMPT_BUNDLE_PATH",
        }

    def test_runner_handoff_env_as_dict_values_match_constructor(self) -> None:
        """as_dict() values must exactly match constructor arguments."""
        env = RunnerHandoffEnv(
            VECTL_ORCH_RUN_ID="run-2",
            VECTL_ORCH_STEP_ID="core.validate",
            VECTL_ORCH_AGENT_ID="executor",
            VECTL_ORCH_PROMPT_PATH="/path/to/prompt.md",
            VECTL_ORCH_PROMPT_BUNDLE_PATH="/path/to/bundle.json",
        )
        d = env.as_dict()
        assert d["VECTL_ORCH_RUN_ID"] == "run-2"
        assert d["VECTL_ORCH_STEP_ID"] == "core.validate"
        assert d["VECTL_ORCH_AGENT_ID"] == "executor"
        assert d["VECTL_ORCH_PROMPT_PATH"] == "/path/to/prompt.md"
        assert d["VECTL_ORCH_PROMPT_BUNDLE_PATH"] == "/path/to/bundle.json"

    def test_build_runner_handoff_env_uses_workspace_prompt_path(self) -> None:
        """build_runner_handoff_env must use workspace prompt path for VECTL_ORCH_PROMPT_PATH.

        Authority: RFC §8.5 — VECTL_ORCH_PROMPT_PATH points to the
        workspace-copy runner_prompt.md, not the authority copy.
        """
        paths = resolve_prompt_artifact_paths(
            artifact_root=Path("/runs"),
            run_id="run-1",
            workspace=Path("/ws"),
        )
        env = build_runner_handoff_env(
            run_id="run-1",
            step_id="phase.step",
            agent_id="agent-1",
            artifact_paths=paths,
        )
        assert env.VECTL_ORCH_PROMPT_PATH == paths.workspace_prompt_path
        assert env.VECTL_ORCH_PROMPT_BUNDLE_PATH == paths.prompt_bundle_path


# ---------------------------------------------------------------------
# 3. OpenCodeLaunchConfig contract tests
# ---------------------------------------------------------------------


class TestOpenCodeLaunchConfigContract:
    """Prove OpenCodeLaunchConfig pins the --file and launch command contract.

    Authority: docs/RFC-opencode-orchestration-runner.md sections 9.2, 9.3
    """

    def test_opencode_launch_config_is_frozen_dataclass(self) -> None:
        """OpenCodeLaunchConfig must be immutable."""
        assert is_dataclass(OpenCodeLaunchConfig)
        with pytest.raises(AttributeError):
            cfg = OpenCodeLaunchConfig()
            cfg.file_flag = "changed"  # type: ignore[misc]

    def test_opencode_launch_config_fields_match_spec(self) -> None:
        """OpenCodeLaunchConfig must have exactly the documented fields.

        Authority: RFC §9.2 — the one-shot launch contract includes:
            - file_flag (``--file .vectl/orch/runner_prompt.md``)
            - format_flag (``json``)
            - dir_flag_key (``--dir``)
            - agent_flag_key (``--agent``)
            - session_flag_key (``--session``)
            - bootstrap_start (start-mode message)
            - bootstrap_resume (resume-mode message)
        """
        expected = {
            "file_flag",
            "format_flag",
            "dir_flag_key",
            "agent_flag_key",
            "session_flag_key",
            "bootstrap_start",
            "bootstrap_resume",
        }
        actual = {f.name for f in fields(OpenCodeLaunchConfig)}
        assert actual == expected, (
            f"OpenCodeLaunchConfig field mismatch. Expected: {expected}, Got: {actual}"
        )

    def test_file_flag_defaults_to_workspace_runner_prompt(self) -> None:
        """file_flag must default to .vectl/orch/runner_prompt.md.

        Authority: RFC §9.2 — ``--file .vectl/orch/runner_prompt.md``
        """
        cfg = OpenCodeLaunchConfig()
        assert cfg.file_flag == ".vectl/orch/runner_prompt.md"

    def test_format_flag_defaults_to_json(self) -> None:
        """format_flag must default to 'json'.

        Authority: RFC §9.2 — ``--format json``
        """
        cfg = OpenCodeLaunchConfig()
        assert cfg.format_flag == "json"

    def test_bootstrap_start_matches_rfc(self) -> None:
        """bootstrap_start must match the frozen RFC text exactly.

        Authority: RFC §9.2
        """
        cfg = OpenCodeLaunchConfig()
        assert cfg.bootstrap_start == (
            "Read the attached runner prompt file, execute the requested "
            "task in the current workspace, and then exit."
        )

    def test_bootstrap_resume_matches_rfc(self) -> None:
        """bootstrap_resume must match the frozen RFC text exactly.

        Authority: RFC §9.3
        """
        cfg = OpenCodeLaunchConfig()
        assert cfg.bootstrap_resume == (
            "Continue this session by executing the attached runner prompt "
            "in the current workspace."
        )


# ---------------------------------------------------------------------
# 4. Path resolution contract tests
# ---------------------------------------------------------------------


class TestPathResolutionContract:
    """Prove resolve_prompt_artifact_paths produces correct deterministic paths."""

    def test_authority_bundle_path_structure(self) -> None:
        """Resolved bundle path must be <artifact_root>/<run_id>/input/prompt_bundle.json."""
        paths = resolve_prompt_artifact_paths(
            artifact_root=Path("/data/.vectl/runs"),
            run_id="run-abc",
            workspace=Path("/home/user/workspaces/ws-1"),
        )
        assert paths.prompt_bundle_path == "/data/.vectl/runs/run-abc/input/prompt_bundle.json"

    def test_authority_runner_prompt_path_structure(self) -> None:
        """Resolved runner prompt path must be <artifact_root>/<run_id>/input/runner_prompt.md."""
        paths = resolve_prompt_artifact_paths(
            artifact_root=Path("/data/.vectl/runs"),
            run_id="run-abc",
            workspace=Path("/home/user/workspaces/ws-1"),
        )
        assert paths.runner_prompt_path == "/data/.vectl/runs/run-abc/input/runner_prompt.md"

    def test_workspace_prompt_path_structure(self) -> None:
        """Resolved workspace prompt path must be <workspace>/.vectl/orch/runner_prompt.md."""
        paths = resolve_prompt_artifact_paths(
            artifact_root=Path("/data/.vectl/runs"),
            run_id="run-abc",
            workspace=Path("/home/user/workspaces/ws-1"),
        )
        assert (
            paths.workspace_prompt_path == "/home/user/workspaces/ws-1/.vectl/orch/runner_prompt.md"
        )

    def test_different_run_ids_produce_different_authority_paths(self) -> None:
        """Different run_id values must produce different authority artifact paths."""
        paths_a = resolve_prompt_artifact_paths(
            artifact_root=Path("/runs"),
            run_id="run-alpha",
            workspace=Path("/ws"),
        )
        paths_b = resolve_prompt_artifact_paths(
            artifact_root=Path("/runs"),
            run_id="run-beta",
            workspace=Path("/ws"),
        )
        assert paths_a.prompt_bundle_path != paths_b.prompt_bundle_path
        assert paths_a.runner_prompt_path != paths_b.runner_prompt_path

    def test_different_workspaces_produce_different_workspace_paths(self) -> None:
        """Different workspace paths must produce different workspace prompt paths."""
        paths_a = resolve_prompt_artifact_paths(
            artifact_root=Path("/runs"),
            run_id="run-1",
            workspace=Path("/ws-alpha"),
        )
        paths_b = resolve_prompt_artifact_paths(
            artifact_root=Path("/runs"),
            run_id="run-1",
            workspace=Path("/ws-beta"),
        )
        assert paths_a.workspace_prompt_path != paths_b.workspace_prompt_path


# ---------------------------------------------------------------------
# 5. Launch argv contract tests
# ---------------------------------------------------------------------


class TestLaunchArgvContract:
    """Prove build_opencode_launch_argv honors the frozen command contract.

    Authority: docs/RFC-opencode-orchestration-runner.md section 9.2, 9.3
    """

    def test_start_mode_argv_matches_rfc(self) -> None:
        """Start mode argv must match the frozen RFC contract exactly.

        Authority: RFC §9.2::

            opencode run --format json --dir <workspace> --agent <agent_id> \\
                --file .vectl/orch/runner_prompt.md \\
                "Read the attached runner prompt file..."
        """
        argv = build_opencode_launch_argv(
            workspace=Path("/ws"),
            agent_id="python-senior-tacit",
        )
        assert argv[0] == "opencode"
        assert argv[1] == "run"
        assert "--format" in argv
        assert "json" in argv
        assert "--dir" in argv
        assert "/ws" in argv
        assert "--agent" in argv
        assert "python-senior-tacit" in argv
        assert "--file" in argv
        assert ".vectl/orch/runner_prompt.md" in argv
        # No --session in start mode
        assert "--session" not in argv

    def test_resume_mode_argv_includes_session(self) -> None:
        """Resume mode argv must include --session with the session ID.

        Authority: RFC §9.3::

            opencode run --format json --dir <workspace> \\
                --session <session_id> --agent <agent_id> \\
                --file .vectl/orch/runner_prompt.md \\
                "Continue this session..."
        """
        argv = build_opencode_launch_argv(
            workspace=Path("/ws"),
            agent_id="python-senior-tacit",
            session_id="sess-123",
        )
        assert argv[0] == "opencode"
        assert "--session" in argv
        session_idx = argv.index("--session")
        assert argv[session_idx + 1] == "sess-123"
        assert "--file" in argv
        assert ".vectl/orch/runner_prompt.md" in argv

    def test_file_flag_appears_after_agent_flag(self) -> None:
        """--file must appear after --agent in the argv.

        Authority: RFC §9.2 — the --file flag follows --agent.
        """
        argv = build_opencode_launch_argv(
            workspace=Path("/ws"),
            agent_id="agent-1",
        )
        agent_idx = argv.index("--agent")
        file_idx = argv.index("--file")
        assert file_idx > agent_idx, (
            f"--file (index {file_idx}) must come after --agent (index {agent_idx})"
        )

    def test_continue_flag_is_not_used(self) -> None:
        """--continue must NOT appear in the argv.

        Authority: RFC §9.3 — '--continue' targets "last session" and is
        therefore non-deterministic for machine recovery. --session <id>
        is the required deterministic continuation surface.
        """
        for session_id in (None, "sess-1"):
            argv = build_opencode_launch_argv(
                workspace=Path("/ws"),
                agent_id="agent-1",
                session_id=session_id,
            )
            assert "--continue" not in argv, (
                f"--continue must not appear in argv (session_id={session_id!r})"
            )

    def test_start_uses_start_bootstrap_message(self) -> None:
        """Start mode must use the frozen start bootstrap message."""
        argv = build_opencode_launch_argv(
            workspace=Path("/ws"),
            agent_id="agent-1",
        )
        assert argv[-1] == OpenCodeLaunchConfig().bootstrap_start

    def test_resume_uses_resume_bootstrap_message(self) -> None:
        """Resume mode must use the frozen resume bootstrap message."""
        argv = build_opencode_launch_argv(
            workspace=Path("/ws"),
            agent_id="agent-1",
            session_id="sess-1",
        )
        assert argv[-1] == OpenCodeLaunchConfig().bootstrap_resume

    def test_launch_argv_is_a_tuple_of_strings(self) -> None:
        """Launch argv must be a tuple of strings (safe for subprocess.Popen)."""
        argv = build_opencode_launch_argv(
            workspace=Path("/ws"),
            agent_id="agent-1",
        )
        assert isinstance(argv, tuple)
        assert all(isinstance(arg, str) for arg in argv)


# ---------------------------------------------------------------------
# 6. Launch env contract tests
# ---------------------------------------------------------------------


class TestLaunchEnvContract:
    """Prove build_opencode_launch_env produces the required env vars."""

    def test_launch_env_includes_all_handoff_vars(self) -> None:
        """The launch env must include all 5 VECTL_ORCH_* variables."""
        paths = resolve_prompt_artifact_paths(
            artifact_root=Path("/runs"),
            run_id="run-1",
            workspace=Path("/ws"),
        )
        handoff = build_runner_handoff_env(
            run_id="run-1",
            step_id="phase.step",
            agent_id="agent-1",
            artifact_paths=paths,
        )
        env = build_opencode_launch_env(handoff_env=handoff, parent_env={})
        for key in (
            "VECTL_ORCH_RUN_ID",
            "VECTL_ORCH_STEP_ID",
            "VECTL_ORCH_AGENT_ID",
            "VECTL_ORCH_PROMPT_PATH",
            "VECTL_ORCH_PROMPT_BUNDLE_PATH",
        ):
            assert key in env, f"Missing required env var: {key}"

    def test_launch_env_merges_with_parent(self) -> None:
        """The launch env must preserve parent environment variables."""
        paths = resolve_prompt_artifact_paths(
            artifact_root=Path("/runs"),
            run_id="run-1",
            workspace=Path("/ws"),
        )
        handoff = build_runner_handoff_env(
            run_id="run-1",
            step_id="phase.step",
            agent_id="agent-1",
            artifact_paths=paths,
        )
        env = build_opencode_launch_env(
            handoff_env=handoff,
            parent_env={"PATH": "/usr/bin", "HOME": "/home/user"},
        )
        assert env["PATH"] == "/usr/bin"
        assert env["HOME"] == "/home/user"
        assert env["VECTL_ORCH_RUN_ID"] == "run-1"

    def test_launch_env_overrides_parent_conflicts(self) -> None:
        """Handoff vars must override parent env values for the same keys."""
        paths = resolve_prompt_artifact_paths(
            artifact_root=Path("/runs"),
            run_id="run-1",
            workspace=Path("/ws"),
        )
        handoff = build_runner_handoff_env(
            run_id="run-1",
            step_id="phase.step",
            agent_id="agent-1",
            artifact_paths=paths,
        )
        env = build_opencode_launch_env(
            handoff_env=handoff,
            parent_env={"VECTL_ORCH_RUN_ID": "stale-value"},
        )
        assert env["VECTL_ORCH_RUN_ID"] == "run-1"


# ---------------------------------------------------------------------
# 7. Prompt materialization contract tests
# ---------------------------------------------------------------------


class TestPromptMaterializationContract:
    """Prove materialize_prompt_artifacts writes the required disk artifacts."""

    def test_materialize_creates_prompt_bundle_json(self, tmp_path: Path) -> None:
        """materialize must create input/prompt_bundle.json with correct schema."""
        artifact_root = tmp_path / "runs"
        workspace = tmp_path / "ws"
        artifact_root.mkdir()
        workspace.mkdir()

        paths = resolve_prompt_artifact_paths(
            artifact_root=artifact_root,
            run_id="run-1",
            workspace=workspace,
        )
        bundle = PromptBundle(
            system_prompt="You are an expert.",
            task_prompt="Fix the bug.",
            messages=({"role": "user", "content": "context"},),
        )

        materialize_prompt_artifacts(
            bundle=bundle,
            artifact_paths=paths,
            role_id="python-senior-tacit",
            agent_id="python-senior-tacit",
            runner="opencode",
        )

        bundle_file = Path(paths.prompt_bundle_path)
        assert bundle_file.exists(), f"prompt_bundle.json not found at {bundle_file}"
        payload = json.loads(bundle_file.read_text())
        assert payload["role_id"] == "python-senior-tacit"
        assert payload["agent_id"] == "python-senior-tacit"
        assert payload["runner"] == "opencode"
        assert payload["system_prompt"] == "You are an expert."
        assert payload["task_prompt"] == "Fix the bug."
        assert len(payload["messages"]) == 1
        assert "prompt_bundle_sha256" in payload
        assert len(payload["prompt_bundle_sha256"]) == 64  # SHA-256 hex

    def test_materialize_creates_runner_prompt_md(self, tmp_path: Path) -> None:
        """materialize must create input/runner_prompt.md with content."""
        artifact_root = tmp_path / "runs"
        workspace = tmp_path / "ws"
        artifact_root.mkdir()
        workspace.mkdir()

        paths = resolve_prompt_artifact_paths(
            artifact_root=artifact_root,
            run_id="run-1",
            workspace=workspace,
        )
        bundle = PromptBundle(
            system_prompt="System instructions here.",
            task_prompt="Do the thing.",
            messages=(),
        )

        materialize_prompt_artifacts(
            bundle=bundle,
            artifact_paths=paths,
            role_id="agent-1",
            agent_id="agent-1",
            runner="opencode",
        )

        prompt_file = Path(paths.runner_prompt_path)
        assert prompt_file.exists(), f"runner_prompt.md not found at {prompt_file}"
        content = prompt_file.read_text()
        assert "agent-1" in content
        assert "System instructions here." in content
        assert "Do the thing." in content

    def test_materialize_creates_workspace_copy(self, tmp_path: Path) -> None:
        """materialize must create workspace-copy .vectl/orch/runner_prompt.md."""
        artifact_root = tmp_path / "runs"
        workspace = tmp_path / "ws"
        artifact_root.mkdir()
        workspace.mkdir()

        paths = resolve_prompt_artifact_paths(
            artifact_root=artifact_root,
            run_id="run-1",
            workspace=workspace,
        )
        bundle = PromptBundle(
            system_prompt="sys",
            task_prompt="task",
            messages=(),
        )

        materialize_prompt_artifacts(
            bundle=bundle,
            artifact_paths=paths,
            role_id="r1",
            agent_id="a1",
            runner="opencode",
        )

        ws_prompt = Path(paths.workspace_prompt_path)
        assert ws_prompt.exists(), f"Workspace prompt not found at {ws_prompt}"

    def test_workspace_copy_matches_authority_copy(self, tmp_path: Path) -> None:
        """Workspace copy content must match authority runner_prompt.md content."""
        artifact_root = tmp_path / "runs"
        workspace = tmp_path / "ws"
        artifact_root.mkdir()
        workspace.mkdir()

        paths = resolve_prompt_artifact_paths(
            artifact_root=artifact_root,
            run_id="run-1",
            workspace=workspace,
        )
        bundle = PromptBundle(
            system_prompt="sys",
            task_prompt="task",
            messages=(),
        )

        materialize_prompt_artifacts(
            bundle=bundle,
            artifact_paths=paths,
            role_id="r1",
            agent_id="a1",
            runner="opencode",
        )

        authority_content = Path(paths.runner_prompt_path).read_text()
        workspace_content = Path(paths.workspace_prompt_path).read_text()
        assert authority_content == workspace_content


# ---------------------------------------------------------------------
# 8. SHA-256 digest contract tests
# ---------------------------------------------------------------------


class TestPromptBundleSha256Contract:
    """Prove prompt_bundle_sha256 is deterministic and complete."""

    def test_same_bundle_produces_same_sha256(self) -> None:
        """Identical bundles must produce identical digests."""
        bundle = PromptBundle(
            system_prompt="sys",
            task_prompt="task",
            messages=({"role": "user", "content": "hi"},),
        )
        assert compute_prompt_bundle_sha256(bundle) == compute_prompt_bundle_sha256(bundle)

    def test_different_bundles_produce_different_sha256(self) -> None:
        """Different bundles must produce different digests."""
        bundle_a = PromptBundle(system_prompt="sys-a", task_prompt="task", messages=())
        bundle_b = PromptBundle(system_prompt="sys-b", task_prompt="task", messages=())
        assert compute_prompt_bundle_sha256(bundle_a) != compute_prompt_bundle_sha256(bundle_b)

    def test_sha256_is_64_hex_chars(self) -> None:
        """SHA-256 hex digest must be 64 characters."""
        bundle = PromptBundle(system_prompt="s", task_prompt="t", messages=())
        digest = compute_prompt_bundle_sha256(bundle)
        assert len(digest) == 64
        assert all(c in "0123456789abcdef" for c in digest)


# ---------------------------------------------------------------------
# 9. Frozen constants contract tests
# ---------------------------------------------------------------------


class TestFrozenConstantsContract:
    """Prove that the path-building constants are frozen and correct."""

    def test_runs_input_dir_is_input(self) -> None:
        """The input directory name must be 'input'."""
        assert _RUNS_INPUT_DIR == "input"

    def test_prompt_bundle_filename(self) -> None:
        """The bundle filename must be 'prompt_bundle.json'."""
        assert _PROMPT_BUNDLE_FILENAME == "prompt_bundle.json"

    def test_runner_prompt_filename(self) -> None:
        """The runner prompt filename must be 'runner_prompt.md'."""
        assert _RUNNER_PROMPT_FILENAME == "runner_prompt.md"

    def test_workspace_orch_dir(self) -> None:
        """The workspace orchestration dir must be '.vectl/orch'."""
        assert _WORKSPACE_ORCH_DIR == ".vectl/orch"

    def test_workspace_prompt_relative(self) -> None:
        """The workspace-relative prompt path must be '.vectl/orch/runner_prompt.md'."""
        assert _RUNNER_PROMPT_WORKSPACE_RELATIVE == ".vectl/orch/runner_prompt.md"


# ---------------------------------------------------------------------
# 10. End-to-end handoff integration contract
# ---------------------------------------------------------------------


class TestEndToEndHandoffContract:
    """Prove the complete handoff pipeline from request -> artifacts -> launch."""

    def test_full_handoff_pipeline_produces_consistent_artifacts_and_env(
        self, tmp_path: Path
    ) -> None:
        """Full pipeline: resolve paths -> materialize -> build env -> build argv.

        This test proves all contract elements are consistent end-to-end.
        """
        artifact_root = tmp_path / "runs"
        workspace = tmp_path / "ws"
        artifact_root.mkdir()
        workspace.mkdir()
        run_id = "run-e2e"
        step_id = "core.validate"
        agent_id = "python-senior-tacit"

        # Step 1: Resolve paths
        paths = resolve_prompt_artifact_paths(
            artifact_root=artifact_root,
            run_id=run_id,
            workspace=workspace,
        )

        # Step 2: Materialize artifacts
        bundle = PromptBundle(
            system_prompt="System instructions.",
            task_prompt="Validate the contracts.",
            messages=(),
        )
        materialize_prompt_artifacts(
            bundle=bundle,
            artifact_paths=paths,
            role_id=agent_id,
            agent_id=agent_id,
            runner="opencode",
        )

        # Step 3: Verify artifacts exist at pinned locations
        assert Path(paths.prompt_bundle_path).exists()
        assert Path(paths.runner_prompt_path).exists()
        assert Path(paths.workspace_prompt_path).exists()

        # Step 4: Build handoff env
        handoff = build_runner_handoff_env(
            run_id=run_id,
            step_id=step_id,
            agent_id=agent_id,
            artifact_paths=paths,
        )
        assert handoff.VECTL_ORCH_RUN_ID == run_id
        assert handoff.VECTL_ORCH_STEP_ID == step_id
        assert handoff.VECTL_ORCH_AGENT_ID == agent_id
        assert handoff.VECTL_ORCH_PROMPT_PATH == paths.workspace_prompt_path
        assert handoff.VECTL_ORCH_PROMPT_BUNDLE_PATH == paths.prompt_bundle_path

        # Step 5: Build launch argv
        argv = build_opencode_launch_argv(
            workspace=workspace,
            agent_id=agent_id,
        )
        assert "--file" in argv
        file_idx = argv.index("--file")
        assert argv[file_idx + 1] == ".vectl/orch/runner_prompt.md"

        # Step 6: Build launch env
        env = build_opencode_launch_env(
            handoff_env=handoff,
            parent_env={"PATH": "/usr/bin"},
        )
        assert env["VECTL_ORCH_RUN_ID"] == run_id
        assert env["PATH"] == "/usr/bin"

    def test_handoff_contract_fields_cover_all_rfc_requirements(self) -> None:
        """Exhaustive check: all RFC-required paths, env vars, and flags are pinned.

        Authority: docs/RFC-opencode-orchestration-runner.md sections 8, 8.5, 9.2

        This is the master assertion that the contract is complete.
        """
        # RFC §8.1: authority artifact locations
        paths = resolve_prompt_artifact_paths(
            artifact_root=Path(".vectl/runs"),
            run_id="run-x",
            workspace=Path("/ws"),
        )
        assert "input/prompt_bundle.json" in paths.prompt_bundle_path
        assert "input/runner_prompt.md" in paths.runner_prompt_path

        # RFC §8.2: workspace copy location
        assert ".vectl/orch/runner_prompt.md" in paths.workspace_prompt_path
        assert paths.workspace_prompt_relative == ".vectl/orch/runner_prompt.md"

        # RFC §8.5: required env vars
        env_fields = {f.name for f in fields(RunnerHandoffEnv)}
        required_env_vars = {
            "VECTL_ORCH_RUN_ID",
            "VECTL_ORCH_STEP_ID",
            "VECTL_ORCH_AGENT_ID",
            "VECTL_ORCH_PROMPT_PATH",
            "VECTL_ORCH_PROMPT_BUNDLE_PATH",
        }
        assert required_env_vars == env_fields

        # RFC §9.2: --file attachment and frozen command
        cfg = OpenCodeLaunchConfig()
        assert cfg.file_flag == ".vectl/orch/runner_prompt.md"
        assert "Read the attached runner prompt file" in cfg.bootstrap_start


# ---------------------------------------------------------------------
# 11. Recovery validation contract tests
# ---------------------------------------------------------------------


class TestPromptArtifactValidation:
    """Prove prompt artifact validation meets RFC §10.2 recovery criteria.

    Authority: docs/RFC-opencode-orchestration-runner.md section 10.2

    Minimum prompt artifact validity criteria before fresh relaunch:
    1. prompt_bundle.json exists and is valid JSON
    2. prompt_bundle.json has non-empty required fields
    3. runner_prompt.md exists and is non-empty
    """

    def test_valid_artifacts_pass_validation(self, tmp_path: Path) -> None:
        """Fully valid artifacts must pass validation."""
        artifact_root = tmp_path / "runs"
        workspace = tmp_path / "ws"
        artifact_root.mkdir()
        workspace.mkdir()

        paths = resolve_prompt_artifact_paths(
            artifact_root=artifact_root,
            run_id="run-recover",
            workspace=workspace,
        )
        bundle = PromptBundle(
            system_prompt="System instructions.",
            task_prompt="Execute the task.",
            messages=({"role": "user", "content": "context"},),
        )
        materialize_prompt_artifacts(
            bundle=bundle,
            artifact_paths=paths,
            role_id="python-senior-tacit",
            agent_id="python-senior-tacit",
            runner="opencode",
        )

        result = validate_prompt_artifacts_for_recovery(
            artifact_root=artifact_root,
            run_id="run-recover",
            workspace=workspace,
        )
        assert result.valid is True
        assert "prompt_bundle.json" in result.prompt_bundle_path
        assert "runner_prompt.md" in result.runner_prompt_path
        assert ".vectl/orch" in result.workspace_prompt_path

    def test_missing_bundle_fails_validation(self, tmp_path: Path) -> None:
        """Missing prompt_bundle.json must fail validation with clear reason."""
        artifact_root = tmp_path / "runs"
        workspace = tmp_path / "ws"
        artifact_root.mkdir()
        workspace.mkdir()

        result = validate_prompt_artifacts_for_recovery(
            artifact_root=artifact_root,
            run_id="run-missing-bundle",
            workspace=workspace,
        )
        assert result.valid is False
        assert "prompt_bundle.json" in result.reason
        assert "not found" in result.reason

    def test_invalid_json_bundle_fails_validation(self, tmp_path: Path) -> None:
        """Corrupt JSON in prompt_bundle.json must fail validation."""
        artifact_root = tmp_path / "runs"
        workspace = tmp_path / "ws"
        artifact_root.mkdir()
        workspace.mkdir()

        run_dir = artifact_root / "run-corrupt" / "input"
        run_dir.mkdir(parents=True)
        (run_dir / "prompt_bundle.json").write_text("NOT VALID JSON{{{")

        result = validate_prompt_artifacts_for_recovery(
            artifact_root=artifact_root,
            run_id="run-corrupt",
            workspace=workspace,
        )
        assert result.valid is False
        assert "not valid JSON" in result.reason

    def test_missing_required_field_fails_validation(self, tmp_path: Path) -> None:
        """Missing required field in prompt_bundle.json must fail validation."""
        artifact_root = tmp_path / "runs"
        workspace = tmp_path / "ws"
        artifact_root.mkdir()
        workspace.mkdir()

        run_dir = artifact_root / "run-incomplete" / "input"
        run_dir.mkdir(parents=True)

        # Missing role_id, agent_id, runner, etc.
        incomplete_payload = {
            "system_prompt": "sys",
            "task_prompt": "task",
            "prompt_bundle_sha256": "abc",
        }
        (run_dir / "prompt_bundle.json").write_text(json.dumps(incomplete_payload, indent=2))
        (run_dir / "runner_prompt.md").write_text("# Task\nDo it.")

        result = validate_prompt_artifacts_for_recovery(
            artifact_root=artifact_root,
            run_id="run-incomplete",
            workspace=workspace,
        )
        assert result.valid is False
        assert "role_id" in result.reason

    def test_empty_field_fails_validation(self, tmp_path: Path) -> None:
        """Empty required field in prompt_bundle.json must fail validation."""
        artifact_root = tmp_path / "runs"
        workspace = tmp_path / "ws"
        artifact_root.mkdir()
        workspace.mkdir()

        run_dir = artifact_root / "run-empty" / "input"
        run_dir.mkdir(parents=True)

        payload_with_empty = {
            "role_id": "python-senior-tacit",
            "agent_id": "python-senior-tacit",
            "runner": "opencode",
            "system_prompt": "",
            "task_prompt": "task",
            "prompt_bundle_sha256": "abc",
        }
        (run_dir / "prompt_bundle.json").write_text(json.dumps(payload_with_empty, indent=2))
        (run_dir / "runner_prompt.md").write_text("# Task\nDo it.")

        result = validate_prompt_artifacts_for_recovery(
            artifact_root=artifact_root,
            run_id="run-empty",
            workspace=workspace,
        )
        assert result.valid is False
        assert "system_prompt" in result.reason

    def test_missing_runner_prompt_fails_validation(self, tmp_path: Path) -> None:
        """Missing runner_prompt.md must fail validation."""
        artifact_root = tmp_path / "runs"
        workspace = tmp_path / "ws"
        artifact_root.mkdir()
        workspace.mkdir()

        run_dir = artifact_root / "run-no-prompt" / "input"
        run_dir.mkdir(parents=True)

        valid_payload = {
            "role_id": "python-senior-tacit",
            "agent_id": "python-senior-tacit",
            "runner": "opencode",
            "system_prompt": "sys",
            "task_prompt": "task",
            "prompt_bundle_sha256": "abc",
            "messages": [],
        }
        (run_dir / "prompt_bundle.json").write_text(json.dumps(valid_payload, indent=2))
        # Do NOT write runner_prompt.md

        result = validate_prompt_artifacts_for_recovery(
            artifact_root=artifact_root,
            run_id="run-no-prompt",
            workspace=workspace,
        )
        assert result.valid is False
        assert "runner_prompt.md" in result.reason

    def test_empty_runner_prompt_fails_validation(self, tmp_path: Path) -> None:
        """Empty runner_prompt.md must fail validation."""
        artifact_root = tmp_path / "runs"
        workspace = tmp_path / "ws"
        artifact_root.mkdir()
        workspace.mkdir()

        run_dir = artifact_root / "run-blank-prompt" / "input"
        run_dir.mkdir(parents=True)

        valid_payload = {
            "role_id": "python-senior-tacit",
            "agent_id": "python-senior-tacit",
            "runner": "opencode",
            "system_prompt": "sys",
            "task_prompt": "task",
            "prompt_bundle_sha256": "abc",
            "messages": [],
        }
        (run_dir / "prompt_bundle.json").write_text(json.dumps(valid_payload, indent=2))
        (run_dir / "runner_prompt.md").write_text("   \n  \n  ")

        result = validate_prompt_artifacts_for_recovery(
            artifact_root=artifact_root,
            run_id="run-blank-prompt",
            workspace=workspace,
        )
        assert result.valid is False
        assert "empty" in result.reason

    def test_materialized_artifacts_pass_validation(self, tmp_path: Path) -> None:
        """Artifacts produced by materialize_prompt_artifacts must pass validation.

        This is a round-trip test: materialize then validate.
        Authority: RFC §10.2 — the same artifacts written at launch must
        be usable for recovery fallback.
        """
        artifact_root = tmp_path / "runs"
        workspace = tmp_path / "ws"
        artifact_root.mkdir()
        workspace.mkdir()

        paths = resolve_prompt_artifact_paths(
            artifact_root=artifact_root,
            run_id="run-roundtrip",
            workspace=workspace,
        )
        bundle = PromptBundle(
            system_prompt="You are a senior Python engineer.",
            task_prompt="Implement the prompt materialization pipeline.",
            messages=({"role": "user", "content": "Additional context"},),
        )
        materialize_prompt_artifacts(
            bundle=bundle,
            artifact_paths=paths,
            role_id="python-senior-tacit",
            agent_id="python-senior-tacit",
            runner="opencode",
        )

        result = validate_prompt_artifacts_for_recovery(
            artifact_root=artifact_root,
            run_id="run-roundtrip",
            workspace=workspace,
        )
        assert result.valid is True
        assert result.prompt_bundle_path == paths.prompt_bundle_path
        assert result.runner_prompt_path == paths.runner_prompt_path
        assert result.workspace_prompt_path == paths.workspace_prompt_path

    def test_validation_result_is_frozen_dataclass(self) -> None:
        """PromptArtifactValidation must be frozen."""
        from dataclasses import is_dataclass

        assert is_dataclass(PromptArtifactValidation)
        with pytest.raises(AttributeError):
            result = PromptArtifactValidation(
                valid=True,
                reason="test",
            )
            result.valid = False  # type: ignore[misc]

    def test_validation_result_fields_match_spec(self) -> None:
        """PromptArtifactValidation must have the documented fields.

        Authority: RFC §10.2
        """
        from dataclasses import fields as dc_fields

        expected = {
            "valid",
            "reason",
            "prompt_bundle_path",
            "runner_prompt_path",
            "workspace_prompt_path",
        }
        actual = {f.name for f in dc_fields(PromptArtifactValidation)}
        assert actual == expected, (
            f"PromptArtifactValidation field mismatch. Expected: {expected}, Got: {actual}"
        )


# ---------------------------------------------------------------------
# 12. Integration: materialization-to-recovery round-trip
# ---------------------------------------------------------------------


class TestMaterializationRecoveryIntegration:
    """Prove the complete materialization pipeline produces artifacts
    that satisfy recovery validation and handoff requirements.

    Authority: RFC §8, §10.2

    This section tests the end-to-end pipeline from DispatchSpec
    through artifact writing, validation, and env construction.
    """

    def test_materialize_then_validate_then_env(self, tmp_path: Path) -> None:
        """Full pipeline: materialize -> validate -> build handoff env.

        This test proves that the same materialized artifacts that satisfy
        launch requirements also satisfy recovery validation, and that
        handoff env vars point to the correct artifact paths.
        """
        artifact_root = tmp_path / "runs"
        workspace = tmp_path / "ws"
        artifact_root.mkdir()
        workspace.mkdir()

        run_id = "run-integration-1"
        step_id = "core.validate"
        agent_id = "python-senior-tacit"

        # Step 1: Resolve paths
        paths = resolve_prompt_artifact_paths(
            artifact_root=artifact_root,
            run_id=run_id,
            workspace=workspace,
        )

        # Step 2: Materialize artifacts
        bundle = PromptBundle(
            system_prompt="System instructions.",
            task_prompt="Validate the contracts.",
            messages=(),
        )
        materialize_prompt_artifacts(
            bundle=bundle,
            artifact_paths=paths,
            role_id=agent_id,
            agent_id=agent_id,
            runner="opencode",
        )

        # Step 3: Validate artifacts for recovery (RFC §10.2)
        validation = validate_prompt_artifacts_for_recovery(
            artifact_root=artifact_root,
            run_id=run_id,
            workspace=workspace,
        )
        assert validation.valid is True

        # Step 4: Build handoff env (RFC §8.5)
        handoff = build_runner_handoff_env(
            run_id=run_id,
            step_id=step_id,
            agent_id=agent_id,
            artifact_paths=paths,
        )
        assert handoff.VECTL_ORCH_RUN_ID == run_id
        assert handoff.VECTL_ORCH_STEP_ID == step_id
        assert handoff.VECTL_ORCH_AGENT_ID == agent_id
        assert handoff.VECTL_ORCH_PROMPT_PATH == paths.workspace_prompt_path
        assert handoff.VECTL_ORCH_PROMPT_BUNDLE_PATH == paths.prompt_bundle_path

        # Step 5: Verify the handoff env dict is usable for subprocess
        env_dict = handoff.as_dict()
        assert env_dict["VECTL_ORCH_PROMPT_PATH"] == paths.workspace_prompt_path
        assert Path(env_dict["VECTL_ORCH_PROMPT_BUNDLE_PATH"]).exists()
        assert Path(env_dict["VECTL_ORCH_PROMPT_PATH"]).exists()

    def test_workspace_copy_exists_for_runner_startup(self, tmp_path: Path) -> None:
        """The workspace copy must be readable at the expected path for runner startup.

        Authority: RFC §8.2, §8.5

        The runner reads the prompt from the workspace copy at
        .vectl/orch/runner_prompt.md (relative to workspace root).
        """
        artifact_root = tmp_path / "runs"
        workspace = tmp_path / "ws"
        artifact_root.mkdir()
        workspace.mkdir()

        paths = resolve_prompt_artifact_paths(
            artifact_root=artifact_root,
            run_id="run-ws-copy",
            workspace=workspace,
        )
        bundle = PromptBundle(
            system_prompt="System instructions.",
            task_prompt="Execute the task.",
            messages=(),
        )
        materialize_prompt_artifacts(
            bundle=bundle,
            artifact_paths=paths,
            role_id="python-senior-tacit",
            agent_id="python-senior-tacit",
            runner="opencode",
        )

        # Workspace copy must exist and be readable
        ws_prompt = Path(paths.workspace_prompt_path)
        assert ws_prompt.exists(), f"Workspace prompt not found at {ws_prompt}"

        # The workspace-relative path must match RFC §8.2
        assert paths.workspace_prompt_relative == ".vectl/orch/runner_prompt.md"

        # Content must match authority copy
        authority_content = Path(paths.runner_prompt_path).read_text()
        workspace_content = ws_prompt.read_text()
        assert authority_content == workspace_content

    def test_authority_bundle_contains_required_recovery_fields(self, tmp_path: Path) -> None:
        """The materialized prompt_bundle.json must contain all required
        fields for recovery validation (RFC §10.2).

        Authority: RFC §10.2 — prompt_bundle.json must contain non-empty
        values for role_id, agent_id, runner, system_prompt, task_prompt,
        prompt_bundle_sha256.
        """
        artifact_root = tmp_path / "runs"
        workspace = tmp_path / "ws"
        artifact_root.mkdir()
        workspace.mkdir()

        paths = resolve_prompt_artifact_paths(
            artifact_root=artifact_root,
            run_id="run-bundle-fields",
            workspace=workspace,
        )
        bundle = PromptBundle(
            system_prompt="System instructions.",
            task_prompt="Execute the task.",
            messages=({"role": "user", "content": "context"},),
        )
        materialize_prompt_artifacts(
            bundle=bundle,
            artifact_paths=paths,
            role_id="python-senior-tacit",
            agent_id="python-senior-tacit",
            runner="opencode",
        )

        bundle_data = json.loads(Path(paths.prompt_bundle_path).read_text())
        # RFC §10.2 required fields
        assert bundle_data["role_id"] == "python-senior-tacit"
        assert bundle_data["agent_id"] == "python-senior-tacit"
        assert bundle_data["runner"] == "opencode"
        assert bundle_data["system_prompt"] == "System instructions."
        assert bundle_data["task_prompt"] == "Execute the task."
        assert len(bundle_data["prompt_bundle_sha256"]) == 64
