"""RFC contract tests for atomic multi-operation MCP Plan mutation."""

from __future__ import annotations

import hashlib
import json
import subprocess
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

import vectl.io as io_module
import vectl.mcp_core_mutation_tools as mutation_module
from vectl.io import load_plan_definition, save_plan
from vectl.mcp_server import vectl_mutate as mutate_tool
from vectl.models import Phase, PhaseStatus, Plan, Step, StepStatus

vectl_mutate = mutate_tool.fn  # type: ignore[attr-defined]


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=repo, capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


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


def _state(repo: Path, plan_path: Path, claims_path: Path) -> tuple[bytes, bytes, str, list[str]]:
    plan, _ = load_plan_definition(plan_path)
    return (
        plan_path.read_bytes(),
        claims_path.read_bytes(),
        _git(repo, "rev-parse", "HEAD"),
        [phase.status.value for phase in plan.phases],
    )


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


def test_ordered_batch_persists_once_with_exact_hash_and_results(
    git_plan: tuple[Path, Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, plan_path, _ = git_plan
    counts = {"load": 0, "recalc": 0, "save": 0, "commit": 0}

    original_load = mutation_module._load
    original_recalc = mutation_module.recalc_lock_status
    original_save = mutation_module._save_plan_receipt
    original_commit = io_module._git_commit_plan

    def counted_load() -> tuple[Plan, str]:
        counts["load"] += 1
        return original_load()

    def counted_recalc(plan: Plan) -> list[str]:
        counts["recalc"] += 1
        return original_recalc(plan)

    def counted_save(*args: Any, **kwargs: Any) -> tuple[str, str]:
        counts["save"] += 1
        return original_save(*args, **kwargs)

    def counted_commit(path: Path, message: str) -> bool:
        counts["commit"] += 1
        return bool(original_commit(path, message))

    monkeypatch.setattr(mutation_module, "_load", counted_load)
    monkeypatch.setattr(mutation_module, "recalc_lock_status", counted_recalc)
    monkeypatch.setattr(mutation_module, "_save_plan_receipt", counted_save)
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
    assert result == (
        "**Updated step:** foundation.seed\n\n"
        "→ Use `vectl_search` to verify plan consistency."
    )
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
