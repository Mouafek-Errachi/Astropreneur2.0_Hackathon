import json
import uuid

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlmodel import Session, select

from backend.agents import candidate_chat
from backend.config import get_settings
from backend.data import vectors
from backend.data.db import get_session
from backend.data.models import Application, Candidate, JobOffer, Resume
from backend.orchestration import ingestion, screening
from backend.schemas import (CandidateEmailRequest, ChatRequest, CompareRequest, CriteriaText, DecisionRequest, JobCreate,
                             JobCriteria)
from backend.security import audit
from backend.security.gateway import Principal, get_owned, require
from backend.security.guard import inspect_text

router = APIRouter(tags=["recruiting"])


def _uuid(v: str) -> uuid.UUID:
    try:
        return uuid.UUID(v)
    except ValueError:
        raise HTTPException(404, "Not found")


def _job_out(session: Session, j: JobOffer) -> dict:
    n = len(session.exec(select(Application.id).where(Application.job_id == j.id)).all())
    return {"id": str(j.id), "title": j.title, "description": j.description, "criteria": screening.criteria(j),
            "applications": n}


def _criteria(body: JobCriteria) -> dict:
    return {"required_skills": [s.lower() for s in body.required_skills],
            "nice_to_have": [s.lower() for s in body.nice_to_have if s.lower() not in
                             {r.lower() for r in body.required_skills}],
            "min_years": body.min_years, "weights": body.weights.model_dump()}


@router.post("/jobs")
def create_job(body: JobCreate, p: Principal = Depends(require("jobs:write")), session: Session = Depends(get_session)):
    job = JobOffer(tenant_id=p.tenant_id, title=body.title, description=body.description,
                   criteria=json.dumps(_criteria(body)),
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
    s = get_settings()
    if len(files) > s.max_files_per_batch:
        raise HTTPException(413, f"At most {s.max_files_per_batch} files per upload")
    job = get_owned(session, JobOffer, _uuid(job_id), p)
    results = []
    for f in files:
        data = await f.read(s.max_upload_bytes + 1)  # never read more than the limit into memory
        results.append(ingestion.ingest_resume(session, p.tenant_id, job, f.filename or "file", data))
    audit.log(session, actor=p.subject, event="resumes.uploaded", tenant_id=p.tenant_id, target=f"job:{job.id}",
              detail={"count": len(files), "accepted": sum(r["accepted"] for r in results)})
    return results


@router.post("/jobs/{job_id}/screen")
def screen_job(job_id: str, rescore: bool = False, p: Principal = Depends(require("screening:run")),
               session: Session = Depends(get_session)):
    job = get_owned(session, JobOffer, _uuid(job_id), p)
    n = screening.screen(session, job, p.subject, reparse=rescore)
    return {"screened": n, "ranking": screening.ranking(session, job)}


@router.put("/jobs/{job_id}/criteria")
def set_criteria(job_id: str, body: JobCriteria, reparse: bool = False, p: Principal = Depends(require("jobs:write")),
                 session: Session = Depends(get_session)):
    """Changes what the ranking is based on, then re-ranks. By default only the scoring is recomputed from the
    facts already extracted (instant, no LLM). reparse=true reads every resume again with the new skill list."""
    job = get_owned(session, JobOffer, _uuid(job_id), p)
    screening.set_criteria(session, job, _criteria(body), p.subject, reparse)
    return {"job": _job_out(session, job), "ranking": screening.ranking(session, job)}


@router.post("/jobs/{job_id}/criteria/from-text")
def criteria_from_text(job_id: str, body: CriteriaText, p: Principal = Depends(require("jobs:write")),
                       session: Session = Depends(get_session)):
    """"Who are you looking for?" in the recruiter's own words -> criteria. Nothing is saved: the recruiter checks
    them and applies them with PUT /jobs/{job_id}/criteria."""
    job = get_owned(session, JobOffer, _uuid(job_id), p)
    q = inspect_text(session, body.text, source="recruiter_request", tenant_id=p.tenant_id, max_chars=2000)
    crit, fallback = screening.criteria_from_text(session, job, q.clean)
    return {"criteria": crit, "llm_fallback": fallback, "flags": [f.to_dict() for f in q.flags]}


@router.post("/jobs/{job_id}/compare")
def compare(job_id: str, body: CompareRequest, p: Principal = Depends(require("jobs:read")),
            session: Session = Depends(get_session)):
    """Who to choose among the picked candidates, and when another one is the better pick, in plain sentences."""
    job = get_owned(session, JobOffer, _uuid(job_id), p)
    return screening.compare_applications(session, job, [_uuid(i) for i in body.application_ids])


@router.post("/jobs/{job_id}/learn-weights")
async def learn_weights(job_id: str, file: UploadFile = File(...), p: Principal = Depends(require("jobs:write")),
                        session: Session = Depends(get_session)):
    """Learns the criteria weights from a CSV of past hires (skills, years_experience, jira_done, performance and an
    optional group column for the four-fifths bias check) and proposes them for approval by someone else."""
    job = get_owned(session, JobOffer, _uuid(job_id), p)
    return screening.propose_learned_weights(session, job, await file.read(1_000_001), p.subject)


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
    a = screening.propose_candidate_email(session, app, body.kind, p.subject)
    return {"action_id": str(a.id), "status": a.status, "payload": json.loads(a.payload)}


@router.post("/candidates/{cand_id}/chat")
def chat(cand_id: str, body: ChatRequest, p: Principal = Depends(require("jobs:read")), session: Session = Depends(get_session)):
    cand = get_owned(session, Candidate, _uuid(cand_id), p)
    resume = session.exec(select(Resume).where(Resume.candidate_id == cand.id)).first()
    if not resume:
        raise HTTPException(404, "No resume")
    q = inspect_text(session, body.question, source="chat", tenant_id=p.tenant_id, max_chars=500)
    answer, fallback = candidate_chat.ask(q.clean, resume.raw_text, resume.profile)
    return {**answer.model_dump(), "llm_fallback": fallback, "question_flags": [f.to_dict() for f in q.flags]}
