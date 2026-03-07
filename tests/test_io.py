"""Tests for core.2: YAML IO with CAS."""

import json
from pathlib import Path

import pytest

from vectl.io import (
    detect_orphan_state,
    extract_state,
    load_plan,
    load_plan_definition,
    load_state,
    merge_plan,
    save_plan,
    save_state,
    strip_state,
)
from vectl.models import (
    CASConflictError,
    Clipboard,
    Phase,
    PhaseState,
    PhaseStatus,
    Plan,
    PlanIOError,
    PlanState,
    RejectionEntry,
    Step,
    StepState,
    StepStatus,
)
from vectl.plan_path import resolve_state_path


def make_plan_for_state_tests() -> Plan:
    return Plan(
        project="state-test",
        plan_id="plan-123",
        clipboard=Clipboard(
            author="agent-ops",
            summary="handoff",
            content="state extraction test",
            written_at="2026-01-01T00:00:00Z",
            expires_at="2026-01-02T00:00:00Z",
        ),
        phases=[
            Phase(
                id="core",
                name="Core phase",
                status=PhaseStatus.IN_PROGRESS,
                evidence="phase evidence",
                steps=[
                    Step(id="s_pending", name="Pending", status=StepStatus.PENDING),
                    Step(
                        id="s_claimed",
                        name="Claimed",
                        status=StepStatus.CLAIMED,
                        claimed_by="agent-a",
                    ),
                    Step(
                        id="s_done",
                        name="Done",
                        status=StepStatus.DONE,
                        description="done step description",
                        verification="run done checks",
                        refs=["docs/done.md"],
                        depends_on=["s_claimed"],
                        evidence="done evidence",
                    ),
                    Step(
                        id="s_skipped",
                        name="Skipped",
                        status=StepStatus.SKIPPED,
                        skipped_reason="not needed",
                    ),
                    Step(
                        id="s_rejected",
                        name="Rejected",
                        status=StepStatus.REJECTED,
                        rejection_reason="bad approach",
                    ),
                ],
            ),
            Phase(
                id="qa",
                name="QA",
                status=PhaseStatus.LOCKED,
                evidence=None,
                steps=[
                    Step(
                        id="s2_pending",
                        name="Another",
                        status=StepStatus.PENDING,
                    ),
                ],
            ),
        ],
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

    def test_load_plan_state_json_precedence_over_stale_yaml_runtime_state(
        self, tmp_path: Path
    ) -> None:
        plan_path = tmp_path / "plan.yaml"
        state_path = resolve_state_path(plan_path)
        plan_path.write_text(
            """
project: precedence-test
phases:
  - id: core
    name: Core
    steps:
      - id: core.step
        name: Core Step
        status: done
        claimed_by: stale-agent
        evidence: stale-state
""".strip()
            + "\n",
            encoding="utf-8",
        )
        save_state(
            PlanState(
                plan_id="",
                steps={
                    "core.step": StepState(
                        status=StepStatus.CLAIMED,
                        claimed_by="fresh-agent",
                        evidence="fresh-state",
                    )
                },
                phases={},
            ),
            state_path,
        )

        merged, _ = load_plan(plan_path)
        _, step = merged.find_step("core.step") or (None, None)
        assert step is not None
        assert step.status == StepStatus.CLAIMED
        assert step.claimed_by == "fresh-agent"
        assert step.evidence == "fresh-state"

    def test_load_plan_missing_state_json_falls_back_to_plan_yaml(self, tmp_path: Path) -> None:
        plan_path = tmp_path / "plan.yaml"
        plan_path.write_text(
            """
project: fallback-test
phases:
  - id: core
    name: Core
    steps:
      - id: core.step
        name: Core Step
        status: done
        claimed_by: legacy-agent
        evidence: legacy-state
""".strip()
            + "\n",
            encoding="utf-8",
        )

        loaded, _ = load_plan(plan_path)
        _, step = loaded.find_step("core.step") or (None, None)
        assert step is not None
        assert step.status == StepStatus.DONE
        assert step.claimed_by == "legacy-agent"
        assert step.evidence == "legacy-state"

    def test_load_plan_definition_does_not_merge_companion_state(self, tmp_path: Path) -> None:
        plan_path = tmp_path / "plan.yaml"
        state_path = resolve_state_path(plan_path)
        plan_path.write_text(
            """
project: definition-only-test
phases:
  - id: core
    name: Core
    steps:
      - id: core.step
        name: Core Step
        status: done
        claimed_by: yaml-agent
""".strip()
            + "\n",
            encoding="utf-8",
        )
        save_state(
            PlanState(
                plan_id="",
                steps={"core.step": StepState(status=StepStatus.CLAIMED, claimed_by="state-agent")},
                phases={},
            ),
            state_path,
        )

        plan_def, _ = load_plan_definition(plan_path)
        _, step = plan_def.find_step("core.step") or (None, None)
        assert step is not None
        assert step.status == StepStatus.DONE
        assert step.claimed_by == "yaml-agent"


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

    def test_load_state_roundtrip(self, tmp_path: Path, sample_state: PlanState):
        path = tmp_path / "state.json"
        saved_hash = save_state(sample_state, path)

        loaded_state, loaded_hash = load_state(path)

        assert loaded_state == sample_state
        assert loaded_hash == saved_hash


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

    def test_save_state_auto_retry_with_clipboard_clear(
        self, tmp_path: Path, sample_state: PlanState
    ):
        path = tmp_path / "state.json"
        base_state = sample_state.model_copy(deep=True)
        base_state.clipboard = Clipboard(
            author="agent-base",
            summary="base summary",
            content="before clear",
            written_at="2026-03-01T00:00:00Z",
            expires_at="2026-03-02T00:00:00Z",
        )
        base_hash = save_state(base_state, path)

        external_state = PlanState(
            plan_id="plan-state-1",
            steps={"core.1": StepState(status=StepStatus.CLAIMED, claimed_by="agent-external")},
            phases={"core": PhaseState(status=PhaseStatus.PENDING, evidence="concurrent")},
            clipboard=Clipboard(
                author="agent-external",
                summary="external overwrite",
                content="still present",
                written_at="2026-03-01T01:00:00Z",
                expires_at="2026-03-02T01:00:00Z",
            ),
        )
        save_state(external_state, path, expected_hash=base_hash)

        local_update = base_state.model_copy(deep=True)
        local_update.clipboard = None
        local_update.steps["core.3"] = StepState(
            status=StepStatus.CLAIMED, claimed_by="agent-local"
        )

        final_hash = save_state(local_update, path, expected_hash=base_hash, max_retries=3)

        saved_state, _ = load_state(path)
        assert final_hash
        assert saved_state.clipboard is None
        assert saved_state.steps["core.3"].status == StepStatus.CLAIMED
        assert saved_state.steps["core.3"].claimed_by == "agent-local"

    def test_save_state_creates_parent_dirs(self, tmp_path: Path, sample_state: PlanState):
        path = tmp_path / "a" / "deep" / "path" / "state.json"

        save_state(sample_state, path)

        assert path.exists()

    def test_save_state_atomic_cleanup_on_replace_error(
        self, tmp_path: Path, sample_state: PlanState, monkeypatch: pytest.MonkeyPatch
    ):
        path = tmp_path / "state.json"

        def _boom_replace(src: str, dst: str) -> None:
            raise OSError("replace failed")

        monkeypatch.setattr("vectl.io.os.replace", _boom_replace)

        with pytest.raises(OSError, match="replace failed"):
            save_state(sample_state, path)

        assert not path.exists()
        assert list(tmp_path.glob("*.tmp")) == []


class TestStateExtractionAndMerge:
    def test_extract_state_empty_plan(self) -> None:
        plan = Plan(project="empty-project")
        state = extract_state(plan)
        assert state == PlanState(plan_id="", steps={}, phases={}, clipboard=None)

    def test_extract_state_collects_mutable_fields(self) -> None:
        plan = make_plan_for_state_tests()
        state = extract_state(plan)

        assert state.plan_id == "plan-123"
        assert state.clipboard == plan.clipboard
        assert set(state.steps) == {
            "s_pending",
            "s_claimed",
            "s_done",
            "s_skipped",
            "s_rejected",
            "s2_pending",
        }
        assert state.steps["s_claimed"].status == StepStatus.CLAIMED
        assert state.steps["s_claimed"].claimed_by == "agent-a"
        assert state.steps["s_skipped"].skipped_reason == "not needed"
        assert state.steps["s_rejected"].rejection_reason == "bad approach"
        assert state.phases["core"].status == PhaseStatus.IN_PROGRESS
        assert state.phases["core"].evidence == "phase evidence"

    def test_extract_state_copies_rejection_history(self) -> None:
        plan = Plan(
            project="history-test",
            phases=[
                Phase(
                    id="core",
                    name="Core",
                    steps=[
                        Step(
                            id="s_rejected",
                            name="Rejected",
                            status=StepStatus.REJECTED,
                            rejection_reason="bad approach",
                            rejection_history=[
                                RejectionEntry(
                                    reason="old rejection",
                                    timestamp="2026-01-01T00:00:00Z",
                                    reviewer="reviewer-1",
                                )
                            ],
                        )
                    ],
                )
            ],
        )
        state = extract_state(plan)

        plan_rejections = plan.phases[0].steps[0].rejection_history
        state_rejections = state.steps["s_rejected"].rejection_history

        assert state_rejections == plan_rejections
        assert state_rejections is not plan_rejections

        plan_rejections.append(
            RejectionEntry(
                reason="later rejection",
                timestamp="2026-01-05T00:00:00Z",
                reviewer="reviewer-2",
            )
        )
        assert len(state_rejections) == 1

    def test_strip_state_resets_mutable_fields(self) -> None:
        original = make_plan_for_state_tests()
        stripped = strip_state(original)

        assert original is not stripped
        assert stripped.clipboard is None
        assert all(phase.status == PhaseStatus.PENDING for phase in stripped.phases)
        assert all(
            step.status == StepStatus.PENDING for phase in stripped.phases for step in phase.steps
        )
        assert all(step.claimed_by is None for phase in stripped.phases for step in phase.steps)
        assert all(step.claimed_at is None for phase in stripped.phases for step in phase.steps)
        assert all(step.evidence is None for phase in stripped.phases for step in phase.steps)
        assert all(step.skipped_reason is None for phase in stripped.phases for step in phase.steps)
        assert all(
            step.rejection_reason is None for phase in stripped.phases for step in phase.steps
        )
        assert all(
            step.rejection_history == [] for phase in stripped.phases for step in phase.steps
        )
        assert all(
            step.affinity_override is False for phase in stripped.phases for step in phase.steps
        )
        assert all(
            step.affinity_override_by is None for phase in stripped.phases for step in phase.steps
        )
        assert all(
            step.affinity_override_at is None for phase in stripped.phases for step in phase.steps
        )
        assert all(phase.evidence is None for phase in stripped.phases)

        # Plan definition fields are preserved
        assert stripped.project == original.project
        assert [phase.id for phase in stripped.phases] == [phase.id for phase in original.phases]
        assert [phase.name for phase in stripped.phases] == [
            phase.name for phase in original.phases
        ]
        assert stripped.phases[0].steps[2].description == original.phases[0].steps[2].description
        assert stripped.phases[0].steps[2].verification == original.phases[0].steps[2].verification
        assert stripped.phases[0].steps[2].refs == original.phases[0].steps[2].refs
        assert stripped.phases[0].steps[2].depends_on == original.phases[0].steps[2].depends_on

        # Original plan remains unchanged
        assert original.clipboard is not None
        assert original.phases[0].status == PhaseStatus.IN_PROGRESS
        assert original.phases[0].steps[1].status == StepStatus.CLAIMED

    def test_merge_plan(self) -> None:
        plan_def = make_plan_for_state_tests()
        plan_def.phases[0].steps[0].claimed_by = "before"
        plan_def.phases[0].steps[0].status = StepStatus.CLAIMED

        state = PlanState(
            plan_id="plan-123",
            clipboard=Clipboard(
                author="agent-b",
                summary="merged",
                content="from state",
                written_at="2026-01-03T00:00:00Z",
                expires_at="2026-01-04T00:00:00Z",
            ),
            steps={
                "s_claimed": StepState(
                    status=StepStatus.REJECTED,
                    rejection_reason="now rejected",
                    rejection_history=[
                        RejectionEntry(
                            reason="rejected in merge",
                            timestamp="2026-03-01T00:00:00Z",
                            reviewer="reviewer-a",
                        )
                    ],
                    claimed_by=None,
                ),
                "s_done": StepState(
                    status=StepStatus.SKIPPED,
                    skipped_reason="obsolete",
                ),
            },
            phases={
                "core": PhaseState(status=PhaseStatus.DONE, evidence="merged phase"),
            },
        )

        merged = merge_plan(plan_def, state)

        assert merged.plan_id == "plan-123"
        assert merged.clipboard == state.clipboard
        assert merged.phases[0].status == PhaseStatus.DONE
        assert merged.phases[0].evidence == "merged phase"
        assert merged.phases[1].status == PhaseStatus.LOCKED
        assert merged.phases[1].evidence is None
        assert merged.phases[0].steps[1].status == StepStatus.REJECTED
        assert merged.phases[0].steps[1].rejection_reason == "now rejected"
        assert merged.phases[0].steps[1].rejection_history
        assert isinstance(merged.phases[0].steps[1].rejection_history[0], RejectionEntry)
        assert merged.phases[0].steps[2].status == StepStatus.SKIPPED
        assert merged.phases[0].steps[2].skipped_reason == "obsolete"

    def test_merge_plan_partial_state(self) -> None:
        plan_def = make_plan_for_state_tests()
        state = PlanState(
            plan_id="plan-123",
            steps={"s_claimed": StepState(status=StepStatus.DONE, claimed_by="agent-merge")},
            phases={},
        )

        merged = merge_plan(plan_def, state)

        assert merged.phases[0].steps[1].status == StepStatus.DONE
        assert merged.phases[0].steps[0].status == StepStatus.PENDING
        assert merged.clipboard is None

    def test_merge_plan_can_clear_clipboard(self) -> None:
        plan_def = make_plan_for_state_tests()
        state = PlanState(
            plan_id="plan-123",
            steps={},
            phases={},
            clipboard=None,
        )

        merged = merge_plan(plan_def, state)

        assert merged.clipboard is None

    def test_merge_plan_roundtrip_invariant(self) -> None:
        original = make_plan_for_state_tests()
        normalized = merge_plan(strip_state(original), extract_state(original))

        assert normalized == original


class TestOrphanDetection:
    """Tests for orphan state detection in detect_orphan_state()."""

    def test_orphan_detect_extra_phase(self) -> None:
        """State with extra phase key returns orphan entry."""
        plan_def = Plan(
            project="orphan-test",
            phases=[
                Phase(id="core", name="Core", steps=[Step(id="s1", name="Step 1")]),
            ],
        )
        state = PlanState(
            plan_id="orphan-test",
            phases={
                "core": PhaseState(status=PhaseStatus.PENDING),
                # This phase doesn't exist in plan definition
                "orphan_phase": PhaseState(status=PhaseStatus.DONE),
            },
            steps={
                "s1": StepState(status=StepStatus.PENDING),
            },
        )

        orphans = detect_orphan_state(plan_def, state)

        assert len(orphans) == 1
        assert orphans[0].kind == "phase"
        assert orphans[0].id == "orphan_phase"
        assert orphans[0].phase_id is None

    def test_orphan_detect_extra_step(self) -> None:
        """State with extra step key returns orphan entry."""
        plan_def = Plan(
            project="orphan-test",
            phases=[
                Phase(id="core", name="Core", steps=[Step(id="s1", name="Step 1")]),
            ],
        )
        state = PlanState(
            plan_id="orphan-test",
            phases={
                "core": PhaseState(status=PhaseStatus.PENDING),
            },
            steps={
                "s1": StepState(status=StepStatus.PENDING),
                # This step doesn't exist in plan definition
                "orphan_step": StepState(status=StepStatus.CLAIMED, claimed_by="agent-x"),
            },
        )

        orphans = detect_orphan_state(plan_def, state)

        assert len(orphans) == 1
        assert orphans[0].kind == "step"
        assert orphans[0].id == "orphan_step"
        assert orphans[0].phase_id is None

    def test_orphan_detect_clean_state_no_warning(self) -> None:
        """Clean state with no orphan entries returns empty list."""
        plan_def = Plan(
            project="orphan-test",
            phases=[
                Phase(
                    id="core",
                    name="Core",
                    steps=[
                        Step(id="s1", name="Step 1"),
                        Step(id="s2", name="Step 2"),
                    ],
                ),
                Phase(id="qa", name="QA", steps=[Step(id="s3", name="Step 3")]),
            ],
        )
        state = PlanState(
            plan_id="orphan-test",
            phases={
                "core": PhaseState(status=PhaseStatus.PENDING),
                "qa": PhaseState(status=PhaseStatus.LOCKED),
            },
            steps={
                "s1": StepState(status=StepStatus.PENDING),
                "s2": StepState(status=StepStatus.CLAIMED, claimed_by="agent-a"),
                "s3": StepState(status=StepStatus.PENDING),
            },
        )

        orphans = detect_orphan_state(plan_def, state)

        assert orphans == []

    def test_orphan_detect_multiple_orphans(self) -> None:
        """State with multiple orphan entries returns all of them."""
        plan_def = Plan(
            project="orphan-test",
            phases=[
                Phase(id="core", name="Core", steps=[Step(id="s1", name="Step 1")]),
            ],
        )
        state = PlanState(
            plan_id="orphan-test",
            phases={
                "core": PhaseState(status=PhaseStatus.PENDING),
                "orphan_phase_1": PhaseState(status=PhaseStatus.DONE),
                "orphan_phase_2": PhaseState(status=PhaseStatus.DONE),
            },
            steps={
                "s1": StepState(status=StepStatus.PENDING),
                "orphan_step_1": StepState(status=StepStatus.CLAIMED),
                "orphan_step_2": StepState(status=StepStatus.DONE),
            },
        )

        orphans = detect_orphan_state(plan_def, state)

        assert len(orphans) == 4
        orphan_ids = [(o.kind, o.id) for o in orphans]
        assert ("phase", "orphan_phase_1") in orphan_ids
        assert ("phase", "orphan_phase_2") in orphan_ids
        assert ("step", "orphan_step_1") in orphan_ids
        assert ("step", "orphan_step_2") in orphan_ids
