"""Legacy gemini resume tests retired with vectl.driver removal.

Authority: orchestration control/runtime boundaries supersede removed driver APIs.
"""

from vectl.orchestration.control import DEFAULT_DISPATCH_ROLE


def test_default_dispatch_role_is_stable() -> None:
    assert DEFAULT_DISPATCH_ROLE == "python-executor"
