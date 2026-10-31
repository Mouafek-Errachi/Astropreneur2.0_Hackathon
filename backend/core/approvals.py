"""The ONLY path that sends or records anything. Agents create pending actions; humans approve."""
import json
from datetime import datetime
from typing import Optional

from fastapi import HTTPException
from sqlmodel import Session, select

from backend.config import get_settings
from backend.core import audit
from backend.core.security import Principal
from backend.features.scheduling.calendar import is_free
from backend.models import Action, CalendarEvent, Candidate, Email, Followup, OutboxMessage, as_utc, utcnow


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
            acknowledge_risk: bool = False, selected_slot: Optional[int] = None) -> Action:
    if not principal.is_human:
        raise HTTPException(403, "Only humans can approve")
    if action.status != "pending":
        raise HTTPException(409, f"Action already {action.status}")
    if action.risk_level == "high" and not acknowledge_risk:
        raise HTTPException(400, "High-risk action: set acknowledge_risk=true")
    if (get_settings().four_eyes_decisions and action.type == "record_decision"
            and action.created_by == principal.subject):
        raise HTTPException(403, "Four-eyes rule: a decision about a candidate must be approved by someone else")
    if action.type == "schedule_interview":
        _pick_slot(session, action, selected_slot)
    action.status, action.decided_by, action.decided_at, action.decision_note = "approved", principal.subject, utcnow(), note
    _execute(session, action)
    audit.log(session, actor=principal.subject, event="action.approved", tenant_id=action.tenant_id,
              target=f"action:{action.id}", detail={"type": action.type, "result": action.result})
    session.refresh(action)
    return action


def reject(session: Session, action: Action, principal: Principal, note: str = "") -> Action:
    if not principal.is_human:
        raise HTTPException(403, "Only humans can reject")
    if action.status != "pending":
        raise HTTPException(409, f"Action already {action.status}")
    action.status, action.decided_by, action.decided_at, action.decision_note = "rejected", principal.subject, utcnow(), note
    session.add(action)
    session.commit()
    audit.log(session, actor=principal.subject, event="action.rejected", tenant_id=action.tenant_id, target=f"action:{action.id}")
    session.refresh(action)
    return action


def cancel(session: Session, action: Action, reason: str, actor: str = "system") -> None:
    """Withdraws a pending action that is no longer needed (e.g. the candidate replied). Never sends anything."""
    if action.status != "pending":
        return
    action.status, action.decided_by, action.decided_at, action.decision_note = "cancelled", actor, utcnow(), reason
    session.add(action)
    audit.log(session, actor=actor, event="action.cancelled", tenant_id=action.tenant_id,
              target=f"action:{action.id}", detail={"reason": reason}, commit=False)


def _pick_slot(session: Session, action: Action, selected_slot: Optional[int]) -> None:
    payload = json.loads(action.payload)
    slots = payload.get("slots", [])
    i = selected_slot or 0
    if not 0 <= i < len(slots):
        raise HTTPException(400, f"selected_slot must be between 0 and {len(slots) - 1}")
    start, end = datetime.fromisoformat(slots[i]["start"]), datetime.fromisoformat(slots[i]["end"])
    if not is_free(session, action.tenant_id, payload["interviewer"], start, end):
        raise HTTPException(409, "This slot is no longer free. Reject this proposal and propose new slots.")
    payload["selected_slot"] = i
    action.payload = json.dumps(payload)


def _execute(session: Session, action: Action) -> None:
    payload = json.loads(action.payload)
    if action.type in ("send_email", "send_reminder"):
        _send(session, action, payload["to"], payload.get("subject", ""), payload.get("body", ""))
        action.result = f"email queued to {payload['to']}"
        if payload.get("email_id"):
            _mark_email_handled(session, action.tenant_id, payload["email_id"])
        if payload.get("followup_id"):
            _close_followup(session, action.tenant_id, payload["followup_id"])
    elif action.type == "record_decision":
        cand = session.get(Candidate, payload["candidate_id"])
        if cand and cand.tenant_id == action.tenant_id:
            cand.status = payload["decision"]
            session.add(cand)
        action.result = f"candidate {payload['candidate_id']} -> {payload['decision']}"
    elif action.type == "schedule_interview":
        slot = payload["slots"][payload.get("selected_slot", 0)]
        start, end = as_utc(datetime.fromisoformat(slot["start"])), as_utc(datetime.fromisoformat(slot["end"]))
        session.add(CalendarEvent(tenant_id=action.tenant_id, interviewer=payload["interviewer"],
                                  candidate_id=payload["candidate_id"], title=action.title,
                                  start=start, end=end, action_id=action.id))
        action.result = f"interview booked {start:%a %d %b %H:%M} with {payload['interviewer']}"
        if payload.get("to"):
            when = f"{start:%A %d %B %Y, %H:%M}-{end:%H:%M} UTC"
            _send(session, action, payload["to"], payload.get("subject", ""), payload.get("body", "").replace("{slot}", when))
            action.result += f"; invitation queued to {payload['to']}"
        cand = session.get(Candidate, payload["candidate_id"])
        if cand and cand.tenant_id == action.tenant_id and cand.status in ("new", "screened", "shortlisted"):
            cand.status = "interview"
            session.add(cand)
    else:
        raise HTTPException(400, f"Unknown action type: {action.type}")
    action.status, action.executed_at = "executed", utcnow()
    session.add(action)
    session.commit()


def _send(session: Session, action: Action, to: str, subject: str, body: str) -> None:
    session.add(OutboxMessage(tenant_id=action.tenant_id, action_id=action.id, to=to, subject=subject, body=body))
    # Remember when we last wrote to this person: candidate follow-ups are computed from it.
    for cand in session.exec(select(Candidate).where(Candidate.tenant_id == action.tenant_id,
                                                     Candidate.email == to.lower())):
        cand.last_contact_at = utcnow()
        session.add(cand)


def _mark_email_handled(session: Session, tenant_id: int, email_id: int) -> None:
    email = session.get(Email, email_id)
    if email and email.tenant_id == tenant_id:
        email.handled = True
        session.add(email)
    for f in session.exec(select(Followup).where(Followup.tenant_id == tenant_id, Followup.ref_type == "email",
                                                 Followup.ref_id == email_id, Followup.status == "open")):
        f.status = "done"
        session.add(f)


def _close_followup(session: Session, tenant_id: int, followup_id: int) -> None:
    f = session.get(Followup, followup_id)
    if f and f.tenant_id == tenant_id:
        f.status = "done"
        session.add(f)
