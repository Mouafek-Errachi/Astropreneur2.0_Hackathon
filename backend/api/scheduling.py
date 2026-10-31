import json
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlmodel import Session, select

from backend.data.db import get_session
from backend.data.models import Application, CalendarEvent
from backend.orchestration import scheduling
from backend.schemas import ChooseSlot, ScheduleRequest
from backend.security.gateway import Principal, get_owned, limiter, require

router = APIRouter(tags=["scheduling"])


@router.get("/scheduling/interviewers")
def list_interviewers(p: Principal = Depends(require("scheduling:read"))):
    return list(scheduling.interviewers())


@router.post("/scheduling/propose")
def propose(body: ScheduleRequest, p: Principal = Depends(require("scheduling:manage")), session: Session = Depends(get_session)):
    try:
        app = get_owned(session, Application, uuid.UUID(body.application_id), p)
    except ValueError:
        raise HTTPException(404, "Application not found")
    a = scheduling.propose_interview(session, app, body.interviewer, body.duration_minutes, body.days_ahead, p.subject)
    return {"action_id": str(a.id), "status": a.status, "slots": json.loads(a.payload)["slots"]}


@router.get("/scheduling/events")
def events(p: Principal = Depends(require("scheduling:read")), session: Session = Depends(get_session)):
    rows = session.exec(select(CalendarEvent).where(CalendarEvent.tenant_id == p.tenant_id).order_by(CalendarEvent.start))
    return [{"interviewer": e.interviewer, "title": e.title, "start": e.start, "end": e.end,
             "candidate_id": str(e.candidate_id) if e.candidate_id else None} for e in rows]


# ---------------------------------------------------------------- public magic-link (no login, single use)
@router.get("/public/schedule/{token}", response_class=HTMLResponse, include_in_schema=False)
@limiter.limit("20/minute")
def public_page(request: Request, token: str, session: Session = Depends(get_session)):
    from html import escape

    data = scheduling.public_view(session, token)
    buttons = "".join(
        f'<button onclick="pick({i})">{escape(s["start"].replace("T", " ")[:16])}</button><br>'
        for i, s in enumerate(data["slots"]))
    return f"""<!doctype html><meta charset="utf-8"><title>Choose your interview slot</title>
<body style="font-family:sans-serif;max-width:480px;margin:40px auto">
<h2>Choose your interview slot</h2><p>Interviewer: {escape(data['interviewer'])}</p>{buttons}<p id="r"></p>
<script>async function pick(i){{const r=await fetch(location.pathname,{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{slot_index:i}})}});
document.getElementById('r').textContent=r.ok?'Booked, thank you! You will receive a confirmation.':'This link is no longer valid.';}}</script></body>"""


@router.post("/public/schedule/{token}", include_in_schema=False)
@limiter.limit("10/minute")
def public_choose(request: Request, token: str, body: ChooseSlot, session: Session = Depends(get_session)):
    return scheduling.public_choose(session, token, body.slot_index)
