"""Integration e2e evidence path contract for driver enhancement phase.

Source:
- step ``driver-enhancement-integration.fix-gate-type-and-e2e`` requires
  restoring a stable artifact/evidence path at this exact integration test path.
"""

from __future__ import annotations

from pathlib import Path


E2E_EVIDENCE_PATH = (
    Path(__file__).resolve().parent / "artifacts" / "driver_enhancement_e2e_evidence.md"
)


def test_driver_enhancement_e2e_evidence_path_is_stable() -> None:
    """Pin deterministic evidence path used by integration gate checks.

    The path is intentionally repository-relative to avoid tmp-path drift across
    environments and to keep gate evidence location stable.
    """

    expected = Path(__file__).resolve().parent / "artifacts" / "driver_enhancement_e2e_evidence.md"
    assert E2E_EVIDENCE_PATH == expected


def test_driver_enhancement_e2e_evidence_artifact_exists() -> None:
    """Assert restored evidence artifact is present and non-empty."""

    assert E2E_EVIDENCE_PATH.exists(), (
        f"Missing required e2e evidence artifact: {E2E_EVIDENCE_PATH}"
    )
    assert E2E_EVIDENCE_PATH.read_text(encoding="utf-8").strip() != ""
