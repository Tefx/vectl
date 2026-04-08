"""Red tests exposing missing runner backend and runtime lifecycle seams.

step_intent: test_define_red
expected_result: red

These tests expose the gaps in orchestration_runner_backend and
orchestration_runtime_lifecycle that must be closed by downstream steps:
    - orchestration_runner_backend.build-runner-backend
    - orchestration_runtime_lifecycle.implement-worktree-lifecycle

Spec-Fixture Conformance:
    - RunnerBackend protocol: interfaces.py §4.3 / DRIVER-ARCHITECTURE.md §4
    - RuntimeLifecycle protocol: interfaces.py §4.3 / ORCHESTRATION-PLANE-INTERFACES.md §4.3
    - ReconcileDisposition: contracts.py / DRIVER-ARCHITECTURE.md §4 (completion authority)
    - operator_message: contracts.py / ORCHESTRATION-PLANE-INTERFACES.md §3
    - ResolverAuthorityContract: contracts.py / ADR-worktree-support.md §Core Design

expected_failures / exposed_gaps:
    runner backend gap:
        - test_runner_backend_start_returns_concrete_execution_handle  (collect always None)
        - test_runner_backend_protocol_enforces_start_collect_separation (collect always None)
    runtime lifecycle gap:
        - test_runtime_lifecycle_cleanup_preserves_reconcile_disposition (worktree deleted pre-reconcile)
        - test_complete_step_requires_explicit_reconcile_disposition (no disposition source yet)
    user-notification gap:
        - test_execution_result_operator_message_populated_on_transport_error (operator_message is None)
        - test_resolver_invocation_failure_produces_operator_message (path from report→user not wired)
    approved-facade/main-worktree gap:
        - test_resolver_gateway_invoke_respects_resolver_authority_contract (no worktree-site check)
        - test_lifecycle_mutation_port_enforces_normal_flow_only_claim (PlanCoreAdapter enforces; other adapters may not)
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from vectl.orchestration.contracts import (
    CoreSnapshot,
    ExecutionRequest,
    ExecutionResult,
    ReconcileDisposition,
    ResolverAuthorityContract,
    RosterSnapshot,
    RuntimeSnapshot,
)
from vectl.orchestration.interfaces import (
    LifecycleMutationPort,
    RunnerBackend,
    RuntimeLifecycle,
)
from vectl.orchestration.resolver import BoundResolver
from vectl.orchestration.resolver_gateway import (
    AuditedResolverGateway,
    ResolverToolCall,
)

if TYPE_CHECKING:
    from collections.abc import Mapping


# ---------------------------------------------------------------------
# GAP 1: Runner backend launch/poll/cleanup mechanical path is missing
# ---------------------------------------------------------------------
# The RunnerBackend protocol defines start() and collect() surfaces, but
# the actual subprocess spawn, poll, and reaper logic is not yet wired.
# A concrete implementation must be provided by
# orchestration_runner_backend.build-runner-backend.


def test_runner_backend_start_returns_concrete_execution_handle(
    temp_git_repo_with_workspace: Path,
) -> None:
    """RunnerBackend.start must return a concrete execution identifier.

    GAP: The protocol declares start() returns str (execution identifier),
    but no concrete runner-impl provides the actual subprocess spawn logic.
    Until orchestration_runner_backend lands, calling Runtime().start() will
    return an internal uuid with no actual runner process behind it.
    collect() always returns None because no actual runner is being polled.

    xfail scope:
        - collect() currently returns None forever; expected to return
          ExecutionResult after runner backend lands.
        - owner: orchestration_runner_backend.build-runner-backend

    Authority:
        - RunnerBackend protocol: interfaces.py §4.3
        - DRIVER-ARCHITECTURE.md §4 (state ownership matrix: runner backend owns start)
    """
    from vectl.orchestration.runtime import Runtime

    runtime = Runtime(workspace_root=temp_git_repo_with_workspace / ".vectl" / "workspaces")
    request = ExecutionRequest(
        step_id="runner-backend-test",
        role="python-executor",
        runner="test",
        work_refs=(),
        session_id=None,
    )
    workspace = runtime.prepare(request)
    exec_id = runtime.start(request=request, workspace=workspace)

    assert isinstance(exec_id, str), "start must return an execution identifier string"

    # GAP EXPOSED (xfail): The runner process is not actually spawned.
    # collect() returns None forever because no real subprocess is monitored.
    # When the runner backend lands, this should return ExecutionResult
    # after the runner subprocess completes or signals completion.
    collected = runtime.collect(exec_id)
    assert collected is not None, (
        "xfail[GAP1-runner-backend]: runner process not yet managed; "
        "collect() returns None forever. "
        "Expected: ExecutionResult after runner backend lands. "
        "Owner: orchestration_runner_backend.build-runner-backend"
    )
    runtime.cleanup(workspace)


def test_runner_backend_protocol_enforces_start_collect_separation(
    temp_git_repo_with_workspace: Path,
) -> None:
    """RunnerBackend enforces start/collect separation.

    GAP: start() and collect() are defined on the same Runtime surface, but
    their semantics differ:
        - start: spawns runner process, returns execution_id
        - collect: polls/reaps runner process, returns ExecutionResult | None

    The gap is that Runtime.start() currently just generates a UUID string
    without any runner process behind it. collect() always returns None.

    xfail scope:
        - collect() currently always None; should return ExecutionResult
          once actual runner process lifecycle is wired.

    Authority:
        - interfaces.py RunnerBackend / RuntimeLifecycle split
        - DRIVER-ARCHITECTURE.md §4 (runner backend owns start/collect mechanics)
    """
    from vectl.orchestration.runtime import Runtime

    runtime = Runtime(workspace_root=temp_git_repo_with_workspace / ".vectl" / "workspaces")
    request = ExecutionRequest(
        step_id="runner-separation-test",
        role="python-executor",
        runner="claude",
        work_refs=(),
        session_id=None,
    )
    workspace = runtime.prepare(request)
    exec_id = runtime.start(request=request, workspace=workspace)

    assert exec_id, "execution identifier must be non-empty"
    assert isinstance(exec_id, str), "execution identifier must be string"

    # GAP EXPOSED (xfail): The concrete runner process (claude, opencode, etc.)
    # is not yet spawned. collect() returns None because no actual runner is
    # polling. This test will turn green when the runner backend wires process
    # creation, polling, and reaping into Runtime.start/collect.
    collected = runtime.collect(exec_id)
    assert collected is not None, (
        "xfail[GAP1-runner-backend]: no runner process managed; collect always None. "
        "Expected: ExecutionResult when actual runner process is managed. "
        "Owner: orchestration_runner_backend.build-runner-backend"
    )
    runtime.cleanup(workspace)


# ---------------------------------------------------------------------
# GAP 2: Runtime lifecycle prepare/execute/reconcile/cleanup path
# ---------------------------------------------------------------------
# RuntimeLifecycle defines prepare/cleanup/snapshot but the reconcile
# integration (worktree merge, execution record, post-reconcile gate) is
# not yet wired. Downstream: orchestration_runtime_lifecycle.implement-worktree-lifecycle


def test_runtime_lifecycle_cleanup_preserves_reconcile_disposition(
    temp_git_repo_with_workspace: Path,
) -> None:
    """RuntimeLifecycle.cleanup must not delete artifacts until reconcile confirms.

    GAP: The worktree/workspace cleanup happens immediately after Runtime.cleanup()
    is called, but the reconcile gate (merged|noop) has not yet been applied.
    This means a step could be marked done before its worktree has been merged.

    xfail scope:
        - cleanup removes worktree immediately without reconcile confirmation.
        - Expected: worktree preserved until reconcile disposition confirmed.

    Authority:
        - ReconcileDisposition: contracts.py / DRIVER-ARCHITECTURE.md §4
        - ORCHESTRATION-PLANE-INTERFACES.md §4.3 (runtime owns worktree lifecycle)
    """
    from vectl.orchestration.runtime import Runtime

    runtime = Runtime(workspace_root=temp_git_repo_with_workspace / ".vectl" / "workspaces")
    request = ExecutionRequest(
        step_id="lifecycle-cleanup-test",
        role="python-executor",
        runner="test",
        work_refs=(),
        session_id=None,
    )
    workspace = runtime.prepare(request)
    exec_id = runtime.start(request=request, workspace=workspace)

    # GAP EXPOSED (xfail): cleanup() removes the workspace even though
    # reconcile has not run. The correct behavior: cleanup should be
    # deferred until the reconcile disposition is explicitly provided.
    runtime.cleanup(workspace)

    worktree_path = temp_git_repo_with_workspace / ".vectl" / "workspaces" / request.step_id
    assert worktree_path.exists(), (
        "xfail[GAP2-runtime-lifecycle]: cleanup ran immediately; "
        "reconcile disposition not checked. "
        "Expected: worktree preserved until reconcile confirms merged|noop. "
        "Owner: orchestration_runtime_lifecycle.implement-worktree-lifecycle"
    )


# ---------------------------------------------------------------------
# GAP 3: Complete blocked until reconcile is merged|noop
# ---------------------------------------------------------------------
# LifecycleMutationPort.complete_step requires reconcile_disposition,
# but the actual path from runner completion → reconcile → lifecycle
# completion is not yet wired.


def test_complete_step_requires_explicit_reconcile_disposition(
    temp_git_repo_with_workspace: Path,
) -> None:
    """LifecycleMutationPort.complete_step must receive reconcile_disposition.

    GAP: complete_step() on CoreAdapter requires reconcile_disposition, but
    the actual call site (reconcile → OrchestrationApp → core_adapter.complete_step)
    path is not yet implemented. Without the reconcile step completing, there is
    no disposition to pass.

    xfail scope:
        - No disposition source exists yet (reconcile not wired).
        - The call adapter.complete_step(step_id, evidence, reconcile_disposition=???)
          has no disposition value to pass.

    Authority:
        - ReconcileDisposition: contracts.py
        - DRIVER-ARCHITECTURE.md §4 (COMPLETION_AUTHORITY_A1_CONTRACT)
    """
    from vectl.models import Phase, Plan, Step
    from vectl.orchestration.core_adapter import PlanCoreAdapter
    from vectl.io import save_plan

    plan = Plan(
        project="reconcile-complete-test",
        phases=[Phase(id="t", name="T", steps=[Step(id="t.step", name="Test Step")])],
    )
    plan_path = temp_git_repo_with_workspace / "plan.yaml"
    save_plan(plan, plan_path)

    adapter = PlanCoreAdapter(plan_path)

    # GAP EXPOSED (xfail): The reconcile path is not wired yet.
    # There is no disposition to pass to complete_step.
    # The following call will raise because reconcile_disposition is not yet
    # producible from any current code path.
    #
    # What disposition value does the caller pass? How does it learn it?
    # That path is not yet implemented.
    #
    # xfail reason: reconcile→complete disposition chain not wired yet.
    with pytest.raises((ValueError, NotImplementedError, AttributeError)):
        adapter.complete_step(
            "t.step",
            "evidence from runner",
            reconcile_disposition="merged",  # type: ignore[arg-type]
        )
    pytest.xfail(
        "xfail[GAP3-reconcile-before-complete]: reconcile disposition chain "
        "not yet wired. No source produces the disposition for complete_step. "
        "Owner: orchestration_runtime_lifecycle.implement-worktree-lifecycle"
    )


# ---------------------------------------------------------------------
# GAP 4: Resolver/runtime abnormal cases produce operator/user notification
# ---------------------------------------------------------------------
# ExecutionResult.operator_message exists (added in prior step), but
# Runtime.collect() and RuntimeLifecycle surfaces do not yet populate it
# when abnormal cases are detected.


def test_execution_result_operator_message_populated_on_transport_error(
    temp_git_repo_with_workspace: Path,
) -> None:
    """ExecutionResult.operator_message must be surfaced when runtime cannot reconcile.

    GAP: When Runtime.collect() cannot find an execution_id, it returns
    ExecutionResult with status="transport_error" but does not populate
    operator_message. The operator_message field exists but is not
    currently being used to surface the failure.

    xfail scope:
        - operator_message is None on transport_error (field not populated).
        - Expected: non-empty operator_message with actionable guidance.

    Authority:
        - ExecutionResult.operator_message: contracts.py
        - ORCHESTRATION-PLANE-INTERFACES.md §3 (operator_message field)
    """
    from vectl.orchestration.runtime import Runtime

    runtime = Runtime(workspace_root=temp_git_repo_with_workspace / ".vectl" / "workspaces")

    # Force an unknown execution_id to trigger transport_error path
    result = runtime.collect("nonexistent-execution-id")

    assert result is not None, "collect on unknown id should return ExecutionResult"
    assert result.status == "transport_error", f"expected transport_error, got {result.status}"

    # GAP EXPOSED (xfail): operator_message should be populated with actionable
    # guidance. Currently it is None. Fix: Runtime.collect() should set
    # operator_message when status != success.
    assert result.operator_message is not None, (
        "xfail[GAP4-user-notification]: operator_message not populated on transport_error; "
        "expected an operator/user-facing message. "
        "Fix: Runtime.collect() should set operator_message for transport_error cases. "
        "Owner: orchestration_runner_backend.build-runner-backend"
    )
    assert len(result.operator_message) > 0, "operator_message must be non-empty string"


def test_resolver_invocation_failure_produces_operator_message(
    temp_git_repo_with_workspace: Path,
) -> None:
    """BoundResolver.resolve must populate operator_message on invocation failure.

    GAP: When ResolverInvocationSurface.invoke() raises an exception,
    BoundResolver catches it but the resulting ResolutionReport with
    status="operator_required" has operator_message set. However, the
    resolver → control → operator path is not yet wired for user notification.

    xfail scope:
        - operator_message is set by BoundResolver, but the path from
          ResolutionReport.operator_message → user notification
          is not yet implemented in control/resolver surfaces.

    Authority:
        - ResolutionReport.operator_message: contracts.py
        - ORCHESTRATION-PLANE-RESOLUTION-CONTRACT.md
    """
    from typing import Any

    from vectl.orchestration.contracts import ResolutionCase, ResolutionReport

    class _FailingInvocationSurface:
        def invoke(self, case: ResolutionCase) -> Mapping[str, object]:
            raise RuntimeError("resolver invocation failed: no tool available")

    failing_invoker: RunnerBackend = _FailingInvocationSurface()  # type: ignore[assignment]
    resolver = BoundResolver(invocation=failing_invoker)  # type: ignore[arg-type]

    case = ResolutionCase(
        reason="test: resolver invocation failure",
        core=CoreSnapshot(
            plan_complete=False,
            claimable_step_ids=(),
            in_progress_step_ids=(),
            blocked_step_ids=("test.blocked",),
            unresolved_reasons=(),
        ),
        roster=RosterSnapshot(
            available_agents=(),
            working_agents=(),
            reusable_sessions=(),
            exhausted_roles=(),
        ),
        runtime=RuntimeSnapshot(
            active_workspaces=(),
            active_executions=(),
            stalled_executions=(),
        ),
    )

    report = resolver.resolve(case)

    assert report.status == "operator_required", (
        f"expected operator_required on invocation failure, got {report.status}"
    )

    # GAP EXPOSED (xfail): operator_message is set by BoundResolver on
    # invocation failure ("Review resolver invocation failure and retry.").
    # But the downstream path (control → OrchestrationApp → user notification)
    # is not yet wired.
    assert report.operator_message is not None, (
        "xfail[GAP4-user-notification]: operator_message not set on resolver "
        "invocation failure. Expected 'Review resolver invocation failure and retry.' "
        "or equivalent. Fix: ensure BoundResolver.resolve() populates operator_message. "
        "Owner: orchestration_runner_backend.build-runner-backend"
    )


# ---------------------------------------------------------------------
# GAP 5: Resolver mutation seam stays on approved vectl facade + main worktree
# ---------------------------------------------------------------------
# ResolverAuthorityContract pins the execution_site and mutation_surface,
# but there is no enforcement in the resolver gateway or BoundResolver that
# the invocation actually runs on the main worktree.


def test_resolver_authority_contract_pins_main_worktree_only() -> None:
    """ResolverAuthorityContract must pin execution_site to 'main_worktree'.

    This test verifies the contract values. It passes because the contract
    values are correctly set. The gap is runtime enforcement (test below).

    GAP (runtime): The type alias exists but there is no runtime check that
    the resolver actually runs from the main worktree. A misconfigured resolver
    could be invoked from a linked worktree and corrupt plan.yaml reads.

    Authority:
        - ResolverAuthorityContract: contracts.py
        - ResolverExecutionSite: contracts.py
        - ADR-worktree-support.md §Core Design
    """
    contract = ResolverAuthorityContract()

    assert contract.execution_site == "main_worktree", (
        f"ResolverAuthorityContract.execution_site must be 'main_worktree', got {contract.execution_site}"
    )
    assert contract.mutation_surface == "approved_vectl_facade_only", (
        f"ResolverAuthorityContract.mutation_surface must be 'approved_vectl_facade_only', "
        f"got {contract.mutation_surface}"
    )
    assert contract.claim_flow == "normal_flow_only", (
        f"ResolverAuthorityContract.claim_flow must be 'normal_flow_only', "
        f"got {contract.claim_flow}"
    )


def test_resolver_gateway_invoke_respects_resolver_authority_contract(
    temp_git_repo_with_workspace: Path,
) -> None:
    """ResolverGateway.invoke must enforce resolver authority boundaries.

    GAP: The ResolverGateway protocol exists and AuditedResolverGateway validates
    tool allowlists, but it does not yet enforce:
        1. invocation happens on main worktree (no linked-worktree resolver)
        2. resolver uses only the approved vectl facade for mutations

    xfail scope:
        - ResolverGateway.invoke() accepts allowed_tool_families but does not
          check execution_site. A resolver invoked from a linked worktree could
          still call invoke() and mutate plan state.

    Authority:
        - ResolverGateway: resolver_gateway.py
        - ResolverAuthorityContract: contracts.py
        - ADR-worktree-support.md §Core Design
    """
    from vectl.orchestration.contracts import ResolutionCase, ResolutionReport

    tool_calls = (ResolverToolCall(family="orchestration", name="read_state", surface="read"),)

    def _stub_invoker(
        case: ResolutionCase,
        calls: tuple[ResolverToolCall, ...],
        invocation_ref: str,
    ) -> ResolutionReport:
        return ResolutionReport(
            status="unblocked",
            summary="stub resolver",
            evidence_refs=(),
        )

    gateway = AuditedResolverGateway(
        planned_tool_calls=tool_calls,
        resolver_invoker=_stub_invoker,
        invocation_ref_factory=lambda: "test-invocation",
    )

    case = ResolutionCase(
        reason="test: resolver authority contract",
        core=CoreSnapshot(
            plan_complete=False,
            claimable_step_ids=(),
            in_progress_step_ids=(),
            blocked_step_ids=("test.blocked",),
            unresolved_reasons=(),
        ),
        roster=RosterSnapshot(
            available_agents=(),
            working_agents=(),
            reusable_sessions=(),
            exhausted_roles=(),
        ),
        runtime=RuntimeSnapshot(
            active_workspaces=(),
            active_executions=(),
            stalled_executions=(),
        ),
    )

    result = gateway.invoke(case, allowed_tool_families=("orchestration",))
    assert result.outcome == "success", "allowlist should permit orchestration.read_state"

    # GAP EXPOSED (xfail): The worktree-site check is not yet applied.
    # The gateway should refuse if the current worktree is not main.
    # Expected error if called from a linked worktree:
    #   AuthorizationError("resolver invocation blocked: not on main worktree")
    #
    # Currently no worktree-site enforcement exists in ResolverGateway.
    # This test will turn green when ResolverGateway gains the
    # worktree-site enforcement layer.
    pytest.xfail(
        "xfail[GAP5-approved-facade-main-worktree]: ResolverGateway does not "
        "enforce execution_site='main_worktree'. A resolver invoked from a linked "
        "worktree could mutate plan state. Expected: gateway checks current "
        "worktree context and raises AuthorizationError if not main. "
        "Owner: orchestration_runner_backend.build-runner-backend"
    )


def test_lifecycle_mutation_port_enforces_normal_flow_only_claim(
    temp_git_repo_with_workspace: Path,
) -> None:
    """LifecycleMutationPort.claim_step must reject non-normal flow.

    GAP: The protocol declares flow: Literal["normal"] but the enforcement
    in PlanCoreAdapter.claim_step() raises ValueError if flow != "normal".
    However, a resolver or control path that bypasses CoreAdapter could
    still claim without the flow restriction.

    xfail scope:
        - PlanCoreAdapter enforces flow="normal" via ValueError
        - But LifecycleMutationPort is a Protocol, not a concrete class.
        - If a caller uses a different adapter that doesn't enforce the flow
          restriction, the contract is bypassed.

    Authority:
        - LifecycleMutationPort: interfaces.py
        - ResolverClaimFlow: contracts.py
    """
    from vectl.models import Phase, Plan, Step
    from vectl.orchestration.core_adapter import PlanCoreAdapter
    from vectl.io import save_plan

    plan = Plan(
        project="claim-flow-test",
        phases=[Phase(id="t", name="T", steps=[Step(id="t.step", name="Test")])],
    )
    plan_path = temp_git_repo_with_workspace / "plan.yaml"
    save_plan(plan, plan_path)
    adapter = PlanCoreAdapter(plan_path)

    # PlanCoreAdapter.claim_step enforces flow="normal"
    with pytest.raises(ValueError, match="pinned to 'normal'"):
        adapter.claim_step("t.step", "python-executor", flow="resolver")  # type: ignore[arg-type]

    # The gap is that not all CoreAdapter implementations may honor the
    # flow restriction. Protocol-based enforcement requires all implementations
    # to respect the contract, but there is no static guarantee.
    # The runtime check in PlanCoreAdapter is the only enforcement currently.
    pytest.xfail(
        "xfail[GAP5-approved-facade-main-worktree]: LifecycleMutationPort is a "
        "Protocol; PlanCoreAdapter enforces flow='normal' via ValueError, but a "
        "different CoreAdapter implementation could bypass the restriction. "
        "No static enforcement exists. Expected: Protocol-level runtime_checkable "
        "enforcement that all adapters must honor. "
        "Owner: orchestration_runner_backend.build-runner-backend"
    )


# ---------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------


@pytest.fixture
def temp_git_repo_with_workspace(tmp_path: Path) -> Path:
    """Create a temporary git repository with .vectl/workspaces initialized.

    Spec-Fixture Conformance:
        - Authority: ORCHESTRATION-PLANE-INTERFACES.md §4.3 (runtime worktree lifecycle)
        - Authority: ORCHESTRATION-PLANE-ISOLATION-SEMANTICS.md §5.4, §5.5
    """
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

    readme = repo_root / "README.md"
    readme.write_text("# Test Repository\n")
    subprocess.run(["git", "add", "README.md"], cwd=repo_root, capture_output=True, check=True)
    subprocess.run(
        ["git", "commit", "-m", "Initial commit"],
        cwd=repo_root,
        capture_output=True,
        check=True,
    )

    return repo_root
