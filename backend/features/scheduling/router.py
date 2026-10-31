from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session

from backend.core import approvals
from backend.core.security import Principal, get_owned, require
from backend.db import get_session
from backend.features.scheduling.calendar import interviewers
from backend.features.scheduling.proposer import propose_slots
from backend.models import Candidate
from backend.schemas import ScheduleRequest

router = APIRouter(prefix="/scheduling", tags=["scheduling"])


@router.get("/interviewers")
def list_interviewers(p: Principal = Depends(require("scheduling:read"))):
    return interviewers()


@router.post("/propose")
def propose(body: ScheduleRequest, p: Principal = Depends(require("scheduling:manage")),
            session: Session = Depends(get_session)):
    c = get_owned(session, Candidate, body.candidate_id, p)
    slots = propose_slots(body.interviewer, body.duration_minutes, body.days_ahead)
    if not slots:
        raise HTTPException(409, "No free slots found")
    action = approvals.propose(
        session, tenant_id=p.tenant_id, type="schedule_interview",
        title=f"Interview {c.name or c.original_filename} with {body.interviewer}",
        payload={"candidate_id": c.id, "interviewer": body.interviewer, "slots": slots, "to": c.email},
        rationale="Free slots from interviewer calendar", risk_level=c.risk_level)
    return {"action_id": action.id, "slots": slots}
