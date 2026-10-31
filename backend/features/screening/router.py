import hashlib
import json

from fastapi import APIRouter, Depends, File, UploadFile
from sqlmodel import Session, select

from backend.core import approvals, audit, injection, storage
from backend.core.ingestion import IngestionError, ingest
from backend.core.sanitizer import safe_display_filename
from backend.core.security import Principal, get_owned, require
from backend.db import get_session
from backend.features.screening.parser import parse_resume
from backend.features.screening.ranking import bucket, rank
from backend.features.screening.scorer import score
from backend.models import Candidate, Job
from backend.schemas import CandidateOut, DecisionRequest, JobCreate, JobOut, UploadResult

router = APIRouter(tags=["screening"])


def _job_out(job: Job, count: int = 0) -> JobOut:
    return JobOut(id=job.id, title=job.title, description=job.description,
                  required_skills=json.loads(job.required_skills), nice_to_have=json.loads(job.nice_to_have),
                  min_years=job.min_years, candidate_count=count)


def _cand_out(c: Candidate) -> CandidateOut:
    return CandidateOut(id=c.id, job_id=c.job_id, name=c.name, email=c.email, original_filename=c.original_filename,
                        status=c.status, score=c.score, rank=c.rank, bucket=bucket(c.score), risk_level=c.risk_level,
                        needs_review=c.needs_review, llm_fallback=c.llm_fallback,
                        injection_flags=json.loads(c.injection_flags), profile=json.loads(c.profile_json),
                        score_breakdown=json.loads(c.score_json))


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
    results = []
    for f in files:
        name = safe_display_filename(f.filename)
        data = await f.read()
        qpath = storage.quarantine(data)
        try:
            doc = ingest(data)
        except IngestionError as e:
            storage.discard(qpath)
            results.append(UploadResult(filename=name, accepted=False, error=str(e)))
            continue
        flags = injection.scan(doc.text, sanitizer_notes=doc.notes)
        risk = injection.risk_level(flags)
        cand = Candidate(tenant_id=p.tenant_id, job_id=job.id, original_filename=name,
                         stored_filename=storage.promote(qpath, doc.mime), mime_type=doc.mime,
                         sha256=hashlib.sha256(data).hexdigest(), text=doc.text,
                         injection_flags=json.dumps([f.to_dict() for f in flags]), risk_level=risk,
                         needs_review=risk in ("medium", "high"))
        session.add(cand)
        session.commit()
        session.refresh(cand)
        results.append(UploadResult(filename=name, accepted=True, candidate_id=cand.id, risk_level=risk,
                                    flags=[f.type for f in flags]))
    audit.log(session, actor=p.subject, event="resumes.uploaded", tenant_id=p.tenant_id, target=f"job:{job.id}",
              detail={"count": len(files), "accepted": sum(r.accepted for r in results)})
    return results


@router.post("/jobs/{job_id}/screen", response_model=list[CandidateOut])
def screen_job(job_id: int, p: Principal = Depends(require("screening:run")), session: Session = Depends(get_session)):
    job = get_owned(session, Job, job_id, p)
    req, nice = json.loads(job.required_skills), json.loads(job.nice_to_have)
    cands = session.exec(select(Candidate).where(Candidate.job_id == job.id, Candidate.tenant_id == p.tenant_id)).all()
    for c in cands:
        if c.score is not None:
            continue
        profile, fallback = parse_resume(c.text)
        breakdown = score(profile, req, nice, job.min_years)
        c.name, c.email = profile.name, profile.email
        c.profile_json, c.score_json, c.score = profile.model_dump_json(), json.dumps(breakdown), breakdown["total"]
        c.llm_fallback, c.status = fallback, "screened" if c.status == "new" else c.status
    for c in rank(list(cands)):
        session.add(c)
    session.commit()
    audit.log(session, actor=p.subject, event="screening.run", tenant_id=p.tenant_id, target=f"job:{job.id}")
    return [_cand_out(c) for c in rank(list(cands))]


@router.get("/jobs/{job_id}/ranking", response_model=list[CandidateOut])
def ranking(job_id: int, p: Principal = Depends(require("jobs:read")), session: Session = Depends(get_session)):
    job = get_owned(session, Job, job_id, p)
    cands = session.exec(select(Candidate).where(Candidate.job_id == job.id)).all()
    return [_cand_out(c) for c in rank(list(cands))]


@router.get("/candidates/{cid}", response_model=CandidateOut)
def get_candidate(cid: int, p: Principal = Depends(require("jobs:read")), session: Session = Depends(get_session)):
    return _cand_out(get_owned(session, Candidate, cid, p))


@router.post("/candidates/{cid}/decision")
def propose_decision(cid: int, body: DecisionRequest, p: Principal = Depends(require("drafts:create")),
                     session: Session = Depends(get_session)):
    """Creates a pending decision. Nothing is recorded until approved in /approvals."""
    c = get_owned(session, Candidate, cid, p)
    action = approvals.propose(session, tenant_id=p.tenant_id, type="record_decision",
                               title=f"Mark {c.name or c.original_filename} as {body.decision}",
                               payload={"candidate_id": c.id, "decision": body.decision}, rationale=body.note,
                               risk_level=c.risk_level, created_by=p.subject)
    return {"action_id": action.id, "status": action.status}
