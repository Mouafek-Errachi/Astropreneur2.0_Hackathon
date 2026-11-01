"""HR-ops endpoints (ported from the n8n workflows) + policy Q&A. Only HR staff log in: tickets are filed by HR on
an employee's behalf, and the drafted reply to the employee still waits for approval."""
import json

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, select

from backend.agents import hr_ops as hr_agents
from backend.agents import policy_rag
from backend.data.db import get_session
from backend.data.models import Employee, Followup, Team, Ticket
from backend.orchestration import followups, hr_ops, seed
from backend.schemas import AskRequest, AwardRequest, BroadcastRequest, InterviewAssistRequest, TicketCreate
from backend.security import audit
from backend.security.gateway import Principal, get_owned, require
from backend.security.guard import inspect_text

router = APIRouter(tags=["hr"])


# ---------------------------------------------------------------- policy Q&A (Policy RAG)
@router.post("/qa/ask")
def ask(body: AskRequest, p: Principal = Depends(require("qa:ask")), session: Session = Depends(get_session)):
    g = inspect_text(session, body.question, source="qa", tenant_id=p.tenant_id, max_chars=1000)
    emp_ctx = ""
    if p.employee_id and (emp := session.get(Employee, p.employee_id)):
        emp_ctx = f"contract {emp.contract_type}, leave balance {emp.leave_balance_days} days"  # only their own data
    hits = policy_rag.search(session, p.tenant_id, g.clean)
    result, fallback = policy_rag.answer(g.clean, hits, emp_ctx)
    return {**result.model_dump(), "llm_fallback": fallback, "flags": [f.to_dict() for f in g.flags],
            "sources": [{"label": policy_rag.label(c), "title": c.title} for _, c in hits
                        if policy_rag.label(c) in result.cited_sources]}


@router.post("/qa/reindex")
def reindex(p: Principal = Depends(require("qa:admin")), session: Session = Depends(get_session)):
    return {"chunks": seed.index_policies(session, p.tenant_id)}


# ---------------------------------------------------------------- tickets (HR files them for an employee)
@router.post("/hr/tickets")
def create_ticket(body: TicketCreate, p: Principal = Depends(require("hr:write")), session: Session = Depends(get_session)):
    t = hr_ops.create_ticket(session, p.tenant_id, body.employee_id, body.request_text, p.subject)
    return {"ticket_id": str(t.id), "category": t.category, "priority": t.priority, "status": t.status,
            "message": "Ticket created. The drafted reply to the employee is waiting in Approvals."}


@router.get("/hr/tickets")
def list_tickets(p: Principal = Depends(require("hr:read")), session: Session = Depends(get_session)):
    rows = session.exec(select(Ticket).where(Ticket.tenant_id == p.tenant_id).order_by(Ticket.created_at.desc()))
    return [{"id": str(t.id), "employee_id": t.employee_id, "category": t.category, "priority": t.priority,
             "status": t.status, "risk_level": t.risk_level, "request_text": t.request_text} for t in rows]


# ---------------------------------------------------------------- broadcasts, points, interview assist
@router.get("/hr/teams")
def teams(p: Principal = Depends(require("hr:read")), session: Session = Depends(get_session)):
    return [{"id": t.id, "display_name": t.display_name, "member_count": t.member_count}
            for t in session.exec(select(Team).where(Team.tenant_id == p.tenant_id))]


@router.get("/hr/employees")
def employees(p: Principal = Depends(require("hr:read")), session: Session = Depends(get_session)):
    return [{"id": e.id, "name": f"{e.first_name} {e.last_initial}.", "team": e.team_id, "active": e.active}
            for e in session.exec(select(Employee).where(Employee.tenant_id == p.tenant_id))]


@router.post("/hr/broadcast")
def broadcast(body: BroadcastRequest, p: Principal = Depends(require("hr:write")), session: Session = Depends(get_session)):
    a = hr_ops.propose_broadcast(session, p.tenant_id, body.target_team, body.message_prompt, p.subject)
    return {"action_id": str(a.id), "status": a.status, "risk_level": a.risk_level, "payload": json.loads(a.payload),
            "note": "Queued for approval by a different HR user (four-eyes)."}


@router.post("/hr/points/award")
def award(body: AwardRequest, p: Principal = Depends(require("hr:write")), session: Session = Depends(get_session)):
    a = hr_ops.propose_award(session, p.tenant_id, body.employee_id, body.action_type, body.note, body.idempotency_key,
                             p.subject, p.employee_id)
    return {"action_id": str(a.id), "status": a.status}


@router.get("/hr/leaderboard")
def leaderboard(period: str | None = None, p: Principal = Depends(require("hr:read")), session: Session = Depends(get_session)):
    return hr_ops.leaderboard(session, p.tenant_id, period)


@router.post("/interview/assist")
def interview_assist(body: InterviewAssistRequest, p: Principal = Depends(require("interview:assist")),
                     session: Session = Depends(get_session)):
    g = inspect_text(session, f"{body.previous_chunk}\n{body.current_chunk}", source="transcript", tenant_id=p.tenant_id,
                     max_chars=8000)
    out, fallback = hr_agents.interview_assist(body.job_role, body.competencies, body.resume_summary, g.clean)
    return {**out.model_dump(), "llm_fallback": fallback,
            "security": {"injection_suspected": bool(g.flags) or out.injection_attempt_observed,
                         "flags": [f.to_dict() for f in g.flags]}}


# ---------------------------------------------------------------- follow-ups (n8n cron calls /followups/scan)
@router.post("/followups/scan")
def scan(p: Principal = Depends(require("followups:manage")), session: Session = Depends(get_session)):
    return followups.scan(session, p.tenant_id, p.subject)


@router.get("/followups")
def list_followups(p: Principal = Depends(require("followups:read")), session: Session = Depends(get_session)):
    rows = session.exec(select(Followup).where(Followup.tenant_id == p.tenant_id).order_by(Followup.due_at))
    return [{"id": str(f.id), "kind": f.kind, "ref_type": f.ref_type, "ref_id": f.ref_id, "title": f.title,
             "status": f.status, "due_at": f.due_at,
             "action_id": str(f.action_id) if f.action_id else None} for f in rows]


@router.post("/followups/{fid}/done")
def followup_done(fid: str, p: Principal = Depends(require("followups:manage")), session: Session = Depends(get_session)):
    import uuid

    f = get_owned(session, Followup, uuid.UUID(fid), p)
    f.status = "done"
    session.add(f)
    session.commit()
    audit.log(session, actor=p.subject, event="followup.done", tenant_id=p.tenant_id, target=f"followup:{f.id}")
    return {"done": True}
