"""Scheduling workflow: free slots -> proposed invitation (approval) -> executor emails a single-use
magic link -> the candidate picks a slot on the public page -> calendar event."""
import json
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException
from sqlmodel import Session, select

from backend.config import get_settings
from backend.data.models import (Application, CalendarEvent, Candidate, JobOffer, ProposedAction, SchedulingToken,
                                 utcnow)
from backend.orchestration import workflow
from backend.security import audit

WORK_START, WORK_END = 9, 17


def interviewers() -> dict:
    path = get_settings().data_dir / "calendar.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def busy(session: Session, tenant_id: int, interviewer: str) -> list[tuple[datetime, datetime]]:
    blocks = [(datetime.fromisoformat(b["start"]), datetime.fromisoformat(b["end"]))
              for b in interviewers().get(interviewer, [])]
    for ev in session.exec(select(CalendarEvent).where(CalendarEvent.tenant_id == tenant_id,
                                                       CalendarEvent.interviewer == interviewer)):
        blocks.append((ev.start.replace(tzinfo=None), ev.end.replace(tzinfo=None)))
    return blocks


def free_slots(blocks, duration_min: int, days_ahead: int, count: int = 3, now: datetime | None = None) -> list[dict]:
    now = now or datetime.now()
    dur, slots = timedelta(minutes=duration_min), []
    day = (now + timedelta(days=1)).replace(hour=WORK_START, minute=0, second=0, microsecond=0)
    for _ in range(days_ahead):
        if day.weekday() < 5:
            start = day
            while start + dur <= day.replace(hour=WORK_END) and len(slots) < count:
                if not any(s < start + dur and start < e for s, e in blocks):
                    slots.append({"start": start.isoformat(), "end": (start + dur).isoformat()})
                start += timedelta(hours=1)
        if len(slots) >= count:
            break
        day += timedelta(days=1)
    return slots


def propose_interview(session: Session, app: Application, interviewer: str, duration: int, days_ahead: int,
                      requested_by: str) -> ProposedAction:
    if interviewer not in interviewers():
        raise HTTPException(404, "Unknown interviewer")
    cand, job = session.get(Candidate, app.candidate_id), session.get(JobOffer, app.job_id)
    if not cand.email:
        raise HTTPException(422, "Candidate has no email address")
    slots = free_slots(busy(session, app.tenant_id, interviewer), duration, days_ahead)
    if not slots:
        raise HTTPException(409, "No free slots found")
    from backend.agents.drafter import template

    draft = template("invitation", name=cand.name.split()[0] if cand.name else "", job=job.title)
    return workflow.propose(
        session, tenant_id=app.tenant_id, type="schedule_interview",
        title=f"Interview {cand.name or cand.email} with {interviewer}",
        payload={"candidate_id": str(cand.id), "to": cand.email, "interviewer": interviewer, "slots": slots,
                 "subject": draft.subject, "body": draft.body},
        rationale=f"{len(slots)} free slots from the interviewer calendar", requested_by=requested_by,
        source_type="application", source_id=str(app.id))


def _aware(iso: str) -> datetime:
    """Calendar times are office wall-clock times; stored with a UTC marker for the DB."""
    return datetime.fromisoformat(iso).replace(tzinfo=timezone.utc)


def _valid_token(session: Session, jti: str) -> SchedulingToken:
    tok = session.get(SchedulingToken, jti)
    if not tok or tok.used or tok.expires_at.replace(tzinfo=None) < utcnow().replace(tzinfo=None):
        raise HTTPException(404, "This link is invalid or has expired")
    return tok


def public_view(session: Session, jti: str) -> dict:
    tok = _valid_token(session, jti)
    return {"interviewer": tok.interviewer, "slots": json.loads(tok.slots)}


def public_choose(session: Session, jti: str, slot_index: int) -> dict:
    tok = _valid_token(session, jti)
    slots = json.loads(tok.slots)
    if not 0 <= slot_index < len(slots):
        raise HTTPException(400, "Invalid slot")
    slot = slots[slot_index]
    tok.used = True  # single use
    cand = session.get(Candidate, tok.candidate_id)
    cand.stage = "interview"
    session.add(CalendarEvent(tenant_id=tok.tenant_id, interviewer=tok.interviewer, candidate_id=cand.id,
                              title=f"Interview: {cand.name or cand.email}",
                              start=_aware(slot["start"]), end=_aware(slot["end"])))
    session.add(tok)
    session.add(cand)
    session.commit()
    audit.log(session, actor=f"candidate:{cand.id}", event="interview.booked", tenant_id=tok.tenant_id,
              target=f"candidate:{cand.id}", detail=slot)
    return {"booked": slot}
