"""The ONLY path that sends or records anything. Agents create pending actions; humans approve."""
import json

from fastapi import HTTPException
from sqlmodel import Session

from backend.core import audit
from backend.core.security import Principal
from backend.models import Action, Candidate, OutboxMessage, utcnow


def propose(session: Session, *, tenant_id: int, type: str, title: str, payload: dict,
            rationale: str = "", risk_level: str = "none", created_by: str = "agent") -> Action:
    action = Action(tenant_id=tenant_id, type=type, title=title, payload=json.dumps(payload),
                    rationale=rationale, risk_level=risk_level, created_by=created_by)
    session.add(action)
    session.commit()
    session.refresh(action)
    audit.log(session, actor=created_by, event="action.proposed", tenant_id=tenant_id, target=f"action:{action.id}")
    session.refresh(action)
    return action


def approve(session: Session, action: Action, principal: Principal, note: str = "",
            acknowledge_risk: bool = False) -> Action:
    if not principal.is_human:
        raise HTTPException(403, "Only humans can approve")
    if action.status != "pending":
        raise HTTPException(409, f"Action already {action.status}")
    if action.risk_level == "high" and not acknowledge_risk:
        raise HTTPException(400, "High-risk action: set acknowledge_risk=true")
    action.status, action.decided_by, action.decided_at, action.decision_note = "approved", principal.subject, utcnow(), note
    _execute(session, action)
    audit.log(session, actor=principal.subject, event="action.approved", tenant_id=action.tenant_id, target=f"action:{action.id}")
    session.refresh(action)
    return action


def reject(session: Session, action: Action, principal: Principal, note: str = "") -> Action:
    if action.status != "pending":
        raise HTTPException(409, f"Action already {action.status}")
    action.status, action.decided_by, action.decided_at, action.decision_note = "rejected", principal.subject, utcnow(), note
    session.add(action)
    session.commit()
    audit.log(session, actor=principal.subject, event="action.rejected", tenant_id=action.tenant_id, target=f"action:{action.id}")
    session.refresh(action)
    return action


def _execute(session: Session, action: Action) -> None:
    payload = json.loads(action.payload)
    if action.type in ("send_email", "send_reminder"):
        session.add(OutboxMessage(tenant_id=action.tenant_id, action_id=action.id, to=payload["to"],
                                  subject=payload.get("subject", ""), body=payload.get("body", "")))
        action.result = f"email queued to {payload['to']}"
    elif action.type == "record_decision":
        cand = session.get(Candidate, payload["candidate_id"])
        if cand and cand.tenant_id == action.tenant_id:
            cand.status = payload["decision"]
            session.add(cand)
        action.result = f"candidate {payload['candidate_id']} -> {payload['decision']}"
    # TODO: schedule_interview -> CalendarEvent
    action.status, action.executed_at = "executed", utcnow()
    session.add(action)
    session.commit()
