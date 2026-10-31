import json
from datetime import timezone

from fastapi import APIRouter, Depends
from sqlmodel import Session, select

from backend.core import injection
from backend.core.sanitizer import sanitize_text
from backend.core.security import Principal, require
from backend.db import get_session
from backend.features.email_digest.classifier import classify
from backend.features.email_digest.digest import build_digest
from backend.models import Email, utcnow
from backend.schemas import EmailIn, EmailOut

router = APIRouter(prefix="/emails", tags=["email"])
# TODO: signed inbound webhook (HMAC + timestamp) at /webhooks/email


def email_out(e: Email) -> EmailOut:
    return EmailOut(**e.model_dump(exclude={"injection_flags", "tenant_id", "external_id"}),
                    injection_flags=json.loads(e.injection_flags))


@router.post("", response_model=EmailOut)
def ingest_email(body: EmailIn, p: Principal = Depends(require("emails:ingest")), session: Session = Depends(get_session)):
    text, notes = sanitize_text(body.body)
    subject, _ = sanitize_text(body.subject, 300)
    flags = injection.scan(f"{subject}\n{text}", sanitizer_notes=notes)
    received = body.received_at or utcnow()
    if received.tzinfo is None:
        received = received.replace(tzinfo=timezone.utc)
    e = Email(tenant_id=p.tenant_id, sender=body.sender, subject=subject, body=text, external_id=body.external_id,
              received_at=received, injection_flags=json.dumps([f.to_dict() for f in flags]),
              risk_level=injection.risk_level(flags))
    session.add(e)
    session.commit()
    session.refresh(e)
    return email_out(e)


@router.get("", response_model=list[EmailOut])
def list_emails(p: Principal = Depends(require("emails:read")), session: Session = Depends(get_session)):
    rows = session.exec(select(Email).where(Email.tenant_id == p.tenant_id).order_by(Email.received_at.desc())).all()
    return [email_out(e) for e in rows]


@router.post("/classify", response_model=list[EmailOut])
def classify_all(p: Principal = Depends(require("emails:process")), session: Session = Depends(get_session)):
    rows = session.exec(select(Email).where(Email.tenant_id == p.tenant_id, Email.category == None)).all()  # noqa: E711
    for e in rows:
        result, _ = classify(e.subject, e.body)
        e.category, e.priority, e.summary, e.needs_reply = result.category, result.priority, result.summary, result.needs_reply
        session.add(e)
    session.commit()
    return [email_out(e) for e in rows]


@router.get("/digest")
def digest(p: Principal = Depends(require("emails:read")), session: Session = Depends(get_session)):
    return build_digest(session.exec(select(Email).where(Email.tenant_id == p.tenant_id)).all())
