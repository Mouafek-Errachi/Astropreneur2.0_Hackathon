"""HR-ops endpoints (ported from the n8n workflows) + policy Q&A."""
import json

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlmodel import Session, select

from backend.agents import hr_ops as hr_agents
from backend.agents import policy_rag
from backend.data.db import get_session
from backend.data.models import Employee, Followup, Team
from backend.orchestration import followups, hr_ops, knowledge, seed
from backend.schemas import AskRequest, AwardRequest, BroadcastRequest, GroupEmailRequest, InterviewAssistRequest
from backend.security import audit
from backend.security.gateway import Principal, get_owned, require
from backend.security.guard import inspect_text

router = APIRouter(tags=["hr"])


# ---------------------------------------------------------------- policy Q&A (Policy RAG)
@router.post("/qa/ask")
def ask(body: AskRequest, p: Principal = Depends(require("qa:ask")), session: Session = Depends(get_session)):
    g = inspect_text(session, body.question, source="qa", tenant_id=p.tenant_id, max_chars=1000)
    history = [(t.role, t.text) for t in body.history]
    # A follow-up ("and for interns?") is searched together with the questions before it.
    query = " ".join([*(text for role, text in history[-4:] if role == "user"), g.clean])
    hits = policy_rag.search(session, p.tenant_id, query)
    result, fallback = policy_rag.answer(g.clean, hits, history)
    return {**result.model_dump(), "llm_fallback": fallback, "flags": [f.to_dict() for f in g.flags],
            "sources": [{"label": policy_rag.label(c), "title": c.title, "excerpt": c.content[:500]} for _, c in hits
                        if policy_rag.label(c) in result.cited_sources]}


@router.get("/qa/documents")
def documents(p: Principal = Depends(require("qa:ask"))):
    return knowledge.list_docs(p.tenant_id)


@router.post("/qa/documents")
async def add_documents(files: list[UploadFile] = File(...), p: Principal = Depends(require("qa:admin", human_only=True)),
                        session: Session = Depends(get_session)):
    if len(files) > 10:
        raise HTTPException(400, "Upload at most 10 documents at a time")
    return [knowledge.add(session, p.tenant_id, f.filename or "document", await f.read(), p.subject) for f in files]


@router.delete("/qa/documents/{doc_id}")
def remove_document(doc_id: str, p: Principal = Depends(require("qa:admin", human_only=True)),
                    session: Session = Depends(get_session)):
    knowledge.delete(session, p.tenant_id, doc_id, p.subject)
    return {"removed": doc_id}


@router.post("/qa/reindex")
def reindex(p: Principal = Depends(require("qa:admin")), session: Session = Depends(get_session)):
    return {"chunks": seed.index_policies(session, p.tenant_id)}


# ---------------------------------------------------------------- broadcasts, points, interview assist
@router.get("/hr/teams")
def teams(p: Principal = Depends(require("hr:read")), session: Session = Depends(get_session)):
    return [{"id": t.id, "display_name": t.display_name, "member_count": t.member_count}
            for t in session.exec(select(Team).where(Team.tenant_id == p.tenant_id))]


@router.get("/hr/employees")
def employees(p: Principal = Depends(require("hr:read")), session: Session = Depends(get_session)):
    return [{"id": e.id, "name": f"{e.first_name} {e.last_initial}.", "team": e.team_id, "job_title": e.job_title,
             "active": e.active} for e in session.exec(select(Employee).where(Employee.tenant_id == p.tenant_id))]


@router.post("/hr/broadcast")
def broadcast(body: BroadcastRequest, p: Principal = Depends(require("hr:write")), session: Session = Depends(get_session)):
    a = hr_ops.propose_broadcast(session, p.tenant_id, body.target_team, body.message_prompt, p.subject)
    return {"action_id": str(a.id), "status": a.status, "risk_level": a.risk_level, "payload": json.loads(a.payload),
            "note": "Queued for approval by a different HR user (four-eyes)."}


@router.post("/hr/group-email")
def group_email(body: GroupEmailRequest, p: Principal = Depends(require("hr:write")),
                session: Session = Depends(get_session)):
    a = hr_ops.propose_group_email(session, p.tenant_id, body.request, p.subject)
    return {"action_id": str(a.id), "status": a.status, "risk_level": a.risk_level, "payload": json.loads(a.payload),
            "flags": json.loads(a.risk_flags), "note": "Queued for approval by a different HR user (four-eyes)."}


@router.post("/hr/points/award")
def award(body: AwardRequest, p: Principal = Depends(require("hr:write")), session: Session = Depends(get_session)):
    a = hr_ops.propose_award(session, p.tenant_id, body.employee_id, body.action_type, body.note, body.idempotency_key,
                             p.subject)
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
