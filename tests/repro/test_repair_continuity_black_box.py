"""Black-box verification for `vectl repair continuity` CLI command.

Expected: `repair continuity` correctly classifies artifacts per spec.
Actual: Verifying through public CLI surface only.

Architecture: docs/DRIVER-CONTINUITY-FOUNDATION.md Section 7a (Startup Hygiene and Quarantine)
"""

from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path

import yaml


# =============================================================================
# SPEC-FIXTURE CONFORMANCE: Exact artifact formats from continuity contracts
# Source: DRIVER-CONTINUITY-FOUNDATION.md Section 7a
# =============================================================================

# Per spec §7a: Artifact classifications
# | Classification | Blocks Startup | Quarantine Allowed | Definition |
# |----------------|----------------|-------------------|------------|
# | safe_stale_quarantine | No | Yes | Step absent from plan and repaired claims |
# | blocking_divergence | Yes | No | Conflicts with current plan or claims |
# | corrupt_blocking | Yes | No | Unparsable or missing step identity |
# | ambiguous_blocking | Yes | No | Migration/rename suspicion detected |


def make_valid_ledger_entry(step_id: str, session_id: str, status: str = "completed") -> dict:
    """Create a valid ledger entry per spec contract.

    Source: src/vectl/driver/types.py::ContinuityLedgerEntry
    Required fields: step_id, session_id, status, replay_envelope
    """
    attempt_key = f"{step_id}|opencode|{session_id}"
    return {
        "step_id": step_id,
        "session_id": session_id,
        "status": status,
        "runner_name": "opencode",
        "replay_envelope": {
            "attempt_key": attempt_key,
            "step_id": step_id,
            "session_id": session_id,
            "runner_name": "opencode",
            "idempotency_scope": "reconcile_success"
            if status == "completed"
            else "reconcile_failure",
        },
        "latest_attempt_key": attempt_key,
        "recovery_cursor": "complete" if status == "completed" else "awaiting_retry_or_escalation",
    }


def make_valid_journal_entry(step_id: str, session_id: str, event_kind: str = "success") -> dict:
    """Create a valid journal entry per spec contract.

    Source: src/vectl/driver/types.py::ContinuityJournalEntry
    Required fields: step_id, session_id, event_kind, recorded_at, replay_envelope
    """
    from datetime import datetime, timezone

    attempt_key = f"{step_id}|opencode|{session_id}"
    return {
        "attempt_key": attempt_key,
        "step_id": step_id,
        "session_id": session_id,
        "event_kind": event_kind,
        "runner_name": "opencode",
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "replay_envelope": {
            "attempt_key": attempt_key,
            "step_id": step_id,
            "session_id": session_id,
            "runner_name": "opencode",
            "idempotency_scope": "reconcile_success"
            if event_kind == "success"
            else "reconcile_failure",
        },
    }


def write_continuity_artifacts(
    ledger_dir: Path,
    journal_dir: Path,
    artifacts: dict[str, list[dict]],
) -> None:
    """Write test continuity artifacts in spec format.

    Args:
        ledger_dir: Directory for .json ledger files
        journal_dir: Directory for .jsonl journal files
        artifacts: Dict mapping step_id to list of [ledger_entry, journal_entry]
    """
    ledger_dir.mkdir(parents=True, exist_ok=True)
    journal_dir.mkdir(parents=True, exist_ok=True)

    for step_id, entries in artifacts.items():
        if len(entries) >= 1 and entries[0] is not None:
            # Write ledger
            ledger_path = ledger_dir / f"{step_id}.json"
            ledger_path.write_text(json.dumps(entries[0], indent=2))

        if len(entries) >= 2 and entries[1] is not None:
            # Write journal
            journal_path = journal_dir / f"{step_id}.jsonl"
            lines = [json.dumps(entries[1])]
            journal_path.write_text("\n".join(lines) + "\n")


def run_repair_continuity(
    repo_root: Path, extra_args: list[str] | None = None
) -> subprocess.CompletedProcess:
    """Execute `vectl repair continuity` CLI command.

    Returns subprocess result for caller to verify.
    """
    args = ["uv", "run", "vectl", "repair", "continuity", "--plan", str(repo_root / "plan.yaml")]
    if extra_args:
        args.extend(extra_args)

    return subprocess.run(
        args,
        capture_output=True,
        text=True,
        timeout=30,
        cwd=str(repo_root),
    )


def make_minimal_plan(repo_root: Path, step_ids: list[str]) -> Path:
    """Create minimal plan.yaml with given step IDs (all marked done).

    Per plan.yaml schema: steps use `id` field, not `step_id`.
    """
    plan = {
        "version": 1,
        "project": "continuity-test",
        "strategy_ref": "#",
        "context": "Black-box continuity hygiene verification",
        "phases": [
            {
                "id": "test-phase",
                "name": "Test Phase",
                "status": "done",
                "gate": "All done",
                "steps": [
                    {
                        "id": sid,  # Per schema: `id` not `step_id`
                        "name": f"Step {sid}",
                        "status": "done",
                        "description": f"Test step {sid}",
                        "agent": "blind-tester",
                    }
                    for sid in step_ids
                ],
            }
        ],
    }
    plan_path = repo_root / "plan.yaml"
    plan_path.write_text(yaml.dump(plan, sort_keys=False))
    return plan_path


# =============================================================================
# TEST 1: CLI Reachable
# =============================================================================


def test_cli_reachable():
    """Verify `vectl repair continuity --help` produces documented output."""
    result = subprocess.run(
        ["uv", "run", "vectl", "repair", "continuity", "--help"],
        capture_output=True,
        text=True,
        timeout=30,
        cwd="/Users/tefx/Projects/vectl",
    )

    assert result.returncode == 0, (
        f"CLI help failed with exit code {result.returncode}\nstderr: {result.stderr}"
    )

    # Per spec §7a, help must document these concepts
    help_text = result.stdout
    assert "Scan and repair" in help_text or "continuity" in help_text.lower(), (
        f"Help missing continuity reference:\n{help_text}"
    )
    assert "--dry-run" in help_text, f"Help missing --dry-run option:\n{help_text}"
    assert "--json" in help_text, f"Help missing --json option:\n{help_text}"

    # Verify documented classifications
    assert "safe_stale_quarantine" in help_text, (
        f"Help missing safe_stale classification:\n{help_text}"
    )
    assert "blocking_divergence" in help_text, f"Help missing blocking_divergence:\n{help_text}"
    assert "corrupt_blocking" in help_text, f"Help missing corrupt_blocking:\n{help_text}"
    assert "ambiguous_blocking" in help_text, f"Help missing ambiguous_blocking:\n{help_text}"

    print("PASS: vectl repair continuity --help documents all required classifications")


# =============================================================================
# TEST 2: Safe Stale Quarantine
# =============================================================================


def test_safe_stale_quarantine():
    """Verify safe_stale_quarantine artifacts are quarantined (not deleted).

    Per spec §7a:
    - safe_stale_quarantine: Step absent from plan AND absent from repaired claims
    - Quarantine action: copy to .vectl/continuity/quarantine/
    - Original bytes preserved (no silent deletion)
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)

        # Create continuity directories
        continuity_dir = tmpdir / ".vectl" / "continuity"
        ledger_dir = continuity_dir / "ledger"
        journal_dir = continuity_dir / "journal"
        quarantine_dir = continuity_dir / "quarantine"

        # Create step ID that does NOT exist in plan = safe stale
        SAFE_STALE_STEP = "nonexistent.step.that.is.safe"

        # Write artifacts
        write_continuity_artifacts(
            ledger_dir,
            journal_dir,
            {
                SAFE_STALE_STEP: [
                    make_valid_ledger_entry(SAFE_STALE_STEP, "ses_test_001"),
                    make_valid_journal_entry(SAFE_STALE_STEP, "ses_test_001"),
                ]
            },
        )

        # Create plan WITHOUT the safe_stale step
        make_minimal_plan(tmpdir, ["some.other.step"])

        # Run dry-run first
        result = run_repair_continuity(tmpdir, ["--dry-run", "--json"])

        assert result.returncode == 0, (
            f"dry-run failed:\nstderr: {result.stderr}\nstdout: {result.stdout}"
        )

        output = json.loads(result.stdout)

        # Verify safe_stale is classified correctly
        # Note: field name is parsed_step_id (parsed from artifact content/filename)
        safe_stale_found = False
        for assessment in output.get("classifications", []):
            if assessment.get("parsed_step_id") == SAFE_STALE_STEP:
                assert assessment.get("classification") == "safe_stale_quarantine", (
                    f"Expected safe_stale_quarantine, got {assessment.get('classification')}"
                )
                assert not assessment.get("blocks_startup_recovery"), (
                    "safe_stale should not block startup"
                )
                safe_stale_found = True
                break

        assert safe_stale_found, (
            f"safe_stale step not found in classifications:\n{json.dumps(output, indent=2)}"
        )
        assert output.get("quarantined_count", 0) == 0, "dry-run should not quarantine"

        # Verify original files still exist (no deletion in dry-run)
        assert (ledger_dir / f"{SAFE_STALE_STEP}.json").exists(), (
            "dry-run should not delete original ledger"
        )

        # Run apply (without --dry-run)
        result = run_repair_continuity(tmpdir, ["--json"])

        assert result.returncode == 0, (
            f"apply failed:\nstderr: {result.stderr}\nstdout: {result.stdout}"
        )

        output = json.loads(result.stdout)

        # Verify quarantine happened
        assert output.get("quarantined_count", 0) >= 1, (
            f"Expected at least 1 quarantined artifact, got {output.get('quarantined_count')}"
        )

        # Verify quarantine manifest exists
        manifest_path = quarantine_dir / "manifest.jsonl"
        assert manifest_path.exists(), "Quarantine manifest not created"

        # Load manifest entries
        manifest_entries = []
        for line in manifest_path.read_text().strip().split("\n"):
            if line.strip():
                manifest_entries.append(json.loads(line))

        # Find our safe_stale artifact in manifest
        safe_stale_entries = [e for e in manifest_entries if e.get("step_id") == SAFE_STALE_STEP]
        assert len(safe_stale_entries) >= 1, (
            f"safe_stale not in manifest:\n{manifest_path.read_text()}"
        )

        entry = safe_stale_entries[0]
        assert entry.get("classification") == "safe_stale_quarantine"
        assert entry.get("reason") is not None
        assert entry.get("original_path") is not None
        assert entry.get("quarantine_destination") is not None
        assert entry.get("audit_timestamp") is not None

        # Original bytes preserved (original file still exists per spec)
        assert (ledger_dir / f"{SAFE_STALE_STEP}.json").exists(), (
            "spec guardrail violated: original file was deleted"
        )

        # Quarantine copy exists
        quarantined_ledger = quarantine_dir / "ledger" / f"{SAFE_STALE_STEP}.json"
        assert quarantined_ledger.exists(), "Quarantine copy not created"

        print("PASS: safe_stale_quarantine artifacts are quarantined (not deleted)")
        print(f"PASS: Manifest entry created with audit trail: {entry.get('audit_timestamp')}")


# =============================================================================
# TEST 3: Blocking Divergence (Active Claim)
# =============================================================================


def test_blocking_divergence_active_claim():
    """Verify blocking_divergence for active claims prevents startup.

    Per spec §7a:
    - current-claim disagreement remains blocking
    - Divergence between ledger/journal and claims blocks startup
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)

        # Create continuity directories
        continuity_dir = tmpdir / ".vectl" / "continuity"
        ledger_dir = continuity_dir / "ledger"
        journal_dir = continuity_dir / "journal"

        # Create step ID that exists in plan (blocking = current_plan_step)
        BLOCKING_STEP = "test-phase.current-claimed"

        # Write artifacts
        write_continuity_artifacts(
            ledger_dir,
            journal_dir,
            {
                BLOCKING_STEP: [
                    make_valid_ledger_entry(BLOCKING_STEP, "ses_test_002", status="running"),
                    make_valid_journal_entry(BLOCKING_STEP, "ses_test_002", event_kind="start"),
                ]
            },
        )

        # Create plan WITH the step
        make_minimal_plan(tmpdir, [BLOCKING_STEP])

        # Run dry-run
        result = run_repair_continuity(tmpdir, ["--dry-run", "--json"])

        assert result.returncode == 0, (
            f"dry-run failed:\nstderr: {result.stderr}\nstdout: {result.stdout}"
        )

        output = json.loads(result.stdout)

        # Verify blocking_divergence classification
        # Note: field name is parsed_step_id
        blocking_found = False
        for assessment in output.get("blocked_assessments", []):
            if assessment.get("parsed_step_id") == BLOCKING_STEP:
                classification = assessment.get("classification")
                # Per spec: current_plan_step is a blocking_divergence
                assert classification == "blocking_divergence", (
                    f"Expected blocking_divergence for current_plan_step, got {classification}"
                )
                assert assessment.get("blocks_startup_recovery"), (
                    "blocking_divergence should block startup"
                )
                assert output.get("blocked_count", 0) >= 1, "Should report blocked artifacts"
                blocking_found = True
                break

        assert blocking_found, f"blocking step not found:\n{json.dumps(output, indent=2)}"

        # Run apply - should NOT quarantine blocking artifacts
        result = run_repair_continuity(tmpdir, ["--json"])
        output = json.loads(result.stdout)

        # Blocked artifacts should remain in place
        assert output.get("quarantined_count", 0) == 0, (
            "blocking_divergence should NOT be quarantined"
        )

        print("PASS: blocking_divergence classifications block startup correctly")


# =============================================================================
# TEST 4: Corrupt Blocking
# =============================================================================


def test_corrupt_blocking():
    """Verify corrupt_blocking for unparsable artifacts.

    Per spec §7a:
    - corrupt files remain blocking
    - Unparsable artifacts halt startup recovery
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)

        # Create continuity directories
        continuity_dir = tmpdir / ".vectl" / "continuity"
        ledger_dir = continuity_dir / "ledger"
        journal_dir = continuity_dir / "journal"

        # Create corrupt ledger (invalid JSON)
        ledger_dir.mkdir(parents=True, exist_ok=True)
        ledger_path = ledger_dir / "corrupt-test.json"
        ledger_path.write_text("{ this is not valid json }")  # Invalid JSON

        make_minimal_plan(tmpdir, [])

        # Run dry-run
        result = run_repair_continuity(tmpdir, ["--dry-run", "--json"])

        assert result.returncode == 0, (
            f"dry-run failed:\nstderr: {result.stderr}\nstdout: {result.stdout}"
        )

        output = json.loads(result.stdout)

        # Verify corrupt_artifacts contains our file
        corrupt_found = False
        for err_path in output.get("corrupt_artifacts", []):
            if "corrupt-test" in err_path:
                corrupt_found = True
                break

        # Alternatively check blocked_assessments
        for assessment in output.get("blocked_assessments", []):
            if assessment.get("classification") == "corrupt_blocking":
                corrupt_found = True
                break

        assert corrupt_found, f"corrupt artifact not detected:\n{json.dumps(output, indent=2)}"

        print("PASS: corrupt_blocking artifacts detected correctly")


# =============================================================================
# TEST 5: Ambiguous Blocking
# =============================================================================


def test_ambiguous_blocking():
    """Verify ambiguous_blocking for migration/rename suspicion.

    Per spec §7a:
    - ambiguous identity remains blocking
    - No auto-remap of renamed step IDs
    - If step ID suggests migration, startup blocks
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)

        # Create continuity directories
        continuity_dir = tmpdir / ".vectl" / "continuity"
        ledger_dir = continuity_dir / "ledger"
        journal_dir = continuity_dir / "journal"

        # Create step ID that suggests migration/ambiguity
        # Per spec §7a: IDs containing migrated, duplicate, renamed, legacy_alias
        AMBIGUOUS_STEP = "auth.user-migrated-model"

        write_continuity_artifacts(
            ledger_dir,
            journal_dir,
            {
                AMBIGUOUS_STEP: [
                    make_valid_ledger_entry(AMBIGUOUS_STEP, "ses_test_003"),
                    make_valid_journal_entry(AMBIGUOUS_STEP, "ses_test_003"),
                ]
            },
        )

        make_minimal_plan(tmpdir, [])

        # Run dry-run
        result = run_repair_continuity(tmpdir, ["--dry-run", "--json"])

        assert result.returncode == 0, (
            f"dry-run failed:\nstderr: {result.stderr}\nstdout: {result.stdout}"
        )

        output = json.loads(result.stdout)

        # Verify ambiguous_blocking classification
        # Note: ambiguous entries appear in blocked_assessments not classifications
        # because they block (not safe_stale)
        ambiguous_found = False
        for assessment in output.get("blocked_assessments", output.get("classifications", [])):
            if assessment.get("parsed_step_id") == AMBIGUOUS_STEP:
                classification = assessment.get("classification")
                # Per spec: migration-suggesting IDs are ambiguous_blocking
                assert classification == "ambiguous_blocking", (
                    f"Expected ambiguous_blocking, got {classification}"
                )
                assert assessment.get("blocks_startup_recovery"), "ambiguous should block startup"
                ambiguous_found = True
                break

        assert ambiguous_found, f"ambiguous step not classified:\n{json.dumps(output, indent=2)}"

        print("PASS: ambiguous_blocking detected for migration-suggesting IDs")


# =============================================================================
# TEST 6: Audit Output
# =============================================================================


def test_audit_output_json():
    """Verify --json produces machine-readable audit output.

    Per spec §7a: Manifest format must include:
    - artifact_kind
    - original_path
    - reason
    - classification
    - quarantine_destination (for safe_stale)
    - audit_timestamp
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)

        # Create continuity directories
        continuity_dir = tmpdir / ".vectl" / "continuity"
        ledger_dir = continuity_dir / "ledger"
        journal_dir = continuity_dir / "journal"

        SAFE_STEP = "audit-test.safe-stale"

        write_continuity_artifacts(
            ledger_dir,
            journal_dir,
            {
                SAFE_STEP: [
                    make_valid_ledger_entry(SAFE_STEP, "ses_test_audit"),
                    make_valid_journal_entry(SAFE_STEP, "ses_test_audit"),
                ]
            },
        )

        make_minimal_plan(tmpdir, ["unrelated.step"])

        # Run with --json
        result = run_repair_continuity(tmpdir, ["--json"])

        assert result.returncode == 0, (
            f"--json failed:\nstderr: {result.stderr}\nstdout: {result.stdout}"
        )

        # Verify output is valid JSON
        output = json.loads(result.stdout)

        # Per spec, required fields in output:
        required_fields = [
            "artifacts_scanned",
            "blocked_count",
            "quarantined_count",
            "classifications",
            "guardrails",
            "policy",
        ]
        for field in required_fields:
            assert field in output, f"JSON output missing required field: {field}"

        # Verify guardrails are documented
        guardrails = output.get("guardrails", [])
        assert len(guardrails) >= 4, "Should document at least 4 guardrails"
        guardrails_str = " ".join(guardrails)
        assert "no silent deletion" in guardrails_str.lower(), (
            f"Guardrails missing 'no silent deletion': {guardrails}"
        )

        print("PASS: --json output is valid and contains required audit fields")


def test_audit_output_human_readable():
    """Verify default output is human-readable.

    Per spec §7a: Error messages should be understandable by operators.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)

        continuity_dir = tmpdir / ".vectl" / "continuity"
        ledger_dir = continuity_dir / "ledger"
        journal_dir = continuity_dir / "journal"

        BLOCKING_STEP = "human-test.blocking-step"

        write_continuity_artifacts(
            ledger_dir,
            journal_dir,
            {
                BLOCKING_STEP: [
                    make_valid_ledger_entry(BLOCKING_STEP, "ses_human_test"),
                    make_valid_journal_entry(BLOCKING_STEP, "ses_human_test"),
                ]
            },
        )

        make_minimal_plan(tmpdir, [BLOCKING_STEP])

        # Run without --json (human-readable)
        result = run_repair_continuity(tmpdir, ["--dry-run"])

        assert result.returncode == 0, (
            f"dry-run failed:\nstderr: {result.stderr}\nstdout: {result.stdout}"
        )

        output = result.stdout

        # Human-readable should show classification clearly
        assert "Blocking" in output or "blocking" in output.lower(), (
            f"Human output should show blocking status:\n{output}"
        )

        # Should indicate action required
        assert (
            "require" in output.lower()
            or "operator" in output.lower()
            or "action" in output.lower()
        ), f"Human output should mention action required:\n{output}"

        print("PASS: Human-readable output guides operators correctly")


if __name__ == "__main__":
    import sys

    tests = [
        ("CLI Reachable", test_cli_reachable),
        ("Safe Stale Quarantine", test_safe_stale_quarantine),
        ("Blocking Divergence", test_blocking_divergence_active_claim),
        ("Corrupt Blocking", test_corrupt_blocking),
        ("Ambiguous Blocking", test_ambiguous_blocking),
        ("Audit Output JSON", test_audit_output_json),
        ("Audit Output Human", test_audit_output_human_readable),
    ]

    passed = 0
    failed = []

    for name, test_fn in tests:
        try:
            test_fn()
            passed += 1
            print(f"\n✓ {name}")
        except AssertionError as e:
            failed.append((name, str(e)))
            print(f"\n✗ {name}: {e}")
        except Exception as e:
            failed.append((name, str(e)))
            print(f"\n✗ {name}: {e}")

    print(f"\n{'=' * 60}")
    print(f"Results: {passed}/{len(tests)} passed")

    if failed:
        print("\nVERDICT: FAILED")
        for name, err in failed:
            print(f"\nFailed: {name}")
            print(f"  {err}")
        sys.exit(1)
    else:
        print("\nVERDICT: FIXED - All black-box verification tests pass")
        sys.exit(0)
