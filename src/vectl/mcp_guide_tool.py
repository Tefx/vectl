"""MCP onboarding-guide read tool."""

from returns.result import Result

from vectl.guide import GUIDE_ALL, GUIDE_TOPICS, VALID_TOPICS


def vectl_guide(topic: str | None = None) -> Result[str, str]:
    """Show agent onboarding guide for one topic or the combined guide."""
    if topic is None:
        combined = "\n---\n\n".join(guide.strip() for guide in GUIDE_ALL)
        return combined + "\n\n---\n*Topics: " + ", ".join(VALID_TOPICS) + "*"
    guide = GUIDE_TOPICS.get(topic)
    if guide is None:
        return f"**Error:** Unknown topic '{topic}'. Valid topics: {', '.join(VALID_TOPICS)}."
    return guide.strip()
