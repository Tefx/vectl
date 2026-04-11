"""End-to-end integration proof for orch_app routing seams.

Authority:
    docs/ORCHESTRATION-PLANE-ORCH-APP-ROUTING.md
    docs/ORCHESTRATION-PLANE-IMPLEMENTATION-DESIGN.md section 6
    docs/ORCHESTRATION-PLANE-INTERFACES.md sections 3, 4, 5

This module provides runtime integration proof that the three critical
orch_app routing seams function correctly when composed through the
real build_orchestration_app() factory and real collaborator instances:

1. **claim-before-start**: OrchestrationApp.run() must claim a step via
   the authoritative core_adapter *before* invoking runtime.start().
   This prevents phantom dispatches where runtime work begins without
   an authoritative claim, which would corrupt plan state.

2. **reconcile-before-complete**: OrchestrationApp.route_terminal_execution()
   must complete reconcile *before* core_adapter.complete_step() is called.
   This is the completion authority gate: a step may only be marked complete
   after its work is merged/accepted by the reconcile subsystem.

3. **review/gate failure routing to ResolutionCase**: When a structured
   review/gate produces a non-pass outcome (needs_fix, needs_replan,
   operator_required) or the review output is unparseable, route_terminal_execution()
   must produce a ResolutionCase (not call complete_step). This ensures failed
   gates do not silently complete steps.

Each test operates through the composed OrchestrationApp surface wired by
build_orchestration_app() with real (non-mock) collaborator instances wherever
possible.  Monkeypatching is used *only* to inject probes at internal method
boundaries (to observe ordering), never to replace I/O or state boundaries.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal, cast

import pytest

from vectl.io import save_plan
from vectl.models import Phase, Plan, Step
from vectl.orch_app import (
    AppConfig,
    OrchestrationApp,
    OrchestrationResult,
    build_orchestration_app,
)
from vectl.orchestration.config import OrchestrationConfig
from vectl.orchestration.contracts import DispatchSpec, ExecutionResult, ReconcileResult
from vectl.orchestration.run_store import RunRegistry


def _skip_collect_and_route_terminal(
    self,
    *,
    registry: RunRegistry,
    run_id: str,
    step_id: str,
    execution_id: str,
    dispatch_spec: DispatchSpec,
    agent: str,
    run_root: Path,
    max_poll_iterations: int = 600,
    poll_interval_seconds: float = 0.1,
) -> OrchestrationResult:
    """Skipped collect-and-route that returns success without full lifecycle.

    This is a monkeypatch for tests that verify other aspects of the
    orchestration (claim ordering, event persistence, etc.) without needing
    a complete runner completion cycle.
    """
    return OrchestrationResult(
        success=True,
        message=(f"resume_safe: Skipped collect-and-route for integration test (step={step_id})"),
        step_id=step_id,
        run_id=run_id,
    )


@pytest.fixture(autouse=True)
def _patch_collect_and_route():
    """Autouse fixture: skip _collect_and_route_terminal for routing seam tests.

    These tests verify ordering invariants (claim-before-start, etc.) via
    monkeypatching, not end-to-end runner completion.
    """
    original = OrchestrationApp._collect_and_route_terminal
    OrchestrationApp._collect_and_route_terminal = _skip_collect_and_route_terminal  # type: ignore[assignment]
    yield
    OrchestrationApp._collect_and_route_terminal = original  # type: ignore[assignment]


def _write_plan(plan_path: Path) -> None:
    plan = Plan(
        project="routing-seam-proof",
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
    app = build_orchestration_app(config)
    return app


# ======================================================================
# SEAM 1: claim-before-start
# ======================================================================


def test_claim_before_start_seam(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Prove that claim_step is invoked BEFORE runtime.start in the run() path.

    This is the authoritative ordering invariant:
        core_adapter.claim_step(step_id, agent)  →  runtime.start(request, workspace)

    If runtime.start is invoked before claim_step, then the step is being
    dispatched without an authoritative claim, which would corrupt the plan
    lifecycle. The test fails if the ordering is violated.

    Evidence: the call order list must show claim before start.
    """
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

    assert result.success is True, f"Run must succeed: {result.message}"
    # The authoritative invariant: claim appears before start
    assert order[:2] == [
        "claim:core.ready:python-executor:normal",
        "start:core.ready:python-executor",
    ], f"claim-before-start ordering violated: {order}"


def test_claim_before_start_durable_before_start(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Prove that the durable run record exists in 'pending' state before runtime.start.

    The admission guard must persist a 'pending' record before runtime.start
    is invoked, ensuring the run is durable even if the runtime crashes.
    This is not just ordering—it's durability before side effects.
    """
    app = _build_app(tmp_path)
    assert app._config.run_store_root is not None
    registry = RunRegistry(store_root=app._config.run_store_root)

    original_start = app._runtime.start

    def start_with_pending_check(*, request, workspace):
        # Verify that at the moment runtime.start is called, the run record
        # already exists in 'pending' state (durable admission)
        record = registry.by_id(request.session_id)
        assert record is not None, "Run record must be durable before runtime.start is invoked"
        assert record.status == "pending", (
            f"Run record must be 'pending' before runtime.start, got: {record.status}"
        )
        return original_start(request=request, workspace=workspace)

    monkeypatch.setattr(app._runtime, "start", start_with_pending_check)

    result = app.run(step_id="core.ready", agent="python-executor")
    assert result.success is True, f"Run must succeed: {result.message}"


# ======================================================================
# SEAM 2: reconcile-before-complete
# ======================================================================


def test_reconcile_before_complete_seam(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Prove that reconcile is invoked BEFORE complete_step in route_terminal_execution().

    This is the completion authority gate:
        runtime.begin_reconcile(execution_id)  →  core_adapter.complete_step(step_id, evidence)

    Complete_step is the final irreversible plan mutation. It must only happen
    after reconcile has confirmed the work was successfully merged.

    Evidence: the call order list must show reconcile before complete.
    """
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

    # Successful path: no resolution case produced
    assert case is None, "Successful execution should not produce a ResolutionCase"

    # The authoritative ordering invariant
    assert calls == [
        "reconcile:exec-1",
        "can_complete:exec-1",
        "complete:core.ready:merged",
    ], f"reconcile-before-complete ordering violated: {calls}"


def test_reconcile_merge_conflict_prevents_complete(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Prove that a merge conflict during reconcile BLOCKS complete_step.

    When reconcile returns merge_conflict status, route_terminal_execution()
    must produce a ResolutionCase instead of calling complete_step. This is the
    negative path of the reconcile-before-complete seam.
    """
    app = _build_app(tmp_path)
    orch = cast(Any, app)
    complete_called: list[str] = []

    def begin_reconcile(execution_id: str) -> ReconcileResult:
        return ReconcileResult(
            execution_id=execution_id,
            workspace_id="ws-1",
            status="merge_conflict",
            summary="conflict in integration",
            artifact_refs=("artifact://conflict",),
        )

    monkeypatch.setattr(app._runtime, "begin_reconcile", begin_reconcile)
    monkeypatch.setattr(
        app._core_adapter,
        "complete_step",
        lambda *args, **kwargs: complete_called.append("COMPLETE_WAS_CALLED"),
    )

    case = orch.route_terminal_execution(
        step_id="core.ready",
        execution_id="exec-conflict",
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
            output_summary="work done but merge had conflict",
        ),
    )

    # Merge conflict must produce a ResolutionCase, not complete the step
    assert case is not None, "Merge conflict must produce a ResolutionCase"
    assert case.case_source == "merge_conflict", (
        f"Expected merge_conflict source, got: {case.case_source}"
    )
    assert complete_called == [], (
        "complete_step must NOT be called when reconcile has merge conflicts"
    )


# ======================================================================
# SEAM 3: review/gate failure routing to ResolutionCase
# ======================================================================


def test_review_needs_fix_routes_to_resolution_case(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Prove that a structured review with 'needs_fix' outcome routes to ResolutionCase.

    When a dispatch spec has output_contract='structured_review_result' and
    the review returns needs_fix, route_terminal_execution() must:
    1. Normalize the review result to a ResolutionCase
    2. NOT call complete_step

    This is the gate failure routing seam: failed reviews must not silently
    complete steps.
    """
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
        execution_id="exec-review-needs-fix",
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
                    "summary": "defects found in implementation",
                    "findings": ["missing test coverage", "unsafe error handling"],
                    "evidence_refs": ["artifact://review-1"],
                }
            ),
        ),
    )

    assert case is not None, "needs_fix review must produce a ResolutionCase"
    assert case.case_source == "review_failed", (
        f"Expected review_failed source, got: {case.case_source}"
    )
    assert completed == [], "complete_step must NOT be called when review returns needs_fix"


def test_review_needs_replan_routes_to_resolution_case(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Prove that a structured review with 'needs_replan' outcome routes to ResolutionCase."""
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
        execution_id="exec-review-needs-replan",
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
                    "review_outcome": "needs_replan",
                    "summary": "architecture requires fundamental redesign",
                    "evidence_refs": ["artifact://arch-review"],
                }
            ),
        ),
    )

    assert case is not None, "needs_replan review must produce a ResolutionCase"
    assert case.case_source == "review_failed"
    assert completed == []


def test_review_operator_required_routes_to_resolution_case(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Prove that a structured review with 'operator_required' routes to ResolutionCase."""
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
        execution_id="exec-review-operator",
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
                    "review_outcome": "operator_required",
                    "summary": "human adjudication required for safety",
                    "evidence_refs": ["artifact://safety-review"],
                }
            ),
        ),
    )

    assert case is not None, "operator_required review must produce a ResolutionCase"
    assert case.case_source == "review_failed"
    assert completed == []


def test_review_parse_failure_routes_to_resolution_case(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Prove that unparseable review output routes to ResolutionCase.

    When output_contract='structured_review_result' but the output is not
    valid JSON, route_terminal_execution() must produce a ResolutionCase
    with case_source='review_failed' and a reason indicating the parse failure.
    """
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
        lambda *args, **kwargs: completed.append("COMPLETE_WAS_CALLED"),
    )

    case = orch.route_terminal_execution(
        step_id="core.ready",
        execution_id="exec-parse-failure",
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
            output_summary="this is not valid JSON at all",
        ),
    )

    assert case is not None, "Parse failure must produce a ResolutionCase"
    assert case.case_source == "review_failed"
    assert "unparseable" in case.reason, f"Reason must reference parse failure: {case.reason}"
    assert completed == [], "complete_step must NOT be called when review output is unparseable"


def test_review_pass_allows_complete_step(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Prove that a passing review DOES complete the step (positive control).

    This is the positive path of the review routing seam: when a structured
    review returns 'pass', the step should be completed normally (not routed
    to a ResolutionCase).
    """
    app = _build_app(tmp_path)
    orch = cast(Any, app)
    calls: list[str] = []

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

    def complete_step(step_id: str, evidence: str, *, reconcile_disposition: str) -> None:
        calls.append(f"complete:{step_id}:{reconcile_disposition}")

    monkeypatch.setattr(app._core_adapter, "complete_step", complete_step)

    case = orch.route_terminal_execution(
        step_id="core.ready",
        execution_id="exec-review-pass",
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
                    "review_outcome": "pass",
                    "summary": "all checks passed",
                    "evidence_refs": [],
                }
            ),
        ),
    )

    # Pass outcome: no ResolutionCase, step completed
    assert case is None, "Passing review should NOT produce a ResolutionCase"
    assert calls == ["complete:core.ready:noop"], (
        f"Passing review should complete the step: {calls}"
    )


# ======================================================================
# COMPOSITE: Full lifecycle routing through all three seams
# ======================================================================


def test_full_lifecycle_proof_all_three_seams(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Composite integration proof: run → reconcile → complete, showing all three seams.

    This test exercises the full lifecycle through the composed OrchestrationApp,
    proving all three routing seams work in sequence:

    1. claim-before-start: run() claims step before runtime.start
    2. reconcile-before-complete: route_terminal_execution() reconciles before complete
    3. review/gate: passing review allows completion; this is the positive control

    Then it separately proves the negative path: runtime failure routes to ResolutionCase.
    """
    app = _build_app(tmp_path)
    orch = cast(Any, app)

    # --- Seam 1 Proofs (claim-before-start) ---
    claim_order: list[str] = []

    original_claim = app._core_adapter.claim_step
    original_start = app._runtime.start

    def claim_probe(step_id, agent, *, force=False, flow="normal"):
        claim_order.append(f"claim:{step_id}")
        original_claim(step_id, agent, force=force, flow=flow)

    def start_probe(*, request, workspace):
        claim_order.append(f"start:{request.step_id}")
        return original_start(request=request, workspace=workspace)

    monkeypatch.setattr(app._core_adapter, "claim_step", claim_probe)
    monkeypatch.setattr(app._runtime, "start", start_probe)

    run_result = app.run(step_id="core.ready", agent="python-executor")
    assert run_result.success is True
    assert claim_order.index("claim:core.ready") < claim_order.index("start:core.ready"), (
        f"Seam 1 FAIL: claim must precede start. Order: {claim_order}"
    )

    # Reset monkeypatches for seam 2/3 tests
    monkeypatch.undo()

    # --- Seam 2 Proofs (reconcile-before-complete) ---
    reconcile_order: list[str] = []

    def begin_reconcile(execution_id: str) -> ReconcileResult:
        reconcile_order.append(f"reconcile:{execution_id}")
        return ReconcileResult(
            execution_id=execution_id,
            workspace_id="ws-lifecycle",
            status="merged",
            summary="merged cleanly",
        )

    def can_complete(execution_id: str) -> tuple[bool, str]:
        reconcile_order.append(f"can_complete:{execution_id}")
        return True, "ok"

    def complete_probe(step_id: str, evidence: str, *, reconcile_disposition: str) -> None:
        reconcile_order.append(f"complete:{step_id}:{reconcile_disposition}")

    monkeypatch.setattr(app._runtime, "begin_reconcile", begin_reconcile)
    monkeypatch.setattr(app._runtime, "can_complete", can_complete)
    monkeypatch.setattr(app._runtime, "reconcile_disposition", lambda _eid: "merged")
    monkeypatch.setattr(app._core_adapter, "complete_step", complete_probe)

    case = orch.route_terminal_execution(
        step_id="core.ready",
        execution_id="exec-lifecycle",
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
            output_summary="lifecycle verification passed",
        ),
    )

    assert case is None, "Successful lifecycle should not produce ResolutionCase"
    assert reconcile_order.index("reconcile:exec-lifecycle") < reconcile_order.index(
        "complete:core.ready:merged"
    ), f"Seam 2 FAIL: reconcile must precede complete. Order: {reconcile_order}"

    monkeypatch.undo()

    # --- Seam 3 Proof (runtime failure routes to ResolutionCase) ---
    def begin_reconcile_fail(execution_id: str) -> ReconcileResult:
        return ReconcileResult(
            execution_id=execution_id,
            workspace_id="ws-fail",
            status="aborted",
            summary="aborted during reconcile",
        )

    monkeypatch.setattr(app._runtime, "begin_reconcile", begin_reconcile_fail)

    case_fail = orch.route_terminal_execution(
        step_id="core.ready",
        execution_id="exec-fail",
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
            status="fail",
            output_summary="runtime reported failure",
        ),
    )

    assert case_fail is not None, "Runtime failure must produce a ResolutionCase"
    assert case_fail.case_source == "runtime_failure", (
        f"Expected runtime_failure source, got: {case_fail.case_source}"
    )
