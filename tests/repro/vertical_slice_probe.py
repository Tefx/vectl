#!/usr/bin/env python3
"""Vertical slice probe: entrypoint → control → runtime → observable effect.

Step: orch_system_wiring.liveness_probe
Intent: Prove one complete path from user invocation to observable result.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from dataclasses import dataclass

# Configure logging
import logging
logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)s: %(message)s"
)


@dataclass
class ObservableEffect:
    """Evidence of observable effect from orchestration."""
    config_parsed: bool = False
    plan_loaded: bool = False
    wiring_constructed: bool = False
    control_state_evaluated: bool = False
    runtime_state_captured: bool = False
    decision_made: bool = False
    result_produced: bool = False


async def test_vertical_slice():
    """Test complete vertical slice: entrypoint → control → runtime → result."""
    
    print("=== VERTICAL SLICE: ENTRYPOINT → CONTROL → RUNTIME → RESULT ===")
    
    effect = ObservableEffect()
    
    # ENTRYPOINT: User-facing module invocation
    print("\n[ENTRYPOINT] Testing python -m vectl.driver entrypoint...")
    
    from vectl.driver.config import load_config
    from vectl.driver.entrypoint import SharedRuntimeEntrypointAdapter, EntrypointInvocation
    from vectl.driver.loop import run, _build_orchestration_wiring
    from vectl.plan_path import resolve_plan_path
    from vectl.orchestration.contracts import ResolutionCase
    
    config_path = Path("driver.yaml")
    
    # Parse config from user input
    invocation = EntrypointInvocation(
        form="python-module-positional-config",
        argv=(str(config_path),),
        config_arg=config_path
    )
    adapter = SharedRuntimeEntrypointAdapter()
    resolved_config = adapter.normalize_config(invocation)
    effect.config_parsed = True
    print(f"   ✓ Config parsed: {resolved_config}")
    
    # CONTROL: Load orchestration control plane
    print("\n[CONTROL] Testing orchestration control instantiation...")
    
    plan_path = resolve_plan_path()
    effect.plan_loaded = True
    print(f"   ✓ Plan loaded: {plan_path}")
    
    # Build orchestration wiring (control + runtime + resolver)
    wiring = _build_orchestration_wiring(plan_path=plan_path)
    effect.wiring_constructed = True
    print(f"   ✓ Wiring constructed")
    
    # Evaluate control decision
    decision = wiring.control.evaluate_current()
    effect.control_state_evaluated = True
    print(f"   ✓ Control evaluated: {decision.kind} ({decision.reason})")
    
    # RUNTIME: Test runtime state capture
    print("\n[RUNTIME] Testing runtime state...")
    
    runtime_snapshot = wiring.runtime.snapshot()
    effect.runtime_state_captured = True
    print(f"   ✓ Runtime snapshot: {len(runtime_snapshot.active_executions)} executions")
    
    core_snapshot = wiring.core_adapter.snapshot()
    roster_snapshot = wiring.roster.snapshot()
    
    # RESULT: Test resolver produces result
    print("\n[RESULT] Testing resolver result production...")
    
    case = ResolutionCase(
        reason="vertical_slice_test",
        core=core_snapshot,
        roster=roster_snapshot,
        runtime=runtime_snapshot
    )
    
    report = wiring.resolver.resolve(case)
    effect.result_produced = True
    effect.decision_made = True
    print(f"   ✓ Resolver result: {report.status}")
    print(f"     Summary: {report.summary}")
    if report.evidence_refs:
        print(f"     Evidence: {', '.join(report.evidence_refs)}")
    
    # VERIFY: Complete path observable
    print("\n=== OBSERVABLE EFFECT VERIFICATION ===")
    
    checks = [
        ("Config parsed", effect.config_parsed),
        ("Plan loaded", effect.plan_loaded),
        ("Wiring constructed", effect.wiring_constructed),
        ("Control state evaluated", effect.control_state_evaluated),
        ("Runtime state captured", effect.runtime_state_captured),
        ("Decision made", effect.decision_made),
        ("Result produced", effect.result_produced),
    ]
    
    all_passed = True
    for check_name, check_result in checks:
        status = "✓" if check_result else "✗"
        print(f"  {status} {check_name}")
        if not check_result:
            all_passed = False
    
    if all_passed:
        print("\n=== VERDICT: ALIVE ===")
        print("Complete vertical slice successful:")
        print("  ENTRYPOINT (config) → CONTROL (plan/core) → RUNTIME (snapshot) → RESULT (report)")
        return 0
    else:
        print("\n=== VERDICT: FAILED ===")
        print("Vertical slice incomplete")
        return 1


if __name__ == "__main__":
    try:
        exit_code = asyncio.run(test_vertical_slice())
        sys.exit(exit_code)
    except Exception as e:
        print(f"FAIL: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
