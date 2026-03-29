"""Continuity capability contracts for runner resume/persist/replay semantics.

This module is contract-only. It introduces no runtime behavior and exists so
downstream continuity phases can consume explicit capability semantics without
re-deriving them from ad hoc runner notes.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from .types import ContinuityResultFields, ReplaySafetyEnvelope, ReplayTokenSemantics


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


class RunnerReplaySafetyController(Protocol):
    """Capability-aware replay safety contract on continuity surfaces.

    Source: docs/DRIVER-CONTINUITY-FOUNDATION.md Section 4 (Restart/Replay),
    Section 6 (capability mismatch forces fresh session), and Step intent for
    ``driver-continuity-capability-safety.design-and-test``.
    """

    def evaluate_resume_or_replay(
        self,
        *,
        capability: RunnerContinuityCapability,
        envelope: ReplaySafetyEnvelope,
        requested_token: ReplayTokenSemantics | None,
        seen_attempt_keys: frozenset[str],
    ) -> ContinuityResultFields:
        """Classify resume/replay safety for one continuity envelope."""
        ...


def capability_snapshot_id(capability: RunnerContinuityCapability) -> str:
    """Return a deterministic capability snapshot identifier.

    Source: docs/DRIVER-CONTINUITY-FOUNDATION.md Section 4 (Restart/Resume) and
    Section 13 (capability snapshot persistence question).
    """

    telemetry = ",".join(capability.minimum_recovery_telemetry)
    return (
        f"{capability.runner_name}|"
        f"resume={int(capability.resume_supported)}|"
        f"persist={int(capability.persistent_session_identity)}|"
        f"replay={int(capability.replay_safe_after_partial_output)}|"
        f"fresh_on_mismatch={int(capability.requires_fresh_session_on_capability_mismatch)}|"
        f"telemetry={telemetry}"
    )


def capability_for_runner(runner_name: str) -> RunnerContinuityCapability:
    """Resolve runner continuity capability by runner name.

    Source: docs/DRIVER-CONTINUITY-FOUNDATION.md Section 3 source-of-truth
    matrix and Section 6 transport boundary rules.
    """

    if runner_name in _RUNNER_CAPABILITY_INDEX:
        return _RUNNER_CAPABILITY_INDEX[runner_name]

    return RunnerContinuityCapability(
        runner_name=runner_name,
        resume_supported=False,
        persistent_session_identity=False,
        replay_safe_after_partial_output=False,
        requires_fresh_session_on_capability_mismatch=True,
        minimum_recovery_telemetry=DEFAULT_MINIMUM_RECOVERY_TELEMETRY,
        notes=(
            "Runner capability not explicitly declared; defaulting to "
            "non-resumable safe restart semantics."
        ),
    )


def evaluate_resume_or_replay_safety(
    *,
    capability: RunnerContinuityCapability,
    envelope: ReplaySafetyEnvelope,
    requested_token: ReplayTokenSemantics | None,
    seen_attempt_keys: frozenset[str],
) -> ContinuityResultFields:
    """Evaluate continuity safety for resume/replay requests.

    This function is intentionally unimplemented in the design-and-test step.
    Runtime behavior ownership is deferred to
    ``driver-continuity-capability-safety.impl``.
    """

    expected_snapshot = capability_snapshot_id(capability)
    duplicate_replay = envelope.attempt_key in seen_attempt_keys
    if duplicate_replay:
        return ContinuityResultFields(
            attempt_key=envelope.attempt_key,
            replay_token=requested_token,
            replay_safe=False,
            duplicate_replay=True,
            recovery_reason="duplicate_attempt_key_replay",
        )

    if envelope.runner_name != capability.runner_name:
        return ContinuityResultFields(
            attempt_key=envelope.attempt_key,
            replay_token=requested_token,
            replay_safe=False,
            duplicate_replay=False,
            recovery_reason="runner_name_mismatch",
        )

    if requested_token is not None:
        if requested_token.bound_step_id != envelope.step_id:
            return ContinuityResultFields(
                attempt_key=envelope.attempt_key,
                replay_token=requested_token,
                replay_safe=False,
                duplicate_replay=False,
                recovery_reason="token_step_binding_mismatch",
            )
        if requested_token.bound_runner_name != capability.runner_name:
            return ContinuityResultFields(
                attempt_key=envelope.attempt_key,
                replay_token=requested_token,
                replay_safe=False,
                duplicate_replay=False,
                recovery_reason="token_runner_binding_mismatch",
            )
        if requested_token.capability_snapshot_id != expected_snapshot:
            return ContinuityResultFields(
                attempt_key=envelope.attempt_key,
                replay_token=requested_token,
                replay_safe=False,
                duplicate_replay=False,
                recovery_reason="token_capability_snapshot_drift",
            )
        if not requested_token.token.strip():
            return ContinuityResultFields(
                attempt_key=envelope.attempt_key,
                replay_token=requested_token,
                replay_safe=False,
                duplicate_replay=False,
                recovery_reason="empty_replay_token",
            )

    if requested_token is None:
        return ContinuityResultFields(
            attempt_key=envelope.attempt_key,
            replay_token=None,
            replay_safe=True,
            duplicate_replay=False,
            recovery_reason="fresh_dispatch_no_replay_token",
        )

    if not capability.resume_supported:
        return ContinuityResultFields(
            attempt_key=envelope.attempt_key,
            replay_token=requested_token,
            replay_safe=False,
            duplicate_replay=False,
            recovery_reason="runner_not_resumable",
        )

    return ContinuityResultFields(
        attempt_key=envelope.attempt_key,
        replay_token=requested_token,
        replay_safe=True,
        duplicate_replay=False,
        recovery_reason="capability_and_token_valid",
    )


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
        notes=(
            "Contract remains conservative until runtime verification proves "
            "restart-safe resume semantics."
        ),
    ),
)

_RUNNER_CAPABILITY_INDEX: dict[str, RunnerContinuityCapability] = {
    capability.runner_name: capability for capability in RUNNER_CONTINUITY_CAPABILITIES
}
