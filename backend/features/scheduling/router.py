from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, select

from backend.core import approvals
from backend.core.security import Principal, get_owned, require
from backend.db import get_session
from backend.features.scheduling.calendar import busy_slots, interviewers
from backend.features.scheduling.proposer import propose_slots
from backend.models import CalendarEvent, Candidate, Job
from backend.schemas import ScheduleRequest

router = APIRouter(prefix="/scheduling", tags=["scheduling"])

INVITE_BODY = (
    "Hello {name},\n\nThank you for your application for the {job} position. We would like to invite you to an "
    "interview on {{slot}} with {interviewer}.\n\nPlease reply to confirm, or let us know if this time does not "
    "suit you.\n\nBest regards,\nHR Team"
)


@router.get("/interviewers")
def list_interviewers(p: Principal = Depends(require("scheduling:read"))):
    return interviewers()


@router.get("/events")
def list_events(p: Principal = Depends(require("scheduling:read")), session: Session = Depends(get_session)):
    return session.exec(select(CalendarEvent).where(CalendarEvent.tenant_id == p.tenant_id)
                        .order_by(CalendarEvent.start)).all()


@router.post("/propose")
def propose(body: ScheduleRequest, p: Principal = Depends(require("scheduling:manage")),
            session: Session = Depends(get_session)):
    c = get_owned(session, Candidate, body.candidate_id, p)
    if body.interviewer not in interviewers():
        raise HTTPException(404, "Unknown interviewer")
    slots = propose_slots(busy_slots(body.interviewer, session, p.tenant_id), body.duration_minutes, body.days_ahead)
    if not slots:
        raise HTTPException(409, "No free slots found")
    job = session.get(Job, c.job_id)
    name = c.name or "candidate"
    payload = {"candidate_id": c.id, "interviewer": body.interviewer, "slots": slots, "to": c.email,
               "subject": f"Interview invitation: {job.title}",
               "body": INVITE_BODY.format(name=name.split()[0], job=job.title, interviewer=body.interviewer)}
    rationale = "Free slots from the interviewer's calendar. {slot} is replaced by the slot you pick."
    if not c.email:
        rationale += " No email on file: the interview is booked but no invitation is sent."
    action = approvals.propose(
        session, tenant_id=p.tenant_id, type="schedule_interview",
        title=f"Interview {c.name or c.original_filename} with {body.interviewer}",
        payload=payload, rationale=rationale, risk_level=c.risk_level, created_by=p.subject)
    return {"action_id": action.id, "slots": slots}
