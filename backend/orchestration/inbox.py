"""Inbox workflows: classify -> digest by urgency -> draft reply (queued for approval)."""
import json
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

from sqlmodel import Session, select

from backend.agents import classifier, drafter
from backend.config import get_settings
from backend.data.models import Application, Candidate, EmailMessage, JobOffer, ProposedAction, utcnow
from backend.orchestration import followups, workflow

URGENCIES = ("high", "medium", "low")
URGENCY_ORDER = {"high": 0, "medium": 1, "low": 2, None: 3}


def classify_pending(session: Session, tenant_id: int) -> int:
    rows = session.exec(select(EmailMessage).where(EmailMessage.tenant_id == tenant_id,
                                                   EmailMessage.category == None,  # noqa: E711
                                                   EmailMessage.quarantined == False)).all()  # noqa: E712
    with ThreadPoolExecutor(max_workers=max(1, get_settings().llm_concurrency)) as pool:  # LLM calls only
        results = list(pool.map(lambda e: classifier.classify(e.subject, e.body)[0], rows))
    for e, result in zip(rows, results):
        e.category, e.urgency, e.summary, e.needs_reply = result.category, result.urgency, result.summary, result.needs_reply
        if result.injection_attempt_observed and e.risk_level == "none":
            e.risk_level = "low"
        session.add(e)
    session.commit()
    return len(rows)


def digest(emails: list[EmailMessage], hours: int) -> dict:
    """Deterministic digest of the inbox window, grouped by type and urgency. Built from classified fields only,
    so text inside an email cannot rewrite the digest."""
    since = (utcnow() - timedelta(hours=hours)).replace(tzinfo=None)
    emails = [e for e in emails if e.received_at.replace(tzinfo=None) >= since]
    open_ = [e for e in emails if not e.handled and not e.quarantined]
    classified = [e for e in emails if e.category]
    groups = []
    for category, count in Counter(e.category for e in classified).most_common():
        in_cat = [e for e in classified if e.category == category]
        groups.append({"category": category, "total": count, **{u: sum(e.urgency == u for e in in_cat) for u in URGENCIES}})
    needs_reply = [{"id": str(e.id), "sender": e.sender, "subject": e.subject, "urgency": e.urgency, "summary": e.summary}
                   for e in sorted(open_, key=lambda e: URGENCY_ORDER.get(e.urgency, 3)) if e.needs_reply]
    urgent = [x for x in needs_reply if x["urgency"] == "high"]
    quarantined = sum(e.quarantined for e in emails)
    flagged = [{"id": str(e.id), "sender": e.sender, "subject": e.subject, "risk": e.risk_level}
               for e in emails if e.risk_level in ("medium", "high")]
    headline = (f"{len(emails)} emails in the last {hours}h: {len(urgent)} urgent, {len(needs_reply)} need a human "
                f"answer, {len(flagged)} flagged as suspicious ({quarantined} quarantined).")
    if len(classified) + quarantined < len(emails):
        headline += f" {len(emails) - len(classified) - quarantined} not classified yet."
    return {
        "hours": hours, "headline": headline, "total": len(emails),
        "unread_by_urgency": dict(Counter(e.urgency or "unclassified" for e in open_)),
        "by_category": dict(Counter(e.category for e in classified)),
        "groups": groups, "needs_reply": len(needs_reply), "needs_reply_list": needs_reply,
        "quarantined": quarantined, "flagged": flagged, "top_urgent": urgent[:10],
    }


def _candidate_context(session: Session, e: EmailMessage) -> str:
    """Candidate-scoped context: only the sender's own applications."""
    if not e.candidate_id:
        return ""
    cand = session.get(Candidate, e.candidate_id)
    apps = session.exec(select(Application, JobOffer).join(JobOffer, JobOffer.id == Application.job_id)
                        .where(Application.candidate_id == cand.id)).all()
    return "SENDER IS A CANDIDATE. Applications: " + "; ".join(f"{j.title}: status {a.status}" for a, j in apps)


def propose_reply(session: Session, e: EmailMessage, requested_by: str) -> ProposedAction:
    if e.quarantined:
        raise ValueError("quarantined email: review it manually, the agent will not process it")
    draft, fallback = drafter.draft_reply(e.sender, e.subject, e.body, _candidate_context(session, e))
    # The recipient is always the original sender: the LLM never chooses who receives mail.
    return workflow.propose(
        session, tenant_id=e.tenant_id, type="send_email", title=f"Reply to {e.sender}",
        payload={"to": e.sender, "subject": draft.subject, "body": draft.body, "_source_text": e.body},
        rationale="Drafted reply" + (" (fallback template)" if fallback else ""), input_risk=e.risk_level,
        input_flags=json.loads(e.flags), created_by_agent="reply_drafter", requested_by=requested_by,
        source_type="email", source_id=str(e.id))


@workflow.on_resume("email")
def _after_reply(session: Session, action: ProposedAction) -> None:
    import uuid

    if action.status == "executed":
        e = session.get(EmailMessage, uuid.UUID(action.source_id))
        if e:
            e.handled = True
            session.add(e)
            followups.close_email_followups(session, e.tenant_id, str(e.id))
