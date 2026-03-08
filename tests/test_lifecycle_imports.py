"""Import compatibility checks for lifecycle extraction."""

from vectl import core, lifecycle


def test_lifecycle_functions_importable_from_core_and_lifecycle() -> None:
    names = [
        "claim_step",
        "complete_step",
        "complete_phase",
        "defer_step",
        "reject_step",
        "skip_step",
        "skip_phase",
        "get_claimed_steps",
    ]

    for name in names:
        core_fn = getattr(core, name, None)
        lifecycle_fn = getattr(lifecycle, name, None)
        assert callable(core_fn), f"vectl.core.{name} is not callable"
        assert callable(lifecycle_fn), f"vectl.lifecycle.{name} is not callable"
