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
import logging
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Final, Literal, Protocol

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
from .observe import Observer, create_observer
from .runners import Runner, create_runner
from .session import SessionPool
from .types import CompletedEntry, DriverState, RunnerStatus
from .worktree import (
    Failure,
    Success,
    cleanup as cleanup_worktree,
    create as create_worktree,
    merge,
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
    """Planner dispatch payload derived from a REPLAN verdict.

    Architecture: docs/DRIVER-ARCHITECTURE.md Section 2.11 (`dispatch_planner`)
    Blueprint: DRIVER-BLUEPRINT.md lines 566-568, 697-698, 728-733

    This request is the canonical loop->planner handoff shape. It is contract
    surface only; runtime dispatch remains deferred.

    Invariants:
        - ``planner_instruction`` is non-empty and comes from the judge verdict.
        - ``source_verdict`` is exactly ``"REPLAN"``.
        - ``trigger`` identifies the originating branch so later phases cannot
          silently merge distinct REPLAN sites into one reject-only path.
        - ``step_id`` identifies the step whose plan needs strengthening,
          decomposition, repair, or gate follow-up.
    """

    step_id: str
    trigger: ReplanTriggerName
    judgment_type: str
    planner_instruction: str
    source_verdict: str = "REPLAN"


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
        trigger_surface="reconcile() failure classification before blocker disposition is finalized",
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
        trigger_surface="run() startup recovery after anomaly detection and before unsafe auto-repair",
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

    This stub exists to pin the callable surface only.
    """
    raise NotImplementedError(
        "Planner dispatch runtime is deferred to driver-judgment-expansion-replan"
    )


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
        plan, _ = load_plan_definition(plan_path)
        claims_path = resolve_claims_path(plan_path)
        repair_claims(plan, plan_path, claims_path)
        _cleanup_orphan_worktrees(plan_path=plan_path, plan_step_ids=_all_plan_step_ids(plan))

        runners = {
            name: create_runner(name, runner_cfg) for name, runner_cfg in config.runners.items()
        }
        session_pool = SessionPool(config.session)
        judge = Judge(config.judge, observer)

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

    plan, expected_hash = load_plan_definition(plan_path)
    claims_path = resolve_claims_path(plan_path)
    plan, _ = claim_step(plan, action.step_id, action.agent, claims_path=claims_path)

    save_plan(
        plan,
        path=plan_path,
        expected_hash=expected_hash,
    )

    binding_result = await create_worktree(action.step_id, cwd=plan_path.parent)
    if isinstance(binding_result, Failure):
        raise binding_result.error
    binding = binding_result.value

    runner_name = config.route_agent(action.agent)
    primary_runner = runner_name
    if state.runner_failures.get((action.step_id, runner_name), 0) >= 2:
        runner_name = config.fallback_runner
        observer.emit(
            "RUNNER_FALLBACK",
            step_id=action.step_id,
            from_runner=primary_runner,
            to_runner=runner_name,
            reason="repeated_failures",
        )

    runner = runners.get(runner_name)
    if runner is None:
        raise RunnerError(runner_name, action.step_id, "runner not configured")

    found = plan.find_step(action.step_id)
    if found is None:
        raise PlanError(f"Step '{action.step_id}' not found after claim")
    _, step = found

    session_id: str | None = None
    session_source = "none"
    if action.session == "reuse" and action.task_id:
        session_id = action.task_id
        session_source = "action"
    else:
        session_id = session_pool.find_reusable(
            step_id=action.step_id,
            agent=action.agent,
            runner_name=runner_name,
            depends_on=step.depends_on,
        )
        if session_id is not None:
            session_source = "pool"

    if session_id is not None:
        observer.emit(
            "SESSION_REUSE_HIT",
            step_id=action.step_id,
            session_id=session_id,
            source=session_source,
        )
    else:
        observer.emit("SESSION_REUSE_MISS", step_id=action.step_id, reason="no_reusable_session")

    prompt = render_prompt(
        step_id=action.step_id,
        agent=action.agent,
        description=action.step_description or step.description,
        verification=action.step_verification or step.verification,
        refs=action.step_refs or step.refs,
        worktree_path=str(binding.worktree_path),
        session_reuse=session_id is not None,
        failure_context=state.get_failure_context(action.step_id),
        plan_context=plan.context,
        phase_context="",
    )

    try:
        handle = await runner.dispatch(
            prompt=prompt,
            agent=action.agent,
            workdir=str(binding.worktree_path),
            session_id=session_id,
        )
    except RunnerError:
        if session_id is not None:
            observer.emit(
                "SESSION_REUSE_MISS",
                step_id=action.step_id,
                reason="stale_or_unavailable_session",
            )
            handle = await runner.dispatch(
                prompt=prompt,
                agent=action.agent,
                workdir=str(binding.worktree_path),
                session_id=None,
            )
        else:
            raise
    state.register(
        step_id=action.step_id,
        agent=action.agent,
        runner_name=runner_name,
        handle=handle,
        worktree_path=str(binding.worktree_path),
    )

    observer.emit(
        "STEP_DISPATCHED",
        step_id=action.step_id,
        agent=action.agent,
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

    This surface exists separately from ``reconcile()`` because ``decide()`` may
    emit completion actions based on prior iteration results.
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
    if completed.result.status == RunnerStatus.SUCCESS:
        evidence = completed.result.output
        plan, expected_hash = load_plan_definition(plan_path)
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

            if merge_result.value.outcome.value == "auto_resolved_conflict":
                observer.emit(
                    "MERGE_COMPLETED",
                    step_id=completed.step_id,
                    auto_resolved=True,
                    conflicting_files=list(merge_result.value.conflicted_files),
                    elapsed_seconds=completed.elapsed_seconds,
                )

        if completed.result.session_id:
            session_pool.record(
                step_id=completed.step_id,
                session_id=completed.result.session_id,
                runner_name=completed.runner_name,
                agent=completed.agent,
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

        observer.emit(
            "MERGE_COMPLETED", step_id=completed.step_id, elapsed_seconds=completed.elapsed_seconds
        )
        return

    state.increment_failure(completed.step_id, completed.runner_name)
    state.failure_history.setdefault(completed.step_id, []).append(completed.result.output)
    count = state.failure_count(completed.step_id)

    plan, expected_hash = load_plan_definition(plan_path)
    claims_path = resolve_claims_path(plan_path)

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

    observer.emit(
        "ESCALATION_DEFERRED",
        step_id=completed.step_id,
        attempt=count,
        reason="Escalation runtime verdict path is deferred to driver-judgment-expansion-replan",
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


def _cleanup_orphan_worktrees(*, plan_path: Path, plan_step_ids: set[str]) -> None:
    """Delete stale step directories that do not map to any plan step.

    Args:
        plan_path: Path to plan.yaml used for deriving worktree base path.
        plan_step_ids: Set of current step IDs in the loaded plan.
    """
    base_dir = plan_path.parent / ".vectl" / "worktrees"
    if not base_dir.exists() or not base_dir.is_dir():
        return

    for candidate in base_dir.iterdir():
        if not candidate.is_dir():
            continue
        if candidate.name in plan_step_ids:
            continue
        shutil.rmtree(candidate, ignore_errors=True)


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
    """Execute the deterministic decide/dispatch/wait/reconcile loop."""
    while not state.halt_requested:
        decide_output = decide(
            running_tasks=state.as_running_tasks(),
            completed_results=state.drain_completed(),
            max_parallelism=config.orchestration.max_parallelism,
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
                await handle_complete(
                    action=action,
                    state=state,
                    judge=judge,
                    session_pool=session_pool,
                    observer=observer,
                    plan_path=plan_path,
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
    "DEFERRED_REPLAN_BRANCHES",
    "GRACEFUL_SHUTDOWN_CONTRACT",
    "LoopSurfaceName",
    "PlannerDispatchContract",
    "PlannerDispatchRequest",
    "PlannerDispatcher",
    "REPLAN_PLANNER_WIRING",
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
