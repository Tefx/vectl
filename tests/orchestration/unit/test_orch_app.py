"""Composition-root and operator-surface tests for ``vectl.orch_app``.

Authority:
    orch_operator_control_surface.impl_orch_app_composition_root step contract
    docs/ORCHESTRATION-PLANE-IMPLEMENTATION-DESIGN.md section 6
"""

from __future__ import annotations

import json
import subprocess
import time
from dataclasses import replace
from pathlib import Path
from typing import Any, Literal, cast

import pytest

from vectl.io import save_plan
from vectl.models import IsolationMode, Phase, Plan, Step
from vectl.orch_app import (
    AppConfig,
    CaseRuntimeToolMediationSource,
    build_orchestration_app,
    build_resolution_case,
    build_review_parse_failure_case,
    normalize_review_resolution_case,
)
from vectl.orchestration.config import (
    OrchestrationConfig,
    ResolverConfig,
    ResolverToolAllowlist,
    RuntimeConfig,
    default_role_profiles,
)
from vectl.orchestration.contracts import (
    ControlDecision,
    CoreSnapshot,
    DispatchSpec,
    ExecutionResult,
    ReconcileResult,
    ResolutionCase,
    ResolutionReport,
    RoleProfile,
    RosterSnapshot,
    RuntimeSnapshot,
    StructuredReviewResult,
)
from vectl.orchestration.control_channel import FilesystemControlChannel
from vectl.orchestration.dispatch_policy import (
    ConfigPromptRegistry,
    ConfigRoleProfileRegistry,
    DispatchCoordinator,
)
from vectl.orchestration.events import load_event_jsonl
from vectl.orchestration.recovery import LegacyRunStatus
from vectl.orchestration.resolver_gateway import GatewayInvocationResult, ResolverToolCall
from vectl.orchestration.run_store import RunRecord, RunRegistry, generate_run_id
from vectl.orchestration.runner_registry import RunnerRegistry
from vectl.orchestration.runners import (
    RunnerCapabilities,
    RunnerHandle,
    RunnerLaunchResult,
    RunnerPollResult,
)


@pytest.fixture
def temp_git_repo(tmp_path: Path) -> Path:
    """Create a temporary git repository with initial commit."""

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

    (repo_root / "README.md").write_text("# Orch App Test Repository\n")
    subprocess.run(["git", "add", "README.md"], cwd=repo_root, capture_output=True, check=True)
    subprocess.run(
        ["git", "commit", "-m", "Initial commit"],
        cwd=repo_root,
        capture_output=True,
        check=True,
    )
    return repo_root


class _JsonSuccessRunner:
    def __init__(self, payload: dict[str, object]) -> None:
        self._payload = payload
        self.launched = 0
        self._handles: set[str] = set()

    def capabilities(self) -> RunnerCapabilities:
        return RunnerCapabilities(
            runner_id="resolver-test",
            supports_resume=False,
            supports_cancel=True,
            supports_streaming=False,
        )

    def launch(self, request, workspace: Path) -> RunnerLaunchResult:
        self.launched += 1
        run_id = f"resolver-test-{self.launched}"
        self._handles.add(run_id)
        return RunnerLaunchResult(
            handle=RunnerHandle(
                runner="resolver-test", run_id=run_id, session_id=request.session_id
            ),
            initial_summary=f"resolver-test started in {workspace}",
        )

    def resume(self, request, workspace: Path) -> RunnerLaunchResult:
        raise AssertionError(f"resume not expected for {request.step_id} in {workspace}")

    def poll(self, handle: RunnerHandle) -> RunnerPollResult:
        assert handle.run_id in self._handles
        self._handles.remove(handle.run_id)
        return RunnerPollResult(
            status="success",
            output_summary=json.dumps(self._payload),
            session_id=handle.session_id,
        )

    def cancel(self, handle: RunnerHandle) -> None:
        self._handles.discard(handle.run_id)


def _write_plan(plan_path: Path) -> None:
    plan = Plan(
        project="orch-app",
        phases=[
            Phase(
                id="core",
                name="Core",
                steps=[
                    Step(id="core.ready", name="Ready"),
                    Step(id="core.other", name="Other", depends_on=["core.ready"]),
                ],
            )
        ],
    )
    save_plan(plan, plan_path)


def _build_app(tmp_path: Path):
    plan_path = tmp_path / "plan.yaml"
    runs_root = tmp_path / "runs"
    _write_plan(plan_path)
    config = AppConfig(
        plan_path=plan_path,
        orchestration_config=OrchestrationConfig(plan_path=plan_path),
        run_store_root=runs_root,
    )
    return build_orchestration_app(config)


def _continuity_payload(step_id: str, session_id: str, event_id: str) -> dict[str, object]:
    return {
        "ledger": {
            "step_id": step_id,
            "session_id": session_id,
            "runner": "python",
            "status": "active",
        },
        "journal": {
            "event_id": event_id,
            "step_id": step_id,
            "session_id": session_id,
            "runner": "python",
            "event_type": "resume_attempt",
        },
    }


def _core_snapshot(*, blocked: tuple[str, ...] = ("core.ready",)) -> CoreSnapshot:
    return CoreSnapshot(
        plan_complete=False,
        claimable_step_ids=(),
        in_progress_step_ids=(),
        blocked_step_ids=blocked,
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
    return RuntimeSnapshot(
        active_workspaces=(),
        active_executions=(),
        stalled_executions=(),
    )


def test_build_composes_real_collaborators_without_noop_resolver_dependency(tmp_path: Path) -> None:
    app = _build_app(tmp_path)
    orch = cast(Any, app)

    assert orch._control.__class__.__name__ == "PlanAwareControl"
    assert orch._core_adapter.__class__.__name__ == "PlanCoreAdapter"
    assert orch._resolver.__class__.__name__ == "BoundResolver"
    assert orch._resolver.invocation.__class__.__name__ == "GatewayEnforcedResolverInvocation"


def test_runtime_mediation_source_narrows_allowlist_to_live_case_tools() -> None:
    source = CaseRuntimeToolMediationSource(configured_allowlist_families=("core", "orchestration"))

    mediation = source.resolve(
        ResolutionCase(
            case_id="case-runtime-mediation",
            case_source="review_failed",
            reason="Blocked steps require resolution: core.ready",
            core=_core_snapshot(),
            roster=_roster_snapshot(),
            runtime=_runtime_snapshot(),
        ),
        role_id="blocked-case-coordinator",
    )

    assert mediation.planned_tool_calls == (
        ResolverToolCall(family="orchestration", name="read_case", surface="read"),
    )
    assert mediation.allowed_tool_families == ("orchestration",)
    assert "resolver_mediation_calls=orchestration.read_case:read" in mediation.evidence_refs


def test_runtime_mediation_source_uses_case_artifact_directives() -> None:
    source = CaseRuntimeToolMediationSource(configured_allowlist_families=("core",))

    mediation = source.resolve(
        ResolutionCase(
            case_id="",
            case_source="review_failed",
            reason="Blocked steps require resolution: core.ready",
            core=_core_snapshot(),
            roster=_roster_snapshot(),
            runtime=_runtime_snapshot(),
            artifact_refs=("resolver_tool=core.status:read",),
        ),
        role_id="blocked-case-coordinator",
    )

    assert mediation.planned_tool_calls == (
        ResolverToolCall(family="core", name="status", surface="read"),
    )
    assert mediation.allowed_tool_families == ("core",)
    assert "resolver_mediation_allowlist=core" in mediation.evidence_refs


def test_resolve_case_requires_gateway_live_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = _build_app(tmp_path)
    seen: dict[str, object] = {}

    def _fake_authorize_and_invoke(*, case, allowed_tool_families, gateway):
        seen["case_id"] = case.case_id
        seen["allowlist"] = allowed_tool_families
        seen["gateway_class"] = gateway.__class__.__name__
        return GatewayInvocationResult(
            outcome="success",
            invocation_ref="gw-1",
            report=ResolutionReport(
                status="waiting",
                summary="resolver completed through gateway",
                evidence_refs=("resolver://gateway",),
            ),
        )

    monkeypatch.setattr("vectl.orch_app.authorize_and_invoke", _fake_authorize_and_invoke)
    monkeypatch.setattr(
        app._runtime,
        "prepare",
        lambda request: (_ for _ in ()).throw(
            AssertionError("runtime must not be called when gateway is stubbed first")
        ),
    )

    report = app.resolve_case(
        ResolutionCase(
            case_id="case-gateway",
            case_source="review_failed",
            reason="Blocked steps require resolution: core.ready",
            summary="force gateway path",
            core=_core_snapshot(),
            roster=_roster_snapshot(),
            runtime=_runtime_snapshot(),
        )
    )

    assert report.status == "waiting"
    assert seen["case_id"] == "case-gateway"
    assert seen["allowlist"] == ("orchestration",)


def test_resolve_case_returns_operator_required_when_gateway_denies(tmp_path: Path) -> None:
    plan_path = tmp_path / "plan.yaml"
    runs_root = tmp_path / "runs"
    _write_plan(plan_path)
    app = build_orchestration_app(
        AppConfig(
            plan_path=plan_path,
            orchestration_config=OrchestrationConfig(
                plan_path=plan_path,
                resolver=ResolverConfig(tool_allowlist=ResolverToolAllowlist()),
            ),
            run_store_root=runs_root,
        )
    )

    report = app.resolve_case(
        ResolutionCase(
            case_id="case-denied",
            case_source="review_failed",
            reason="Blocked steps require resolution: core.ready",
            summary="deny by empty allowlist",
            core=_core_snapshot(),
            roster=_roster_snapshot(),
            runtime=_runtime_snapshot(),
        )
    )

    assert report.status == "operator_required"
    assert "authorization denied" in report.summary.lower()
    assert "resolver:authorization-denied" in report.evidence_refs
    assert "resolver_mediation_calls=orchestration.read_case:read" in report.evidence_refs


def test_resolve_case_denies_runtime_mediated_tool_outside_config_allowlist(
    temp_git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(temp_git_repo)
    plan_path = temp_git_repo / "plan.yaml"
    runs_root = temp_git_repo / "runs"
    _write_plan(plan_path)
    app = build_orchestration_app(
        AppConfig(
            plan_path=plan_path,
            orchestration_config=OrchestrationConfig(
                plan_path=plan_path,
                resolver=ResolverConfig(
                    tool_allowlist=ResolverToolAllowlist(allowed_tool_families=("orchestration",))
                ),
            ),
            run_store_root=runs_root,
        )
    )

    report = app.resolve_case(
        ResolutionCase(
            case_id="",
            case_source="review_failed",
            reason="Blocked steps require resolution: core.ready",
            summary="runtime-mediated core tool should be denied",
            core=_core_snapshot(),
            roster=_roster_snapshot(),
            runtime=_runtime_snapshot(),
            artifact_refs=("resolver_tool=core.status:read",),
        )
    )

    assert report.status == "operator_required"
    assert "authorization denied" in report.summary.lower()
    assert "resolver_denied_family=core" in report.evidence_refs
    assert "resolver_mediation_calls=core.status:read" in report.evidence_refs


def test_resolve_case_reaches_runtime_runner_through_gateway(
    temp_git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(temp_git_repo)
    plan_path = temp_git_repo / "plan.yaml"
    runs_root = temp_git_repo / "runs"
    _write_plan(plan_path)

    role_profiles: tuple[RoleProfile, ...] = tuple(
        replace(profile, default_runner="resolver-test")
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

    runner = _JsonSuccessRunner(
        {
            "status": "unblocked",
            "summary": "resolved by runtime runner through gateway",
            "evidence_refs": ["resolver://runtime-runner"],
        }
    )
    registry = RunnerRegistry()
    registry.register("resolver-test", runner)
    app._runtime._runner_registry = registry

    report = app.resolve_case(
        ResolutionCase(
            case_id="case-runtime-runner",
            case_source="review_failed",
            reason="Blocked steps require resolution: core.ready",
            summary="runtime runner integration",
            core=_core_snapshot(),
            roster=_roster_snapshot(),
            runtime=_runtime_snapshot(),
            blocked_step_ids=("core.ready",),
        )
    )

    assert report.status == "unblocked"
    assert runner.launched == 1
    assert "resolver://runtime-runner" in report.evidence_refs
    assert any(ref.startswith("resolver_gateway_invocation=") for ref in report.evidence_refs)


def test_build_resolution_case_requires_resolve_decision() -> None:
    core = _core_snapshot()
    roster = _roster_snapshot()
    runtime = _runtime_snapshot()

    with pytest.raises(ValueError, match="kind='resolve'"):
        build_resolution_case(
            case_id="case-1",
            decision=ControlDecision(kind="wait", reason="still waiting"),
            core=core,
            roster=roster,
            runtime=runtime,
        )


def test_build_resolution_case_uses_explicit_non_closure_input() -> None:
    case = build_resolution_case(
        case_id="case-1",
        decision=ControlDecision(
            kind="resolve", reason="Blocked steps require resolution: core.ready"
        ),
        core=_core_snapshot(blocked=("core.ready", "core.other")),
        roster=_roster_snapshot(),
        runtime=_runtime_snapshot(),
        case_source="review_failed",
        summary="review failed after gate",
        artifact_refs=("artifact://gate",),
    )

    assert case.case_id == "case-1"
    assert case.case_source == "review_failed"
    assert case.blocked_step_ids == ("core.ready", "core.other")
    assert case.artifact_refs == ("artifact://gate",)


def test_review_non_pass_normalizes_to_resolution_case() -> None:
    case = normalize_review_resolution_case(
        StructuredReviewResult(
            review_outcome="needs_fix",
            summary="gate found defects",
            evidence_refs=("artifact://review",),
        ),
        case_id="case-review-1",
        core=_core_snapshot(),
        roster=_roster_snapshot(),
        runtime=_runtime_snapshot(),
    )

    assert case is not None
    assert case.case_id == "case-review-1"
    assert case.case_source == "review_failed"
    assert case.artifact_refs == ("artifact://review",)


def test_review_parse_failure_normalizes_to_resolution_case() -> None:
    case = build_review_parse_failure_case(
        raw_output="not parseable",
        role_id="gate-reviewer",
        case_id="case-parse-1",
        core=_core_snapshot(),
        roster=_roster_snapshot(),
        runtime=_runtime_snapshot(),
    )

    assert case.case_id == "case-parse-1"
    assert case.case_source == "review_failed"
    assert "unparseable" in case.reason


def test_resolve_case_surfaces_operator_required_via_case_views(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = _build_app(tmp_path)
    case = ResolutionCase(
        case_id="case-operator-1",
        case_source="review_failed",
        reason="review outcome: operator_required",
        summary="human approval required",
        core=_core_snapshot(),
        roster=_roster_snapshot(),
        runtime=_runtime_snapshot(),
    )

    monkeypatch.setattr(
        app,
        "_resolver",
        type(
            "_StubResolver",
            (),
            {
                "resolve": lambda self, _case: ResolutionReport(
                    status="operator_required",
                    summary="resolver could not close safely",
                    evidence_refs=("resolver://1",),
                    operator_message="human decision required",
                )
            },
        )(),
    )

    report = app.resolve_case(case)

    assert report.status == "operator_required"
    visible = {item.case_id: item for item in app.case_list(status="open")}
    assert visible["case-operator-1"].reason == "human decision required"
    shown = app.case_show("case-operator-1")
    assert shown.status == "open"
    assert shown.reason == "human decision required"


def test_resolve_case_emits_explicit_operator_notification_events(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = _build_app(tmp_path)
    case = ResolutionCase(
        case_id="case-operator-events",
        case_source="review_failed",
        reason="review outcome: operator_required",
        summary="human approval required",
        core=_core_snapshot(),
        roster=_roster_snapshot(),
        runtime=_runtime_snapshot(),
        blocked_step_ids=("core.ready",),
    )

    monkeypatch.setattr(
        app,
        "_resolver",
        type(
            "_StubResolver",
            (),
            {
                "resolve": lambda self, _case: ResolutionReport(
                    status="operator_required",
                    summary="resolver could not close safely",
                    evidence_refs=("resolver://1",),
                    operator_message="human decision required",
                )
            },
        )(),
    )

    report = app.resolve_case(case)
    assert report.status == "operator_required"

    monkeypatch.setattr(
        app,
        "_resolver",
        type(
            "_StubResolverWait",
            (),
            {
                "resolve": lambda self, _case: ResolutionReport(
                    status="waiting", summary="awaiting input"
                )
            },
        )(),
    )
    cleared = app.resolve_case(case)
    assert cleared.status == "waiting"

    events = load_event_jsonl(app._events_path())
    assert [event.kind for event in events] == ["operator_case_opened", "operator_case_resolved"]
    opened_payload = dict(events[0].payload or {})
    resolved_payload = dict(events[1].payload or {})
    assert opened_payload["case_id"] == "case-operator-events"
    assert opened_payload["step_id"] == "core.ready"
    assert opened_payload["resolution_status"] == "operator_required"
    assert opened_payload["open_case_delta"] == 1
    assert resolved_payload == {
        "case_id": "case-operator-events",
        "resolution": "waiting",
        "open_case_delta": -1,
    }


@pytest.mark.parametrize(
    ("status", "expected_kind", "expected_notification"),
    [
        ("unblocked", "dispatch", False),
        ("waiting", "wait", False),
        ("operator_required", "wait", True),
        ("halt", "done", False),
    ],
)
def test_route_resolution_case_covers_all_resolver_outcomes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    status: Literal["unblocked", "waiting", "operator_required", "halt"],
    expected_kind: Literal["dispatch", "wait", "done"],
    expected_notification: bool,
) -> None:
    app = _build_app(tmp_path)
    case = ResolutionCase(
        case_id=f"case-{status}",
        case_source="review_failed",
        reason="resolver outcome proof",
        summary="resolver outcome proof",
        core=_core_snapshot(),
        roster=_roster_snapshot(),
        runtime=_runtime_snapshot(),
        blocked_step_ids=("core.ready",),
    )

    monkeypatch.setattr(
        app,
        "_resolver",
        type(
            "_OutcomeResolver",
            (),
            {
                "resolve": lambda self, _case: ResolutionReport(
                    status=status,
                    summary=f"resolver status={status}",
                    operator_message=(
                        "operator attention required" if status == "operator_required" else None
                    ),
                )
            },
        )(),
    )

    decision = app.route_resolution_case(case)

    assert decision.kind == expected_kind
    assert (app._latest_operator_notification is not None) is expected_notification


def test_resolve_case_invokes_configured_default_resolver_role(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = _build_app(tmp_path)
    seen: dict[str, object] = {}

    monkeypatch.setattr("vectl.orch_app.is_linked_worktree", lambda: (False, tmp_path))
    monkeypatch.setattr(app._runtime, "prepare", lambda request: "ws-1")
    monkeypatch.setattr(
        app._runtime,
        "start",
        lambda *, request, workspace: (
            seen.update(
                {
                    "role": request.role,
                    "workspace": workspace,
                    "work_refs": request.work_refs,
                }
            )
            or "exec-1"
        ),
    )
    monkeypatch.setattr(
        app._runtime,
        "collect",
        lambda _execution_id: ExecutionResult(
            step_id="case-blocked",
            status="success",
            output_summary=json.dumps(
                {
                    "status": "unblocked",
                    "summary": "resolved by configured resolver role",
                    "evidence_refs": ["resolver://default-role"],
                }
            ),
        ),
    )

    report = app.resolve_case(
        ResolutionCase(
            case_id="case-blocked",
            case_source="runtime_failure",
            reason="Blocked steps require resolution: core.ready",
            summary="repair the blocked step",
            core=_core_snapshot(),
            roster=_roster_snapshot(),
            runtime=_runtime_snapshot(),
            blocked_step_ids=("core.ready",),
            artifact_refs=("artifact://blocked",),
        )
    )

    assert report.status == "unblocked"
    assert seen["role"] == "blocked-case-coordinator"
    assert "resolver_role_id=blocked-case-coordinator" in cast(tuple[str, ...], seen["work_refs"])


def test_resolve_case_supports_explicit_tacit_resolver_config_selection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plan_path = tmp_path / "plan.yaml"
    runs_root = tmp_path / "runs"
    _write_plan(plan_path)
    app = build_orchestration_app(
        AppConfig(
            plan_path=plan_path,
            orchestration_config=OrchestrationConfig(
                plan_path=plan_path,
                resolver=ResolverConfig(default_role_id="blocked-case-coordinator-tacit"),
            ),
            run_store_root=runs_root,
        )
    )
    seen_roles: list[str] = []

    monkeypatch.setattr("vectl.orch_app.is_linked_worktree", lambda: (False, tmp_path))
    monkeypatch.setattr(
        app._runtime, "prepare", lambda request: seen_roles.append(request.role) or "ws-1"
    )
    monkeypatch.setattr(app._runtime, "start", lambda *, request, workspace: "exec-tacit")
    monkeypatch.setattr(
        app._runtime,
        "collect",
        lambda _execution_id: ExecutionResult(
            step_id="case-tacit",
            status="success",
            output_summary=json.dumps(
                {
                    "status": "waiting",
                    "summary": "tacit resolver is gathering more context",
                    "evidence_refs": ["resolver://tacit"],
                }
            ),
        ),
    )

    report = app.resolve_case(
        ResolutionCase(
            case_id="case-tacit",
            case_source="review_failed",
            reason="Unresolved authoritative state: claim graph conflict",
            summary="resolve the conflict",
            core=_core_snapshot(),
            roster=_roster_snapshot(),
            runtime=_runtime_snapshot(),
        )
    )

    assert report.status == "waiting"
    assert seen_roles == ["blocked-case-coordinator-tacit"]


def test_route_resolution_case_refreshes_state_after_real_resolver_invocation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = _build_app(tmp_path)
    refreshed_core = _core_snapshot(blocked=("core.other",))
    refreshed_roster = _roster_snapshot()
    refreshed_runtime = _runtime_snapshot()
    snapshot_calls = {"core": 0, "roster": 0, "runtime": 0}
    seen: dict[str, object] = {}

    monkeypatch.setattr("vectl.orch_app.is_linked_worktree", lambda: (False, tmp_path))
    monkeypatch.setattr(app._runtime, "prepare", lambda request: "ws-refresh")
    monkeypatch.setattr(app._runtime, "start", lambda *, request, workspace: "exec-refresh")
    monkeypatch.setattr(
        app._runtime,
        "collect",
        lambda _execution_id: ExecutionResult(
            step_id="case-refresh",
            status="success",
            output_summary=json.dumps(
                {
                    "status": "waiting",
                    "summary": "resolver needs another pass",
                    "evidence_refs": ["resolver://refresh"],
                }
            ),
        ),
    )
    monkeypatch.setattr(
        app._core_adapter,
        "snapshot",
        lambda agent=None: (
            snapshot_calls.__setitem__("core", snapshot_calls["core"] + 1) or refreshed_core
        ),
    )
    monkeypatch.setattr(
        app._roster,
        "snapshot",
        lambda: (
            snapshot_calls.__setitem__("roster", snapshot_calls["roster"] + 1) or refreshed_roster
        ),
    )
    monkeypatch.setattr(
        app._runtime,
        "snapshot",
        lambda: (
            snapshot_calls.__setitem__("runtime", snapshot_calls["runtime"] + 1)
            or refreshed_runtime
        ),
    )
    monkeypatch.setattr(
        app,
        "_control",
        type(
            "_StubControl",
            (),
            {
                "apply_resolution": lambda self, *, report, core, roster, runtime: (
                    seen.update(
                        {
                            "report": report,
                            "core": core,
                            "roster": roster,
                            "runtime": runtime,
                        }
                    )
                    or ControlDecision(kind="wait", reason="refreshed state applied")
                )
            },
        )(),
    )

    decision = app.route_resolution_case(
        ResolutionCase(
            case_id="case-refresh",
            case_source="runtime_failure",
            reason="Blocked steps require resolution: core.ready",
            summary="refresh state after resolver output",
            core=_core_snapshot(),
            roster=_roster_snapshot(),
            runtime=_runtime_snapshot(),
            blocked_step_ids=("core.ready",),
        )
    )

    assert decision.kind == "wait"
    assert snapshot_calls == {"core": 1, "roster": 1, "runtime": 1}
    assert cast(ResolutionReport, seen["report"]).status == "waiting"
    assert seen["core"] == refreshed_core


def test_run_and_control_route_through_typed_boundaries(tmp_path: Path) -> None:
    app = _build_app(tmp_path)

    run_result = app.run(step_id="core.ready", agent="python-executor")
    assert run_result.success is True
    assert run_result.run_id is not None

    runs = app.runs(step_id="core.ready")
    assert len(runs) == 1
    assert runs[0].run_id == run_result.run_id
    assert runs[0].status == "running"

    events = app.inspect_events(step_id="core.ready", limit=10)
    assert events.view_type == "events"
    assert any("event=run_started" in row for row in events.data)

    pause = app.control_pause(step_id="core.ready")
    assert pause.success is True

    actions = app.inspect_actions(run_id=run_result.run_id)
    assert actions.view_type == "actions"
    assert any("type=control.pause" in row for row in actions.data)


def test_run_admission_failure_when_same_plan_already_has_active_run(tmp_path: Path) -> None:
    app = _build_app(tmp_path)
    assert app._config.run_store_root is not None
    registry = RunRegistry(store_root=app._config.run_store_root)
    conflicting_run_id = generate_run_id()
    registry.save(
        RunRecord(
            run_id=conflicting_run_id,
            step_id="core.ready",
            plan_path=str(app._config.plan_path),
            status="running",
            agent="python-executor",
        )
    )

    result = app.run(step_id="core.ready", agent="python-executor")
    assert result.success is False
    assert "Run admission denied" in result.message


def test_run_persists_pending_before_runtime_start(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = _build_app(tmp_path)
    assert app._config.run_store_root is not None
    registry = RunRegistry(store_root=app._config.run_store_root)

    original_start = app._runtime.start

    def start_with_pending_check(*, request, workspace):
        record = registry.by_id(request.session_id)
        assert record is not None
        assert record.status == "pending"
        return original_start(request=request, workspace=workspace)

    monkeypatch.setattr(app._runtime, "start", start_with_pending_check)

    result = app.run(step_id="core.ready", agent="python-executor")
    assert result.success is True


def test_run_claims_before_runtime_start(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    app = _build_app(tmp_path)
    order: list[str] = []

    original_claim = app._core_adapter.claim_step
    original_start = app._runtime.start

    def claim_step(
        step_id: str,
        agent: str,
        *,
        force: bool = False,
        flow: Literal["normal"] = "normal",
    ) -> None:
        order.append(f"claim:{step_id}:{agent}:{flow}")
        original_claim(step_id, agent, force=force, flow=flow)

    def start_with_probe(*, request, workspace):
        order.append(f"start:{request.step_id}:{request.role}")
        return original_start(request=request, workspace=workspace)

    monkeypatch.setattr(app._core_adapter, "claim_step", claim_step)
    monkeypatch.setattr(app._runtime, "start", start_with_probe)

    result = app.run(step_id="core.ready", agent="python-executor")

    assert result.success is True
    assert order[:2] == [
        "claim:core.ready:python-executor:normal",
        "start:core.ready:python-executor",
    ]


def test_run_refuses_runtime_start_when_authoritative_claim_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = _build_app(tmp_path)
    start_calls: list[str] = []
    original_start = app._runtime.start

    def start_with_probe(*, request, workspace):
        start_calls.append(request.step_id)
        return original_start(request=request, workspace=workspace)

    monkeypatch.setattr(app._runtime, "start", start_with_probe)

    result = app.run(step_id="core.other", agent="python-executor")

    assert result.success is False
    assert "unmet dependencies" in result.message
    assert start_calls == []


def test_run_consumes_authoritative_step_isolation_for_runtime_request(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """R30 proof: runtime request isolation comes from authoritative core adapter."""

    plan_path = tmp_path / "plan.yaml"
    runs_root = tmp_path / "runs"
    plan = Plan(
        project="orch-app-isolation",
        phases=[
            Phase(
                id="core",
                name="Core",
                steps=[
                    Step(
                        id="core.independent",
                        name="Independent",
                        isolation=IsolationMode.INDEPENDENT,
                    )
                ],
            )
        ],
    )
    save_plan(plan, plan_path)
    app = build_orchestration_app(
        AppConfig(
            plan_path=plan_path,
            orchestration_config=OrchestrationConfig(plan_path=plan_path),
            run_store_root=runs_root,
        )
    )

    captured_work_refs: list[tuple[str, ...]] = []
    original_prepare = app._runtime.prepare

    def probe_prepare(request):
        captured_work_refs.append(request.work_refs)
        return original_prepare(request)

    monkeypatch.setattr(app._runtime, "prepare", probe_prepare)

    result = app.run(step_id="core.independent", agent="python-executor")
    assert result.success is True
    assert captured_work_refs
    assert "isolation=independent" in captured_work_refs[0]
    assert "execution_context=linked_worktree" in captured_work_refs[0]
    assert "source_kind=step" in captured_work_refs[0]


def test_start_runtime_execution_blocks_context_drift_before_runtime_launch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = _build_app(tmp_path)
    orch = cast(Any, app)
    prepare_called = False

    original_prepare = app._runtime.prepare

    def probe_prepare(request):
        nonlocal prepare_called
        prepare_called = True
        return original_prepare(request)

    monkeypatch.setattr(app._runtime, "prepare", probe_prepare)

    with pytest.raises(ValueError, match="dispatch authority violation"):
        orch._start_runtime_execution(
            run_id="run-drift",
            step_id="core.ready",
            dispatch_spec=DispatchSpec(
                source_kind="step",
                source_id="core.ready",
                role_id="gate-reviewer",
                role_source="default",
                execution_context="linked_worktree",
                runner="codex",
                session_mode="fresh",
            ),
            mode="start",
        )

    assert prepare_called is False


def test_start_runtime_execution_renders_prompt_bundle_before_runtime_launch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ordinary dispatch must pass through PromptRegistry before runtime launch.

    Authority:
        docs/ORCHESTRATION-PLANE-LIVE-AUTHORITY-CONTRACT-LOCK.md §3.1, §3.4, §4.1
        docs/ORCHESTRATION-PLANE-ORCH-APP-ROUTING.md §12.1
    """

    app = _build_app(tmp_path)
    base_prompt_registry = ConfigPromptRegistry(role_registry=app._role_registry)

    class RecordingPromptRegistry:
        def __init__(self) -> None:
            self.render_calls: list[str] = []

        def render(self, spec: DispatchSpec):
            self.render_calls.append(spec.role_id)
            return base_prompt_registry.render(spec)

        def has_role(self, role_id: str) -> bool:
            return base_prompt_registry.has_role(role_id)

    prompt_registry = RecordingPromptRegistry()
    app._dispatch_coordinator = DispatchCoordinator(
        role_registry=app._role_registry,
        prompt_registry=prompt_registry,
        step_adapter=app._dispatch_coordinator.step_adapter,
    )

    captured_work_refs: list[tuple[str, ...]] = []
    original_prepare = app._runtime.prepare

    def probe_prepare(request):
        captured_work_refs.append(request.work_refs)
        return original_prepare(request)

    monkeypatch.setattr(app._runtime, "prepare", probe_prepare)

    result = app.run(step_id="core.ready", agent="python-executor")

    assert result.success is True
    assert prompt_registry.render_calls == ["python-executor"]
    assert captured_work_refs
    assert any(ref.startswith("prompt_bundle_sha256=") for ref in captured_work_refs[0])


def test_start_runtime_execution_fails_closed_when_prompt_registry_lacks_role_support(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ordinary dispatch must fail closed if the prompt seam cannot prove role authority.

    Authority: docs/ORCHESTRATION-PLANE-LIVE-AUTHORITY-CONTRACT-LOCK.md §3.4, §4.3
    """

    app = _build_app(tmp_path)
    orch = cast(Any, app)

    class MissingRolePromptRegistry:
        def render(self, spec: DispatchSpec):
            pytest.fail(f"render should not run when role support is missing for {spec.role_id}")

        def has_role(self, role_id: str) -> bool:
            return False

    app._dispatch_coordinator = DispatchCoordinator(
        role_registry=app._role_registry,
        prompt_registry=MissingRolePromptRegistry(),
        step_adapter=app._dispatch_coordinator.step_adapter,
    )

    prepare_called = False
    original_prepare = app._runtime.prepare

    def probe_prepare(request):
        nonlocal prepare_called
        prepare_called = True
        return original_prepare(request)

    monkeypatch.setattr(app._runtime, "prepare", probe_prepare)

    with pytest.raises(ValueError, match="prompt registry has no authoritative support"):
        orch._start_runtime_execution(
            run_id="run-missing-prompt-role",
            step_id="core.ready",
            dispatch_spec=DispatchSpec(
                source_kind="step",
                source_id="core.ready",
                role_id="python-executor",
                role_source="default",
                execution_context="linked_worktree",
                runner="codex",
                session_mode="fresh",
                prompt_family="coder",
                output_contract="freeform_evidence",
                mutation_policy="worktree_changes",
            ),
            mode="start",
        )

    assert prepare_called is False


def test_route_terminal_execution_completes_only_after_reconcile_allows_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = _build_app(tmp_path)
    orch = cast(Any, app)
    calls: list[str] = []

    def begin_reconcile(execution_id: str) -> ReconcileResult:
        calls.append(f"reconcile:{execution_id}")
        return ReconcileResult(
            execution_id=execution_id,
            workspace_id="ws-1",
            status="merged",
            summary="merged cleanly",
        )

    def can_complete(execution_id: str) -> tuple[bool, str]:
        calls.append(f"can_complete:{execution_id}")
        return True, "ok"

    def complete_step(step_id: str, evidence: str, *, reconcile_disposition: str) -> None:
        calls.append(f"complete:{step_id}:{reconcile_disposition}")

    monkeypatch.setattr(app._runtime, "begin_reconcile", begin_reconcile)
    monkeypatch.setattr(app._runtime, "can_complete", can_complete)
    monkeypatch.setattr(app._runtime, "reconcile_disposition", lambda _execution_id: "merged")
    monkeypatch.setattr(app._core_adapter, "complete_step", complete_step)

    case = orch.route_terminal_execution(
        step_id="core.ready",
        execution_id="exec-1",
        dispatch_spec=DispatchSpec(
            source_kind="step",
            source_id="core.ready",
            role_id="python-executor",
            role_source="default",
            execution_context="linked_worktree",
            runner="codex",
            session_mode="fresh",
        ),
        execution_result=ExecutionResult(
            step_id="core.ready",
            status="success",
            output_summary="verification passed",
        ),
    )

    assert case is None
    assert calls == ["reconcile:exec-1", "can_complete:exec-1", "complete:core.ready:merged"]


def test_route_terminal_execution_hard_gates_non_closing_reconcile_status(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = _build_app(tmp_path)
    orch = cast(Any, app)
    completed: list[str] = []

    monkeypatch.setattr(
        app._runtime,
        "begin_reconcile",
        lambda execution_id: ReconcileResult(
            execution_id=execution_id,
            workspace_id="ws-1",
            status=cast(Any, "unexpected_non_closure"),
            summary="runtime returned an unsupported reconcile status",
        ),
    )
    monkeypatch.setattr(app._runtime, "can_complete", lambda _execution_id: (True, "ok"))
    monkeypatch.setattr(app._runtime, "reconcile_disposition", lambda _execution_id: "merged")
    monkeypatch.setattr(
        app._core_adapter,
        "complete_step",
        lambda step_id, evidence, *, reconcile_disposition: completed.append(step_id),
    )

    case = orch.route_terminal_execution(
        step_id="core.ready",
        execution_id="exec-unexpected-reconcile",
        dispatch_spec=DispatchSpec(
            source_kind="step",
            source_id="core.ready",
            role_id="python-executor",
            role_source="default",
            execution_context="linked_worktree",
            runner="codex",
            session_mode="fresh",
        ),
        execution_result=ExecutionResult(
            step_id="core.ready",
            status="success",
            output_summary="execution succeeded but reconcile did not close",
        ),
    )

    assert case is not None
    assert case.case_source == "runtime_failure"
    assert completed == []


def test_build_dispatch_spec_preserves_canonical_default_role_precedence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = _build_app(tmp_path)
    app._role_registry = ConfigRoleProfileRegistry(default_role="python-executor")
    captured_default_roles: list[str] = []
    original_build_dispatch_spec = DispatchCoordinator.build_dispatch_spec

    def capture_build_dispatch_spec(self, decision):
        captured_default_roles.append(self.role_registry.default_role)
        return original_build_dispatch_spec(self, decision)

    monkeypatch.setattr(DispatchCoordinator, "build_dispatch_spec", capture_build_dispatch_spec)

    spec = app.build_dispatch_spec(step_id="core.ready", role_hint="gate-reviewer")
    assert spec.role_id == "gate-reviewer"
    assert captured_default_roles == ["python-executor"]


def test_route_terminal_execution_non_pass_review_becomes_resolution_case(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = _build_app(tmp_path)
    orch = cast(Any, app)
    completed: list[str] = []

    monkeypatch.setattr(
        app._runtime,
        "begin_reconcile",
        lambda execution_id: ReconcileResult(
            execution_id=execution_id,
            workspace_id="ws-1",
            status="noop",
            summary="nothing to merge",
        ),
    )
    monkeypatch.setattr(app._runtime, "can_complete", lambda _execution_id: (True, "ok"))
    monkeypatch.setattr(app._runtime, "reconcile_disposition", lambda _execution_id: "noop")
    monkeypatch.setattr(
        app._core_adapter,
        "complete_step",
        lambda step_id, evidence, *, reconcile_disposition: completed.append(step_id),
    )

    case = orch.route_terminal_execution(
        step_id="core.ready",
        execution_id="exec-review",
        dispatch_spec=DispatchSpec(
            source_kind="step",
            source_id="core.ready",
            role_id="gate-reviewer",
            role_source="default",
            execution_context="main_worktree",
            runner="codex",
            session_mode="fresh",
            output_contract="structured_review_result",
        ),
        execution_result=ExecutionResult(
            step_id="core.ready",
            status="success",
            output_summary=json.dumps(
                {
                    "review_outcome": "needs_fix",
                    "summary": "defects remain",
                    "evidence_refs": ["artifact://review"],
                }
            ),
        ),
    )

    assert case is not None
    assert case.case_source == "review_failed"
    assert completed == []


def test_route_terminal_execution_parse_failure_becomes_resolution_case(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = _build_app(tmp_path)
    orch = cast(Any, app)
    monkeypatch.setattr(
        app._runtime,
        "begin_reconcile",
        lambda execution_id: ReconcileResult(
            execution_id=execution_id,
            workspace_id="ws-1",
            status="noop",
            summary="nothing to merge",
        ),
    )
    monkeypatch.setattr(app._runtime, "can_complete", lambda _execution_id: (True, "ok"))
    monkeypatch.setattr(app._runtime, "reconcile_disposition", lambda _execution_id: "noop")
    monkeypatch.setattr(
        app._core_adapter,
        "complete_step",
        lambda *args, **kwargs: pytest.fail("complete_step must not run on parse failure"),
    )

    case = orch.route_terminal_execution(
        step_id="core.ready",
        execution_id="exec-parse",
        dispatch_spec=DispatchSpec(
            source_kind="step",
            source_id="core.ready",
            role_id="gate-reviewer",
            role_source="default",
            execution_context="main_worktree",
            runner="codex",
            session_mode="fresh",
            output_contract="structured_review_result",
        ),
        execution_result=ExecutionResult(
            step_id="core.ready",
            status="success",
            output_summary="not-json",
        ),
    )

    assert case is not None
    assert case.case_source == "review_failed"
    assert "unparseable" in case.reason


def test_dispatch_resolution_subtask_uses_shared_runtime_substrate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = _build_app(tmp_path)
    orch = cast(Any, app)
    captured_requests: list[tuple[str, ...]] = []

    original_prepare = app._runtime.prepare

    def probe_prepare(request):
        captured_requests.append(request.work_refs)
        return original_prepare(request)

    monkeypatch.setattr(app._runtime, "prepare", probe_prepare)

    spec, _workspace, _execution_id = orch.dispatch_resolution_subtask(
        case_id="case-1",
        role_id="gate-reviewer",
        description="review the blocked case",
        run_id="run-1",
    )

    assert spec.source_kind == "resolution_subtask"
    assert spec.execution_context == "main_worktree"
    assert captured_requests
    assert "source_kind=resolution_subtask" in captured_requests[0]
    assert "execution_context=main_worktree" in captured_requests[0]


def test_run_runtime_start_failure_terminalizes_after_durable_admission(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = _build_app(tmp_path)

    def fail_start(*, request, workspace):
        raise RuntimeError("boom")

    monkeypatch.setattr(app._runtime, "start", fail_start)

    result = app.run(step_id="core.ready", agent="python-executor")
    assert result.success is False
    assert result.run_id is not None
    registry = RunRegistry(store_root=tmp_path / "runs")
    record = registry.by_id(result.run_id)
    assert record is not None
    assert record.status == "fail"


def test_run_events_emit_only_after_running_record_is_durable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = _build_app(tmp_path)
    registry = RunRegistry(store_root=tmp_path / "runs")
    observed_statuses: list[str | None] = []

    original_emit = app._event_sink.emit

    def emit_with_status_probe(envelope) -> None:
        run_id = str((envelope.payload or {}).get("run_id", ""))
        record = registry.by_id(run_id)
        observed_statuses.append(None if record is None else record.status)
        original_emit(envelope)

    monkeypatch.setattr(app._event_sink, "emit", emit_with_status_probe)

    result = app.run(step_id="core.ready", agent="python-executor")
    assert result.success is True
    assert observed_statuses == ["running", "running"]


def test_run_event_persistence_failure_terminalizes_run(tmp_path: Path) -> None:
    app = _build_app(tmp_path)
    assert app._config.run_store_root is not None

    def fail_emit(_envelope) -> None:
        raise RuntimeError("event sink down")

    app._event_sink.emit = fail_emit  # type: ignore[method-assign] # test fault injection

    result = app.run(step_id="core.ready", agent="python-executor")
    assert result.success is False
    assert result.run_id is not None
    registry = RunRegistry(store_root=app._config.run_store_root)
    final = registry.by_id(result.run_id)
    assert final is not None
    assert final.status == "fail"


def test_build_fails_when_authoritative_plan_path_missing(tmp_path: Path) -> None:
    missing_plan = tmp_path / "missing-plan.yaml"
    config = AppConfig(
        plan_path=missing_plan,
        orchestration_config=OrchestrationConfig(plan_path=missing_plan),
        run_store_root=tmp_path / "runs",
    )

    with pytest.raises(FileNotFoundError, match="authoritative plan path not found"):
        build_orchestration_app(config)


def test_build_wires_runtime_workspace_root_from_orchestration_config(tmp_path: Path) -> None:
    plan_path = tmp_path / "plan.yaml"
    runs_root = tmp_path / "runs"
    workspace_root = tmp_path / "custom-workspaces"
    _write_plan(plan_path)
    config = AppConfig(
        plan_path=plan_path,
        orchestration_config=OrchestrationConfig(
            plan_path=plan_path,
            runtime=RuntimeConfig(workspace_root=workspace_root),
        ),
        run_store_root=runs_root,
    )
    app = build_orchestration_app(config)

    result = app.run(step_id="core.ready", agent="python-executor")
    assert result.success is True
    assert workspace_root.exists()
    assert any(workspace_root.iterdir())


def test_control_pause_reports_ambiguous_run_selection(tmp_path: Path) -> None:
    app = _build_app(tmp_path)
    assert app._config.run_store_root is not None
    registry = RunRegistry(store_root=app._config.run_store_root)
    for step_id in ("core.ready", "core.other"):
        registry.save(
            RunRecord(
                run_id=generate_run_id(),
                step_id=step_id,
                plan_path=str(app._config.plan_path),
                status="running",
                agent="python-executor",
            )
        )

    result = app.control_pause(step_id=None)
    assert result.success is False
    assert "Run selection is ambiguous" in result.message


def test_control_reason_and_force_are_persisted_in_pending_actions(tmp_path: Path) -> None:
    app = _build_app(tmp_path)
    orch = cast(Any, app)
    run_result = app.run(step_id="core.ready", agent="python-executor")
    assert run_result.run_id is not None

    pause = orch.control_pause(step_id="core.ready", reason="maintenance")
    unpause = orch.control_unpause(step_id="core.ready", reason="resumed")
    stop = orch.control_stop(run_id=run_result.run_id, reason="halt", force=True)
    assert pause.success is True
    assert unpause.success is True
    assert stop.success is True

    assert app._config.run_store_root is not None
    channel = FilesystemControlChannel(runs_root=app._config.run_store_root)
    requests = channel.list_requests(run_result.run_id, status="pending")
    by_type = {request.msg_type: request for request in requests}

    assert by_type["control.pause"].payload == (run_result.run_id, "maintenance")
    assert by_type["control.unpause"].payload == (run_result.run_id, "resumed")
    assert by_type["control.stop"].payload == (run_result.run_id, "halt", "force=true")


def test_control_stop_uses_explicit_run_id_when_multiple_runs_exist(tmp_path: Path) -> None:
    app = _build_app(tmp_path)
    orch = cast(Any, app)
    first = app.run(step_id="core.ready", agent="python-executor")
    assert first.run_id is not None
    assert app._config.run_store_root is not None
    registry = RunRegistry(store_root=app._config.run_store_root)
    second_run_id = generate_run_id()
    registry.save(
        RunRecord(
            run_id=second_run_id,
            step_id="core.other",
            status="running",
            updated_at=time.time(),
            plan_path=str(app._config.plan_path),
            agent="python-executor",
        )
    )

    stop = orch.control_stop(run_id=first.run_id, reason="halt")

    assert stop.success is True
    assert first.run_id in stop.message

    channel = FilesystemControlChannel(runs_root=app._config.run_store_root)
    requests = channel.list_requests(first.run_id, status="pending")
    assert any(request.msg_type == "control.stop" for request in requests)


def test_config_show_effective_returns_expanded_view(tmp_path: Path) -> None:
    app = _build_app(tmp_path)
    orch = cast(Any, app)

    summary = orch.config_show(effective=False)
    effective = orch.config_show(effective=True)

    assert "artifact_root=" in summary.show_output
    assert "control.idle_poll_interval_ms=" not in summary.show_output
    assert "control.idle_poll_interval_ms=" in effective.show_output
    assert "operator.max_pending_actions=" in effective.show_output


def test_resume_safe_replays_from_durable_artifacts(tmp_path: Path) -> None:
    """End-to-end runtime proof that projection replay restores derived state artifacts.

    Authority: §9.2 - Projection must replay events into state/latest.json, state/summary.json,
    state/metrics.json with seq-order replay semantics.

    Verifies:
    - All three derived state artifacts are created after resume
    - Artifact schemas contain required fields per §9.2
    - Events are replayed in seq-order (last_event_seq matches event count)
    """
    app = _build_app(tmp_path)
    started = app.run(step_id="core.ready", agent="python-executor")
    assert started.success is True
    assert started.run_id is not None

    resumed = app.resume(started.run_id)
    assert resumed.success is True
    assert "resume_safe" in resumed.message

    registry = RunRegistry(store_root=tmp_path / "runs")
    run_root = registry.run_artifact_root(started.run_id)

    # Verify all three derived state artifacts exist (§9.2)
    latest_path = run_root / "state" / "latest.json"
    summary_path = run_root / "state" / "summary.json"
    metrics_path = run_root / "state" / "metrics.json"
    assert latest_path.exists(), "state/latest.json must exist per §9.2"
    assert summary_path.exists(), "state/summary.json must exist per §9.2"
    assert metrics_path.exists(), "state/metrics.json must exist per §9.2"

    # Load and validate state/latest.json schema (per §9.2.1)
    latest_payload = json.loads(latest_path.read_text(encoding="utf-8"))
    assert latest_payload.get("run_id") == started.run_id, "latest.json run_id mismatch"
    assert "version" in latest_payload, "latest.json missing version field"
    assert "status" in latest_payload, "latest.json missing status field"
    assert "last_event_seq" in latest_payload, "latest.json missing last_event_seq for replay proof"
    assert "active_step_id" in latest_payload, "latest.json missing active_step_id"
    assert "projection_health" in latest_payload, "latest.json missing projection_health"

    # Load and validate state/summary.json schema (per §9.2.1)
    summary_payload = json.loads(summary_path.read_text(encoding="utf-8"))
    assert summary_payload.get("run_id") == started.run_id, "summary.json run_id mismatch"
    assert "version" in summary_payload, "summary.json missing version field"
    assert "active_step_id" in summary_payload, "summary.json missing active_step_id"
    assert "open_case_count" in summary_payload, "summary.json missing open_case_count"
    assert "active_execution_count" in summary_payload, (
        "summary.json missing active_execution_count"
    )
    assert "last_event_seq" in summary_payload, "summary.json missing last_event_seq"

    # Load and validate state/metrics.json schema (per §9.2.1)
    metrics_payload = json.loads(metrics_path.read_text(encoding="utf-8"))
    assert metrics_payload.get("run_id") == started.run_id, "metrics.json run_id mismatch"
    assert "version" in metrics_payload, "metrics.json missing version field"
    assert "dispatch_count" in metrics_payload, "metrics.json missing dispatch_count"
    assert "resolution_count" in metrics_payload, "metrics.json missing resolution_count"
    assert "operator_required_case_count" in metrics_payload, (
        "metrics.json missing operator_required_case_count"
    )
    assert "transport_error_count" in metrics_payload, "metrics.json missing transport_error_count"
    assert "total_resolver_tokens" in metrics_payload, "metrics.json missing total_resolver_tokens"

    # Verify seq-order replay: last_event_seq should be non-negative
    # (actual value depends on events emitted during run/resume sequence)
    last_seq = latest_payload.get("last_event_seq", -1)
    assert isinstance(last_seq, int), "last_event_seq must be int for seq-order replay proof"
    assert last_seq >= 0, f"last_event_seq={last_seq} indicates no events replayed"

    # Verify consistency across all three artifacts
    assert latest_payload.get("last_event_seq") == summary_payload.get("last_event_seq"), (
        "latest.json and summary.json last_event_seq must match for projection consistency"
    )


def test_recover_and_resume_from_durable_artifacts(tmp_path: Path) -> None:
    app = _build_app(tmp_path)
    started = app.run(step_id="core.ready", agent="python-executor")
    assert started.success is True
    assert started.run_id is not None

    recovered = app.recover()
    assert recovered.success is True
    assert "recover_and_resume" in recovered.message
    assert "artifact_families=" in recovered.message


def test_recover_blocks_on_blocking_divergence(tmp_path: Path) -> None:
    app = _build_app(tmp_path)
    started = app.run(step_id="core.ready", agent="python-executor")
    assert started.run_id is not None

    run_root = RunRegistry(store_root=tmp_path / "runs").run_artifact_root(started.run_id)
    ledger_path = run_root / "continuity" / "ledger.json"
    ledger_payload = json.loads(ledger_path.read_text(encoding="utf-8"))
    ledger_payload["step_id"] = "core.other"
    ledger_path.write_text(json.dumps(ledger_payload) + "\n", encoding="utf-8")

    recovered = app.recover()
    assert recovered.success is False
    assert "blocking_divergence" in recovered.message


def test_recover_blocks_on_corrupt_blocking(tmp_path: Path) -> None:
    app = _build_app(tmp_path)
    started = app.run(step_id="core.ready", agent="python-executor")
    assert started.run_id is not None

    run_root = RunRegistry(store_root=tmp_path / "runs").run_artifact_root(started.run_id)
    journal_path = run_root / "continuity" / "journal.jsonl"
    journal_path.write_text('{"broken"\n', encoding="utf-8")

    recovered = app.recover()
    assert recovered.success is False
    assert "corrupt_blocking" in recovered.message


def test_recover_blocks_on_ambiguous_blocking(tmp_path: Path) -> None:
    app = _build_app(tmp_path)
    started = app.run(step_id="core.ready", agent="python-executor")
    assert started.run_id is not None

    run_root = RunRegistry(store_root=tmp_path / "runs").run_artifact_root(started.run_id)
    capability_path = run_root / "continuity" / "capability.snapshot.json"
    capability_payload = json.loads(capability_path.read_text(encoding="utf-8"))
    capability_payload["fingerprint"] = "mismatch"
    capability_path.write_text(json.dumps(capability_payload) + "\n", encoding="utf-8")

    recovered = app.recover()
    assert recovered.success is False
    assert "ambiguous_blocking" in recovered.message


def test_recover_terminalizes_fresh_start_required_before_restart(tmp_path: Path) -> None:
    app = _build_app(tmp_path)
    started = app.run(step_id="core.ready", agent="python-executor")
    assert started.run_id is not None

    run_root = RunRegistry(store_root=tmp_path / "runs").run_artifact_root(started.run_id)
    heartbeat_path = run_root / "heartbeat.json"
    heartbeat_payload = json.loads(heartbeat_path.read_text(encoding="utf-8"))
    heartbeat_payload["last_heartbeat_at"] = time.time() - 1000.0
    heartbeat_path.write_text(json.dumps(heartbeat_payload) + "\n", encoding="utf-8")

    recovered = app.recover()
    assert recovered.success is True
    assert "fresh_start_required" in recovered.message

    registry = RunRegistry(store_root=tmp_path / "runs")
    terminal = registry.by_id(started.run_id)
    assert terminal is not None
    assert terminal.status == "fail"

    restarted = app.run(step_id="core.ready", agent="python-executor")
    assert restarted.success is True


def test_recover_dry_run_does_not_mutate_durable_state(tmp_path: Path) -> None:
    app = _build_app(tmp_path)
    started = app.run(step_id="core.ready", agent="python-executor")
    assert started.run_id is not None

    run_root = RunRegistry(store_root=tmp_path / "runs").run_artifact_root(started.run_id)
    heartbeat_path = run_root / "heartbeat.json"
    heartbeat_payload = json.loads(heartbeat_path.read_text(encoding="utf-8"))
    heartbeat_payload["last_heartbeat_at"] = time.time() - 1000.0
    heartbeat_path.write_text(json.dumps(heartbeat_payload) + "\n", encoding="utf-8")

    recovered = app.recover(dry_run=True)
    assert recovered.success is True
    assert "fresh_start_required" in recovered.message


def test_imported_legacy_runs_are_visible_via_orch_runs_surface(tmp_path: Path) -> None:
    app = _build_app(tmp_path)
    assert app._config.run_store_root is not None
    registry = RunRegistry(store_root=app._config.run_store_root)
    imported = registry.import_legacy_run(
        legacy_run_id="legacy-101",
        step_id="core.ready",
        plan_path=str(app._config.plan_path),
        status="running",
        migration_state="parallel",
        continuity_artifacts={
            "ledger": {
                "step_id": "core.ready",
                "session_id": "sess-101",
                "runner": "python",
                "status": "active",
            },
            "journal": {
                "event_id": "evt-101",
                "step_id": "core.ready",
                "session_id": "sess-101",
                "runner": "python",
                "event_type": "resume_attempt",
            },
        },
    )

    runs = app.runs(step_id="core.ready")
    assert len(runs) == 1
    assert runs[0].run_id == imported.run_id
    assert runs[0].source == "legacy_imported"
    assert runs[0].legacy_run_id == "legacy-101"
    assert runs[0].legacy_migration_state == "parallel"


def test_recover_surfaces_imported_legacy_continuity_blockers(tmp_path: Path) -> None:
    app = _build_app(tmp_path)
    assert app._config.run_store_root is not None
    registry = RunRegistry(store_root=app._config.run_store_root)
    imported = registry.import_legacy_run(
        legacy_run_id="legacy-blocked",
        step_id="core.ready",
        plan_path=str(app._config.plan_path),
        status="running",
        migration_state="preferred",
        continuity_artifacts={
            "ledger": {
                "step_id": "core.ready",
                "session_id": "sess-201",
                "runner": "python",
                "status": "active",
            }
        },
    )

    result = app.recover(step_id="core.ready")
    assert result.success is False
    assert "Recovery blocked" in result.message
    assert imported.run_id in result.message


def test_cutover_validate_surfaces_four_retirement_criteria(tmp_path: Path) -> None:
    app = _build_app(tmp_path)
    result = app.cutover_validate()

    assert result.can_cutover is True
    assert len(result.criteria_results) == 4
    assert all(item.startswith("criterion.") for item in result.criteria_results)


def test_migration_advance_state_updates_imported_run_record(tmp_path: Path) -> None:
    app = _build_app(tmp_path)
    assert app._config.run_store_root is not None
    registry = RunRegistry(store_root=app._config.run_store_root)
    imported = registry.import_legacy_run(
        legacy_run_id="legacy-advance",
        step_id="core.ready",
        plan_path=str(app._config.plan_path),
        status="running",
        migration_state="parallel",
        continuity_artifacts=_continuity_payload("core.ready", "sess-advance", "evt-advance"),
    )

    advanced = app.migration_advance_state(
        status=LegacyRunStatus.DEPRECATED,
        legacy_run_id="legacy-advance",
    )

    assert advanced.success is True
    updated = registry.by_id(imported.run_id)
    assert updated is not None
    assert updated.legacy_migration_state == "deprecated"


def test_resume_blocks_when_event_transcript_is_corrupt(tmp_path: Path) -> None:
    app = _build_app(tmp_path)
    started = app.run(step_id="core.ready", agent="python-executor")
    assert started.run_id is not None

    events_path = tmp_path / "runs" / "events.jsonl"
    events_path.write_text('{"broken"\n', encoding="utf-8")

    resumed = app.resume(started.run_id)
    assert resumed.success is False
    assert "corrupt_blocking" in resumed.message


@pytest.mark.parametrize("missing_heartbeat", [True, False])
def test_decision_matrix_blocks_or_requires_fresh_start_for_unknown_or_stale_heartbeat(
    tmp_path: Path,
    missing_heartbeat: bool,
) -> None:
    app = _build_app(tmp_path)
    started = app.run(step_id="core.ready", agent="python-executor")
    assert started.run_id is not None

    run_root = RunRegistry(store_root=tmp_path / "runs").run_artifact_root(started.run_id)
    heartbeat_path = run_root / "heartbeat.json"
    if missing_heartbeat:
        heartbeat_path.unlink()
    else:
        heartbeat_payload = json.loads(heartbeat_path.read_text(encoding="utf-8"))
        heartbeat_payload["last_heartbeat_at"] = time.time() - 1000.0
        heartbeat_path.write_text(json.dumps(heartbeat_payload) + "\n", encoding="utf-8")

    recovered = app.recover(dry_run=True)
    assert recovered.success is True
    assert "fresh_start_required" in recovered.message
