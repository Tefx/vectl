"""Implementation tests for the orchestration core authority bridge.

Authority:
    docs/ORCHESTRATION-PLANE-IMPLEMENTATION-DESIGN.md section 3.6
    docs/ORCHESTRATION-PLANE-ISOLATION-SEMANTICS.md sections 6 and 10
"""

from __future__ import annotations

from pathlib import Path

from vectl.io import load_plan_definition, save_plan
from vectl.models import IsolationMode, Phase, PhaseStatus, Plan, Step, StepStatus
from vectl.orchestration.core_adapter import PlanCoreAdapter


def _build_plan_for_snapshot() -> Plan:
    return Plan(
        project="orchestration-core-authority",
        phases=[
            Phase(
                id="core",
                name="Core",
                steps=[
                    Step(id="core.ready", name="Ready"),
                    Step(id="core.blocked", name="Blocked", depends_on=["core.ready"]),
                ],
            )
        ],
    )


def test_snapshot_uses_authoritative_core_reads(tmp_path: Path) -> None:
    plan_path = tmp_path / "plan.yaml"
    save_plan(_build_plan_for_snapshot(), plan_path)
    adapter = PlanCoreAdapter(plan_path)

    snapshot = adapter.snapshot()

    assert snapshot.plan_complete is False
    assert snapshot.claimable_step_ids == ("core.ready",)
    assert snapshot.in_progress_step_ids == ()
    assert snapshot.blocked_step_ids == ("core.blocked",)
    assert snapshot.unresolved_reasons == ()


def test_snapshot_does_not_turn_evidence_guard_debt_into_scheduler_blocker(
    tmp_path: Path,
) -> None:
    """Evidence guard debt belongs to validation/gates, not frontier scheduling."""
    plan_path = tmp_path / "plan.yaml"
    save_plan(
        Plan(
            project="evidence-guard-scheduler-scope",
            phases=[
                Phase(
                    id="historical",
                    name="Historical",
                    status=PhaseStatus.DONE,
                    steps=[
                        Step(
                            id="historical.gate",
                            name="Historical gate",
                            status=StepStatus.DONE,
                            evidence="gate_open_allowed=false\nreview_outcome=NEEDS_REVISION",
                        )
                    ],
                ),
                Phase(
                    id="active",
                    name="Active",
                    steps=[Step(id="active.next", name="Next")],
                ),
            ],
        ),
        plan_path,
    )
    adapter = PlanCoreAdapter(plan_path)

    snapshot = adapter.snapshot()

    assert snapshot.claimable_step_ids == ("active.next",)
    assert snapshot.unresolved_reasons == ()


def test_step_isolation_surfaces_workspace_and_independent(tmp_path: Path) -> None:
    plan_path = tmp_path / "plan.yaml"
    plan = Plan(
        project="orchestration-core-authority",
        phases=[
            Phase(
                id="core",
                name="Core",
                steps=[
                    Step(id="core.workspace", name="Workspace", isolation=IsolationMode.WORKSPACE),
                    Step(
                        id="core.independent",
                        name="Independent",
                        isolation=IsolationMode.INDEPENDENT,
                    ),
                ],
            )
        ],
    )
    save_plan(plan, plan_path)
    adapter = PlanCoreAdapter(plan_path)

    assert adapter.step_isolation("core.workspace") == IsolationMode.WORKSPACE
    assert adapter.step_isolation("core.independent") == IsolationMode.INDEPENDENT


def test_claim_and_complete_mutations_flow_through_core_surface(tmp_path: Path) -> None:
    plan_path = tmp_path / "plan.yaml"
    save_plan(_build_plan_for_snapshot(), plan_path)
    adapter = PlanCoreAdapter(plan_path)

    adapter.claim_step("core.ready", "python-executor", flow="normal")
    adapter.complete_step("core.ready", "verified", reconcile_disposition="merged")

    updated_plan, _ = load_plan_definition(plan_path)
    found = updated_plan.find_step("core.ready")
    assert found is not None
    _, step = found

    assert step.status.value == "done"
    assert step.evidence == "[reconcile_disposition=merged] verified"


def test_claim_rejects_non_normal_flow_contract(tmp_path: Path) -> None:
    plan_path = tmp_path / "plan.yaml"
    save_plan(_build_plan_for_snapshot(), plan_path)
    adapter = PlanCoreAdapter(plan_path)

    try:
        adapter.claim_step("core.ready", "python-executor", flow="resolver")
    except ValueError as exc:
        assert "pinned to 'normal'" in str(exc)
    else:
        raise AssertionError("Expected ValueError for non-normal claim flow")


def test_load_step_data_for_dispatch_public_seam(tmp_path: Path) -> None:
    """Verify load_step_data_for_dispatch is the stable public boundary."""
    plan_path = tmp_path / "plan.yaml"
    plan = Plan(
        project="dispatch-boundary-test",
        phases=[
            Phase(
                id="core",
                name="Core",
                steps=[
                    Step(
                        id="core.test",
                        name="Test",
                        description="A test step",
                        verification="pass",
                        refs=("docs/SPEC.md",),
                        evidence_template="## Evidence\n",
                        verify="expected_red",
                        agent="python-executor",
                    ),
                ],
            )
        ],
    )
    save_plan(plan, plan_path)
    adapter = PlanCoreAdapter(plan_path)

    result = adapter.load_step_data_for_dispatch("core.test")

    assert result is not None
    assert result.step_id == "core.test"
    assert result.description == "A test step"
    assert result.verification == "pass"
    assert result.refs == ("docs/SPEC.md",)
    assert result.evidence_template == "## Evidence\n"
    assert result.verify == "expected_red"
    assert result.agent == "python-executor"


def test_load_step_data_for_dispatch_returns_none_for_missing_step(tmp_path: Path) -> None:
    """Verify load_step_data_for_dispatch returns None for unknown step."""
    plan_path = tmp_path / "plan.yaml"
    save_plan(_build_plan_for_snapshot(), plan_path)
    adapter = PlanCoreAdapter(plan_path)

    result = adapter.load_step_data_for_dispatch("core.nonexistent")

    assert result is None
