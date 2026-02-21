"""Integration test: manually edited YAML with wrong lock status is corrected on _save().

Scenario:
  A human (or broken tool) edits plan.yaml directly and sets a phase to PENDING
  even though its declared dependency is not DONE.  The bug should be silently
  corrected the next time any write operation is performed via _save(), which
  calls recalc_lock_status() before persisting.

Two sub-scenarios are covered:
  1. PENDING phase with unmet dependency  → should become LOCKED after _save().
  2. LOCKED  phase with all deps DONE     → should become PENDING after _save().
"""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from vectl.cli import _save, app
from vectl.io import load_plan
from vectl.models import PhaseStatus

runner = CliRunner()


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _write_yaml(path: Path, content: str) -> None:
    """Write raw YAML string to path (bypasses _save / recalc_lock_status)."""
    path.write_text(content, encoding="utf-8")


# ---------------------------------------------------------------------------
# tests
# ---------------------------------------------------------------------------


class TestManualYamlEditLockCorrection:
    """_save() corrects wrong lock statuses introduced by hand-editing plan.yaml."""

    def test_pending_with_unmet_dep_becomes_locked(self, tmp_path: Path) -> None:
        """A PENDING phase whose dependency is not DONE should become LOCKED."""
        plan_path = tmp_path / "plan.yaml"

        # Write intentionally wrong YAML: phase2 is PENDING but phase1 is not DONE.
        _write_yaml(
            plan_path,
            """\
project: test-manual-edit
phases:
  - id: phase1
    name: Phase 1
    status: pending
    steps:
      - id: s1
        name: Step 1
  - id: phase2
    name: Phase 2
    status: pending
    depends_on:
      - phase1
    steps:
      - id: s2
        name: Step 2
""",
        )

        # Load: no correction happens at load time.
        plan, file_hash = load_plan(plan_path)
        assert plan.phases[1].id == "phase2"
        assert plan.phases[1].status == PhaseStatus.PENDING, (
            "Pre-condition: loaded plan still has the wrong PENDING status"
        )

        # _save() calls recalc_lock_status() then save_plan().
        _save(plan, plan_path, file_hash)

        # Reload from disk and verify correction persisted.
        corrected_plan, _ = load_plan(plan_path)
        phase2 = next(p for p in corrected_plan.phases if p.id == "phase2")
        assert phase2.status == PhaseStatus.LOCKED, (
            f"Expected phase2 to be LOCKED after _save(), got {phase2.status}"
        )

    def test_locked_with_done_dep_becomes_pending(self, tmp_path: Path) -> None:
        """A LOCKED phase whose dependency IS DONE should become PENDING."""
        plan_path = tmp_path / "plan.yaml"

        # Write intentionally wrong YAML: phase2 is LOCKED but phase1 is DONE.
        _write_yaml(
            plan_path,
            """\
project: test-manual-edit
phases:
  - id: phase1
    name: Phase 1
    status: done
    steps:
      - id: s1
        name: Step 1
        status: done
  - id: phase2
    name: Phase 2
    status: locked
    depends_on:
      - phase1
    steps:
      - id: s2
        name: Step 2
""",
        )

        # Load: wrong LOCKED status is still present.
        plan, file_hash = load_plan(plan_path)
        assert plan.phases[1].id == "phase2"
        assert plan.phases[1].status == PhaseStatus.LOCKED, (
            "Pre-condition: loaded plan still has the wrong LOCKED status"
        )

        # _save() corrects it.
        _save(plan, plan_path, file_hash)

        # Reload and verify.
        corrected_plan, _ = load_plan(plan_path)
        phase2 = next(p for p in corrected_plan.phases if p.id == "phase2")
        assert phase2.status == PhaseStatus.PENDING, (
            f"Expected phase2 to be PENDING after _save(), got {phase2.status}"
        )

    def test_done_phase_not_regressed(self, tmp_path: Path) -> None:
        """Rule 1: _save() must never change a DONE phase back to LOCKED."""
        plan_path = tmp_path / "plan.yaml"

        _write_yaml(
            plan_path,
            """\
project: test-manual-edit
phases:
  - id: phase1
    name: Phase 1
    status: pending
    steps:
      - id: s1
        name: Step 1
  - id: phase2
    name: Phase 2
    status: done
    depends_on:
      - phase1
    steps:
      - id: s2
        name: Step 2
        status: done
""",
        )

        plan, file_hash = load_plan(plan_path)
        _save(plan, plan_path, file_hash)

        corrected_plan, _ = load_plan(plan_path)
        phase2 = next(p for p in corrected_plan.phases if p.id == "phase2")
        assert phase2.status == PhaseStatus.DONE, (
            f"DONE phase must not be regressed by _save(), got {phase2.status}"
        )


# ---------------------------------------------------------------------------
# dry-run: vectl recalc-lock --dry-run must NOT modify disk
# ---------------------------------------------------------------------------


class TestRecalcLockDryRun:
    """vectl recalc-lock --dry-run previews changes without persisting them."""

    _INCONSISTENT_YAML = """\
project: dry-run-test
phases:
  - id: phase1
    name: Phase 1
    status: pending
    steps:
      - id: s1
        name: Step 1
  - id: phase2
    name: Phase 2
    status: pending
    depends_on:
      - phase1
    steps:
      - id: s2
        name: Step 2
"""

    def test_dry_run_does_not_modify_disk(self, tmp_path: Path) -> None:
        """--dry-run must leave the plan file unchanged on disk."""
        plan_path = tmp_path / "plan.yaml"
        plan_path.write_text(self._INCONSISTENT_YAML, encoding="utf-8")

        original_content = plan_path.read_text(encoding="utf-8")

        result = runner.invoke(
            app, ["recalc-lock", "--dry-run", "--plan", str(plan_path)]
        )

        assert result.exit_code == 0, f"Unexpected exit: {result.output}"
        assert plan_path.read_text(encoding="utf-8") == original_content, (
            "--dry-run must not modify the plan file on disk"
        )

    def test_dry_run_reports_what_would_change(self, tmp_path: Path) -> None:
        """--dry-run output must describe the phases that would be corrected."""
        plan_path = tmp_path / "plan.yaml"
        plan_path.write_text(self._INCONSISTENT_YAML, encoding="utf-8")

        result = runner.invoke(
            app, ["recalc-lock", "--dry-run", "--plan", str(plan_path)]
        )

        assert result.exit_code == 0, f"Unexpected exit: {result.output}"
        assert "Lock status updated:" in result.output
        assert "phase2" in result.output
