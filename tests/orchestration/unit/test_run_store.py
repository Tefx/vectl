from __future__ import annotations

import json
import math
from datetime import datetime, timedelta, timezone

from hypothesis import HealthCheck, given, settings, strategies as st
import pytest

from vectl.orchestration.run_store import (
    AppendConflictError,
    CaseIndexEntry,
    CorruptJSONLError,
    RunRecord,
    RunRegistry,
    SamePlanAdmissionError,
    generate_run_id,
    latest_run,
)


def _is_malformed_float_text(value: str) -> bool:
    if value.strip() == "":
        return False
    try:
        return not math.isfinite(float(value))
    except ValueError:
        return True


def test_run_id_is_sortable_ulid_like() -> None:
    t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    t1 = t0 + timedelta(milliseconds=1)
    first = generate_run_id(now=t0)
    second = generate_run_id(now=t1)
    assert len(first) == 26
    assert len(second) == 26
    assert first < second


def test_append_and_read_latest_for_step(tmp_path) -> None:
    registry = RunRegistry(store_root=tmp_path)
    registry.save(
        RunRecord(
            run_id="01AAA000000000000000000001",
            step_id="alpha.step",
            plan_path="plan.yaml",
            status="running",
            started_at=10.0,
            updated_at=10.0,
        )
    )
    registry.save(
        RunRecord(
            run_id="01AAA000000000000000000002",
            step_id="alpha.step",
            plan_path="plan.yaml",
            status="success",
            started_at=9.0,
            updated_at=9.0,
        )
    )

    latest = registry.latest_for_step("alpha.step")
    assert latest is not None
    assert latest.run_id == "01AAA000000000000000000001"


def test_latest_selection_tie_breaks_by_run_id(tmp_path) -> None:
    registry = RunRegistry(store_root=tmp_path)
    registry.save(
        RunRecord(
            run_id="01AAA000000000000000000009",
            step_id="beta.step",
            status="running",
            updated_at=20.0,
        )
    )
    registry.save(
        RunRecord(
            run_id="01AAA00000000000000000000A",
            step_id="beta.step",
            status="running",
            updated_at=20.0,
        )
    )

    chosen = latest_run("beta.step", registry=registry)
    assert chosen is not None
    assert chosen.run_id == "01AAA00000000000000000000A"


def test_malformed_trailing_jsonl_surfaces_corruption(tmp_path) -> None:
    index = tmp_path / "index.jsonl"
    index.write_text('{"run_id":"01","step_id":"s","status":"running","updated_at":1}\n{"bad"\n')
    registry = RunRegistry(store_root=tmp_path)

    with pytest.raises(CorruptJSONLError):
        registry.latest_for_step("s")


def test_prune_run_tombstones_case_index_entries(tmp_path) -> None:
    registry = RunRegistry(store_root=tmp_path)
    registry.append_case(
        CaseIndexEntry(
            case_id="case-01",
            run_id="01RUN",
            status="open",
            updated_at=1.0,
            case_path="cases/case-01.json",
        )
    )
    removed = registry.prune_run("01RUN")

    assert removed == 1
    assert registry.cases_for_run("01RUN") == ()


def test_missing_heartbeat_classifies_as_unknown(tmp_path) -> None:
    registry = RunRegistry(store_root=tmp_path)
    assert registry.liveness_for_run("01MISSING") == "unknown"


def test_same_plan_admission_rejects_conflicting_active_runs(tmp_path) -> None:
    registry = RunRegistry(store_root=tmp_path)
    registry.save(
        RunRecord(
            run_id="01ACTIVE",
            step_id="s",
            plan_path="plan.yaml",
            status="running",
            updated_at=5.0,
        )
    )

    with pytest.raises(SamePlanAdmissionError):
        registry.assert_can_admit_same_plan("plan.yaml", current_run_id="01NEW")


def test_append_conflict_retry_is_bounded_and_observable(tmp_path, monkeypatch) -> None:
    observed_attempts: list[int] = []
    failures = {"count": 0}

    from vectl.orchestration import run_store as run_store_module

    original_append_once = run_store_module._append_jsonl_once

    def flaky_append(path, payload):
        if failures["count"] < 1:
            failures["count"] += 1
            raise BlockingIOError("simulated contention")
        return original_append_once(path, payload)

    monkeypatch.setattr(run_store_module, "_append_jsonl_once", flaky_append)

    registry = RunRegistry(
        store_root=tmp_path,
        max_append_retries=2,
        append_retry_delay_seconds=0,
        append_retry_observer=lambda _path, attempt, _exc: observed_attempts.append(attempt),
    )
    registry.save(RunRecord(run_id="01R", step_id="s", status="running", updated_at=1.0))
    assert observed_attempts == [1]


def test_append_conflict_raises_after_retry_budget(tmp_path, monkeypatch) -> None:
    from vectl.orchestration import run_store as run_store_module

    def always_fail(_path, _payload):
        raise BlockingIOError("always blocked")

    monkeypatch.setattr(run_store_module, "_append_jsonl_once", always_fail)

    registry = RunRegistry(
        store_root=tmp_path,
        max_append_retries=1,
        append_retry_delay_seconds=0,
    )
    with pytest.raises(AppendConflictError) as exc_info:
        registry.save(RunRecord(run_id="01R", step_id="s", status="running", updated_at=1.0))
    assert exc_info.value.attempts == 2


def test_unimported_legacy_artifacts_do_not_surface_as_native_runs(tmp_path) -> None:
    legacy_path = tmp_path / "legacy" / "run-01.json"
    legacy_path.parent.mkdir(parents=True, exist_ok=True)
    legacy_path.write_text('{"legacy_run_id":"legacy-01","step_id":"core.ready"}', encoding="utf-8")

    registry = RunRegistry(store_root=tmp_path)

    assert registry.by_id("legacy-01") is None
    assert registry.latest_for_step("core.ready") is None
    assert registry.imported_legacy_runs() == ()


def test_imported_legacy_run_is_visible_and_blocks_same_plan_when_active(tmp_path) -> None:
    registry = RunRegistry(store_root=tmp_path)

    imported = registry.import_legacy_run(
        legacy_run_id="legacy-01",
        step_id="core.ready",
        plan_path="plan.yaml",
        status="running",
        migration_state="parallel",
        continuity_artifacts={
            "ledger": {
                "step_id": "core.ready",
                "session_id": "sess-1",
                "runner": "python",
                "status": "active",
            },
            "journal": {
                "event_id": "evt-1",
                "step_id": "core.ready",
                "session_id": "sess-1",
                "runner": "python",
                "event_type": "resume_attempt",
            },
        },
    )

    assert imported.source == "legacy_imported"
    assert imported.legacy_run_id == "legacy-01"
    assert imported.legacy_migration_state == "parallel"
    assert imported.continuity_blocker is None
    assert registry.by_id(imported.run_id) is not None
    with pytest.raises(SamePlanAdmissionError):
        registry.assert_can_admit_same_plan("plan.yaml")


def test_import_missing_continuity_minimums_creates_case_blocker(tmp_path) -> None:
    registry = RunRegistry(store_root=tmp_path)

    imported = registry.import_legacy_run(
        legacy_run_id="legacy-02",
        step_id="core.ready",
        plan_path="plan.yaml",
        status="running",
        migration_state="preferred",
        continuity_artifacts={
            "ledger": {
                "step_id": "core.ready",
                "session_id": "sess-2",
                "runner": "python",
                "status": "active",
            }
        },
    )

    assert imported.status == "stall"
    assert imported.continuity_blocker is not None
    assert "journal entry is required" in imported.continuity_blocker
    case_entries = registry.cases_for_run(imported.run_id)
    assert len(case_entries) == 1
    assert case_entries[0].status == "open"


def test_retired_imported_legacy_run_does_not_block_same_plan_admission(tmp_path) -> None:
    registry = RunRegistry(store_root=tmp_path)

    registry.import_legacy_run(
        legacy_run_id="legacy-retired",
        step_id="core.ready",
        plan_path="plan.yaml",
        status="running",
        migration_state="retired",
        continuity_artifacts={
            "ledger": {
                "step_id": "core.ready",
                "session_id": "sess-3",
                "runner": "python",
                "status": "completed",
            },
            "journal": {
                "event_id": "evt-3",
                "step_id": "core.ready",
                "session_id": "sess-3",
                "runner": "python",
                "event_type": "cutover",
            },
        },
    )

    registry.assert_can_admit_same_plan("plan.yaml")


def test_public_latest_queries_are_authoritative_and_sorted(tmp_path) -> None:
    registry = RunRegistry(store_root=tmp_path)
    registry.save(
        RunRecord(
            run_id="01RUNA",
            step_id="core.ready",
            plan_path="plan.yaml",
            status="running",
            updated_at=1.0,
        )
    )
    registry.save(
        RunRecord(
            run_id="01RUNB",
            step_id="core.other",
            plan_path="plan.yaml",
            status="pending",
            updated_at=2.0,
        )
    )

    records = registry.latest_records()
    assert [record.run_id for record in records] == ["01RUNB", "01RUNA"]


def test_case_lookup_surfaces_latest_entry_and_filters_removed(tmp_path) -> None:
    registry = RunRegistry(store_root=tmp_path)
    registry.append_case(
        CaseIndexEntry(
            case_id="case-1",
            run_id="01RUN",
            status="open",
            updated_at=1.0,
            case_path="cases/case-1.json",
        )
    )
    registry.append_case(
        CaseIndexEntry(
            case_id="case-1",
            run_id="01RUN",
            status="removed",
            updated_at=2.0,
            case_path="cases/case-1.json",
        )
    )

    assert registry.case_by_id("case-1") is not None
    assert registry.latest_cases(include_removed=False) == ()


def test_admit_for_start_persists_pending_and_rejects_conflict(tmp_path) -> None:
    registry = RunRegistry(store_root=tmp_path)
    registry.save(
        RunRecord(
            run_id="01ACTIVE",
            step_id="core.ready",
            plan_path="plan.yaml",
            status="running",
            updated_at=5.0,
        )
    )

    with pytest.raises(SamePlanAdmissionError):
        registry.admit_for_start(
            run_id="01NEW",
            step_id="core.other",
            plan_path="plan.yaml",
            agent="python-executor",
            artifact_root=str(tmp_path / "runs" / "01NEW"),
            output_summary="pending admission",
        )


@given(
    malformed_number=st.text(min_size=1).filter(_is_malformed_float_text)
)
@settings(suppress_health_check=[HealthCheck.function_scoped_fixture])
def test_malformed_numeric_run_payload_surfaces_corruption(tmp_path, malformed_number: str) -> None:
    index = tmp_path / "index.jsonl"
    index.write_text(
        '{"run_id":"01BAD","step_id":"s","status":"running",'
        f'"updated_at":{json.dumps(malformed_number)}}}\n',
        encoding="utf-8",
    )
    registry = RunRegistry(store_root=tmp_path)

    with pytest.raises(CorruptJSONLError):
        registry.latest_for_step("s")
