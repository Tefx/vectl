"""Tests for claims.json IO and locking behavior."""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from vectl.claims import (
    ClaimEntry,
    acquire_claim,
    cleanup_stale_claims,
    load_claims,
    release_claim,
    resolve_claims_path,
    save_claims,
)
from vectl.models import PlanError


def test_acquire_release_claim(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    claims_path = tmp_path / "claims.json"

    assert acquire_claim("core.step", "feature/test", "agent-a", claims_path) is True
    claims = load_claims(claims_path)
    assert "feature/test:core.step" in claims
    assert claims["feature/test:core.step"].agent == "agent-a"

    with pytest.raises(PlanError, match="already claimed"):
        acquire_claim("core.step", "feature/test", "agent-b", claims_path)

    assert release_claim("core.step", "feature/test", claims_path) is True
    assert release_claim("core.step", "feature/test", claims_path) is False
    assert load_claims(claims_path) == {}

    save_claims(
        {
            "feature/test:core.step": ClaimEntry(
                step_id="core.step",
                branch="feature/test",
                agent="agent-a",
                claimed_at="2026-03-08T00:00:00Z",
            )
        },
        claims_path,
    )
    before = claims_path.read_text(encoding="utf-8")

    def _boom_replace(src: str, dst: str) -> None:
        raise OSError("replace failed")

    monkeypatch.setattr("vectl.claims.os.replace", _boom_replace)
    with pytest.raises(OSError, match="replace failed"):
        save_claims({}, claims_path)
    assert claims_path.read_text(encoding="utf-8") == before


def test_stale_claim_cleanup(tmp_path: Path) -> None:
    claims_path = tmp_path / "claims.json"
    save_claims(
        {
            "feature/x:core.step": ClaimEntry(
                step_id="core.step",
                branch="feature/x",
                agent="agent-old",
                claimed_at="2020-01-01T00:00:00Z",
            )
        },
        claims_path,
    )

    removed = cleanup_stale_claims(claims_path, ttl_hours=2.0)
    assert removed == 1
    assert load_claims(claims_path) == {}

    assert acquire_claim("core.step", "feature/x", "agent-new", claims_path) is True
    claims = load_claims(claims_path)
    assert claims["feature/x:core.step"].agent == "agent-new"


def test_concurrent_claims(tmp_path: Path) -> None:
    claims_path = tmp_path / "claims.json"
    start = threading.Barrier(3)

    def _claim_same_branch(agent: str) -> bool:
        start.wait()
        return acquire_claim("core.step", "feature/shared", agent, claims_path)

    with ThreadPoolExecutor(max_workers=2) as executor:
        future_a = executor.submit(_claim_same_branch, "agent-a")
        future_b = executor.submit(_claim_same_branch, "agent-b")
        start.wait()

    results = []
    failures = 0
    for future in (future_a, future_b):
        try:
            results.append(future.result())
        except PlanError:
            failures += 1

    assert results == [True]
    assert failures == 1

    branch_start = threading.Barrier(3)

    def _claim_other_branch(branch: str) -> bool:
        branch_start.wait()
        return acquire_claim("core.step", branch, f"agent-{branch}", claims_path)

    with ThreadPoolExecutor(max_workers=2) as executor:
        future_x = executor.submit(_claim_other_branch, "feature/x")
        future_y = executor.submit(_claim_other_branch, "feature/y")
        branch_start.wait()

    assert future_x.result() is True
    assert future_y.result() is True
    claims = load_claims(claims_path)
    assert "feature/x:core.step" in claims
    assert "feature/y:core.step" in claims


def test_missing_claims_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    claims_path = tmp_path / "missing" / "claims.json"
    assert load_claims(claims_path) == {}
    assert release_claim("core.step", "feature/test", claims_path) is False

    plan_path = tmp_path / "plan.yaml"
    plan_path.write_text("project: test\n", encoding="utf-8")

    def _boom_run(*args: object, **kwargs: object) -> object:
        raise OSError("git unavailable")

    monkeypatch.setattr("vectl.claims.subprocess.run", _boom_run)
    resolved = resolve_claims_path(plan_path)
    assert resolved == plan_path.parent / ".vectl" / "claims.json"


def test_branch_isolation(tmp_path: Path) -> None:
    claims_path = tmp_path / "claims.json"

    assert acquire_claim("core.step", "feature/a", "agent-a", claims_path) is True
    assert acquire_claim("core.step", "feature/b", "agent-b", claims_path) is True

    claims = load_claims(claims_path)
    assert set(claims.keys()) == {"feature/a:core.step", "feature/b:core.step"}

    assert release_claim("core.step", "feature/a", claims_path) is True
    claims = load_claims(claims_path)
    assert "feature/a:core.step" not in claims
    assert "feature/b:core.step" in claims


def test_claims_use_sidecar_lock_file(tmp_path: Path) -> None:
    claims_path = tmp_path / "claims.json"
    lock_path = claims_path.parent / f"{claims_path.name}.lock"
    assert lock_path.exists() is False

    assert acquire_claim("core.step", "feature/test", "agent-a", claims_path) is True

    assert lock_path.exists() is True
    assert release_claim("core.step", "feature/test", claims_path) is True
