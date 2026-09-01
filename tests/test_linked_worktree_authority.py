"""Real Git worktree coverage for immutable Vectl Plan authority."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

import vectl.io as io_module
from vectl.claims import ClaimEntry, load_claims, save_claims
from vectl.cli import app
from vectl.cli_plan_common import _save_plan
from vectl.cli_top import _diagnostics
from vectl.core.batch_claim import BatchClaimItem, BatchClaimRequest, claim_many
from vectl.io import load_plan_definition, save_plan
from vectl.models import Phase, PhaseStatus, Plan, PlanError, Step, StepStatus
from vectl.plan_path import authority_for_plan_path

runner = CliRunner()


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=repo, capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def _seed_worktree_repo(tmp_path: Path, step_ids: list[str]) -> tuple[Path, Path]:
    main = tmp_path / "integration"
    main.mkdir()
    _git(main, "init", "-q", "-b", "integration")
    _git(main, "config", "user.email", "authority@example.invalid")
    _git(main, "config", "user.name", "Authority Test")
    plan_path = main / "plan.yaml"
    save_plan(
        Plan(
            project="linked-worktree-authority",
            phases=[
                Phase(
                    id="delivery",
                    name="Delivery",
                    status=PhaseStatus.PENDING,
                    steps=[
                        Step(id=step_id, name=step_id, status=StepStatus.PENDING)
                        for step_id in step_ids
                    ],
                )
            ],
        ),
        plan_path,
    )
    _git(main, "add", "plan.yaml")
    _git(main, "commit", "-qm", "seed plan")
    linked = tmp_path / "linked"
    _git(main, "worktree", "add", "-q", "-b", "worker", str(linked))
    return main, linked


def _invoke(monkeypatch: pytest.MonkeyPatch, cwd: Path, args: list[str]):
    monkeypatch.chdir(cwd)
    result = runner.invoke(app, args)
    assert result.exit_code == 0, result.output
    return result


def test_real_linked_worktree_uses_integration_authority_for_cli_mcp_and_repair(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("VECTL_PLAN_PATH", raising=False)
    monkeypatch.delenv("VECTL_PLAN", raising=False)
    main, linked = _seed_worktree_repo(
        tmp_path,
        ["main-complete", "linked-complete", "linked-defer", "linked-skip", "mcp-step"],
    )
    plan_path = main / "plan.yaml"
    claims_path = main / ".git" / "vectl" / "claims.json"

    # A linked claim writes the integration Plan and integration branch key;
    # completion from the main worktree removes that exact entry.
    _invoke(monkeypatch, linked, ["claim", "main-complete", "--agent", "worker", "--no-guidance"])
    claims = load_claims(claims_path)
    assert set(claims) == {"integration:main-complete"}
    _invoke(monkeypatch, main, ["complete", "main-complete", "--evidence", "main completion"])
    assert load_claims(claims_path) == {}

    # Linked lifecycle commands share the same branch key and exact cleanup.
    _invoke(monkeypatch, linked, ["claim", "linked-complete", "--agent", "worker", "--no-guidance"])
    _invoke(monkeypatch, linked, ["complete", "linked-complete", "--evidence", "linked completion"])
    _invoke(monkeypatch, linked, ["claim", "linked-defer", "--agent", "worker", "--no-guidance"])
    _invoke(monkeypatch, linked, ["defer", "linked-defer"])
    _invoke(monkeypatch, linked, ["claim", "linked-skip", "--agent", "worker", "--no-guidance"])
    _invoke(monkeypatch, linked, ["skip", "linked-skip", "--reason", "irrelevant"])
    assert load_claims(claims_path) == {}

    # Importing and calling MCP from the linked startup CWD still resolves the
    # integration Plan, branch, Git commit, and backup target.
    monkeypatch.chdir(linked)
    from vectl.mcp_server import vectl_claim, vectl_complete

    claim_response = vectl_claim.fn(agent="mcp-worker", step_id="mcp-step", guidance=False)
    assert claim_response["ok"] is True
    assert set(load_claims(claims_path)) == {"integration:mcp-step"}
    assert (main / ".git" / "vectl" / "plan.yaml.bak").exists()

    # Replace the current-branch entry with a ghost and preserve a foreign
    # branch entry. Status/show/top and repair must use integration authority
    # from either worktree, while repair never prunes the foreign entry.
    foreign = ClaimEntry(
        step_id="foreign-only",
        branch="worker",
        agent="other-worker",
        claimed_at="2026-01-01T00:00:00Z",
    )
    ghost = ClaimEntry(
        step_id="ghost",
        branch="integration",
        agent="ghost-worker",
        claimed_at="2026-01-01T00:00:00Z",
    )
    save_claims({"worker:foreign-only": foreign, "integration:ghost": ghost}, claims_path)
    linked_status = _invoke(monkeypatch, linked, ["status"]).output
    main_status = _invoke(monkeypatch, main, ["status"]).output
    assert "Ghost claims: 1" in linked_status
    assert "Ghost claims: 1" in main_status
    assert "missing from claims.json" in _invoke(monkeypatch, linked, ["show", "mcp-step"]).output

    plan, _ = load_plan_definition(plan_path)
    linked_diagnostics = _diagnostics(
        plan,
        authority_for_plan_path(plan_path).plan_path,
        branch=authority_for_plan_path(plan_path).branch,
    ).unwrap()
    monkeypatch.chdir(main)
    main_authority = authority_for_plan_path(plan_path)
    main_diagnostics = _diagnostics(
        plan, main_authority.plan_path, branch=main_authority.branch
    ).unwrap()
    assert [line.plain for line in linked_diagnostics] == [line.plain for line in main_diagnostics]

    linked_repair = json.loads(
        _invoke(monkeypatch, linked, ["repair", "claims", "--dry-run", "--json"]).output
    )
    main_repair = json.loads(
        _invoke(monkeypatch, main, ["repair", "claims", "--dry-run", "--json"]).output
    )
    assert linked_repair["branch"] == main_repair["branch"] == "integration"
    assert linked_repair["actions"] == main_repair["actions"]
    _invoke(monkeypatch, linked, ["repair", "claims"])
    repaired = load_claims(claims_path)
    assert repaired["worker:foreign-only"] == foreign
    assert "integration:ghost" not in repaired
    assert "integration:mcp-step" in repaired

    monkeypatch.chdir(linked)
    assert vectl_complete.fn(step_id="mcp-step", evidence="MCP completion")
    assert "integration:mcp-step" not in load_claims(claims_path)

    # The integration worktree owns all Plan history and remains clean.
    assert _git(main, "status", "--short") == ""
    assert len(_git(main, "log", "--oneline").splitlines()) >= 9

    # An explicit external Plan writes only that Plan and never autosaves into
    # the integration repository.
    external = tmp_path / "external" / "plan.yaml"
    external.parent.mkdir()
    save_plan(
        Plan(
            project="external",
            phases=[
                Phase(
                    id="outside",
                    name="Outside",
                    status=PhaseStatus.PENDING,
                    steps=[Step(id="external-step", name="External")],
                )
            ],
        ),
        external,
    )
    integration_before = plan_path.read_bytes()
    head_before = _git(main, "rev-parse", "HEAD")
    _invoke(
        monkeypatch,
        main,
        ["claim", "external-step", "--agent", "external", "--no-guidance", "--plan", str(external)],
    )
    assert plan_path.read_bytes() == integration_before
    assert _git(main, "rev-parse", "HEAD") == head_before
    assert _git(main, "status", "--short") == ""
    external_plan, _ = load_plan_definition(external)
    assert external_plan.find_step("external-step")[1].status == StepStatus.CLAIMED  # type: ignore[index]


def test_linked_duplicate_migration_uses_integration_claim_branch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("VECTL_PLAN_PATH", raising=False)
    main, linked = _seed_worktree_repo(tmp_path, ["duplicate"])
    plan_path = main / "plan.yaml"
    plan, plan_hash = load_plan_definition(plan_path)
    plan.phases.append(
        Phase(
            id="second",
            name="Second",
            status=PhaseStatus.PENDING,
            steps=[Step(id="duplicate", name="Duplicate")],
        )
    )
    save_plan(plan, plan_path, expected_hash=plan_hash)
    _git(main, "add", "plan.yaml")
    _git(main, "commit", "-qm", "add duplicate")
    claims_path = main / ".git" / "vectl" / "claims.json"
    save_claims(
        {
            "integration:duplicate": ClaimEntry(
                step_id="duplicate",
                branch="integration",
                agent="integration-worker",
                claimed_at="2026-01-01T00:00:00Z",
            )
        },
        claims_path,
    )

    payload = json.loads(
        _invoke(monkeypatch, linked, ["migrate-step-id", "--dry-run", "--json"]).output
    )
    assert len(payload["report"]["claimed_step_conflicts"]) == 2


def test_batch_claim_compensation_preserves_a_concurrent_replacement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plan_path = tmp_path / "plan.yaml"
    save_plan(
        Plan(
            project="batch-compensation",
            phases=[
                Phase(
                    id="phase",
                    name="Phase",
                    status=PhaseStatus.PENDING,
                    steps=[Step(id="step", name="Step")],
                )
            ],
        ),
        plan_path,
    )
    authority = authority_for_plan_path(plan_path)

    def replace_then_fail(*args: object, **kwargs: object) -> str:
        claims = load_claims(authority.claims_path)
        key, entry = next(iter(claims.items()))
        replacement = entry.model_copy(update={"agent": "replacement"})
        save_claims({key: replacement}, authority.claims_path)
        raise OSError("simulated Plan persistence failure")

    monkeypatch.setattr(io_module, "save_plan", replace_then_fail)
    with pytest.raises(OSError, match="simulated Plan persistence failure"):
        claim_many(
            BatchClaimRequest([BatchClaimItem(step_id="step", agent="original")]),
            authority=authority,
        )

    claims = load_claims(authority.claims_path)
    assert claims[f"{authority.branch}:step"].agent == "replacement"


def test_linked_cli_mcp_status_and_repair_share_full_entry_diagnostics(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    main, linked = _seed_worktree_repo(tmp_path, ["claimed"])
    plan_path = main / "plan.yaml"
    authority = authority_for_plan_path(plan_path)
    plan, plan_hash = load_plan_definition(plan_path)
    step = plan.find_step("claimed")[1]  # type: ignore[index]
    step.status = StepStatus.CLAIMED
    step.claimed_by = "plan-owner"
    step.claimed_at = "2026-01-01T00:00:00Z"
    save_plan(plan, plan_path, expected_hash=plan_hash)
    save_claims(
        {
            "integration:claimed": ClaimEntry(
                step_id="claimed",
                branch="integration",
                agent="stale-owner",
                claimed_at="2026-01-02T00:00:00Z",
            )
        },
        authority.claims_path,
    )

    linked_status = _invoke(monkeypatch, linked, ["status"]).output
    main_status = _invoke(monkeypatch, main, ["status"]).output
    assert "Stale claim entries: 1" in linked_status
    assert "Stale claim entries: 1" in main_status

    monkeypatch.chdir(linked)
    from vectl.mcp_server import vectl_repair_claims, vectl_status

    mcp_status = vectl_status.fn()
    assert "Stale claim entries: 1" in mcp_status
    cli_repair = json.loads(
        _invoke(monkeypatch, linked, ["repair", "claims", "--dry-run", "--json"]).output
    )
    mcp_repair = vectl_repair_claims.fn(dry_run=True)
    assert [action["action"] for action in cli_repair["actions"]] == ["update"]
    assert mcp_repair["actions"] == cli_repair["actions"]


def test_authority_captures_commit_policy_and_reports_partial_git_durability(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    main, linked = _seed_worktree_repo(tmp_path, ["captured"])
    plan_path = main / "plan.yaml"
    monkeypatch.chdir(main)
    authority = authority_for_plan_path(plan_path)
    head_before = _git(main, "rev-parse", "HEAD")
    plan, plan_hash = load_plan_definition(plan_path)
    plan.project = "captured-policy"
    monkeypatch.chdir(linked)
    _save_plan(plan, plan_path, plan_hash, "vectl: captured policy", authority=authority)
    assert _git(main, "rev-parse", "HEAD") != head_before

    monkeypatch.setattr("vectl.io._git_commit_plan", lambda *_args: False)
    plan, plan_hash = load_plan_definition(plan_path)
    plan.project = "partial-durability"
    _save_plan(plan, plan_path, plan_hash, "vectl: partial durability", authority=authority)
    assert load_plan_definition(plan_path)[0].project == "partial-durability"
    assert "Plan persisted, but Git commit failed" in capsys.readouterr().out


def test_external_and_detached_authority_never_use_caller_cwd_branch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    main, linked = _seed_worktree_repo(tmp_path, ["authority"])
    external_plan = tmp_path / "external" / "plan.yaml"
    external_plan.parent.mkdir()
    save_plan(Plan(project="external", phases=[]), external_plan)
    monkeypatch.chdir(main)
    external_authority = authority_for_plan_path(external_plan)
    monkeypatch.chdir(linked)
    assert authority_for_plan_path(external_plan).branch == external_authority.branch == "external"
    assert external_authority.provenance == "external_non_git"
    assert external_authority.commit_policy is False

    monkeypatch.chdir(main)
    _git(main, "worktree", "remove", "--force", str(linked))
    _git(main, "checkout", "--detach")
    with pytest.raises(PlanError, match="ambiguous"):
        authority_for_plan_path(main / "plan.yaml")
