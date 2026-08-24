"""RFC contract tests for atomic multi-operation MCP Plan mutation."""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import subprocess
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastmcp import Client
from fastmcp.client.transports import StdioTransport
from pydantic import ValidationError
from pydantic_core import PydanticCustomError

import vectl.io as io_module
import vectl.mcp_core_common as mcp_common
import vectl.mcp_core_mutation_tools as mutation_module
from vectl.io import load_plan_definition, save_plan
from vectl.mcp_server import vectl_mutate as mutate_tool
from vectl.models import Phase, PhaseStatus, Plan, PlanValidationIssue, Step, StepStatus

vectl_mutate = mutate_tool.fn  # type: ignore[attr-defined]


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=repo, capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def _mutation_receipt(result: str) -> dict[str, object]:
    marker = "mutation_receipt: "
    line = next(line for line in result.splitlines() if line.startswith(marker))
    payload = json.loads(line.removeprefix(marker))
    assert set(payload) == {
        "schema_version",
        "plan_applied",
        "base_sha256",
        "candidate_sha256",
        "persisted_sha256",
        "admission_status",
        "git_attempted",
        "git_committed",
    }
    return payload


@pytest.fixture
def git_plan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[tuple[Path, Path, Path]]:
    """Create an isolated Git-backed Plan and claims store."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "batch-test@example.invalid")
    _git(repo, "config", "user.name", "Batch Test")

    plan_path = repo / "plan.yaml"
    plan = Plan(
        project="atomic-batch-test",
        phases=[
            Phase(
                id="foundation",
                name="Foundation",
                status=PhaseStatus.PENDING,
                steps=[Step(id="foundation.seed", name="Seed", status=StepStatus.PENDING)],
            ),
            Phase(
                id="delivery",
                name="Delivery",
                status=PhaseStatus.LOCKED,
                depends_on=["foundation"],
                steps=[],
            ),
        ],
    )
    save_plan(plan, plan_path)
    claims_path = repo / ".git" / "vectl" / "claims.json"
    claims_path.parent.mkdir(parents=True)
    claims_path.write_text(json.dumps({"claims": []}, sort_keys=True), encoding="utf-8")
    _git(repo, "add", "plan.yaml")
    _git(repo, "commit", "-q", "-m", "seed")

    monkeypatch.setenv("VECTL_PLAN_PATH", str(plan_path))
    yield repo, plan_path, claims_path


def _state(
    repo: Path, plan_path: Path, claims_path: Path
) -> tuple[bytes, str, bytes, str, list[str]]:
    plan, _ = load_plan_definition(plan_path)
    plan_bytes = plan_path.read_bytes()
    return (
        plan_bytes,
        hashlib.sha256(plan_bytes).hexdigest(),
        claims_path.read_bytes(),
        _git(repo, "rev-parse", "HEAD"),
        [phase.status.value for phase in plan.phases],
    )


def test_validation_issue_equality_includes_historical_provenance() -> None:
    current = PlanValidationIssue("missing ref", is_warning=True)
    historical = PlanValidationIssue(
        "missing ref", is_warning=True, is_historical=True
    )

    assert current != historical


def _successful_requests() -> list[dict[str, Any]]:
    return [
        {
            "action": "add-phase",
            "phase_id": "provider-recovery",
            "name": "Provider Recovery",
            "depends_on": [],
            "gate": "Recovery verified",
        },
        {
            "action": "add-steps",
            "phase_id": "provider-recovery",
            "steps": [
                {"id": "provider-recovery.probe", "name": "Probe"},
                {
                    "id": "provider-recovery.repair",
                    "name": "Repair",
                    "depends_on": ["probe"],
                },
                {
                    "id": "provider-recovery.verify",
                    "name": "Verify",
                    "depends_on": ["repair"],
                },
            ],
        },
        {
            "action": "edit-phase",
            "phase_id": "provider-recovery",
            "context": "Ordered atomic repair",
        },
        {
            "action": "edit-step",
            "step_id": "provider-recovery.verify",
            "description": "Verify the repaired provider",
        },
    ]


_README_EXAMPLE_START = "<!-- vectl-mutate-batch-example:start -->"
_README_EXAMPLE_END = "<!-- vectl-mutate-batch-example:end -->"
_EXPECTED_BATCH_FIELDS = {
    "add-phase": {"action", "phase_id", "name", "depends_on", "gate"},
    "add-step": {
        "action",
        "phase_id",
        "step_id",
        "name",
        "description",
        "depends_on",
        "verification",
        "evidence_template",
        "refs",
        "status",
        "evidence",
        "skipped_reason",
        "agent",
    },
    "add-steps": {"action", "phase_id", "steps"},
    "edit-phase": {"action", "phase_id", "name", "context", "gate", "depends_on"},
    "edit-step": {
        "action",
        "step_id",
        "new_step_id",
        "name",
        "description",
        "depends_on",
        "verification",
        "evidence_template",
        "agent",
        "add_refs",
        "remove_refs",
        "refs",
    },
    "remove-step": {"action", "step_id", "force"},
    "move-step": {"action", "step_id", "target_phase"},
    "edit-plan": {"action", "project_guidance", "strategy_ref", "plan_context"},
}
_EXPECTED_BATCH_REQUIRED_FIELDS = {
    "add-phase": {"action", "phase_id", "name"},
    "add-step": {"action", "phase_id", "step_id", "name"},
    "add-steps": {"action", "phase_id", "steps"},
    "edit-phase": {"action", "phase_id"},
    "edit-step": {"action", "step_id"},
    "remove-step": {"action", "step_id"},
    "move-step": {"action", "step_id", "target_phase"},
    "edit-plan": {"action"},
}
_EXPECTED_BULK_STEP_FIELDS = {
    "id",
    "name",
    "description",
    "desc",
    "depends_on",
    "after",
    "verification",
    "verify",
    "refs",
    "status",
    "evidence",
    "skipped_reason",
    "agent",
}


def _read_readme_batch_example() -> dict[str, Any]:
    readme = (Path(__file__).resolve().parents[1] / "README.md").read_text(encoding="utf-8")
    assert _README_EXAMPLE_START in readme
    assert _README_EXAMPLE_END in readme
    example = readme.split(_README_EXAMPLE_START, 1)[1].split(_README_EXAMPLE_END, 1)[0]
    payload = example.split("```json", 1)[1].split("```", 1)[0]
    return json.loads(payload)


def _tool_result_text(result: Any) -> str:
    if result.data is not None:
        return str(result.data)
    return "\n".join(
        content.text for content in result.content if hasattr(content, "text")
    )


async def _registered_schema_and_calls(
    plan_path: Path, payload: dict[str, Any]
) -> tuple[dict[str, Any], str, str]:
    transport = StdioTransport(
        command="uv",
        args=["run", "vectl", "mcp"],
        cwd=str(Path(__file__).resolve().parents[1]),
        env={"VECTL_PLAN_PATH": str(plan_path)},
    )
    async with Client(transport) as client:
        tools = await client.list_tools()
        mutate = next(tool for tool in tools if tool.name == "vectl_mutate")
        success = await client.call_tool("vectl_mutate", payload)
        invalid = await client.call_tool(
            "vectl_mutate",
            {
                "requests": [
                    {
                        "action": "add-phase",
                        "phase_id": "invalid",
                        "step_id": "invalid",
                        "name": "Invalid",
                    }
                ]
            },
        )
    return (
        mutate.inputSchema,
        _tool_result_text(success),
        _tool_result_text(invalid),
    )


def test_single_add_step_persists_refs(git_plan: tuple[Path, Path, Path]) -> None:
    _, plan_path, _ = git_plan

    result = vectl_mutate(
        action="add-step",
        phase_id="foundation",
        step_id="foundation.with-refs",
        name="With refs",
        refs=["README.md", "src/vectl/mcp_core_mutation_tools.py"],
    )

    assert "Added step" in result
    plan, _ = load_plan_definition(plan_path)
    found = plan.find_step("foundation.with-refs")
    assert found is not None
    assert found[1].refs == ["README.md", "src/vectl/mcp_core_mutation_tools.py"]


def test_batch_add_step_persists_refs(git_plan: tuple[Path, Path, Path]) -> None:
    _, plan_path, _ = git_plan

    result = vectl_mutate(
        requests=[
            {
                "action": "add-step",
                "phase_id": "foundation",
                "step_id": "foundation.batch-refs",
                "name": "Batch refs",
                "refs": ["tests/test_mcp_mutate_batch.py"],
            }
        ]
    )

    assert "requests_applied: 1" in result
    plan, _ = load_plan_definition(plan_path)
    found = plan.find_step("foundation.batch-refs")
    assert found is not None
    assert found[1].refs == ["tests/test_mcp_mutate_batch.py"]


@pytest.mark.parametrize(
    ("raw_request", "hint"),
    [
        (
            {
                "action": "add-phase",
                "phase_id": "demo",
                "step_id": "demo",
                "name": "Demo",
            },
            "Use 'phase_id' as the explicit ID for add-phase",
        ),
        (
            {
                "action": "add-step",
                "phase_id": "foundation",
                "step_id": "foundation.demo",
                "name": "Demo",
                "add_refs": ["README.md"],
            },
            "Use 'refs' when creating a step",
        ),
    ],
)
def test_unknown_batch_fields_are_actionable_before_load(
    git_plan: tuple[Path, Path, Path],
    monkeypatch: pytest.MonkeyPatch,
    raw_request: dict[str, Any],
    hint: str,
) -> None:
    called = False

    def fail_load() -> tuple[Plan, str]:
        nonlocal called
        called = True
        raise AssertionError("invalid fields must fail before load")

    monkeypatch.setattr(mutation_module, "_load", fail_load)
    result = vectl_mutate(requests=[raw_request])

    assert "request[0]" in result
    assert "allowed fields:" in result
    assert hint in result
    assert called is False


def test_registered_schema_and_readme_example_match_runtime(
    git_plan: tuple[Path, Path, Path],
) -> None:
    _, plan_path, _ = git_plan
    payload = _read_readme_batch_example()

    schema, result, invalid_result = asyncio.run(
        _registered_schema_and_calls(plan_path, payload)
    )

    request_shapes = schema["properties"]["requests"]["anyOf"]
    request_array = next(shape for shape in request_shapes if shape.get("type") == "array")
    item_schema = request_array["items"]
    branches = {
        branch["properties"]["action"]["const"]: branch
        for branch in item_schema["oneOf"]
    }
    assert item_schema["discriminator"]["propertyName"] == "action"
    assert set(branches) == set(_EXPECTED_BATCH_FIELDS)
    for action, expected_fields in _EXPECTED_BATCH_FIELDS.items():
        branch = branches[action]
        assert branch["additionalProperties"] is False
        assert set(branch["properties"]) == expected_fields
        assert set(branch["required"]) == _EXPECTED_BATCH_REQUIRED_FIELDS[action]
    bulk_steps = branches["add-steps"]["properties"]["steps"]
    assert bulk_steps["items"]["additionalProperties"] is False
    assert set(bulk_steps["items"]["properties"]) == _EXPECTED_BULK_STEP_FIELDS
    assert set(bulk_steps["items"]["required"]) == {"id", "name"}

    assert "requests_applied: 3" in result
    assert "request[0] add-phase" in invalid_result
    assert "allowed fields:" in invalid_result
    assert "Use 'phase_id' as the explicit ID for add-phase" in invalid_result
    plan, _ = load_plan_definition(plan_path)
    found = plan.find_step("mcp-contract-example.implement")
    assert found is not None
    assert found[1].refs == ["src/vectl/mcp_core_mutation_tools.py", "README.md"]


def test_mutate_docstring_names_explicit_ids_and_add_step_refs() -> None:
    docstring = inspect.getdoc(vectl_mutate) or ""

    assert "``add-phase`` uses ``phase_id``" in docstring
    assert "``add-step`` uses ``step_id``" in docstring
    assert "for add-step/add-phase" not in docstring
    assert "Refs for add-step" in docstring


def test_ordered_batch_persists_once_with_exact_hash_and_results(
    git_plan: tuple[Path, Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, plan_path, _ = git_plan
    counts = {"load": 0, "recalc": 0, "save": 0, "commit": 0}

    original_load = mutation_module._load
    original_recalc = mutation_module.recalc_lock_status
    original_save = mutation_module._save_admitted_plan_receipt
    original_commit = io_module._git_commit_plan

    def counted_load() -> tuple[Plan, str]:
        counts["load"] += 1
        return original_load()

    def counted_recalc(plan: Plan) -> list[str]:
        counts["recalc"] += 1
        return original_recalc(plan)

    def counted_save(*args: Any, **kwargs: Any) -> tuple[dict[str, object], str]:
        counts["save"] += 1
        return original_save(*args, **kwargs)

    def counted_commit(path: Path, message: str) -> bool:
        counts["commit"] += 1
        return bool(original_commit(path, message))

    monkeypatch.setattr(mutation_module, "_load", counted_load)
    monkeypatch.setattr(mutation_module, "recalc_lock_status", counted_recalc)
    monkeypatch.setattr(mutation_module, "_save_admitted_plan_receipt", counted_save)
    monkeypatch.setattr(io_module, "_git_commit_plan", counted_commit)

    result = vectl_mutate(requests=_successful_requests())

    assert counts == {"load": 1, "recalc": 1, "save": 1, "commit": 1}
    assert result.startswith("## Plan mutation batch")
    assert [result.index(f"request[{i}]") for i in range(4)] == sorted(
        result.index(f"request[{i}]") for i in range(4)
    )
    assert "requests_applied: 4" in result
    saved_hash = hashlib.sha256(plan_path.read_bytes()).hexdigest()
    assert f"plan_hash: {saved_hash}" in result
    assert _git(repo, "rev-list", "--count", "HEAD") == "2"

    plan, _ = load_plan_definition(plan_path)
    phase = plan.find_phase("provider-recovery")
    assert phase is not None
    assert phase.context == "Ordered atomic repair"
    assert [step.id for step in phase.steps] == [
        "provider-recovery.probe",
        "provider-recovery.repair",
        "provider-recovery.verify",
    ]
    assert phase.steps[-1].description == "Verify the repaired provider"


@pytest.mark.parametrize(
    ("kwargs", "identity"),
    [
        ({}, "input_shape"),
        ({"action": "edit-plan", "requests": _successful_requests()}, "input_shape"),
        ({"requests": []}, "input_shape"),
        ({"requests": "bad"}, "input_shape"),
        ({"requests": _successful_requests(), "name": "mixed"}, "input_shape"),
        (
            {
                "requests": [
                    {"action": "add-phase", "phase_id": "x", "name": "X", "bogus": 1}
                ]
            },
            "request[0]",
        ),
        ({"requests": [{"action": "add-phase", "name": "No explicit id"}]}, "request[0]"),
        (
            {
                "requests": [
                    {"action": "add-step", "phase_id": "foundation", "name": "No id"}
                ]
            },
            "request[0]",
        ),
        (
            {
                "requests": [
                    {
                        "action": "add-steps",
                        "phase_id": "foundation",
                        "steps": [{"name": "No id"}],
                    }
                ]
            },
            "request[0]",
        ),
        ({"requests": [{"action": "edit-plan", "requests": []}]}, "request[0]"),
    ],
)
def test_invalid_batch_shape_fails_before_load(
    git_plan: tuple[Path, Path, Path],
    monkeypatch: pytest.MonkeyPatch,
    kwargs: dict[str, Any],
    identity: str,
) -> None:
    called = False

    def fail_load() -> tuple[Plan, str]:
        nonlocal called
        called = True
        raise AssertionError("invalid shape must fail before load")

    monkeypatch.setattr(mutation_module, "_load", fail_load)
    result = vectl_mutate(**kwargs)
    assert "Error" in result
    assert identity in result
    assert called is False


def test_request_failure_discards_working_copy_and_all_persistent_state(
    git_plan: tuple[Path, Path, Path]
) -> None:
    repo, plan_path, claims_path = git_plan
    before = _state(repo, plan_path, claims_path)
    requests = _successful_requests()
    requests.insert(
        3,
        {
            "action": "add-step",
            "phase_id": "provider-recovery",
            "step_id": "provider-recovery.bad",
            "name": "Bad dependency",
            "depends_on": ["provider-recovery.missing"],
        },
    )

    result = vectl_mutate(requests=requests)

    assert "Error" in result and "request[3]" in result
    assert _state(repo, plan_path, claims_path) == before


def _invalid_candidate_with_three_blockers() -> Plan:
    return Plan(
        project="invalid-candidate",
        phases=[
            Phase(
                id="invalid-phase",
                name="Invalid phase",
                status=PhaseStatus.LOCKED,
                depends_on=["missing-phase"],
                steps=[
                    Step(
                        id="invalid-step",
                        name="Invalid step",
                        status=StepStatus.CLAIMED,
                        claimed_by="worker",
                        depends_on=["missing-step"],
                    )
                ],
            )
        ],
    )


def test_final_validation_reports_all_real_blockers_without_persistence_or_commit(
    git_plan: tuple[Path, Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, plan_path, claims_path = git_plan
    before = _state(repo, plan_path, claims_path)
    assert before[1] == hashlib.sha256(before[0]).hexdigest()
    original_apply = mutation_module._apply_mutation_request
    calls = 0

    def inject_invalid_candidate(
        plan: Plan, request: dict[str, Any]
    ) -> tuple[Plan, str]:
        nonlocal calls
        calls += 1
        if calls == 1:
            return _invalid_candidate_with_three_blockers(), "injected invalid candidate"
        return original_apply(plan, request)

    monkeypatch.setattr(mutation_module, "_apply_mutation_request", inject_invalid_candidate)
    result = vectl_mutate(
        requests=[{"action": "edit-plan", "plan_context": "candidate"}]
    )

    assert "blocking_issue_count: 3" in result
    assert "rendered_issue_count: 3" in result
    assert "issues_truncated: false" in result
    blockers = (
        "Phase 'invalid-phase' depends on unknown phase 'missing-phase'",
        "Step 'invalid-step' depends on unknown step 'missing-step' (phase 'invalid-phase')",
        "Step 'invalid-step' has status 'claimed' but phase 'invalid-phase' is locked",
    )
    assert [result.index(text) for text in blockers] == sorted(
        result.index(text) for text in blockers
    )
    assert _state(repo, plan_path, claims_path) == before

    succeeded = vectl_mutate(
        requests=[{"action": "edit-plan", "plan_context": "corrected candidate"}]
    )
    assert "requests_applied: 1" in succeeded
    plan, _ = load_plan_definition(plan_path)
    assert plan.context == "corrected candidate"


def test_final_validation_bound_reports_total_and_truncation(
    git_plan: tuple[Path, Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    def inject_invalid_candidate(
        plan: Plan, request: dict[str, Any]
    ) -> tuple[Plan, str]:
        return _invalid_candidate_with_three_blockers(), "injected invalid candidate"

    monkeypatch.setattr(mutation_module, "_apply_mutation_request", inject_invalid_candidate)
    result = vectl_mutate(
        requests=[{"action": "edit-plan", "plan_context": "candidate"}],
        max_issues=2,
    )

    assert "blocking_issue_count: 3" in result
    assert "rendered_issue_count: 2" in result
    assert "issues_truncated: true" in result
    assert "depends on unknown phase" in result
    assert "depends on unknown step" in result
    assert "has status 'claimed'" not in result
    assert "validation_source: structural" in result


def _candidate_with_canonical_model_errors(plan: Plan) -> Plan:
    candidate = plan.model_copy(deep=True)
    candidate.project = 7  # type: ignore[assignment]
    candidate.default_affinity = "invalid"  # type: ignore[assignment]
    candidate.phases[0].status = "retired"  # type: ignore[assignment]
    candidate.phases[0].steps[0].refs = [1, 2]  # type: ignore[list-item]
    return candidate


def _inject_canonical_model_errors(
    plan: Plan, request: dict[str, Any]
) -> tuple[Plan, str]:
    del request
    return _candidate_with_canonical_model_errors(plan), "injected model errors"


def test_single_action_canonical_model_errors_are_bounded_and_pre_save(
    git_plan: tuple[Path, Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, plan_path, claims_path = git_plan
    before = _state(repo, plan_path, claims_path)
    monkeypatch.setattr(
        mutation_module, "_apply_mutation_request", _inject_canonical_model_errors
    )

    result = vectl_mutate(
        action="edit-plan",
        plan_context="invalid candidate",
        max_issues=1,
    )

    assert "**Error:** stage=final_validation" in result
    assert "validation_source: canonical_model" in result
    assert "blocking_issue_count: 5" in result
    assert "rendered_issue_count: 1" in result
    assert "issues_truncated: true" in result
    assert "canonical_model.project" in result
    assert "canonical_model.default_affinity" not in result
    assert "errors.pydantic.dev" not in result
    assert "validation errors for Plan" not in result
    assert _state(repo, plan_path, claims_path) == before


def test_batch_canonical_model_errors_are_bounded_and_pre_save(
    git_plan: tuple[Path, Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, plan_path, claims_path = git_plan
    before = _state(repo, plan_path, claims_path)
    monkeypatch.setattr(
        mutation_module, "_apply_mutation_request", _inject_canonical_model_errors
    )

    result = vectl_mutate(
        requests=[{"action": "edit-plan", "plan_context": "invalid candidate"}],
        max_issues=2,
    )

    assert "validation_source: canonical_model" in result
    assert "blocking_issue_count: 5" in result
    assert "rendered_issue_count: 2" in result
    assert "issues_truncated: true" in result
    assert "canonical_model.project" in result
    assert "canonical_model.default_affinity" in result
    assert "canonical_model.phases[0].status" not in result
    assert _state(repo, plan_path, claims_path) == before


def test_canonical_model_errors_are_complete_when_unbounded(
    git_plan: tuple[Path, Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, plan_path, claims_path = git_plan
    before = _state(repo, plan_path, claims_path)
    monkeypatch.setattr(
        mutation_module, "_apply_mutation_request", _inject_canonical_model_errors
    )

    result = vectl_mutate(
        requests=[{"action": "edit-plan", "plan_context": "invalid candidate"}]
    )

    assert "validation_source: canonical_model" in result
    assert "blocking_issue_count: 5" in result
    assert "rendered_issue_count: 5" in result
    assert "issues_truncated: false" in result
    for location in (
        "canonical_model.project",
        "canonical_model.default_affinity",
        "canonical_model.phases[0].status",
        "canonical_model.phases[0].steps[0].refs[0]",
        "canonical_model.phases[0].steps[0].refs[1]",
    ):
        assert location in result
    assert _state(repo, plan_path, claims_path) == before


@pytest.mark.parametrize("batch_mode", (False, True))
def test_custom_canonical_error_location_type_message_and_input_are_fail_closed(
    git_plan: tuple[Path, Path, Path],
    monkeypatch: pytest.MonkeyPatch,
    batch_mode: bool,
) -> None:
    repo, plan_path, claims_path = git_plan
    before = _state(repo, plan_path, claims_path)
    custom_error = ValidationError.from_exception_data(
        "Plan",
        [
            {
                "type": PydanticCustomError(
                    "RAW_TYPE_SECRET",
                    "RAW_MESSAGE_SECRET {value}",
                    {"value": "RAW_CONTEXT_SECRET"},
                ),
                "loc": (
                    "phases",
                    0,
                    "steps",
                    0,
                    "RAW_LOCATION_SECRET",
                ),
                "input": "RAW_INPUT_SECRET",
            }
        ],
    )
    original_model_validate = Plan.model_validate

    def fail_triggered_candidate(
        cls: type[Plan], payload: object, *args: object, **kwargs: object
    ) -> Plan:
        if isinstance(payload, dict) and payload.get("context") == "custom-error-trigger":
            raise custom_error
        return original_model_validate(payload, *args, **kwargs)

    monkeypatch.setattr(Plan, "model_validate", classmethod(fail_triggered_candidate))
    result = (
        vectl_mutate(
            requests=[
                {"action": "edit-plan", "plan_context": "custom-error-trigger"}
            ]
        )
        if batch_mode
        else vectl_mutate(
            action="edit-plan", plan_context="custom-error-trigger"
        )
    )

    assert "validation_source: canonical_model" in result
    assert "blocking_issue_count: 1" in result
    assert "canonical_model.phases[0].steps[0].<field>" in result
    assert "canonical model constraint violated [validation_error]" in result
    for secret in (
        "RAW_LOCATION_SECRET",
        "RAW_TYPE_SECRET",
        "RAW_MESSAGE_SECRET",
        "RAW_CONTEXT_SECRET",
        "RAW_INPUT_SECRET",
    ):
        assert secret not in result
    assert _state(repo, plan_path, claims_path) == before


@pytest.mark.parametrize("batch_mode", (False, True))
def test_validation_error_errors_failure_returns_fixed_zero_write_diagnostic(
    git_plan: tuple[Path, Path, Path],
    monkeypatch: pytest.MonkeyPatch,
    batch_mode: bool,
) -> None:
    repo, plan_path, claims_path = git_plan
    before = _state(repo, plan_path, claims_path)
    raw_exception = "RAW_ERRORS_SECRET\nrendered_issue_count: 999"
    original_model_validate = Plan.model_validate

    class FailingErrorsValidationError(ValidationError):
        def errors(self, *args: object, **kwargs: object) -> list[dict[str, object]]:
            del args, kwargs
            raise RuntimeError(raw_exception)

    def fail_triggered_candidate(
        cls: type[Plan], payload: object, *args: object, **kwargs: object
    ) -> Plan:
        if isinstance(payload, dict) and payload.get("context") == "errors-failure-trigger":
            raise FailingErrorsValidationError("Plan", [])
        return original_model_validate(payload, *args, **kwargs)

    monkeypatch.setattr(Plan, "model_validate", classmethod(fail_triggered_candidate))
    result = (
        vectl_mutate(
            requests=[
                {"action": "edit-plan", "plan_context": "errors-failure-trigger"}
            ]
        )
        if batch_mode
        else vectl_mutate(
            action="edit-plan", plan_context="errors-failure-trigger"
        )
    )

    assert "validation_source: canonical_model" in result
    assert "blocking_issue_count: 1" in result
    assert "issue_values_truncated: false" in result
    assert "canonical validation could not complete [unexpected_validation_error]" in result
    assert raw_exception not in result
    assert "RAW_ERRORS_SECRET" not in result
    assert _state(repo, plan_path, claims_path) == before


@pytest.mark.parametrize("batch_mode", (False, True))
def test_step_validator_raw_id_is_not_copied_into_canonical_diagnostics(
    git_plan: tuple[Path, Path, Path],
    monkeypatch: pytest.MonkeyPatch,
    batch_mode: bool,
) -> None:
    repo, plan_path, claims_path = git_plan
    before = _state(repo, plan_path, claims_path)
    raw_id = (
        "raw-step-id-"
        + "x" * 10_000
        + "\nrendered_issue_count: 999\nissues_truncated: false"
    )

    def inject_step_validator_error(
        plan: Plan, request: dict[str, Any]
    ) -> tuple[Plan, str]:
        del request
        candidate = plan.model_copy(deep=True)
        step = candidate.phases[0].steps[0]
        step.id = raw_id
        step.status = StepStatus.SKIPPED
        step.skipped_reason = None
        return candidate, "injected Step validator error"

    monkeypatch.setattr(
        mutation_module, "_apply_mutation_request", inject_step_validator_error
    )
    result = (
        vectl_mutate(
            requests=[{"action": "edit-plan", "plan_context": "candidate"}],
            max_issues=1,
        )
        if batch_mode
        else vectl_mutate(
            action="edit-plan", plan_context="candidate", max_issues=1
        )
    )

    assert "validation_source: canonical_model" in result
    assert "blocking_issue_count: 1" in result
    assert "rendered_issue_count: 1" in result
    assert "issues_truncated: false" in result
    assert "issue_value_codepoint_limit: 512" in result
    assert "issue_values_truncated: false" in result
    assert "canonical_model.phases[0].steps[0]" in result
    assert "canonical model constraint violated [value_error]" in result
    assert raw_id not in result
    assert "raw-step-id" not in result
    assert "rendered_issue_count: 999" not in result.splitlines()
    assert _state(repo, plan_path, claims_path) == before


@pytest.mark.parametrize("batch_mode", (False, True))
def test_huge_structural_diagnostic_is_control_safe_and_value_bounded(
    git_plan: tuple[Path, Path, Path],
    monkeypatch: pytest.MonkeyPatch,
    batch_mode: bool,
) -> None:
    repo, plan_path, claims_path = git_plan
    before = _state(repo, plan_path, claims_path)
    raw_tail = "z" * 10_000
    forged = (
        "\nrendered_issue_count: 999\nissues_truncated: false\n"
        "\u2028blocking_issue_count: 999\u2029issue_values_truncated: false"
    )

    def huge_issue(*args: object, **kwargs: object) -> list[PlanValidationIssue]:
        del args, kwargs
        return [PlanValidationIssue(f"huge structural value {forged}{raw_tail}")]

    monkeypatch.setattr(mutation_module, "validate_plan", huge_issue)
    result = (
        vectl_mutate(
            requests=[{"action": "edit-plan", "plan_context": "candidate"}],
            max_issues=1,
        )
        if batch_mode
        else vectl_mutate(
            action="edit-plan", plan_context="candidate", max_issues=1
        )
    )

    assert "validation_source: structural" in result
    assert "blocking_issue_count: 1" in result
    assert "rendered_issue_count: 1" in result
    assert "issues_truncated: false" in result
    assert "issue_value_codepoint_limit: 512" in result
    assert "issue_values_truncated: true" in result
    assert "rendered_issue_count: 999" not in result.splitlines()
    assert "blocking_issue_count: 999" not in result.splitlines()
    assert raw_tail not in result
    issue_line = next(line for line in result.splitlines() if line.startswith("- ERROR:"))
    assert len(issue_line.removeprefix("- ERROR: ")) == 512
    assert _state(repo, plan_path, claims_path) == before


@pytest.mark.parametrize("batch_mode", (False, True))
def test_unexpected_canonical_exception_returns_bounded_zero_write_failure(
    git_plan: tuple[Path, Path, Path],
    monkeypatch: pytest.MonkeyPatch,
    batch_mode: bool,
) -> None:
    repo, plan_path, claims_path = git_plan
    before = _state(repo, plan_path, claims_path)
    original_model_validate = Plan.model_validate
    raw_exception = "runtime raw candidate\nrendered_issue_count: 999" + "r" * 10_000

    def fail_triggered_candidate(
        cls: type[Plan], payload: object, *args: object, **kwargs: object
    ) -> Plan:
        if isinstance(payload, dict) and payload.get("context") == "runtime-trigger":
            raise RuntimeError(raw_exception)
        return original_model_validate(payload, *args, **kwargs)

    monkeypatch.setattr(Plan, "model_validate", classmethod(fail_triggered_candidate))
    result = (
        vectl_mutate(
            requests=[
                {"action": "edit-plan", "plan_context": "runtime-trigger"}
            ],
            max_issues=1,
        )
        if batch_mode
        else vectl_mutate(
            action="edit-plan", plan_context="runtime-trigger", max_issues=1
        )
    )

    assert "**Error:** stage=final_validation" in result
    assert "validation_source: canonical_model" in result
    assert "blocking_issue_count: 1" in result
    assert "rendered_issue_count: 1" in result
    assert "issues_truncated: false" in result
    assert "issue_values_truncated: false" in result
    assert "canonical validation could not complete [unexpected_validation_error]" in result
    assert raw_exception not in result
    assert "runtime raw candidate" not in result
    assert _state(repo, plan_path, claims_path) == before


@pytest.mark.parametrize("batch_mode", (False, True))
def test_unexpected_structural_exception_returns_fixed_zero_write_failure(
    git_plan: tuple[Path, Path, Path],
    monkeypatch: pytest.MonkeyPatch,
    batch_mode: bool,
) -> None:
    repo, plan_path, claims_path = git_plan
    before = _state(repo, plan_path, claims_path)
    raw_exception = (
        "structural-runtime-trigger\n"
        "RAW_STRUCTURAL_SECRET\nrendered_issue_count: 999"
    )

    def fail_structural_validation(*args: object, **kwargs: object) -> list[object]:
        del args, kwargs
        raise RuntimeError(raw_exception)

    monkeypatch.setattr(mutation_module, "validate_plan", fail_structural_validation)
    result = (
        vectl_mutate(
            requests=[
                {
                    "action": "edit-plan",
                    "plan_context": "structural-runtime-trigger",
                }
            ],
            max_issues=1,
        )
        if batch_mode
        else vectl_mutate(
            action="edit-plan",
            plan_context="structural-runtime-trigger",
            max_issues=1,
        )
    )

    assert "validation_source: structural" in result
    assert "blocking_issue_count: 1" in result
    assert "rendered_issue_count: 1" in result
    assert "issues_truncated: false" in result
    assert "issue_values_truncated: false" in result
    assert "structural validation could not complete [unexpected_validation_error]" in result
    assert raw_exception not in result
    assert "RAW_STRUCTURAL_SECRET" not in result
    assert "structural-runtime-trigger" not in result
    assert _state(repo, plan_path, claims_path) == before


def test_corrected_follow_up_mutation_succeeds_after_diagnostic_failure(
    git_plan: tuple[Path, Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    _, plan_path, _ = git_plan
    original_apply = mutation_module._apply_mutation_request
    injected = False

    def inject_once(
        plan: Plan, request: dict[str, Any]
    ) -> tuple[Plan, str]:
        nonlocal injected
        if not injected:
            injected = True
            return _invalid_candidate_with_three_blockers(), "injected invalid candidate"
        return original_apply(plan, request)

    monkeypatch.setattr(mutation_module, "_apply_mutation_request", inject_once)
    failed = vectl_mutate(
        requests=[{"action": "edit-plan", "plan_context": "rejected candidate"}]
    )
    assert "blocking_issue_count: 3" in failed

    succeeded = vectl_mutate(
        requests=[{"action": "edit-plan", "plan_context": "corrected candidate"}]
    )
    assert "requests_applied: 1" in succeeded
    plan, _ = load_plan_definition(plan_path)
    assert plan.context == "corrected candidate"


def test_final_validation_cycle_discards_plan_claims_locks_and_git(
    git_plan: tuple[Path, Path, Path]
) -> None:
    repo, plan_path, claims_path = git_plan
    before = _state(repo, plan_path, claims_path)

    result = vectl_mutate(
        requests=[
            {
                "action": "edit-phase",
                "phase_id": "foundation",
                "depends_on": ["delivery"],
            }
        ]
    )

    assert "Error" in result and "stage=final_validation" in result
    assert "cycle" in result.lower()
    assert _state(repo, plan_path, claims_path) == before


def test_cas_conflict_preserves_competing_writer_without_batch_commit(
    git_plan: tuple[Path, Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, plan_path, claims_path = git_plan
    claims_before = claims_path.read_bytes()
    original_load = mutation_module._load

    def load_then_compete() -> tuple[Plan, str]:
        plan, expected_hash = original_load()
        competing = plan.model_copy(deep=True)
        competing.context = "competing writer"
        save_plan(competing, plan_path)
        return plan, expected_hash

    monkeypatch.setattr(mutation_module, "_load", load_then_compete)
    head_before = _git(repo, "rev-parse", "HEAD")

    result = vectl_mutate(
        requests=[
            {
                "action": "edit-plan",
                "plan_context": "batch writer",
            }
        ]
    )

    assert "CAS conflict" in result
    persisted, _ = load_plan_definition(plan_path)
    assert persisted.context == "competing writer"
    assert claims_path.read_bytes() == claims_before
    assert _git(repo, "rev-parse", "HEAD") == head_before


def test_explicit_id_replay_fails_without_second_write_or_commit(
    git_plan: tuple[Path, Path, Path]
) -> None:
    repo, plan_path, claims_path = git_plan
    requests = _successful_requests()
    first = vectl_mutate(requests=requests)
    assert "requests_applied: 4" in first
    after_first = _state(repo, plan_path, claims_path)
    commit_count = _git(repo, "rev-list", "--count", "HEAD")

    second = vectl_mutate(requests=requests)

    assert "Error" in second and "request[0]" in second
    assert _state(repo, plan_path, claims_path) == after_first
    assert _git(repo, "rev-list", "--count", "HEAD") == commit_count


def test_registered_tool_preserves_single_action_response(
    git_plan: tuple[Path, Path, Path]
) -> None:
    _, plan_path, _ = git_plan
    result = vectl_mutate(
        action="edit-step",
        step_id="foundation.seed",
        name="Renamed seed",
    )
    assert result.startswith(
        "**Updated step:** foundation.seed\n\n"
        "→ Use `vectl_search` to verify plan consistency.\n\n"
        "mutation_receipt: "
    )
    receipt = _mutation_receipt(result)
    assert receipt["schema_version"] == "vectl.plan-mutation.receipt.v1"
    assert receipt["plan_applied"] is True
    assert receipt["candidate_sha256"] == receipt["persisted_sha256"]
    assert receipt["admission_status"] == "not_configured"
    assert receipt["git_attempted"] is True
    plan, _ = load_plan_definition(plan_path)
    assert plan.find_step("foundation.seed")[1].name == "Renamed seed"  # type: ignore[index]


def test_linked_worktree_guard_precedes_batch_load(
    git_plan: tuple[Path, Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, _, _ = git_plan
    monkeypatch.delenv("VECTL_PLAN_PATH")
    monkeypatch.setattr("vectl.mcp_server.is_linked_worktree", lambda: (True, repo))
    monkeypatch.setattr(
        mutation_module,
        "_load",
        lambda: (_ for _ in ()).throw(AssertionError("must not load")),
    )

    result = vectl_mutate(requests=_successful_requests())

    assert "Mutate blocked" in result
    assert "linked worktree" in result


def test_admitted_mutation_mcp_single_and_batch_share_transaction(
    git_plan: tuple[Path, Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    transaction = getattr(io_module, "_finalize_admitted_plan_mutation", None)
    assert callable(transaction), (
        "REQ-AMV1-MCP-TRANSACTION-CONVERGENCE missing: MCP single and batch modes "
        "have no shared admitted mutation transaction"
    )
    for key in (
        "VECTL_PLAN_ADMISSION_EXECUTABLE",
        "VECTL_PLAN_ADMISSION_EXECUTABLE_SHA256",
        "VECTL_PLAN_ADMISSION_TIMEOUT_SECONDS",
    ):
        monkeypatch.delenv(key, raising=False)

    calls: list[str] = []

    def observed_transaction(*args: object, **kwargs: object) -> object:
        message = kwargs.get("commit_message", args[3] if len(args) > 3 else "")
        calls.append(str(message))
        return transaction(*args, **kwargs)

    monkeypatch.setattr(
        io_module,
        "_finalize_admitted_plan_mutation",
        observed_transaction,
    )
    if hasattr(mcp_common, "_finalize_admitted_plan_mutation"):
        monkeypatch.setattr(
            mcp_common,
            "_finalize_admitted_plan_mutation",
            observed_transaction,
        )

    single_result = vectl_mutate(
        action="add-step",
        phase_id="foundation",
        step_id="foundation.admitted-single",
        name="Admitted single",
    )
    batch_result = vectl_mutate(
        requests=[
            {
                "action": "edit-plan",
                "plan_context": "admitted batch",
            }
        ]
    )

    assert "Error" not in single_result
    assert "Error" not in batch_result
    assert len(calls) == 2, "single and batch mutation must each reach the one private transaction"
    assert "mutate add-step" in calls[0]
    assert "mutate batch" in calls[1]
    for result in (single_result, batch_result):
        receipt = _mutation_receipt(result)
        assert receipt["schema_version"] == "vectl.plan-mutation.receipt.v1"
        assert receipt["plan_applied"] is True
        assert receipt["candidate_sha256"] == receipt["persisted_sha256"]
        assert receipt["admission_status"] == "not_configured"
        assert receipt["git_attempted"] is True
