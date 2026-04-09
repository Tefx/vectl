"""Contract-lock tests for orchestration runner/runtime/resolver seams."""

from __future__ import annotations

import inspect
from typing import get_args, get_type_hints

from vectl.orchestration.contracts import ExecutionResult, ResolutionCase, ResolverAuthorityContract
from vectl.orchestration.core_adapter import CoreAdapter
from vectl.orchestration.interfaces import LifecycleMutationPort, RunnerBackend, RuntimeLifecycle
from vectl.orchestration.runtime import Runtime


def test_resolution_case_stays_bounded_while_resolver_contract_is_exported() -> None:
    resolver_contract = ResolverAuthorityContract()

    assert tuple(ResolutionCase.__dataclass_fields__) == ("reason", "core", "roster", "runtime")
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
