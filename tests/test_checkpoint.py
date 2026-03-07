"""Tests for checkpoint generation (CLI and MCP)."""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from typer.testing import CliRunner

from vectl.cli import app
from vectl.io import load_plan, save_plan
from vectl.models import Clipboard, Phase, Plan, Step, StepStatus
from vectl.plan_path import resolve_state_path

runner = CliRunner()


@pytest.fixture
def checkpoint_plan(tmp_path: Path) -> Path:
    plan = Plan(
        project="chk",
        phases=[
            Phase(
                id="p1",
                name="P1",
                steps=[
                    Step(id="p1.1", name="S1", status=StepStatus.CLAIMED, claimed_by="alice"),
                    Step(id="p1.2", name="S2", status=StepStatus.CLAIMED, claimed_by="bob"),
                    Step(id="p1.3", name="S3", status=StepStatus.PENDING, depends_on=["p1.1"]),
                ],
            )
        ],
    )
    path = tmp_path / "plan.yaml"
    save_plan(plan, path)
    return path


class TestCheckpointCore:
    def test_schema_structure(self, checkpoint_plan: Path) -> None:
        # Default is lite mode (True) -> no metadata
        result = runner.invoke(app, ["checkpoint", "--plan", str(checkpoint_plan)])
        assert result.exit_code == 0
        data = json.loads(result.stdout)

        assert data["schema"] == "vectl.checkpoint/v1"
        assert "metadata" not in data  # default lite=True

        # Test full mode
        result = runner.invoke(app, ["checkpoint", "--full", "--plan", str(checkpoint_plan)])
        data = json.loads(result.stdout)

        assert "metadata" in data
        assert data["metadata"]["generated_at"]
        assert data["metadata"]["tool"]["name"] == "vectl"
        assert data["metadata"]["plan"]["project"] == "chk"

        # v1.1: phase context
        assert "phase" in data
        assert data["phase"]["id"] == "p1"

        assert "focus" in data
        assert "active_steps" in data
        assert "next" in data

        # v1.2: next has names
        # Add step first
        plan, _ = load_plan(checkpoint_plan)
        plan.phases[0].steps.append(Step(id="p1.4", name="S4", status=StepStatus.PENDING))
        save_plan(plan, checkpoint_plan)

        result = runner.invoke(app, ["checkpoint", "--plan", str(checkpoint_plan)])
        data = json.loads(result.stdout)
        assert len(data["next"]) > 0
        assert isinstance(data["next"][0], dict)
        assert "name" in data["next"][0]

    def test_lite_mode(self, checkpoint_plan: Path) -> None:
        # Default is lite
        result = runner.invoke(app, ["checkpoint", "--plan", str(checkpoint_plan)])
        assert result.exit_code == 0
        data = json.loads(result.stdout)

        # v1.2: metadata omitted
        assert "metadata" not in data
        # active_steps present because multiple claimed (alice, bob)
        assert "active_steps" in data

    def test_lite_mode_omits_redundant_active(self, tmp_path: Path) -> None:
        # Only one claimed step
        plan = Plan(
            project="chk",
            phases=[
                Phase(
                    id="p1",
                    name="P1",
                    steps=[Step(id="s1", name="S1", status=StepStatus.CLAIMED, claimed_by="me")],
                )
            ],
        )
        path = tmp_path / "plan.yaml"
        save_plan(plan, path)

        # Default is lite
        result = runner.invoke(app, ["checkpoint", "--plan", str(path)])
        data = json.loads(result.stdout)

        # focus is s1
        assert data["focus"]["step_id"] == "s1"
        # active_steps omitted because it equals focus
        assert "active_steps" not in data

    def test_deterministic_focus_any_claimed(self, checkpoint_plan: Path) -> None:
        # No agent specified -> should pick first claimed (p1.1 or p1.2 depending on sort)
        # Sort key is ID (p1.1 < p1.2)
        result = runner.invoke(app, ["checkpoint", "--plan", str(checkpoint_plan)])
        data = json.loads(result.stdout)
        assert data["focus"]["step_id"] == "p1.1"

    def test_deterministic_focus_agent_preference(self, checkpoint_plan: Path) -> None:
        # Request bob -> should focus p1.2
        result = runner.invoke(
            app, ["checkpoint", "--agent", "bob", "--plan", str(checkpoint_plan)]
        )
        data = json.loads(result.stdout)
        assert data["focus"]["step_id"] == "p1.2"
        assert data["focus"]["claimed_by"] == "bob"

    def test_focus_fallback_to_next(self, tmp_path: Path) -> None:
        # No claimed steps
        plan = Plan(
            project="chk",
            phases=[
                Phase(
                    id="p1", name="P1", steps=[Step(id="s1", name="S1", status=StepStatus.PENDING)]
                )
            ],
        )
        path = tmp_path / "plan.yaml"
        save_plan(plan, path)

        result = runner.invoke(app, ["checkpoint", "--plan", str(path)])
        data = json.loads(result.stdout)
        assert data["focus"]["step_id"] == "s1"
        assert data["focus"]["status"] == "pending"

    def test_bounding_limits(self, tmp_path: Path) -> None:
        steps = [Step(id=f"s{i}", name=f"S{i}", status=StepStatus.PENDING) for i in range(10)]
        plan = Plan(project="chk", phases=[Phase(id="p1", name="P1", steps=steps)])
        path = tmp_path / "plan.yaml"
        save_plan(plan, path)

        # Default limit 3
        result = runner.invoke(app, ["checkpoint", "--plan", str(path)])
        data = json.loads(result.stdout)
        assert len(data["next"]) == 3
        assert data["focus"]["step_id"] == "s0"  # Focus consumes 1st next

        # v1.2: next items are dicts {step_id, name}
        # Assuming build logic aligns next with get_next_steps, and focus consumes first.
        # If build_checkpoint logic: next_ids = [s.id for s in candidates[:limit]]
        # And focus = candidates[0]
        # Then next includes focus. (Ideally should dedup but schema allows overlap)
        # Let's verify IDs
        next_ids = [item["step_id"] for item in data["next"]]
        assert next_ids == ["s0", "s1", "s2"]

    def test_guidance_inclusion(self, tmp_path: Path) -> None:
        steps = [
            Step(
                id="s1",
                name="S1",
                status=StepStatus.PENDING,
                evidence_template="Template content",
                refs=["ref1", "ref2"],
            )
        ]
        plan = Plan(project="chk", phases=[Phase(id="p1", name="P1", steps=steps)])
        path = tmp_path / "plan.yaml"
        save_plan(plan, path)

        # Without guidance
        res1 = runner.invoke(app, ["checkpoint", "--plan", str(path)])
        d1 = json.loads(res1.stdout)
        assert "guidance" not in d1

        # With guidance
        res2 = runner.invoke(app, ["checkpoint", "--include-guidance", "--plan", str(path)])
        d2 = json.loads(res2.stdout)
        assert d2["guidance"]["evidence_template"] == "Template content"
        assert d2["guidance"]["read_before"] == ["ref1", "ref2"]  # v1.1 key


class TestCheckpointActiveSteps:
    def test_active_steps_include_names_and_claimed_by(self, checkpoint_plan: Path) -> None:
        result = runner.invoke(app, ["checkpoint", "--plan", str(checkpoint_plan)])
        assert result.exit_code == 0
        data = json.loads(result.stdout)

        assert "active_steps" in data
        assert len(data["active_steps"]) > 0
        item = data["active_steps"][0]
        assert "step_id" in item
        assert "name" in item
        assert "claimed_by" in item
        assert "owner" not in item


class TestCheckpointParity:
    def test_cli_mcp_output_match(self, checkpoint_plan: Path, monkeypatch) -> None:
        # Mock MCP load to use the test plan
        def mock_load():
            p, h = load_plan(checkpoint_plan)
            return p, h

        monkeypatch.setattr("vectl.mcp_server._load", mock_load)

        # CLI output
        cli_res = runner.invoke(
            app, ["checkpoint", "--agent", "alice", "--plan", str(checkpoint_plan)]
        )
        cli_data = json.loads(cli_res.stdout)

        # MCP output via shared builder directly to ensure logic parity
        # (Assuming vectl_checkpoint wraps build_checkpoint purely)
        from vectl.checkpoint import build_checkpoint

        p, h = load_plan(checkpoint_plan)
        core_data = build_checkpoint(p, h, agent="alice")

        # Compare keys
        assert cli_data["schema"] == core_data["schema"]
        # v1.2 grouping - default is lite=True so metadata missing in both
        # assert cli_data["metadata"]["plan"]["etag"] == core_data["metadata"]["plan"]["etag"]
        assert "metadata" not in cli_data
        assert "metadata" not in core_data

        # Ensure next has items for name check
        if cli_data["next"]:
            assert cli_data["next"][0]["name"] == core_data["next"][0]["name"]


class TestCheckpointClipboard:
    """Tests for clipboard integration per RFC-clipboard.md."""

    def test_clipboard_present_when_non_empty(self, tmp_path: Path) -> None:
        """Clipboard summary appears in checkpoint when present and unexpired."""
        now = datetime.now(timezone.utc)
        future = now + timedelta(hours=24)

        plan = Plan(
            project="chk",
            clipboard=Clipboard(
                author="agent-1",
                summary="Handoff note",
                content="Full content here",
                written_at=now.isoformat().replace("+00:00", "Z"),
                expires_at=future.isoformat().replace("+00:00", "Z"),
            ),
            phases=[Phase(id="p1", name="P1", steps=[Step(id="s1", name="S1")])],
        )
        path = tmp_path / "plan.yaml"
        save_plan(plan, path)

        result = runner.invoke(app, ["checkpoint", "--plan", str(path)])
        data = json.loads(result.stdout)

        assert "clipboard" in data
        assert data["clipboard"]["author"] == "agent-1"
        assert data["clipboard"]["summary"] == "Handoff note"
        assert "content" not in data["clipboard"]  # content NOT included

    def test_clipboard_omitted_when_empty(self, tmp_path: Path) -> None:
        """Clipboard key omitted when empty."""
        plan = Plan(
            project="chk",
            phases=[Phase(id="p1", name="P1", steps=[Step(id="s1", name="S1")])],
        )
        path = tmp_path / "plan.yaml"
        save_plan(plan, path)

        result = runner.invoke(app, ["checkpoint", "--plan", str(path)])
        data = json.loads(result.stdout)

        assert "clipboard" not in data

    def test_clipboard_omitted_when_expired(self, tmp_path: Path) -> None:
        """Clipboard key omitted when expired."""
        now = datetime.now(timezone.utc)
        past = now - timedelta(hours=1)

        plan = Plan(
            project="chk",
            clipboard=Clipboard(
                author="agent-1",
                summary="Old note",
                content="Expired content",
                written_at=past.isoformat().replace("+00:00", "Z"),
                expires_at=past.isoformat().replace("+00:00", "Z"),
            ),
            phases=[Phase(id="p1", name="P1", steps=[Step(id="s1", name="S1")])],
        )
        path = tmp_path / "plan.yaml"
        save_plan(plan, path)

        result = runner.invoke(app, ["checkpoint", "--plan", str(path)])
        data = json.loads(result.stdout)

        assert "clipboard" not in data

    def test_clipboard_present_in_full_mode(self, tmp_path: Path) -> None:
        """Clipboard appears in both lite and non-lite modes."""
        now = datetime.now(timezone.utc)
        future = now + timedelta(hours=24)

        plan = Plan(
            project="chk",
            clipboard=Clipboard(
                author="agent-1",
                summary="Handoff",
                content="Content",
                written_at=now.isoformat().replace("+00:00", "Z"),
                expires_at=future.isoformat().replace("+00:00", "Z"),
            ),
            phases=[Phase(id="p1", name="P1", steps=[Step(id="s1", name="S1")])],
        )
        path = tmp_path / "plan.yaml"
        save_plan(plan, path)

        # Full mode
        result = runner.invoke(app, ["checkpoint", "--full", "--plan", str(path)])
        data = json.loads(result.stdout)

        assert "clipboard" in data
        assert "metadata" in data  # also verify full mode works

    def test_clipboard_clear_writes_split_state_null_override(self, tmp_path: Path) -> None:
        """Clearing clipboard must hide legacy YAML clipboard via state override."""
        now = datetime.now(timezone.utc)
        future = now + timedelta(hours=24)

        plan = Plan(
            project="chk",
            clipboard=Clipboard(
                author="agent-1",
                summary="legacy clipboard",
                content="legacy content",
                written_at=now.isoformat().replace("+00:00", "Z"),
                expires_at=future.isoformat().replace("+00:00", "Z"),
            ),
            phases=[Phase(id="p1", name="P1", steps=[Step(id="s1", name="S1")])],
        )
        path = tmp_path / "plan.yaml"
        save_plan(plan, path)

        clear_result = runner.invoke(app, ["clipboard-clear", "--plan", str(path)])
        assert clear_result.exit_code == 0

        checkpoint_result = runner.invoke(app, ["checkpoint", "--plan", str(path)])
        assert checkpoint_result.exit_code == 0
        checkpoint = json.loads(checkpoint_result.stdout)
        assert "clipboard" not in checkpoint

        # Source: src/vectl/io.py::merge_plan applies state.clipboard over YAML.
        # clear writes clipboard=None (omitted in JSON), but steps/phases make
        # state document non-empty so merge still applies and clears clipboard.
        state_path = resolve_state_path(path)
        assert state_path.exists()
        state_payload = json.loads(state_path.read_text(encoding="utf-8"))
        assert "clipboard" not in state_payload
