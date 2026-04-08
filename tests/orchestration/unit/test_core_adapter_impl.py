"""Implementation tests for the orchestration core authority bridge.

Authority:
    docs/ORCHESTRATION-PLANE-IMPLEMENTATION-DESIGN.md section 3.6
    docs/ORCHESTRATION-PLANE-ISOLATION-SEMANTICS.md sections 6 and 10
"""

from __future__ import annotations

from pathlib import Path

from vectl.io import load_plan_definition, save_plan
from vectl.models import IsolationMode, Phase, Plan, Step
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
