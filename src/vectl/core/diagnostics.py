"""Compatibility facade for pure diagnostic formatting and review scope logic."""

from vectl.core.diagnostic_formatting import (
    FinalValidationSource,
    ReviewIssueCategory,
    ReviewScope,
    bounded_review_value,
    format_bounded_ref_detail,
    format_final_validation_failure,
    format_unexpected_final_validation_failure,
    normalize_model_validation_issues,
    order_review_issues,
    review_issue_category,
)
from vectl.core.review_scope import (
    ReviewStepKey,
    gate_integration_ambiguity_issues,
    phase_gate_validation_step_keys,
    review_gate_phase_ids,
    review_phase_ids,
    review_step_keys,
    review_validation_plan,
    scoped_review_plan,
)

__all__ = [
    "FinalValidationSource",
    "ReviewIssueCategory",
    "ReviewScope",
    "ReviewStepKey",
    "bounded_review_value",
    "format_bounded_ref_detail",
    "format_final_validation_failure",
    "format_unexpected_final_validation_failure",
    "gate_integration_ambiguity_issues",
    "normalize_model_validation_issues",
    "order_review_issues",
    "phase_gate_validation_step_keys",
    "review_gate_phase_ids",
    "review_issue_category",
    "review_phase_ids",
    "review_step_keys",
    "review_validation_plan",
    "scoped_review_plan",
]
