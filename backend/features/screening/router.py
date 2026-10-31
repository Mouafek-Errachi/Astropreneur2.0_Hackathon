import json

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlmodel import Session, select

from backend.config import get_settings
from backend.core import approvals, audit
from backend.core.security import Principal, get_owned, require
from backend.db import get_session
from backend.features.screening.intake import intake_resume, screen_candidates
from backend.features.screening.ranking import bucket, rank
from backend.models import Candidate, Job
from backend.schemas import CandidateOut, DecisionRequest, JobCreate, JobOut, UploadResult

router = APIRouter(tags=["screening"])


def _job_out(job: Job, count: int = 0) -> JobOut:
    return JobOut(id=job.id, title=job.title, description=job.description,
                  required_skills=json.loads(job.required_skills), nice_to_have=json.loads(job.nice_to_have),
                  min_years=job.min_years, candidate_count=count)


def _cand_out(c: Candidate) -> CandidateOut:
    breakdown = json.loads(c.score_json)
    return CandidateOut(id=c.id, job_id=c.job_id, name=c.name, email=c.email, original_filename=c.original_filename,
                        status=c.status, score=c.score, rank=c.rank, bucket=bucket(c.score), risk_level=c.risk_level,
                        needs_review=c.needs_review, llm_fallback=c.llm_fallback,
                        injection_flags=json.loads(c.injection_flags), profile=json.loads(c.profile_json),
                        score_breakdown=breakdown, justification=breakdown.get("justification", ""))


@router.post("/jobs", response_model=JobOut)
def create_job(body: JobCreate, p: Principal = Depends(require("jobs:write")), session: Session = Depends(get_session)):
    job = Job(tenant_id=p.tenant_id, title=body.title, description=body.description,
              required_skills=json.dumps(body.required_skills), nice_to_have=json.dumps(body.nice_to_have),
              min_years=body.min_years)
    session.add(job)
    session.commit()
    session.refresh(job)
    return _job_out(job)


@router.get("/jobs", response_model=list[JobOut])
def list_jobs(p: Principal = Depends(require("jobs:read")), session: Session = Depends(get_session)):
    jobs = session.exec(select(Job).where(Job.tenant_id == p.tenant_id)).all()
    return [_job_out(j, len(session.exec(select(Candidate.id).where(Candidate.job_id == j.id)).all())) for j in jobs]


@router.post("/jobs/{job_id}/resumes", response_model=list[UploadResult])
async def upload_resumes(job_id: int, files: list[UploadFile] = File(...),
                         p: Principal = Depends(require("resumes:upload")), session: Session = Depends(get_session)):
    job = get_owned(session, Job, job_id, p)
    s = get_settings()
    if len(files) > s.max_files_per_batch:
        raise HTTPException(413, f"At most {s.max_files_per_batch} files per upload")
    results = []
    for f in files:
        data = await f.read(s.max_upload_bytes + 1)  # never read more than the limit into memory
        results.append(intake_resume(session, p.tenant_id, job, f.filename, data))
    audit.log(session, actor=p.subject, event="resumes.uploaded", tenant_id=p.tenant_id, target=f"job:{job.id}",
              detail={"count": len(files), "accepted": sum(r.accepted for r in results)})
    return results


@router.post("/jobs/{job_id}/screen", response_model=list[CandidateOut])
def screen_job(job_id: int, p: Principal = Depends(require("screening:run")), session: Session = Depends(get_session)):
    job = get_owned(session, Job, job_id, p)
    ranked = screen_candidates(session, p.tenant_id, job)
    audit.log(session, actor=p.subject, event="screening.run", tenant_id=p.tenant_id, target=f"job:{job.id}")
    return [_cand_out(c) for c in ranked]


@router.get("/jobs/{job_id}/ranking", response_model=list[CandidateOut])
def ranking(job_id: int, p: Principal = Depends(require("jobs:read")), session: Session = Depends(get_session)):
    job = get_owned(session, Job, job_id, p)
    cands = session.exec(select(Candidate).where(Candidate.job_id == job.id, Candidate.tenant_id == p.tenant_id)).all()
    return [_cand_out(c) for c in rank(list(cands))]


@router.get("/candidates/{cid}", response_model=CandidateOut)
def get_candidate(cid: int, p: Principal = Depends(require("jobs:read")), session: Session = Depends(get_session)):
    return _cand_out(get_owned(session, Candidate, cid, p))


@router.post("/candidates/{cid}/decision")
def propose_decision(cid: int, body: DecisionRequest, p: Principal = Depends(require("drafts:create")),
                     session: Session = Depends(get_session)):
    """Creates a pending decision. Nothing is recorded until someone else approves it in /approvals."""
    c = get_owned(session, Candidate, cid, p)
    action = approvals.propose(session, tenant_id=p.tenant_id, type="record_decision",
                               title=f"Mark {c.name or c.original_filename} as {body.decision}",
                               payload={"candidate_id": c.id, "decision": body.decision}, rationale=body.note,
                               risk_level=c.risk_level, created_by=p.subject)
    return {"action_id": action.id, "status": action.status}
