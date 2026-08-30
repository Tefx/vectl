"""Regression coverage for Plan narrative text neutrality."""

from __future__ import annotations

from hashlib import sha256
from pathlib import Path

import pytest
from typer.testing import CliRunner

from vectl.cli import app
from vectl.core import claim_step, complete_step, defer_step, reject_step, validate_plan
from vectl.core.diagnostics import (
    gate_integration_ambiguity_issues,
    phase_gate_validation_step_keys,
    review_phase_ids,
    review_step_keys,
    review_validation_plan,
)
from vectl.io import load_plan_definition, save_plan
from vectl.mcp_core_mutation_tools import vectl_mutate
from vectl.mcp_core_read_tools import vectl_show, vectl_status, vectl_validate
from vectl.mcp_render_tools import vectl_review
from vectl.models import Phase, PhaseStatus, Plan, Step, StepStatus

runner = CliRunner()


def _legacy_text() -> str:
    return (
        "FAIL PASS OPEN NEEDS_REVISION\n"
        "gate_open_allowed=false\n"
        "<vectl_gate_scope>{invalid json}</vectl_gate_scope>"
    )


def _textual_plan(*, text: str) -> Plan:
    return Plan(
        project="text-neutrality",
        phases=[
            Phase(
                id="legacy",
                name="Legacy history",
                status=PhaseStatus.DONE,
                steps=[
                    Step(
                        id="legacy.review",
                        name="Historical review",
                        status=StepStatus.DONE,
                        description=text,
                        verification=text,
                        evidence=text,
                    )
                ],
            ),
            Phase(
                id="active",
                name="Active work",
                status=PhaseStatus.PENDING,
                gate=text,
                steps=[Step(id="active.work", name="Current work")],
            ),
        ],
    )


@pytest.mark.parametrize(
    "legacy_scope_text",
    [
        "<vectl_gate_scope>{invalid json}</vectl_gate_scope>",
        "<vectl_gate_scope>{}</vectl_gate_scope><vectl_gate_scope>{}</vectl_gate_scope>",
        (
            "<vectl_gate_scope>"
            '{"schema_version":"vectl.gate.scope.v1","integrated_steps":[]}'
            "</vectl_gate_scope>"
        ),
        (
            "<vectl_gate_scope>"
            '{"schema_version":"vectl.gate.scope.v2","integrated_steps":["missing.step"]}'
            "</vectl_gate_scope>"
        ),
    ],
)
def test_validation_is_identical_when_only_narrative_text_changes(
    legacy_scope_text: str,
) -> None:
    baseline = _textual_plan(text="ordinary historical note")
    variant = _textual_plan(text=_legacy_text() + "\n" + legacy_scope_text)

    assert validate_plan(baseline) == []
    assert validate_plan(variant) == []
    assert validate_plan(
        variant,
        include_completed_evidence_guard=False,
        completed_evidence_scope="active_dependency_closure",
    ) == []


@pytest.mark.parametrize("include_guard", [True, False])
@pytest.mark.parametrize("scope", ["all", "active_dependency_closure"])
def test_legacy_validation_options_are_inert(
    include_guard: bool, scope: str
) -> None:
    plan = _textual_plan(text=_legacy_text())

    assert validate_plan(
        plan,
        include_completed_evidence_guard=include_guard,
        completed_evidence_scope=scope,
    ) == []


def test_unknown_legacy_validation_scope_still_fails() -> None:
    with pytest.raises(ValueError, match="Unknown completed evidence scope"):
        validate_plan(
            _textual_plan(text="ordinary note"),
            completed_evidence_scope="unknown",  # type: ignore[arg-type]
        )


def test_legacy_gate_scope_cannot_expand_scoped_review_or_create_ambiguity() -> None:
    gate_scope = (
        "<vectl_gate_scope>"
        '{"schema_version":"vectl.gate.scope.v2","integrated_steps":["target.review"]}'
        "</vectl_gate_scope>"
    )
    plan = Plan(
        project="inert-gate-scope",
        phases=[
            Phase(
                id="other",
                name="Other",
                status=PhaseStatus.DONE,
                steps=[
                    Step(
                        id="target.review",
                        name="Exact legacy selector",
                        status=StepStatus.DONE,
                        evidence="FAIL",
                    )
                ],
            ),
            Phase(
                id="target",
                name="Target",
                status=PhaseStatus.PENDING,
                gate=gate_scope,
                steps=[
                    Step(id="review", name="Qualified legacy selector"),
                    Step(id="target.gate", name="Gate", description=gate_scope),
                ],
            ),
        ],
    )

    assert validate_plan(plan) == []
    assert review_phase_ids(plan, "active_dependency_closure", None) == {"target"}
    assert review_step_keys(plan, "active_dependency_closure", None) == {
        ("target", "review"),
        ("target", "target.gate"),
    }
    assert [phase.id for phase in review_validation_plan(plan, "phase", "target").phases] == [
        "target"
    ]
    assert phase_gate_validation_step_keys(plan, "target") == {
        ("target", "review"),
        ("target", "target.gate"),
    }
    assert gate_integration_ambiguity_issues(plan, {"target"}) == []


def test_cli_and_mcp_text_surfaces_preserve_plan_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plan = Plan(
        project="read-only-text",
        phases=[
            Phase(
                id="gate",
                name="Gate",
                status=PhaseStatus.DONE,
                gate=_legacy_text(),
                steps=[
                    Step(
                        id="gate.review",
                        name="Review",
                        status=StepStatus.DONE,
                        description=_legacy_text(),
                        verification=_legacy_text(),
                        evidence=_legacy_text(),
                    )
                ],
            )
        ],
    )
    path = tmp_path / "plan.yaml"
    save_plan(plan, path)
    before = sha256(path.read_bytes()).hexdigest()

    for command in (
        ["status", "--plan", str(path)],
        ["show", "gate.review", "--plan", str(path)],
        ["validate", "--plan", str(path)],
        ["review", "--plan", str(path)],
        ["gate-check", "gate", "--plan", str(path)],
    ):
        result = runner.invoke(app, command)
        assert result.exit_code == 0, result.output

    monkeypatch.setenv("VECTL_PLAN_PATH", str(path))
    assert "Gate" in vectl_status()
    assert "FAIL PASS OPEN NEEDS_REVISION" in vectl_show("gate.review")
    assert "0 errors, 0 warnings" in vectl_validate()
    for result in (
        vectl_review(),
        vectl_review(scope="phase", phase_id="gate"),
        vectl_review(scope="active_dependency_closure"),
    ):
        assert "current_error_count: 0" in result

    assert sha256(path.read_bytes()).hexdigest() == before


def test_lifecycle_and_mcp_mutation_preserve_unrelated_legacy_text(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plan = _textual_plan(text=_legacy_text())
    legacy_before = plan.find_step("legacy.review")
    assert legacy_before is not None
    expected_legacy = legacy_before[1].model_dump()

    plan, _ = claim_step(plan, "active.work", "worker")
    plan = defer_step(plan, "active.work")
    plan, _ = claim_step(plan, "active.work", "worker")
    plan = complete_step(plan, "active.work", "FAIL remains ordinary completion evidence")
    plan = reject_step(plan, "active.work", "requires rework", "reviewer")

    legacy_after = plan.find_step("legacy.review")
    assert legacy_after is not None
    assert legacy_after[1].model_dump() == expected_legacy
    assert validate_plan(plan) == []

    path = tmp_path / "plan.yaml"
    save_plan(_textual_plan(text=_legacy_text()), path)
    monkeypatch.setenv("VECTL_PLAN_PATH", str(path))
    result = vectl_mutate(
        action="edit-step",
        step_id="active.work",
        description="Updated active work only",
    )
    assert "mutation_receipt:" in result

    persisted, _ = load_plan_definition(path)
    legacy_persisted = persisted.find_step("legacy.review")
    assert legacy_persisted is not None
    assert legacy_persisted[1].model_dump() == expected_legacy
