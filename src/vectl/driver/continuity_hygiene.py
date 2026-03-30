"""Startup continuity hygiene scanning, classification, and quarantine.

Authority:
- docs/DRIVER-CONTINUITY-FOUNDATION.md §3 source-of-truth matrix
- docs/DRIVER-CONTINUITY-FOUNDATION.md §4 Restart
- docs/DRIVER-CONTINUITY-FOUNDATION.md §5 state strata
- docs/DRIVER-CONTINUITY-FOUNDATION.md §7 cross-cutting governance

This module evaluates startup continuity artifacts (ledger + journal),
classifies each artifact against current plan/claims state, and quarantines only
provably stale artifacts while preserving source bytes for auditability.
"""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Final, Literal, Protocol

ContinuityArtifactKind = Literal["ledger", "journal"]
ContinuityArtifactClassification = Literal[
    "safe_stale_quarantine",
    "blocking_divergence",
    "corrupt_blocking",
    "ambiguous_blocking",
]

SAFE_STALE_QUARANTINE_RULE: Final[str] = (
    "Artifact qualifies as safe stale quarantine only when its step is absent "
    "from the current plan and absent from repaired current-branch claims."
)

STARTUP_HYGIENE_GUARDRAILS: Final[tuple[str, ...]] = (
    "no silent deletion during startup",
    "no auto-remap of renamed or migrated step IDs",
    "corrupt files remain blocking",
    "current-claim disagreement remains blocking",
    "ambiguous identity remains blocking",
)

QUARANTINE_MANIFEST_FILE: Final[str] = "manifest.jsonl"


@dataclass(frozen=True)
class ContinuityArtifactClassificationRule:
    """Normative semantics for one startup hygiene classification outcome."""

    classification: ContinuityArtifactClassification
    blocks_startup_recovery: bool
    quarantine_allowed: bool
    definition: str


CONTINUITY_ARTIFACT_CLASSIFICATION_RULES: Final[
    tuple[ContinuityArtifactClassificationRule, ...]
] = (
    ContinuityArtifactClassificationRule(
        classification="safe_stale_quarantine",
        blocks_startup_recovery=False,
        quarantine_allowed=True,
        definition=(
            "Artifact belongs to a step absent from the current plan and absent "
            "from repaired current-branch claims, so it may be quarantined but "
            "must not be silently deleted."
        ),
    ),
    ContinuityArtifactClassificationRule(
        classification="blocking_divergence",
        blocks_startup_recovery=True,
        quarantine_allowed=False,
        definition=(
            "Artifact conflicts with current plan or repaired current-branch "
            "claims, including current-claim disagreement, and therefore blocks "
            "startup recovery pending explicit operator action."
        ),
    ),
    ContinuityArtifactClassificationRule(
        classification="corrupt_blocking",
        blocks_startup_recovery=True,
        quarantine_allowed=False,
        definition=(
            "Artifact contents are corrupt or unparsable, so startup recovery "
            "must block rather than infer or repair identity automatically."
        ),
    ),
    ContinuityArtifactClassificationRule(
        classification="ambiguous_blocking",
        blocks_startup_recovery=True,
        quarantine_allowed=False,
        definition=(
            "Artifact identity is ambiguous, including renamed or migrated step "
            "ID suspicion, and startup recovery must block without auto-remap."
        ),
    ),
)


@dataclass(frozen=True)
class ContinuityArtifactRecord:
    """Normalized continuity artifact identity provided to hygiene classification."""

    artifact_kind: ContinuityArtifactKind
    original_path: str
    parsed_step_id: str | None
    identity_evidence: str | None = None


@dataclass(frozen=True)
class QuarantineManifestEntry:
    """Manifest row for a quarantined continuity artifact.

    Required preservation fields:
    - ``original_path``
    - ``reason``
    - ``classification``
    - ``quarantine_destination``
    - ``audit_timestamp``
    """

    artifact_kind: ContinuityArtifactKind
    original_path: str
    reason: str
    classification: ContinuityArtifactClassification
    quarantine_destination: str
    audit_timestamp: str
    step_id: str | None = None


@dataclass(frozen=True)
class ContinuityArtifactAssessment:
    """Single artifact classification result from startup hygiene."""

    artifact: ContinuityArtifactRecord
    classification: ContinuityArtifactClassification
    reason: str
    quarantine_destination: str | None
    safe_stale_rule_satisfied: bool
    blocks_startup_recovery: bool
    quarantine_manifest_entry: QuarantineManifestEntry | None = None


@dataclass(frozen=True)
class StartupHygieneStageInput:
    """Inputs for the pre-recovery startup hygiene stage."""

    current_branch: str
    current_plan_step_ids: tuple[str, ...]
    repaired_current_branch_claim_step_ids: tuple[str, ...]
    artifacts: tuple[ContinuityArtifactRecord, ...]
    quarantine_root: str


@dataclass(frozen=True)
class StartupHygieneStageResult:
    """Deterministic output of the pre-recovery startup hygiene stage."""

    current_branch: str
    assessments: tuple[ContinuityArtifactAssessment, ...]
    quarantine_manifest: tuple[QuarantineManifestEntry, ...]
    blocked_assessments: tuple[ContinuityArtifactAssessment, ...]
    safe_stale_rule: str = SAFE_STALE_QUARANTINE_RULE
    guardrails: tuple[str, ...] = STARTUP_HYGIENE_GUARDRAILS


@dataclass(frozen=True)
class ArtifactClassificationResult:
    """Standalone artifact classification used by helper-level tests."""

    classification: ContinuityArtifactClassification
    reason: str
    blocking_repair_actions: tuple[str, ...]


@dataclass(frozen=True)
class ArtifactQuarantineResult:
    """Standalone quarantine operation result used by helper-level tests."""

    manifest_entry: QuarantineManifestEntry
    original_preserved: bool


class StartupHygieneStage(Protocol):
    """Pre-recovery hygiene boundary consumed before active startup recovery."""

    def evaluate(
        self,
        *,
        stage_input: StartupHygieneStageInput,
    ) -> StartupHygieneStageResult:
        """Classify continuity artifacts without deleting or remapping them."""
        ...


class _HygieneArtifactLike(Protocol):
    """Protocol for test helper artifacts consumed by public helper APIs."""

    artifact_type: str
    step_id: str
    source_path: Path
    content: dict[str, object]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def is_ambiguous_migration(step_id: str) -> bool:
    """Return True when step identity suggests migration/rename ambiguity.

    This is read-only detection. It does not remap IDs.
    """

    lowered = step_id.lower()
    ambiguous_markers = ("migrated", "duplicate", "renamed", "legacy_alias")
    return any(marker in lowered for marker in ambiguous_markers)


def _quarantine_destination_for(
    *,
    quarantine_root: Path,
    artifact_kind: ContinuityArtifactKind,
    source_path: Path,
) -> Path:
    destination_dir = quarantine_root / artifact_kind
    destination_dir.mkdir(parents=True, exist_ok=True)
    return destination_dir / source_path.name


def _classify_record(
    *,
    artifact: ContinuityArtifactRecord,
    plan_step_ids: set[str],
    claim_step_ids: set[str],
    quarantine_root: Path,
) -> ContinuityArtifactAssessment:
    source_path = Path(artifact.original_path)

    if artifact.parsed_step_id is None or not artifact.parsed_step_id.strip():
        return ContinuityArtifactAssessment(
            artifact=artifact,
            classification="corrupt_blocking",
            reason=(
                "corrupt_blocking: missing_or_unparsable_step_id"
                + (f" ({artifact.identity_evidence})" if artifact.identity_evidence else "")
            ),
            quarantine_destination=None,
            safe_stale_rule_satisfied=False,
            blocks_startup_recovery=True,
        )

    step_id = artifact.parsed_step_id
    if is_ambiguous_migration(step_id):
        return ContinuityArtifactAssessment(
            artifact=artifact,
            classification="ambiguous_blocking",
            reason="ambiguous_blocking: migration_identity_ambiguous",
            quarantine_destination=None,
            safe_stale_rule_satisfied=False,
            blocks_startup_recovery=True,
        )

    if step_id in plan_step_ids:
        return ContinuityArtifactAssessment(
            artifact=artifact,
            classification="blocking_divergence",
            reason="blocking_divergence: current_plan_step",
            quarantine_destination=None,
            safe_stale_rule_satisfied=False,
            blocks_startup_recovery=True,
        )

    if step_id in claim_step_ids:
        return ContinuityArtifactAssessment(
            artifact=artifact,
            classification="blocking_divergence",
            reason="blocking_divergence: current_claimed_step",
            quarantine_destination=None,
            safe_stale_rule_satisfied=False,
            blocks_startup_recovery=True,
        )

    destination_path = _quarantine_destination_for(
        quarantine_root=quarantine_root,
        artifact_kind=artifact.artifact_kind,
        source_path=source_path,
    )
    return ContinuityArtifactAssessment(
        artifact=artifact,
        classification="safe_stale_quarantine",
        reason="safe_stale_quarantine: absent_from_plan_and_repaired_claims",
        quarantine_destination=str(destination_path),
        safe_stale_rule_satisfied=True,
        blocks_startup_recovery=False,
    )


def run_startup_hygiene_stage(
    *,
    stage_input: StartupHygieneStageInput,
) -> StartupHygieneStageResult:
    """Classify continuity artifacts for startup hygiene decisions."""

    plan_step_ids = set(stage_input.current_plan_step_ids)
    claim_step_ids = set(stage_input.repaired_current_branch_claim_step_ids)
    quarantine_root = Path(stage_input.quarantine_root)

    assessments = tuple(
        _classify_record(
            artifact=artifact,
            plan_step_ids=plan_step_ids,
            claim_step_ids=claim_step_ids,
            quarantine_root=quarantine_root,
        )
        for artifact in stage_input.artifacts
    )
    blocked_assessments = tuple(a for a in assessments if a.blocks_startup_recovery)

    return StartupHygieneStageResult(
        current_branch=stage_input.current_branch,
        assessments=assessments,
        quarantine_manifest=(),
        blocked_assessments=blocked_assessments,
    )


def scan_continuity_artifacts(
    repo_root: Path,
) -> tuple[tuple[ContinuityArtifactRecord, ...], tuple[str, ...]]:
    """Scan continuity ledger/journal artifacts from disk.

    Returns records and corrupt artifact source paths.
    Missing continuity directories return empty tuples.
    """

    records: list[ContinuityArtifactRecord] = []
    corrupt_paths: list[str] = []

    ledger_dir = repo_root / ".vectl" / "continuity" / "ledger"
    if ledger_dir.exists() and ledger_dir.is_dir():
        for path in sorted(ledger_dir.glob("*.json")):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                record = ContinuityArtifactRecord(
                    artifact_kind="ledger",
                    original_path=str(path),
                    parsed_step_id=None,
                    identity_evidence="json_parse_error",
                )
                records.append(record)
                corrupt_paths.append(str(path))
                continue
            step_id_raw = payload.get("step_id") if isinstance(payload, dict) else None
            step_id = step_id_raw if isinstance(step_id_raw, str) and step_id_raw else None
            if step_id is None:
                corrupt_paths.append(str(path))
            records.append(
                ContinuityArtifactRecord(
                    artifact_kind="ledger",
                    original_path=str(path),
                    parsed_step_id=step_id,
                    identity_evidence=None if step_id is not None else "missing_step_id",
                )
            )

    journal_dir = repo_root / ".vectl" / "continuity" / "journal"
    if journal_dir.exists() and journal_dir.is_dir():
        for path in sorted(journal_dir.glob("*.jsonl")):
            raw = path.read_text(encoding="utf-8")
            lines = [line for line in raw.splitlines() if line.strip()]
            if not lines:
                records.append(
                    ContinuityArtifactRecord(
                        artifact_kind="journal",
                        original_path=str(path),
                        parsed_step_id=None,
                        identity_evidence="empty_jsonl",
                    )
                )
                corrupt_paths.append(str(path))
                continue

            parsed_last: dict[str, object] | None = None
            parse_failed = False
            for line in lines:
                try:
                    loaded = json.loads(line)
                except Exception:
                    parse_failed = True
                    break
                if not isinstance(loaded, dict):
                    parse_failed = True
                    break
                parsed_last = loaded

            if parse_failed or parsed_last is None:
                records.append(
                    ContinuityArtifactRecord(
                        artifact_kind="journal",
                        original_path=str(path),
                        parsed_step_id=None,
                        identity_evidence="jsonl_parse_error",
                    )
                )
                corrupt_paths.append(str(path))
                continue

            step_id_raw = parsed_last.get("step_id")
            step_id = step_id_raw if isinstance(step_id_raw, str) and step_id_raw else None
            if step_id is None:
                corrupt_paths.append(str(path))
            records.append(
                ContinuityArtifactRecord(
                    artifact_kind="journal",
                    original_path=str(path),
                    parsed_step_id=step_id,
                    identity_evidence=None if step_id is not None else "missing_step_id",
                )
            )

    return tuple(records), tuple(corrupt_paths)


def _manifest_path(quarantine_root: Path) -> Path:
    return quarantine_root / QUARANTINE_MANIFEST_FILE


def _append_manifest_entry(*, quarantine_root: Path, entry: QuarantineManifestEntry) -> None:
    quarantine_root.mkdir(parents=True, exist_ok=True)
    manifest_path = _manifest_path(quarantine_root)
    with manifest_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry.__dict__, ensure_ascii=False) + "\n")


def load_quarantine_manifest(quarantine_root: Path) -> tuple[QuarantineManifestEntry, ...]:
    """Load quarantine manifest in last-writer-wins order by source path."""

    manifest_path = _manifest_path(quarantine_root)
    if not manifest_path.exists() or not manifest_path.is_file():
        return ()

    latest_by_source: dict[str, QuarantineManifestEntry] = {}
    with manifest_path.open("r", encoding="utf-8") as handle:
        for raw_line in handle:
            line = raw_line.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except Exception:
                continue
            if not isinstance(payload, dict):
                continue
            source_path = payload.get("original_path")
            if not isinstance(source_path, str) or not source_path:
                continue
            try:
                entry = QuarantineManifestEntry(
                    artifact_kind=payload["artifact_kind"],
                    original_path=source_path,
                    reason=payload["reason"],
                    classification=payload["classification"],
                    quarantine_destination=payload["quarantine_destination"],
                    audit_timestamp=payload["audit_timestamp"],
                    step_id=payload.get("step_id"),
                )
            except Exception:
                continue
            latest_by_source[source_path] = entry
    return tuple(sorted(latest_by_source.values(), key=lambda entry: entry.original_path))


def apply_quarantine(
    *,
    stage_result: StartupHygieneStageResult,
    quarantine_root: Path,
) -> StartupHygieneStageResult:
    """Copy safe-stale artifacts into quarantine and append manifest records."""

    if not stage_result.assessments:
        return stage_result

    manifest_entries: list[QuarantineManifestEntry] = list(stage_result.quarantine_manifest)
    updated_assessments: list[ContinuityArtifactAssessment] = []

    for assessment in stage_result.assessments:
        if (
            assessment.classification != "safe_stale_quarantine"
            or assessment.quarantine_destination is None
        ):
            updated_assessments.append(assessment)
            continue

        source_path = Path(assessment.artifact.original_path)
        destination_path = Path(assessment.quarantine_destination)
        destination_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_path, destination_path)
        manifest_entry = QuarantineManifestEntry(
            artifact_kind=assessment.artifact.artifact_kind,
            original_path=str(source_path),
            reason=assessment.reason,
            classification=assessment.classification,
            quarantine_destination=str(destination_path),
            audit_timestamp=_now_iso(),
            step_id=assessment.artifact.parsed_step_id,
        )
        _append_manifest_entry(quarantine_root=quarantine_root, entry=manifest_entry)
        manifest_entries.append(manifest_entry)
        updated_assessments.append(
            ContinuityArtifactAssessment(
                artifact=assessment.artifact,
                classification=assessment.classification,
                reason=assessment.reason,
                quarantine_destination=assessment.quarantine_destination,
                safe_stale_rule_satisfied=True,
                blocks_startup_recovery=False,
                quarantine_manifest_entry=manifest_entry,
            )
        )

    return StartupHygieneStageResult(
        current_branch=stage_result.current_branch,
        assessments=tuple(updated_assessments),
        quarantine_manifest=tuple(manifest_entries),
        blocked_assessments=stage_result.blocked_assessments,
        safe_stale_rule=stage_result.safe_stale_rule,
        guardrails=stage_result.guardrails,
    )


def classify_artifact(
    *,
    artifact: _HygieneArtifactLike,
    plan_step_ids: set[str],
    claims_step_ids: set[str],
    corrupt_files: set[str] | None = None,
) -> ArtifactClassificationResult:
    """Classify one helper artifact for unit-level tests.

    Precedence: corrupt > ambiguous > plan/claims divergence > safe stale.
    """

    corrupt = corrupt_files or set()
    source_key = artifact.source_path.name
    if artifact.step_id in corrupt or source_key in corrupt:
        return ArtifactClassificationResult(
            classification="corrupt_blocking",
            reason="corrupt_blocking: explicitly_marked_corrupt",
            blocking_repair_actions=("halt_startup_and_repair_corrupt_artifact",),
        )

    if is_ambiguous_migration(artifact.step_id):
        return ArtifactClassificationResult(
            classification="ambiguous_blocking",
            reason="ambiguous_blocking: migration_identity_ambiguous",
            blocking_repair_actions=("halt_startup_and_require_manual_identity_resolution",),
        )

    if artifact.step_id in plan_step_ids:
        return ArtifactClassificationResult(
            classification="blocking_divergence",
            reason="blocking_divergence: current_plan_step",
            blocking_repair_actions=("halt_startup_and_reconcile_plan_claims_ledger",),
        )

    if artifact.step_id in claims_step_ids:
        return ArtifactClassificationResult(
            classification="blocking_divergence",
            reason="blocking_divergence: current_claimed_step",
            blocking_repair_actions=("halt_startup_until_claim_repair_completed",),
        )

    return ArtifactClassificationResult(
        classification="safe_stale_quarantine",
        reason="safe_stale_quarantine: absent_from_plan_and_repaired_claims",
        blocking_repair_actions=(),
    )


def _ensure_source_artifact_bytes(artifact: _HygieneArtifactLike) -> None:
    """Materialize source bytes when helper artifacts were in-memory only."""

    source_path = artifact.source_path
    source_path.parent.mkdir(parents=True, exist_ok=True)
    if source_path.exists():
        return

    if artifact.artifact_type == "journal":
        source_path.write_text(
            json.dumps(artifact.content, ensure_ascii=False) + "\n", encoding="utf-8"
        )
    else:
        source_path.write_text(json.dumps(artifact.content, ensure_ascii=False), encoding="utf-8")


def quarantine_artifact(
    *,
    artifact: _HygieneArtifactLike,
    reason: str,
    quarantine_dir: Path,
) -> ArtifactQuarantineResult:
    """Copy one artifact into quarantine and append manifest entry."""

    _ensure_source_artifact_bytes(artifact)
    destination_path = _quarantine_destination_for(
        quarantine_root=quarantine_dir,
        artifact_kind="journal" if artifact.artifact_type == "journal" else "ledger",
        source_path=artifact.source_path,
    )
    shutil.copy2(artifact.source_path, destination_path)

    classification: ContinuityArtifactClassification = "safe_stale_quarantine"
    manifest_entry = QuarantineManifestEntry(
        artifact_kind="journal" if artifact.artifact_type == "journal" else "ledger",
        original_path=str(artifact.source_path),
        reason=reason,
        classification=classification,
        quarantine_destination=str(destination_path),
        audit_timestamp=_now_iso(),
        step_id=artifact.step_id,
    )
    _append_manifest_entry(quarantine_root=quarantine_dir, entry=manifest_entry)
    return ArtifactQuarantineResult(manifest_entry=manifest_entry, original_preserved=True)


def get_quarantine_manifest(quarantine_dir: Path) -> tuple[QuarantineManifestEntry, ...]:
    """Return manifest entries using last-writer-wins for duplicate sources."""

    return load_quarantine_manifest(quarantine_dir)
