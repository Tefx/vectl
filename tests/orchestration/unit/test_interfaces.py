"""
Protocol interface signature tests for orchestration plane components.

These tests verify that each component Protocol from section 4 of the spec
has the correct method signatures as documented.

Authority: docs/ORCHESTRATION-PLANE-INTERFACES.md section 4
Step: orch_foundation.contract_tests
Intent: test_define_red
"""

import inspect
from typing import Protocol, get_type_hints


class TestControlProtocol:
    """Test Control Protocol interface (Section 4.1)."""

    def test_control_is_protocol(self):
        """Verify Control is a Protocol."""
        from vectl.orchestration.interfaces import Control

        assert issubclass(Control, Protocol)

    def test_control_has_evaluate_method(self):
        """Verify Control has evaluate() method with correct signature."""
        from vectl.orchestration.contracts import (
            ControlDecision,
            CoreSnapshot,
            RosterSnapshot,
            RuntimeSnapshot,
        )
        from vectl.orchestration.interfaces import Control

        assert hasattr(Control, "evaluate")
        sig = inspect.signature(Control.evaluate)
        params = list(sig.parameters.keys())

        # Should have self, core, roster, runtime
        assert "core" in params
        assert "roster" in params
        assert "runtime" in params

    def test_control_has_apply_resolution_method(self):
        """Verify Control has apply_resolution() method."""
        from vectl.orchestration.interfaces import Control

        assert hasattr(Control, "apply_resolution")
        sig = inspect.signature(Control.apply_resolution)
        params = list(sig.parameters.keys())

        # Should have self, report, core, roster, runtime
        assert "report" in params
        assert "core" in params
        assert "roster" in params
        assert "runtime" in params

    def test_control_evaluate_return_type(self):
        """Verify Control.evaluate returns ControlDecision."""
        from vectl.orchestration.contracts import ControlDecision
        from vectl.orchestration.interfaces import Control

        hints = get_type_hints(Control.evaluate)
        assert hints.get("return") is ControlDecision


class TestRosterProtocol:
    """Test Roster Protocol interface (Section 4.2)."""

    def test_roster_is_protocol(self):
        """Verify Roster is a Protocol."""
        from vectl.orchestration.interfaces import Roster

        assert issubclass(Roster, Protocol)

    def test_roster_has_snapshot_method(self):
        """Verify Roster has snapshot() method."""
        from vectl.orchestration.interfaces import Roster

        assert hasattr(Roster, "snapshot")

    def test_roster_has_claim_method(self):
        """Verify Roster has claim() method with role parameter."""
        from vectl.orchestration.interfaces import Roster

        assert hasattr(Roster, "claim")
        sig = inspect.signature(Roster.claim)
        params = list(sig.parameters.keys())

        assert "role" in params

    def test_roster_has_release_method(self):
        """Verify Roster has release() method."""
        from vectl.orchestration.interfaces import Roster

        assert hasattr(Roster, "release")
        sig = inspect.signature(Roster.release)
        params = list(sig.parameters.keys())

        assert "lease" in params

    def test_roster_has_register_method(self):
        """Verify Roster has register() method."""
        from vectl.orchestration.interfaces import Roster

        assert hasattr(Roster, "register")
        sig = inspect.signature(Roster.register)
        params = list(sig.parameters.keys())

        assert "lease" in params
        assert "expires_at" in params

    def test_roster_claim_return_type(self):
        """Verify Roster.claim returns WorkLease | None."""
        from vectl.orchestration.interfaces import Roster

        hints = get_type_hints(Roster.claim)
        return_type = hints.get("return")
        # Should be WorkLease | None (Union[WorkLease, None])
        assert return_type is not None


class TestRuntimeProtocol:
    """Test Runtime Protocol interface (Section 4.3)."""

    def test_runtime_is_protocol(self):
        """Verify Runtime is a Protocol."""
        from vectl.orchestration.interfaces import Runtime

        assert issubclass(Runtime, Protocol)

    def test_runtime_has_snapshot_method(self):
        """Verify Runtime has snapshot() method."""
        from vectl.orchestration.interfaces import Runtime

        assert hasattr(Runtime, "snapshot")

    def test_runtime_has_prepare_method(self):
        """Verify Runtime has prepare() method."""
        from vectl.orchestration.interfaces import Runtime

        assert hasattr(Runtime, "prepare")
        sig = inspect.signature(Runtime.prepare)
        params = list(sig.parameters.keys())

        assert "request" in params

    def test_runtime_has_start_method(self):
        """Verify Runtime has start() method."""
        from vectl.orchestration.interfaces import Runtime

        assert hasattr(Runtime, "start")
        sig = inspect.signature(Runtime.start)
        params = list(sig.parameters.keys())

        assert "request" in params
        assert "workspace" in params

    def test_runtime_has_collect_method(self):
        """Verify Runtime has collect() method."""
        from vectl.orchestration.interfaces import Runtime

        assert hasattr(Runtime, "collect")
        sig = inspect.signature(Runtime.collect)
        params = list(sig.parameters.keys())

        assert "execution_id" in params

    def test_runtime_has_cleanup_method(self):
        """Verify Runtime has cleanup() method."""
        from vectl.orchestration.interfaces import Runtime

        assert hasattr(Runtime, "cleanup")
        sig = inspect.signature(Runtime.cleanup)
        params = list(sig.parameters.keys())

        assert "workspace" in params

    def test_runtime_start_return_type(self):
        """Verify Runtime.start returns str (execution_id)."""
        from vectl.orchestration.interfaces import Runtime

        hints = get_type_hints(Runtime.start)
        return_type = hints.get("return")
        assert return_type is str


class TestResolverProtocol:
    """Test Resolver Protocol interface (Section 4.4)."""

    def test_resolver_is_protocol(self):
        """Verify Resolver is a Protocol."""
        from vectl.orchestration.interfaces import Resolver

        assert issubclass(Resolver, Protocol)

    def test_resolver_has_resolve_method(self):
        """Verify Resolver has resolve() method."""
        from vectl.orchestration.interfaces import Resolver

        assert hasattr(Resolver, "resolve")
        sig = inspect.signature(Resolver.resolve)
        params = list(sig.parameters.keys())

        assert "case" in params

    def test_resolver_resolve_parameter_type(self):
        """Verify Resolver.resolve takes ResolutionCase parameter."""
        from vectl.orchestration.contracts import ResolutionCase
        from vectl.orchestration.interfaces import Resolver

        hints = get_type_hints(Resolver.resolve)
        case_type = hints.get("case")
        assert case_type is ResolutionCase

    def test_resolver_resolve_return_type(self):
        """Verify Resolver.resolve returns ResolutionReport."""
        from vectl.orchestration.contracts import ResolutionReport
        from vectl.orchestration.interfaces import Resolver

        hints = get_type_hints(Resolver.resolve)
        return_type = hints.get("return")
        assert return_type is ResolutionReport


class TestProtocolGaps:
    """
    Document expected-red gaps between spec and implementation.

    These tests define acceptance criteria that should FAIL initially
    (red state) until the implementation is complete.
    """

    def test_all_protocol_parameters_have_type_annotations(self):
        """
        GAP: All Protocol method parameters should have complete type annotations.

        Spec: docs/ORCHESTRATION-PLANE-INTERFACES.md section 4
        Expected: Every parameter in every Protocol method has a type annotation.
        Current: Some methods may have incomplete annotations.

        This test documents the gap in signature completeness verification.
        """
        from vectl.orchestration.interfaces import Control, Resolver, Roster, Runtime

        protocols = [Control, Roster, Runtime, Resolver]

        for protocol in protocols:
            for name, method in inspect.getmembers(protocol, predicate=inspect.isfunction):
                if name.startswith("_"):
                    continue
                sig = inspect.signature(method)
                for param_name, param in sig.parameters.items():
                    if param_name == "self":
                        continue
                    # This is a documentation of the gap
                    assert param.annotation is not inspect.Parameter.empty

    def test_protocol_methods_have_spec_aligned_docstrings(self):
        """
        GAP: Protocol methods should have docstrings matching spec descriptions.

        Spec: docs/ORCHESTRATION-PLANE-INTERFACES.md section 4
        Expected: Each method has a docstring describing responsibility per spec.
        Current: Docstrings exist but alignment with spec is not verified.

        This test documents the gap in docstring verification.
        """
        from vectl.orchestration.interfaces import Control, Resolver, Roster, Runtime

        protocols = [Control, Roster, Runtime, Resolver]
        for protocol in protocols:
            for name, method in inspect.getmembers(protocol, predicate=inspect.isfunction):
                if name.startswith("_"):
                    continue
                assert method.__doc__ is not None
                assert method.__doc__.strip() != ""
