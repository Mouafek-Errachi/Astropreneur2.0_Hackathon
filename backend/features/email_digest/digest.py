from collections import Counter

from backend.models import Email

PRIORITIES = ("high", "medium", "low")


def build_digest(emails: list[Email], hours: int) -> dict:
    """Deterministic digest of the inbox window, grouped by type and urgency. Built from classified fields only,
    so text inside an email cannot rewrite the digest."""
    classified = [e for e in emails if e.category]
    groups = []
    for category, count in Counter(e.category for e in classified).most_common():
        in_cat = [e for e in classified if e.category == category]
        groups.append({"category": category, "total": count,
                       **{p: sum(e.priority == p for e in in_cat) for p in PRIORITIES}})
    needs_reply = [{"id": e.id, "sender": e.sender, "subject": e.subject, "priority": e.priority, "summary": e.summary}
                   for e in sorted(classified, key=lambda e: PRIORITIES.index(e.priority or "low"))
                   if e.needs_reply and not e.handled]
    urgent = [{"id": e.id, "sender": e.sender, "summary": e.summary}
              for e in classified if e.priority == "high" and not e.handled]
    flagged = [{"id": e.id, "sender": e.sender, "subject": e.subject, "risk": e.risk_level}
               for e in emails if e.risk_level in ("medium", "high")]
    headline = (f"{len(emails)} emails in the last {hours}h: {len(urgent)} urgent, {len(needs_reply)} need a human "
                f"answer, {len(flagged)} flagged as suspicious.")
    if len(classified) < len(emails):
        headline += f" {len(emails) - len(classified)} not classified yet."
    return {
        "hours": hours,
        "headline": headline,
        "total": len(emails),
        "unclassified": len(emails) - len(classified),
        "by_category": dict(Counter(e.category for e in classified)),
        "by_priority": {p: sum(e.priority == p for e in classified) for p in PRIORITIES},
        "groups": groups,
        "needs_reply": needs_reply,
        "urgent": urgent,
        "flagged": flagged,
    }
