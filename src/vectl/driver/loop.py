"""Main runtime loop contract for the Python driver.

Responsibility: Coordinate the decide -> dispatch -> wait -> reconcile cycle,
startup recovery, and graceful shutdown.

Non-responsibility: Does NOT implement runner internals, prompt rendering
templates, judge internals, git operations, or vectl core lifecycle internals.
It orchestrates those modules through their existing contracts.

Architecture Reference: docs/DRIVER-ARCHITECTURE.md Section 2.11, Section 3,
Section 4, and Q1-Q3.

CONTRACT PURITY: This module pins runtime signatures, type definitions, and
bounded deferrals only. Business/runtime behavior is intentionally deferred to
later implementation phases.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import shutil
import subprocess
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from fnmatch import fnmatch
from pathlib import Path
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Final, Literal, Protocol, cast

from vectl.claims import load_claims_for_branch, repair_claims
from vectl.decide import decide
from vectl.io import load_plan_definition, save_plan
from vectl.lifecycle import claim_step, complete_step, defer_step
from vectl.models import Action, PlanError
from vectl.plan_path import resolve_claims_path, resolve_plan_path

from .action_registry import (
    ACTION_REGISTRY_SCOPE_STATEMENT,
    INITIAL_HANDLER_INPUT_CONTRACT,
    PLANNER_DISPATCH_ACTION_DECLARATIONS,
    PLANNER_DISPATCH_GATE_REJECT_ACTION_TYPE,
    PLANNER_DISPATCH_GATE_REJECT_HANDLER_CONTRACT,
    PLANNER_DISPATCH_REPLAN_ACTION_TYPE,
    PLANNER_DISPATCH_REPLAN_HANDLER_CONTRACT,
    RECOVERY_ALL_STALLED_ACTION_TYPE,
    PlannerDispatchAction,
    UnknownLoopActionError,
    UnknownPlannerDispatchActionError,
    resolve_planner_dispatch_action_declaration,
    resolve_runtime_loop_action_declaration,
)
from .config import (
    DriverConfig,
    JudgeConfig,
    ObservabilityConfig,
    OrchestrationConfig,
    RunnerConfig,
    SessionConfig,
    load_config,
)
from .continuity_hygiene import (
    ContinuityArtifactAssessment,
    StartupHygieneStageInput,
    apply_quarantine,
    run_startup_hygiene_stage,
    scan_continuity_artifacts,
)
from .dispatch import render_prompt
from .errors import ConfigError, JudgmentParseError, JudgmentTimeoutError, RunnerError
from .events.emitter import (
    DECIDE_EVENT_DEF,
    FINAL_EVENT_DEF,
    STEP_COMPLETED_EVENT_DEF,
    emit_decide,
    emit_driver_lifecycle,
    emit_final,
    emit_heartbeat_progress,
    emit_planner_dispatch_progress,
    emit_recovery_visibility,
    emit_startup_hygiene_blocked,
    emit_startup_hygiene_classify,
    emit_startup_hygiene_quarantine,
    emit_startup_hygiene_scan,
    emit_step_completed,
)
from .judge import (
    Judge,
    JudgeOutcomeKind,
    JudgeRecoveryPolicyInput,
    decide_judge_recovery_policy,
)
from .judgments import JudgmentRequest, JudgmentType, JudgmentVerdict
from .observe import Observer, create_observer
from .policy import (
    ParsedGateIssue,
    _detect_preflight_risk_signals,
    _extract_failure_disposition,
    _is_gate_or_freeze_step,
    _parse_gate_issues,
)
from .runner_continuity import (
    capability_for_runner,
    capability_snapshot_id,
    evaluate_resume_or_replay_safety,
)
from .runners import Runner, create_runner
from .runners import RunnerStatus as RunnerDispatchStatus
from .runtime_context import (
    ROLE_SPECIFIC_CONTEXTS_DEFERRED,
    RuntimeContext,
    runtime_context_from_parts,
)
from .session import SessionPool
from .types import (
    CompletedEntry,
    ContinuityHandoff,
    ContinuityJournalEntry,
    ContinuityLedgerEntry,
    DriverState,
    JudgeContinuityPolicyOutput,
    ReplaySafetyEnvelope,
    ReplayTokenSemantics,
    RunnerRecoveryBoundaryInput,
    RunnerRecoveryBoundaryOutput,
    RunnerRecoveryDecision,
    RunnerStatus,
    StartupRecoveryBoundaryInput,
    StartupRecoveryBoundaryOutput,
    StartupRecoveryController,
    StartupRecoveryDecision,
    StartupRecoveryDisposition,
    StartupRecoveryJudgeInput,
    StartupRecoveryReconciliationFacts,
)
from .worktree import (
    ConflictResolverDispatch,
    Failure,
    merge,
)
from .worktree import (
    cleanup as cleanup_worktree,
)
from .worktree import (
    create as create_worktree,
)

if TYPE_CHECKING:
    from .config import DriverConfig
    from .judge import Judge
    from .observe import Observer
    from .runners import Runner
    from .session import SessionPool


_LOGGER = logging.getLogger(__name__)


RegisteredLoopActionHandlerName = Literal[
    "handle_dispatch",
    "emit_complete_action_ignored",
    "emit_wait",
    "emit_escalation_deferred",
    "recover_all_stalled",
]

RegisteredPlannerDispatchHandlerName = Literal[
    "dispatch_planner_replan",
    "dispatch_planner_gate_reject",
]


RUNTIME_CONTEXT_ROLLOUT_CONTRACT: Final[str] = (
    "Initial registry-backed loop handlers receive a single shared RuntimeContext; "
    "role-specific contexts remain explicitly deferred for this rollout."
)


LoopSurfaceName = Literal[
    "run",
    "handle_dispatch",
    "handle_complete",
    "reconcile",
    "shutdown",
]


CompletionSinkName = Literal["wait_for_any_then_reconcile"]


LegacyCompleteShimDisposition = Literal[
    "delete_preferred_if_feasible",
    "legacy_internal_shim_only",
]


@dataclass(frozen=True)
class CompletionAuthorityContract:
    """Approved A1 runtime completion authority contract.

    Authority source:
    - step ``driver-debt-completion-authority.contract`` required statements.

    Scope:
    - pins runtime authority boundaries only
    - carries no runtime behavior changes in this contract phase

    Invariants:
        - Runtime must not route raw runner completions through ``decide()`` for
          completion-action generation.
        - Main runtime completion sink is exactly ``wait_for_any()`` then
          ``reconcile()``.
        - ``reconcile()`` is the sole owner of completion/failure/session
          side-effects.
        - ``handle_complete()`` is removed if feasible; otherwise constrained to
          a legacy/internal shim path outside main runtime flow.
    """

    contract_id: str
    source_step_id: str
    decide_runtime_completed_results_policy: str
    runtime_main_path_forbidden_actions: tuple[str, ...]
    sole_completion_sink: CompletionSinkName
    reconcile_side_effect_owner: tuple[str, ...]
    handle_complete_disposition: LegacyCompleteShimDisposition
    handle_complete_removal_conditions: tuple[str, ...]
    blocker_regressions: tuple[str, ...]
    escalation_once_per_completed_result: bool
    implementation_owner_step: str
    rationale: str


@dataclass(frozen=True)
class DeferredRuntimeBranch:
    """Bounded runtime behavior intentionally deferred to a later phase.

    The purpose of this type is architectural: it records behavior that MUST
    remain part of the approved runtime surface even though this contract step
    does not implement it yet.
    """

    branch: str
    deferred_to: str
    rationale: str


ReplanTriggerName = Literal[
    "handle_dispatch.preflight_replan",
    "reconcile.evidence_replan",
    "reconcile.failure_classification_replan",
    "reconcile.escalation_replan",
    "run.startup_recovery_anomaly_replan",
]


GateRejectPlannerTriggerName = Literal["reconcile.gate_reject_fix_retest_chain"]


PlannerDispatchTriggerName = ReplanTriggerName | GateRejectPlannerTriggerName


PlannerDispatchSourceVerdict = Literal["REPLAN", "REJECT"]


PLANNER_SOURCE_VERDICT_REPLAN: Final[PlannerDispatchSourceVerdict] = "REPLAN"
PLANNER_SOURCE_VERDICT_REJECT: Final[PlannerDispatchSourceVerdict] = "REJECT"
LOOP_HANDLER_RUNTIME_CONTEXT: Final[type[RuntimeContext]] = RuntimeContext
LOOP_ACTION_REGISTRY_SCOPE_STATEMENT: Final[str] = ACTION_REGISTRY_SCOPE_STATEMENT
LOOP_PLANNER_ACTION_DECLARATIONS = PLANNER_DISPATCH_ACTION_DECLARATIONS
LOOP_EVENT_HELPER_DEFS: Final[tuple[str, ...]] = (
    DECIDE_EVENT_DEF.event,
    FINAL_EVENT_DEF.event,
    STEP_COMPLETED_EVENT_DEF.event,
)


def _emit_halt(*, state: DriverState, observer: Observer, reason: str) -> None:
    """Emit HALT and persist canonical halt reason for FINAL summary."""

    state.final_halt_reason = reason
    observer.emit("HALT", reason=reason)


RemainingJudgmentTriggerName = Literal[
    "reconcile.failure_classification",
    "reconcile.escalation_threshold",
    "reconcile.gate_assessment",
    "run.startup_recovery_anomaly",
    "handle_dispatch.gate_cold_context",
]


@dataclass(frozen=True)
class RemainingJudgmentRuntimeContract:
    """Pinned runtime semantics for the remaining prompt-defined judgment paths.

    Authority: docs/JUDGE-AGENT-PROMPT.md sections ``failure``,
    ``escalation``, ``gate``, ``anomaly``, and ``cold_context``.

    The purpose of this contract is to prevent later runtime work from
    narrowing any prompt-mandated branch into a smaller local rule.

    Invariants:
        - ``judgment_type`` names one of the still-expanding runtime judgment
          surfaces whose semantics remain judge-owned.
        - ``trigger`` names the exact loop entry point that must invoke or honor
          the judgment.
        - ``allowed_verdicts`` is the complete runtime verdict surface preserved
          for that trigger until an explicit architecture update changes it.
        - ``runtime_owner`` stays ``loop.py`` because loop wiring owns when these
          prompt-derived semantics are executed.
    """

    judgment_type: str
    trigger: RemainingJudgmentTriggerName
    trigger_surface: str
    allowed_verdicts: tuple[str, ...]
    planner_instruction_required_for: tuple[str, ...]
    runtime_owner: str
    deferred_to: str
    rationale: str


GateRemediationTriggerName = Literal["reconcile.gate_failure_fix_retest_chain",]


@dataclass(frozen=True)
class GateRemediationChainContract:
    """Pinned contract for gate failure -> batched remediation -> retest flow.

    Authority: docs/JUDGE-AGENT-PROMPT.md ``TYPE: gate`` and
    DRIVER-BLUEPRINT.md Flow 4.

    Invariants:
        - ``batched_remediation_owner`` explicitly names who creates the fix
          batch so later phases cannot split ownership implicitly.
        - ``included_same_batch`` contains all severities that MUST travel with
          the blocker batch once remediation is required.
        - ``retest_step_owner`` names the owner of the deferred gate re-run.
        - ``planner_instruction_source`` MUST remain the judge verdict so gate
          failures keep prompt-authored remediation semantics.
    """

    trigger: GateRemediationTriggerName
    judgment_trigger: RemainingJudgmentTriggerName
    planner_trigger: GateRejectPlannerTriggerName
    batched_remediation_owner: str
    retest_step_owner: str
    blocker_severities: tuple[str, ...]
    included_same_batch: tuple[str, ...]
    record_only_severities: tuple[str, ...]
    planner_instruction_source: str
    deferred_to: str
    rationale: str


ConflictResolverTriggerName = Literal["reconcile.merge_conflict_resolver_dispatch",]


class ConflictResolverDispatcher(Protocol):
    """Protocol for dispatching a non-trivial merge conflict resolver.

    Non-responsibility: does NOT decide whether a conflict is trivial. That is
    owned by ``worktree.merge()``. This protocol only carries the ready-to-
    dispatch payload once merge semantics have classified the conflict.
    """

    async def dispatch_conflict_resolver(
        self,
        dispatch: ConflictResolverDispatch,
        *,
        config: DriverConfig,
        runners: Mapping[str, Runner],
        observer: Observer,
    ) -> bool: ...


@dataclass(frozen=True)
class ConflictResolverReadinessContract:
    """Pinned readiness contract for non-trivial merge conflict dispatch.

    Authority: DRIVER-BLUEPRINT.md Worktree Lifecycle and Failure Modes,
    plus docs/DRIVER-ARCHITECTURE.md Section 2.11 ``dispatch_conflict_resolver``.

    Invariants:
        - ``payload_source`` stays ``MergeResult.resolver_dispatch`` so merge
          classification remains the source of truth.
        - ``dispatch_ready_when`` enumerates the full readiness preconditions the
          later implementation MUST check before invoking a resolver agent.
        - ``runtime_owner`` stays ``loop.py`` because loop reconciliation owns
          post-merge branching.
    """

    trigger: ConflictResolverTriggerName
    payload_source: str
    dispatch_ready_when: tuple[str, ...]
    runtime_owner: str
    deferred_to: str
    rationale: str


@dataclass(frozen=True)
class PlannerDispatchContract:
    """Pinned planner-dispatch contract for REPLAN-capable loop branches.

    Architecture: docs/DRIVER-ARCHITECTURE.md Section 2.11
    Blueprint: DRIVER-BLUEPRINT.md Flow 2 / Flow 3 / Flow 4

    This contract is intentionally pre-runtime. It names the exact loop trigger
    points that MAY produce planner dispatch, the source of the instruction, and
    the bounded deferral that remains in force until the later implementation
    phase wires planner execution.

    Invariants:
        - ``trigger`` MUST correspond to a branch named in
          ``DEFERRED_REPLAN_BRANCHES``.
        - ``planner_instruction`` originates from
          ``JudgmentVerdict.planner_instruction`` and may not be replaced with a
          hard-coded reject/defer message.
        - ``verdict`` remains semantically distinct from REJECT/DEFER/HALT.
          Later phases MUST preserve REPLAN as planner-dispatch-capable behavior.
        - ``deferred_to`` names the only later phase allowed to implement the
          runtime branch unless architecture authority changes.
    """

    trigger: ReplanTriggerName
    judgment_type: str
    trigger_surface: str
    planner_instruction_source: str
    deferred_to: str
    rationale: str


@dataclass(frozen=True)
class PlannerDispatchRequest:
    """Planner dispatch payload derived from planner-dispatch verdict branches.

    Architecture: docs/DRIVER-ARCHITECTURE.md Section 2.11 (`dispatch_planner`)
    Blueprint: DRIVER-BLUEPRINT.md lines 566-568, 697-698, 728-733

    This request is the canonical loop->planner handoff shape. It is contract
    surface only; runtime dispatch remains deferred.

    Invariants:
        - ``planner_instruction`` is non-empty and comes from the judge verdict.
        - ``source_verdict`` is either ``"REPLAN"`` or ``"REJECT"``.
        - ``trigger`` identifies the originating branch so later phases cannot
          silently merge distinct REPLAN sites into one reject-only path.
        - ``step_id`` identifies the step whose plan needs strengthening,
          decomposition, repair, or gate follow-up.
    """

    step_id: str
    trigger: PlannerDispatchTriggerName
    judgment_type: str
    planner_instruction: str
    source_verdict: PlannerDispatchSourceVerdict = PLANNER_SOURCE_VERDICT_REPLAN


class PlannerDispatcher(Protocol):
    """Protocol for loop-owned planner dispatch.

    Non-responsibility: does NOT decide whether REPLAN is warranted. That is the
    judge's responsibility. This protocol only carries out the loop->planner
    wiring once a REPLAN verdict already exists.
    """

    async def dispatch_planner(
        self,
        request: PlannerDispatchRequest,
        *,
        config: DriverConfig,
        runners: Mapping[str, Runner],
        observer: Observer,
        plan_path: Path,
    ) -> None: ...


@dataclass(frozen=True)
class StartupRecoveryContract:
    """Contract summary for startup recovery owned by ``run()``.

    Minimal runtime scope:
    - reload durable plan/claims state
    - repair stale claim metadata
    - clean up orphaned worktrees

    Explicitly deferred:
    - anomaly semantics that require judge-mediated repair vs halt decisions
    """

    reload_plan: bool = True
    repair_claims: bool = True
    cleanup_orphans: bool = True
    anomaly_judgment_deferred: bool = True


@dataclass(frozen=True)
class GracefulShutdownContract:
    """Contract summary for graceful shutdown owned by ``shutdown()``."""

    set_halt_requested: bool = True
    wait_for_running_handles: bool = True
    kill_remaining_processes: bool = True
    cleanup_orphans: bool = True
    emit_final_event: bool = True
    close_observer: bool = True


STARTUP_RECOVERY_CONTRACT: Final[StartupRecoveryContract] = StartupRecoveryContract()
GRACEFUL_SHUTDOWN_CONTRACT: Final[GracefulShutdownContract] = GracefulShutdownContract()


@dataclass(frozen=True)
class ReconcileContextOnlyContract:
    """Final reconcile context-only contract.

    Authority:
    - docs/DRIVER-ARCHITECTURE.md Section 2.11 reconcile boundary
    - docs/ADR-driver-evolution-foundation.md#96-handlers-receive-a-unified-runtimecontext

    This contract pins the final ``reconcile()`` signature to a single
    context-only parameter. All legacy explicit parameters are forbidden.

    Invariants:
        - ``reconcile()`` accepts exactly one required positional parameter
          (``completed``) plus one keyword-only parameter (``context``).
        - The legacy explicit parameters (``state``, ``judge``, ``session_pool``,
          ``observer``, ``plan_path``, ``config``, ``runners``) are forbidden
          on ``reconcile()`` and MUST be accessed only through
          ``RuntimeContext``.
        - ``RuntimeContext`` is the sole authoritative runtime bundle for the
          reconcile boundary.
        - ``handle_complete()`` is explicitly out of scope for completion
          authority convergence; it remains a legacy/internal shim only.

    Non-goal (handle_complete scope):
        ``handle_complete()`` does NOT handle completion authority convergence.
        The sole completion authority is ``wait_for_any() -> reconcile()``.
        ``handle_complete()`` is retained only as a legacy compatibility shim
        for internal callers that invoke ``decide(action='complete')``.
    """

    contract_id: str
    source_step_id: str
    final_signature: str
    forbidden_params: tuple[str, ...]
    runtime_bundle_authority: str
    handle_complete_non_goal: str
    rationale: str


RECONCILE_CONTEXT_ONLY_CONTRACT: Final[ReconcileContextOnlyContract] = ReconcileContextOnlyContract(
    contract_id="driver-reconcile-context-only-v1",
    source_step_id="driver-reconcile-context-convergence.contract",
    final_signature=(
        "async def reconcile(completed: CompletedEntry, *, context: RuntimeContext) -> None"
    ),
    forbidden_params=(
        "state",
        "judge",
        "session_pool",
        "observer",
        "plan_path",
        "config",
        "runners",
    ),
    runtime_bundle_authority="RuntimeContext only",
    handle_complete_non_goal=(
        "handle_complete() does NOT handle completion authority convergence. "
        "The sole completion authority is wait_for_any() -> reconcile(). "
        "handle_complete() is retained only as a legacy compatibility shim "
        "for internal callers that invoke decide(action='complete')."
    ),
    rationale=(
        "Context-only boundary enforces single runtime bundle authority at the "
        "reconcile entry point, preventing implicit coupling to individual runtime "
        "components and ensuring all runtime state is accessed through the "
        "RuntimeContext contract."
    ),
)


@dataclass(frozen=True)
class DefaultStartupRecoveryController:
    """Production startup recovery controller adapter.

    Source:
    - ``driver-continuity-review.fix-wiring-blockers`` (B1 protocol wiring)

    This adapter makes ``StartupRecoveryController`` the production startup
    entrypoint contract while preserving the existing deterministic boundary
    evaluator as the implementation authority.
    """

    def plan_recovery(
        self,
        *,
        boundary_input: StartupRecoveryBoundaryInput,
    ) -> StartupRecoveryBoundaryOutput:
        """Evaluate startup recovery through the authoritative boundary matrix."""

        return _evaluate_startup_recovery_boundary_matrix(boundary_input=boundary_input)


def _startup_recovery_controller() -> StartupRecoveryController:
    """Return the production startup recovery controller implementation."""

    return DefaultStartupRecoveryController()


STARTUP_RECOVERY_SCAN_ORDER: Final[tuple[str, ...]] = (
    "load_plan",
    "repair_claims",
    "cleanup_orphan_worktrees",
    "scan_continuity_ledger",
    "reconcile_plan_claims_ledger",
    "consume_judge_failure_policy_inputs",
    "classify_resume_restart_halt",
)
"""Normative startup scan order for restart-recovery boundary tests.

Source:
- docs/DRIVER-ARCHITECTURE.md Section 2.11 startup sequence
- docs/DRIVER-CONTINUITY-FOUNDATION.md Section 4 and Section 7
"""


RUNNER_RECOVERY_TAXONOMY: Final[tuple[str, ...]] = (
    "clean_fail",
    "crash",
    "stall",
    "no_progress",
)
"""Normative post-bootstrap watchdog/recovery taxonomy.

Source:
- step ``driver-enhancement-runner-recovery.design-and-test``
- docs/DRIVER-CONTINUITY-FOUNDATION.md Section 4 and Section 7

This taxonomy is intentionally separate from startup bootstrap recovery ordering.
"""


def evaluate_runner_recovery_boundary(
    *,
    boundary_input: RunnerRecoveryBoundaryInput,
) -> RunnerRecoveryBoundaryOutput:
    """Evaluate post-bootstrap watchdog/recovery matrix at loop boundary.

    Source:
    - step ``driver-enhancement-runner-recovery.design-and-test``

    Design authority notes:
    - Preserves continuity-owned bootstrap minimum replay/restart authority.
    - Pins crash vs clean-fail and heartbeat-driven watchdog taxonomy at the
      boundary without implementing runtime policy logic in this test-design step.
    """

    signal = boundary_input.signal
    step_id = signal.step_id
    attempt_key = signal.attempt_key
    owner = "startup_recovery_controller"

    heartbeat_age = signal.heartbeat_age_seconds
    watchdog_timeout = signal.watchdog_timeout_seconds
    heartbeat_stale = heartbeat_age is not None and heartbeat_age > watchdog_timeout

    if signal.taxonomy == "crash":
        reason = "crash_requires_controller_review"
        decision = RunnerRecoveryDecision(
            step_id=step_id,
            disposition="halt",
            reason=reason,
            source_attempt_key=attempt_key,
            restart_controller_owner=owner,
        )
        return RunnerRecoveryBoundaryOutput(
            decisions=(decision,),
            repair_actions=(),
            blocked_reasons=(f"{step_id}:{reason}",),
        )

    if signal.taxonomy == "clean_fail":
        reason = "clean_fail_restart_with_replay_guard"
        decision = RunnerRecoveryDecision(
            step_id=step_id,
            disposition="restart",
            reason=reason,
            source_attempt_key=attempt_key,
            restart_controller_owner=owner,
        )
        return RunnerRecoveryBoundaryOutput(
            decisions=(decision,),
            repair_actions=(f"restart:{step_id}:{reason}",),
            blocked_reasons=(),
        )

    if signal.taxonomy == "no_progress":
        if heartbeat_stale:
            reason = "watchdog_heartbeat_gap_no_progress"
            disposition: StartupRecoveryDisposition = "restart"
            repair_actions = (f"restart:{step_id}:{reason}",)
            blocked_reasons: tuple[str, ...] = ()
        else:
            reason = "no_progress_with_fresh_heartbeat_requires_manual_review"
            disposition = "halt"
            repair_actions = ()
            blocked_reasons = (f"{step_id}:{reason}",)

        decision = RunnerRecoveryDecision(
            step_id=step_id,
            disposition=disposition,
            reason=reason,
            source_attempt_key=attempt_key,
            restart_controller_owner=owner,
        )
        return RunnerRecoveryBoundaryOutput(
            decisions=(decision,),
            repair_actions=repair_actions,
            blocked_reasons=blocked_reasons,
        )

    # taxonomy == "stall"
    if heartbeat_stale or not signal.process_alive:
        reason = "stall_timeout_restart_with_watchdog_guard"
        disposition = "restart"
        repair_actions = (f"restart:{step_id}:{reason}",)
        blocked_reasons = ()
    else:
        reason = "stall_with_fresh_heartbeat_requires_manual_review"
        disposition = "halt"
        repair_actions = ()
        blocked_reasons = (f"{step_id}:{reason}",)

    decision = RunnerRecoveryDecision(
        step_id=step_id,
        disposition=disposition,
        reason=reason,
        source_attempt_key=attempt_key,
        restart_controller_owner=owner,
    )
    return RunnerRecoveryBoundaryOutput(
        decisions=(decision,),
        repair_actions=repair_actions,
        blocked_reasons=blocked_reasons,
    )


def evaluate_startup_recovery_boundary(
    *,
    boundary_input: StartupRecoveryBoundaryInput,
) -> StartupRecoveryBoundaryOutput:
    """Evaluate startup recovery by routing through controller contract.

    Source:
    - ``driver-continuity-review.fix-wiring-blockers`` (B1 protocol wiring)

    This surface remains available for callers that require function-level
    invocation semantics, but it no longer bypasses ``StartupRecoveryController``.
    """

    return _startup_recovery_controller().plan_recovery(boundary_input=boundary_input)


def _evaluate_startup_recovery_boundary_matrix(
    *,
    boundary_input: StartupRecoveryBoundaryInput,
) -> StartupRecoveryBoundaryOutput:
    """Evaluate startup recovery matrix from reconciliation + policy inputs.

    Source:
    - docs/DRIVER-CONTINUITY-FOUNDATION.md Section 3 and Section 4
      (ledger/plan/claims reconciliation and resume vs restart contract)
    - docs/DRIVER-CONTINUITY-FOUNDATION.md Section 7
      (judge outcomes are continuity policy inputs)
    - tests/test_driver_recovery.py

    Matrix semantics:
    - ``halt`` when plan/claims/ledger diverge for a ledger-owned step
    - ``halt`` when a judge policy input already classifies to halt
    - ``restart`` when capability snapshot facts needed for safe resume are missing
    - ``resume`` only when facts are present and no stronger block applies
    """
    reconciliation = boundary_input.reconciliation
    plan_steps = set(reconciliation.plan_step_ids)
    claim_steps = set(reconciliation.claim_step_ids)
    ledger_steps = set(reconciliation.ledger_step_ids)
    available_snapshots = set(boundary_input.capability_snapshot_ids)

    decisions: list[StartupRecoveryDecision] = []
    repair_actions: list[str] = []
    blocked_reasons: list[str] = []
    resumable_handoffs: list[ContinuityHandoff] = []

    judge_halt_by_step: dict[str, StartupRecoveryJudgeInput] = {
        judge_input.step_id: judge_input
        for judge_input in boundary_input.judge_failure_inputs
        if judge_input.policy.action == "halt"
    }

    for orphaned_step in sorted(ledger_steps - plan_steps):
        reason = "ledger_plan_claims_divergence"
        decisions.append(
            StartupRecoveryDecision(
                step_id=orphaned_step,
                disposition="halt",
                reason=reason,
            )
        )
        blocked_reasons.append(f"{orphaned_step}:{reason}")

    ledger_by_step = {entry.step_id: entry for entry in boundary_input.ledger_entries}
    candidate_steps = sorted(plan_steps & claim_steps & set(ledger_by_step))

    for step_id in candidate_steps:
        judge_halt = judge_halt_by_step.get(step_id)
        if judge_halt is not None:
            decisions.append(
                StartupRecoveryDecision(
                    step_id=step_id,
                    disposition="halt",
                    reason="judge_policy_halt",
                    source_attempt_key=judge_halt.attempt_key,
                )
            )
            blocked_reasons.append(f"{step_id}:judge_policy_halt")
            continue

        ledger_entry = ledger_by_step[step_id]
        status = ledger_entry.status.strip().lower()
        recovery_cursor = (ledger_entry.recovery_cursor or "").strip()

        if status == "completed":
            decisions.append(
                StartupRecoveryDecision(
                    step_id=step_id,
                    disposition="halt",
                    reason="ledger_status_completed",
                    source_attempt_key=ledger_entry.latest_attempt_key,
                )
            )
            blocked_reasons.append(f"{step_id}:ledger_status_completed")
            continue

        if status == "aborted":
            decisions.append(
                StartupRecoveryDecision(
                    step_id=step_id,
                    disposition="halt",
                    reason="ledger_status_aborted",
                    source_attempt_key=ledger_entry.latest_attempt_key,
                )
            )
            blocked_reasons.append(f"{step_id}:ledger_status_aborted")
            continue

        if recovery_cursor == "halt_requested_by_judge_policy":
            decisions.append(
                StartupRecoveryDecision(
                    step_id=step_id,
                    disposition="halt",
                    reason="recovery_cursor_halt_requested_by_judge_policy",
                    source_attempt_key=ledger_entry.latest_attempt_key,
                )
            )
            blocked_reasons.append(f"{step_id}:recovery_cursor_halt_requested_by_judge_policy")
            continue

        if recovery_cursor == "awaiting_conflict_resolution":
            decisions.append(
                StartupRecoveryDecision(
                    step_id=step_id,
                    disposition="halt",
                    reason="recovery_cursor_awaiting_conflict_resolution",
                    source_attempt_key=ledger_entry.latest_attempt_key,
                )
            )
            blocked_reasons.append(f"{step_id}:recovery_cursor_awaiting_conflict_resolution")
            continue

        if recovery_cursor == "pre_merge":
            decisions.append(
                StartupRecoveryDecision(
                    step_id=step_id,
                    disposition="restart",
                    reason="recovery_cursor_pre_merge",
                    source_attempt_key=ledger_entry.latest_attempt_key,
                )
            )
            repair_actions.append(f"restart:{step_id}:recovery_cursor_pre_merge")
            continue

        if recovery_cursor == "awaiting_retry_or_escalation":
            decisions.append(
                StartupRecoveryDecision(
                    step_id=step_id,
                    disposition="restart",
                    reason="recovery_cursor_awaiting_retry_or_escalation",
                    source_attempt_key=ledger_entry.latest_attempt_key,
                )
            )
            repair_actions.append(f"restart:{step_id}:recovery_cursor_awaiting_retry_or_escalation")
            continue

        runner_capability = capability_for_runner(ledger_entry.runner_name)
        expected_snapshot = capability_snapshot_id(runner_capability)

        if expected_snapshot not in available_snapshots:
            decisions.append(
                StartupRecoveryDecision(
                    step_id=step_id,
                    disposition="restart",
                    reason="capability_snapshot_missing",
                    source_attempt_key=ledger_entry.latest_attempt_key,
                )
            )
            repair_actions.append(f"restart:{step_id}:capability_snapshot_missing")
            continue

        if not runner_capability.resume_supported:
            decisions.append(
                StartupRecoveryDecision(
                    step_id=step_id,
                    disposition="restart",
                    reason="runner_not_resumable",
                    source_attempt_key=ledger_entry.latest_attempt_key,
                )
            )
            repair_actions.append(f"restart:{step_id}:runner_not_resumable")
            continue

        decisions.append(
            StartupRecoveryDecision(
                step_id=step_id,
                disposition="resume",
                reason="resume_safe_from_ledger_and_capability",
                source_attempt_key=ledger_entry.latest_attempt_key,
            )
        )
        resumable_handoffs.append(
            ContinuityHandoff(
                step_id=step_id,
                resume_from_session_id=ledger_entry.last_session_id,
                replay_envelope=ledger_entry.replay_envelope,
                ledger_entry=ledger_entry,
                capability_snapshot_id=expected_snapshot,
                reason="resume_safe_from_ledger_and_capability",
            )
        )

    return StartupRecoveryBoundaryOutput(
        decisions=tuple(decisions),
        resumable_handoffs=tuple(resumable_handoffs),
        repair_actions=tuple(repair_actions),
        blocked_reasons=tuple(blocked_reasons),
    )


COMPLETION_AUTHORITY_A1_CONTRACT: Final[CompletionAuthorityContract] = CompletionAuthorityContract(
    contract_id="driver-runtime-completion-authority-a1",
    source_step_id="driver-debt-completion-authority.contract",
    decide_runtime_completed_results_policy=(
        "driver runtime MUST NOT pass raw completed_results into decide() "
        "for completion action generation"
    ),
    runtime_main_path_forbidden_actions=(
        "_run_main_loop main path MUST NOT execute handle_complete() for runner completion",
    ),
    sole_completion_sink="wait_for_any_then_reconcile",
    reconcile_side_effect_owner=(
        "evidence judgment",
        "complete/defer lifecycle mutation",
        "merge",
        "session record",
        "worktree cleanup",
        "event emission",
    ),
    handle_complete_disposition="legacy_internal_shim_only",
    handle_complete_removal_conditions=(
        "delete handle_complete() when no legacy caller depends on decide(action='complete')",
        "if retained, keep off main runtime path and scope to internal compatibility only",
    ),
    blocker_regressions=(
        "duplicate complete_step() for one runner result",
        "duplicate STEP_COMPLETED emission for one runner result",
    ),
    escalation_once_per_completed_result=True,
    implementation_owner_step="driver-debt-completion-authority.impl",
    rationale=(
        "A1 authority converges runtime completion semantics to one sink so "
        "completion side-effects are serialized and idempotence risks are bounded."
    ),
)

# Bounded REPLAN-capable branches intentionally deferred to the
# driver-judgment-expansion-replan phase. These names are contract authority for
# future implementation work and prevent silent narrowing of runtime behavior.
DEFERRED_REPLAN_BRANCHES: Final[tuple[DeferredRuntimeBranch, ...]] = (
    DeferredRuntimeBranch(
        branch="handle_dispatch.preflight_replan",
        deferred_to="driver-judgment-expansion-replan",
        rationale=(
            "PREFLIGHT invocation commitment exists, but verdict-specific runtime "
            "handling beyond the single-runner happy path is deferred."
        ),
    ),
    DeferredRuntimeBranch(
        branch="reconcile.evidence_replan",
        deferred_to="driver-judgment-expansion-replan",
        rationale=(
            "EVIDENCE judgment is committed at reconcile success boundaries; "
            "REPLAN handling must remain available for semantic adequacy flows."
        ),
    ),
    DeferredRuntimeBranch(
        branch="reconcile.failure_classification_replan",
        deferred_to="driver-judgment-expansion-replan",
        rationale=(
            "FAILURE classification may require planner involvement before any "
            "non-blocking or downstream-blocker conclusion is accepted."
        ),
    ),
    DeferredRuntimeBranch(
        branch="reconcile.escalation_replan",
        deferred_to="driver-judgment-expansion-replan",
        rationale=(
            "Section 2.11 explicitly reserves repeated-failure escalation verdict "
            "handling including REPLAN once threshold is reached."
        ),
    ),
    DeferredRuntimeBranch(
        branch="run.startup_recovery_anomaly_replan",
        deferred_to="driver-judgment-expansion-replan",
        rationale=(
            "Startup recovery may surface structural or semantic anomalies whose "
            "safe disposition is deferred beyond minimal happy-path startup."
        ),
    ),
)


REPLAN_PLANNER_WIRING: Final[tuple[PlannerDispatchContract, ...]] = (
    PlannerDispatchContract(
        trigger="handle_dispatch.preflight_replan",
        judgment_type="PREFLIGHT",
        trigger_surface="handle_dispatch() before claim/worktree/runner dispatch",
        planner_instruction_source="JudgmentVerdict.planner_instruction",
        deferred_to="driver-judgment-expansion-replan",
        rationale=(
            "Blueprint Flow 2 requires planner dispatch when PREFLIGHT returns "
            "REPLAN so the step can be strengthened rather than silently "
            "downgraded to reject-only behavior."
        ),
    ),
    PlannerDispatchContract(
        trigger="reconcile.evidence_replan",
        judgment_type="EVIDENCE",
        trigger_surface="reconcile() success path after evidence semantics review",
        planner_instruction_source="JudgmentVerdict.planner_instruction",
        deferred_to="driver-judgment-expansion-replan",
        rationale=(
            "Evidence inadequacy may require plan strengthening or fix/retest "
            "decomposition; REPLAN must remain a planner path, not a bare reject."
        ),
    ),
    PlannerDispatchContract(
        trigger="reconcile.failure_classification_replan",
        judgment_type="FAILURE",
        trigger_surface=(
            "reconcile() failure classification before blocker disposition is finalized"
        ),
        planner_instruction_source="JudgmentVerdict.planner_instruction",
        deferred_to="driver-judgment-expansion-replan",
        rationale=(
            "Failure provenance/disposition may require planner-created follow-up "
            "work; architecture forbids collapsing this branch into reject-only "
            "or defer-only handling."
        ),
    ),
    PlannerDispatchContract(
        trigger="reconcile.escalation_replan",
        judgment_type="ESCALATION",
        trigger_surface="reconcile() repeated-failure branch once threshold is reached",
        planner_instruction_source="JudgmentVerdict.planner_instruction",
        deferred_to="driver-judgment-expansion-replan",
        rationale=(
            "Blueprint Flow 3 explicitly routes repeated-failure REPLAN verdicts "
            "to planner dispatch instead of retry/switch-agent only."
        ),
    ),
    PlannerDispatchContract(
        trigger="run.startup_recovery_anomaly_replan",
        judgment_type="ANOMALY",
        trigger_surface=(
            "run() startup recovery after anomaly detection and before unsafe auto-repair"
        ),
        planner_instruction_source="JudgmentVerdict.planner_instruction",
        deferred_to="driver-judgment-expansion-replan",
        rationale=(
            "Structural/semantic anomalies may require planner-authored repair "
            "steps; later phases may not narrow this to halt-only behavior."
        ),
    ),
)
"""Exact REPLAN/planner-dispatch wiring commitments for ``loop.py``.

Each entry pins:
- the trigger point
- the judgment type that may emit REPLAN
- the source of planner instructions
- the later phase allowed to implement runtime behavior

Anti-narrowing rule:
    Later phases MUST preserve all entries in this matrix as planner-capable.
    They MUST NOT reinterpret REPLAN as a synonym for REJECT, DEFER, or HALT.
"""


REMAINING_JUDGMENT_RUNTIME_CONTRACTS: Final[tuple[RemainingJudgmentRuntimeContract, ...]] = (
    RemainingJudgmentRuntimeContract(
        judgment_type="FAILURE",
        trigger="reconcile.failure_classification",
        trigger_surface=(
            "reconcile() failure path before any provenance/disposition result is accepted"
        ),
        allowed_verdicts=("ACCEPT", "REPLAN"),
        planner_instruction_required_for=("REPLAN",),
        runtime_owner="loop.py",
        deferred_to="driver-judgment-expansion-replan",
        rationale=(
            "The judge prompt requires provenance/disposition classification and makes "
            "planner-authored remediation ownership mandatory when disposition is "
            "downstream_blocker."
        ),
    ),
    RemainingJudgmentRuntimeContract(
        judgment_type="ESCALATION",
        trigger="reconcile.escalation_threshold",
        trigger_surface=("reconcile() repeated-failure branch once failure_count >= 3 is reached"),
        allowed_verdicts=("RETRY", "SWITCH_AGENT", "REPLAN", "DEFER", "HALT"),
        planner_instruction_required_for=("REPLAN",),
        runtime_owner="loop.py",
        deferred_to="driver-judgment-expansion-replan",
        rationale=(
            "The judge prompt owns repeated-failure disposition, including retry, "
            "agent switch, plan strengthening, defer, and terminal halt semantics."
        ),
    ),
    RemainingJudgmentRuntimeContract(
        judgment_type="GATE",
        trigger="reconcile.gate_assessment",
        trigger_surface=(
            "gate-result reconciliation after issue parsing and before blocker promotion or "
            "freeze disposition is finalized"
        ),
        allowed_verdicts=("ACCEPT", "REJECT", "HALT"),
        planner_instruction_required_for=("REJECT",),
        runtime_owner="loop.py",
        deferred_to="driver-judgment-expansion-replan",
        rationale=(
            "The judge prompt requires gate blocker promotion, runnable-surface liveness "
            "checks, freeze hard-block evaluation, and planner-authored batched fix+retest "
            "instructions when blockers remain."
        ),
    ),
    RemainingJudgmentRuntimeContract(
        judgment_type="ANOMALY",
        trigger="run.startup_recovery_anomaly",
        trigger_surface=(
            "run() startup recovery after repair/orphan detection and before unsafe auto-repair"
        ),
        allowed_verdicts=("ACCEPT", "HALT"),
        planner_instruction_required_for=(),
        runtime_owner="loop.py",
        deferred_to="driver-judgment-expansion-replan",
        rationale=(
            "The judge prompt defines the safe-vs-unsafe anomaly boundary for automatic "
            "recovery and forbids silent structural repair without judgment."
        ),
    ),
    RemainingJudgmentRuntimeContract(
        judgment_type="COLD_CONTEXT",
        trigger="handle_dispatch.gate_cold_context",
        trigger_surface=(
            "handle_dispatch() before gate/freeze dispatch payload is assembled for an "
            "independent auditor"
        ),
        allowed_verdicts=("ACCEPT",),
        planner_instruction_required_for=(),
        runtime_owner="loop.py",
        deferred_to="driver-judgment-expansion-replan",
        rationale=(
            "The judge prompt requires cold-context pruning to include only objective gate "
            "artifacts and exclude worker/orchestrator reasoning before gate dispatch."
        ),
    ),
)
"""Normative matrix for remaining prompt-defined judgment runtime paths.

This matrix closes the contract gap after the earlier REPLAN trigger pinning by
recording the exact runtime trigger point and verdict surface for the remaining
judgment semantics that are still deferred.
"""


GATE_REMEDIATION_CHAIN_CONTRACT: Final[GateRemediationChainContract] = GateRemediationChainContract(
    trigger="reconcile.gate_failure_fix_retest_chain",
    judgment_trigger="reconcile.gate_assessment",
    planner_trigger="reconcile.gate_reject_fix_retest_chain",
    batched_remediation_owner="planner dispatch from loop.py",
    retest_step_owner="loop.py gate rerun after planner-created fix batch lands",
    blocker_severities=("blocker",),
    included_same_batch=("blocker", "should_fix"),
    record_only_severities=("suggestion", "tech_debt"),
    planner_instruction_source="JudgmentVerdict.planner_instruction",
    deferred_to="driver-judgment-expansion-replan",
    rationale=(
        "The judge prompt and Flow 4 require blocker and should-fix issues to travel through "
        "one batched remediation chain, followed by a deferred gate re-run rather than ad hoc "
        "manual fixes."
    ),
)
"""Canonical owner/shape for gate failure remediation batching.

The ownership split is deliberate:
- planner dispatch owns creation of the batched fix chain
- loop reconciliation owns deferring and re-running the gate step
"""


CONFLICT_RESOLVER_READINESS_CONTRACT: Final[ConflictResolverReadinessContract] = (
    ConflictResolverReadinessContract(
        trigger="reconcile.merge_conflict_resolver_dispatch",
        payload_source="MergeResult.resolver_dispatch",
        dispatch_ready_when=(
            "merge outcome is non_trivial_conflict",
            "resolver_dispatch payload is present",
            "conflicted_files is non-empty",
            "step has already been durably deferred away from merge happy-path completion",
        ),
        runtime_owner="loop.py",
        deferred_to="driver-judgment-expansion-replan",
        rationale=(
            "The blueprint requires dispatching a conflict-resolver agent for source-file merge "
            "conflicts once worktree classification has proven the conflict is non-trivial."
        ),
    )
)
"""Canonical readiness contract for conflict-resolver dispatch."""


async def dispatch_planner(
    request: PlannerDispatchRequest,
    *,
    config: DriverConfig,
    runners: Mapping[str, Runner],
    observer: Observer,
    plan_path: Path,
) -> None:
    """Invoke the planner agent in response to a REPLAN verdict.

    Contract pin from docs/DRIVER-ARCHITECTURE.md Section 2.11 and
    DRIVER-BLUEPRINT.md Flow 2 / Flow 3 / Flow 4:

    - Input is a ``PlannerDispatchRequest`` derived from a judge REPLAN verdict.
    - ``request.planner_instruction`` is the canonical planner instruction and
      must be preserved verbatim enough to retain judge intent.
    - ``request.trigger`` identifies the originating branch so audit logs and
      later runtime code cannot silently collapse distinct REPLAN sites.
    - Runtime behavior remains deferred to ``driver-judgment-expansion-replan``.

    Runtime behavior implemented by step
    ``driver-judgment-expansion-replan.impl-replan-planner-wiring``:

    - route to planner agent ``vectl-planner``
    - execute planner instruction in repository root (plan_path.parent)
    - require planner success; non-success is explicit failure
    """
    if not request.planner_instruction.strip():
        raise PlanError(
            "REPLAN verdict requires non-empty planner_instruction "
            f"(step={request.step_id}, trigger={request.trigger})"
        )

    planner_agent = config.planner_agent_name
    runner_name = config.route_agent(planner_agent)
    runner = runners.get(runner_name)
    if runner is None:
        raise RunnerError(runner_name, request.step_id, "planner runner not configured")

    observer.emit(
        "PLANNER_DISPATCH_STARTED",
        step_id=request.step_id,
        trigger=request.trigger,
        judgment_type=request.judgment_type,
        runner=runner_name,
    )
    emit_planner_dispatch_progress(
        observer,
        judgment_type=request.judgment_type,
        phase="started",
        runner=runner_name,
        step_id=request.step_id,
        trigger=request.trigger,
        progress_index=0,
        progress_total=2,
    )

    prompt = _render_planner_prompt(
        request=request,
        plan_path=plan_path,
        planner_agent=planner_agent,
    )
    handle = await runner.dispatch(
        prompt=prompt,
        agent=planner_agent,
        workdir=str(plan_path.parent),
    )
    result = await handle.wait()

    emit_planner_dispatch_progress(
        observer,
        judgment_type=request.judgment_type,
        phase="completed" if result.status == RunnerDispatchStatus.SUCCESS else "failed",
        runner=runner_name,
        step_id=request.step_id,
        trigger=request.trigger,
        progress_index=2,
        progress_total=2,
        session_id=result.session_id,
    )

    if result.status != RunnerDispatchStatus.SUCCESS:
        observer.emit(
            "PLANNER_DISPATCH_FAILED",
            step_id=request.step_id,
            trigger=request.trigger,
            judgment_type=request.judgment_type,
            runner=runner_name,
            status=result.status.value,
            output=result.output,
        )
        raise RunnerError(
            runner_name,
            request.step_id,
            (
                f"planner dispatch failed ({result.status.value}) for "
                f"{request.trigger}: {result.output[:200]}"
            ),
        )

    observer.emit(
        "PLANNER_DISPATCH_COMPLETED",
        step_id=request.step_id,
        trigger=request.trigger,
        judgment_type=request.judgment_type,
        runner=runner_name,
        elapsed_seconds=result.elapsed_seconds,
        session_id=result.session_id,
    )


def _render_planner_prompt(
    *, request: PlannerDispatchRequest, plan_path: Path, planner_agent: str
) -> str:
    """Render deterministic planner prompt for REPLAN dispatch.

    Source: docs/DRIVER-ARCHITECTURE.md Section 2.11 (`dispatch_planner`) and
    "Planner Instruction Handling" subsection (loop owns routing the instruction
    to planner dispatch, planner owns plan mutation).
    """
    payload = {
        "step_id": request.step_id,
        "trigger": request.trigger,
        "judgment_type": request.judgment_type,
        "source_verdict": request.source_verdict,
        "plan_path": str(plan_path),
        "planner_instruction": request.planner_instruction,
    }
    return "\n".join(
        [
            f"You are {planner_agent}. Apply the requested replanning change.",
            "",
            "## REPLAN Handoff",
            json.dumps(payload, ensure_ascii=False, indent=2),
            "",
            "## Requirements",
            "1. Preserve existing completed history unless instruction explicitly requires change.",
            "2. Keep step IDs globally unique.",
            "3. Apply only the requested replanning intent.",
            "4. Return exact edits and rationale.",
        ]
    )


def _build_replan_dispatch_request(
    *,
    step_id: str,
    trigger: ReplanTriggerName,
    judgment_type: str,
    verdict: JudgmentVerdict,
) -> PlannerDispatchRequest:
    """Build a validated planner dispatch request from a judge verdict.

    Source: docs/DRIVER-ARCHITECTURE.md Section 2.11 REPLAN / Planner Wiring
    Contract and "Planner Instruction Handling".
    """
    if verdict.verdict != "REPLAN":
        raise PlanError(
            "Planner dispatch request requires REPLAN verdict "
            f"(got={verdict.verdict}, step={step_id}, trigger={trigger})"
        )
    if verdict.planner_instruction is None or not verdict.planner_instruction.strip():
        raise PlanError(
            "REPLAN verdict requires non-empty planner_instruction "
            f"(step={step_id}, trigger={trigger})"
        )

    return PlannerDispatchRequest(
        step_id=step_id,
        trigger=trigger,
        judgment_type=judgment_type,
        planner_instruction=verdict.planner_instruction,
    )


def _is_impl_step_id(step_id: str) -> bool:
    """Return True when step_id matches implementation-oriented naming.

    Source: DRIVER-BLUEPRINT.md Flow 2 preflight guard (`is_impl_step(step_id)`).
    """
    lowered = step_id.lower()
    return lowered.endswith(".impl") or ".impl-" in lowered or lowered.endswith(".implementation")


def _build_preflight_request(
    *,
    step_id: str,
    description: str,
    verification: str,
    refs: list[str],
    risk_signals: list[str],
    plan_summary: str,
) -> JudgmentRequest:
    """Build a PREFLIGHT request with required schema fields.

    Source: src/vectl/driver/judgments.py CONTEXT_SCHEMAS[PREFLIGHT].
    """
    return JudgmentRequest(
        type=JudgmentType.PREFLIGHT,
        step_id=step_id,
        context={
            "step_id": step_id,
            "step_description": description,
            "step_verification": verification,
            "step_refs": json.dumps(refs, ensure_ascii=False),
            "risk_signals": ", ".join(risk_signals),
        },
        failure_history=[],
        plan_summary=plan_summary,
    )


def _build_escalation_request(
    *,
    step_id: str,
    failure_count: int,
    failure_history: list[str],
    step_description: str,
    available_agents: list[str],
    plan_summary: str,
) -> JudgmentRequest:
    """Build an ESCALATION request with required schema fields.

    Source: src/vectl/driver/judgments.py CONTEXT_SCHEMAS[ESCALATION].
    """
    return JudgmentRequest(
        type=JudgmentType.ESCALATION,
        step_id=step_id,
        context={
            "step_id": step_id,
            "failure_count": str(failure_count),
            "failure_history": "\n".join(failure_history),
            "step_description": step_description,
            "available_agents": ", ".join(available_agents),
            "runner_failures": str(failure_count),
        },
        failure_history=failure_history,
        plan_summary=plan_summary,
    )


def _judge_type_enabled(judge: Judge, judgment_type: JudgmentType) -> bool:
    """Return whether a judgment type is enabled for this judge instance.

    Source: docs/DRIVER-ARCHITECTURE.md Section 2.10 judge-vs-rules boundary.
    """

    is_enabled = getattr(judge, "is_enabled", None)
    if callable(is_enabled):
        return bool(is_enabled(judgment_type))
    return False


def _classify_judge_parse_outcome(raw_output: str) -> JudgeOutcomeKind:
    """Classify parse failures into policy outcome kinds.

    Source: docs/DRIVER-ARCHITECTURE.md Section 2.10 verdict extraction
    boundary (timeout/parse/malformed/empty handling split).
    """

    normalized = raw_output.strip()
    if not normalized or "empty output from judge runner" in normalized.lower():
        return "empty_output"

    stripped = normalized.lstrip()
    if stripped.startswith("{") or stripped.startswith("["):
        return "malformed_output"

    return "parse_error"


def _collect_remaining_required_checks(plan: object, step_id: str) -> list[str]:
    """Collect remaining required checks for downstream-intersection reasoning.

    Source: docs/JUDGE-AGENT-PROMPT.md ``TYPE: failure`` gate-intersection
    reasoning rule requiring enumeration of remaining required checks.
    """

    find_step = getattr(plan, "find_step", None)
    phases = getattr(plan, "phases", None)
    if not callable(find_step) or not isinstance(phases, list):
        return []

    found = find_step(step_id)
    if found is None or not isinstance(found, tuple) or len(found) != 2:
        return []

    phase_obj, step_obj = found

    phase_index = -1
    step_index = -1
    for index, phase in enumerate(phases):
        if phase is phase_obj:
            phase_index = index
            phase_steps = list(getattr(phase, "steps", []))
            for inner_index, candidate_step in enumerate(phase_steps):
                if candidate_step is step_obj:
                    step_index = inner_index
                    break
            break

    if phase_index < 0 or step_index < 0:
        return []

    checks: list[str] = []
    for index in range(phase_index, len(phases)):
        phase = phases[index]
        phase_id = str(getattr(phase, "id", ""))
        gate_text = str(getattr(phase, "gate", "")).strip()
        if gate_text:
            checks.append(f"phase_gate:{phase_id}:{gate_text}")

        steps = list(getattr(phase, "steps", []))
        start = step_index + 1 if index == phase_index else 0
        for step in steps[start:]:
            candidate_step_id = str(getattr(step, "id", "")).strip()
            if not candidate_step_id:
                continue
            checks.append(f"step:{candidate_step_id}")

    # deterministic de-dup while preserving order
    seen: set[str] = set()
    deduped: list[str] = []
    for check in checks:
        if check in seen:
            continue
        deduped.append(check)
        seen.add(check)
    return deduped


def _build_failure_request(
    *,
    step_id: str,
    error_output: str,
    failure_count: int,
    step_description: str,
    remaining_gates: list[str],
    failure_history: list[str],
    plan_summary: str,
) -> JudgmentRequest:
    """Build FAILURE judgment request with downstream-intersection context.

    Source: docs/JUDGE-AGENT-PROMPT.md ``TYPE: failure`` and
    docs/DRIVER-ARCHITECTURE.md Section 2.9 ``CONTEXT_SCHEMAS[FAILURE]``.
    """

    context: dict[str, str] = {
        "step_id": step_id,
        "error_output": error_output,
        "failure_count": str(failure_count),
    }
    if step_description:
        context["step_description"] = step_description
    if remaining_gates:
        context["remaining_gates"] = "\n".join(remaining_gates)

    return JudgmentRequest(
        type=JudgmentType.FAILURE,
        step_id=step_id,
        context=context,
        failure_history=failure_history,
        plan_summary=plan_summary,
    )


def _extract_entry_points_from_verification(verification: str) -> list[str]:
    """Extract likely runnable entry points from verification text.

    Source: docs/JUDGE-AGENT-PROMPT.md ``TYPE: cold_context`` runnable-surface
    requirement (include entry points and expected startup behavior).
    """

    entry_points: list[str] = []
    for line in verification.splitlines():
        stripped = line.strip()
        lowered = stripped.lower()
        if not stripped:
            continue
        if lowered.startswith("run:") or lowered.startswith("start:"):
            entry_points.append(stripped)
            continue
        if "pytest" in lowered or "uv run" in lowered or "python -m" in lowered:
            entry_points.append(stripped)
    return entry_points[:5]


def _build_cold_context_request(
    *,
    step_id: str,
    step_spec: str,
    verification: str,
    diff: str,
) -> JudgmentRequest:
    """Build COLD_CONTEXT judgment request with required fields.

    Source: docs/DRIVER-ARCHITECTURE.md Section 2.9
    ``CONTEXT_SCHEMAS[JudgmentType.COLD_CONTEXT]``.
    """

    context: dict[str, str] = {
        "step_id": step_id,
        "step_spec": step_spec,
        "diff": diff,
    }
    if verification:
        context["verification_criteria"] = verification

    entry_points = _extract_entry_points_from_verification(verification)
    if entry_points:
        context["entry_points"] = "\n".join(entry_points)

    return JudgmentRequest(
        type=JudgmentType.COLD_CONTEXT,
        step_id=step_id,
        context=context,
        failure_history=[],
        plan_summary="",
    )


def _build_anomaly_request(
    *,
    anomaly_type: str,
    repair_scope: str,
    dry_run_recommendation: str,
) -> JudgmentRequest:
    """Build ANOMALY judgment request with required evidence sections.

    Source: docs/DRIVER-ARCHITECTURE.md Section 2.9
    ``CONTEXT_SCHEMAS[JudgmentType.ANOMALY]``.
    """

    return JudgmentRequest(
        type=JudgmentType.ANOMALY,
        step_id="startup.recovery",
        context={
            "anomaly_type": anomaly_type,
            "repair_scope": repair_scope,
            "dry_run_recommendation": dry_run_recommendation,
        },
        failure_history=[],
        plan_summary="startup recovery",
    )


def _compose_gate_batch_instruction(
    *,
    base_instruction: str,
    step_id: str,
    remediation_issues: list[ParsedGateIssue],
) -> str:
    """Compose deterministic planner instruction for full gate fix chain.

    Source: docs/JUDGE-AGENT-PROMPT.md ``TYPE: gate`` and DRIVER-BLUEPRINT.md
    Flow 4 (batched fix+retest chain for blockers and should-fix issues).
    """

    normalized = base_instruction.strip()
    lines = [
        f"Gate remediation batch for {step_id} MUST include all listed issues.",
    ]
    for issue in remediation_issues:
        lines.append(f"- [{issue.severity}] {issue.summary}")
    if normalized:
        return "\n".join([normalized, "", *lines])
    return "\n".join(lines)


def _continuity_attempt_key(*, step_id: str, runner_name: str, session_id: str | None) -> str:
    """Build deterministic attempt key for replay/idempotency checks.

    Source: docs/DRIVER-CONTINUITY-FOUNDATION.md Section 4 (Replay) and
    Section 5 (state strata).
    """

    return f"{step_id}|{runner_name}|{session_id or 'fresh'}"


def _state_attempt_key_set(state: DriverState) -> set[str]:
    """Return mutable per-state replay attempt-key registry."""

    return state.continuity_attempt_keys


def _state_capability_snapshot_cache(state: DriverState) -> dict[str, str]:
    """Return last-writer-wins capability snapshot cache by step."""

    return state.continuity_capability_snapshots


def _resolve_repo_root_from_plan(plan_path: Path) -> Path:
    """Resolve repository root from plan path."""

    plan_abs = plan_path if plan_path.is_absolute() else (Path.cwd() / plan_path)
    return plan_abs.resolve().parent


def _durability_commit_plan_if_dirty(*, plan_path: Path, reason: str) -> None:
    """Commit canonical ``plan.yaml`` mutations before/after dispatch boundaries.

    This encodes the orchestrator-owned durability barrier from the canonical
    vectl orchestrator contract: if ``plan.yaml`` is dirty, the orchestrator is
    the sole authority that may checkpoint it in git.
    """

    repo_root = _resolve_repo_root_from_plan(plan_path)
    plan_abs = plan_path if plan_path.is_absolute() else (Path.cwd() / plan_path)
    plan_abs = plan_abs.resolve()
    try:
        plan_rel = plan_abs.relative_to(repo_root)
    except ValueError:
        plan_rel = Path(plan_abs.name)

    rel_text = os.fspath(plan_rel)
    status = subprocess.run(
        ["git", "status", "--porcelain", "--", rel_text],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
    )
    if status.returncode != 0:
        _LOGGER.debug(
            "Skipping plan durability barrier; git status unavailable for %s: %s",
            rel_text,
            (status.stderr or status.stdout).strip(),
        )
        return
    if not status.stdout.strip():
        return

    add = subprocess.run(
        ["git", "add", "--", rel_text],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
    )
    if add.returncode != 0:
        raise PlanError(
            f"Plan durability barrier failed during git add for {rel_text}: "
            f"{(add.stderr or add.stdout).strip()}"
        )

    commit = subprocess.run(
        ["git", "commit", "-m", f"[vectl-driver] durability: {reason}"],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
    )
    if commit.returncode != 0:
        combined = f"{commit.stdout}\n{commit.stderr}".lower()
        if "nothing to commit" in combined or "no changes added to commit" in combined:
            return
        raise PlanError(
            f"Plan durability barrier failed during git commit for {rel_text}: "
            f"{(commit.stderr or commit.stdout).strip()}"
        )


def _save_plan_with_durability(
    plan: Any,
    *,
    plan_path: Path,
    expected_hash: str,
    reason: str,
) -> None:
    """Persist plan state, then checkpoint git durability if the file is dirty."""

    save_plan(plan, path=plan_path, expected_hash=expected_hash)
    _durability_commit_plan_if_dirty(plan_path=plan_path, reason=reason)


def _resolve_repo_root_from_worktree_path(worktree_path: str) -> Path:
    """Resolve repository root from an absolute or relative worktree path."""

    candidate = Path(worktree_path)
    if not candidate.is_absolute():
        candidate = Path.cwd() / candidate
    candidate = candidate.resolve()

    for parent in (candidate, *candidate.parents):
        if parent.name == "worktrees" and parent.parent.name == ".vectl":
            return parent.parent.parent
    return Path.cwd().resolve()


def _safe_step_filename(step_id: str) -> str:
    """Map a step id to a stable ledger/journal filename."""

    return step_id.replace("/", "__")


def _atomic_write_text(path: Path, content: str) -> None:
    """Atomically write UTF-8 text to path with fsync."""

    path.parent.mkdir(parents=True, exist_ok=True)
    temp_name = f".{path.name}.tmp-{os.getpid()}-{time.time_ns()}"
    temp_path = path.parent / temp_name
    with temp_path.open("w", encoding="utf-8") as handle:
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temp_path, path)


def _write_json(path: Path, payload: dict[str, object]) -> None:
    """Write JSON payload atomically."""

    serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    _atomic_write_text(path, serialized)


def _append_jsonl(path: Path, payload: dict[str, object]) -> None:
    """Append one JSONL record with atomic replace semantics."""

    line = json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n"
    existing = ""
    if path.exists():
        existing = path.read_text(encoding="utf-8")
    _atomic_write_text(path, existing + line)


def _continuity_now_iso() -> str:
    """Return UTC timestamp in ISO 8601 format."""

    return datetime.now(timezone.utc).isoformat()


def _build_replay_envelope(
    *,
    step_id: str,
    attempt_key: str,
    runner_name: str,
    session_id: str | None,
    scope: str,
    fingerprint: str | None,
) -> ReplaySafetyEnvelope:
    """Build replay envelope for continuity journal/ledger records."""

    return ReplaySafetyEnvelope(
        step_id=step_id,
        attempt_key=attempt_key,
        runner_name=runner_name,
        session_id=session_id,
        idempotency_scope=scope,
        tool_call_fingerprint=fingerprint,
    )


def _persist_continuity_journal(
    *,
    repo_root: Path,
    step_id: str,
    event_kind: str,
    attempt_key: str,
    runner_name: str,
    session_id: str | None,
    summary: str,
    replay_envelope: ReplaySafetyEnvelope,
) -> ContinuityJournalEntry:
    """Persist continuity journal entry for recovery-grade telemetry."""

    entry = ContinuityJournalEntry(
        step_id=step_id,
        event_kind=event_kind,
        recorded_at=_continuity_now_iso(),
        attempt_key=attempt_key,
        runner_name=runner_name,
        session_id=session_id,
        summary=summary,
        replay_envelope=replay_envelope,
    )
    journal_path = (
        repo_root / ".vectl" / "continuity" / "journal" / f"{_safe_step_filename(step_id)}.jsonl"
    )
    _append_jsonl(journal_path, asdict(entry))
    return entry


def _persist_continuity_ledger(
    *,
    repo_root: Path,
    step_id: str,
    status: str,
    attempt_key: str,
    runner_name: str,
    session_id: str | None,
    replay_envelope: ReplaySafetyEnvelope,
    last_journal_event: ContinuityJournalEntry,
    judge_policy: JudgeContinuityPolicyOutput | None = None,
    recovery_cursor: str | None,
) -> ContinuityLedgerEntry:
    """Persist continuity ledger as durable last-writer-wins snapshot."""

    entry = ContinuityLedgerEntry(
        step_id=step_id,
        latest_attempt_key=attempt_key,
        status=status,
        runner_name=runner_name,
        last_session_id=session_id,
        replay_envelope=replay_envelope,
        last_journal_event=last_journal_event,
        judge_policy=judge_policy,
        recovery_cursor=recovery_cursor,
    )
    ledger_path = (
        repo_root / ".vectl" / "continuity" / "ledger" / f"{_safe_step_filename(step_id)}.json"
    )
    _write_json(ledger_path, asdict(entry))
    return entry


def _load_ledger_step_ids(repo_root: Path) -> set[str]:
    """Load step ids present in durable continuity ledger files."""

    ledger_dir = repo_root / ".vectl" / "continuity" / "ledger"
    if not ledger_dir.exists() or not ledger_dir.is_dir():
        return set()

    step_ids: set[str] = set()
    for ledger_path in ledger_dir.glob("*.json"):
        try:
            payload = json.loads(ledger_path.read_text(encoding="utf-8"))
        except Exception:
            continue
        step_id = payload.get("step_id")
        if isinstance(step_id, str) and step_id:
            step_ids.add(step_id)
    return step_ids


def _load_ledger_entries(
    repo_root: Path,
) -> tuple[tuple[ContinuityLedgerEntry, ...], tuple[str, ...]]:
    """Load continuity ledger entries and report corrupt files.

    Source:
    - docs/DRIVER-CONTINUITY-FOUNDATION.md Section 4 restart contract
      (startup decisions require durable ledger facts)
    """

    ledger_dir = repo_root / ".vectl" / "continuity" / "ledger"
    if not ledger_dir.exists() or not ledger_dir.is_dir():
        return (), ()

    entries: list[ContinuityLedgerEntry] = []
    corrupt_files: list[str] = []
    for ledger_path in sorted(ledger_dir.glob("*.json")):
        try:
            payload = json.loads(ledger_path.read_text(encoding="utf-8"))
        except Exception:
            corrupt_files.append(ledger_path.name)
            continue

        try:
            replay_envelope_raw = payload["replay_envelope"]
            last_journal_event_raw = payload["last_journal_event"]
            replay_envelope = ReplaySafetyEnvelope(**replay_envelope_raw)
            last_journal_event = ContinuityJournalEntry(
                **{
                    **last_journal_event_raw,
                    "replay_envelope": ReplaySafetyEnvelope(
                        **last_journal_event_raw["replay_envelope"]
                    ),
                }
            )
            entries.append(
                ContinuityLedgerEntry(
                    step_id=payload["step_id"],
                    latest_attempt_key=payload["latest_attempt_key"],
                    status=payload["status"],
                    runner_name=payload["runner_name"],
                    last_session_id=payload.get("last_session_id"),
                    replay_envelope=replay_envelope,
                    last_journal_event=last_journal_event,
                    judge_policy=(
                        JudgeContinuityPolicyOutput(**payload["judge_policy"])
                        if isinstance(payload.get("judge_policy"), dict)
                        else None
                    ),
                    recovery_cursor=payload.get("recovery_cursor"),
                )
            )
        except Exception:
            corrupt_files.append(ledger_path.name)

    return tuple(entries), tuple(corrupt_files)


def _startup_judge_inputs_from_ledger_entries(
    ledger_entries: tuple[ContinuityLedgerEntry, ...],
) -> tuple[StartupRecoveryJudgeInput, ...]:
    """Extract durable startup judge-failure inputs from continuity ledger.

    Source:
    - docs/DRIVER-CONTINUITY-FOUNDATION.md Section 4 and Section 7
      (judge failure outcomes must be journaled and consumed on restart)
    """

    judge_inputs: list[StartupRecoveryJudgeInput] = []
    for ledger_entry in sorted(ledger_entries, key=lambda entry: entry.step_id):
        policy = ledger_entry.judge_policy
        if policy is None:
            continue
        if policy.action != "halt":
            continue
        judge_inputs.append(
            StartupRecoveryJudgeInput(
                step_id=ledger_entry.step_id,
                attempt_key=policy.attempt_key,
                policy=policy,
            )
        )
    return tuple(judge_inputs)


def _hygiene_assessment_requires_startup_halt(
    *,
    assessment: ContinuityArtifactAssessment,
    plan_step_ids: set[str],
    claim_step_ids: set[str],
) -> bool:
    """Return whether a hygiene assessment must halt startup.

    Source:
    - step ``driver-continuity-hygiene-core.impl-startup-stage`` two-stage
      recovery requirement
    - gate preview requirement that active/current-claim divergence remains
      blocking, while startup recovery still runs on remaining active continuity
      state
    """

    if not assessment.blocks_startup_recovery:
        return False

    if assessment.classification != "blocking_divergence":
        return True

    parsed_step_id = assessment.artifact.parsed_step_id
    if not isinstance(parsed_step_id, str) or not parsed_step_id:
        return True

    in_plan = parsed_step_id in plan_step_ids
    in_claims = parsed_step_id in claim_step_ids
    if in_plan and in_claims:
        return False

    return True


async def run(config_path: Path) -> None:
    """Main driver entry point.

    Contract pin from docs/DRIVER-ARCHITECTURE.md Section 2.11:
    1. Load config.
    2. Resolve durable plan path.
    3. Initialize runners, judge, observer, session pool, and driver state.
    4. Perform startup recovery according to ``STARTUP_RECOVERY_CONTRACT``.
    5. Enter the decide -> dispatch -> wait -> reconcile loop.
    6. Exit through ``shutdown()`` on completion, SIGINT, or SIGTERM.

    Minimal runtime scope for this phase:
    - single-runner / single-judge happy path only
    - startup recovery and graceful shutdown contracts are pinned

    Deferred beyond this phase:
    - REPLAN-capable branches listed in ``DEFERRED_REPLAN_BRANCHES``
    - multi-runner hardening
    - full judgment expansion semantics
    """
    config = _load_runtime_config(config_path)
    plan_path = resolve_plan_path(Path(config.plan_path) if config.plan_path else None)

    observer = create_observer(config.observability)
    state = DriverState()
    run_id = str(config_path.resolve())

    emit_driver_lifecycle(observer, phase="startup", run_id=run_id)
    emit_heartbeat_progress(
        observer,
        completed_count=0,
        loop_iteration=0,
        running_count=0,
        waiting_count=0,
        note="run.bootstrap",
    )

    try:
        runners = {
            name: create_runner(name, runner_cfg) for name, runner_cfg in config.runners.items()
        }
        session_pool = SessionPool(config.session)
        judge = Judge(config.judge, observer)
        startup_runtime_context = runtime_context_from_parts(
            state=state,
            config=config,
            runners=runners,
            judge=judge,
            session_pool=session_pool,
            observer=observer,
            plan_path=plan_path,
        )

        plan, _ = load_plan_definition(plan_path)
        plan_step_ids = _all_plan_step_ids(plan)
        claims_path = resolve_claims_path(plan_path)
        repair_result = repair_claims(plan, plan_path, claims_path)
        repaired_branch = getattr(repair_result, "branch", None)
        if isinstance(repaired_branch, str) and repaired_branch.strip():
            branch_claims = load_claims_for_branch(claims_path, repaired_branch)
            claim_step_ids = tuple(sorted(branch_claims.keys()))
        else:
            claim_step_ids = ()
        removed_orphans = _cleanup_orphan_worktrees(
            plan_path=plan_path,
            plan_step_ids=plan_step_ids,
        )

        continuity_repo_root = _resolve_repo_root_from_plan(plan_path)
        continuity_artifacts, corrupt_continuity_artifacts = scan_continuity_artifacts(
            continuity_repo_root
        )
        emit_startup_hygiene_scan(
            observer,
            artifact_count=len(continuity_artifacts),
            corrupt_count=len(corrupt_continuity_artifacts),
            current_branch=repaired_branch if isinstance(repaired_branch, str) else "",
            corrupt_paths=list(corrupt_continuity_artifacts),
        )
        quarantine_root = continuity_repo_root / ".vectl" / "continuity" / "quarantine"
        startup_hygiene = run_startup_hygiene_stage(
            stage_input=StartupHygieneStageInput(
                current_branch=repaired_branch if isinstance(repaired_branch, str) else "",
                current_plan_step_ids=tuple(sorted(plan_step_ids)),
                repaired_current_branch_claim_step_ids=claim_step_ids,
                artifacts=continuity_artifacts,
                quarantine_root=str(quarantine_root),
            )
        )
        for assessment in startup_hygiene.assessments:
            emit_startup_hygiene_classify(
                observer,
                artifact_kind=assessment.artifact.artifact_kind,
                classification=assessment.classification,
                original_path=assessment.artifact.original_path,
                step_id=assessment.artifact.parsed_step_id or "",
                quarantine_destination=assessment.quarantine_destination,
                safe_stale_rule_satisfied=assessment.safe_stale_rule_satisfied,
            )
        startup_hygiene = apply_quarantine(
            stage_result=startup_hygiene,
            quarantine_root=quarantine_root,
        )
        for entry in startup_hygiene.quarantine_manifest:
            emit_startup_hygiene_quarantine(
                observer,
                artifact_kind=entry.artifact_kind,
                classification=entry.classification,
                destination_path=entry.quarantine_destination,
                original_path=entry.original_path,
                reason=entry.reason,
                step_id=entry.step_id or "",
                audit_timestamp=entry.audit_timestamp,
            )

        safe_stale_ledger_step_ids = {
            assessment.artifact.parsed_step_id
            for assessment in startup_hygiene.assessments
            if assessment.classification == "safe_stale_quarantine"
            and assessment.artifact.artifact_kind == "ledger"
            and assessment.artifact.parsed_step_id is not None
        }
        ledger_entries, corrupt_ledger_files = _load_ledger_entries(continuity_repo_root)
        if safe_stale_ledger_step_ids:
            ledger_entries = tuple(
                entry for entry in ledger_entries if entry.step_id not in safe_stale_ledger_step_ids
            )
        startup_judge_failure_inputs = _startup_judge_inputs_from_ledger_entries(ledger_entries)
        startup_boundary = evaluate_startup_recovery_boundary(
            boundary_input=StartupRecoveryBoundaryInput(
                reconciliation=StartupRecoveryReconciliationFacts(
                    plan_step_ids=tuple(sorted(plan_step_ids)),
                    claim_step_ids=claim_step_ids,
                    ledger_step_ids=tuple(sorted(entry.step_id for entry in ledger_entries)),
                ),
                capability_snapshot_ids=tuple(
                    sorted(
                        {
                            capability_snapshot_id(capability_for_runner(entry.runner_name))
                            for entry in ledger_entries
                        }
                    )
                ),
                ledger_entries=ledger_entries,
                judge_failure_inputs=startup_judge_failure_inputs,
            )
        )

        halt_reasons = list(startup_boundary.blocked_reasons)
        for blocked_assessment in startup_hygiene.blocked_assessments:
            if not _hygiene_assessment_requires_startup_halt(
                assessment=blocked_assessment,
                plan_step_ids=plan_step_ids,
                claim_step_ids=set(claim_step_ids),
            ):
                continue
            emit_startup_hygiene_blocked(
                observer,
                classification=blocked_assessment.classification,
                reason=blocked_assessment.reason,
                step_id=blocked_assessment.artifact.parsed_step_id or "unknown",
            )
            step_segment = blocked_assessment.artifact.parsed_step_id or "unknown"
            halt_reasons.append(f"hygiene_blocked:{step_segment}:{blocked_assessment.reason}")
        if corrupt_continuity_artifacts:
            halt_reasons.extend(
                f"continuity_corrupt:{Path(artifact_path).name}"
                for artifact_path in corrupt_continuity_artifacts
            )
            for artifact_path in corrupt_continuity_artifacts:
                emit_startup_hygiene_blocked(
                    observer,
                    classification="corrupt_blocking",
                    reason=f"continuity_corrupt:{Path(artifact_path).name}",
                    step_id=Path(artifact_path).stem,
                )
        if corrupt_ledger_files:
            halt_reasons.extend(
                f"ledger_corrupt:{ledger_file_name}" for ledger_file_name in corrupt_ledger_files
            )
            for ledger_file_name in corrupt_ledger_files:
                emit_startup_hygiene_blocked(
                    observer,
                    classification="corrupt_blocking",
                    reason=f"ledger_corrupt:{ledger_file_name}",
                    step_id=Path(ledger_file_name).stem,
                )
        if halt_reasons:
            _emit_halt(
                state=state,
                observer=observer,
                reason=("Continuity startup recovery blocked: " + ",".join(halt_reasons)),
            )
            state.halt_requested = True

        for decision in startup_boundary.decisions:
            observer.emit(
                "STARTUP_RECOVERY_DECISION",
                step_id=decision.step_id,
                disposition=decision.disposition,
                reason=decision.reason,
                attempt_key=decision.source_attempt_key,
            )

        if _judge_type_enabled(judge, JudgmentType.ANOMALY):
            startup_anomalies: list[JudgmentRequest] = []
            if repair_result.actions:
                startup_anomalies.append(
                    _build_anomaly_request(
                        anomaly_type="claim_state_repair",
                        repair_scope=json.dumps(
                            [
                                {
                                    "action": action.action,
                                    "key": action.key,
                                    "reason": action.reason,
                                }
                                for action in repair_result.actions
                            ],
                            ensure_ascii=False,
                        ),
                        dry_run_recommendation="safe_to_apply",
                    )
                )
            if removed_orphans:
                startup_anomalies.append(
                    _build_anomaly_request(
                        anomaly_type="orphan_worktrees",
                        repair_scope=json.dumps(
                            {"removed_orphans": list(removed_orphans)}, ensure_ascii=False
                        ),
                        dry_run_recommendation="safe_to_apply",
                    )
                )

            for anomaly_request in startup_anomalies:
                anomaly_verdict = await judge.judge(anomaly_request)
                observer.emit(
                    "ANOMALY_VERDICT",
                    step_id=anomaly_request.step_id,
                    verdict=anomaly_verdict.verdict,
                    reason=anomaly_verdict.reason,
                    anomaly_type=anomaly_request.context["anomaly_type"],
                )

                if anomaly_verdict.verdict == "HALT":
                    _emit_halt(
                        state=state,
                        observer=observer,
                        reason=(
                            "Startup anomaly marked unsafe for auto-repair: "
                            f"{anomaly_verdict.reason}"
                        ),
                    )
                    state.halt_requested = True
                    break

                if anomaly_verdict.verdict == "REPLAN":
                    if (
                        anomaly_verdict.planner_instruction is None
                        or not anomaly_verdict.planner_instruction.strip()
                    ):
                        raise PlanError(
                            "ANOMALY REPLAN verdict requires non-empty planner_instruction "
                            "for startup recovery"
                        )
                    await _dispatch_registered_planner_action(
                        action=PlannerDispatchAction(
                            action_type=PLANNER_DISPATCH_REPLAN_ACTION_TYPE,
                            step_id="startup.recovery",
                            trigger="run.startup_recovery_anomaly_replan",
                            judgment_type="ANOMALY",
                            planner_instruction=anomaly_verdict.planner_instruction,
                            source_verdict=PLANNER_SOURCE_VERDICT_REPLAN,
                        ),
                        context=startup_runtime_context,
                    )

                if anomaly_verdict.verdict not in {"ACCEPT", "HALT", "REPLAN"}:
                    raise PlanError(
                        "ANOMALY verdict must be ACCEPT, REPLAN, or HALT "
                        f"(got={anomaly_verdict.verdict})"
                    )

        if not state.halt_requested:
            await _run_main_loop(
                state=state,
                config=config,
                runners=runners,
                judge=judge,
                session_pool=session_pool,
                observer=observer,
                plan_path=plan_path,
            )
    finally:
        emit_driver_lifecycle(
            observer,
            phase="shutdown",
            run_id=run_id,
            note=state.final_halt_reason,
        )
        await shutdown(state=state, observer=observer)


async def handle_dispatch(
    action: Action,
    state: DriverState | None = None,
    config: DriverConfig | None = None,
    runners: Mapping[str, Runner] | None = None,
    judge: Judge | None = None,
    session_pool: SessionPool | None = None,
    observer: Observer | None = None,
    plan_path: Path | None = None,
    *,
    context: RuntimeContext | None = None,
) -> None:
    """Handle one ``claim_and_dispatch`` action from ``decide()``.

    Contract pin from Section 2.11 and Q1-Q3:
    - optionally invoke preflight judgment at the committed trigger point
    - reload plan state before lifecycle mutation
    - claim step using CAS-safe plan/claims path
    - create worktree
    - resolve runner
    - resolve session reuse for the single-runner happy path
    - render prompt and dispatch runner
    - register in ``DriverState``

    Deferred branch authority:
    - ``handle_dispatch.preflight_replan`` remains deferred and may not be
      silently removed in later phases.
    """
    if action.step_id is None or action.agent is None:
        raise PlanError("claim_and_dispatch action requires step_id and agent")

    if context is not None:
        state = context.state
        config = context.config
        runners = context.runners
        judge = context.judge
        session_pool = context.session_pool
        observer = context.observer
        plan_path = context.plan_path

    if state is None:
        raise PlanError("handle_dispatch requires runtime state")
    if config is None:
        raise PlanError("handle_dispatch requires runtime config")
    if runners is None:
        raise PlanError("handle_dispatch requires runtime runners")
    if judge is None:
        raise PlanError("handle_dispatch requires runtime judge")
    if session_pool is None:
        raise PlanError("handle_dispatch requires runtime session_pool")
    if observer is None:
        raise PlanError("handle_dispatch requires runtime observer")
    if plan_path is None:
        raise PlanError("handle_dispatch requires runtime plan_path")

    step_id = action.step_id
    agent = action.agent

    preflight_metadata_plan = None
    preflight_description = action.step_description or ""
    preflight_verification = action.step_verification or ""
    preflight_refs = action.step_refs or []
    if config.judge.preflight and _is_impl_step_id(step_id):
        if any(fnmatch(step_id, pattern) for pattern in config.judge.skip_preflight_for):
            observer.emit("PREFLIGHT_SKIPPED", step_id=step_id, reason="skip_preflight_for_pattern")
        else:
            if not preflight_description or not preflight_verification:
                preflight_metadata_plan, _ = load_plan_definition(plan_path)
                found = preflight_metadata_plan.find_step(step_id)
                if found is not None:
                    _, preflight_step = found
                    if not preflight_description:
                        preflight_description = preflight_step.description
                    if not preflight_verification:
                        preflight_verification = preflight_step.verification
                    if not preflight_refs:
                        preflight_refs = list(preflight_step.refs)

            risk_signals = _detect_preflight_risk_signals(
                step_id=step_id,
                description=preflight_description,
                verification=preflight_verification,
                refs=preflight_refs,
            )
            if risk_signals:
                preflight_request = _build_preflight_request(
                    step_id=step_id,
                    description=preflight_description,
                    verification=preflight_verification,
                    refs=preflight_refs,
                    risk_signals=risk_signals,
                    plan_summary=(
                        preflight_metadata_plan.context
                        if preflight_metadata_plan is not None
                        else ""
                    ),
                )
                preflight_verdict = await judge.judge(preflight_request)
                observer.emit(
                    "PREFLIGHT_VERDICT",
                    step_id=step_id,
                    verdict=preflight_verdict.verdict,
                    reason=preflight_verdict.reason,
                    risk_signals=risk_signals,
                )

                if preflight_verdict.verdict == "REPLAN":
                    planner_request = _build_replan_dispatch_request(
                        step_id=step_id,
                        trigger="handle_dispatch.preflight_replan",
                        judgment_type="PREFLIGHT",
                        verdict=preflight_verdict,
                    )
                    await _dispatch_registered_planner_action(
                        action=PlannerDispatchAction(
                            action_type=PLANNER_DISPATCH_REPLAN_ACTION_TYPE,
                            step_id=planner_request.step_id,
                            trigger=planner_request.trigger,
                            judgment_type=planner_request.judgment_type,
                            planner_instruction=planner_request.planner_instruction,
                            source_verdict=planner_request.source_verdict,
                        ),
                        context=_runtime_context_from_runtime(
                            state=state,
                            config=config,
                            runners=runners,
                            judge=judge,
                            session_pool=session_pool,
                            observer=observer,
                            plan_path=plan_path,
                        ),
                    )
                    return
                if preflight_verdict.verdict == "REJECT":
                    observer.emit(
                        "PREFLIGHT_REJECT", step_id=step_id, reason=preflight_verdict.reason
                    )
                    return
                if preflight_verdict.verdict == "DEFER":
                    observer.emit(
                        "PREFLIGHT_DEFER", step_id=step_id, reason=preflight_verdict.reason
                    )
                    return

    plan, expected_hash = load_plan_definition(plan_path)
    claims_path = resolve_claims_path(plan_path)
    plan, _ = claim_step(plan, step_id, agent, claims_path=claims_path)

    _save_plan_with_durability(
        plan,
        plan_path=plan_path,
        expected_hash=expected_hash,
        reason=f"claim {step_id}",
    )

    binding_result = await create_worktree(step_id, cwd=plan_path.parent)
    if isinstance(binding_result, Failure):
        raise binding_result.error
    binding = binding_result.value

    runner_name = config.route_agent(agent)
    primary_runner = runner_name
    if state.runner_failures.get((step_id, runner_name), 0) >= 2:
        runner_name = config.fallback_runner
        observer.emit(
            "RUNNER_FALLBACK",
            step_id=step_id,
            from_runner=primary_runner,
            to_runner=runner_name,
            reason="repeated_failures",
        )

    runner = runners.get(runner_name)
    if runner is None:
        raise RunnerError(runner_name, step_id, "runner not configured")

    found = plan.find_step(step_id)
    if found is None:
        raise PlanError(f"Step '{step_id}' not found after claim")
    _, step = found

    step_description = action.step_description or step.description
    step_verification = action.step_verification or step.verification

    if _is_gate_or_freeze_step(
        step_id=step_id,
        description=step_description,
        verification=step_verification,
    ) and _judge_type_enabled(judge, JudgmentType.COLD_CONTEXT):
        cold_context_request = _build_cold_context_request(
            step_id=step_id,
            step_spec=step_description,
            verification=step_verification,
            diff="No diff available before gate dispatch; use objective artifacts only.",
        )
        cold_context_verdict = await judge.judge(cold_context_request)
        observer.emit(
            "COLD_CONTEXT_VERDICT",
            step_id=step_id,
            verdict=cold_context_verdict.verdict,
            reason=cold_context_verdict.reason,
        )
        if cold_context_verdict.verdict != "ACCEPT":
            raise PlanError(
                "COLD_CONTEXT judgment returned non-ACCEPT verdict "
                f"(step={step_id}, verdict={cold_context_verdict.verdict})"
            )

    pool_session_candidate = session_pool.find_reusable(
        step_id=step_id,
        agent=agent,
        runner_name=runner_name,
        depends_on=step.depends_on,
    )

    session_id: str | None = None
    session_source = "none"
    if action.session == "reuse" and action.task_id:
        session_id = action.task_id
        session_source = "action"
        if pool_session_candidate is not None and pool_session_candidate != action.task_id:
            observer.emit(
                "CONTINUITY_DOUBLE_TRUTH_DETECTED",
                step_id=step_id,
                action_task_id=action.task_id,
                pool_session_id=pool_session_candidate,
                authority="durable_continuity_required",
            )
            replay_envelope = _build_replay_envelope(
                step_id=step_id,
                attempt_key=_continuity_attempt_key(
                    step_id=step_id,
                    runner_name=runner_name,
                    session_id=action.task_id,
                ),
                runner_name=runner_name,
                session_id=action.task_id,
                scope="dispatch_double_truth_detection",
                fingerprint=action.task_id,
            )
            _persist_continuity_journal(
                repo_root=_resolve_repo_root_from_plan(plan_path),
                step_id=step_id,
                event_kind="double_truth_detected",
                attempt_key=replay_envelope.attempt_key,
                runner_name=runner_name,
                session_id=action.task_id,
                summary=(
                    "Action reuse task_id diverged from SessionPool candidate; "
                    "durable ledger authority required"
                ),
                replay_envelope=replay_envelope,
            )
    else:
        session_id = pool_session_candidate
        if session_id is not None:
            session_source = "pool"

    capability = capability_for_runner(runner_name)
    current_snapshot = capability_snapshot_id(capability)
    snapshot_cache = _state_capability_snapshot_cache(state)
    previous_snapshot = snapshot_cache.get(step_id)
    snapshot_cache[step_id] = current_snapshot

    replay_token: ReplayTokenSemantics | None = None
    if session_id is not None:
        replay_token = ReplayTokenSemantics(
            token=session_id,
            token_kind="session",
            semantics_version="bootstrap-v1",
            bound_runner_name=runner_name,
            bound_step_id=step_id,
            capability_snapshot_id=previous_snapshot or current_snapshot,
        )

    attempt_key = _continuity_attempt_key(
        step_id=step_id,
        runner_name=runner_name,
        session_id=session_id,
    )
    seen_attempt_keys = frozenset(_state_attempt_key_set(state))
    replay_decision = evaluate_resume_or_replay_safety(
        capability=capability,
        envelope=ReplaySafetyEnvelope(
            step_id=step_id,
            attempt_key=attempt_key,
            runner_name=runner_name,
            session_id=session_id,
            idempotency_scope="step_dispatch",
            tool_call_fingerprint=action.task_id,
        ),
        requested_token=replay_token,
        seen_attempt_keys=seen_attempt_keys,
    )
    if replay_decision.replay_safe:
        _state_attempt_key_set(state).add(replay_decision.attempt_key)
    else:
        if session_id is not None:
            observer.emit(
                "SESSION_REUSE_MISS",
                step_id=step_id,
                reason=replay_decision.recovery_reason,
            )
        session_id = None
        session_source = "none"

    if session_id is not None:
        observer.emit(
            "SESSION_REUSE_HIT",
            step_id=step_id,
            session_id=session_id,
            source=session_source,
        )
    else:
        observer.emit("SESSION_REUSE_MISS", step_id=step_id, reason="no_reusable_session")

    prompt = render_prompt(
        step_id=step_id,
        agent=agent,
        description=step_description,
        verification=step_verification,
        refs=action.step_refs or step.refs,
        worktree_path=str(binding.worktree_path),
        session_reuse=session_id is not None,
        failure_context=state.get_failure_context(step_id),
        plan_context=plan.context,
        phase_context="",
    )

    try:
        handle = await runner.dispatch(
            prompt=prompt,
            agent=agent,
            workdir=str(binding.worktree_path),
            session_id=session_id,
        )
    except RunnerError:
        if session_id is not None:
            observer.emit(
                "SESSION_REUSE_MISS",
                step_id=step_id,
                reason="stale_or_unavailable_session",
            )
            handle = await runner.dispatch(
                prompt=prompt,
                agent=agent,
                workdir=str(binding.worktree_path),
                session_id=None,
            )
        else:
            raise
    state.register(
        step_id=step_id,
        agent=agent,
        runner_name=runner_name,
        handle=handle,
        worktree_path=str(binding.worktree_path),
    )

    observer.emit(
        "STEP_DISPATCHED",
        step_id=step_id,
        agent=agent,
        runner=runner_name,
        session_reuse=session_id is not None,
        preflight_enabled=config.judge.preflight,
    )


async def handle_complete(
    action: Action,
    state: DriverState | None = None,
    judge: Judge | None = None,
    session_pool: SessionPool | None = None,
    observer: Observer | None = None,
    plan_path: Path | None = None,
    *,
    context: RuntimeContext | None = None,
) -> None:
    """Handle one ``complete`` action from ``decide()``.

    Contract pin from Section 2.11:
    - reload plan from disk before mutation
    - complete the step using CAS-safe lifecycle operations
    - persist the resulting plan state

    Legacy-shim disposition (A1 authority):
    - main runtime path should converge on ``wait_for_any() -> reconcile()`` as
      the sole completion sink.
    - this function remains only as an internal compatibility shim until
      ``driver-debt-completion-authority.impl`` removes or narrows legacy calls.
    """
    if action.step_id is None:
        raise PlanError("complete action requires step_id")

    if context is not None:
        state = context.state
        judge = context.judge
        session_pool = context.session_pool
        observer = context.observer
        plan_path = context.plan_path

    if state is None:
        raise PlanError("handle_complete requires runtime state")
    if judge is None:
        raise PlanError("handle_complete requires runtime judge")
    if session_pool is None:
        raise PlanError("handle_complete requires runtime session_pool")
    if observer is None:
        raise PlanError("handle_complete requires runtime observer")
    if plan_path is None:
        raise PlanError("handle_complete requires runtime plan_path")

    evidence = action.evidence or ""
    plan, expected_hash = load_plan_definition(plan_path)
    claims_path = resolve_claims_path(plan_path)
    plan = complete_step(plan, action.step_id, evidence, claims_path=claims_path)
    _save_plan_with_durability(
        plan,
        plan_path=plan_path,
        expected_hash=expected_hash,
        reason=f"complete {action.step_id}",
    )

    emit_step_completed(
        observer,
        step_id=action.step_id,
        elapsed_seconds=0.0,
        evidence_len=len(evidence),
    )


async def reconcile(
    completed: CompletedEntry,
    *,
    context: RuntimeContext,
) -> None:
    """Reconcile one completed runner result.

    Contract pin from Section 2.11, Section 3, Section 4, and Q1-Q3:
    - success path: rules first, then judge where semantic validation is required,
      then durable completion, merge, session recording, and worktree cleanup
    - failure path: increment counters, defer under threshold, preserve
      escalation threshold contract at ``count >= 3``
    - all durable mutations reload plan state before write
    - merge operations are serialized by ``DriverState.merge_lock``

    Minimal runtime scope for this phase:
    - single-runner / single-judge happy path only

    Explicit deferred REPLAN-capable branches:
    - ``reconcile.evidence_replan``
    - ``reconcile.failure_classification_replan``
    - ``reconcile.escalation_replan``
    """
    state = context.state
    judge = context.judge
    session_pool = context.session_pool
    observer = context.observer
    plan_path = context.plan_path
    config = context.config
    runners = context.runners

    continuity_repo_root = _resolve_repo_root_from_plan(plan_path)
    continuity_attempt_key = (
        completed.result.continuity.attempt_key
        if completed.result.continuity is not None
        else _continuity_attempt_key(
            step_id=completed.step_id,
            runner_name=completed.runner_name,
            session_id=completed.result.session_id,
        )
    )

    if completed.result.status == RunnerStatus.SUCCESS:
        success_envelope = _build_replay_envelope(
            step_id=completed.step_id,
            attempt_key=continuity_attempt_key,
            runner_name=completed.runner_name,
            session_id=completed.result.session_id,
            scope="reconcile_success",
            fingerprint=None,
        )
        success_journal = _persist_continuity_journal(
            repo_root=continuity_repo_root,
            step_id=completed.step_id,
            event_kind="runner_success",
            attempt_key=continuity_attempt_key,
            runner_name=completed.runner_name,
            session_id=completed.result.session_id,
            summary="Runner completed successfully; reconciling merge and lifecycle mutations",
            replay_envelope=success_envelope,
        )
        emit_recovery_visibility(
            observer,
            attempt_key=continuity_attempt_key,
            event_kind="runner_success",
            recorded_at=success_journal.recorded_at,
            runner_name=completed.runner_name,
            session_id=completed.result.session_id,
            step_id=completed.step_id,
            summary=success_journal.summary,
            recovery_cursor="pre_merge",
        )
        _persist_continuity_ledger(
            repo_root=continuity_repo_root,
            step_id=completed.step_id,
            status="runner_success",
            attempt_key=continuity_attempt_key,
            runner_name=completed.runner_name,
            session_id=completed.result.session_id,
            replay_envelope=success_envelope,
            last_journal_event=success_journal,
            recovery_cursor="pre_merge",
        )

        evidence = completed.result.output
        plan, expected_hash = load_plan_definition(plan_path)
        state.decide_state.reset_failure(step_id=completed.step_id)
        state.failure_counts.pop(completed.step_id, None)
        found = plan.find_step(completed.step_id)
        if found is not None:
            _, step = found
            if _is_gate_or_freeze_step(
                step_id=completed.step_id,
                description=step.description,
                verification=step.verification,
            ) and _judge_type_enabled(judge, JudgmentType.GATE):
                issues = _parse_gate_issues(evidence)
                remediation_issues = [
                    issue
                    for issue in issues
                    if issue.severity in GATE_REMEDIATION_CHAIN_CONTRACT.included_same_batch
                ]
                has_blocker = any(
                    issue.severity in GATE_REMEDIATION_CHAIN_CONTRACT.blocker_severities
                    for issue in remediation_issues
                )
                if has_blocker:
                    remaining_checks = _collect_remaining_required_checks(plan, completed.step_id)
                    gate_request = JudgmentRequest(
                        type=JudgmentType.GATE,
                        step_id=completed.step_id,
                        context={
                            "step_id": completed.step_id,
                            "gate_evidence": evidence,
                            "blocker_issues": json.dumps(
                                [
                                    {
                                        "severity": issue.severity,
                                        "summary": issue.summary,
                                        "raw": issue.raw,
                                    }
                                    for issue in remediation_issues
                                ],
                                ensure_ascii=False,
                            ),
                            "remaining_gates": "\n".join(remaining_checks),
                        },
                        failure_history=state.get_failure_history(completed.step_id),
                        plan_summary=str(getattr(plan, "context", "")),
                    )
                    gate_verdict = await judge.judge(gate_request)
                    observer.emit(
                        "GATE_VERDICT",
                        step_id=completed.step_id,
                        verdict=gate_verdict.verdict,
                        reason=gate_verdict.reason,
                        issue_count=len(remediation_issues),
                    )

                    if gate_verdict.verdict == "HALT":
                        _emit_halt(
                            state=state,
                            observer=observer,
                            reason=(
                                f"Gate hard-block for {completed.step_id}: {gate_verdict.reason}"
                            ),
                        )
                        state.halt_requested = True
                        return

                    if gate_verdict.verdict == "REJECT":
                        if config is None or runners is None:
                            raise PlanError(
                                "GATE REJECT remediation requires config/runners wiring "
                                "for planner dispatch"
                            )
                        batch_instruction = _compose_gate_batch_instruction(
                            base_instruction=gate_verdict.planner_instruction or "",
                            step_id=completed.step_id,
                            remediation_issues=remediation_issues,
                        )
                        if not batch_instruction.strip():
                            raise PlanError(
                                "GATE REJECT verdict requires planner instruction for batched "
                                f"fix chain (step={completed.step_id})"
                            )

                        await _dispatch_registered_planner_action(
                            action=PlannerDispatchAction(
                                action_type=PLANNER_DISPATCH_GATE_REJECT_ACTION_TYPE,
                                step_id=completed.step_id,
                                trigger=GATE_REMEDIATION_CHAIN_CONTRACT.planner_trigger,
                                judgment_type="GATE",
                                planner_instruction=batch_instruction,
                                source_verdict=PLANNER_SOURCE_VERDICT_REJECT,
                            ),
                            context=_runtime_context_from_runtime(
                                state=state,
                                config=config,
                                runners=runners,
                                judge=judge,
                                session_pool=session_pool,
                                observer=observer,
                                plan_path=plan_path,
                            ),
                        )

                        claims_path = resolve_claims_path(plan_path)
                        deferred_plan = defer_step(
                            plan,
                            completed.step_id,
                            claims_path=claims_path,
                        )
                        _save_plan_with_durability(
                            deferred_plan,
                            plan_path=plan_path,
                            expected_hash=expected_hash,
                            reason=f"defer {completed.step_id}",
                        )
                        observer.emit(
                            "GATE_REMEDIATION_DEFERRED",
                            step_id=completed.step_id,
                            blocker_count=sum(
                                1
                                for issue in remediation_issues
                                if issue.severity
                                in GATE_REMEDIATION_CHAIN_CONTRACT.blocker_severities
                            ),
                            batched_issue_count=len(remediation_issues),
                        )
                        return

        async with state.merge_lock:
            merge_result = await merge(
                step_id=completed.step_id,
                worktree_path=Path(completed.worktree_path),
                cwd=plan_path.parent,
            )
            if isinstance(merge_result, Failure):
                raise merge_result.error

            if merge_result.value.outcome.value == "non_trivial_conflict":
                conflict_journal = _persist_continuity_journal(
                    repo_root=continuity_repo_root,
                    step_id=completed.step_id,
                    event_kind="merge_conflict_deferred",
                    attempt_key=continuity_attempt_key,
                    runner_name=completed.runner_name,
                    session_id=completed.result.session_id,
                    summary="Non-trivial merge conflict; step deferred for resolver flow",
                    replay_envelope=success_envelope,
                )
                _persist_continuity_ledger(
                    repo_root=continuity_repo_root,
                    step_id=completed.step_id,
                    status="deferred_conflict",
                    attempt_key=continuity_attempt_key,
                    runner_name=completed.runner_name,
                    session_id=completed.result.session_id,
                    replay_envelope=success_envelope,
                    last_journal_event=conflict_journal,
                    recovery_cursor="awaiting_conflict_resolution",
                )
                observer.emit(
                    "MERGE_CONFLICTED",
                    step_id=completed.step_id,
                    conflicting_files=list(merge_result.value.conflicted_files),
                )
                plan, expected_hash = load_plan_definition(plan_path)
                claims_path = resolve_claims_path(plan_path)
                plan = defer_step(plan, completed.step_id, claims_path=claims_path)
                _save_plan_with_durability(
                    plan,
                    plan_path=plan_path,
                    expected_hash=expected_hash,
                    reason=f"defer {completed.step_id}",
                )
                return

            auto_resolved = merge_result.value.outcome.value == "auto_resolved_conflict"
            conflicting_files = list(merge_result.value.conflicted_files)

        plan, expected_hash = load_plan_definition(plan_path)
        claims_path = resolve_claims_path(plan_path)
        plan = complete_step(plan, completed.step_id, evidence, claims_path=claims_path)
        _save_plan_with_durability(
            plan,
            plan_path=plan_path,
            expected_hash=expected_hash,
            reason=f"complete {completed.step_id}",
        )

        if completed.result.session_id:
            session_pool.record(
                step_id=completed.step_id,
                session_id=completed.result.session_id,
                runner_name=completed.runner_name,
                agent=completed.agent,
            )
            state.decide_state.record_completion(
                step_id=completed.step_id,
                task_id=completed.result.session_id,
                completed_at=time.time(),
            )

        cleanup_result = await cleanup_worktree(
            completed.step_id,
            Path(completed.worktree_path),
            cwd=plan_path.parent,
        )
        if isinstance(cleanup_result, Failure):
            _LOGGER.warning(
                "Worktree cleanup failed for %s: %s", completed.step_id, cleanup_result.error
            )

        completed_journal = _persist_continuity_journal(
            repo_root=continuity_repo_root,
            step_id=completed.step_id,
            event_kind="completed",
            attempt_key=continuity_attempt_key,
            runner_name=completed.runner_name,
            session_id=completed.result.session_id,
            summary="Step completion committed, merged, and cleaned up",
            replay_envelope=success_envelope,
        )
        _persist_continuity_ledger(
            repo_root=continuity_repo_root,
            step_id=completed.step_id,
            status="completed",
            attempt_key=continuity_attempt_key,
            runner_name=completed.runner_name,
            session_id=completed.result.session_id,
            replay_envelope=success_envelope,
            last_journal_event=completed_journal,
            recovery_cursor="terminal",
        )

        emit_step_completed(
            observer,
            step_id=completed.step_id,
            elapsed_seconds=completed.elapsed_seconds,
            evidence_len=len(evidence),
        )
        state.completed_success_count += 1
        state.record_completion_metrics(completed.result)
        observer.emit(
            "MERGE_COMPLETED",
            step_id=completed.step_id,
            auto_resolved=auto_resolved,
            conflicting_files=conflicting_files,
            elapsed_seconds=completed.elapsed_seconds,
        )
        return

    failure_envelope = _build_replay_envelope(
        step_id=completed.step_id,
        attempt_key=continuity_attempt_key,
        runner_name=completed.runner_name,
        session_id=completed.result.session_id,
        scope="reconcile_failure",
        fingerprint=None,
    )
    failure_journal = _persist_continuity_journal(
        repo_root=continuity_repo_root,
        step_id=completed.step_id,
        event_kind="failure",
        attempt_key=continuity_attempt_key,
        runner_name=completed.runner_name,
        session_id=completed.result.session_id,
        summary=completed.result.output,
        replay_envelope=failure_envelope,
    )
    emit_recovery_visibility(
        observer,
        attempt_key=continuity_attempt_key,
        event_kind="failure",
        recorded_at=failure_journal.recorded_at,
        runner_name=completed.runner_name,
        session_id=completed.result.session_id,
        step_id=completed.step_id,
        summary=failure_journal.summary,
        recovery_cursor="awaiting_retry_or_escalation",
    )
    _persist_continuity_ledger(
        repo_root=continuity_repo_root,
        step_id=completed.step_id,
        status="failed",
        attempt_key=continuity_attempt_key,
        runner_name=completed.runner_name,
        session_id=completed.result.session_id,
        replay_envelope=failure_envelope,
        last_journal_event=failure_journal,
        recovery_cursor="awaiting_retry_or_escalation",
    )
    state.completed_failure_count += 1
    state.record_completion_metrics(completed.result)

    state.runner_failures[(completed.step_id, completed.runner_name)] = (
        state.runner_failures.get((completed.step_id, completed.runner_name), 0) + 1
    )
    state.failure_history.setdefault(completed.step_id, []).append(completed.result.output)
    legacy_failure_count = state.failure_counts.get(completed.step_id, 0)
    if legacy_failure_count > state.decide_state.failure_counts.get(completed.step_id, 0):
        state.decide_state.failure_counts[completed.step_id] = legacy_failure_count
    count = state.decide_state.register_failure(step_id=completed.step_id)
    state.failure_counts[completed.step_id] = count

    plan, expected_hash = load_plan_definition(plan_path)
    claims_path = resolve_claims_path(plan_path)

    failure_step_description = ""
    found = plan.find_step(completed.step_id)
    if found is not None:
        _, failure_step = found
        failure_step_description = failure_step.description

    if _judge_type_enabled(judge, JudgmentType.FAILURE):
        remaining_checks = _collect_remaining_required_checks(plan, completed.step_id)
        failure_request = _build_failure_request(
            step_id=completed.step_id,
            error_output=completed.result.output,
            failure_count=count,
            step_description=failure_step_description,
            remaining_gates=remaining_checks,
            failure_history=state.get_failure_history(completed.step_id),
            plan_summary=str(getattr(plan, "context", "")),
        )
        fallback_runner_name: str | None = None
        if config is not None:
            fallback_value = getattr(config, "fallback_runner", None)
            if isinstance(fallback_value, str) and fallback_value.strip():
                if fallback_value != completed.runner_name:
                    fallback_runner_name = fallback_value

        policy_output: JudgeContinuityPolicyOutput
        failure_verdict: JudgmentVerdict | None = None
        disposition: str | None = None

        try:
            failure_verdict = await judge.judge(failure_request)
            disposition = _extract_failure_disposition(failure_verdict.reason)
            observer.emit(
                "FAILURE_VERDICT",
                step_id=completed.step_id,
                verdict=failure_verdict.verdict,
                reason=failure_verdict.reason,
                disposition=disposition or "",
                remaining_checks=len(remaining_checks),
            )
            policy_output = decide_judge_recovery_policy(
                JudgeRecoveryPolicyInput(
                    step_id=completed.step_id,
                    judgment_type=JudgmentType.FAILURE,
                    outcome_kind="verdict",
                    attempt_key=continuity_attempt_key,
                    failure_count=count,
                    retry_budget=2,
                    fallback_runner_name=fallback_runner_name,
                    verdict=failure_verdict,
                )
            )
        except JudgmentTimeoutError:
            policy_output = decide_judge_recovery_policy(
                JudgeRecoveryPolicyInput(
                    step_id=completed.step_id,
                    judgment_type=JudgmentType.FAILURE,
                    outcome_kind="timeout",
                    attempt_key=continuity_attempt_key,
                    failure_count=count,
                    retry_budget=2,
                    fallback_runner_name=fallback_runner_name,
                )
            )
        except JudgmentParseError as exc:
            policy_output = decide_judge_recovery_policy(
                JudgeRecoveryPolicyInput(
                    step_id=completed.step_id,
                    judgment_type=JudgmentType.FAILURE,
                    outcome_kind=_classify_judge_parse_outcome(exc.raw_output),
                    attempt_key=continuity_attempt_key,
                    failure_count=count,
                    retry_budget=2,
                    fallback_runner_name=fallback_runner_name,
                )
            )

        observer.emit("JUDGE_FAILURE_POLICY", **asdict(policy_output))

        if policy_output.action == "halt":
            policy_envelope = _build_replay_envelope(
                step_id=completed.step_id,
                attempt_key=continuity_attempt_key,
                runner_name=completed.runner_name,
                session_id=completed.result.session_id,
                scope="judge_failure_policy",
                fingerprint=policy_output.judge_outcome,
            )
            policy_journal = _persist_continuity_journal(
                repo_root=continuity_repo_root,
                step_id=completed.step_id,
                event_kind="judge_failure_policy",
                attempt_key=continuity_attempt_key,
                runner_name=completed.runner_name,
                session_id=completed.result.session_id,
                summary=(policy_output.halt_reason or policy_output.continuity_recovery_reason),
                replay_envelope=policy_envelope,
            )
            _persist_continuity_ledger(
                repo_root=continuity_repo_root,
                step_id=completed.step_id,
                status="failed",
                attempt_key=continuity_attempt_key,
                runner_name=completed.runner_name,
                session_id=completed.result.session_id,
                replay_envelope=policy_envelope,
                last_journal_event=policy_journal,
                judge_policy=policy_output,
                recovery_cursor="halt_requested_by_judge_policy",
            )

        if failure_verdict is None:
            if policy_output.action == "fallback":
                state.runner_failures[(completed.step_id, completed.runner_name)] = max(
                    state.runner_failures.get((completed.step_id, completed.runner_name), 0),
                    2,
                )
            if policy_output.action == "halt":
                state.halt_requested = True
                state.final_halt_reason = (
                    policy_output.halt_reason or policy_output.continuity_recovery_reason
                )
                observer.emit(
                    "HALT_REQUESTED",
                    step_id=completed.step_id,
                    reason=policy_output.halt_reason or policy_output.continuity_recovery_reason,
                )

            plan = defer_step(plan, completed.step_id, claims_path=claims_path)
            _save_plan_with_durability(
                plan,
                plan_path=plan_path,
                expected_hash=expected_hash,
                reason=f"defer {completed.step_id}",
            )
            observer.emit(
                "STEP_FAILED",
                step_id=completed.step_id,
                failure_type=completed.result.status.value,
                attempt=count,
                error=completed.result.output,
                judge_policy_action=policy_output.action,
                judge_recovery_reason=policy_output.continuity_recovery_reason,
            )
            return

        requires_planner = (
            policy_output.action == "planner_remediation" or disposition == "downstream_blocker"
        )
        if requires_planner:
            if config is None or runners is None:
                raise PlanError(
                    "FAILURE downstream-blocker remediation requires config/runners wiring "
                    "for planner dispatch"
                )
            planner_instruction = (
                policy_output.planner_instruction or failure_verdict.planner_instruction
            )
            if planner_instruction is None or not planner_instruction.strip():
                raise PlanError(
                    "FAILURE downstream_blocker/REPLAN verdict requires non-empty "
                    f"planner_instruction (step={completed.step_id})"
                )

            await _dispatch_registered_planner_action(
                action=PlannerDispatchAction(
                    action_type=PLANNER_DISPATCH_REPLAN_ACTION_TYPE,
                    step_id=completed.step_id,
                    trigger="reconcile.failure_classification_replan",
                    judgment_type="FAILURE",
                    planner_instruction=planner_instruction,
                    source_verdict=PLANNER_SOURCE_VERDICT_REPLAN,
                ),
                context=_runtime_context_from_runtime(
                    state=state,
                    config=config,
                    runners=runners,
                    judge=judge,
                    session_pool=session_pool,
                    observer=observer,
                    plan_path=plan_path,
                ),
            )
            deferred_plan = defer_step(plan, completed.step_id, claims_path=claims_path)
            _save_plan_with_durability(
                deferred_plan,
                plan_path=plan_path,
                expected_hash=expected_hash,
                reason=f"defer {completed.step_id}",
            )
            observer.emit(
                "FAILURE_REMEDIATION_DEFERRED",
                step_id=completed.step_id,
                verdict=failure_verdict.verdict,
                disposition=disposition or "",
            )
            return

    if count < 3:
        plan = defer_step(plan, completed.step_id, claims_path=claims_path)
        _save_plan_with_durability(
            plan,
            plan_path=plan_path,
            expected_hash=expected_hash,
            reason=f"defer {completed.step_id}",
        )
        observer.emit(
            "STEP_FAILED",
            step_id=completed.step_id,
            failure_type=completed.result.status.value,
            attempt=count,
            error=completed.result.output,
        )
        return

    escalation_step_description = failure_step_description

    escalation_request = _build_escalation_request(
        step_id=completed.step_id,
        failure_count=count,
        failure_history=state.get_failure_history(completed.step_id),
        step_description=escalation_step_description,
        available_agents=[completed.agent],
        plan_summary=plan.context,
    )
    escalation_verdict = await judge.judge(escalation_request)
    observer.emit(
        "ESCALATION_VERDICT",
        step_id=completed.step_id,
        attempt=count,
        verdict=escalation_verdict.verdict,
        reason=escalation_verdict.reason,
    )

    observer.emit(
        "ESCALATION_DEFERRED",
        step_id=completed.step_id,
        attempt=count,
        reason=(
            "Escalation verdict handling beyond threshold remains deferred to "
            "driver-judgment-expansion-replan"
        ),
        verdict=escalation_verdict.verdict,
        deferred_branches=[branch.branch for branch in DEFERRED_REPLAN_BRANCHES],
    )


async def shutdown(state: DriverState, observer: Observer) -> None:
    """Graceful shutdown sequence.

    Contract pin from Section 2.11:
    1. Preserve the caller-established terminal state (normal completion vs halt).
    2. Wait for running handles up to configured timeout bounds.
    3. Kill any remaining processes.
    4. Clean up orphan worktrees.
    5. Emit a FINAL event using ``state.summary()``.
    6. Close the observer.

    The exact orchestration order is locked by ``GRACEFUL_SHUTDOWN_CONTRACT``;
    implementation details are deferred.
    """
    running_entries = list(state.running.values())
    for entry in running_entries:
        abort_attempt_key = _continuity_attempt_key(
            step_id=entry.step_id,
            runner_name=entry.runner_name,
            session_id=entry.handle.session_id,
        )
        abort_envelope = _build_replay_envelope(
            step_id=entry.step_id,
            attempt_key=abort_attempt_key,
            runner_name=entry.runner_name,
            session_id=entry.handle.session_id,
            scope="shutdown_abort",
            fingerprint=None,
        )
        abort_repo_root = _resolve_repo_root_from_worktree_path(entry.worktree_path)
        abort_journal = _persist_continuity_journal(
            repo_root=abort_repo_root,
            step_id=entry.step_id,
            event_kind="shutdown_abort",
            attempt_key=abort_attempt_key,
            runner_name=entry.runner_name,
            session_id=entry.handle.session_id,
            summary="Driver shutdown while step still running",
            replay_envelope=abort_envelope,
        )
        _persist_continuity_ledger(
            repo_root=abort_repo_root,
            step_id=entry.step_id,
            status="aborted",
            attempt_key=abort_attempt_key,
            runner_name=entry.runner_name,
            session_id=entry.handle.session_id,
            replay_envelope=abort_envelope,
            last_journal_event=abort_journal,
            recovery_cursor="shutdown_abort",
        )

        with contextlib.suppress(Exception):
            await asyncio.wait_for(entry.handle.wait(timeout=0.1), timeout=0.1)

    for entry in running_entries:
        if entry.handle.is_alive():
            with contextlib.suppress(Exception):
                await entry.handle.kill()

    for step_id, entry in list(state.running.items()):
        cleanup_result = await cleanup_worktree(step_id, Path(entry.worktree_path))
        if isinstance(cleanup_result, Failure):
            _LOGGER.warning(
                "Worktree cleanup failed during shutdown for %s: %s", step_id, cleanup_result.error
            )

    final_summary = state.summary()
    emit_final(
        observer,
        completed_summary=cast(dict[str, object], final_summary["completed_summary"]),
        total_duration_seconds=cast(float, final_summary["total_duration_seconds"]),
        halt_reason=cast(str | None, final_summary.get("halt_reason")),
        total_cost_usd=cast(float | None, final_summary.get("total_cost_usd")),
        total_tokens=cast(int | None, final_summary.get("total_tokens")),
    )
    observer.close()


def _load_runtime_config(config_path: Path) -> DriverConfig:
    """Load runtime config, with a plan-path compatibility fallback.

    Args:
        config_path: Path supplied to ``run``.

    Returns:
        A validated ``DriverConfig``.

    Raises:
        ConfigError: If no valid runtime configuration can be derived.
    """
    if not config_path.exists():
        raise ConfigError(f"Configuration file not found: {config_path}")

    try:
        return load_config(config_path)
    except ConfigError as config_error:
        plan, _ = _try_load_plan(config_path)
        if plan is None:
            raise config_error
        return _default_config_for_plan(config_path)


def _try_load_plan(path: Path) -> tuple[object | None, str | None]:
    """Try loading a path as plan.yaml, returning ``(plan, hash)`` on success."""
    try:
        return load_plan_definition(path)
    except Exception:
        return (None, None)


def _default_config_for_plan(plan_path: Path) -> DriverConfig:
    """Build a minimal single-runner config anchored to an explicit plan path."""
    return DriverConfig(
        plan_path=str(plan_path),
        runners={
            "opencode": RunnerConfig(
                command="opencode",
                args=["run", "--format", "json"],
                prompt_mode="stdin_dash",
                stall_timeout=300,
                output_parser="opencode_jsonl",
            )
        },
        fallback_runner="opencode",
        orchestration=OrchestrationConfig(max_parallelism=1),
        session=SessionConfig(reuse_ttl=300),
        judge=JudgeConfig(preflight=False),
        observability=ObservabilityConfig(),
    )


def _all_plan_step_ids(plan: object) -> set[str]:
    """Collect step IDs from a plan-like object."""
    phases = getattr(plan, "phases", [])
    return {step.id for phase in phases for step in phase.steps}


def _cleanup_orphan_worktrees(*, plan_path: Path, plan_step_ids: set[str]) -> tuple[str, ...]:
    """Delete stale step directories that do not map to any plan step.

    Args:
        plan_path: Path to plan.yaml used for deriving worktree base path.
        plan_step_ids: Set of current step IDs in the loaded plan.
    """
    base_dir = plan_path.parent / ".vectl" / "worktrees"
    if not base_dir.exists() or not base_dir.is_dir():
        return ()

    removed: list[str] = []

    for candidate in base_dir.iterdir():
        if not candidate.is_dir():
            continue
        if candidate.name in plan_step_ids:
            continue
        shutil.rmtree(candidate, ignore_errors=True)
        removed.append(candidate.name)

    return tuple(sorted(removed))


def _serialize_actions_for_loop_guard(actions: list[Action]) -> str:
    """Serialize actions to a stable loop-signature string."""
    parts: list[str] = []
    for action in actions:
        parts.append(
            "|".join(
                [
                    action.action,
                    action.step_id or "",
                    action.agent or "",
                    action.session or "",
                    action.task_id or "",
                ]
            )
        )
    return ";".join(parts)


def _all_running_handles_dead(state: DriverState) -> bool:
    """Return True when all tracked running handles are not alive."""
    if not state.running:
        return False
    return all(not entry.handle.is_alive() for entry in state.running.values())


async def _recover_all_stalled(state: DriverState, observer: Observer) -> None:
    """Kill any residual handles and clear running map after full stall."""
    for step_id, entry in list(state.running.items()):
        with contextlib.suppress(Exception):
            await entry.handle.kill()
        state.running.pop(step_id, None)
    observer.emit("RECOVERY", type="all_stalled")


async def _registry_handle_dispatch(action: Action, context: RuntimeContext) -> None:
    """Dispatch one execution action through ``handle_dispatch``."""

    await handle_dispatch(action=action, context=context)


async def _registry_emit_complete_action_ignored(action: Action, context: RuntimeContext) -> None:
    """Emit complete-action compatibility event per registry declaration."""

    context.observer.emit(
        "COMPLETE_ACTION_IGNORED",
        step_id=action.step_id,
        reason=(
            "Runtime completion authority is reconcile-only; "
            "decide(action='complete') is legacy and ignored"
        ),
    )


async def _registry_emit_wait(action: Action, context: RuntimeContext) -> None:
    """Emit wait event per registry declaration."""

    context.observer.emit("WAIT", reason=action.reason or "No action")


async def _registry_emit_escalation_deferred(action: Action, context: RuntimeContext) -> None:
    """Emit escalation deferred event per registry declaration."""

    context.observer.emit(
        "ESCALATION_DEFERRED",
        step_id=action.step_id,
        reason="Escalation handling beyond threshold is deferred",
    )


async def _registry_recover_all_stalled(
    action: Action,
    context: RuntimeContext,
) -> None:
    """Execute all-stalled recovery via registry-declared recovery handler."""

    _ = action
    await _recover_all_stalled(state=context.state, observer=context.observer)


async def _registry_dispatch_planner_replan(
    action: PlannerDispatchAction,
    context: RuntimeContext,
) -> None:
    """Dispatch planner action for REPLAN-triggered branches."""

    await dispatch_planner(
        PlannerDispatchRequest(
            step_id=action.step_id,
            trigger=action.trigger,
            judgment_type=action.judgment_type,
            planner_instruction=action.planner_instruction,
            source_verdict=action.source_verdict,
        ),
        config=context.config,
        runners=context.runners,
        observer=context.observer,
        plan_path=context.plan_path,
    )


async def _registry_dispatch_planner_gate_reject(
    action: PlannerDispatchAction,
    context: RuntimeContext,
) -> None:
    """Dispatch planner action for gate-reject remediation branches."""

    await dispatch_planner(
        PlannerDispatchRequest(
            step_id=action.step_id,
            trigger=action.trigger,
            judgment_type=action.judgment_type,
            planner_instruction=action.planner_instruction,
            source_verdict=action.source_verdict,
        ),
        config=context.config,
        runners=context.runners,
        observer=context.observer,
        plan_path=context.plan_path,
    )


def _runtime_context_from_runtime(
    *,
    state: DriverState,
    config: DriverConfig,
    runners: Mapping[str, Runner],
    judge: Judge,
    session_pool: SessionPool,
    observer: Observer,
    plan_path: Path,
) -> RuntimeContext:
    """Build the single authoritative RuntimeContext for loop-owned runtime inputs."""

    return runtime_context_from_parts(
        state=state,
        config=config,
        runners=runners,
        judge=judge,
        session_pool=session_pool,
        observer=observer,
        plan_path=plan_path,
    )


_REGISTERED_PLANNER_ACTION_HANDLERS: Final[
    MappingProxyType[
        RegisteredPlannerDispatchHandlerName,
        Callable[[PlannerDispatchAction, RuntimeContext], Awaitable[None]],
    ]
] = MappingProxyType(
    {
        PLANNER_DISPATCH_REPLAN_HANDLER_CONTRACT: _registry_dispatch_planner_replan,
        PLANNER_DISPATCH_GATE_REJECT_HANDLER_CONTRACT: _registry_dispatch_planner_gate_reject,
    }
)


async def _dispatch_registered_planner_action(
    *,
    action: PlannerDispatchAction,
    context: RuntimeContext,
) -> None:
    """Execute planner dispatch via explicit planner layer registry."""

    try:
        declaration = resolve_planner_dispatch_action_declaration(action.action_type)
    except UnknownPlannerDispatchActionError as exc:
        raise PlanError(str(exc)) from exc

    if declaration.category != "planner":
        raise PlanError(
            "Planner dispatch action resolved to non-planner category: "
            f"action_type={declaration.action_type}, category={declaration.category}"
        )
    if declaration.runtime_context_contract != "RuntimeContext":
        raise PlanError(
            "Planner dispatch registry declaration must use RuntimeContext: "
            f"action_type={declaration.action_type}, "
            f"runtime_context_contract={declaration.runtime_context_contract}"
        )

    handler_name = cast(RegisteredPlannerDispatchHandlerName, declaration.handler_contract)
    handler = _REGISTERED_PLANNER_ACTION_HANDLERS.get(handler_name)
    if handler is None:
        raise PlanError(
            "Planner action registry handler is not wired: "
            f"action_type={declaration.action_type}, handler={declaration.handler_contract}"
        )

    await handler(action, context)


_REGISTERED_RUNTIME_ACTION_HANDLERS: Final[
    MappingProxyType[
        RegisteredLoopActionHandlerName,
        Callable[[Action, RuntimeContext], Awaitable[None]],
    ]
] = MappingProxyType(
    {
        "handle_dispatch": _registry_handle_dispatch,
        "emit_complete_action_ignored": _registry_emit_complete_action_ignored,
        "emit_wait": _registry_emit_wait,
        "emit_escalation_deferred": _registry_emit_escalation_deferred,
        "recover_all_stalled": _registry_recover_all_stalled,
    }
)


async def _dispatch_registered_runtime_action(
    *,
    action: Action,
    context: RuntimeContext,
) -> None:
    """Execute runtime/control/recovery actions via runtime action registry."""

    try:
        declaration = resolve_runtime_loop_action_declaration(action.action)
    except UnknownLoopActionError as exc:
        raise PlanError(str(exc)) from exc

    if declaration.runtime_context_contract != "RuntimeContext":
        raise PlanError(
            "Loop action registry declaration must use RuntimeContext: "
            f"action_type={declaration.action_type}, "
            f"runtime_context_contract={declaration.runtime_context_contract}"
        )

    handler_name = cast(RegisteredLoopActionHandlerName, declaration.handler_contract)
    handler = _REGISTERED_RUNTIME_ACTION_HANDLERS.get(handler_name)
    if handler is None:
        raise PlanError(
            "Loop action registry handler is not wired: "
            f"action_type={declaration.action_type}, handler={declaration.handler_contract}"
        )

    await handler(action, context)


async def _run_main_loop(
    *,
    state: DriverState,
    config: DriverConfig,
    runners: Mapping[str, Runner],
    judge: Judge,
    session_pool: SessionPool,
    observer: Observer,
    plan_path: Path,
) -> None:
    """Execute the deterministic decide/dispatch/wait/reconcile loop.

    Completion authority note (A1 contract pin):
    - runtime convergence target is a single completion sink
      ``wait_for_any() -> reconcile()``
    - ``handle_complete()`` is legacy/internal compatibility only and should be
      removed from the main runtime path by
      ``driver-debt-completion-authority.impl``
    """
    runtime_context = _runtime_context_from_runtime(
        state=state,
        config=config,
        runners=runners,
        judge=judge,
        session_pool=session_pool,
        observer=observer,
        plan_path=plan_path,
    )

    loop_iteration = 0
    while not state.halt_requested:
        loop_iteration += 1
        decide_output = decide(
            running_tasks=state.as_running_tasks(),
            completed_results=None,
            max_parallelism=config.orchestration.max_parallelism,
            state=state.decide_state,
        )
        emit_decide(
            observer,
            running_count=len(state.running),
            actions=[action.action for action in decide_output.actions],
        )
        emit_heartbeat_progress(
            observer,
            completed_count=state.completed_success_count + state.completed_failure_count,
            loop_iteration=loop_iteration,
            running_count=len(state.running),
            waiting_count=0,
            active_step_ids=sorted(state.running.keys()),
        )

        state.loop_detector.append(_serialize_actions_for_loop_guard(decide_output.actions))
        if state.detect_loop():
            _emit_halt(state=state, observer=observer, reason="SUSPECTED_INFINITE_LOOP")
            state.halt_requested = True
            break

        for action in decide_output.actions:
            await _dispatch_registered_runtime_action(action=action, context=runtime_context)

        if state.running:
            completed = await state.wait_for_any()
            await reconcile(
                completed=completed,
                context=runtime_context,
            )

            if completed.result.status == RunnerStatus.STALL and _all_running_handles_dead(state):
                await _dispatch_registered_runtime_action(
                    action=Action.model_construct(
                        action=RECOVERY_ALL_STALLED_ACTION_TYPE,
                        reason="all_running_handles_dead_after_stall",
                    ),
                    context=runtime_context,
                )

        if not decide_output.continuation and not state.running:
            break


__all__ = [
    "COMPLETION_AUTHORITY_A1_CONTRACT",
    "INITIAL_HANDLER_INPUT_CONTRACT",
    "CompletionAuthorityContract",
    "CompletionSinkName",
    "CONFLICT_RESOLVER_READINESS_CONTRACT",
    "ConflictResolverDispatcher",
    "ConflictResolverReadinessContract",
    "ConflictResolverTriggerName",
    "DEFERRED_REPLAN_BRANCHES",
    "ROLE_SPECIFIC_CONTEXTS_DEFERRED",
    "RUNTIME_CONTEXT_ROLLOUT_CONTRACT",
    "GATE_REMEDIATION_CHAIN_CONTRACT",
    "GateRemediationChainContract",
    "GateRemediationTriggerName",
    "GRACEFUL_SHUTDOWN_CONTRACT",
    "LoopSurfaceName",
    "LegacyCompleteShimDisposition",
    "PlannerDispatchContract",
    "PlannerDispatchRequest",
    "PlannerDispatcher",
    "RECONCILE_CONTEXT_ONLY_CONTRACT",
    "ReconcileContextOnlyContract",
    "REMAINING_JUDGMENT_RUNTIME_CONTRACTS",
    "REPLAN_PLANNER_WIRING",
    "RemainingJudgmentRuntimeContract",
    "RemainingJudgmentTriggerName",
    "ReplanTriggerName",
    "GracefulShutdownContract",
    "DeferredRuntimeBranch",
    "LOOP_ACTION_REGISTRY_SCOPE_STATEMENT",
    "LOOP_HANDLER_RUNTIME_CONTEXT",
    "LOOP_PLANNER_ACTION_DECLARATIONS",
    "LOOP_EVENT_HELPER_DEFS",
    "STARTUP_RECOVERY_CONTRACT",
    "RUNNER_RECOVERY_TAXONOMY",
    "StartupRecoveryContract",
    "evaluate_runner_recovery_boundary",
    "dispatch_planner",
    "emit_decide",
    "emit_final",
    "emit_step_completed",
    "handle_complete",
    "handle_dispatch",
    "reconcile",
    "run",
    "shutdown",
]
