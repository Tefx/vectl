"""Tests for core.2: YAML IO with CAS."""

import pytest
from pathlib import Path

from vectl.io import load_plan, save_plan
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
