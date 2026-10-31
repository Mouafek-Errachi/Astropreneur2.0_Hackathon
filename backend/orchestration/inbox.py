"""Inbox workflows: classify -> digest by urgency -> draft reply (queued for approval)."""
import json
from collections import Counter

from sqlmodel import Session, select

from backend.agents import classifier, drafter
from backend.data.models import Application, Candidate, EmailMessage, JobOffer, ProposedAction
from backend.orchestration import workflow

URGENCY_ORDER = {"high": 0, "medium": 1, "low": 2, None: 3}


def classify_pending(session: Session, tenant_id: int) -> int:
    rows = session.exec(select(EmailMessage).where(EmailMessage.tenant_id == tenant_id,
                                                   EmailMessage.category == None,  # noqa: E711
                                                   EmailMessage.quarantined == False)).all()  # noqa: E712
    for e in rows:
        result, _ = classifier.classify(e.subject, e.body)
        e.category, e.urgency, e.summary, e.needs_reply = result.category, result.urgency, result.summary, result.needs_reply
        if result.injection_attempt_observed and e.risk_level == "none":
            e.risk_level = "low"
        session.add(e)
    session.commit()
    return len(rows)


def digest(emails: list[EmailMessage]) -> dict:
    open_ = [e for e in emails if not e.handled and not e.quarantined]
    return {
        "total": len(emails),
        "unread_by_urgency": dict(Counter(e.urgency or "unclassified" for e in open_)),
        "by_category": dict(Counter(e.category for e in emails if e.category)),
        "needs_reply": sum(e.needs_reply for e in open_),
        "quarantined": sum(e.quarantined for e in emails),
        "top_urgent": [{"id": str(e.id), "sender": e.sender, "summary": e.summary or e.subject}
                       for e in sorted(open_, key=lambda e: URGENCY_ORDER.get(e.urgency, 3)) if e.urgency == "high"][:10],
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
