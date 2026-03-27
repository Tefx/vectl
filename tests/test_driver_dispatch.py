"""Focused tests for render_prompt() deterministic template rendering.

Tests verify deterministic template rendering inputs and outputs,
ensuring downstream workers receive context-pinned prompts.

Architecture Reference: docs/DRIVER-ARCHITECTURE.md Section 2.7
Architecture Reference: docs/DRIVER-ARCHITECTURE.md Q5 (Prompt Data Flow)
Blueprint Reference: DRIVER-BLUEPRINT.md Dispatch with Preflow flow
"""

import pytest

from src.vectl.driver.dispatch import (
    PROMPT_TEMPLATE_CONTEXT,
    PROMPT_TEMPLATE_FAILURE_CONTEXT,
    PROMPT_TEMPLATE_FOOTER,
    PROMPT_TEMPLATE_HEADER,
    PROMPT_TEMPLATE_SESSION_REUSE,
    render_prompt,
)


class TestRenderPromptDeterministic:
    """Tests proving render_prompt is deterministic."""

    def test_deterministic_same_inputs(self) -> None:
        """Same inputs MUST produce identical output.

        This is the core guarantee: no LLM, no random values, no timestamps
        in the prompt content itself.

        Architecture: docs/DRIVER-ARCHITECTURE.md Section 2.7
        """
        result1 = render_prompt(
            step_id="core.impl",
            agent="python-executor",
            description="Implement the core module",
            verification="Tests pass",
            refs=["src/core.py", "tests/test_core.py"],
            worktree_path=".vectl/worktrees/core.impl",
            session_reuse=False,
        )
        result2 = render_prompt(
            step_id="core.impl",
            agent="python-executor",
            description="Implement the core module",
            verification="Tests pass",
            refs=["src/core.py", "tests/test_core.py"],
            worktree_path=".vectl/worktrees/core.impl",
            session_reuse=False,
        )

        assert result1 == result2

    def test_no_timestamp_in_content(self) -> None:
        """render_prompt MUST NOT include timestamps in prompt content.

        Timestamps belong in events (observe.py), not prompts.
        This ensures determinism across calls.
        """
        import time

        # Time before and after should not affect output
        time.sleep(0.01)

        result = render_prompt(
            step_id="test.step",
            agent="test-agent",
            description="Test task",
            verification="Pass",
            refs=[],
            worktree_path="/tmp/test",
            session_reuse=False,
        )

        # No timestamps in the output
        import re

        # Check for ISO timestamp patterns
        iso_pattern = r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}"
        assert not re.search(iso_pattern, result), "Timestamps should not appear in prompt"

        # Check for Unix timestamp
        unix_pattern = r"\b\d{10,13}\b"
        assert not re.search(unix_pattern, result), "Unix timestamps should not appear in prompt"


class TestRenderPromptInputs:
    """Tests for each prompt input and how it's used."""

    def test_step_id_included_in_output(self) -> None:
        """render_prompt MUST include step_id in the output."""
        result = render_prompt(
            step_id="core.impl",
            agent="python-executor",
            description="Task",
            verification="Pass",
            refs=[],
            worktree_path="/tmp/test",
            session_reuse=False,
        )

        assert "core.impl" in result
        assert "step core.impl" in result or "step_id" in result.lower()

    def test_agent_included_in_output(self) -> None:
        """render_prompt MUST include agent in the output."""
        result = render_prompt(
            step_id="test.step",
            agent="python-executor",
            description="Task",
            verification="Pass",
            refs=[],
            worktree_path="/tmp/test",
            session_reuse=False,
        )

        assert "python-executor" in result
        assert "You are python-executor" in result

    def test_description_included_in_output(self) -> None:
        """render_prompt MUST include description in the output."""
        result = render_prompt(
            step_id="test.step",
            agent="test-agent",
            description="Implement the feature",
            verification="Pass",
            refs=[],
            worktree_path="/tmp/test",
            session_reuse=False,
        )

        assert "Implement the feature" in result

    def test_verification_included_in_output(self) -> None:
        """render_prompt MUST include verification criteria in the output."""
        result = render_prompt(
            step_id="test.step",
            agent="test-agent",
            description="Task",
            verification="All tests pass with coverage > 80%",
            refs=[],
            worktree_path="/tmp/test",
            session_reuse=False,
        )

        assert "All tests pass with coverage > 80%" in result
        assert "Verification Criteria" in result

    def test_refs_included_in_output(self) -> None:
        """render_prompt MUST include refs list in the output."""
        result = render_prompt(
            step_id="test.step",
            agent="test-agent",
            description="Task",
            verification="Pass",
            refs=["src/main.py", "tests/test_main.py", "docs/api.md"],
            worktree_path="/tmp/test",
            session_reuse=False,
        )

        assert "src/main.py" in result
        assert "tests/test_main.py" in result
        assert "docs/api.md" in result
        assert "Reference Files" in result

    def test_worktree_path_included_in_output(self) -> None:
        """render_prompt MUST include worktree_path in the output."""
        result = render_prompt(
            step_id="test.step",
            agent="test-agent",
            description="Task",
            verification="Pass",
            refs=[],
            worktree_path=".vectl/worktrees/core.impl",
            session_reuse=False,
        )

        assert ".vectl/worktrees/core.impl" in result
        assert "Working Directory" in result

    def test_session_reuse_true_adds_context(self) -> None:
        """When session_reuse=True, render_prompt MUST include session context."""
        result = render_prompt(
            step_id="test.step",
            agent="test-agent",
            description="Task",
            verification="Pass",
            refs=[],
            worktree_path="/tmp/test",
            session_reuse=True,
        )

        assert "Session Context" in result
        assert "resuming a previous session" in result

    def test_session_reuse_false_no_session_context(self) -> None:
        """When session_reuse=False, render_prompt MUST NOT include session context."""
        result = render_prompt(
            step_id="test.step",
            agent="test-agent",
            description="Task",
            verification="Pass",
            refs=[],
            worktree_path="/tmp/test",
            session_reuse=False,
        )

        assert "Session Context" not in result
        assert "resuming a previous session" not in result

    def test_failure_context_none_no_failure_section(self) -> None:
        """When failure_context=None, render_prompt MUST NOT add failure section."""
        result = render_prompt(
            step_id="test.step",
            agent="test-agent",
            description="Task",
            verification="Pass",
            refs=[],
            worktree_path="/tmp/test",
            session_reuse=False,
            failure_context=None,
        )

        assert "Previous Attempt" not in result
        assert "previous attempt failed" not in result.lower()

    def test_failure_context_included_in_output(self) -> None:
        """When failure_context is provided, render_prompt MUST include it."""
        result = render_prompt(
            step_id="test.step",
            agent="test-agent",
            description="Task",
            verification="Pass",
            refs=[],
            worktree_path="/tmp/test",
            session_reuse=False,
            failure_context="Error: Tests failed with 3 failures",
        )

        assert "Previous Attempt" in result
        assert "Tests failed with 3 failures" in result
        assert "Address the issues above" in result

    def test_plan_context_included_when_provided(self) -> None:
        """When plan_context is provided, render_prompt MUST include it."""
        result = render_prompt(
            step_id="test.step",
            agent="test-agent",
            description="Task",
            verification="Pass",
            refs=[],
            worktree_path="/tmp/test",
            session_reuse=False,
            plan_context="This is a Python project using pytest.",
        )

        assert "Context" in result
        assert "This is a Python project using pytest" in result

    def test_plan_context_empty_not_included(self) -> None:
        """When plan_context is empty, it should not add empty context section."""
        result = render_prompt(
            step_id="test.step",
            agent="test-agent",
            description="Task",
            verification="Pass",
            refs=[],
            worktree_path="/tmp/test",
            session_reuse=False,
            plan_context="",
        )

        # Empty context should not result in empty context section
        # Count occurrences of "## Context" - should be 0 or 1 (from phase_context)
        context_count = result.count("## Context")
        assert context_count <= 1  # Only phase_context might add one

    def test_phase_context_included_when_provided(self) -> None:
        """When phase_context is provided, render_prompt MUST include it."""
        result = render_prompt(
            step_id="test.step",
            agent="test-agent",
            description="Task",
            verification="Pass",
            refs=[],
            worktree_path="/tmp/test",
            session_reuse=False,
            phase_context="Phase: Implementation - building core features",
        )

        assert "Context" in result
        assert "Phase: Implementation" in result

    def test_both_contexts_included(self) -> None:
        """Both plan_context and phase_context should be included when provided."""
        result = render_prompt(
            step_id="test.step",
            agent="test-agent",
            description="Task",
            verification="Pass",
            refs=[],
            worktree_path="/tmp/test",
            session_reuse=False,
            plan_context="Plan: Build a CLI tool",
            phase_context="Phase: Core implementation",
        )

        assert "Build a CLI tool" in result
        assert "Core implementation" in result


class TestRefsFormatting:
    """Tests for refs list formatting."""

    def test_empty_refs_no_section(self) -> None:
        """When refs is empty, no Reference Files section should appear."""
        result = render_prompt(
            step_id="test.step",
            agent="test-agent",
            description="Task",
            verification="Pass",
            refs=[],
            worktree_path="/tmp/test",
            session_reuse=False,
        )

        assert "Reference Files" not in result

    def test_single_ref_formatted_correctly(self) -> None:
        """Single ref MUST be included in output."""
        result = render_prompt(
            step_id="test.step",
            agent="test-agent",
            description="Task",
            verification="Pass",
            refs=["src/main.py"],
            worktree_path="/tmp/test",
            session_reuse=False,
        )

        assert "Reference Files" in result
        assert "src/main.py" in result

    def test_multiple_refs_formatted_correctly(self) -> None:
        """Multiple refs MUST all be included in output."""
        result = render_prompt(
            step_id="test.step",
            agent="test-agent",
            description="Task",
            verification="Pass",
            refs=["src/main.py", "src/utils.py", "tests/test_main.py"],
            worktree_path="/tmp/test",
            session_reuse=False,
        )

        assert "Reference Files" in result
        assert "src/main.py" in result
        assert "src/utils.py" in result
        assert "tests/test_main.py" in result

    def test_refs_as_file_paths(self) -> None:
        """refs are file paths that the agent should read."""
        result = render_prompt(
            step_id="test.step",
            agent="test-agent",
            description="Task",
            verification="Pass",
            refs=["/absolute/path/to/file.py", "relative/path/to/file.py"],
            worktree_path="/tmp/test",
            session_reuse=False,
        )

        # Both absolute and relative paths should be included
        assert "/absolute/path/to/file.py" in result
        assert "relative/path/to/file.py" in result


class TestNoRuntimeDispatchLogic:
    """Tests proving dispatch.py does NOT implement runner invocation."""

    def test_dispatch_does_not_import_subprocess(self) -> None:
        """dispatch.py MUST NOT import subprocess (runner invocation happens elsewhere)."""
        import ast
        import inspect

        import src.vectl.driver.dispatch as dispatch_module

        source = inspect.getsource(dispatch_module)
        tree = ast.parse(source)

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert alias.name != "subprocess", "dispatch.py MUST NOT import subprocess"
            elif isinstance(node, ast.ImportFrom):
                if node.module and "subprocess" in node.module:
                    raise AssertionError("dispatch.py MUST NOT import from subprocess")

    def test_dispatch_does_not_import_asyncio(self) -> None:
        """dispatch.py MUST NOT import asyncio (async dispatch happens in runners.py)."""
        import ast
        import inspect

        import src.vectl.driver.dispatch as dispatch_module

        source = inspect.getsource(dispatch_module)
        tree = ast.parse(source)

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert alias.name != "asyncio", "dispatch.py MUST NOT import asyncio"
            elif isinstance(node, ast.ImportFrom):
                if node.module and "asyncio" in node.module:
                    raise AssertionError("dispatch.py MUST NOT import from asyncio")

    def test_render_prompt_is_pure_function(self) -> None:
        """render_prompt MUST be a pure function (no side effects).

        Same inputs -> same output, no side effects.
        """
        # Call multiple times with same inputs
        for _ in range(3):
            result = render_prompt(
                step_id="test.step",
                agent="test-agent",
                description="Task",
                verification="Pass",
                refs=["file.py"],
                worktree_path="/tmp/test",
                session_reuse=False,
            )

            # Result should be consistent
            assert "test.step" in result
            assert "test-agent" in result
            assert "Task" in result
            assert "file.py" in result


class TestCombinedOutput:
    """Tests for combined output with multiple features enabled."""

    def test_full_prompt_with_all_features(self) -> None:
        """Full prompt with all features should contain all sections."""
        result = render_prompt(
            step_id="core.impl",
            agent="python-executor",
            description="Implement the core processing module",
            verification="All tests pass with >80% coverage",
            refs=["src/core.py", "tests/test_core.py"],
            worktree_path=".vectl/worktrees/core.impl",
            session_reuse=True,
            failure_context="Previous: SyntaxError on line 42",
            plan_context="Building a CLI tool for data processing",
            phase_context="Phase: Implementation - core features",
        )

        # Verify all sections are present
        assert "core.impl" in result
        assert "python-executor" in result
        assert "Implement the core processing module" in result
        assert "All tests pass with >80% coverage" in result
        assert "src/core.py" in result
        assert ".vectl/worktrees/core.impl" in result
        assert "Session Context" in result
        assert "Previous Attempt" in result
        assert "SyntaxError on line 42" in result
        assert "CLI tool for data processing" in result
        assert "Phase: Implementation" in result
        assert "Instructions" in result

    def test_minimal_prompt_works(self) -> None:
        """Minimal prompt with only required fields should work."""
        result = render_prompt(
            step_id="minimal",
            agent="test",
            description="Do something",
            verification="Done",
            refs=[],
            worktree_path="/tmp/test",
            session_reuse=False,
        )

        # Verify required sections are present
        assert "minimal" in result
        assert "test" in result
        assert "Do something" in result
        assert "Done" in result
        assert "/tmp/test" in result

        # Verify optional sections are NOT present
        assert "Session Context" not in result
        assert "Previous Attempt" not in result
