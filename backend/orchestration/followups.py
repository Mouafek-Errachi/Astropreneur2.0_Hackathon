"""Scheduler workflow (n8n calls POST /followups/scan on a cron).

- Inbox emails that need a reply and waited more than UNANSWERED_EMAIL_HOURS become follow-ups.
- Candidates we wrote to who have not answered for REMINDER_AFTER_DAYS get a follow-up and a reminder draft,
  queued for approval. If the candidate writes back first, the follow-up closes and the reminder is withdrawn.
"""
import uuid
from datetime import timedelta

from sqlmodel import Session, select

from backend.agents.drafter import draft_reminder
from backend.config import get_settings
from backend.data.models import (Application, Candidate, EmailMessage, Followup, JobOffer, ProposedAction, SentEmail,
                                 utcnow)
from backend.orchestration import workflow


def _naive(dt):
    return dt.replace(tzinfo=None) if dt else dt


def _open(session: Session, tenant_id: int, ref_type: str, ref_id: str) -> Followup | None:
    return session.exec(select(Followup).where(Followup.tenant_id == tenant_id, Followup.ref_type == ref_type,
                                               Followup.ref_id == ref_id, Followup.status == "open")).first()


def scan_unanswered(session: Session, tenant_id: int) -> int:
    hours = get_settings().unanswered_email_hours
    cutoff = _naive(utcnow() - timedelta(hours=hours))
    n = 0
    for e in session.exec(select(EmailMessage).where(EmailMessage.tenant_id == tenant_id, EmailMessage.needs_reply == True,  # noqa: E712
                                                     EmailMessage.handled == False, EmailMessage.quarantined == False)):  # noqa: E712
        exists = session.exec(select(Followup).where(Followup.ref_type == "email", Followup.ref_id == str(e.id))).first()
        if _naive(e.received_at) < cutoff and not exists:
            session.add(Followup(tenant_id=tenant_id, kind="email_unanswered", ref_type="email", ref_id=str(e.id),
                                 title=f"Unanswered for {hours}h+: {e.subject or e.sender}"))
            n += 1
    session.commit()
    return n


def scan_candidates(session: Session, tenant_id: int, actor: str) -> int:
    days = get_settings().reminder_after_days
    cutoff = _naive(utcnow() - timedelta(days=days))
    n = 0
    cands = session.exec(select(Candidate).where(Candidate.tenant_id == tenant_id, Candidate.email != "",
                                                 Candidate.last_contact_at != None,  # noqa: E711
                                                 Candidate.stage != "rejected")).all()
    for c in cands:
        contacted = _naive(c.last_contact_at)
        if contacted > cutoff or _open(session, tenant_id, "candidate", str(c.id)):
            continue
        answers = session.exec(select(EmailMessage).where(EmailMessage.tenant_id == tenant_id, EmailMessage.sender == c.email))
        if any(_naive(e.received_at) > contacted for e in answers):
            continue
        last = session.exec(select(SentEmail).where(SentEmail.tenant_id == tenant_id, SentEmail.to == c.email)
                            .order_by(SentEmail.sent_at.desc())).first()
        app = session.exec(select(Application).where(Application.candidate_id == c.id)).first()
        job = session.get(JobOffer, app.job_id) if app else None
        waited = (_naive(utcnow()) - contacted).days
        f = Followup(tenant_id=tenant_id, kind="candidate_no_reply", ref_type="candidate", ref_id=str(c.id),
                     title=f"No answer from {c.name or c.email} for {waited} days")
        session.add(f)
        session.commit()
        draft, fallback = draft_reminder(c.name, job.title if job else "open", waited,
                                         last.subject if last else "", last.body if last else "")
        action = workflow.propose(
            session, tenant_id=tenant_id, type="send_reminder", title=f"Reminder to {c.name or c.email}",
            payload={"to": c.email, "subject": draft.subject, "body": draft.body, "candidate_id": str(c.id),
                     "_source_text": last.body if last else ""},
            rationale=f"No answer for {waited} days" + (" (fallback template)" if fallback else ""),
            created_by_agent="followup_scheduler", requested_by=actor, source_type="followup", source_id=str(f.id))
        f.action_id = action.id
        session.add(f)
        session.commit()
        n += 1
    return n


def scan(session: Session, tenant_id: int, actor: str) -> dict:
    emails = scan_unanswered(session, tenant_id)
    reminders = scan_candidates(session, tenant_id, actor)
    return {"created": emails + reminders, "reminders_proposed": reminders}


def close_for_sender(session: Session, tenant_id: int, sender: str) -> int:
    """A candidate wrote back: close their follow-ups and withdraw any reminder still waiting for approval."""
    closed = 0
    for c in session.exec(select(Candidate).where(Candidate.tenant_id == tenant_id, Candidate.email == sender.lower())):
        f = _open(session, tenant_id, "candidate", str(c.id))
        if not f:
            continue
        f.status = "done"
        session.add(f)
        if f.action_id and (action := session.get(ProposedAction, f.action_id)):
            workflow.cancel(session, action, "Candidate replied before the reminder was sent")
        closed += 1
    session.commit()
    return closed


@workflow.on_resume("followup")
def _after_reminder(session: Session, action: ProposedAction) -> None:
    if action.status == "executed":
        f = session.get(Followup, uuid.UUID(action.source_id))
        if f:
            f.status = "done"
            session.add(f)


def close_email_followups(session: Session, tenant_id: int, email_id: str) -> None:
    for f in session.exec(select(Followup).where(Followup.tenant_id == tenant_id, Followup.ref_type == "email",
                                                 Followup.ref_id == email_id, Followup.status == "open")):
        f.status = "done"
        session.add(f)

