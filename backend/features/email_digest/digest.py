from collections import Counter

from backend.models import Email


def build_digest(emails: list[Email]) -> dict:
    """Deterministic daily digest. TODO: optional LLM-written headline (DigestSummary)."""
    classified = [e for e in emails if e.category]
    return {
        "total": len(emails),
        "unclassified": len(emails) - len(classified),
        "by_category": dict(Counter(e.category for e in classified)),
        "needs_reply": sum(e.needs_reply and not e.handled for e in classified),
        "urgent": [{"id": e.id, "sender": e.sender, "summary": e.summary}
                   for e in classified if e.priority == "high" and not e.handled],
        "flagged": [{"id": e.id, "sender": e.sender, "risk": e.risk_level}
                    for e in emails if e.risk_level in ("medium", "high")],
    }
