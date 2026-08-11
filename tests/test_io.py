"""Tests for YAML I/O, CAS behavior, and backup handling."""

import ast
from dataclasses import asdict, is_dataclass
import hashlib
import inspect
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest
import yaml

import vectl.io as io_module
from vectl.io import (
    _backup_definition,
    _clean_dict,
    _git_commit_plan,
    _write_plan_content,
    save_plan,
)
from vectl.io import (
    load_plan_definition as load_plan,
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

    def test_c_loader_is_semantically_equivalent_to_safe_loader(
        self, tmp_path: Path, sample_plan: Plan
    ) -> None:
        path = tmp_path / "plan.yaml"
        save_plan(sample_plan, path)
        content = path.read_text(encoding="utf-8")

        safe_raw = yaml.safe_load(content)
        optimized_raw = yaml.load(
            content,
            Loader=getattr(yaml, "CSafeLoader", yaml.SafeLoader),
        )

        assert Plan(**optimized_raw).model_dump() == Plan(**safe_raw).model_dump()


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


class TestDeletedStepIsolationField:
    def test_load_legacy_step_isolation_payload_does_not_round_trip(self, tmp_path: Path) -> None:
        path = tmp_path / "plan.yaml"
        deleted_field = "".join(("isol", "ation"))
        path.write_text(
            "project: test\n"
            "phases:\n"
            "  - id: p1\n"
            "    name: Phase 1\n"
            "    steps:\n"
            "      - id: s1\n"
            "        name: Step 1\n"
            f"        {deleted_field}: independent\n",
            encoding="utf-8",
        )

        plan, _ = load_plan(path)
        step = plan.phases[0].steps[0]
        assert "isolation" not in Step.model_fields
        assert "isolation" not in step.model_dump()

        save_plan(plan, path)

        assert "isolation" not in path.read_text(encoding="utf-8")


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

    def test_atomic_cas_and_write_are_serialized_under_plan_lock(
        self, tmp_path: Path, sample_plan: Plan, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        path = tmp_path / "plan.yaml"
        seed_hash = save_plan(sample_plan, path)

        first_plan = sample_plan.model_copy(deep=True)
        first_plan.context = "writer-1"
        second_plan = sample_plan.model_copy(deep=True)
        second_plan.context = "writer-2"

        first_write_started = threading.Event()
        original_write = _write_plan_content

        def delayed_first_write(target_path: Path, content: str) -> None:
            if not first_write_started.is_set():
                first_write_started.set()
                time.sleep(0.2)
            original_write(target_path, content)

        monkeypatch.setattr("vectl.io._write_plan_content", delayed_first_write)

        results: dict[str, str] = {}
        errors: dict[str, Exception] = {}

        def writer_one() -> None:
            try:
                save_plan(first_plan, path, expected_hash=seed_hash)
                results["writer_one"] = "saved"
            except Exception as exc:
                errors["writer_one"] = exc

        def writer_two() -> None:
            try:
                save_plan(second_plan, path, expected_hash=seed_hash)
                results["writer_two"] = "saved"
            except Exception as exc:
                errors["writer_two"] = exc

        thread_one = threading.Thread(target=writer_one, name="writer-one")
        thread_two = threading.Thread(target=writer_two, name="writer-two")

        thread_one.start()
        assert first_write_started.wait(timeout=1.0), "writer one did not reach write phase"
        thread_two.start()

        thread_one.join(timeout=2.0)
        thread_two.join(timeout=2.0)

        assert results.get("writer_one") == "saved"
        assert isinstance(errors.get("writer_two"), CASConflictError)

        final_plan, _ = load_plan(path)
        assert final_plan.context == "writer-1"


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
        from datetime import datetime, timezone

        from vectl.claims import (
            ClaimEntry,
            get_current_branch,
            load_claims,
            save_claims,
        )
        from vectl.lifecycle import claim_step, complete_step
        from vectl.plan_path import resolve_claims_path

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
                    claimed_at=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
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
        from vectl.claims import acquire_claim, load_claims
        from vectl.plan_path import resolve_claims_path

        plan_path = tmp_path / "plan.yaml"
        save_plan(sample_plan, plan_path)
        claims_path = resolve_claims_path(plan_path)
        claims_path.parent.mkdir(parents=True, exist_ok=True)

        save_plan(sample_plan, plan_path)

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


class TestExpectedRedSelectedExecutionObserver:
    @staticmethod
    def _write_probe(tmp_path: Path) -> tuple[str, Path]:
        sentinel = tmp_path / "executed.txt"
        probe = tmp_path / "test_observer_probe.py"
        probe.write_text(
            "from pathlib import Path\n\n"
            "def test_observed():\n"
            f"    Path({str(sentinel)!r}).write_text('executed', encoding='utf-8')\n",
            encoding="utf-8",
        )
        return "test_observer_probe.py::test_observed", sentinel

    @staticmethod
    def _invoke_observer(
        tmp_path: Path, mode: str, node_id: str
    ) -> subprocess.CompletedProcess[str]:
        observer = Path(__file__).with_name("expected_red.py")
        return subprocess.run(
            [sys.executable, str(observer), mode, "pytest", node_id],
            cwd=tmp_path,
            capture_output=True,
            text=True,
            timeout=30,
        )

    @staticmethod
    def _selection_envelope(node_id: str) -> dict[str, object]:
        encoded = json.dumps(
            {"identities": [node_id]},
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8", "surrogateescape")
        return {
            "schema_version": "vectl.check.selection.v1",
            "check_id": "admitted_mutation_expected_red",
            "identities": [node_id],
            "digest": "sha256:" + hashlib.sha256(encoded).hexdigest(),
        }

    def test_select_collects_without_executing_test_body(self, tmp_path: Path) -> None:
        node_id, sentinel = self._write_probe(tmp_path)

        result = self._invoke_observer(tmp_path, "select", node_id)

        assert result.returncode == 0, result.stderr
        assert not sentinel.exists()
        assert json.loads(result.stdout) == self._selection_envelope(node_id)

    def test_run_emits_matching_selected_and_executed_ids(self, tmp_path: Path) -> None:
        node_id, sentinel = self._write_probe(tmp_path)

        result = self._invoke_observer(tmp_path, "run", node_id)

        assert result.returncode == 0, result.stderr
        assert sentinel.read_text(encoding="utf-8") == "executed"
        lines = result.stdout.splitlines()
        assert len(lines) == 1
        evidence = json.loads(lines[0])
        assert evidence["schema_version"] == "vectl.check.evidence.v1"
        assert evidence["check_id"] == "admitted_mutation_expected_red"
        assert evidence["selected_ids"] == [node_id]
        assert evidence["executed_ids"] == [node_id]
        assert evidence["exit_code"] == 0
        assert evidence["outcome"] == "green"
        assert node_id in evidence["observations"]
        assert evidence["artifacts"] == []


def test_admitted_plan_mutation_rfc_fixes_narrow_v1_contract() -> None:
    rfc = Path(__file__).parents[1] / "docs" / "RFC-admitted-plan-mutation.md"
    content = rfc.read_text(encoding="utf-8")

    for heading in (
        "## Pre-edit baseline classification",
        "## Startup configuration",
        "## Private transaction",
        "## Admission process contract",
        "## Cooperative same-UID trust boundary",
        "## Mutation receipt",
        "## Covered writers",
        "## Excluded writers",
        "## Expected-red selected-execution observer",
        "## Rollout",
        "## Rollback and recovery",
        "## Rejected full Preview design",
    ):
        assert heading in content

    for contract_term in (
        "VECTL_PLAN_ADMISSION_EXECUTABLE",
        "VECTL_PLAN_ADMISSION_EXECUTABLE_SHA256",
        "VECTL_PLAN_ADMISSION_TIMEOUT_SECONDS",
        '"schema_version": "vectl.plan-mutation-admission.v1"',
        '"schema_version": "vectl.plan-mutation.receipt.v1"',
        '"plan_applied": true',
        '"git_committed": false',
        "shell=False",
        "candidate_sha256",
        "persisted_sha256 == candidate_sha256",
        "vectl.check.selection.v1",
        "vectl.check.evidence.v1",
    ):
        assert contract_term in content


def _receipt_payload(receipt: object) -> dict[str, object]:
    if isinstance(receipt, dict):
        return receipt
    if is_dataclass(receipt) and not isinstance(receipt, type):
        return asdict(receipt)
    model_dump = getattr(receipt, "model_dump", None)
    assert callable(model_dump), "admitted mutation receipt must expose machine fields"
    payload = model_dump(mode="json")
    assert isinstance(payload, dict)
    return payload


def _require_admitted_transaction(message: str):
    transaction = getattr(io_module, "_finalize_admitted_plan_mutation", None)
    assert callable(transaction), message
    return transaction


def _freeze_admission_config(environment: dict[str, str], message: str):
    freeze = getattr(io_module, "_freeze_plan_admission_config", None)
    assert callable(freeze), message
    return freeze(environment)


def _git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        ["git", *args],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
    )
    if check:
        assert result.returncode == 0, result.stderr
    return result


def _seed_admitted_mutation_repo(root: Path, sample_plan: Plan) -> tuple[Path, Path]:
    repo = root / "repo"
    repo.mkdir(parents=True)
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "admission-test@example.invalid")
    _git(repo, "config", "user.name", "Admission Test")
    plan_path = repo / "plan.yaml"
    save_plan(sample_plan, plan_path)
    _git(repo, "add", "plan.yaml")
    _git(repo, "commit", "-q", "-m", "seed")
    return repo, plan_path


def _write_fake_admission_adapter(path: Path, *, invalid_shebang: bool = False) -> Path:
    if invalid_shebang:
        path.write_text("#!/missing/vectl-test-interpreter\n", encoding="utf-8")
    else:
        path.write_text(
            "#!/usr/bin/env python3\n"
            "import argparse\n"
            "import hashlib\n"
            "import json\n"
            "import os\n"
            "import time\n"
            "from pathlib import Path\n\n"
            "parser = argparse.ArgumentParser()\n"
            "parser.add_argument('--base', required=True)\n"
            "parser.add_argument('--candidate', required=True)\n"
            "parser.add_argument('--verdict', required=True)\n"
            "args = parser.parse_args()\n"
            "mode = os.environ.get('VECTL_TEST_ADMISSION_MODE', 'approve')\n"
            "candidate = Path(args.candidate)\n"
            "verdict = Path(args.verdict)\n"
            "candidate_bytes = candidate.read_bytes()\n"
            "candidate_sha256 = 'sha256:' + hashlib.sha256(candidate_bytes).hexdigest()\n"
            "if mode == 'timeout':\n"
            "    time.sleep(2)\n"
            "elif mode == 'crash':\n"
            "    raise SystemExit(23)\n"
            "elif mode == 'malformed':\n"
            "    verdict.write_text('{', encoding='utf-8')\n"
            "elif mode == 'too-large':\n"
            "    verdict.write_bytes(b'x' * 17000)\n"
            "elif mode == 'hash-mismatch':\n"
            "    verdict.write_text(json.dumps({\n"
            "        'schema_version': 'vectl.plan-mutation-admission.v1',\n"
            "        'decision': 'approve',\n"
            "        'candidate_sha256': 'sha256:' + ('0' * 64),\n"
            "    }), encoding='utf-8')\n"
            "elif mode == 'mutate-candidate':\n"
            "    candidate.write_bytes(candidate_bytes + b'\\n# adapter mutation\\n')\n"
            "    verdict.write_text(json.dumps({\n"
            "        'schema_version': 'vectl.plan-mutation-admission.v1',\n"
            "        'decision': 'approve',\n"
            "        'candidate_sha256': candidate_sha256,\n"
            "    }), encoding='utf-8')\n"
            "elif mode == 'reject':\n"
            "    verdict.write_text(json.dumps({\n"
            "        'schema_version': 'vectl.plan-mutation-admission.v1',\n"
            "        'decision': 'reject',\n"
            "        'candidate_sha256': candidate_sha256,\n"
            "        'reason': 'synthetic policy rejection',\n"
            "    }), encoding='utf-8')\n"
            "else:\n"
            "    verdict.write_text(json.dumps({\n"
            "        'schema_version': 'vectl.plan-mutation-admission.v1',\n"
            "        'decision': 'approve',\n"
            "        'candidate_sha256': candidate_sha256,\n"
            "    }), encoding='utf-8')\n",
            encoding="utf-8",
        )
    path.chmod(0o755)
    return path


def _configured_admission_environment(adapter: Path, *, timeout: int = 1) -> dict[str, str]:
    digest = hashlib.sha256(adapter.read_bytes()).hexdigest()
    return {
        "VECTL_PLAN_ADMISSION_EXECUTABLE": str(adapter.resolve()),
        "VECTL_PLAN_ADMISSION_EXECUTABLE_SHA256": f"sha256:{digest}",
        "VECTL_PLAN_ADMISSION_TIMEOUT_SECONDS": str(timeout),
    }


def test_admitted_mutation_exact_byte_cas_and_persisted_hash(
    tmp_path: Path,
    sample_plan: Plan,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan_path = tmp_path / "plan.yaml"
    save_plan(sample_plan, plan_path)
    crlf_bytes = plan_path.read_bytes().replace(b"\n", b"\r\n")
    plan_path.write_bytes(crlf_bytes)

    loaded, loaded_hash = load_plan(plan_path)
    raw_hash = hashlib.sha256(crlf_bytes).hexdigest()
    assert loaded_hash == raw_hash, (
        "REQ-AMV1-EXACT-BYTE-TRANSACTION missing: load CAS identity normalizes CRLF "
        "instead of hashing authoritative raw bytes"
    )

    transaction = _require_admitted_transaction(
        "REQ-AMV1-EXACT-BYTE-TRANSACTION missing: private finalization/admission/"
        "persistence transaction is absent"
    )
    admission = _freeze_admission_config(
        {},
        "REQ-AMV1-EXACT-BYTE-TRANSACTION missing: startup-frozen not-configured state is absent",
    )
    candidate = loaded.model_copy(deep=True)
    candidate.context = "approved exact bytes"

    dump_calls = 0
    original_dump = io_module.yaml.dump

    def counted_dump(*args: object, **kwargs: object) -> str:
        nonlocal dump_calls
        dump_calls += 1
        return original_dump(*args, **kwargs)

    monkeypatch.setattr(io_module.yaml, "dump", counted_dump)
    receipt = transaction(
        candidate,
        plan_path,
        expected_hash=loaded_hash,
        commit_message=None,
        admission_config=admission,
    )
    payload = _receipt_payload(receipt)
    persisted_bytes = plan_path.read_bytes()
    persisted_sha256 = "sha256:" + hashlib.sha256(persisted_bytes).hexdigest()

    assert dump_calls == 1, "candidate Plan must be serialized exactly once"
    assert payload["schema_version"] == "vectl.plan-mutation.receipt.v1"
    assert payload["base_sha256"] == f"sha256:{raw_hash}"
    assert payload["candidate_sha256"] == persisted_sha256
    assert payload["persisted_sha256"] == persisted_sha256
    assert payload["admission_status"] == "not_configured"
    assert payload["plan_applied"] is True
    assert payload["git_attempted"] is False
    assert payload["git_committed"] is False

    stale_hash = loaded_hash
    plan_path.write_bytes(persisted_bytes + b"# competing raw-byte writer\n")
    competing_bytes = plan_path.read_bytes()
    with pytest.raises(CASConflictError):
        transaction(
            candidate,
            plan_path,
            expected_hash=stale_hash,
            commit_message=None,
            admission_config=admission,
        )
    assert plan_path.read_bytes() == competing_bytes


def test_admitted_mutation_configured_adapter_failure_is_zero_write(
    tmp_path: Path,
    sample_plan: Plan,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    transaction = _require_admitted_transaction(
        "REQ-AMV1-ADMISSION-FAIL-CLOSED missing: configured adapter transaction is absent"
    )
    freeze = getattr(io_module, "_freeze_plan_admission_config", None)
    assert callable(freeze), (
        "REQ-AMV1-ADMISSION-FAIL-CLOSED missing: startup configuration is not frozen "
        "into not_configured/configured/configuration_invalid state"
    )

    cases = (
        ("partial", "configuration_invalid", False),
        ("reject", "rejected", False),
        ("timeout", "adapter_timeout", False),
        ("crash", "adapter_crash", False),
        ("malformed", "malformed_verdict", False),
        ("too-large", "adapter_output_too_large", False),
        ("hash-mismatch", "candidate_identity_mismatch", False),
        ("mutate-candidate", "candidate_identity_mismatch", False),
        ("identity-drift", "executable_identity_mismatch", False),
        ("launch-failure", "adapter_crash", True),
    )
    for mode, expected_code, invalid_shebang in cases:
        case_root = tmp_path / mode
        repo, plan_path = _seed_admitted_mutation_repo(case_root, sample_plan)
        adapter = _write_fake_admission_adapter(
            case_root / "adapter.py",
            invalid_shebang=invalid_shebang,
        )
        if mode == "partial":
            admission = freeze({"VECTL_PLAN_ADMISSION_EXECUTABLE": str(adapter.resolve())})
        else:
            admission = freeze(_configured_admission_environment(adapter))
        if mode == "identity-drift":
            adapter.write_bytes(adapter.read_bytes() + b"# identity drift\n")
        monkeypatch.setenv("VECTL_TEST_ADMISSION_MODE", mode)

        loaded, loaded_hash = load_plan(plan_path)
        candidate = loaded.model_copy(deep=True)
        candidate.context = f"candidate for {mode}"
        plan_before = plan_path.read_bytes()
        head_before = _git(repo, "rev-parse", "HEAD").stdout.strip()
        status_before = _git(repo, "status", "--porcelain=v1").stdout
        backup = repo / ".git" / "vectl" / "plan.yaml.bak"
        backup_before = backup.read_bytes() if backup.exists() else None

        with pytest.raises(Exception) as raised:
            transaction(
                candidate,
                plan_path,
                expected_hash=loaded_hash,
                commit_message=f"admitted test: {mode}",
                admission_config=admission,
            )

        assert getattr(raised.value, "code", None) == expected_code, mode
        assert plan_path.read_bytes() == plan_before, mode
        assert _git(repo, "rev-parse", "HEAD").stdout.strip() == head_before, mode
        assert _git(repo, "status", "--porcelain=v1").stdout == status_before, mode
        assert (backup.read_bytes() if backup.exists() else None) == backup_before, mode

    approved_root = tmp_path / "approved"
    approved_repo, approved_path = _seed_admitted_mutation_repo(approved_root, sample_plan)
    approved_adapter = _write_fake_admission_adapter(approved_root / "adapter.py")
    approved_config = freeze(_configured_admission_environment(approved_adapter))
    monkeypatch.setenv("VECTL_TEST_ADMISSION_MODE", "approve")
    approved_plan, approved_hash = load_plan(approved_path)
    approved_plan.context = "configured approval"
    approved_receipt = _receipt_payload(
        transaction(
            approved_plan,
            approved_path,
            expected_hash=approved_hash,
            commit_message="admitted test: approved",
            admission_config=approved_config,
        )
    )
    assert approved_receipt["admission_status"] == "approved"
    assert approved_receipt["plan_applied"] is True
    assert approved_receipt["git_committed"] is True
    assert _git(approved_repo, "rev-list", "--count", "HEAD").stdout.strip() == "2"


def test_admitted_mutation_applied_and_git_receipt_are_separate(
    tmp_path: Path,
    sample_plan: Plan,
) -> None:
    transaction = _require_admitted_transaction(
        "REQ-AMV1-APPLIED-GIT-RECEIPT missing: no private transaction returns separate "
        "Plan-application and Git status"
    )
    admission = _freeze_admission_config(
        {},
        "REQ-AMV1-APPLIED-GIT-RECEIPT missing: not-configured compatibility state is absent",
    )
    repo, plan_path = _seed_admitted_mutation_repo(tmp_path, sample_plan)
    unrelated = repo / "unrelated.txt"
    unrelated.write_text("preserve staged content\n", encoding="utf-8")
    _git(repo, "add", "unrelated.txt")

    loaded, loaded_hash = load_plan(plan_path)
    candidate = loaded.model_copy(deep=True)
    candidate.context = "Plan applies even when Git fails"
    head_before = _git(repo, "rev-parse", "HEAD").stdout.strip()
    index_lock = repo / ".git" / "index.lock"
    index_lock.write_text("force bounded Git failure", encoding="utf-8")
    try:
        receipt = transaction(
            candidate,
            plan_path,
            expected_hash=loaded_hash,
            commit_message="admitted test: Git failure receipt",
            admission_config=admission,
        )
    finally:
        index_lock.unlink(missing_ok=True)

    payload = _receipt_payload(receipt)
    assert payload["schema_version"] == "vectl.plan-mutation.receipt.v1"
    assert payload["plan_applied"] is True
    assert payload["git_attempted"] is True
    assert payload["git_committed"] is False
    assert payload["candidate_sha256"] == payload["persisted_sha256"]
    assert _git(repo, "rev-parse", "HEAD").stdout.strip() == head_before
    assert _git(repo, "diff", "--cached", "--name-only").stdout.splitlines() == [
        "unrelated.txt"
    ]
    persisted, _ = load_plan(plan_path)
    assert persisted.context == "Plan applies even when Git fails"


def test_admitted_mutation_exact_byte_docs_match_contract() -> None:
    transaction = getattr(io_module, "_finalize_admitted_plan_mutation", None)
    assert callable(transaction), (
        "REQ-AMV1-EXACT-DOC-SYNC missing: RFC private transaction has no documented "
        "implementation surface"
    )
    implementation_contract = inspect.getdoc(transaction) or ""
    rfc_path = Path(__file__).parents[1] / "docs" / "RFC-admitted-plan-mutation.md"
    rfc_contract = rfc_path.read_text(encoding="utf-8")

    shared_terms = (
        "raw bytes",
        "serialize",
        "candidate_sha256",
        "admission",
        "CAS",
        "persisted_sha256",
        "git_committed",
    )
    for term in shared_terms:
        assert term.lower() in rfc_contract.lower(), f"RFC omits {term}"
        assert term.lower() in implementation_contract.lower(), (
            f"private transaction docstring omits RFC invariant {term}"
        )
    assert "serialize the finalized managed Plan exactly once" in rfc_contract
    assert "git commit --only --no-verify plan.yaml" in rfc_contract
    assert "configuration_invalid" in rfc_contract
    assert "candidate_identity_mismatch" in rfc_contract
