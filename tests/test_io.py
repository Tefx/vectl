"""Tests for YAML I/O, CAS behavior, and backup handling."""

import os
import subprocess
from pathlib import Path

import pytest

from vectl.io import (
    _backup_definition,
    _git_commit_plan,
    load_plan_definition as load_plan,
    save_plan,
)
from vectl.models import (
    CASConflictError,
    Clipboard,
    Phase,
    PhaseStatus,
    Plan,
    PlanIOError,
    Step,
    StepStatus,
)


@pytest.fixture
def sample_plan() -> Plan:
    return Plan(
        project="test-project",
        context="Test context",
        phases=[
            Phase(
                id="p1",
                name="Phase 1",
                status=PhaseStatus.PENDING,
                steps=[
                    Step(id="s1", name="Step 1"),
                    Step(id="s2", name="Step 2", depends_on=["s1"]),
                ],
            ),
        ],
    )


class TestLoadPlanDefinition:
    def test_load_valid(self, tmp_path: Path, sample_plan: Plan) -> None:
        path = tmp_path / "plan.yaml"
        save_plan(sample_plan, path)
        plan, hash_ = load_plan(path)
        assert plan.project == "test-project"
        assert len(plan.phases) == 1
        assert len(plan.phases[0].steps) == 2
        assert isinstance(hash_, str)
        assert len(hash_) == 64

    def test_load_nonexistent(self, tmp_path: Path) -> None:
        with pytest.raises(PlanIOError, match="not found"):
            load_plan(tmp_path / "nope.yaml")

    def test_load_invalid_yaml(self, tmp_path: Path) -> None:
        path = tmp_path / "bad.yaml"
        path.write_text("{{{{invalid yaml", encoding="utf-8")
        with pytest.raises(PlanIOError, match="Invalid YAML"):
            load_plan(path)

    def test_load_non_mapping(self, tmp_path: Path) -> None:
        path = tmp_path / "list.yaml"
        path.write_text("- item1\n- item2\n", encoding="utf-8")
        with pytest.raises(PlanIOError, match="must be a YAML mapping"):
            load_plan(path)


class TestSavePlan:
    def test_save_creates_file(self, tmp_path: Path, sample_plan: Plan) -> None:
        path = tmp_path / "plan.yaml"
        hash_ = save_plan(sample_plan, path)
        assert path.exists()
        assert isinstance(hash_, str)

    def test_save_creates_parent_dirs(self, tmp_path: Path, sample_plan: Plan) -> None:
        path = tmp_path / "sub" / "dir" / "plan.yaml"
        save_plan(sample_plan, path)
        assert path.exists()

    def test_round_trip(self, tmp_path: Path, sample_plan: Plan) -> None:
        path = tmp_path / "plan.yaml"
        save_plan(sample_plan, path)
        loaded, _ = load_plan(path)
        assert loaded.project == sample_plan.project
        assert len(loaded.phases) == len(sample_plan.phases)
        assert loaded.phases[0].steps[0].id == sample_plan.phases[0].steps[0].id
        assert loaded.phases[0].steps[1].depends_on == ["s1"]

    def test_save_with_commit_message_creates_git_commit(
        self, tmp_path: Path, sample_plan: Plan
    ) -> None:
        repo_root = tmp_path / "repo"
        repo_root.mkdir()

        for command in (
            ["git", "init"],
            ["git", "config", "user.email", "test@example.com"],
            ["git", "config", "user.name", "Test User"],
        ):
            result = subprocess.run(command, cwd=repo_root, capture_output=True, text=True)
            assert result.returncode == 0, result.stderr

        plan_path = repo_root / "plan.yaml"
        save_plan(sample_plan, plan_path)

        for command in (
            ["git", "add", "plan.yaml"],
            ["git", "commit", "-m", "seed"],
        ):
            result = subprocess.run(command, cwd=repo_root, capture_output=True, text=True)
            assert result.returncode == 0, result.stderr

        updated = sample_plan.model_copy(deep=True)
        updated.context = "Updated context"
        save_plan(updated, plan_path, commit_message="update plan")

        commit_count = subprocess.run(
            ["git", "rev-list", "--count", "HEAD"],
            cwd=repo_root,
            capture_output=True,
            text=True,
        )
        assert commit_count.returncode == 0, commit_count.stderr
        assert commit_count.stdout.strip() == "2"

        latest_subject = subprocess.run(
            ["git", "log", "-1", "--pretty=%s"],
            cwd=repo_root,
            capture_output=True,
            text=True,
        )
        assert latest_subject.returncode == 0, latest_subject.stderr
        assert latest_subject.stdout.strip() == "update plan"

    def test_git_commit_plan_retries_once_on_index_lock_contention(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls: list[list[str]] = []
        sleep_calls: list[float] = []

        def fake_run(
            command: list[str], cwd: str, capture_output: bool, text: bool, timeout: int
        ) -> subprocess.CompletedProcess[str]:
            assert cwd == str(tmp_path)
            assert capture_output is True
            assert text is True
            assert timeout == 10
            calls.append(command)
            if len(calls) == 1:
                return subprocess.CompletedProcess(
                    args=command,
                    returncode=1,
                    stdout="",
                    stderr="fatal: Unable to create '.git/index.lock': File exists.",
                )
            return subprocess.CompletedProcess(args=command, returncode=0, stdout="", stderr="")

        monkeypatch.setattr("vectl.io.subprocess.run", fake_run)
        monkeypatch.setattr("vectl.io.time.sleep", lambda seconds: sleep_calls.append(seconds))

        result = _git_commit_plan(tmp_path / "plan.yaml", "retry message")

        assert result is True
        assert calls == [
            ["git", "commit", "--only", "--no-verify", "plan.yaml", "-m", "retry message"],
            ["git", "commit", "--only", "--no-verify", "plan.yaml", "-m", "retry message"],
        ]
        assert sleep_calls == [0.1]


class TestCAS:
    def test_cas_success(self, tmp_path: Path, sample_plan: Plan) -> None:
        path = tmp_path / "plan.yaml"
        hash1 = save_plan(sample_plan, path)
        hash2 = save_plan(sample_plan, path, expected_hash=hash1)
        assert isinstance(hash2, str)

    def test_cas_conflict(self, tmp_path: Path, sample_plan: Plan) -> None:
        path = tmp_path / "plan.yaml"
        save_plan(sample_plan, path)
        path.write_text("version: 99\nproject: tampered\n", encoding="utf-8")
        with pytest.raises(CASConflictError):
            save_plan(sample_plan, path, expected_hash="wrong-hash")

    def test_cas_no_hash_skips_check(self, tmp_path: Path, sample_plan: Plan) -> None:
        path = tmp_path / "plan.yaml"
        save_plan(sample_plan, path)
        hash_ = save_plan(sample_plan, path)
        assert isinstance(hash_, str)

    def test_cas_new_file_no_conflict(self, tmp_path: Path, sample_plan: Plan) -> None:
        path = tmp_path / "new.yaml"
        hash_ = save_plan(sample_plan, path, expected_hash="anything")
        assert path.exists()
        assert isinstance(hash_, str)


class TestDefinitionBackup:
    @staticmethod
    def _force_not_linked_worktree(monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("vectl.plan_path.is_linked_worktree", lambda: (False, None))

    def test_backup_created_on_save(
        self, tmp_path: Path, sample_plan: Plan, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._force_not_linked_worktree(monkeypatch)
        repo_root = tmp_path / "repo"
        repo_root.mkdir()
        (repo_root / ".git").mkdir()
        plan_path = repo_root / "plan.yaml"

        save_plan(sample_plan, plan_path)
        _backup_definition(plan_path)

        backup_path = repo_root / ".git" / "vectl" / "plan.yaml.bak"
        assert backup_path.exists()

    def test_backup_matches_plan_content(
        self, tmp_path: Path, sample_plan: Plan, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._force_not_linked_worktree(monkeypatch)
        repo_root = tmp_path / "repo"
        repo_root.mkdir()
        (repo_root / ".git").mkdir()
        plan_path = repo_root / "plan.yaml"

        save_plan(sample_plan, plan_path)
        _backup_definition(plan_path)

        backup_path = repo_root / ".git" / "vectl" / "plan.yaml.bak"
        assert backup_path.read_text(encoding="utf-8") == plan_path.read_text(encoding="utf-8")

    def test_backup_skipped_in_linked_worktree(self, tmp_path: Path, sample_plan: Plan) -> None:
        repo_root = tmp_path / "repo"
        repo_root.mkdir()
        (repo_root / ".git").write_text(
            "gitdir: /tmp/shared/.git/worktrees/repo\n", encoding="utf-8"
        )
        plan_path = repo_root / "plan.yaml"

        save_plan(sample_plan, plan_path)
        _backup_definition(plan_path)

        backup_path = repo_root / ".git" / "vectl" / "plan.yaml.bak"
        assert not backup_path.exists()


class TestCorruptedBackupHandling:
    def test_load_corrupted_yaml_backup(self, tmp_path: Path) -> None:
        backup_path = tmp_path / "plan.yaml.bak"
        backup_path.write_text(
            "project: test\n"
            "phases:\n"
            "  - name: Phase\n"
            "    steps:\n"
            "      - name: Step\n"
            "        depends_on: [undefined!!]\n",
            encoding="utf-8",
        )
        with pytest.raises(PlanIOError, match="Invalid"):
            load_plan(backup_path)

    def test_load_invalid_plan_structure_backup(self, tmp_path: Path) -> None:
        backup_path = tmp_path / "plan.yaml.bak"
        backup_path.write_text("project: test\nphases: 'not-a-list'\n", encoding="utf-8")
        with pytest.raises(PlanIOError, match="Invalid plan structure"):
            load_plan(backup_path)


class TestClipboardRoundTrip:
    def test_clipboard_round_trip(self, tmp_path: Path) -> None:
        plan = Plan(
            project="test",
            clipboard=Clipboard(
                author="agent-1",
                summary="Handoff note",
                content="Multi-line\ncontent\nhere",
                written_at="2026-02-16T10:00:00Z",
                expires_at="2026-02-17T10:00:00Z",
            ),
            phases=[Phase(id="p1", name="P1", steps=[Step(id="s1", name="S1")])],
        )
        path = tmp_path / "plan.yaml"
        save_plan(plan, path)

        loaded, _ = load_plan(path)
        assert loaded.clipboard is not None
        assert loaded.clipboard.author == "agent-1"
        assert loaded.clipboard.summary == "Handoff note"
        assert loaded.clipboard.content == "Multi-line\ncontent\nhere"
        assert loaded.clipboard.written_at == "2026-02-16T10:00:00Z"
        assert loaded.clipboard.expires_at == "2026-02-17T10:00:00Z"

    def test_clipboard_omitted_when_none(self, tmp_path: Path) -> None:
        plan = Plan(
            project="test",
            clipboard=None,
            phases=[Phase(id="p1", name="P1", steps=[Step(id="s1", name="S1")])],
        )
        path = tmp_path / "plan.yaml"
        save_plan(plan, path)

        yaml_content = path.read_text(encoding="utf-8")
        assert "clipboard" not in yaml_content


class TestLockStatusRoundtrip:
    def test_pending_phase_with_unmet_dep_is_locked_after_roundtrip(self, tmp_path: Path) -> None:
        from vectl.core import recalc_lock_status

        plan = Plan(
            project="roundtrip-test",
            phases=[
                Phase(
                    id="p1",
                    name="Phase 1",
                    status=PhaseStatus.PENDING,
                    steps=[Step(id="s1", name="Step 1")],
                ),
                Phase(
                    id="p2",
                    name="Phase 2",
                    status=PhaseStatus.PENDING,
                    depends_on=["p1"],
                    steps=[Step(id="s2", name="Step 2")],
                ),
            ],
        )

        recalc_lock_status(plan)
        path = tmp_path / "plan.yaml"
        save_plan(plan, path)

        loaded, _ = load_plan(path)
        assert loaded.phases[1].status == PhaseStatus.LOCKED

    def test_locked_phase_with_all_deps_done_is_unlocked_after_roundtrip(
        self, tmp_path: Path
    ) -> None:
        from vectl.core import recalc_lock_status

        plan = Plan(
            project="roundtrip-unlock-test",
            phases=[
                Phase(
                    id="p1",
                    name="Phase 1",
                    status=PhaseStatus.DONE,
                    steps=[Step(id="s1", name="Step 1")],
                ),
                Phase(
                    id="p2",
                    name="Phase 2",
                    status=PhaseStatus.LOCKED,
                    depends_on=["p1"],
                    steps=[Step(id="s2", name="Step 2")],
                ),
            ],
        )

        recalc_lock_status(plan)
        path = tmp_path / "plan.yaml"
        save_plan(plan, path)

        loaded, _ = load_plan(path)
        assert loaded.phases[1].status == PhaseStatus.PENDING


class TestBackupCrashSafety:
    def test_backup_crash_safety_no_temp_file_left_behind(
        self, tmp_path: Path, sample_plan: Plan, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from unittest.mock import patch

        monkeypatch.setattr("vectl.plan_path.is_linked_worktree", lambda: (False, None))

        repo_root = tmp_path / "repo"
        repo_root.mkdir()
        (repo_root / ".git").mkdir()
        plan_path = repo_root / "plan.yaml"

        save_plan(sample_plan, plan_path)

        backup_dir = repo_root / ".git" / "vectl"
        original_replace = os.replace

        def mock_replace(src: str, dst: str) -> None:
            if src.endswith(".tmp"):
                original_replace(src, dst)
                raise OSError("Simulated I/O error")
            original_replace(src, dst)

        with patch.object(os, "replace", side_effect=mock_replace):
            with pytest.raises(OSError):
                _backup_definition(plan_path)

        temp_files = list(backup_dir.glob("*.tmp"))
        assert len(temp_files) == 0
