"""
Orchestration application composition root.

Authority: docs/ORCHESTRATION-PLANE-IMPLEMENTATION-DESIGN.md section 6
Authority: docs/ORCHESTRATION-PLANE-ARCHITECTURE.md sections 2, 5
Authority: docs/ORCHESTRATION-PLANE-INTERFACES.md sections 3, 4

This module is the composition root that wires together the orchestration plane
components (control, roster, runtime, resolver, core_adapter) and exposes a
typed application API for the ``vectl orch`` CLI command group.

Public surfaces (this module):
    - OrchestrationApp              (composed application facade / entrypoint contract)
    - AppConfig                      (application-level configuration model)
    - OrchestrationResult            (typed result DTO returned by orchestration operations)
    - build_orchestration_app()      (factory for composing orchestration app from config)

Note: This module addresses the ``orch_app`` composition-root responsibility.
The exact wiring and runtime behavior are deferred to implementation phases.
This contract step pins only typed boundaries and stub implementations.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from vectl.orchestration.config import (
    OrchestrationConfig,
    ResolverToolAllowlist,
    freeze_config,
    load_frozen_snapshot,
    load_orchestration_config,
    validate_orchestration_config,
)
from vectl.orchestration.control_channel import (
    ControlChannelMessage,
    FilesystemControlChannel,
    send_to_control,
    set_default_control_channel,
)
from vectl.orchestration.events import (
    JsonlEventSink,
    OrchestrationEventEnvelope,
    OrchestrationEventKind,
    load_event_jsonl,
)
from vectl.orchestration.inspection_queries import (
    CasesQueryImpl,
    InspectQuery,
    RunsQueryImpl,
    query_cases,
    query_runs,
)
from vectl.orchestration.resolver_gateway import (
    AuditedResolverGateway,
    AuthorizationError,
    ResolverToolCall,
    authorize_and_invoke,
)
from vectl.orchestration.run_store import RunRecord, RunRegistry, generate_run_id
from vectl.orchestration.tool_registry import canonical_tool_families

if TYPE_CHECKING:
    from vectl.orchestration.control import Control
    from vectl.orchestration.core_adapter import CoreAdapter
    from vectl.orchestration.resolver import Resolver
    from vectl.orchestration.roster import Roster
    from vectl.orchestration.runtime import Runtime


# ---------------------------------------------------------------------
# Application Configuration
# ---------------------------------------------------------------------


@dataclass(frozen=True)
class AppConfig:
    """
    Application-level configuration for the orchestration app.

    Authority: docs/ORCHESTRATION-PLANE-IMPLEMENTATION-DESIGN.md section 3.7

    Attributes:
        plan_path: Path to the authoritative plan definition file.
        worktree_base_dir: Base directory for worktree/workspace creation.
        default_agent: Default agent name for dispatch.
        resolver_timeout_seconds: Timeout for resolver invocations.
    """

    plan_path: Path = field(default_factory=lambda: Path("plan.yaml"))
    worktree_base_dir: Path = field(default_factory=lambda: Path(".vectl/worktrees"))
    default_agent: str = "python-executor"
    resolver_timeout_seconds: float = 60.0
    orchestration_config: OrchestrationConfig | None = None
    run_store_root: Path | None = None


# ---------------------------------------------------------------------
# Orchestration Result DTOs
# ---------------------------------------------------------------------


@dataclass(frozen=True)
class OrchestrationResult:
    """
    Typed result DTO returned by orchestration operations.

    Authority: docs/ORCHESTRATION-PLANE-INTERFACES.md (shared)

    GAP: The exact result schema for orchestration operations is not yet
    fully specified. The fields below represent the known minimum anchor.

    Attributes:
        success: True if the operation completed successfully.
        message: Human-readable result message.
        step_id: Optional step ID affected by the operation.
        run_id: Optional run identifier created by the operation.
    """

    success: bool
    message: str
    step_id: str | None = None
    run_id: str | None = None


@dataclass(frozen=True)
class RunResult:
    """
    Typed result for a single run execution.

    Authority: docs/ORCHESTRATION-PLANE-INTERFACES.md (shared)

    Attributes:
        run_id: Unique identifier for this run.
        step_id: Step being executed.
        status: Execution status.
        output_summary: Human-readable summary of results.
    """

    run_id: str
    step_id: str
    status: Literal["pending", "running", "success", "fail", "stall"] | None = None
    output_summary: str = ""


@dataclass(frozen=True)
class InspectResult:
    """
    Typed result for inspect operations.

    Authority: docs/ORCHESTRATION-PLANE-INTERFACES.md (shared)

    GAP: The exact inspect result schema is not yet fully specified.

    Attributes:
        view_type: The type of inspect view returned.
        data: Opaque inspection data (structure depends on view_type).
    """

    view_type: str
    data: tuple[str, ...] = ()


@dataclass(frozen=True)
class CaseResult:
    """
    Typed result for case operations.

    Authority: docs/ORCHESTRATION-PLANE-INTERFACES.md (shared)

    GAP: The exact case result schema is not yet fully specified.

    Attributes:
        case_id: Unique identifier for the case.
        status: Case status.
        reason: Reason for the case.
    """

    case_id: str | None = None
    status: Literal["open", "resolved", "halt"] | None = None
    reason: str = ""


@dataclass(frozen=True)
class ControlResult:
    """
    Typed result for control operations.

    Authority: docs/ORCHESTRATION-PLANE-INTERFACES.md (shared)

    GAP: The exact control result schema is not yet fully specified.

    Attributes:
        action: The control action that was taken.
        success: True if the action completed successfully.
        message: Human-readable result message.
    """

    action: Literal["pause", "unpause", "stop"] | None = None
    success: bool = False
    message: str = ""


@dataclass(frozen=True)
class ConfigResult:
    """
    Typed result for config operations.

    Authority: docs/ORCHESTRATION-PLANE-IMPLEMENTATION-DESIGN.md section 3.7

    GAP: The exact config result schema is not yet fully specified.

    Attributes:
        show_output: Output from config show operation.
        validation_passed: True if config validation passed.
        tools: Tuple of registered tool family identifiers.
    """

    show_output: str = ""
    validation_passed: bool = False
    tools: tuple[str, ...] = ()


# ---------------------------------------------------------------------
# Orchestration App Composition Root
# ---------------------------------------------------------------------


class OrchestrationApp:
    """
    Composed orchestration application facade.

    Authority: docs/ORCHESTRATION-PLANE-IMPLEMENTATION-DESIGN.md section 6

    This is the composition root that wires together the orchestration plane
    components (control, roster, runtime, resolver, core_adapter) and exposes
    a typed application API for the ``vectl orch`` CLI command group.

    GAP: Concrete component wiring and runtime behavior are deferred to
    implementation phases. This contract step pins only typed boundaries.

    Public surface:
        - run()             (start/resume a run)
        - recover()         (recover from artifacts)
        - prune()           (prune old runs/artifacts)
        - runs()            (list runs)
        - inspect()          (inspect status/events/logs/artifacts/actions)
        - case_*()           (case list/show/respond)
        - control_*()        (control pause/unpause/stop)
        - config_*()         (config show/validate/tools)
    """

    def __init__(
        self,
        config: AppConfig,
        control: Control,
        roster: Roster,
        runtime: Runtime,
        resolver: Resolver,
        core_adapter: CoreAdapter,
    ) -> None:
        """
        Initialize the orchestration app with composed components.

        Args:
            config: Application-level configuration.
            control: Plan-aware orchestration control component.
            roster: Reusable resource registry component.
            runtime: Mechanical execution runtime component.
            resolver: Blocked/unresolved case resolver component.
            core_adapter: Authoritative core bridge adapter.
        """
        self._config = config
        self._control = control
        self._roster = roster
        self._runtime = runtime
        self._resolver = resolver
        self._core_adapter = core_adapter
        self._event_sink = JsonlEventSink(self._events_path())
        self._text_log_path = self._logs_path()
        self._text_log_path.parent.mkdir(parents=True, exist_ok=True)
        self._text_log_path.touch(exist_ok=True)

    # -----------------------------------------------------------------
    # Run / Resume / Recover / Prune
    # -----------------------------------------------------------------

    def run(
        self,
        step_id: str | None = None,
        agent: str | None = None,
    ) -> OrchestrationResult:
        """
        Start a new orchestration run.

        Authority: docs/ORCHESTRATION-PLANE-ARCHITECTURE.md section 6.1

        Args:
            step_id: Optional explicit step to run. None means auto-select next.
            agent: Optional agent name. None uses default.

        Returns:
            OrchestrationResult with run outcome.

        Raises:
            OSError: When frozen snapshot persistence fails.
        """
        resolved_config = self._effective_orchestration_config()
        resolved_agent = agent or self._config.default_agent
        try:
            resolved_step_id = self._resolve_step_id(step_id=step_id, agent=resolved_agent)
        except ValueError as exc:
            return OrchestrationResult(success=False, message=str(exc))
        if resolved_step_id is None:
            return OrchestrationResult(
                success=False,
                message="No claimable step available for run start",
            )

        registry = self._run_registry(config=resolved_config)
        try:
            registry.assert_can_admit_same_plan(str(self._config.plan_path))
        except Exception as exc:
            return OrchestrationResult(
                success=False,
                message=f"Run admission denied: {exc}",
            )

        run_id = generate_run_id()
        run_root = registry.run_artifact_root(run_id)
        frozen_snapshot = freeze_config(resolved_config, run_dir=run_root)

        registry.save(
            RunRecord(
                run_id=run_id,
                step_id=resolved_step_id,
                plan_path=str(self._config.plan_path),
                agent=resolved_agent,
                status="running",
                artifact_root=str(run_root),
                output_summary=(
                    "run initialized with frozen snapshot "
                    f"runner={frozen_snapshot.config.runtime.default_runner} "
                    f"allowlist={self._allowlist_text(frozen_snapshot.config.resolver.tool_allowlist)}"
                ),
            )
        )

        self._emit_event(
            kind="run_started",
            step_id=resolved_step_id,
            agent=resolved_agent,
            payload={"run_id": run_id, "plan_path": str(self._config.plan_path)},
        )
        self._emit_event(
            kind="run_status_changed",
            step_id=resolved_step_id,
            agent=resolved_agent,
            payload={"run_id": run_id, "status": "running"},
        )
        self._append_log(
            f"run_id={run_id} step_id={resolved_step_id} status=running agent={resolved_agent}"
        )

        return OrchestrationResult(
            success=True,
            message=(
                f"Run initialized with frozen config snapshot ({frozen_snapshot.snapshot_path})"
            ),
            step_id=resolved_step_id,
            run_id=run_id,
        )

    def resume(
        self,
        run_id: str,
    ) -> OrchestrationResult:
        """
        Resume an existing run from artifacts.

        Authority: docs/ORCHESTRATION-PLANE-ARCHITECTURE.md section 6.2

        Args:
            run_id: The run identifier to resume.

        Returns:
            OrchestrationResult with resume outcome.

        Raises:
            FileNotFoundError: If the run snapshot is missing.
        """
        ambient = self._effective_orchestration_config()
        registry = self._run_registry(config=ambient)
        record = registry.by_id(run_id)
        if record is None:
            return OrchestrationResult(
                success=False, message=f"Run not found: {run_id}", run_id=run_id
            )

        run_root = registry.run_artifact_root(run_id)
        snapshot_path = run_root / "config.snapshot.yaml"
        if not snapshot_path.exists():
            return OrchestrationResult(
                success=False,
                message=(
                    "Resume refused: frozen config snapshot missing for authoritative run "
                    f"{run_id} ({snapshot_path})"
                ),
                step_id=record.step_id,
                run_id=run_id,
            )

        frozen = load_frozen_snapshot(snapshot_path)
        if not frozen.continuity.resume_enabled:
            return OrchestrationResult(
                success=False,
                message=f"Resume disabled by frozen config snapshot for run {run_id}",
                step_id=record.step_id,
                run_id=run_id,
            )

        if self._binding_signature(frozen) != self._binding_signature(ambient):
            return OrchestrationResult(
                success=False,
                message=(
                    "Resume refused: ambient configuration differs from authoritative "
                    f"frozen snapshot for run {run_id}"
                ),
                step_id=record.step_id,
                run_id=run_id,
            )

        registry.save(
            RunRecord(
                run_id=run_id,
                step_id=record.step_id,
                plan_path=record.plan_path,
                agent=record.agent,
                status="running",
                artifact_root=str(run_root),
                output_summary=(
                    "run resumed from authoritative frozen snapshot "
                    f"runner={frozen.runtime.default_runner} "
                    f"allowlist={self._allowlist_text(frozen.resolver.tool_allowlist)}"
                ),
            )
        )
        return OrchestrationResult(
            success=True,
            message=f"Run resumed from frozen snapshot: {snapshot_path}",
            step_id=record.step_id,
            run_id=run_id,
        )

    def _effective_orchestration_config(self) -> OrchestrationConfig:
        if self._config.orchestration_config is not None:
            return self._config.orchestration_config
        return OrchestrationConfig(plan_path=self._config.plan_path)

    def _run_registry(self, config: OrchestrationConfig) -> RunRegistry:
        if self._config.run_store_root is not None:
            return RunRegistry(store_root=self._config.run_store_root)
        return RunRegistry(store_root=config.runtime.artifact_root)

    def _resolve_step_id(self, step_id: str | None, agent: str) -> str | None:
        if step_id is not None:
            return step_id
        snapshot = self._core_adapter.snapshot(agent=agent)
        if not snapshot.claimable_step_ids:
            return None
        if len(snapshot.claimable_step_ids) > 1:
            joined = ", ".join(snapshot.claimable_step_ids)
            raise ValueError(
                "Run selection is ambiguous: multiple claimable steps available; "
                f"provide explicit step_id ({joined})"
            )
        return next(iter(snapshot.claimable_step_ids), None)

    def _events_path(self) -> Path:
        resolved = self._effective_orchestration_config()
        root = self._config.run_store_root or resolved.runtime.artifact_root
        return Path(root) / "events.jsonl"

    def _logs_path(self) -> Path:
        resolved = self._effective_orchestration_config()
        root = self._config.run_store_root or resolved.runtime.artifact_root
        return Path(root) / "orchestration.log"

    def _emit_event(
        self,
        *,
        kind: OrchestrationEventKind,
        payload: dict[str, object],
        step_id: str | None = None,
        agent: str | None = None,
    ) -> None:
        envelope = OrchestrationEventEnvelope(
            kind=kind,
            timestamp=datetime.now(timezone.utc),
            step_id=step_id,
            payload=payload,
            agent=agent,
        )
        self._event_sink.emit(envelope)

    def _append_log(self, line: str) -> None:
        timestamp = datetime.now(timezone.utc).isoformat()
        with self._text_log_path.open("a", encoding="utf-8") as handle:
            handle.write(f"{timestamp} {line}\n")

    def _active_run_ids(self, step_id: str | None = None) -> tuple[str, ...]:
        registry = self._run_registry(config=self._effective_orchestration_config())
        candidates: list[RunRecord] = []
        for status in ("running", "pending", "stall"):
            candidates.extend(registry.by_status(status))
        if step_id is not None:
            candidates = [record for record in candidates if record.step_id == step_id]
        unique = tuple(dict.fromkeys(record.run_id for record in candidates))
        return unique

    def _resolve_run_selection(
        self,
        *,
        run_id: str | None,
        step_id: str | None = None,
    ) -> tuple[str | None, str | None]:
        registry = self._run_registry(config=self._effective_orchestration_config())
        if run_id is not None:
            found = registry.by_id(run_id)
            if found is None:
                return None, f"Run not found: {run_id}"
            return run_id, None

        active = self._active_run_ids(step_id=step_id)
        if not active:
            return None, "No active run available; provide explicit run_id"
        if len(active) > 1:
            return (
                None,
                "Run selection is ambiguous: multiple active runs available; "
                f"provide explicit run_id ({', '.join(active)})",
            )
        return next(iter(active), None), None

    def _binding_signature(self, config: OrchestrationConfig) -> dict[str, object]:
        return {
            "runtime": asdict(config.runtime),
            "resolver": {
                "enabled": config.resolver.enabled,
                "invocation_timeout_seconds": config.resolver.invocation_timeout_seconds,
                "max_tool_calls_per_invocation": config.resolver.max_tool_calls_per_invocation,
                "max_tool_argument_bytes": config.resolver.max_tool_argument_bytes,
                "tool_allowlist": asdict(config.resolver.tool_allowlist),
            },
            "continuity": asdict(config.continuity),
        }

    def _allowlist_text(self, allowlist: ResolverToolAllowlist) -> str:
        if not allowlist.allowed_tool_families:
            return "deny-all"
        return ",".join(allowlist.allowed_tool_families)

    def recover(
        self,
        step_id: str | None = None,
    ) -> OrchestrationResult:
        """
        Recover orchestration state from continuity artifacts.

        Authority: docs/ORCHESTRATION-PLANE-MIGRATION.md section 4

        Args:
            step_id: Optional step to recover. None means recover all.

        Returns:
            OrchestrationResult with recovery outcome.

        Raises:
            NotImplementedError: Until recovery semantics are specified.
        """
        recovered = 0
        for result in self.runs(step_id=step_id):
            if result.status in {"running", "pending", "stall"}:
                recovered += 1
        return OrchestrationResult(
            success=True,
            message=f"Recovery scan complete: {recovered} active run(s) require resume handling",
            step_id=step_id,
        )

    def runs(
        self,
        step_id: str | None = None,
        limit: int = 100,
    ) -> tuple[RunResult, ...]:
        """
        List runs, optionally filtered by step.

        Authority: docs/ORCHESTRATION-PLANE-INTERFACES.md (shared)

        Args:
            step_id: Optional step filter.
            limit: Maximum number of runs to return.

        Returns:
            Tuple of RunResult for matching runs.

        Raises:
            NotImplementedError: Until runs listing semantics are specified.
        """
        registry = self._run_registry(config=self._effective_orchestration_config())
        inspect_query = InspectQuery(step_id=step_id, limit=limit, offset=0)
        inspect_view = query_runs(inspect_query, registry=RunsQueryImpl(registry))
        results: list[RunResult] = []
        for run_id in inspect_view.runs:
            record = registry.by_id(run_id)
            if record is None:
                continue
            results.append(
                RunResult(
                    run_id=record.run_id,
                    step_id=record.step_id,
                    status=record.status,
                    output_summary=record.output_summary,
                )
            )
        return tuple(results)

    def prune(
        self,
        before: float | None = None,
        force: bool = False,
    ) -> OrchestrationResult:
        """
        Prune old runs and artifacts.

        Authority: docs/ORCHESTRATION-PLANE-INTERFACES.md (shared)

        Args:
            before: Unix timestamp threshold. None means prune all.
            force: Skip confirmation prompt.

        Returns:
            OrchestrationResult with prune outcome.

        Raises:
            NotImplementedError: Until prune semantics are specified.
        """
        del force
        registry = self._run_registry(config=self._effective_orchestration_config())
        latest = list(registry._latest_records_by_run_id().values())
        if before is not None:
            latest = [record for record in latest if (record.updated_at or 0.0) < before]
        removed_cases = 0
        for record in latest:
            removed_cases += registry.prune_run(record.run_id)
        return OrchestrationResult(
            success=True,
            message=f"Prune complete: removed {removed_cases} case index entries",
        )

    # -----------------------------------------------------------------
    # Inspect
    # -----------------------------------------------------------------

    def inspect_status(
        self,
        step_id: str | None = None,
    ) -> InspectResult:
        """
        Inspect current orchestration status.

        Authority: docs/ORCHESTRATION-PLANE-INTERFACES.md (shared)

        Args:
            step_id: Optional step to inspect.

        Returns:
            InspectResult with status view.

        Raises:
            NotImplementedError: Until inspect semantics are specified.
        """
        runs = query_runs(
            InspectQuery(step_id=step_id, limit=100, offset=0),
            registry=RunsQueryImpl(
                self._run_registry(config=self._effective_orchestration_config())
            ),
        )
        data = (
            f"step_id={step_id or ''}",
            f"total_count={runs.total_count}",
            f"latest_run_id={runs.latest_run_id or ''}",
            f"statuses={','.join(runs.statuses)}",
        )
        return InspectResult(view_type="status", data=data)

    def inspect_events(
        self,
        step_id: str | None = None,
        limit: int = 100,
    ) -> InspectResult:
        """
        Inspect orchestration events.

        Authority: docs/ORCHESTRATION-PLANE-INTERFACES.md (shared)

        Args:
            step_id: Optional step to filter events.
            limit: Maximum number of events to return.

        Returns:
            InspectResult with events view.

        Raises:
            NotImplementedError: Until inspect semantics are specified.
        """
        events = load_event_jsonl(self._events_path())
        filtered = [event for event in events if step_id is None or event.step_id == step_id]
        selected = filtered[-limit:]
        data = tuple(
            f"seq={event.seq} event={event.kind} step_id={event.step_id or ''}"
            for event in selected
        )
        return InspectResult(view_type="events", data=data)

    def inspect_logs(
        self,
        run_id: str | None = None,
        step_id: str | None = None,
    ) -> InspectResult:
        """
        Inspect run logs.

        Authority: docs/ORCHESTRATION-PLANE-INTERFACES.md (shared)

        Args:
            run_id: Optional specific run to inspect.
            step_id: Optional step to filter logs.

        Returns:
            InspectResult with logs view.

        Raises:
            NotImplementedError: Until inspect semantics are specified.
        """
        selected_run_id, error = self._resolve_run_selection(run_id=run_id, step_id=step_id)
        if error is not None:
            return InspectResult(view_type="logs", data=(f"error={error}",))
        lines = self._text_log_path.read_text(encoding="utf-8").splitlines()
        scoped = [
            line for line in lines if selected_run_id is None or f"run_id={selected_run_id}" in line
        ]
        return InspectResult(view_type="logs", data=tuple(scoped[-100:]))

    def inspect_artifacts(
        self,
        step_id: str | None = None,
    ) -> InspectResult:
        """
        Inspect derived artifacts from runs.

        Authority: docs/ORCHESTRATION-PLANE-INTERFACES.md (shared)

        Args:
            step_id: Optional step to filter artifacts.

        Returns:
            InspectResult with artifacts view.

        Raises:
            NotImplementedError: Until inspect semantics are specified.
        """
        results = self.runs(step_id=step_id, limit=100)
        artifact_refs: list[str] = []
        for result in results:
            run_root = self._run_registry(
                config=self._effective_orchestration_config()
            ).run_artifact_root(result.run_id)
            for relative in (
                "config.snapshot.yaml",
                "state/latest.json",
                "state/summary.json",
                "state/metrics.json",
            ):
                candidate = run_root / relative
                if candidate.exists():
                    artifact_refs.append(f"run_id={result.run_id} path={candidate}")
        return InspectResult(view_type="artifacts", data=tuple(artifact_refs))

    def inspect_actions(
        self,
        run_id: str | None = None,
    ) -> InspectResult:
        """
        Inspect actions taken during a run.

        Authority: docs/ORCHESTRATION-PLANE-INTERFACES.md (shared)

        Args:
            run_id: Optional specific run to inspect.

        Returns:
            InspectResult with actions view.

        Raises:
            NotImplementedError: Until inspect semantics are specified.
        """
        selected_run_id, error = self._resolve_run_selection(run_id=run_id)
        if error is not None:
            return InspectResult(view_type="actions", data=(f"error={error}",))
        assert selected_run_id is not None
        channel = FilesystemControlChannel(
            runs_root=self._run_registry(config=self._effective_orchestration_config())._store_root,
            max_pending_actions=self._effective_orchestration_config().operator.max_pending_actions,
        )
        rows: list[str] = []
        for status in ("pending", "applied", "rejected"):
            for request in channel.list_requests(selected_run_id, status=status):
                rows.append(
                    f"status={status} action_id={request.action_id} type={request.msg_type}"
                )
        return InspectResult(view_type="actions", data=tuple(rows))

    # -----------------------------------------------------------------
    # Case
    # -----------------------------------------------------------------

    def case_list(
        self,
        status: Literal["open", "resolved", "halt"] | None = None,
    ) -> tuple[CaseResult, ...]:
        """
        List cases (blocked/unresolved situations).

        Authority: docs/ORCHESTRATION-PLANE-RESOLUTION-CONTRACT.md sections 3, 5

        Args:
            status: Optional status filter.

        Returns:
            Tuple of CaseResult for matching cases.

        Raises:
            NotImplementedError: Until case semantics are specified.
        """
        registry = self._run_registry(config=self._effective_orchestration_config())
        ids = query_cases("open", registry=CasesQueryImpl(registry))
        results: list[CaseResult] = [
            CaseResult(case_id=case_id, status="open", reason="") for case_id in ids
        ]
        if status is not None:
            results = [case for case in results if case.status == status]
        return tuple(results)

    def case_show(
        self,
        case_id: str,
    ) -> CaseResult:
        """
        Show detail for a specific case.

        Authority: docs/ORCHESTRATION-PLANE-RESOLUTION-CONTRACT.md sections 3, 5

        Args:
            case_id: The case identifier to show.

        Returns:
            CaseResult with case detail.

        Raises:
            NotImplementedError: Until case semantics are specified.
        """
        registry = self._run_registry(config=self._effective_orchestration_config())
        entry = registry._latest_cases_by_case_id().get(case_id)
        if entry is None:
            return CaseResult(case_id=case_id, status=None, reason="Case not found")
        return CaseResult(
            case_id=entry.case_id, status="open" if entry.status == "open" else "resolved"
        )

    def case_respond(
        self,
        case_id: str,
        response: str,
    ) -> OrchestrationResult:
        """
        Respond to a case (operator input to resolver).

        Authority: docs/ORCHESTRATION-PLANE-RESOLUTION-CONTRACT.md sections 3, 5

        Args:
            case_id: The case to respond to.
            response: Operator response message.

        Returns:
            OrchestrationResult with response outcome.

        Raises:
            NotImplementedError: Until case respond semantics are specified.
        """
        registry = self._run_registry(config=self._effective_orchestration_config())
        entry = registry._latest_cases_by_case_id().get(case_id)
        if entry is None:
            return OrchestrationResult(success=False, message=f"Case not found: {case_id}")
        message = ControlChannelMessage(
            msg_type="case.respond",
            sender="operator",
            payload=(entry.run_id, case_id, response),
        )
        send_to_control(message)
        self._emit_event(
            kind="operator_action_requested",
            payload={"case_id": case_id, "action": response},
            step_id=None,
            agent="operator",
        )
        return OrchestrationResult(success=True, message=f"Case response queued for {case_id}")

    # -----------------------------------------------------------------
    # Control
    # -----------------------------------------------------------------

    def control_pause(
        self,
        step_id: str | None = None,
    ) -> ControlResult:
        """
        Pause orchestration (stop dispatching new work).

        Authority: docs/ORCHESTRATION-PLANE-ARCHITECTURE.md section 5.1

        Args:
            step_id: Optional specific step to pause.

        Returns:
            ControlResult with pause outcome.

        Raises:
            NotImplementedError: Until control semantics are specified.
        """
        selected_run_id, error = self._resolve_run_selection(run_id=None, step_id=step_id)
        if error is not None:
            return ControlResult(action="pause", success=False, message=error)
        assert selected_run_id is not None
        send_to_control(
            ControlChannelMessage(
                msg_type="control.pause",
                sender="operator",
                payload=(selected_run_id,),
            )
        )
        return ControlResult(
            action="pause", success=True, message=f"Pause queued for {selected_run_id}"
        )

    def control_unpause(
        self,
        step_id: str | None = None,
    ) -> ControlResult:
        """
        Unpause orchestration (resume dispatching).

        Authority: docs/ORCHESTRATION-PLANE-ARCHITECTURE.md section 5.1

        Args:
            step_id: Optional specific step to unpause.

        Returns:
            ControlResult with unpause outcome.

        Raises:
            NotImplementedError: Until control semantics are specified.
        """
        selected_run_id, error = self._resolve_run_selection(run_id=None, step_id=step_id)
        if error is not None:
            return ControlResult(action="unpause", success=False, message=error)
        assert selected_run_id is not None
        send_to_control(
            ControlChannelMessage(
                msg_type="control.unpause",
                sender="operator",
                payload=(selected_run_id,),
            )
        )
        return ControlResult(
            action="unpause",
            success=True,
            message=f"Unpause queued for {selected_run_id}",
        )

    def control_stop(
        self,
        reason: str | None = None,
    ) -> ControlResult:
        """
        Stop orchestration entirely.

        Authority: docs/ORCHESTRATION-PLANE-ARCHITECTURE.md section 5.1

        Args:
            reason: Optional reason for stopping.

        Returns:
            ControlResult with stop outcome.

        Raises:
            NotImplementedError: Until control semantics are specified.
        """
        selected_run_id, error = self._resolve_run_selection(run_id=None)
        if error is not None:
            return ControlResult(action="stop", success=False, message=error)
        assert selected_run_id is not None
        payload = (selected_run_id,) if reason is None else (selected_run_id, reason)
        send_to_control(
            ControlChannelMessage(
                msg_type="control.stop",
                sender="operator",
                payload=payload,
            )
        )
        return ControlResult(
            action="stop", success=True, message=f"Stop queued for {selected_run_id}"
        )

    # -----------------------------------------------------------------
    # Config
    # -----------------------------------------------------------------

    def config_show(
        self,
    ) -> ConfigResult:
        """
        Show current orchestration configuration.

        Authority: docs/ORCHESTRATION-PLANE-IMPLEMENTATION-DESIGN.md section 3.7

        Returns:
            ConfigResult with current configuration.

        Raises:
            NotImplementedError: Until config show semantics are specified.
        """
        config = self._effective_orchestration_config()
        show_output = (
            f"plan_path={config.plan_path}\n"
            f"artifact_root={config.runtime.artifact_root}\n"
            f"workspace_root={config.runtime.workspace_root}\n"
            f"resolver_enabled={config.resolver.enabled}\n"
            f"allowlist={self._allowlist_text(config.resolver.tool_allowlist)}"
        )
        return ConfigResult(
            show_output=show_output, validation_passed=True, tools=canonical_tool_families()
        )

    def config_validate(
        self,
    ) -> ConfigResult:
        """
        Validate current orchestration configuration.

        Authority: docs/ORCHESTRATION-PLANE-IMPLEMENTATION-DESIGN.md section 3.7

        Returns:
            ConfigResult with validation result.

        Raises:
            NotImplementedError: Until config validate semantics are specified.
        """
        errors = validate_orchestration_config(self._effective_orchestration_config())
        if errors:
            output = "\n".join(
                f"{error.field}: {error.reason} (value={error.value!r})" for error in errors
            )
            return ConfigResult(
                show_output=output,
                validation_passed=False,
                tools=canonical_tool_families(),
            )
        return ConfigResult(
            show_output="configuration is valid",
            validation_passed=True,
            tools=canonical_tool_families(),
        )

    def config_tools(
        self,
    ) -> ConfigResult:
        """
        Show registered tool families and allowlist.

        Authority: docs/ORCHESTRATION-PLANE-RESOLUTION-CONTRACT.md section 4.0

        Returns:
            ConfigResult with registered tool families.

        Raises:
            NotImplementedError: Until config tools semantics are specified.
        """
        resolved = self._effective_orchestration_config()
        configured_allowlist = resolved.resolver.tool_allowlist.allowed_tool_families
        output = (
            f"registered={','.join(canonical_tool_families())}\n"
            f"allowlist={','.join(configured_allowlist) if configured_allowlist else 'deny-all'}"
        )
        return ConfigResult(
            show_output=output,
            validation_passed=True,
            tools=canonical_tool_families(),
        )


# ---------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------


def build_orchestration_app(
    config: AppConfig,
) -> OrchestrationApp:
    """
    Factory to build a composed OrchestrationApp from configuration.

    Authority: docs/ORCHESTRATION-PLANE-IMPLEMENTATION-DESIGN.md section 6

    GAP: Concrete component construction and wiring are deferred to
    implementation phases.

    Args:
        config: Application-level configuration.

    Returns:
        A composed OrchestrationApp instance.

    Raises:
        OSError: If authoritative plan path cannot be accessed.
    """
    from vectl.orchestration.control import ControlInputSources, PlanAwareControl
    from vectl.orchestration.core_adapter import PlanCoreAdapter
    from vectl.orchestration.resolver import BoundResolver, ResolverInvocationSurface
    from vectl.orchestration.roster import Roster
    from vectl.orchestration.runtime import Runtime

    resolved_orchestration_config = config.orchestration_config
    if resolved_orchestration_config is None:
        loaded_config, _ = load_orchestration_config()
        resolved_orchestration_config = replace(loaded_config, plan_path=config.plan_path)

    if not config.plan_path.exists():
        raise FileNotFoundError(f"authoritative plan path not found: {config.plan_path}")

    class GatewayResolverInvocation(ResolverInvocationSurface):
        def __init__(self, allowlist: tuple[str, ...]) -> None:
            self._allowlist = allowlist

        def invoke(self, case: object) -> dict[str, object]:
            from vectl.orchestration.contracts import ResolutionCase, ResolutionReport

            if not isinstance(case, ResolutionCase):
                raise TypeError("resolver invocation requires ResolutionCase")

            def _invoker(
                _case: ResolutionCase,
                _calls: tuple[ResolverToolCall, ...],
                invocation_ref: str,
            ) -> ResolutionReport:
                return ResolutionReport(
                    status="waiting",
                    summary="Resolver invocation recorded through gateway authorization",
                    evidence_refs=(f"resolver_invocation_ref={invocation_ref}",),
                )

            gateway = AuditedResolverGateway(
                planned_tool_calls=(
                    ResolverToolCall(family="orchestration", name="read_state", surface="read"),
                    ResolverToolCall(family="orchestration", name="read_case", surface="read"),
                ),
                resolver_invoker=_invoker,
                invocation_ref_factory=lambda: f"resolver-{generate_run_id()}",
            )
            try:
                result = authorize_and_invoke(
                    case=case,
                    allowed_tool_families=self._allowlist,
                    gateway=gateway,
                )
            except AuthorizationError as exc:
                return {
                    "status": "operator_required",
                    "summary": f"Resolver authorization denied: {exc}",
                    "evidence_refs": tuple(
                        f"denied_family={family}" for family in exc.denied_families
                    ),
                    "operator_message": "Expand resolver allowlist or route to operator.",
                }

            report = result.report
            if report is None:
                return {
                    "status": "waiting",
                    "summary": "Resolver gateway returned no report",
                    "evidence_refs": (),
                }
            return {
                "status": report.status,
                "summary": report.summary,
                "evidence_refs": report.evidence_refs,
                "operator_message": report.operator_message,
            }

    core_adapter = PlanCoreAdapter(plan_path=config.plan_path)
    roster = Roster(default_ttl_seconds=resolved_orchestration_config.roster.default_ttl_seconds)
    runtime = Runtime()
    control = PlanAwareControl(
        sources=ControlInputSources(core_adapter=core_adapter, roster=roster, runtime=runtime),
        agent=config.default_agent,
        fallback_role=config.default_agent,
    )
    control_channel = FilesystemControlChannel(
        runs_root=(config.run_store_root or resolved_orchestration_config.runtime.artifact_root),
        max_pending_actions=resolved_orchestration_config.operator.max_pending_actions,
    )
    set_default_control_channel(control_channel)

    resolver = BoundResolver(
        invocation=GatewayResolverInvocation(
            resolved_orchestration_config.resolver.tool_allowlist.allowed_tool_families
        )
    )

    app_config = replace(
        config,
        orchestration_config=resolved_orchestration_config,
        run_store_root=config.run_store_root or resolved_orchestration_config.runtime.artifact_root,
    )
    return OrchestrationApp(
        config=app_config,
        control=control,
        roster=roster,
        runtime=runtime,
        resolver=resolver,
        core_adapter=core_adapter,
    )


__all__ = [
    "AppConfig",
    "OrchestrationApp",
    "OrchestrationResult",
    "RunResult",
    "InspectResult",
    "CaseResult",
    "ControlResult",
    "ConfigResult",
    "build_orchestration_app",
]
