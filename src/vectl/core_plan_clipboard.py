"""Plan clipboard mutation helpers."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from vectl.models import Clipboard, Plan, PlanError


CLIPBOARD_SUMMARY_MAX = 80
CLIPBOARD_CONTENT_MAX = 8000
CLIPBOARD_TTL_DEFAULT_HOURS = 24


def _clipboard_expired(cb: Clipboard) -> bool:
    """Check if a clipboard entry has expired."""
    try:
        expires = datetime.fromisoformat(cb.expires_at.replace("Z", "+00:00"))
        return datetime.now(timezone.utc) > expires
    except (ValueError, AttributeError):
        return True


# @shell_complexity: Branches preserve author/content/size validation, soft summary truncation, and TTL timestamp assignment.
def clipboard_write(
    plan: Plan,
    author: str,
    summary: str,
    content: str,
    ttl: int = CLIPBOARD_TTL_DEFAULT_HOURS,
) -> Plan:
    """Write to the plan clipboard.

    >>> p = Plan(project="test")
    >>> p = clipboard_write(p, "agent-1", "Summary", "Content here", ttl=12)
    >>> p.clipboard is not None
    True
    >>> p.clipboard.author
    'agent-1'
    """
    if not author or not author.strip():
        raise PlanError("Clipboard author cannot be empty")

    if not content or not content.strip():
        raise PlanError("Clipboard content cannot be empty or whitespace-only")

    if len(content) > CLIPBOARD_CONTENT_MAX:
        raise PlanError(
            f"Content exceeds {CLIPBOARD_CONTENT_MAX} char limit ({len(content)} chars). "
            "Shorten or split into step evidence."
        )

    if len(summary) > CLIPBOARD_SUMMARY_MAX:
        summary = summary[: CLIPBOARD_SUMMARY_MAX - 1] + "…"

    now = datetime.now(timezone.utc)
    expires = now + timedelta(hours=ttl)

    plan.clipboard = Clipboard(
        author=author.strip(),
        summary=summary,
        content=content,
        written_at=now.isoformat().replace("+00:00", "Z"),
        expires_at=expires.isoformat().replace("+00:00", "Z"),
    )

    return plan


def clipboard_clear(plan: Plan) -> Plan:
    """Clear the plan clipboard.

    >>> p = clipboard_write(Plan(project="test"), "a", "s", "c")
    >>> p.clipboard is not None
    True
    >>> p = clipboard_clear(p)
    >>> p.clipboard is None
    True
    """
    plan.clipboard = None
    return plan
