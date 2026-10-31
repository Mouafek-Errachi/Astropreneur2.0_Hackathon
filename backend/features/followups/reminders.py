from datetime import timedelta

from sqlmodel import Session, select

from backend.config import get_settings
from backend.core import approvals
from backend.features.replies.drafter import draft_reminder
from backend.features.replies.router import output_risk
from backend.models import Action, Candidate, Email, Followup, Job, OutboxMessage, as_utc, utcnow


def _open_followup(session: Session, tenant_id: int, ref_type: str, ref_id: int) -> Followup | None:
    return session.exec(select(Followup).where(Followup.tenant_id == tenant_id, Followup.ref_type == ref_type,
                                               Followup.ref_id == ref_id, Followup.status == "open")).first()


def scan_unanswered(session: Session, tenant_id: int) -> list[Followup]:
    """Creates follow-ups for inbox emails that need a reply and have waited too long."""
    created = []
    cutoff = utcnow() - timedelta(hours=get_settings().unanswered_email_hours)
    emails = session.exec(select(Email).where(Email.tenant_id == tenant_id, Email.needs_reply == True,  # noqa: E712
                                              Email.handled == False)).all()  # noqa: E712
    for e in emails:
        exists = session.exec(select(Followup).where(Followup.tenant_id == tenant_id, Followup.ref_type == "email",
                                                     Followup.ref_id == e.id)).first()
        if as_utc(e.received_at) < cutoff and not exists:
            f = Followup(tenant_id=tenant_id, kind="email_unanswered", ref_type="email", ref_id=e.id,
                         title=f"Unanswered for {get_settings().unanswered_email_hours}h+: {e.subject or e.sender}")
            session.add(f)
            created.append(f)
    session.commit()
    return created


def scan_candidates(session: Session, tenant_id: int) -> list[Followup]:
    """Candidates we wrote to who have not answered: open a follow-up and queue a reminder draft for approval."""
    days = get_settings().reminder_after_days
    cutoff = utcnow() - timedelta(days=days)
    created = []
    cands = session.exec(select(Candidate).where(Candidate.tenant_id == tenant_id, Candidate.email != "",
                                                 Candidate.last_contact_at != None,  # noqa: E711
                                                 Candidate.status != "rejected")).all()
    for c in cands:
        contacted = as_utc(c.last_contact_at)
        if contacted > cutoff or _open_followup(session, tenant_id, "candidate", c.id):
            continue
        answers = session.exec(select(Email).where(Email.tenant_id == tenant_id, Email.sender == c.email)).all()
        if any(as_utc(e.received_at) > contacted for e in answers):
            continue
        last = session.exec(select(OutboxMessage).where(OutboxMessage.tenant_id == tenant_id, OutboxMessage.to == c.email)
                            .order_by(OutboxMessage.sent_at.desc())).first()
        waited = (utcnow() - contacted).days
        f = Followup(tenant_id=tenant_id, kind="candidate_no_reply", ref_type="candidate", ref_id=c.id,
                     title=f"No answer from {c.name or c.email} for {waited} days")
        session.add(f)
        session.commit()
        session.refresh(f)
        job = session.get(Job, c.job_id)
        draft, fallback = draft_reminder(c.name, job.title, waited, last.subject if last else "", last.body if last else "")
        action = approvals.propose(
            session, tenant_id=tenant_id, type="send_reminder", title=f"Reminder to {c.name or c.email}",
            payload={"to": c.email, "subject": draft.subject, "body": draft.body, "candidate_id": c.id, "followup_id": f.id},
            rationale=f"No answer for {waited} days" + (" (fallback template)" if fallback else ""),
            risk_level=output_risk(c.risk_level, draft))
        f.action_id = action.id
        session.add(f)
        session.commit()
        created.append(f)
    return created


def close_for_sender(session: Session, tenant_id: int, sender: str) -> int:
    """A candidate wrote back: close their follow-ups and withdraw any reminder still waiting for approval."""
    closed = 0
    for c in session.exec(select(Candidate).where(Candidate.tenant_id == tenant_id, Candidate.email == sender.lower())):
        f = _open_followup(session, tenant_id, "candidate", c.id)
        if not f:
            continue
        f.status = "done"
        session.add(f)
        if f.action_id and (action := session.get(Action, f.action_id)):
            approvals.cancel(session, action, "Candidate replied before the reminder was sent")
        closed += 1
    session.commit()
    return closed
