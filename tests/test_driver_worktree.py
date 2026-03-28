"""Integration tests for worktree lifecycle with real git commands.

Tests verify worktree create/merge/cleanup behavior against real git operations
in temporary repositories.

Required checks from spec:
- fresh create path
- retry/reuse path when worktree already exists
- clean squash merge path
- trivial conflict detection path
- non-trivial conflict classification path

Architecture Reference: docs/DRIVER-ARCHITECTURE.md Section 2.6
Blueprint Reference: DRIVER-BLUEPRINT.md Worktree Lifecycle (worktree.py)
"""

import asyncio
import subprocess
from pathlib import Path
from typing import TypeVar

import pytest

from vectl.driver.worktree import (
    Failure,
    Result,
    Success,
    TRIVIAL_CONFLICT_PATTERNS,
    WORKTREE_BASE_DIR,
    WORKTREE_BRANCH_PREFIX,
    ConflictResolverDispatch,
    MergeOutcome,
    MergeResult,
    MergeStrategy,
    WorktreeBinding,
    WorktreeError,
    cleanup,
    create,
    derive_branch_name,
    derive_worktree_path,
    merge,
)

# =============================================================================
# Fixtures: Temporary git repo setup
# =============================================================================


T = TypeVar("T")
E = TypeVar("E", bound=Exception)


def expect_success(result: Result[T, E]) -> T:
    """Return success payload or fail test with error."""

    assert isinstance(result, Success), f"expected Success, got {result!r}"
    return result.value


def expect_failure(result: Result[T, E]) -> E:
    """Return failure payload or fail test when successful."""

    assert isinstance(result, Failure), f"expected Failure, got {result!r}"
    return result.error


@pytest.fixture
def temp_git_repo(tmp_path: Path) -> Path:
    """Create a temporary git repository with initial commit on master branch.

    Returns:
        Path to the repository root.
    """
    repo_root = tmp_path / "repo"
    repo_root.mkdir()

    # Initialize git repo
    subprocess.run(["git", "init"], cwd=repo_root, capture_output=True, check=True)

    # Configure git user (required for commits)
    subprocess.run(
        ["git", "config", "user.email", "test@test.com"],
        cwd=repo_root,
        capture_output=True,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"], cwd=repo_root, capture_output=True, check=True
    )

    # Create initial commit on master branch (git init default)
    (repo_root / "README.md").write_text("# Test Repository\n")
    subprocess.run(["git", "add", "README.md"], cwd=repo_root, capture_output=True, check=True)
    subprocess.run(
        ["git", "commit", "-m", "Initial commit"], cwd=repo_root, capture_output=True, check=True
    )

    return repo_root


# =============================================================================
# Contract Tests: Path/branch derivation
# =============================================================================


class TestDeriveBranchName:
    """Tests for derive_branch_name contract."""

    def test_derive_branch_name_format(self) -> None:
        """Branch name MUST be vectl/step-{step_id}."""
        assert expect_success(derive_branch_name("core-impl")) == "vectl/step-core-impl"
        assert expect_success(derive_branch_name("test-step")) == "vectl/step-test-step"
        assert expect_success(derive_branch_name("phase_step_123")) == "vectl/step-phase_step_123"

    def test_derive_branch_name_uses_prefix_constant(self) -> None:
        """derive_branch_name MUST use WORKTREE_BRANCH_PREFIX."""
        assert WORKTREE_BRANCH_PREFIX == "vectl/step-"
        assert expect_success(derive_branch_name("x")).startswith(WORKTREE_BRANCH_PREFIX)


class TestDeriveWorktreePath:
    """Tests for derive_worktree_path contract."""

    def test_derive_worktree_path_format(self) -> None:
        """Path MUST be base_dir/step_id."""
        path = expect_success(derive_worktree_path("core-impl"))
        assert path == WORKTREE_BASE_DIR / "core-impl"

    def test_derive_worktree_path_custom_base(self) -> None:
        """Path MUST support custom base_dir."""
        custom_base = Path("/custom/worktrees")
        path = expect_success(derive_worktree_path("test-step", base_dir=custom_base))
        assert path == custom_base / "test-step"

    def test_derive_worktree_path_returns_path(self) -> None:
        """MUST return Path object."""
        result = expect_success(derive_worktree_path("any_step"))
        assert isinstance(result, Path)


# =============================================================================
# Integration Tests: Create worktree
# =============================================================================


class TestCreateFreshPath:
    """Tests for fresh worktree creation."""

    def test_creates_branch_and_worktree(self, temp_git_repo: Path) -> None:
        """create() MUST create branch and worktree on fresh creation.

        Fresh create path: worktree path does not exist.
        """
        step_id = "test-create-fresh"
        base_dir = temp_git_repo / ".vectl" / "worktrees"
        base_dir.mkdir(parents=True, exist_ok=True)

        binding = expect_success(asyncio.run(create(step_id, base_dir=base_dir, cwd=temp_git_repo)))

        assert binding.step_id == step_id
        assert binding.branch_name == f"vectl/step-{step_id}"
        assert binding.worktree_path == base_dir / step_id
        assert binding.reused_existing is False

        # Verify branch exists
        result = subprocess.run(
            ["git", "branch", "--list", binding.branch_name],
            cwd=temp_git_repo,
            capture_output=True,
            text=True,
            check=True,
        )
        assert binding.branch_name in result.stdout

        # Verify worktree exists
        assert binding.worktree_path.exists()
        assert (binding.worktree_path / ".git").exists()

    def test_worktree_is_isolated(self, temp_git_repo: Path) -> None:
        """Worktree MUST be isolated (separate working directory).

        Source: DRIVER-BLUEPRINT.md Worktree Lifecycle (worktree isolation).
        """
        step_id = "phase_step"
        base_dir = temp_git_repo / ".vectl" / "worktrees"
        base_dir.mkdir(parents=True, exist_ok=True)

        binding = expect_success(asyncio.run(create(step_id, base_dir=base_dir, cwd=temp_git_repo)))

        # Work on step branch
        test_file = binding.worktree_path / "new_file.txt"
        test_file.write_text("step change\n")
        subprocess.run(
            ["git", "add", "new_file.txt"],
            cwd=binding.worktree_path,
            capture_output=True,
            check=True,
        )
        subprocess.run(
            ["git", "commit", "-m", "step work"],
            cwd=binding.worktree_path,
            capture_output=True,
            check=True,
        )

        # Main repo should NOT see the new file
        assert not (temp_git_repo / "new_file.txt").exists()


class TestCreateReusePath:
    """Tests for retry/reuse path when worktree already exists."""

    def test_reuse_existing_worktree(self, temp_git_repo: Path) -> None:
        """create() MUST reuse existing worktree on retry path.

        Retry path: worktree path already exists.
        Returned WorktreeBinding.reused_existing MUST be True.
        """
        step_id = "test-reuse-step"
        base_dir = temp_git_repo / ".vectl" / "worktrees"
        base_dir.mkdir(parents=True, exist_ok=True)

        # First call: fresh create
        binding1 = expect_success(
            asyncio.run(create(step_id, base_dir=base_dir, cwd=temp_git_repo))
        )
        assert binding1.reused_existing is False

        # Second call: reuse existing
        binding2 = expect_success(
            asyncio.run(create(step_id, base_dir=base_dir, cwd=temp_git_repo))
        )
        assert binding2.reused_existing is True
        assert binding2.worktree_path == binding1.worktree_path
        assert binding2.branch_name == binding1.branch_name

    def test_reuse_preserves_branch_name(self, temp_git_repo: Path) -> None:
        """Reused worktree MUST preserve stable branch naming.

        vectl/step-{step_id} MUST be consistent across calls.
        """
        step_id = "consistent_branch"
        base_dir = temp_git_repo / ".vectl" / "worktrees"
        base_dir.mkdir(parents=True, exist_ok=True)

        binding1 = expect_success(
            asyncio.run(create(step_id, base_dir=base_dir, cwd=temp_git_repo))
        )
        binding2 = expect_success(
            asyncio.run(create(step_id, base_dir=base_dir, cwd=temp_git_repo))
        )

        assert binding1.branch_name == binding2.branch_name
        expected_branch = f"vectl/step-{step_id}"
        assert binding1.branch_name == expected_branch


class TestCreateErrorHandling:
    """Tests for error handling in create."""

    def test_raises_worktree_error_on_git_failure(self, tmp_path: Path) -> None:
        """create() MUST raise WorktreeError on git command failure.

        Non-git repo should cause failure.
        """
        step_id = "error-test"
        base_dir = tmp_path / "not_a_repo" / "worktrees"
        base_dir.mkdir(parents=True, exist_ok=True)

        failure = expect_failure(
            asyncio.run(create(step_id, base_dir=base_dir, cwd=tmp_path / "not_a_repo"))
        )
        assert step_id in str(failure)


# =============================================================================
# Integration Tests: Merge worktree
# =============================================================================


class TestMergeClean:
    """Tests for clean squash merge path."""

    def test_clean_merge_success(self, temp_git_repo: Path) -> None:
        """merge() MUST return CLEAN_MERGE on successful squash merge.

        Clean merge path: no conflicts.
        """
        step_id = "clean-merge"
        base_dir = temp_git_repo / ".vectl" / "worktrees"
        base_dir.mkdir(parents=True, exist_ok=True)

        # Create worktree
        binding = expect_success(asyncio.run(create(step_id, base_dir=base_dir, cwd=temp_git_repo)))

        # Make changes in worktree
        test_file = binding.worktree_path / "feature.txt"
        test_file.write_text("new feature\n")
        subprocess.run(
            ["git", "add", "feature.txt"],
            cwd=binding.worktree_path,
            capture_output=True,
            check=True,
        )
        subprocess.run(
            ["git", "commit", "-m", "Add feature"],
            cwd=binding.worktree_path,
            capture_output=True,
            check=True,
        )

        # Merge back to main
        result = expect_success(
            asyncio.run(
                merge(step_id, binding.worktree_path, target_branch="main", cwd=temp_git_repo)
            )
        )

        # Check result outcome
        assert result.outcome == MergeOutcome.CLEAN_MERGE
        assert len(result.conflicted_files) == 0
        assert result.resolver_dispatch is None

        # Verify squash commit was created
        log_result = subprocess.run(
            ["git", "log", "--oneline", "-1"],
            cwd=temp_git_repo,
            capture_output=True,
            text=True,
            check=True,
        )
        assert "[vectl-driver]" in log_result.stdout

    def test_squash_commit_message_includes_step_id(self, temp_git_repo: Path) -> None:
        """Squash commit MUST include step_id in message.

        Source: DRIVER-BLUEPRINT.md Worktree Lifecycle.
        """
        step_id = "feature-branch"
        base_dir = temp_git_repo / ".vectl" / "worktrees"
        base_dir.mkdir(parents=True, exist_ok=True)

        binding = expect_success(asyncio.run(create(step_id, base_dir=base_dir, cwd=temp_git_repo)))
        (binding.worktree_path / "file.txt").write_text("content\n")
        subprocess.run(
            ["git", "add", "file.txt"], cwd=binding.worktree_path, capture_output=True, check=True
        )
        subprocess.run(
            ["git", "commit", "-m", "Add file"],
            cwd=binding.worktree_path,
            capture_output=True,
            check=True,
        )

        expect_success(asyncio.run(merge(step_id, binding.worktree_path, cwd=temp_git_repo)))

        log_result = subprocess.run(
            ["git", "log", "--oneline", "-1"],
            cwd=temp_git_repo,
            capture_output=True,
            text=True,
            check=True,
        )
        assert step_id in log_result.stdout


class TestMergeTrivialConflict:
    """Tests for trivial conflict detection and auto-resolution."""

    def test_trivial_conflict_plan_yaml(self, temp_git_repo: Path) -> None:
        """merge() MUST auto-resolve plan.yaml conflicts.

        Trivial conflict path: conflict in plan.yaml is auto-resolved.
        """
        step_id = "trivial-conflict"
        base_dir = temp_git_repo / ".vectl" / "worktrees"
        base_dir.mkdir(parents=True, exist_ok=True)

        # Add plan.yaml to main branch first
        plan_main = temp_git_repo / "plan.yaml"
        plan_main.write_text(
            "phases:\n"
            "  - id: core\n"
            "    steps:\n"
            "      - id: test-step\n"
            "        description: Main version\n"
        )
        subprocess.run(
            ["git", "add", "plan.yaml"], cwd=temp_git_repo, capture_output=True, check=True
        )
        subprocess.run(
            ["git", "commit", "-m", "Add plan.yaml"],
            cwd=temp_git_repo,
            capture_output=True,
            check=True,
        )

        # Create worktree (branch from current HEAD)
        binding = expect_success(asyncio.run(create(step_id, base_dir=base_dir, cwd=temp_git_repo)))

        # Modify plan.yaml on step branch
        plan_file = binding.worktree_path / "plan.yaml"
        plan_file.write_text(
            "phases:\n"
            "  - id: core\n"
            "    steps:\n"
            "      - id: test-step\n"
            "        description: Modified on branch\n"
        )
        subprocess.run(
            ["git", "add", "plan.yaml"], cwd=binding.worktree_path, capture_output=True, check=True
        )
        subprocess.run(
            ["git", "commit", "-m", "Modify plan.yaml"],
            cwd=binding.worktree_path,
            capture_output=True,
            check=True,
        )

        # Modify plan.yaml differently on main (conflict)
        plan_main.write_text(
            "phases:\n"
            "  - id: core\n"
            "    steps:\n"
            "      - id: test-step\n"
            "        description: Different on main\n"
        )
        subprocess.run(
            ["git", "add", "plan.yaml"], cwd=temp_git_repo, capture_output=True, check=True
        )
        subprocess.run(
            ["git", "commit", "-m", "Modify plan.yaml on main"],
            cwd=temp_git_repo,
            capture_output=True,
            check=True,
        )

        # Merge back - should auto-resolve
        result = expect_success(
            asyncio.run(merge(step_id, binding.worktree_path, cwd=temp_git_repo))
        )

        # Must classify as AUTO_RESOLVED_CONFLICT
        assert result.outcome == MergeOutcome.AUTO_RESOLVED_CONFLICT
        assert "plan.yaml" in result.conflicted_files

    def test_trivial_conflict_lock_file(self, temp_git_repo: Path) -> None:
        """merge() MUST auto-resolve *.lock files."""
        step_id = "trivial-lock"
        base_dir = temp_git_repo / ".vectl" / "worktrees"
        base_dir.mkdir(parents=True, exist_ok=True)

        binding = expect_success(asyncio.run(create(step_id, base_dir=base_dir, cwd=temp_git_repo)))

        # Add lock file on worktree
        lock_file = binding.worktree_path / "package.lock"
        lock_file.write_text('{"locked": "branch"}\n')
        subprocess.run(
            ["git", "add", "package.lock"],
            cwd=binding.worktree_path,
            capture_output=True,
            check=True,
        )
        subprocess.run(
            ["git", "commit", "-m", "Add lock on branch"],
            cwd=binding.worktree_path,
            capture_output=True,
            check=True,
        )

        # Add different lock file on main
        lock_main = temp_git_repo / "package.lock"
        lock_main.write_text('{"locked": "main"}\n')
        subprocess.run(
            ["git", "add", "package.lock"], cwd=temp_git_repo, capture_output=True, check=True
        )
        subprocess.run(
            ["git", "commit", "-m", "Add lock on main"],
            cwd=temp_git_repo,
            capture_output=True,
            check=True,
        )

        # Merge should auto-resolve
        result = expect_success(
            asyncio.run(merge(step_id, binding.worktree_path, cwd=temp_git_repo))
        )

        assert result.outcome == MergeOutcome.AUTO_RESOLVED_CONFLICT


class TestMergeNonTrivialConflict:
    """Tests for non-trivial conflict classification."""

    def test_non_trivial_conflict_source_code(self, temp_git_repo: Path) -> None:
        """merge() MUST classify source code conflicts as NON_TRIVIAL_CONFLICT.

        Non-trivial conflict path: conflict in .py file is NOT auto-resolved.
        """
        step_id = "nontrivial-code"
        base_dir = temp_git_repo / ".vectl" / "worktrees"
        base_dir.mkdir(parents=True, exist_ok=True)

        binding = expect_success(asyncio.run(create(step_id, base_dir=base_dir, cwd=temp_git_repo)))

        # Add source file on worktree
        src_file = binding.worktree_path / "main.py"
        src_file.write_text('def hello():\n    print("branch")\n')
        subprocess.run(
            ["git", "add", "main.py"], cwd=binding.worktree_path, capture_output=True, check=True
        )
        subprocess.run(
            ["git", "commit", "-m", "Add main.py on branch"],
            cwd=binding.worktree_path,
            capture_output=True,
            check=True,
        )

        # Add same file on main with different content (conflict)
        src_main = temp_git_repo / "main.py"
        src_main.write_text('def hello():\n    print("main")\n')
        subprocess.run(
            ["git", "add", "main.py"], cwd=temp_git_repo, capture_output=True, check=True
        )
        subprocess.run(
            ["git", "commit", "-m", "Add main.py on main"],
            cwd=temp_git_repo,
            capture_output=True,
            check=True,
        )

        # Merge - should detect non-trivial conflict
        result = expect_success(
            asyncio.run(
                merge(step_id, binding.worktree_path, target_branch="main", cwd=temp_git_repo)
            )
        )

        assert result.outcome == MergeOutcome.NON_TRIVIAL_CONFLICT
        assert "main.py" in result.conflicted_files
        assert result.resolver_dispatch is not None
        assert result.resolver_dispatch.step_id == step_id
        assert result.resolver_dispatch.source_branch == expect_success(derive_branch_name(step_id))
        assert result.resolver_dispatch.target_branch == "main"

    def test_non_trivial_conflict_returns_resolver_dispatch(self, temp_git_repo: Path) -> None:
        """NON_TRIVIAL_CONFLICT MUST return ConflictResolverDispatch with metadata.

        Source: docs/DRIVER-ARCHITECTURE.md Section 2.6 non-responsibility.
        """
        step_id = "resolver-dispatch"
        base_dir = temp_git_repo / ".vectl" / "worktrees"
        base_dir.mkdir(parents=True, exist_ok=True)

        binding = expect_success(asyncio.run(create(step_id, base_dir=base_dir, cwd=temp_git_repo)))

        # Create conflict on both branches
        (binding.worktree_path / "conflict.txt").write_text("branch version\n")
        subprocess.run(
            ["git", "add", "conflict.txt"],
            cwd=binding.worktree_path,
            capture_output=True,
            check=True,
        )
        subprocess.run(
            ["git", "commit", "-m", "Add file on branch"],
            cwd=binding.worktree_path,
            capture_output=True,
            check=True,
        )

        (temp_git_repo / "conflict.txt").write_text("main version\n")
        subprocess.run(
            ["git", "add", "conflict.txt"], cwd=temp_git_repo, capture_output=True, check=True
        )
        subprocess.run(
            ["git", "commit", "-m", "Add file on main"],
            cwd=temp_git_repo,
            capture_output=True,
            check=True,
        )

        result = expect_success(
            asyncio.run(merge(step_id, binding.worktree_path, cwd=temp_git_repo))
        )

        # NON_TRIVIAL_CONFLICT dispatches resolver path (does not silent narrow)
        assert result.outcome == MergeOutcome.NON_TRIVIAL_CONFLICT
        assert result.resolver_dispatch is not None
        assert result.resolver_dispatch.worktree_path == binding.worktree_path
        assert len(result.resolver_dispatch.conflicted_files) > 0


# =============================================================================
# Integration Tests: Cleanup worktree
# =============================================================================


class TestCleanup:
    """Tests for worktree and branch cleanup."""

    def test_cleanup_removes_worktree(self, temp_git_repo: Path) -> None:
        """cleanup() MUST remove worktree directory."""
        step_id = "cleanup-test"
        base_dir = temp_git_repo / ".vectl" / "worktrees"
        base_dir.mkdir(parents=True, exist_ok=True)

        binding = expect_success(asyncio.run(create(step_id, base_dir=base_dir, cwd=temp_git_repo)))
        assert binding.worktree_path.exists()

        expect_success(asyncio.run(cleanup(step_id, binding.worktree_path, cwd=temp_git_repo)))

        assert not binding.worktree_path.exists()

    def test_cleanup_removes_branch(self, temp_git_repo: Path) -> None:
        """cleanup() MUST remove vectl/step-{step_id} branch."""
        step_id = "cleanup-branch"
        base_dir = temp_git_repo / ".vectl" / "worktrees"
        base_dir.mkdir(parents=True, exist_ok=True)

        binding = expect_success(asyncio.run(create(step_id, base_dir=base_dir, cwd=temp_git_repo)))
        branch_name = expect_success(derive_branch_name(step_id))

        # Verify branch exists
        result = subprocess.run(
            ["git", "branch", "--list", branch_name],
            cwd=temp_git_repo,
            capture_output=True,
            text=True,
            check=True,
        )
        assert branch_name in result.stdout

        # Cleanup
        expect_success(asyncio.run(cleanup(step_id, binding.worktree_path, cwd=temp_git_repo)))

        # Verify branch is deleted
        result = subprocess.run(
            ["git", "branch", "--list", branch_name],
            cwd=temp_git_repo,
            capture_output=True,
            text=True,
            check=True,
        )
        assert branch_name not in result.stdout

    def test_cleanup_best_effort_on_missing_worktree(self, tmp_path: Path) -> None:
        """cleanup() MUST NOT raise on missing worktree (best-effort)."""
        step_id = "missing-worktree"
        base_dir = tmp_path / "worktrees"
        base_dir.mkdir(parents=True, exist_ok=True)

        # Non-existent worktree path
        non_existent_path = base_dir / "does_not_exist"

        # Should not raise
        expect_success(asyncio.run(cleanup(step_id, non_existent_path, cwd=tmp_path)))


# =============================================================================
# Unit Tests: Pattern matching
# =============================================================================


class TestTrivialConflictPatterns:
    """Tests for trivial conflict pattern matching."""

    def test_plan_yaml_is_trivial(self) -> None:
        """plan.yaml MUST be in TRIVIAL_CONFLICT_PATTERNS."""
        assert "plan.yaml" in TRIVIAL_CONFLICT_PATTERNS

    def test_claims_json_is_trivial(self) -> None:
        """claims.json MUST be in TRIVIAL_CONFLICT_PATTERNS."""
        assert "claims.json" in TRIVIAL_CONFLICT_PATTERNS

    def test_lock_files_are_trivial(self) -> None:
        """*.lock and *.lockb MUST be in TRIVIAL_CONFLICT_PATTERNS."""
        assert "*.lock" in TRIVIAL_CONFLICT_PATTERNS
        assert "*.lockb" in TRIVIAL_CONFLICT_PATTERNS

    def test_vectl_claims_path_is_trivial(self) -> None:
        """.git/vectl/claims.json MUST be in TRIVIAL_CONFLICT_PATTERNS."""
        assert ".git/vectl/claims.json" in TRIVIAL_CONFLICT_PATTERNS


# =============================================================================
# Unit Tests: MergeStrategy and MergeOutcome enums
# =============================================================================


class TestMergeStrategy:
    """Tests for MergeStrategy enum."""

    def test_squash_is_only_strategy(self) -> None:
        """MergeStrategy.SQUASH MUST be the default/only strategy.

        Source: docs/DRIVER-ARCHITECTURE.md Section 2.6.
        """
        assert MergeStrategy.SQUASH.value == "squash"
        assert len(MergeStrategy) == 1

    def test_merge_strategy_is_str_enum(self) -> None:
        """MergeStrategy MUST inherit from str, Enum."""
        assert isinstance(MergeStrategy.SQUASH, str)
        assert MergeStrategy.SQUASH == "squash"


class TestMergeOutcome:
    """Tests for MergeOutcome enum."""

    def test_outcome_values(self) -> None:
        """MergeOutcome MUST have exactly three values.

        Source: docs/DRIVER-ARCHITECTURE.md Section 2.6.
        """
        assert MergeOutcome.CLEAN_MERGE.value == "clean_merge"
        assert MergeOutcome.AUTO_RESOLVED_CONFLICT.value == "auto_resolved_conflict"
        assert MergeOutcome.NON_TRIVIAL_CONFLICT.value == "non_trivial_conflict"
        assert len(MergeOutcome) == 3

    def test_merge_outcome_is_str_enum(self) -> None:
        """MergeOutcome MUST inherit from str, Enum."""
        assert isinstance(MergeOutcome.CLEAN_MERGE, str)


class TestWorktreeBinding:
    """Tests for WorktreeBinding dataclass."""

    def test_binding_is_frozen(self) -> None:
        """WorktreeBinding MUST be immutable (frozen=True)."""
        binding = WorktreeBinding(
            step_id="test",
            worktree_path=Path("/tmp/test"),
            branch_name="vectl/step-test",
            reused_existing=False,
        )
        assert binding.step_id == "test"
        assert binding.reused_existing is False

        # Verify immutability
        import dataclasses

        assert dataclasses.is_dataclass(binding)
        # Frozen dataclasses raise FrozenInstanceError on assignment
        with pytest.raises(dataclasses.FrozenInstanceError):
            binding.reused_existing = True  # type: ignore[misc]

    def test_binding_reused_existing_flag(self) -> None:
        """WorktreeBinding.reused_existing MUST distinguish fresh vs reuse."""
        fresh = WorktreeBinding(
            step_id="test",
            worktree_path=Path("/tmp/test"),
            branch_name="vectl/step-test",
            reused_existing=False,
        )
        reused = WorktreeBinding(
            step_id="test",
            worktree_path=Path("/tmp/test"),
            branch_name="vectl/step-test",
            reused_existing=True,
        )
        assert fresh.reused_existing is False
        assert reused.reused_existing is True


class TestMergeResult:
    """Tests for MergeResult dataclass."""

    def test_merge_result_frozen(self) -> None:
        """MergeResult MUST be immutable (frozen=True)."""
        result = MergeResult(outcome=MergeOutcome.CLEAN_MERGE)

        import dataclasses

        with pytest.raises(dataclasses.FrozenInstanceError):
            result.outcome = MergeOutcome.NON_TRIVIAL_CONFLICT  # type: ignore[misc]

    def test_clean_merge_default_fields(self) -> None:
        """CLEAN_MERGE result MUST have empty conflicted_files, no resolver_dispatch."""
        result = MergeResult(outcome=MergeOutcome.CLEAN_MERGE)
        assert result.conflicted_files == ()
        assert result.resolver_dispatch is None

    def test_non_trivial_conflict_must_have_resolver_dispatch(self) -> None:
        """NON_TRIVIAL_CONFLICT MUST include resolver_dispatch."""
        dispatch = ConflictResolverDispatch(
            step_id="test-step",
            worktree_path=Path("/tmp/test"),
            source_branch="vectl/step-test-step",
            target_branch="master",
            conflicted_files=("main.py",),
        )
        result = MergeResult(
            outcome=MergeOutcome.NON_TRIVIAL_CONFLICT,
            conflicted_files=("main.py",),
            resolver_dispatch=dispatch,
        )
        assert result.outcome == MergeOutcome.NON_TRIVIAL_CONFLICT
        assert result.conflicted_files == ("main.py",)
        assert result.resolver_dispatch is not None
        assert result.resolver_dispatch.step_id == "test-step"
