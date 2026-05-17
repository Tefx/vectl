"""Claim-time guidance builder.

Source (spec): user feature request (2026-02-12) "Output guidance when running vectl claim".

Requirements addressed:
  - R1: Claim success can display a clearly separated Guidance block.
  - R2: Step-level evidence template (copy/paste) supported via Step.evidence_template.
  - R3: Project-level guidance supported via Plan.project_guidance.
  - R4: Output is bounded (limits refs and text length).
  - R5: Guidance can be disabled by caller (CLI/MCP flags handled by caller).
  - R6: MCP can return structured guidance fields (GuidancePayload).
"""

from __future__ import annotations

from typing import Final

from pydantic import BaseModel

from vectl.models import AffinityMode, Phase, Plan, Step

GUIDANCE_BEGIN: Final[str] = "--- VECTL:GUIDANCE:BEGIN ---"
GUIDANCE_END: Final[str] = "--- VECTL:GUIDANCE:END ---"


class GuidancePayload(BaseModel):
    """Structured guidance returned by MCP.

    Source: FR R6 — MCP should return structured guidance fields.
    """

    refs: list[str]
    evidence_template: str
    project_guidance: str
    markdown: str
    truncated: bool


# @invar:allow function_size: Guidance assembly keeps ordered bounded markdown sections together for claim output compatibility.
def build_claim_guidance(plan: Plan, phase: Phase, step: Step) -> GuidancePayload:
    """Build bounded guidance payload for a claimed step.

    Source: FR R1-R4.
    """

    refs: list[str] = []
    if plan.strategy_ref.strip():
        refs.append(plan.strategy_ref.strip())
    refs.extend([r.strip() for r in step.refs if r.strip()])
    refs = _dedupe_keep_order(refs)[:3]

    evidence_template = (step.evidence_template or "").strip() or _default_evidence_template()
    project_guidance = (plan.project_guidance or "").strip()
    # R1 Source: Bug report "vectl show <step> does not display parent phase description or context"
    # Phase context carries operational guidance for all steps in the phase.
    phase_context = (phase.context or "").strip()

    truncated = False
    evidence_template, t1 = _truncate_text(evidence_template, max_chars=900)
    project_guidance, t2 = _truncate_text(project_guidance, max_chars=600)
    phase_context, t3 = _truncate_text(phase_context, max_chars=400)
    truncated = (
        t1
        or t2
        or t3
        or (
            len(refs)
            < len(
                _dedupe_keep_order(
                    ([plan.strategy_ref.strip()] if plan.strategy_ref.strip() else [])
                    + [r.strip() for r in step.refs if r.strip()]
                )
            )
        )
    )

    md_lines: list[str] = [
        GUIDANCE_BEGIN,
        "## Guidance",
        "",
    ]

    # R1 Source: Bug report - phase context should be visible in guidance block
    if phase_context:
        md_lines.extend(["### Phase context", phase_context.rstrip(), ""])

    if refs:
        md_lines.extend(["### Before you start", *[f"- {r}" for r in refs], ""])

    md_lines.extend(
        [
            "### Evidence template (paste into complete)",
            "```text",
            evidence_template.rstrip(),
            "```",
            "",
        ]
    )

    if project_guidance:
        md_lines.extend(["### Project rules", project_guidance.rstrip(), ""])

    # RFC: docs/RFC-affinity.md
    # Include affinity note when step has exclusive affinity
    if step.agent:
        effective_affinity = step.affinity or plan.default_affinity
        if effective_affinity == AffinityMode.EXCLUSIVE:
            md_lines.extend(
                [
                    "### Affinity",
                    f"This step has **exclusive affinity** for '{step.agent}'.",
                    "Only this agent should claim it, or use `force=true` to override.",
                    "",
                ]
            )
        elif effective_affinity == AffinityMode.SUGGESTED and step.agent:
            md_lines.extend(
                [
                    "### Affinity",
                    f"This step is **suggested** for '{step.agent}'.",
                    "",
                ]
            )

    # Duplicate step ID guidance
    md_lines.extend(
        [
            "### Step ID Uniqueness",
            "Step IDs must be **globally unique** across ALL phases.",
            "When adding new steps, use qualified IDs like `phase-name.step-id`.",
            "If you encounter duplicate ID errors, run `vectl migrate-step-id --dry-run`.",
            "",
        ]
    )

    if truncated:
        md_lines.append("*(Guidance truncated to stay bounded.)*\n")

    md_lines.append(GUIDANCE_END)
    markdown = "\n".join(md_lines).rstrip() + "\n"

    return GuidancePayload(
        refs=refs,
        evidence_template=evidence_template,
        project_guidance=project_guidance,
        markdown=markdown,
        truncated=truncated,
    )


def _default_evidence_template() -> str:
    """Default evidence template.

    Source: FR R2 includes an example template and explicitly allows flexibility.
    We keep the default short to satisfy R4.
    """

    return (
        "Artifact:\n"
        "- PR: <url> (or commit hash)\n"
        "\n"
        "Verification:\n"
        "- Run: <cmd> -> <outcome>\n"
        "\n"
        "Spec link:\n"
        "- <proposal/issue/doc section>\n"
    )


def _dedupe_keep_order(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for it in items:
        if it in seen:
            continue
        seen.add(it)
        out.append(it)
    return out


def _truncate_text(text: str, *, max_chars: int) -> tuple[str, bool]:
    if not text:
        return text, False
    if len(text) <= max_chars:
        return text, False
    # Keep it copy/paste friendly.
    return text[: max_chars - 1] + "…", True
