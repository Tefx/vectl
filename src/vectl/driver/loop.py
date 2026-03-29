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
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from fnmatch import fnmatch
from pathlib import Path
from typing import TYPE_CHECKING, Final, Literal, Protocol, cast

from vectl.claims import repair_claims
from vectl.decide import decide
from vectl.io import load_plan_definition, save_plan
from vectl.lifecycle import claim_step, complete_step, defer_step
from vectl.models import PlanError
from vectl.plan_path import resolve_claims_path, resolve_plan_path

from .config import (
    DriverConfig,
    JudgeConfig,
    ObservabilityConfig,
    OrchestrationConfig,
    RunnerConfig,
    SessionConfig,
    load_config,
)
from .dispatch import render_prompt
from .errors import ConfigError, RunnerError
from .judge import Judge
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
from .session import SessionPool
from .types import (
    CompletedEntry,
    ContinuityJournalEntry,
    ContinuityLedgerEntry,
    DriverState,
    ReplaySafetyEnvelope,
    ReplayTokenSemantics,
    RunnerStatus,
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
    from vectl.models import Action

    from .config import DriverConfig
    from .judge import Judge
    from .observe import Observer
    from .runners import Runner
    from .session import SessionPool


_LOGGER = logging.getLogger(__name__)


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
        runners: dict[str, Runner],
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
        runners: dict[str, Runner],
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
    runners: dict[str, Runner],
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

    planner_agent = "vectl-planner"
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

    prompt = _render_planner_prompt(request=request, plan_path=plan_path)
    handle = await runner.dispatch(
        prompt=prompt,
        agent=planner_agent,
        workdir=str(plan_path.parent),
    )
    result = await handle.wait()

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


def _render_planner_prompt(*, request: PlannerDispatchRequest, plan_path: Path) -> str:
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
            "You are vectl-planner. Apply the requested replanning change.",
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

    existing = getattr(state, "_continuity_attempt_keys", None)
    if isinstance(existing, set):
        return existing
    fresh: set[str] = set()
    state.__dict__["_continuity_attempt_keys"] = fresh
    return fresh


def _state_capability_snapshot_cache(state: DriverState) -> dict[str, str]:
    """Return last-writer-wins capability snapshot cache by step."""

    existing = getattr(state, "_continuity_capability_snapshots", None)
    if isinstance(existing, dict):
        return existing
    fresh: dict[str, str] = {}
    state.__dict__["_continuity_capability_snapshots"] = fresh
    return fresh


def _resolve_repo_root_from_plan(plan_path: Path) -> Path:
    """Resolve repository root from plan path."""

    plan_abs = plan_path if plan_path.is_absolute() else (Path.cwd() / plan_path)
    return plan_abs.resolve().parent


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

    try:
        runners = {
            name: create_runner(name, runner_cfg) for name, runner_cfg in config.runners.items()
        }
        session_pool = SessionPool(config.session)
        judge = Judge(config.judge, observer)

        plan, _ = load_plan_definition(plan_path)
        plan_step_ids = _all_plan_step_ids(plan)
        claims_path = resolve_claims_path(plan_path)
        repair_result = repair_claims(plan, plan_path, claims_path)
        removed_orphans = _cleanup_orphan_worktrees(
            plan_path=plan_path,
            plan_step_ids=plan_step_ids,
        )

        continuity_repo_root = _resolve_repo_root_from_plan(plan_path)
        orphaned_ledger_steps = sorted(_load_ledger_step_ids(continuity_repo_root) - plan_step_ids)
        if orphaned_ledger_steps:
            observer.emit(
                "HALT",
                reason=(
                    "Continuity ledger mismatch with recovered plan/claims state: "
                    f"orphaned_steps={','.join(orphaned_ledger_steps)}"
                ),
            )
            state.halt_requested = True

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
                    observer.emit(
                        "HALT",
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
                    await dispatch_planner(
                        PlannerDispatchRequest(
                            step_id="startup.recovery",
                            trigger="run.startup_recovery_anomaly_replan",
                            judgment_type="ANOMALY",
                            planner_instruction=anomaly_verdict.planner_instruction,
                        ),
                        config=config,
                        runners=runners,
                        observer=observer,
                        plan_path=plan_path,
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
        await shutdown(state=state, observer=observer)


async def handle_dispatch(
    action: Action,
    state: DriverState,
    config: DriverConfig,
    runners: dict[str, Runner],
    judge: Judge,
    session_pool: SessionPool,
    observer: Observer,
    plan_path: Path,
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
                    await dispatch_planner(
                        planner_request,
                        config=config,
                        runners=runners,
                        observer=observer,
                        plan_path=plan_path,
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

    save_plan(
        plan,
        path=plan_path,
        expected_hash=expected_hash,
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
    state: DriverState,
    judge: Judge,
    session_pool: SessionPool,
    observer: Observer,
    plan_path: Path,
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

    evidence = action.evidence or ""
    plan, expected_hash = load_plan_definition(plan_path)
    claims_path = resolve_claims_path(plan_path)
    plan = complete_step(plan, action.step_id, evidence, claims_path=claims_path)
    save_plan(plan, path=plan_path, expected_hash=expected_hash)

    observer.emit(
        "STEP_COMPLETED",
        step_id=action.step_id,
        evidence_len=len(evidence),
    )


async def reconcile(
    completed: CompletedEntry,
    state: DriverState,
    judge: Judge,
    session_pool: SessionPool,
    observer: Observer,
    plan_path: Path,
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
        runtime_config = cast(DriverConfig | None, state.runtime_config)
        runtime_runners = cast(dict[str, Runner] | None, state.runtime_runners)
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
                        observer.emit(
                            "HALT",
                            reason=(
                                f"Gate hard-block for {completed.step_id}: {gate_verdict.reason}"
                            ),
                        )
                        state.halt_requested = True
                        return

                    if gate_verdict.verdict == "REJECT":
                        if runtime_config is None or runtime_runners is None:
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

                        await dispatch_planner(
                            PlannerDispatchRequest(
                                step_id=completed.step_id,
                                trigger=GATE_REMEDIATION_CHAIN_CONTRACT.planner_trigger,
                                judgment_type="GATE",
                                planner_instruction=batch_instruction,
                                source_verdict=PLANNER_SOURCE_VERDICT_REJECT,
                            ),
                            config=runtime_config,
                            runners=runtime_runners,
                            observer=observer,
                            plan_path=plan_path,
                        )

                        claims_path = resolve_claims_path(plan_path)
                        deferred_plan = defer_step(
                            plan,
                            completed.step_id,
                            claims_path=claims_path,
                        )
                        save_plan(deferred_plan, path=plan_path, expected_hash=expected_hash)
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

        claims_path = resolve_claims_path(plan_path)
        plan = complete_step(plan, completed.step_id, evidence, claims_path=claims_path)
        save_plan(plan, path=plan_path, expected_hash=expected_hash)

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
                save_plan(plan, path=plan_path, expected_hash=expected_hash)
                return

            auto_resolved = merge_result.value.outcome.value == "auto_resolved_conflict"
            conflicting_files = list(merge_result.value.conflicted_files)

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

        observer.emit(
            "STEP_COMPLETED",
            step_id=completed.step_id,
            evidence_len=len(evidence),
            elapsed_seconds=completed.elapsed_seconds,
        )
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

    runtime_config = cast(DriverConfig | None, state.runtime_config)
    runtime_runners = cast(dict[str, Runner] | None, state.runtime_runners)

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

        requires_planner = (
            failure_verdict.verdict == "REPLAN" or disposition == "downstream_blocker"
        )
        if requires_planner:
            if runtime_config is None or runtime_runners is None:
                raise PlanError(
                    "FAILURE downstream-blocker remediation requires config/runners wiring "
                    "for planner dispatch"
                )
            if (
                failure_verdict.planner_instruction is None
                or not failure_verdict.planner_instruction.strip()
            ):
                raise PlanError(
                    "FAILURE downstream_blocker/REPLAN verdict requires non-empty "
                    f"planner_instruction (step={completed.step_id})"
                )

            await dispatch_planner(
                PlannerDispatchRequest(
                    step_id=completed.step_id,
                    trigger="reconcile.failure_classification_replan",
                    judgment_type="FAILURE",
                    planner_instruction=failure_verdict.planner_instruction,
                    source_verdict=PLANNER_SOURCE_VERDICT_REPLAN,
                ),
                config=runtime_config,
                runners=runtime_runners,
                observer=observer,
                plan_path=plan_path,
            )
            deferred_plan = defer_step(plan, completed.step_id, claims_path=claims_path)
            save_plan(deferred_plan, path=plan_path, expected_hash=expected_hash)
            observer.emit(
                "FAILURE_REMEDIATION_DEFERRED",
                step_id=completed.step_id,
                verdict=failure_verdict.verdict,
                disposition=disposition or "",
            )
            return

    if count < 3:
        plan = defer_step(plan, completed.step_id, claims_path=claims_path)
        save_plan(plan, path=plan_path, expected_hash=expected_hash)
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
    1. Set ``state.halt_requested = True``.
    2. Wait for running handles up to configured timeout bounds.
    3. Kill any remaining processes.
    4. Clean up orphan worktrees.
    5. Emit a FINAL event using ``state.summary()``.
    6. Close the observer.

    The exact orchestration order is locked by ``GRACEFUL_SHUTDOWN_CONTRACT``;
    implementation details are deferred.
    """
    state.halt_requested = True

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

    observer.emit("FINAL", **state.summary())
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


async def _run_main_loop(
    *,
    state: DriverState,
    config: DriverConfig,
    runners: dict[str, Runner],
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
    state.runtime_config = config
    state.runtime_runners = runners

    while not state.halt_requested:
        decide_output = decide(
            running_tasks=state.as_running_tasks(),
            completed_results=None,
            max_parallelism=config.orchestration.max_parallelism,
            state=state.decide_state,
        )
        observer.emit(
            "DECIDE",
            running_count=len(state.running),
            actions=[action.action for action in decide_output.actions],
        )

        state.loop_detector.append(_serialize_actions_for_loop_guard(decide_output.actions))
        if state.detect_loop():
            observer.emit("HALT", reason="SUSPECTED_INFINITE_LOOP")
            state.halt_requested = True
            break

        for action in decide_output.actions:
            if action.action == "claim_and_dispatch":
                await handle_dispatch(
                    action=action,
                    state=state,
                    config=config,
                    runners=runners,
                    judge=judge,
                    session_pool=session_pool,
                    observer=observer,
                    plan_path=plan_path,
                )
            elif action.action == "complete":
                observer.emit(
                    "COMPLETE_ACTION_IGNORED",
                    step_id=action.step_id,
                    reason=(
                        "Runtime completion authority is reconcile-only; "
                        "decide(action='complete') is legacy and ignored"
                    ),
                )
            elif action.action == "wait":
                observer.emit("WAIT", reason=action.reason or "No action")
            elif action.action == "escalate":
                observer.emit(
                    "ESCALATION_DEFERRED",
                    step_id=action.step_id,
                    reason="Escalation handling beyond threshold is deferred",
                )

        if state.running:
            completed = await state.wait_for_any()
            await reconcile(
                completed=completed,
                state=state,
                judge=judge,
                session_pool=session_pool,
                observer=observer,
                plan_path=plan_path,
            )

            if completed.result.status == RunnerStatus.STALL and _all_running_handles_dead(state):
                await _recover_all_stalled(state, observer)

        if not decide_output.continuation and not state.running:
            break


__all__ = [
    "COMPLETION_AUTHORITY_A1_CONTRACT",
    "CompletionAuthorityContract",
    "CompletionSinkName",
    "CONFLICT_RESOLVER_READINESS_CONTRACT",
    "ConflictResolverDispatcher",
    "ConflictResolverReadinessContract",
    "ConflictResolverTriggerName",
    "DEFERRED_REPLAN_BRANCHES",
    "GATE_REMEDIATION_CHAIN_CONTRACT",
    "GateRemediationChainContract",
    "GateRemediationTriggerName",
    "GRACEFUL_SHUTDOWN_CONTRACT",
    "LoopSurfaceName",
    "LegacyCompleteShimDisposition",
    "PlannerDispatchContract",
    "PlannerDispatchRequest",
    "PlannerDispatcher",
    "REMAINING_JUDGMENT_RUNTIME_CONTRACTS",
    "REPLAN_PLANNER_WIRING",
    "RemainingJudgmentRuntimeContract",
    "RemainingJudgmentTriggerName",
    "ReplanTriggerName",
    "GracefulShutdownContract",
    "DeferredRuntimeBranch",
    "STARTUP_RECOVERY_CONTRACT",
    "StartupRecoveryContract",
    "dispatch_planner",
    "handle_complete",
    "handle_dispatch",
    "reconcile",
    "run",
    "shutdown",
]
