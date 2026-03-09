"""Tests for YAML I/O, CAS behavior, and backup handling."""

import ast
import inspect
import os
import subprocess
import threading
import time
from pathlib import Path

import pytest

from vectl.io import (
    _backup_definition,
    _clean_dict,
    _git_commit_plan,
    _write_plan_content,
    load_plan_definition as load_plan,
    save_plan,
)
from vectl.models import (
    AffinityMode,
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


class TestAffinityCleanup:
    def test_clean_dict_omits_affinity_defaults_from_models(self) -> None:
        payload = {
            "default_affinity": Plan.model_fields["default_affinity"].default,
            "phases": [
                {
                    "steps": [
                        {
                            "id": "s1",
                            "name": "Step 1",
                            "affinity_override": Step.model_fields["affinity_override"].default,
                            "affinity_override_by": Step.model_fields[
                                "affinity_override_by"
                            ].default,
                            "affinity_override_at": Step.model_fields[
                                "affinity_override_at"
                            ].default,
                        }
                    ]
                }
            ],
        }

        cleaned = _clean_dict(payload)
        step = cleaned["phases"][0]["steps"][0]

        assert "default_affinity" not in cleaned
        assert "affinity_override" not in step
        assert "affinity_override_by" not in step
        assert "affinity_override_at" not in step

    def test_clean_dict_preserves_non_default_affinity_values(self) -> None:
        payload = {
            "default_affinity": AffinityMode.EXCLUSIVE,
            "phases": [
                {
                    "steps": [
                        {
                            "id": "s1",
                            "name": "Step 1",
                            "affinity_override": True,
                            "affinity_override_by": "planner",
                            "affinity_override_at": "2026-03-08T10:00:00Z",
                        }
                    ]
                }
            ],
        }

        cleaned = _clean_dict(payload)
        step = cleaned["phases"][0]["steps"][0]

        assert cleaned["default_affinity"] == AffinityMode.EXCLUSIVE
        assert step["affinity_override"] is True
        assert step["affinity_override_by"] == "planner"
        assert step["affinity_override_at"] == "2026-03-08T10:00:00Z"

    def test_clean_dict_avoids_hardcoded_affinity_field_name_literals(self) -> None:
        source = inspect.getsource(_clean_dict)
        tree = ast.parse(source)
        string_constants = {
            node.value
            for node in ast.walk(tree)
            if isinstance(node, ast.Constant) and isinstance(node.value, str)
        }

        assert "affinity_override" not in string_constants
        assert "default_affinity" not in string_constants


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


class TestAnimaIncidentReproducer:
    """Reproducer for the anima orchestrator/worktree split-brain incident.

    Symptom: One writer leaves `.git/vectl/claims.json` with `main:<step_id>`
    while a later concurrent write leaves `plan.yaml`/derived step state visible
    as pending.

    This test creates the split-brain state and verifies the downstream symptoms:
    - claim rejects with an existing branch claim (false positive)
    - show reads pending (inconsistent with claims.json)
    - complete refuses because step is not recognized as claimed
    """

    def test_split_brain_claim_rejects_but_plan_shows_pending(
        self, tmp_path: Path, sample_plan: Plan, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Reproducer: claims.json has claim but plan.yaml shows step as pending.

        This simulates the race condition where:
        1. Writer A acquires claim in claims.json
        2. Writer B tries to claim same step but fails (CAS conflict, worktree issue, etc.)
        3. Result: claims.json has entry, plan.yaml doesn't reflect claim

        This test demonstrates the downstream symptoms:
        - claim rejects with "already claimed" from claims.json check
        - but plan.yaml shows step as PENDING (not CLAIMED)
        - complete refuses because plan.yaml step is not CLAIMED
        """
        import os
        from vectl.claims import ClaimEntry, acquire_claim, load_claims, save_claims
        from vectl.lifecycle import claim_step, complete_step
        from vectl.plan_path import resolve_claims_path
        from vectl.claims import get_current_branch

        # Setup: create a git repo so get_current_branch() works
        repo_root = tmp_path / "repo"
        repo_root.mkdir()
        subprocess.run(["git", "init"], cwd=repo_root, capture_output=True)
        subprocess.run(
            ["git", "config", "user.email", "test@test.com"],
            cwd=repo_root,
            capture_output=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "Test"],
            cwd=repo_root,
            capture_output=True,
        )
        subprocess.run(
            ["git", "commit", "--allow-empty", "-m", "init"],
            cwd=repo_root,
            capture_output=True,
        )

        # Change to the repo directory so get_current_branch() returns the correct name
        original_cwd = os.getcwd()
        os.chdir(repo_root)
        try:
            # Get the actual branch name from vectl's get_current_branch()
            branch_name = get_current_branch()
        finally:
            os.chdir(original_cwd)

        plan_path = repo_root / "plan.yaml"
        save_plan(sample_plan, plan_path)
        claims_path = resolve_claims_path(plan_path)

        # Simulate split-brain: manually add claim to claims.json without
        # updating plan.yaml (simulates the race condition)
        claims_path.parent.mkdir(parents=True, exist_ok=True)
        save_claims(
            {
                f"{branch_name}:s1": ClaimEntry(
                    step_id="s1",
                    branch=branch_name,
                    agent="agent-a",
                    claimed_at="2026-03-09T00:00:00Z",
                )
            },
            claims_path,
        )

        # Verify split-brain state exists
        claims = load_claims(claims_path)
        key = f"{branch_name}:s1"
        assert key in claims, "Precondition: claims.json should have claim entry"

        plan_loaded, _ = load_plan(plan_path)
        found = plan_loaded.find_step("s1")
        assert found is not None
        _, step = found
        assert step.status == StepStatus.PENDING, (
            "Precondition: plan.yaml should show step as PENDING"
        )

        # Symptom 1: claim_step should fail because it checks claims.json first
        # even though plan.yaml shows PENDING - this is the inconsistency
        os.chdir(repo_root)
        try:
            plan_to_claim, _ = load_plan(plan_path)
            with pytest.raises(Exception) as exc_info:
                claim_step(plan_to_claim, "s1", "agent-b", claims_path=claims_path)

            assert "already claimed" in str(exc_info.value).lower()
        finally:
            os.chdir(original_cwd)

        # Symptom 2: complete_step should fail because plan.yaml shows PENDING
        # (even though claims.json has the claim)
        original_plan, _ = load_plan(plan_path)
        with pytest.raises(Exception) as exc_info:
            complete_step(original_plan, "s1", "evidence")

        assert "claimed" in str(exc_info.value).lower()

    def test_concurrent_claim_vs_plan_write_race(
        self, tmp_path: Path, sample_plan: Plan, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Race harness: concurrent claim acquisition vs plan.yaml save failure.

        This test exercises the real concurrent path where:
        1. Thread A acquires claim in claims.json (succeeds)
        2. Thread B writes plan.yaml but fails (CAS conflict or worktree issue)
        3. Result: split-brain state

        The test verifies that after such a race:
        - claims.json contains the claim entry
        - plan.yaml step status is PENDING (not CLAIMED)
        """
        from vectl.claims import acquire_claim, load_claims, save_claims
        from vectl.plan_path import resolve_claims_path

        plan_path = tmp_path / "plan.yaml"
        save_plan(sample_plan, plan_path)
        claims_path = resolve_claims_path(plan_path)
        claims_path.parent.mkdir(parents=True, exist_ok=True)

        seed_hash = save_plan(sample_plan, plan_path)

        # Track execution order
        events: dict[str, threading.Event] = {
            "claim_acquired": threading.Event(),
            "plan_save_started": threading.Event(),
            "done": threading.Event(),
        }
        errors: dict[str, Exception] = {}

        # Thread A: acquires claim successfully
        def thread_a_claim() -> None:
            try:
                acquire_claim("s1", "main", "agent-a", claims_path)
                events["claim_acquired"].set()
                # Wait for thread B to start its save
                events["plan_save_started"].wait(timeout=2.0)
                time.sleep(0.1)  # Let thread B complete its write
            except Exception as exc:
                errors["thread_a"] = exc

        # Thread B: tries to save plan but fails due to CAS conflict
        # (simulating worktree issue or race)
        def thread_b_save() -> None:
            try:
                events["claim_acquired"].wait(timeout=2.0)
                events["plan_save_started"].set()

                # Modify the plan (simulating a claim update)
                plan = sample_plan.model_copy(deep=True)
                found_step = plan.find_step("s1")
                assert found_step is not None
                _, step = found_step
                step.status = StepStatus.CLAIMED
                step.claimed_by = "agent-a"

                # But use wrong hash to simulate CAS conflict / failure
                save_plan(plan, plan_path, expected_hash="wrong-hash")
            except Exception as exc:
                errors["thread_b"] = exc

        thread1 = threading.Thread(target=thread_a_claim, name="thread-claim")
        thread2 = threading.Thread(target=thread_b_save, name="thread-save")

        thread1.start()
        thread2.start()
        thread1.join(timeout=5.0)
        thread2.join(timeout=5.0)

        # Verify split-brain state
        claims = load_claims(claims_path)
        assert "main:s1" in claims, "claims.json should have claim entry (thread A succeeded)"

        plan_loaded, _ = load_plan(plan_path)
        found_step = plan_loaded.find_step("s1")
        assert found_step is not None
        _, step = found_step
        assert step.status == StepStatus.PENDING, (
            "plan.yaml should show PENDING (thread B failed due to CAS)"
        )

        # This is the split-brain: claims.json says claimed, plan.yaml says pending
