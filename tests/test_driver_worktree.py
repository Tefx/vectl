"""Tests for driver worktree lifecycle behavior."""

from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path

from vectl.driver.worktree import (
    MergeOutcome,
    cleanup,
    create,
    derive_branch_name,
    derive_worktree_path,
    merge,
)


def _run_git(repo: Path, *args: str) -> str:
    """Run a git command and return stdout."""

    result = subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _init_repo(tmp_path: Path) -> Path:
    """Initialize a temporary git repository with one commit."""

    repo = tmp_path / "repo"
    repo.mkdir()
    _run_git(repo, "init", "-b", "main")
    _run_git(repo, "config", "user.name", "vectl-test")
    _run_git(repo, "config", "user.email", "vectl-test@example.com")
    (repo / "README.md").write_text("seed\n", encoding="utf-8")
    _run_git(repo, "add", "README.md")
    _run_git(repo, "commit", "-m", "seed")
    return repo


def test_derive_worktree_path_preserves_relative_and_absolute(tmp_path: Path) -> None:
    """derive_worktree_path preserves caller-selected path style."""

    relative = derive_worktree_path("alpha", base_dir=Path(".vectl/worktrees"))
    assert relative == Path(".vectl/worktrees") / "alpha"

    absolute_base = tmp_path / "worktrees"
    absolute = derive_worktree_path("beta", base_dir=absolute_base)
    assert absolute == absolute_base / "beta"


def test_create_reuses_existing_worktree_path_on_retry(tmp_path: Path, monkeypatch) -> None:
    """Repeated create call reuses existing path for retries."""

    repo = _init_repo(tmp_path)
    monkeypatch.chdir(repo)

    first = asyncio.run(create("core.impl", base_dir=Path(".vectl/worktrees")))
    second = asyncio.run(create("core.impl", base_dir=Path(".vectl/worktrees")))

    assert first.branch_name == derive_branch_name("core.impl")
    assert first.reused_existing is False
    assert second.reused_existing is True
    assert (repo / second.worktree_path).exists()


def test_create_reuses_existing_branch_when_worktree_path_removed(
    tmp_path: Path, monkeypatch
) -> None:
    """create handles retry where branch exists but worktree path does not."""

    repo = _init_repo(tmp_path)
    monkeypatch.chdir(repo)

    initial = asyncio.run(create("core.retry", base_dir=Path(".vectl/worktrees")))
    _run_git(repo, "worktree", "remove", "--force", str(initial.worktree_path))

    retried = asyncio.run(create("core.retry", base_dir=Path(".vectl/worktrees")))

    assert retried.reused_existing is True
    assert (repo / retried.worktree_path).exists()


def test_merge_auto_resolves_trivial_plan_conflict_and_cleanup(tmp_path: Path, monkeypatch) -> None:
    """Trivial plan conflict auto-resolves with squash merge default."""

    repo = _init_repo(tmp_path)
    monkeypatch.chdir(repo)
    (repo / "plan.yaml").write_text("base\n", encoding="utf-8")
    _run_git(repo, "add", "plan.yaml")
    _run_git(repo, "commit", "-m", "add plan")

    binding = asyncio.run(create("core.plan", base_dir=Path(".vectl/worktrees")))
    source_plan = repo / binding.worktree_path / "plan.yaml"
    source_plan.write_text("source-branch\n", encoding="utf-8")
    _run_git(repo / binding.worktree_path, "add", "plan.yaml")
    _run_git(repo / binding.worktree_path, "commit", "-m", "source plan change")

    (repo / "plan.yaml").write_text("target-branch\n", encoding="utf-8")
    _run_git(repo, "add", "plan.yaml")
    _run_git(repo, "commit", "-m", "target plan change")

    result = asyncio.run(merge(step_id="core.plan", worktree_path=binding.worktree_path))

    assert result.outcome == MergeOutcome.AUTO_RESOLVED_CONFLICT
    assert result.conflicted_files == ("plan.yaml",)
    assert (repo / "plan.yaml").read_text(encoding="utf-8") == "target-branch\n"

    asyncio.run(cleanup("core.plan", binding.worktree_path))
    assert not (repo / binding.worktree_path).exists()


def test_merge_aborts_and_returns_resolver_dispatch_for_non_trivial_conflict(
    tmp_path: Path, monkeypatch
) -> None:
    """Non-trivial conflicts return resolver handoff after merge abort."""

    repo = _init_repo(tmp_path)
    monkeypatch.chdir(repo)
    source_file = repo / "src" / "module.py"
    source_file.parent.mkdir(parents=True, exist_ok=True)
    source_file.write_text("value = 1\n", encoding="utf-8")
    _run_git(repo, "add", "src/module.py")
    _run_git(repo, "commit", "-m", "add module")

    binding = asyncio.run(create("core.conflict", base_dir=Path(".vectl/worktrees")))
    branch_file = repo / binding.worktree_path / "src" / "module.py"
    branch_file.write_text("value = 2\n", encoding="utf-8")
    _run_git(repo / binding.worktree_path, "add", "src/module.py")
    _run_git(repo / binding.worktree_path, "commit", "-m", "branch change")

    source_file.write_text("value = 3\n", encoding="utf-8")
    _run_git(repo, "add", "src/module.py")
    _run_git(repo, "commit", "-m", "main change")

    result = asyncio.run(merge(step_id="core.conflict", worktree_path=binding.worktree_path))

    assert result.outcome == MergeOutcome.NON_TRIVIAL_CONFLICT
    assert result.conflicted_files == ("src/module.py",)
    assert result.resolver_dispatch is not None
    assert result.resolver_dispatch.source_branch == derive_branch_name("core.conflict")
    assert result.resolver_dispatch.target_branch == "main"

    merge_head_check = subprocess.run(
        ["git", "rev-parse", "-q", "--verify", "MERGE_HEAD"],
        cwd=repo,
        check=False,
        capture_output=True,
        text=True,
    )
    assert merge_head_check.returncode != 0

    asyncio.run(cleanup("core.conflict", binding.worktree_path))
    assert not (repo / binding.worktree_path).exists()
