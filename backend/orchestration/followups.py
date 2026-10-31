"""Scheduler workflow (n8n calls POST /followups/scan on a cron): unanswered mail and candidates
without contact become follow-ups; candidate reminders are drafted and queued for approval."""
from datetime import timedelta

from sqlmodel import Session, select

from backend.data.models import Application, Candidate, EmailMessage, Followup, utcnow
from backend.orchestration import screening

UNANSWERED_AFTER = timedelta(hours=48)
NO_CONTACT_AFTER = timedelta(days=5)


def _exists(session: Session, ref_type: str, ref_id: str) -> bool:
    return session.exec(select(Followup).where(Followup.ref_type == ref_type, Followup.ref_id == ref_id,
                                               Followup.status == "open")).first() is not None


def scan(session: Session, tenant_id: int, actor: str) -> dict:
    now = utcnow()
    created = reminders = 0
    for e in session.exec(select(EmailMessage).where(EmailMessage.tenant_id == tenant_id, EmailMessage.needs_reply == True,  # noqa: E712
                                                     EmailMessage.handled == False)):  # noqa: E712
        if e.received_at.replace(tzinfo=None) < (now - UNANSWERED_AFTER).replace(tzinfo=None) and not _exists(session, "email", str(e.id)):
            session.add(Followup(tenant_id=tenant_id, kind="email_unanswered", ref_type="email", ref_id=str(e.id),
                                 title=f"Unanswered for 48h+: {e.subject or e.sender}"))
            created += 1
    stale = (now - NO_CONTACT_AFTER).replace(tzinfo=None)
    rows = session.exec(select(Application, Candidate).join(Candidate, Candidate.id == Application.candidate_id)
                        .where(Application.tenant_id == tenant_id, Application.status.in_(["shortlisted", "interview"]))).all()
    for app, cand in rows:
        last = (cand.last_contact_at or app.created_at).replace(tzinfo=None)
        if last < stale and cand.email and not _exists(session, "application", str(app.id)):
            action = screening.propose_candidate_email(session, app, "reminder", requested_by=actor)
            session.add(Followup(tenant_id=tenant_id, kind="candidate_no_contact", ref_type="application",
                                 ref_id=str(app.id), title=f"No contact for 5+ days: {cand.name}", action_id=action.id))
            created += 1
            reminders += 1
    session.commit()
    return {"created": created, "reminders_proposed": reminders}
