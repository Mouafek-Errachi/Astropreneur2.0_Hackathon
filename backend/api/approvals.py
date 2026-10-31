import json
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, select

from backend.data.db import get_session
from backend.data.models import Approval, ProposedAction, SentEmail
from backend.schemas import ApproveRequest, RejectRequest
from backend.security import executor
from backend.security.gateway import Principal, get_owned, require

router = APIRouter(prefix="/approvals", tags=["approvals (HITL)"])


def action_out(session: Session, a: ProposedAction) -> dict:
    appr = session.exec(select(Approval).where(Approval.action_id == a.id)).first()
    return {"id": str(a.id), "type": a.type, "status": a.status, "title": a.title, "rationale": a.rationale,
            "payload": json.loads(a.payload), "risk_level": a.risk_level, "risk_flags": json.loads(a.risk_flags),
            "created_by_agent": a.created_by_agent, "requested_by": a.requested_by, "created_at": a.created_at,
            "executed_at": a.executed_at, "result": a.result, "source_type": a.source_type,
            "approval": {"reviewer": appr.reviewer_email, "decision": appr.decision, "decided_at": appr.decided_at,
                         "signature": appr.signature[:16] + "…"} if appr else None}


def _get(session: Session, action_id: str, p: Principal) -> ProposedAction:
    try:
        return get_owned(session, ProposedAction, uuid.UUID(action_id), p)
    except ValueError:
        raise HTTPException(404, "Not found")


@router.get("")
def list_actions(status: Optional[str] = None, p: Principal = Depends(require("approvals:read")),
                 session: Session = Depends(get_session)):
    q = select(ProposedAction).where(ProposedAction.tenant_id == p.tenant_id)
    if status:
        q = q.where(ProposedAction.status == status)
    return [action_out(session, a) for a in session.exec(q.order_by(ProposedAction.created_at.desc())).all()]


@router.post("/{action_id}/approve")
def approve(action_id: str, body: ApproveRequest, p: Principal = Depends(require("approvals:decide", human_only=True)),
            session: Session = Depends(get_session)):
    edits = {"subject": body.edited_subject, "body": body.edited_body, "selected_slots": body.selected_slots}
    a = executor.approve(session, _get(session, action_id, p), p, note=body.note,
                         edits={k: v for k, v in edits.items() if v is not None}, acknowledge_risk=body.acknowledge_risk)
    return action_out(session, a)


@router.post("/{action_id}/reject")
def reject(action_id: str, body: RejectRequest, p: Principal = Depends(require("approvals:decide", human_only=True)),
           session: Session = Depends(get_session)):
    return action_out(session, executor.reject(session, _get(session, action_id, p), p, body.note))


@router.get("/sent")
def sent(p: Principal = Depends(require("approvals:read")), session: Session = Depends(get_session)):
    rows = session.exec(select(SentEmail).where(SentEmail.tenant_id == p.tenant_id).order_by(SentEmail.sent_at.desc()))
    return [{"to": m.to, "subject": m.subject, "body": m.body, "transport": m.transport, "message_id": m.message_id,
             "sent_at": m.sent_at} for m in rows]
