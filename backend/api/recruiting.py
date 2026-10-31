import json
import uuid

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlmodel import Session, select

from backend.agents import candidate_chat
from backend.data import vectors
from backend.data.db import get_session
from backend.data.models import Application, Candidate, JobOffer, Resume
from backend.orchestration import ingestion, screening
from backend.schemas import CandidateEmailRequest, ChatRequest, DecisionRequest, JobCreate
from backend.security import audit
from backend.security.gateway import Principal, get_owned, require
from backend.security.guard import inspect_text

router = APIRouter(tags=["recruiting"])
MAX_FILES = 120


def _uuid(v: str) -> uuid.UUID:
    try:
        return uuid.UUID(v)
    except ValueError:
        raise HTTPException(404, "Not found")


def _job_out(session: Session, j: JobOffer) -> dict:
    n = len(session.exec(select(Application.id).where(Application.job_id == j.id)).all())
    return {"id": str(j.id), "title": j.title, "description": j.description, "criteria": json.loads(j.criteria),
            "applications": n}


@router.post("/jobs")
def create_job(body: JobCreate, p: Principal = Depends(require("jobs:write")), session: Session = Depends(get_session)):
    criteria = {"required_skills": [s.lower() for s in body.required_skills],
                "nice_to_have": [s.lower() for s in body.nice_to_have], "min_years": body.min_years}
    job = JobOffer(tenant_id=p.tenant_id, title=body.title, description=body.description, criteria=json.dumps(criteria),
                   embedding=vectors.dumps(vectors.embed(f"{body.title} {body.description}")))
    session.add(job)
    session.commit()
    audit.log(session, actor=p.subject, event="job.created", tenant_id=p.tenant_id, target=f"job:{job.id}")
    return _job_out(session, job)


@router.get("/jobs")
def list_jobs(p: Principal = Depends(require("jobs:read")), session: Session = Depends(get_session)):
    return [_job_out(session, j) for j in session.exec(select(JobOffer).where(JobOffer.tenant_id == p.tenant_id))]


@router.post("/jobs/{job_id}/resumes")
async def upload_resumes(job_id: str, files: list[UploadFile] = File(...), p: Principal = Depends(require("resumes:upload")),
                         session: Session = Depends(get_session)):
    if len(files) > MAX_FILES:
        raise HTTPException(413, f"At most {MAX_FILES} files per upload")
    job = get_owned(session, JobOffer, _uuid(job_id), p)
    results = [ingestion.ingest_resume(session, p.tenant_id, job, f.filename or "file", await f.read()) for f in files]
    audit.log(session, actor=p.subject, event="resumes.uploaded", tenant_id=p.tenant_id, target=f"job:{job.id}",
              detail={"count": len(files), "accepted": sum(r["accepted"] for r in results)})
    return results


@router.post("/jobs/{job_id}/screen")
def screen_job(job_id: str, rescore: bool = False, p: Principal = Depends(require("screening:run")),
               session: Session = Depends(get_session)):
    job = get_owned(session, JobOffer, _uuid(job_id), p)
    n = screening.screen(session, job, p.subject, rescore)
    return {"screened": n, "ranking": screening.ranking(session, job)}


@router.get("/jobs/{job_id}/ranking")
def ranking(job_id: str, p: Principal = Depends(require("jobs:read")), session: Session = Depends(get_session)):
    return screening.ranking(session, get_owned(session, JobOffer, _uuid(job_id), p))


@router.post("/applications/{app_id}/decision")
def propose_decision(app_id: str, body: DecisionRequest, p: Principal = Depends(require("drafts:create")),
                     session: Session = Depends(get_session)):
    """Creates a PENDING decision; nothing is recorded until approved in /approvals."""
    app = get_owned(session, Application, _uuid(app_id), p)
    a = screening.propose_decision(session, app, body.decision, body.note, p.subject)
    return {"action_id": str(a.id), "status": a.status, "risk_level": a.risk_level}


@router.post("/applications/{app_id}/email")
def propose_candidate_email(app_id: str, body: CandidateEmailRequest, p: Principal = Depends(require("drafts:create")),
                            session: Session = Depends(get_session)):
    app = get_owned(session, Application, _uuid(app_id), p)
    try:
        a = screening.propose_candidate_email(session, app, body.kind, p.subject)
    except ValueError as e:
        raise HTTPException(422, str(e))
    return {"action_id": str(a.id), "status": a.status}


@router.post("/candidates/{cand_id}/chat")
def chat(cand_id: str, body: ChatRequest, p: Principal = Depends(require("jobs:read")), session: Session = Depends(get_session)):
    cand = get_owned(session, Candidate, _uuid(cand_id), p)
    resume = session.exec(select(Resume).where(Resume.candidate_id == cand.id)).first()
    if not resume:
        raise HTTPException(404, "No resume")
    q = inspect_text(session, body.question, source="chat", tenant_id=p.tenant_id, max_chars=500)
    answer, fallback = candidate_chat.ask(q.clean, resume.raw_text, resume.profile)
    return {**answer.model_dump(), "llm_fallback": fallback, "question_flags": [f.to_dict() for f in q.flags]}
