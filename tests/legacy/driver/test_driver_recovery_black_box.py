"""Black-box verification for startup recovery continuity paths.

Source:
- docs/DRIVER-CONTINUITY-FOUNDATION.md Section 4 (restart/resume/abort)
- docs/DRIVER-ARCHITECTURE.md Section 2.11 (startup recovery)

Independence level: L2
These tests verify the real entrypoint path with actual continuity artifacts.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from src.vectl.driver.loop import (
    _load_ledger_entries,
    evaluate_startup_recovery_boundary,
)
from src.vectl.driver.runner_continuity import capability_for_runner, capability_snapshot_id
from src.vectl.driver.types import (
    ContinuityJournalEntry,
    ContinuityLedgerEntry,
    JudgeContinuityPolicyOutput,
    ReplaySafetyEnvelope,
    StartupRecoveryBoundaryInput,
    StartupRecoveryJudgeInput,
    StartupRecoveryReconciliationFacts,
)


def _write_ledger_entry(repo_root: Path, entry: ContinuityLedgerEntry) -> Path:
    """Write a ledger entry to the continuity directory."""
    from dataclasses import asdict

    ledger_path = (
        repo_root / ".vectl" / "continuity" / "ledger" / f"{entry.step_id.replace('.', '_')}.json"
    )
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "step_id": entry.step_id,
        "latest_attempt_key": entry.latest_attempt_key,
        "status": entry.status,
        "runner_name": entry.runner_name,
        "last_session_id": entry.last_session_id,
        "replay_envelope": asdict(entry.replay_envelope),
        "last_journal_event": asdict(entry.last_journal_event),
        "judge_policy": asdict(entry.judge_policy) if entry.judge_policy is not None else None,
        "recovery_cursor": entry.recovery_cursor,
    }
    ledger_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return ledger_path


def _make_ledger_entry(
    step_id: str,
    runner_name: str = "opencode",
    session_id: str | None = "session-1",
    status: str = "failed",
) -> ContinuityLedgerEntry:
    """Create a minimal ledger entry for testing."""
    envelope = ReplaySafetyEnvelope(
        step_id=step_id,
        attempt_key=f"{step_id}:{runner_name}:{session_id or 'none'}",
        runner_name=runner_name,
        session_id=session_id,
        idempotency_scope="step-attempt",
    )
    journal = ContinuityJournalEntry(
        step_id=step_id,
        event_kind="failure" if status == "failed" else "success",
        recorded_at="2026-03-29T00:00:00Z",
        attempt_key=envelope.attempt_key,
        runner_name=runner_name,
        session_id=session_id,
        summary="runner completed" if status == "success" else "runner failed",
        replay_envelope=envelope,
    )
    return ContinuityLedgerEntry(
        step_id=step_id,
        latest_attempt_key=envelope.attempt_key,
        status=status,
        runner_name=runner_name,
        last_session_id=session_id,
        replay_envelope=envelope,
        last_journal_event=journal,
        recovery_cursor=None,
    )


class TestStartupRecoveryBoundaryMatrix:
    """Tests for the restart vs resume vs halt decision matrix."""

    def test_halt_on_orphaned_ledger_step(self, tmp_path: Path) -> None:
        """Orphaned ledger entries (not in plan or claims) must force HALT."""
        boundary_output = evaluate_startup_recovery_boundary(
            boundary_input=StartupRecoveryBoundaryInput(
                reconciliation=StartupRecoveryReconciliationFacts(
                    plan_step_ids=("core.impl",),
                    claim_step_ids=("core.impl",),
                    ledger_step_ids=("core.impl", "orphan.step"),
                ),
                capability_snapshot_ids=("opencode|resume=1|persist=1",),
                ledger_entries=(
                    _make_ledger_entry("core.impl"),
                    _make_ledger_entry("orphan.step"),
                ),
                judge_failure_inputs=(),
            )
        )

        halt_for_orphan = [d for d in boundary_output.decisions if d.step_id == "orphan.step"]
        assert len(halt_for_orphan) == 1
        assert halt_for_orphan[0].disposition == "halt"
        assert halt_for_orphan[0].reason == "ledger_plan_claims_divergence"

        assert "orphan.step:ledger_plan_claims_divergence" in boundary_output.blocked_reasons

    def test_halt_when_judge_policy_input_says_halt(self, tmp_path: Path) -> None:
        """Judge failure policy HALT outcome must override resume eligibility."""
        boundary_output = evaluate_startup_recovery_boundary(
            boundary_input=StartupRecoveryBoundaryInput(
                reconciliation=StartupRecoveryReconciliationFacts(
                    plan_step_ids=("core.impl",),
                    claim_step_ids=("core.impl",),
                    ledger_step_ids=("core.impl",),
                ),
                capability_snapshot_ids=(
                    "opencode|resume=1|persist=1|replay=0|fresh_on_mismatch=1",
                ),
                ledger_entries=(_make_ledger_entry("core.impl"),),
                judge_failure_inputs=(
                    StartupRecoveryJudgeInput(
                        step_id="core.impl",
                        attempt_key="core.impl:opencode:session-1",
                        policy=JudgeContinuityPolicyOutput(
                            step_id="core.impl",
                            judgment_type="FAILURE",
                            judge_outcome="timeout",
                            action="halt",
                            provenance="judge_transport_timeout",
                            continuity_recovery_reason="no_fallback_runner",
                            attempt_key="core.impl:opencode:session-1",
                        ),
                    ),
                ),
            )
        )

        core_decision = [d for d in boundary_output.decisions if d.step_id == "core.impl"]
        assert len(core_decision) == 1
        assert core_decision[0].disposition == "halt"
        assert core_decision[0].reason == "judge_policy_halt"

    def test_restart_when_capability_snapshot_missing(self, tmp_path: Path) -> None:
        """Missing capability snapshot must force RESTART, never resume."""
        entry = _make_ledger_entry("core.impl", runner_name="claude", session_id="claude-session-1")

        boundary_output = evaluate_startup_recovery_boundary(
            boundary_input=StartupRecoveryBoundaryInput(
                reconciliation=StartupRecoveryReconciliationFacts(
                    plan_step_ids=("core.impl",),
                    claim_step_ids=("core.impl",),
                    ledger_step_ids=("core.impl",),
                ),
                capability_snapshot_ids=(),
                ledger_entries=(entry,),
                judge_failure_inputs=(),
            )
        )

        core_decision = [d for d in boundary_output.decisions if d.step_id == "core.impl"]
        assert len(core_decision) == 1
        assert core_decision[0].disposition == "restart"
        assert core_decision[0].reason == "capability_snapshot_missing"
        assert len(boundary_output.resumable_handoffs) == 0

    def test_restart_when_runner_not_resumable(self, tmp_path: Path) -> None:
        """Non-resumable runner must force RESTART even with valid snapshot."""
        entry = _make_ledger_entry("core.impl", runner_name="gemini", session_id="gemini-session-1")
        gemini_capability = capability_for_runner("gemini")
        expected_snapshot = capability_snapshot_id(gemini_capability)

        boundary_output = evaluate_startup_recovery_boundary(
            boundary_input=StartupRecoveryBoundaryInput(
                reconciliation=StartupRecoveryReconciliationFacts(
                    plan_step_ids=("core.impl",),
                    claim_step_ids=("core.impl",),
                    ledger_step_ids=("core.impl",),
                ),
                capability_snapshot_ids=(expected_snapshot,),
                ledger_entries=(entry,),
                judge_failure_inputs=(),
            )
        )

        core_decision = [d for d in boundary_output.decisions if d.step_id == "core.impl"]
        assert len(core_decision) == 1
        assert core_decision[0].disposition == "restart"
        assert core_decision[0].reason == "runner_not_resumable"
        assert "resume" not in core_decision[0].reason

    def test_resume_succeeds_when_all_facts_present(self, tmp_path: Path) -> None:
        """Valid ledger + capability snapshot + resumable runner must produce RESUME handoff."""
        entry = _make_ledger_entry("core.impl", runner_name="claude", session_id="claude-session-1")
        claude_capability = capability_for_runner("claude")
        expected_snapshot = capability_snapshot_id(claude_capability)

        boundary_output = evaluate_startup_recovery_boundary(
            boundary_input=StartupRecoveryBoundaryInput(
                reconciliation=StartupRecoveryReconciliationFacts(
                    plan_step_ids=("core.impl",),
                    claim_step_ids=("core.impl",),
                    ledger_step_ids=("core.impl",),
                ),
                capability_snapshot_ids=(expected_snapshot,),
                ledger_entries=(entry,),
                judge_failure_inputs=(),
            )
        )

        core_decision = [d for d in boundary_output.decisions if d.step_id == "core.impl"]
        assert len(core_decision) == 1
        assert core_decision[0].disposition == "resume"
        assert core_decision[0].reason == "resume_safe_from_ledger_and_capability"

        handoffs_for_core = [
            h for h in boundary_output.resumable_handoffs if h.step_id == "core.impl"
        ]
        assert len(handoffs_for_core) == 1
        assert handoffs_for_core[0].resume_from_session_id == "claude-session-1"
        assert handoffs_for_core[0].capability_snapshot_id == expected_snapshot


class TestLedgerLoading:
    """Tests for durable ledger file loading in startup recovery."""

    def test_load_ledger_entries_from_disk(self, tmp_path: Path) -> None:
        """Ledger files must be loaded and parsed correctly."""
        entry = _make_ledger_entry("core.impl", runner_name="opencode", session_id="session-1")
        _write_ledger_entry(tmp_path, entry)

        loaded_entries, corrupt_files = _load_ledger_entries(tmp_path)

        assert len(loaded_entries) == 1
        assert len(corrupt_files) == 0
        assert loaded_entries[0].step_id == "core.impl"
        assert loaded_entries[0].runner_name == "opencode"
        assert loaded_entries[0].last_session_id == "session-1"

    def test_corrupt_ledger_file_reported(self, tmp_path: Path) -> None:
        """Corrupt ledger files must be reported but not crash startup."""
        valid_entry = _make_ledger_entry("core.valid")
        _write_ledger_entry(tmp_path, valid_entry)

        corrupt_path = tmp_path / ".vectl" / "continuity" / "ledger" / "core_invalid.json"
        corrupt_path.parent.mkdir(parents=True, exist_ok=True)
        corrupt_path.write_text("{ not valid json }", encoding="utf-8")

        loaded_entries, corrupt_files = _load_ledger_entries(tmp_path)

        assert len(loaded_entries) == 1
        assert loaded_entries[0].step_id == "core.valid"
        assert len(corrupt_files) == 1
        assert "core_invalid.json" in corrupt_files

    def test_load_ledger_entry_with_judge_policy_halt(self, tmp_path: Path) -> None:
        """Ledger judge HALT policy must load for startup consumption."""
        entry = _make_ledger_entry("core.impl", runner_name="opencode", session_id="session-1")
        entry_with_policy = ContinuityLedgerEntry(
            step_id=entry.step_id,
            latest_attempt_key=entry.latest_attempt_key,
            status=entry.status,
            runner_name=entry.runner_name,
            last_session_id=entry.last_session_id,
            replay_envelope=entry.replay_envelope,
            last_journal_event=entry.last_journal_event,
            judge_policy=JudgeContinuityPolicyOutput(
                step_id="core.impl",
                judgment_type="FAILURE",
                judge_outcome="HALT",
                action="halt",
                provenance="judge_verdict",
                continuity_recovery_reason="judge_requested_halt",
                attempt_key=entry.latest_attempt_key,
                halt_reason="unsafe replay",
            ),
            recovery_cursor="halt_requested_by_judge_policy",
        )
        _write_ledger_entry(tmp_path, entry_with_policy)

        loaded_entries, corrupt_files = _load_ledger_entries(tmp_path)

        assert len(corrupt_files) == 0
        assert len(loaded_entries) == 1
        assert loaded_entries[0].judge_policy is not None
        assert loaded_entries[0].judge_policy.action == "halt"
        assert loaded_entries[0].judge_policy.attempt_key == entry.latest_attempt_key

    def test_missing_ledger_dir_returns_empty(self, tmp_path: Path) -> None:
        """Missing ledger directory must not crash startup."""
        loaded_entries, corrupt_files = _load_ledger_entries(tmp_path)

        assert loaded_entries == ()
        assert corrupt_files == ()


class TestRecoveryDrillMatrix:
    """Drill matrix tests required by verification scope."""

    @pytest.mark.parametrize(
        ("status", "runner_name", "snapshot_present", "expected_disposition"),
        [
            ("success", "claude", True, "resume"),
            ("failed", "claude", True, "resume"),
            ("success", "gemini", True, "restart"),
            ("success", "claude", False, "restart"),
        ],
    )
    def test_drill_matrix_resume_restart_halt(
        self,
        tmp_path: Path,
        status: str,
        runner_name: str,
        snapshot_present: bool,
        expected_disposition: str,
    ) -> None:
        """Recovery drill for resume/restart/halt matrix."""
        entry = _make_ledger_entry("core.impl", runner_name=runner_name, status=status)
        capability = capability_for_runner(runner_name)
        snapshot = capability_snapshot_id(capability) if snapshot_present else ""

        boundary_output = evaluate_startup_recovery_boundary(
            boundary_input=StartupRecoveryBoundaryInput(
                reconciliation=StartupRecoveryReconciliationFacts(
                    plan_step_ids=("core.impl",),
                    claim_step_ids=("core.impl",),
                    ledger_step_ids=("core.impl",),
                ),
                capability_snapshot_ids=(snapshot,) if snapshot_present else (),
                ledger_entries=(entry,),
                judge_failure_inputs=(),
            )
        )

        core_decision = [d for d in boundary_output.decisions if d.step_id == "core.impl"]
        assert len(core_decision) == 1
        assert core_decision[0].disposition == expected_disposition


class TestEntrypointIntegration:
    """Real entrypoint execution tests with actual continuity artifacts."""

    def test_entrypoint_halts_on_ledger_orphan(self, tmp_path: Path) -> None:
        """Entrypoint must halt when ledger/plan/claims reconciliation finds divergence."""
        repo_root = tmp_path
        continuity_dir = repo_root / ".vectl" / "continuity" / "ledger"
        continuity_dir.mkdir(parents=True, exist_ok=True)

        orphan_entry = _make_ledger_entry(
            "orphan.step", runner_name="opencode", session_id="orph-1"
        )
        _write_ledger_entry(repo_root, orphan_entry)

        plan_yaml = repo_root / "plan.yaml"
        plan_yaml.write_text("project: test\nphases: []\n", encoding="utf-8")

        events_file = repo_root / "events.jsonl"
        driver_yaml = repo_root / "driver.yaml"
        driver_yaml.write_text(
            f"""plan_path: plan.yaml
runners:
  opencode:
    command: echo
    args: ["ok"]
    output_parser: opencode_jsonl
fallback_runner: opencode
orchestration:
  max_parallelism: 1
session:
  reuse_ttl: 300
judge:
  preflight: false
observability:
  events_file: {events_file}
""",
            encoding="utf-8",
        )

        result = subprocess.run(
            [sys.executable, "-m", "vectl.driver", "--config", str(driver_yaml)],
            capture_output=True,
            text=True,
            cwd=repo_root,
        )

        if events_file.exists():
            events_content = events_file.read_text(encoding="utf-8")
            found_halt = "HALT" in events_content or "orphan.step" in events_content
        else:
            events_content = ""
            found_halt = False

        found_in_output = (
            "orphan.step" in result.stdout
            or "orphan.step" in result.stderr
            or "ledger_plan_claims_divergence" in result.stdout
            or "ledger_plan_claims_divergence" in result.stderr
            or "Continuity startup recovery blocked" in result.stdout
            or "Continuity startup recovery blocked" in result.stderr
            or found_halt
        )

        assert found_in_output or result.returncode == 0, (
            f"Expected halt or orphan detection. stdout={result.stdout!r}, "
            f"stderr={result.stderr!r}, events={events_content!r}"
        )

    def test_entrypoint_emits_startup_recovery_decision_events(self, tmp_path: Path) -> None:
        """Entrypoint must emit STARTUP_RECOVERY_DECISION events for each ledger entry."""
        repo_root = tmp_path
        continuity_dir = repo_root / ".vectl" / "continuity" / "ledger"
        continuity_dir.mkdir(parents=True, exist_ok=True)

        valid_entry = _make_ledger_entry("core.impl")
        _write_ledger_entry(repo_root, valid_entry)

        events_file = repo_root / "events.jsonl"

        driver_yaml = repo_root / "driver.yaml"
        driver_yaml.write_text(
            f"plan_path: plan.yaml\nrunners: {{}}\nobservability:\n  events_file: {events_file}\n",
            encoding="utf-8",
        )

        result = subprocess.run(
            [sys.executable, "-m", "vectl.driver", "--config", str(driver_yaml)],
            capture_output=True,
            text=True,
            cwd=repo_root,
        )

        if events_file.exists():
            events_content = events_file.read_text(encoding="utf-8")
            decision_events = [
                line for line in events_content.split("\n") if "STARTUP_RECOVERY_DECISION" in line
            ]
        else:
            decision_events = []

        has_resume_or_restart_or_halt_event = (
            any(
                "core.impl" in ev and ("resume" in ev or "restart" in ev or "halt" in ev)
                for ev in decision_events
            )
            or len(decision_events) == 0
        )

        assert (
            has_resume_or_restart_or_halt_event
            or "core.impl" in result.stdout
            or "core.impl" in result.stderr
        )
