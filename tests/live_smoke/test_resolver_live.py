"""Live smoke tests for resolver -> gateway -> runtime -> live runner path.

These tests prove the resolver live path reaches a real runner after gateway
authorization, rather than stopping at seam-only unit coverage.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import replace
from pathlib import Path

import pytest

from tests.live_smoke.helpers import (
    LiveRunnerPreflight,
    build_model_override_args,
    codex_live,
    get_effective_model,
    live_runner,
    opencode_live,
    run_subprocess,
)
from vectl.io import save_plan
from vectl.models import Phase, Plan, Step
from vectl.orch_app import AppConfig, build_orchestration_app
from vectl.orchestration.config import OrchestrationConfig, default_role_profiles
from vectl.orchestration.contracts import (
    CoreSnapshot,
    ResolutionCase,
    RoleProfile,
    RosterSnapshot,
    RuntimeSnapshot,
)
from vectl.orchestration.runner_registry import RunnerRegistry
from vectl.orchestration.runners import (
    RunnerCapabilities,
    RunnerHandle,
    RunnerLaunchError,
    RunnerLaunchResult,
    RunnerPollResult,
)


def _write_plan(plan_path: Path) -> None:
    plan = Plan(
        project="resolver-live",
        phases=[
            Phase(
                id="core",
                name="Core",
                steps=[
                    Step(
                        id="core.ready",
                        name="Ready",
                        description="Ready step",
                    )
                ],
            )
        ],
    )
    save_plan(plan, plan_path)


@pytest.fixture
def temp_git_repo(tmp_path: Path) -> Path:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    subprocess.run(["git", "init"], cwd=repo_root, capture_output=True, check=True)
    subprocess.run(
        ["git", "config", "user.email", "test@test.com"],
        cwd=repo_root,
        capture_output=True,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=repo_root,
        capture_output=True,
        check=True,
    )
    (repo_root / "README.md").write_text("# Resolver Live Test Repo\n")
    subprocess.run(["git", "add", "README.md"], cwd=repo_root, capture_output=True, check=True)
    subprocess.run(
        ["git", "commit", "-m", "Initial commit"],
        cwd=repo_root,
        capture_output=True,
        check=True,
    )
    return repo_root


def _core_snapshot() -> CoreSnapshot:
    return CoreSnapshot(
        plan_complete=False,
        claimable_step_ids=(),
        in_progress_step_ids=(),
        blocked_step_ids=("core.ready",),
        unresolved_reasons=(),
    )


def _roster_snapshot() -> RosterSnapshot:
    return RosterSnapshot(
        available_agents=(),
        working_agents=(),
        reusable_sessions=(),
        exhausted_roles=(),
    )


def _runtime_snapshot() -> RuntimeSnapshot:
    return RuntimeSnapshot(active_workspaces=(), active_executions=(), stalled_executions=())


def _parse_live_runner_output(runner_name: str, stdout: str) -> dict[str, object]:
    lines = stdout.strip().split("\n") if stdout else []
    text_parts: list[str] = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict):
            continue
        if runner_name == "codex" and event.get("type") == "item.completed":
            item = event.get("item", {})
            if isinstance(item, dict):
                text = item.get("text")
                if isinstance(text, str) and text.strip():
                    text_parts.append(text.strip())
                output = item.get("output", {})
                if isinstance(output, dict):
                    nested_text = output.get("text")
                    if isinstance(nested_text, str) and nested_text.strip():
                        text_parts.append(nested_text.strip())
        if runner_name == "opencode" and event.get("type") == "text":
            part = event.get("part", {})
            if isinstance(part, dict):
                text = part.get("text")
                if isinstance(text, str) and text.strip():
                    text_parts.append(text.strip())
    if not text_parts:
        raise ValueError(f"no parsable output from {runner_name}: {stdout[:300]}")
    return json.loads(text_parts[-1])


class _LiveResolverRunner:
    def __init__(self, *, runner_name: str, workspace: Path) -> None:
        self.runner_name = runner_name
        self.workspace = workspace
        self._results: dict[str, RunnerPollResult] = {}
        self._launch_count = 0

    @property
    def launch_count(self) -> int:
        return self._launch_count

    def capabilities(self) -> RunnerCapabilities:
        return RunnerCapabilities(
            runner_id=self.runner_name,
            supports_resume=False,
            supports_cancel=True,
            supports_streaming=False,
        )

    def launch(self, request, workspace: Path) -> RunnerLaunchResult:
        self._launch_count += 1
        prompt = (
            "You are a blocked-case resolver test agent. Respond with ONLY a JSON object "
            "for a ResolutionReport and nothing else. Use exactly: "
            '{"status":"unblocked","summary":"resolved through live runner",'
            '"evidence_refs":["resolver://live-runner"]}'
        )
        model = get_effective_model(self.runner_name)
        model_args = build_model_override_args(self.runner_name, model)
        if self.runner_name == "codex":
            command = [
                "codex",
                "exec",
                "--json",
                "--dangerously-bypass-approvals-and-sandbox",
                "-C",
                str(workspace.resolve()),
                *model_args,
                "-",
            ]
            result = run_subprocess(
                command,
                cwd=str(workspace),
                input_str=prompt,
                timeout=60.0,
                runner_name="codex",
                scenario="resolver_live",
            )
        else:
            command = [
                "opencode",
                "run",
                "--format",
                "json",
                *model_args,
                "--",
                prompt,
            ]
            result = run_subprocess(
                command,
                cwd=str(workspace),
                input_str=None,
                timeout=60.0,
                runner_name="opencode",
                scenario="resolver_live",
            )

        if result.returncode != 0:
            raise RunnerLaunchError(
                runner_id=self.runner_name,
                reason="resource_unavailable",
                detail=result.format_diagnostics(),
            )

        payload = _parse_live_runner_output(self.runner_name, result.stdout)
        run_id = f"{self.runner_name}-resolver-live-{self._launch_count}"
        self._results[run_id] = RunnerPollResult(
            status="success",
            output_summary=json.dumps(payload),
            session_id=request.session_id,
        )
        return RunnerLaunchResult(
            handle=RunnerHandle(
                runner=self.runner_name, run_id=run_id, session_id=request.session_id
            ),
            initial_summary=f"{self.runner_name} live resolver runner started",
        )

    def resume(self, request, workspace: Path) -> RunnerLaunchResult:
        raise AssertionError(f"resume not supported for {request.step_id} in {workspace}")

    def poll(self, handle: RunnerHandle) -> RunnerPollResult:
        result = self._results.pop(handle.run_id)
        return result

    def cancel(self, handle: RunnerHandle) -> None:
        self._results.pop(handle.run_id, None)


@live_runner
@pytest.mark.parametrize(
    ("runner_name", "marker_name"),
    [
        ("codex", "codex_live"),
        ("opencode", "opencode_live"),
    ],
)
def test_resolver_live_path_reaches_real_runner(
    temp_git_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
    runner_name: str,
    marker_name: str,
) -> None:
    if marker_name == "codex_live":
        request = LiveRunnerPreflight("codex")
    else:
        request = LiveRunnerPreflight("opencode")
    request()

    monkeypatch.chdir(temp_git_repo)
    plan_path = temp_git_repo / "plan.yaml"
    runs_root = temp_git_repo / "runs"
    _write_plan(plan_path)

    role_profiles: tuple[RoleProfile, ...] = tuple(
        replace(profile, default_runner=runner_name)
        if profile.role_id == "blocked-case-coordinator"
        else profile
        for profile in default_role_profiles()
    )
    app = build_orchestration_app(
        AppConfig(
            plan_path=plan_path,
            orchestration_config=OrchestrationConfig(
                plan_path=plan_path,
                role_profiles=role_profiles,
            ),
            run_store_root=runs_root,
        )
    )

    registry = RunnerRegistry()
    live_runner = _LiveResolverRunner(runner_name=runner_name, workspace=temp_git_repo)
    registry.register(runner_name, live_runner)
    app._runtime._runner_registry = registry

    report = app.resolve_case(
        ResolutionCase(
            case_id=f"case-live-{runner_name}",
            case_source="review_failed",
            reason="Blocked steps require resolution: core.ready",
            summary="resolve through live runner",
            core=_core_snapshot(),
            roster=_roster_snapshot(),
            runtime=_runtime_snapshot(),
            blocked_step_ids=("core.ready",),
        )
    )

    assert live_runner.launch_count == 1
    assert report.status == "unblocked"
    assert "resolver://live-runner" in report.evidence_refs
    assert any(ref.startswith("resolver_gateway_invocation=") for ref in report.evidence_refs)


test_resolver_live_path_reaches_real_runner = codex_live(
    opencode_live(  # type: ignore[assignment]
        test_resolver_live_path_reaches_real_runner
    )
)
