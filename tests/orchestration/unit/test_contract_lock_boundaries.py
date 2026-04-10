"""Contract-lock tests for orchestration runner/runtime/resolver seams."""

from __future__ import annotations

from dataclasses import MISSING, fields
import inspect
from typing import get_args, get_type_hints

from vectl.orchestration.contracts import (
    CoreSnapshot,
    ExecutionResult,
    ResolutionCase,
    ResolverAuthorityContract,
    RosterSnapshot,
    RuntimeSnapshot,
)
from vectl.orchestration.core_adapter import CoreAdapter
from vectl.orchestration.interfaces import LifecycleMutationPort, RunnerBackend, RuntimeLifecycle
from vectl.orchestration.runtime import Runtime


def test_resolution_case_preserves_required_semantics_while_resolver_contract_is_exported() -> None:
    resolver_contract = ResolverAuthorityContract()
    resolution_case_fields = fields(ResolutionCase)
    resolution_case_hints = get_type_hints(ResolutionCase)
    additive_metadata_fields = resolution_case_fields[4:]

    assert tuple(field.name for field in resolution_case_fields[:4]) == (
        "reason",
        "core",
        "roster",
        "runtime",
    )
    assert resolution_case_hints["reason"] is str
    assert resolution_case_hints["core"] is CoreSnapshot
    assert resolution_case_hints["roster"] is RosterSnapshot
    assert resolution_case_hints["runtime"] is RuntimeSnapshot
    assert all(
        field.default is not MISSING or field.default_factory is not MISSING
        for field in additive_metadata_fields
    ), "Additive ResolutionCase metadata fields must stay optional"
    case = ResolutionCase(
        reason="blocked",
        core=CoreSnapshot(
            plan_complete=False,
            claimable_step_ids=(),
            in_progress_step_ids=(),
            blocked_step_ids=(),
            unresolved_reasons=(),
        ),
        roster=RosterSnapshot(
            available_agents=(),
            working_agents=(),
            reusable_sessions=(),
            exhausted_roles=(),
        ),
        runtime=RuntimeSnapshot(
            active_workspaces=(),
            active_executions=(),
            stalled_executions=(),
        ),
    )

    assert case.reason == "blocked"
    assert isinstance(resolver_contract, ResolverAuthorityContract)
    assert resolver_contract.execution_site == "main_worktree"
    assert resolver_contract.mutation_surface == "approved_vectl_facade_only"
    assert resolver_contract.claim_flow == "normal_flow_only"


def test_execution_result_surfaces_operator_message_for_runtime_failures() -> None:
    hints = get_type_hints(ExecutionResult)

    assert "operator_message" in hints
    assert hints["operator_message"] == str | None


def test_core_adapter_contract_pins_normal_claim_and_post_reconcile_complete() -> None:
    claim_signature = inspect.signature(CoreAdapter.claim_step)
    complete_signature = inspect.signature(CoreAdapter.complete_step)
    claim_hints = get_type_hints(CoreAdapter.claim_step)
    complete_hints = get_type_hints(CoreAdapter.complete_step)

    assert claim_signature.parameters["flow"].default == "normal"
    assert get_args(claim_hints["flow"]) == ("normal",)
    assert get_args(complete_hints["reconcile_disposition"]) == (
        "merged",
        "noop",
    )


def test_runtime_surface_preserves_backend_lifecycle_split() -> None:
    assert issubclass(Runtime, RunnerBackend)
    assert issubclass(Runtime, RuntimeLifecycle)


def test_lifecycle_mutation_port_matches_locked_mutation_rules() -> None:
    claim_signature = inspect.signature(LifecycleMutationPort.claim_step)
    complete_signature = inspect.signature(LifecycleMutationPort.complete_step)
    claim_hints = get_type_hints(LifecycleMutationPort.claim_step)
    complete_hints = get_type_hints(LifecycleMutationPort.complete_step)

    assert claim_signature.parameters["flow"].default == "normal"
    assert get_args(claim_hints["flow"]) == ("normal",)
    assert get_args(complete_hints["reconcile_disposition"]) == (
        "merged",
        "noop",
    )
