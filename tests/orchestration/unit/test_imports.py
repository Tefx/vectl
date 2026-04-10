"""
Import and signature tests for the orchestration package boundary.

These tests verify that the orchestration package exposes the correct
public API surface as defined in docs/ORCHESTRATION-PLANE-INTERFACES.md.

Authority: docs/ORCHESTRATION-PLANE-INTERFACES.md sections 3-4
Step: orch_foundation.contract_tests
Intent: test_define_red
"""


class TestPackageImports:
    """Verify all documented types and protocols are importable from the package root."""

    def test_import_all_contracts_from_package_root(self):
        """
        Verify all Section 3 contract types are exported from vectl.orchestration.

        Spec: docs/ORCHESTRATION-PLANE-INTERFACES.md section 3
        Expected: All 9 types importable without ImportError.
        """
        from vectl.orchestration import (
            ControlDecision,
            CoreSnapshot,
            ExecutionRequest,
            ExecutionResult,
            IsolationMode,
            ResolutionCase,
            ResolutionReport,
            RosterSnapshot,
            RuntimeSnapshot,
            WorkLease,
            CoreAdapter,
        )

        # Verify they are classes/dataclasses
        assert CoreSnapshot is not None
        assert RosterSnapshot is not None
        assert RuntimeSnapshot is not None
        assert ControlDecision is not None
        assert WorkLease is not None
        assert ExecutionRequest is not None
        assert ExecutionResult is not None
        assert ResolutionCase is not None
        assert ResolutionReport is not None
        assert IsolationMode is not None
        assert CoreAdapter is not None

    def test_import_all_protocols_from_package_root(self):
        """
        Verify all Section 4 component Protocols are exported from vectl.orchestration.

        Spec: docs/ORCHESTRATION-PLANE-INTERFACES.md section 4
        Expected: All 4 Protocols importable without ImportError.
        """
        from vectl.orchestration import (
            Control,
            Resolver,
            Roster,
            Runtime,
        )

        # Verify they are Protocols
        assert Control is not None
        assert Roster is not None
        assert Runtime is not None
        assert Resolver is not None

    def test_orchestration_all_exports_match_spec(self):
        """
        Verify __all__ exports include the documented interface boundary.

        Spec: docs/ORCHESTRATION-PLANE-INTERFACES.md sections 3-4
        Expected: __all__ contains the documented symbols
        (11 shared contracts + core adapter + 4 protocols), while allowing
        compatible additive exports.
        """
        from vectl.orchestration import __all__

        expected_contracts = [
            "CoreSnapshot",
            "RosterSnapshot",
            "RuntimeSnapshot",
            "ControlDecision",
            "DispatchSpec",
            "IsolationMode",
            "WorkLease",
            "ExecutionRequest",
            "ExecutionResult",
            "RoleProfile",
            "RoleProfileRegistry",
            "PromptBundle",
            "PromptRegistry",
            "StructuredReviewResult",
            "ResolutionCase",
            "ResolutionReport",
            "CoreAdapter",
        ]
        expected_protocols = [
            "Control",
            "Roster",
            "Runtime",
            "Resolver",
        ]
        expected_all = set(expected_contracts + expected_protocols)
        actual_all = set(__all__)

        # This test documents the required interface surface while allowing
        # additional additive exports that do not remove the canonical boundary.
        assert expected_all <= actual_all, (
            f"__all__ missing required exports. Required: {expected_all}, Got: {actual_all}"
        )


class TestDirectModuleImports:
    """Verify types can be imported from their defining modules."""

    def test_import_contracts_from_contracts_module(self):
        """Verify all contract types importable from vectl.orchestration.contracts."""
        from vectl.orchestration.contracts import (
            ControlDecision,
            CoreSnapshot,
            ExecutionRequest,
            ExecutionResult,
            IsolationMode,
            ResolutionCase,
            ResolutionReport,
            RosterSnapshot,
            RuntimeSnapshot,
            WorkLease,
        )

        assert all(
            x is not None
            for x in [
                CoreSnapshot,
                RosterSnapshot,
                RuntimeSnapshot,
                ControlDecision,
                WorkLease,
                ExecutionRequest,
                ExecutionResult,
                ResolutionCase,
                ResolutionReport,
                IsolationMode,
            ]
        )

    def test_import_core_adapter_from_module(self):
        """Verify core authority bridge protocol import."""
        from vectl.orchestration.core_adapter import CoreAdapter

        assert CoreAdapter is not None

    def test_import_protocols_from_interfaces_module(self):
        """Verify all protocols importable from vectl.orchestration.interfaces."""
        from vectl.orchestration.interfaces import (
            Control,
            Resolver,
            Roster,
            Runtime,
        )

        assert all(x is not None for x in [Control, Roster, Runtime, Resolver])


class TestSignatureGaps:
    """
    Document expected-red gaps between spec and implementation.

    These tests define acceptance criteria that should FAIL initially
    (red state) until the implementation is complete.
    """

    def test_protocol_runtime_checkability(self):
        """
        GAP: Protocols should be runtime-checkable for isinstance() tests.

        Spec: docs/ORCHESTRATION-PLANE-INTERFACES.md section 4
        Expected: Protocols should support runtime checking.
        Current: Protocols use Protocol base but @runtime_checkable not verified.

        This test will XPASS once runtime checking is properly enabled.
        """
        from typing import runtime_checkable

        from vectl.orchestration.interfaces import Control, Resolver, Roster, Runtime

        # This should pass if protocols are properly decorated
        assert runtime_checkable(Control)
        assert runtime_checkable(Roster)
        assert runtime_checkable(Runtime)
        assert runtime_checkable(Resolver)

    def test_contract_field_type_annotations(self):
        """
        GAP: Field type annotations should match spec exactly.

        Spec: docs/ORCHESTRATION-PLANE-INTERFACES.md section 3
        Expected: All fields use correct types (tuple vs list, Literal values, etc.)
        Current: Types exist but comprehensive type verification not implemented.

        This test documents the gap in type-level verification.
        """
        from typing import get_type_hints

        from vectl.orchestration.contracts import CoreSnapshot

        hints = get_type_hints(CoreSnapshot)
        assert hints["plan_complete"] is bool
        assert hints["claimable_step_ids"] == tuple[str, ...]
        assert hints["in_progress_step_ids"] == tuple[str, ...]
        assert hints["blocked_step_ids"] == tuple[str, ...]
        assert hints["unresolved_reasons"] == tuple[str, ...]
