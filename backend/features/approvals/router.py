import json
from typing import Optional

from fastapi import APIRouter, Depends
from sqlmodel import Session, select

from backend.core import approvals
from backend.core.security import Principal, get_owned, require
from backend.db import get_session
from backend.models import Action, OutboxMessage
from backend.schemas import ActionOut, ApproveRequest, RejectRequest

router = APIRouter(prefix="/approvals", tags=["approvals"])


def action_out(a: Action) -> ActionOut:
    return ActionOut(**a.model_dump(exclude={"payload", "risk_flags", "tenant_id"}),
                     payload=json.loads(a.payload), risk_flags=json.loads(a.risk_flags))


@router.get("", response_model=list[ActionOut])
def list_actions(status: Optional[str] = None, p: Principal = Depends(require("approvals:read")),
                 session: Session = Depends(get_session)):
    q = select(Action).where(Action.tenant_id == p.tenant_id)
    if status:
        q = q.where(Action.status == status)
    return [action_out(a) for a in session.exec(q.order_by(Action.created_at.desc())).all()]


@router.post("/{action_id}/approve", response_model=ActionOut)
def approve(action_id: int, body: ApproveRequest, p: Principal = Depends(require("approvals:decide", human_only=True)),
            session: Session = Depends(get_session)):
    a = get_owned(session, Action, action_id, p)
    if a.status == "pending" and (body.edited_subject or body.edited_body):
        payload = json.loads(a.payload)
        payload["subject"] = body.edited_subject or payload.get("subject")
        payload["body"] = body.edited_body or payload.get("body")
        a.payload = json.dumps(payload)
    return action_out(approvals.approve(session, a, p, body.note, body.acknowledge_risk))


@router.post("/{action_id}/reject", response_model=ActionOut)
def reject(action_id: int, body: RejectRequest, p: Principal = Depends(require("approvals:decide", human_only=True)),
           session: Session = Depends(get_session)):
    return action_out(approvals.reject(session, get_owned(session, Action, action_id, p), p, body.note))


@router.get("/outbox")
def outbox(p: Principal = Depends(require("approvals:read")), session: Session = Depends(get_session)):
    return session.exec(select(OutboxMessage).where(OutboxMessage.tenant_id == p.tenant_id)).all()
