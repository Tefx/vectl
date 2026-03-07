"""Tests for core.2: YAML IO with CAS."""

import json
from pathlib import Path

import pytest

from vectl.io import load_plan, load_state, save_plan, save_state
from vectl.models import (
    CASConflictError,
    Clipboard,
    Phase,
    PhaseState,
    PhaseStatus,
    Plan,
    PlanIOError,
    PlanState,
    Step,
    StepState,
    StepStatus,
)


@pytest.fixture
def sample_plan():
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


@pytest.fixture
def sample_state() -> PlanState:
    return PlanState(
        plan_id="plan-state-1",
        steps={
            "core.1": StepState(status=StepStatus.CLAIMED, claimed_by="agent-a"),
            "core.2": StepState(status=StepStatus.DONE, evidence="ok"),
        },
        phases={
            "core": PhaseState(status=PhaseStatus.IN_PROGRESS, evidence="phase"),
        },
    )


class TestLoadPlan:
    def test_load_valid(self, tmp_path: Path, sample_plan: Plan):
        path = tmp_path / "plan.yaml"
        save_plan(sample_plan, path)
        plan, hash_ = load_plan(path)
        assert plan.project == "test-project"
        assert len(plan.phases) == 1
        assert len(plan.phases[0].steps) == 2
        assert isinstance(hash_, str)
        assert len(hash_) == 64  # SHA-256 hex

    def test_load_nonexistent(self, tmp_path: Path):
        with pytest.raises(PlanIOError, match="not found"):
            load_plan(tmp_path / "nope.yaml")

    def test_load_invalid_yaml(self, tmp_path: Path):
        path = tmp_path / "bad.yaml"
        path.write_text("{{{{invalid yaml")
        with pytest.raises(PlanIOError, match="Invalid YAML"):
            load_plan(path)

    def test_load_non_mapping(self, tmp_path: Path):
        path = tmp_path / "list.yaml"
        path.write_text("- item1\n- item2\n")
        with pytest.raises(PlanIOError, match="must be a YAML mapping"):
            load_plan(path)


class TestSavePlan:
    def test_save_creates_file(self, tmp_path: Path, sample_plan: Plan):
        path = tmp_path / "plan.yaml"
        hash_ = save_plan(sample_plan, path)
        assert path.exists()
        assert isinstance(hash_, str)

    def test_save_creates_parent_dirs(self, tmp_path: Path, sample_plan: Plan):
        path = tmp_path / "sub" / "dir" / "plan.yaml"
        save_plan(sample_plan, path)
        assert path.exists()

    def test_round_trip(self, tmp_path: Path, sample_plan: Plan):
        path = tmp_path / "plan.yaml"
        save_plan(sample_plan, path)
        loaded, _ = load_plan(path)
        assert loaded.project == sample_plan.project
        assert len(loaded.phases) == len(sample_plan.phases)
        assert loaded.phases[0].steps[0].id == sample_plan.phases[0].steps[0].id
        assert loaded.phases[0].steps[1].depends_on == ["s1"]


class TestCAS:
    def test_cas_success(self, tmp_path: Path, sample_plan: Plan):
        path = tmp_path / "plan.yaml"
        hash1 = save_plan(sample_plan, path)
        # Save again with correct expected hash
        hash2 = save_plan(sample_plan, path, expected_hash=hash1)
        assert isinstance(hash2, str)

    def test_cas_conflict(self, tmp_path: Path, sample_plan: Plan):
        path = tmp_path / "plan.yaml"
        save_plan(sample_plan, path)
        # Tamper with file
        path.write_text("version: 99\nproject: tampered\n")
        with pytest.raises(CASConflictError):
            save_plan(sample_plan, path, expected_hash="wrong-hash")

    def test_cas_no_hash_skips_check(self, tmp_path: Path, sample_plan: Plan):
        path = tmp_path / "plan.yaml"
        save_plan(sample_plan, path)
        # No expected_hash → no CAS check
        hash_ = save_plan(sample_plan, path)
        assert isinstance(hash_, str)

    def test_cas_new_file_no_conflict(self, tmp_path: Path, sample_plan: Plan):
        path = tmp_path / "new.yaml"
        # expected_hash on non-existent file → no conflict (file doesn't exist yet)
        hash_ = save_plan(sample_plan, path, expected_hash="anything")
        assert path.exists()
        assert isinstance(hash_, str)


class TestClipboardRoundTrip:
    """Tests for clipboard YAML round-trip per RFC-clipboard.md."""

    def test_clipboard_round_trip(self, tmp_path: Path) -> None:
        """Write -> save -> load -> read preserves all fields including expires_at."""
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
        """clipboard: null should be omitted from YAML (exclude_none=True)."""
        plan = Plan(
            project="test",
            clipboard=None,
            phases=[Phase(id="p1", name="P1", steps=[Step(id="s1", name="S1")])],
        )
        path = tmp_path / "plan.yaml"
        save_plan(plan, path)

        yaml_content = path.read_text()
        assert "clipboard" not in yaml_content

    def test_backward_compat_no_clipboard_field(self, tmp_path: Path) -> None:
        """Plan without clipboard field loads correctly (backward compatibility)."""
        yaml_content = """
project: test
phases:
  - id: p1
    name: Phase 1
    steps:
      - id: s1
        name: Step 1
"""
        path = tmp_path / "plan.yaml"
        path.write_text(yaml_content)

        plan, _ = load_plan(path)
        assert plan.project == "test"
        assert plan.clipboard is None
        assert len(plan.phases) == 1


class TestLockStatusRoundtrip:
    """Integration tests: save→load roundtrip preserves corrected lock status.

    The CLI _save() calls recalc_lock_status() before writing to disk.
    These tests exercise the full save→load path using real files (tmp_path)
    and no mocks at the I/O boundary.
    """

    def test_pending_phase_with_unmet_dep_is_locked_after_roundtrip(self, tmp_path: Path) -> None:
        """A PENDING phase whose dependency is not DONE is corrected to LOCKED on save
        and the corrected status survives the load, confirming the roundtrip preserves
        the recalculated lock status.

        Scenario
        --------
        p1: PENDING (no deps)
        p2: PENDING, depends_on=["p1"]   ← inconsistent: should be LOCKED

        After _save() equivalent (recalc + save_plan) the plan on disk must have
        p2 as LOCKED.  load_plan must then return LOCKED for p2.
        """
        from vectl.core import recalc_lock_status
        from vectl.io import load_plan, save_plan

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
                    # Deliberately wrong: should be LOCKED because p1 is not DONE
                    status=PhaseStatus.PENDING,
                    depends_on=["p1"],
                    steps=[Step(id="s2", name="Step 2")],
                ),
            ],
        )

        # Precondition: the plan is inconsistent before save
        assert plan.phases[1].status == PhaseStatus.PENDING, (
            "precondition: p2 starts as PENDING (inconsistent)"
        )

        # Replicate what CLI _save() does: recalc then write
        recalc_lock_status(plan)
        path = tmp_path / "plan.yaml"
        save_plan(plan, path)

        # Load from disk — status must have survived the roundtrip
        loaded, _ = load_plan(path)

        assert loaded.phases[1].status == PhaseStatus.LOCKED, (
            f"expected p2 to be LOCKED after roundtrip, got {loaded.phases[1].status}"
        )

    def test_locked_phase_with_all_deps_done_is_unlocked_after_roundtrip(
        self, tmp_path: Path
    ) -> None:
        """A LOCKED phase whose dependency is DONE is corrected to PENDING on save
        and survives the roundtrip.

        Scenario
        --------
        p1: DONE
        p2: LOCKED, depends_on=["p1"]   ← inconsistent: should be PENDING

        After recalc + save_plan the plan on disk must have p2 as PENDING.
        load_plan must return PENDING for p2.
        """
        from vectl.core import recalc_lock_status
        from vectl.io import load_plan, save_plan

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
                    # Deliberately wrong: dep is DONE so this should be PENDING
                    status=PhaseStatus.LOCKED,
                    depends_on=["p1"],
                    steps=[Step(id="s2", name="Step 2")],
                ),
            ],
        )

        # Precondition
        assert plan.phases[1].status == PhaseStatus.LOCKED, (
            "precondition: p2 starts as LOCKED (inconsistent)"
        )

        recalc_lock_status(plan)
        path = tmp_path / "plan.yaml"
        save_plan(plan, path)

        loaded, _ = load_plan(path)

        assert loaded.phases[1].status == PhaseStatus.PENDING, (
            f"expected p2 to be PENDING after roundtrip, got {loaded.phases[1].status}"
        )


class TestLoadState:
    def test_load_state_file_exists(self, tmp_path: Path, sample_state: PlanState):
        path = tmp_path / "state.json"
        path.write_text(
            json.dumps(sample_state.model_dump(mode="json", exclude_none=True), indent=2),
            encoding="utf-8",
        )

        state, file_hash = load_state(path)
        assert state == sample_state
        assert isinstance(file_hash, str)
        assert len(file_hash) == 64

    def test_load_state_file_missing(self, tmp_path: Path):
        state, file_hash = load_state(tmp_path / "missing.json")
        assert state == PlanState(plan_id="")
        assert file_hash == ""


class TestSaveState:
    def test_save_state_basic_write(self, tmp_path: Path, sample_state: PlanState):
        path = tmp_path / "nested" / "dir" / "state.json"
        file_hash = save_state(sample_state, path)

        assert path.exists()
        assert isinstance(file_hash, str)
        assert len(file_hash) == 64
        assert save_state(sample_state, path, expected_hash=file_hash)

        reloaded = PlanState(**json.loads(path.read_text(encoding="utf-8")))
        assert reloaded == sample_state

    def test_save_state_cas_conflict(self, tmp_path: Path, sample_state: PlanState):
        path = tmp_path / "state.json"
        save_state(sample_state, path)

        with pytest.raises(CASConflictError):
            save_state(sample_state, path, expected_hash="wrong", max_retries=0)

    def test_save_state_auto_retry(self, tmp_path: Path, sample_state: PlanState):
        path = tmp_path / "state.json"
        base_hash = save_state(sample_state, path)

        external_state = PlanState(
            plan_id="plan-state-1",
            steps={
                "core.1": StepState(status=StepStatus.CLAIMED, claimed_by="agent-external"),
                "core.2": StepState(status=StepStatus.DONE),
                "core.3": StepState(status=StepStatus.PENDING),
            },
            phases={
                "core": PhaseState(status=PhaseStatus.PENDING, evidence="concurrent"),
            },
        )
        save_state(external_state, path, expected_hash=base_hash)

        local_update = sample_state.model_copy(deep=True)
        local_update.steps["core.1"] = StepState(
            status=StepStatus.DONE,
            claimed_by="agent-local",
            claimed_at="2026-03-07T00:00:00Z",
            evidence="local update",
        )
        local_update.steps["core.3"] = StepState(
            status=StepStatus.CLAIMED, claimed_by="agent-local"
        )
        local_update.phases["core"] = PhaseState(
            status=PhaseStatus.IN_PROGRESS,
            evidence="local phase",
        )

        final_hash = save_state(local_update, path, expected_hash=base_hash, max_retries=3)

        saved_state, _ = load_state(path)
        assert final_hash
        assert saved_state.steps["core.1"].status == StepStatus.DONE
        assert saved_state.steps["core.2"].status == StepStatus.DONE
        assert saved_state.steps["core.3"].status == StepStatus.CLAIMED
        assert saved_state.phases["core"].status == PhaseStatus.IN_PROGRESS
        assert saved_state.phases["core"].evidence == "local phase"
