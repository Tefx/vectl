"""Black-box checks for retired `vectl repair continuity` surface.

This suite intentionally verifies:
1) legacy continuity command is not advertised
2) direct legacy invocation fails explicitly
3) supported recovery surface (`vectl orch recover`) works in dry-run JSON mode
"""

from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path

import yaml


def _run(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, cwd=str(cwd), capture_output=True, text=True, timeout=30)


def _make_minimal_plan(repo_root: Path) -> Path:
    plan = {
        "version": 1,
        "project": "recovery-retirement-check",
        "strategy_ref": "#",
        "context": "black-box retirement verification",
        "phases": [
            {
                "id": "core",
                "name": "Core",
                "status": "pending",
                "gate": "n/a",
                "steps": [
                    {
                        "id": "core.ready",
                        "name": "Core ready",
                        "status": "pending",
                        "description": "fixture step",
                        "agent": "general",
                    }
                ],
            }
        ],
    }
    plan_path = repo_root / "plan.yaml"
    plan_path.write_text(yaml.dump(plan, sort_keys=False), encoding="utf-8")
    return plan_path


def test_repair_help_no_longer_advertises_continuity() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    result = _run(["uv", "run", "vectl", "repair", "--help"], cwd=repo_root)

    assert result.returncode == 0, result.stderr
    assert "claims" in result.stdout
    assert "continuity" not in result.stdout.lower()


def test_repair_continuity_command_is_removed() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    result = _run(["uv", "run", "vectl", "repair", "continuity", "--help"], cwd=repo_root)

    assert result.returncode != 0
    msg = f"{result.stdout}\n{result.stderr}".lower()
    assert "no such command 'continuity'" in msg


def test_orch_recover_dry_run_json_is_supported_surface() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        plan_path = _make_minimal_plan(tmp)

        result = _run(
            [
                "uv",
                "run",
                "vectl",
                "orch",
                "recover",
                "test-run-nonexistent-001",
                "--step",
                "core.ready",
                "--dry-run",
                "--json",
                "--plan",
                str(plan_path),
            ],
            cwd=tmp,
        )

        assert result.returncode == 0, result.stderr
        payload = json.loads(result.stdout)
        report = payload.get("recovery_report", {})
        assert report.get("outcome") == "no_artifacts"
        assert report.get("gate_open_allowed") is True
