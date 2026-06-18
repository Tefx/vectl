"""Tests for .gitattributes config in init flow."""

from pathlib import Path

from typer.testing import CliRunner

from vectl.cli import app

runner = CliRunner()


def test_init_creates_gitattributes(tmp_path: Path, monkeypatch) -> None:
    """Test that init creates .gitattributes with plan.yaml merge driver."""
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["init", "--project", "test", "--plan", "plan.yaml"])
    assert result.exit_code == 0
    assert "Created: plan.yaml" in result.stdout

    gitattr_path = Path(".gitattributes")
    assert gitattr_path.exists(), ".gitattributes should be created"

    content = gitattr_path.read_text()
    assert "plan.yaml merge=vectl" in content


def test_init_idempotent_gitattributes(tmp_path: Path, monkeypatch) -> None:
    """Test that init does not duplicate .gitattributes entry."""
    monkeypatch.chdir(tmp_path)
    # First run
    result1 = runner.invoke(app, ["init", "--project", "test", "--plan", "plan.yaml"])
    assert result1.exit_code == 0

    # Get content after first run
    gitattr_path = Path(".gitattributes")
    content1 = gitattr_path.read_text()
    count1 = content1.count("plan.yaml merge=vectl")
    assert count1 == 1

    # Second run (with different plan name since plan.yaml exists)
    result2 = runner.invoke(app, ["init", "--project", "test2", "--plan", "plan2.yaml"])
    assert result2.exit_code == 0

    content2 = gitattr_path.read_text()
    count2 = content2.count("plan.yaml merge=vectl")
    # Should still be 1, not 2
    assert count2 == 1, f"Expected 1 occurrence, got {count2}"


def test_init_gitattributes_preserves_existing(tmp_path: Path, monkeypatch) -> None:
    """Test that init preserves existing .gitattributes content."""
    monkeypatch.chdir(tmp_path)
    # Create existing .gitattributes
    gitattr_path = Path(".gitattributes")
    gitattr_path.write_text("*.txt text=auto\n")

    result = runner.invoke(app, ["init", "--project", "test", "--plan", "plan.yaml"])
    assert result.exit_code == 0

    content = gitattr_path.read_text()
    assert "*.txt text=auto" in content
    assert "plan.yaml merge=vectl" in content
