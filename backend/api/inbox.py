import hashlib
import hmac
import json
import time
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import ValidationError
from sqlmodel import Session, select

from backend.config import get_settings
from backend.data.db import get_session
from backend.data.models import EmailMessage, Tenant
from backend.orchestration import inbox, ingestion
from backend.schemas import EmailIn
from backend.security import events
from backend.security.gateway import Principal, get_owned, require

router = APIRouter(tags=["inbox"])


def email_out(e: EmailMessage) -> dict:
    return {"id": str(e.id), "sender": e.sender, "subject": e.subject, "body": e.body, "received_at": e.received_at,
            "category": e.category, "urgency": e.urgency, "summary": e.summary, "needs_reply": e.needs_reply,
            "risk_level": e.risk_level, "flags": json.loads(e.flags), "quarantined": e.quarantined, "handled": e.handled,
            "is_candidate": e.candidate_id is not None}


@router.post("/emails")
def ingest_email(body: EmailIn, p: Principal = Depends(require("emails:ingest")), session: Session = Depends(get_session)):
    e = ingestion.ingest_email(session, p.tenant_id, sender=body.sender, subject=body.subject, body=body.body,
                               external_id=body.external_id, received_at=body.received_at, actor=p.subject)
    return email_out(e)


@router.post("/webhooks/email")
async def email_webhook(request: Request, session: Session = Depends(get_session)):
    """Signed inbound webhook for n8n: X-Timestamp + X-Signature = hex HMAC-SHA256(secret, f"{ts}.{raw_body}")."""
    s = get_settings()
    raw = await request.body()
    ts, sig = request.headers.get("X-Timestamp", ""), request.headers.get("X-Signature", "")
    expected = hmac.new(s.webhook_secret.encode(), f"{ts}.".encode() + raw, hashlib.sha256).hexdigest()
    if not ts.isdigit() or abs(time.time() - int(ts)) > s.webhook_tolerance_seconds or not hmac.compare_digest(sig, expected):
        events.record(session, tenant_id=None, source="auth", kind="auth_denied", severity="medium",
                      detail={"endpoint": "webhooks/email"})
        raise HTTPException(401, "Invalid signature")
    try:
        body = EmailIn.model_validate_json(raw)
    except ValidationError as e:
        raise HTTPException(422, e.errors(include_url=False, include_context=False))
    tenant = session.exec(select(Tenant)).first()  # single-tenant webhook; TODO: one secret per tenant
    e = ingestion.ingest_email(session, tenant.id, sender=body.sender, subject=body.subject, body=body.body,
                               external_id=body.external_id, received_at=body.received_at, actor="n8n-webhook")
    return {"id": str(e.id), "risk_level": e.risk_level, "quarantined": e.quarantined}


@router.get("/emails")
def list_emails(p: Principal = Depends(require("emails:read")), session: Session = Depends(get_session)):
    rows = session.exec(select(EmailMessage).where(EmailMessage.tenant_id == p.tenant_id)).all()
    rows.sort(key=lambda e: (e.handled, inbox.URGENCY_ORDER.get(e.urgency, 3), -e.received_at.timestamp()))
    return [email_out(e) for e in rows]


@router.post("/emails/classify")
def classify(p: Principal = Depends(require("emails:process")), session: Session = Depends(get_session)):
    return {"classified": inbox.classify_pending(session, p.tenant_id)}


@router.get("/emails/digest")
def digest(hours: int = Query(24 * 7, ge=1, le=24 * 30), p: Principal = Depends(require("emails:read")),
           session: Session = Depends(get_session)):
    return inbox.digest(session.exec(select(EmailMessage).where(EmailMessage.tenant_id == p.tenant_id)).all(), hours)


@router.post("/emails/{email_id}/draft-reply")
def draft_reply(email_id: str, p: Principal = Depends(require("drafts:create")), session: Session = Depends(get_session)):
    try:
        e = get_owned(session, EmailMessage, uuid.UUID(email_id), p)
        a = inbox.propose_reply(session, e, p.subject)
    except ValueError as err:
        raise HTTPException(409 if "quarantined" in str(err) else 404, str(err))
    return {"action_id": str(a.id), "status": a.status, "risk_level": a.risk_level, "payload": json.loads(a.payload)}
