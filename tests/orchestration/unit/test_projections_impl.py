"""Projection replay implementation tests for derived state artifacts."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from vectl.orchestration.projections import (
    PROJECTION_STALE,
    FileProjectionReplay,
    ProjectionStaleError,
    replay_events_to_artifacts,
)


def _load_replay_fixture() -> tuple[dict[str, object], ...]:
    fixture_path = (
        Path(__file__).resolve().parents[2] / "fixtures" / "orchestration" / "replay_events.json"
    )
    payload = json.loads(fixture_path.read_text(encoding="utf-8"))
    assert isinstance(payload, list)
    return tuple(payload)


def test_replay_derives_latest_summary_and_metrics(tmp_path: Path) -> None:
    """Replay writes latest/summary/metrics and populates expected schema keys."""
    events = _load_replay_fixture()

    replay = FileProjectionReplay(events=events, artifact_root=tmp_path)
    (latest,) = replay.replay()

    latest_path = tmp_path / "state" / "latest.json"
    summary_path = tmp_path / "state" / "summary.json"
    metrics_path = tmp_path / "state" / "metrics.json"

    latest_payload = json.loads(latest_path.read_text(encoding="utf-8"))
    summary_payload = json.loads(summary_path.read_text(encoding="utf-8"))
    metrics_payload = json.loads(metrics_path.read_text(encoding="utf-8"))

    assert latest_payload["run_id"] == "run-42"
    assert latest_payload["last_event_seq"] == 5
    assert summary_payload["active_step_id"] == "orch.step"
    assert summary_payload["open_case_count"] == 1
    assert metrics_payload["dispatch_count"] == 1
    assert metrics_payload["resolution_count"] == 1
    assert metrics_payload["operator_required_case_count"] == 1
    assert metrics_payload["total_resolver_tokens"] == 120
    assert latest.run_id == "run-42"


def test_replay_exposes_step_and_case_artifact_hooks(tmp_path: Path) -> None:
    """ReplayResult contains step/case artifact metadata for inspection surfaces."""
    result = replay_events_to_artifacts(
        events=_load_replay_fixture(),
        artifact_root=tmp_path,
    )

    step_artifacts = dict(result.step_artifacts)
    case_artifacts = dict(result.case_artifacts)

    assert "orch.step" in step_artifacts
    assert step_artifacts["orch.step"][0].artifact_ref == "artifact-step-1"
    assert "case-007" in case_artifacts
    assert case_artifacts["case-007"][0].artifact_ref == "artifact-case-1"


def test_projection_persistence_failure_surfaces_projection_stale(tmp_path: Path) -> None:
    """Persistence failures raise ProjectionStaleError with PROJECTION_STALE code."""
    state_path = tmp_path / "state"
    state_path.write_text("blocking-file", encoding="utf-8")

    replay = FileProjectionReplay(events=_load_replay_fixture(), artifact_root=tmp_path)

    with pytest.raises(ProjectionStaleError) as error:
        replay.replay()

    assert error.value.code == PROJECTION_STALE
    assert error.value.artifact_path is not None
