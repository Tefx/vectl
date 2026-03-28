"""Continuity capability contracts for runner resume/persist/replay semantics.

This module is contract-only. It introduces no runtime behavior and exists so
downstream continuity phases can consume explicit capability semantics without
re-deriving them from ad hoc runner notes.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class RunnerContinuityCapability:
    """Explicit continuity semantics for one runner implementation.

    Invariants:
    - ``resume_supported`` governs whether a prior session identifier may be
      reused after process restart.
    - ``persistent_session_identity`` states whether the runner's session ID is
      meaningful across process boundaries, not merely within one live process.
    - ``replay_safe_after_partial_output`` is true only when the runner can be
      re-invoked with the same replay envelope without forcing downstream code
      to infer duplicate side effects from free-form output.
    - ``minimum_recovery_telemetry`` names the journal/ledger fields continuity
      code must capture before restart decisions are trusted.
    """

    runner_name: str
    resume_supported: bool
    persistent_session_identity: bool
    replay_safe_after_partial_output: bool
    requires_fresh_session_on_capability_mismatch: bool
    minimum_recovery_telemetry: tuple[str, ...]
    notes: str = ""


class RunnerContinuityCapabilitySource(Protocol):
    """Protocol exposing canonical runner continuity capabilities."""

    def capability_for(self, runner_name: str) -> RunnerContinuityCapability:
        """Return the contract-level continuity capability for ``runner_name``."""
        ...


DEFAULT_MINIMUM_RECOVERY_TELEMETRY: tuple[str, ...] = (
    "step_id",
    "attempt_key",
    "runner_name",
    "session_id",
    "event_kind",
    "recorded_at",
    "summary",
)


RUNNER_CONTINUITY_CAPABILITIES: tuple[RunnerContinuityCapability, ...] = (
    RunnerContinuityCapability(
        runner_name="claude",
        resume_supported=True,
        persistent_session_identity=True,
        replay_safe_after_partial_output=False,
        requires_fresh_session_on_capability_mismatch=True,
        minimum_recovery_telemetry=DEFAULT_MINIMUM_RECOVERY_TELEMETRY,
        notes="Resume is modeled as capability-present, but replay safety remains envelope-gated.",
    ),
    RunnerContinuityCapability(
        runner_name="opencode",
        resume_supported=True,
        persistent_session_identity=True,
        replay_safe_after_partial_output=False,
        requires_fresh_session_on_capability_mismatch=True,
        minimum_recovery_telemetry=DEFAULT_MINIMUM_RECOVERY_TELEMETRY,
        notes="Session reuse is allowed only when recorded capability snapshot still matches.",
    ),
    RunnerContinuityCapability(
        runner_name="codex",
        resume_supported=True,
        persistent_session_identity=True,
        replay_safe_after_partial_output=False,
        requires_fresh_session_on_capability_mismatch=True,
        minimum_recovery_telemetry=DEFAULT_MINIMUM_RECOVERY_TELEMETRY,
    ),
    RunnerContinuityCapability(
        runner_name="gemini",
        resume_supported=False,
        persistent_session_identity=False,
        replay_safe_after_partial_output=False,
        requires_fresh_session_on_capability_mismatch=True,
        minimum_recovery_telemetry=DEFAULT_MINIMUM_RECOVERY_TELEMETRY,
        notes="Contract remains conservative until runtime verification proves restart-safe resume semantics.",
    ),
)
