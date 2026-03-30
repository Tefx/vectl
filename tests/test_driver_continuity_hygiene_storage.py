"""Storage/helper tests for startup continuity hygiene implementation."""

from __future__ import annotations

import json
from pathlib import Path

from src.vectl.driver.continuity_hygiene import (
    StartupHygieneStageInput,
    apply_quarantine,
    classify_artifact,
    get_quarantine_manifest,
    run_startup_hygiene_stage,
    scan_continuity_artifacts,
)


def test_scan_missing_continuity_dirs_returns_empty(tmp_path: Path) -> None:
    records, corrupt = scan_continuity_artifacts(tmp_path)
    assert records == ()
    assert corrupt == ()


def test_scan_reports_corrupt_json_and_jsonl(tmp_path: Path) -> None:
    ledger_dir = tmp_path / ".vectl" / "continuity" / "ledger"
    journal_dir = tmp_path / ".vectl" / "continuity" / "journal"
    ledger_dir.mkdir(parents=True, exist_ok=True)
    journal_dir.mkdir(parents=True, exist_ok=True)

    (ledger_dir / "broken.json").write_text("{not-json}", encoding="utf-8")
    (journal_dir / "broken.jsonl").write_text('{"step_id":"x"}\nnot-json\n', encoding="utf-8")

    records, corrupt = scan_continuity_artifacts(tmp_path)
    assert len(records) == 2
    assert len(corrupt) == 2


def test_claims_divergence_precedence_blocks_before_safe_stale(tmp_path: Path) -> None:
    ledger_dir = tmp_path / ".vectl" / "continuity" / "ledger"
    ledger_dir.mkdir(parents=True, exist_ok=True)
    source = ledger_dir / "orphan.json"
    source.write_text(json.dumps({"step_id": "orphan.step"}), encoding="utf-8")

    records, _ = scan_continuity_artifacts(tmp_path)
    result = run_startup_hygiene_stage(
        stage_input=StartupHygieneStageInput(
            current_branch="main",
            current_plan_step_ids=("current.step",),
            repaired_current_branch_claim_step_ids=("orphan.step",),
            artifacts=records,
            quarantine_root=str(tmp_path / ".vectl" / "continuity" / "quarantine"),
        )
    )

    assert len(result.blocked_assessments) == 1
    assert result.blocked_assessments[0].classification == "blocking_divergence"
    assert "current_claimed_step" in result.blocked_assessments[0].reason


def test_apply_quarantine_copies_bytes_and_writes_manifest(tmp_path: Path) -> None:
    ledger_dir = tmp_path / ".vectl" / "continuity" / "ledger"
    quarantine_dir = tmp_path / ".vectl" / "continuity" / "quarantine"
    ledger_dir.mkdir(parents=True, exist_ok=True)
    source = ledger_dir / "abandoned.json"
    payload = {"step_id": "abandoned.step", "status": "failed"}
    source.write_text(json.dumps(payload), encoding="utf-8")

    records, _ = scan_continuity_artifacts(tmp_path)
    stage_result = run_startup_hygiene_stage(
        stage_input=StartupHygieneStageInput(
            current_branch="main",
            current_plan_step_ids=("current.step",),
            repaired_current_branch_claim_step_ids=("current.step",),
            artifacts=records,
            quarantine_root=str(quarantine_dir),
        )
    )
    applied = apply_quarantine(stage_result=stage_result, quarantine_root=quarantine_dir)

    assert len(applied.quarantine_manifest) == 1
    entry = applied.quarantine_manifest[0]
    assert entry.original_path == str(source)
    assert entry.quarantine_destination
    assert entry.audit_timestamp
    destination = Path(entry.quarantine_destination)
    assert destination.exists()
    assert source.exists()
    assert destination.read_bytes() == source.read_bytes()


def test_quarantine_manifest_last_writer_wins(tmp_path: Path) -> None:
    quarantine_dir = tmp_path / ".vectl" / "continuity" / "quarantine"

    class _Artifact:
        artifact_type = "ledger"
        step_id = "safe.step"
        source_path = tmp_path / ".vectl" / "continuity" / "ledger" / "safe.json"
        content = {"step_id": "safe.step"}

    classify_artifact(
        artifact=_Artifact(),
        plan_step_ids={"current.step"},
        claims_step_ids={"current.step"},
        corrupt_files=None,
    )

    from src.vectl.driver.continuity_hygiene import quarantine_artifact

    quarantine_artifact(artifact=_Artifact(), reason="first", quarantine_dir=quarantine_dir)
    quarantine_artifact(artifact=_Artifact(), reason="second", quarantine_dir=quarantine_dir)

    manifest = get_quarantine_manifest(quarantine_dir)
    assert len(manifest) == 1
    assert manifest[0].reason == "second"
