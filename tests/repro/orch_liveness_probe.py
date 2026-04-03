#!/usr/bin/env python3
"""Liveness probe: orchestration system wiring.

Step: orch_system_wiring.liveness_probe
Intent: Prove complete vertical slice from entrypoint -> control -> runtime -> result.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

# Configure logging to see orchestration flow
import logging
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s"
)


def test_orch_wiring():
    """Test orchestration wiring from entrypoint to control evaluation."""
    
    print("=== TESTING ORCHESTRATION WIRING ===")
    
    # ARRANGE: Load config and build orchestration wiring
    from vectl.driver.entrypoint import SharedRuntimeEntrypointAdapter, EntrypointInvocation
    from vectl.driver.loop import _build_orchestration_wiring
    from vectl.plan_path import resolve_plan_path
    
    config_path = Path("driver.yaml")
    if not config_path.exists():
        print(f"FAIL: Config not found: {config_path}")
        return 1
    
    # ACT 1: Config parsing
    print("\n1. Testing config parsing...")
    adapter = SharedRuntimeEntrypointAdapter()
    invocation = EntrypointInvocation(
        form="python-module-positional-config",
        argv=(str(config_path),),
        config_arg=config_path
    )
    
    try:
        resolved_config = adapter.normalize_config(invocation)
        print(f"   ✓ Config resolved to: {resolved_config}")
    except Exception as e:
        print(f"   ✗ Config parsing FAILED: {e}")
        return 1
    
    # ACT 2: Plan loading
    print("\n2. Testing plan loading...")
    try:
        plan_path = resolve_plan_path()
        print(f"   ✓ Plan loaded from: {plan_path}")
    except Exception as e:
        print(f"   ✗ Plan loading FAILED: {e}")
        return 1
    
    # ACT 3: Orchestration wiring construction
    print("\n3. Testing orchestration wiring construction...")
    try:
        wiring = _build_orchestration_wiring(plan_path=plan_path)
        print(f"   ✓ Orchestration wiring constructed")
        print(f"     - Core adapter: {type(wiring.core_adapter).__name__}")
        print(f"     - Roster: {type(wiring.roster).__name__}")
        print(f"     - Runtime: {type(wiring.runtime).__name__}")
        print(f"     - Control: {type(wiring.control).__name__}")
        print(f"     - Resolver: {type(wiring.resolver).__name__}")
    except Exception as e:
        print(f"   ✗ Orchestration wiring FAILED: {e}")
        import traceback
        traceback.print_exc()
        return 1
    
    # ACT 4: Control evaluation
    print("\n4. Testing control evaluation...")
    try:
        decision = wiring.control.evaluate_current()
        print(f"   ✓ Control evaluation succeeded")
        print(f"     - Decision kind: {decision.kind}")
        print(f"     - Decision reason: {decision.reason}")
    except Exception as e:
        print(f"   ✗ Control evaluation FAILED: {e}")
        import traceback
        traceback.print_exc()
        return 1
    
    # ACT 5: Core snapshot
    print("\n5. Testing core adapter snapshot...")
    try:
        snapshot = wiring.core_adapter.snapshot()
        print(f"   ✓ Core snapshot succeeded")
        print(f"     - Plan complete: {snapshot.plan_complete}")
        print(f"     - Claimable steps: {len(snapshot.claimable_step_ids)}")
        print(f"     - In-progress steps: {len(snapshot.in_progress_step_ids)}")
        print(f"     - Blocked steps: {len(snapshot.blocked_step_ids)}")
        if snapshot.unresolved_reasons:
            print(f"     - Unresolved reasons: {snapshot.unresolved_reasons}")
    except Exception as e:
        print(f"   ✗ Core snapshot FAILED: {e}")
        import traceback
        traceback.print_exc()
        return 1
    
    # ACT 6: Resolver invocation
    print("\n6. Testing resolver invocation...")
    try:
        from vectl.orchestration.contracts import ResolutionCase
        
        case = ResolutionCase(
            reason="test_wiring_verification",
            core=snapshot,
            roster=wiring.roster.snapshot(),
            runtime=wiring.runtime.snapshot()
        )
        report = wiring.resolver.resolve(case)
        print(f"   ✓ Resolver invocation succeeded")
        print(f"     - Status: {report.status}")
        print(f"     - Summary: {report.summary}")
        print(f"     - Evidence refs: {report.evidence_refs}")
    except Exception as e:
        print(f"   ✗ Resolver invocation FAILED: {e}")
        import traceback
        traceback.print_exc()
        return 1
    
    # If we reach here, all components wired successfully
    print("\n=== VERDICT: ALIVE ===")
    print("All orchestration components:")
    print("  ✓ Config parsed")
    print("  ✓ Plan loaded")
    print("  ✓ Core adapter created")
    print("  ✓ Control instantiated")
    print("  ✓ Resolver bound")
    print("  ✓ Control evaluates")
    print("  ✓ Core snapshots")
    print("  ✓ Resolver resolves")
    print()
    print("Complete vertical slice: entrypoint → control → runtime → result")
    
    return 0


if __name__ == "__main__":
    try:
        sys.exit(test_orch_wiring())
    except Exception as e:
        print(f"FAIL: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
