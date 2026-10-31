from datetime import timedelta

from sqlmodel import Session, select

from backend.models import Email, Followup, utcnow

UNANSWERED_AFTER = timedelta(hours=48)


def scan_unanswered(session: Session, tenant_id: int) -> list[Followup]:
    """Creates follow-ups for emails needing a reply for more than 48h.
    TODO: candidates with no contact for N days + proposed reminder drafts."""
    created = []
    cutoff = utcnow() - UNANSWERED_AFTER
    emails = session.exec(select(Email).where(Email.tenant_id == tenant_id, Email.needs_reply == True,  # noqa: E712
                                              Email.handled == False, Email.received_at < cutoff)).all()  # noqa: E712
    for e in emails:
        exists = session.exec(select(Followup).where(Followup.ref_type == "email", Followup.ref_id == e.id)).first()
        if not exists:
            f = Followup(tenant_id=tenant_id, kind="email_unanswered", ref_type="email", ref_id=e.id,
                         title=f"Unanswered for 48h+: {e.subject or e.sender}")
            session.add(f)
            created.append(f)
    session.commit()
    return created
