import json
from collections import Counter

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlmodel import Session, select

from backend.config import get_settings
from backend.core import approvals, audit
from backend.core.security import Principal, get_owned, require
from backend.db import get_session
from backend.features.screening.brief import parse_brief
from backend.features.screening.compare import advise
from backend.features.screening.intake import criteria, intake_resume, rescore, screen_candidates
from backend.features.screening.learner import LearnError, learn, read_csv
from backend.features.screening.ranking import bucket, rank
from backend.features.screening.scorer import DEFAULT_WEIGHTS
from backend.models import Candidate, Job
from backend.schemas import (CandidateOut, CompareRequest, CriteriaFromText, CriteriaText, DecisionRequest, JobCreate,
                             JobCriteria, JobOut, UploadResult)

router = APIRouter(tags=["screening"])


def _job_out(job: Job, count: int = 0) -> JobOut:
    req, nice, weights = criteria(job)
    return JobOut(id=job.id, title=job.title, description=job.description, required_skills=req, nice_to_have=nice,
                  min_years=job.min_years, weights={**DEFAULT_WEIGHTS, **weights}, candidate_count=count)


def _cand_out(c: Candidate) -> CandidateOut:
    breakdown = json.loads(c.score_json)
    return CandidateOut(id=c.id, job_id=c.job_id, name=c.name, email=c.email, original_filename=c.original_filename,
                        status=c.status, score=c.score, rank=c.rank, bucket=bucket(c.score), risk_level=c.risk_level,
                        needs_review=c.needs_review, llm_fallback=c.llm_fallback,
                        injection_flags=json.loads(c.injection_flags), profile=json.loads(c.profile_json),
                        score_breakdown=breakdown, justification=breakdown.get("justification", ""),
                        source=c.source or "upload")


@router.post("/jobs", response_model=JobOut)
def create_job(body: JobCreate, p: Principal = Depends(require("jobs:write")), session: Session = Depends(get_session)):
    job = Job(tenant_id=p.tenant_id, title=body.title, description=body.description,
              required_skills=json.dumps(body.required_skills), nice_to_have=json.dumps(body.nice_to_have),
              min_years=body.min_years, weights=body.weights.model_dump_json())
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
        results.append(intake_resume(session, p.tenant_id, job, f.filename, data, actor=p.subject))
    audit.log(session, actor=p.subject, event="resumes.uploaded", tenant_id=p.tenant_id, target=f"job:{job.id}",
              detail={"count": len(files), "accepted": sum(r.accepted for r in results)})
    return results


@router.post("/jobs/{job_id}/screen", response_model=list[CandidateOut])
def screen_job(job_id: int, p: Principal = Depends(require("screening:run")), session: Session = Depends(get_session)):
    job = get_owned(session, Job, job_id, p)
    ranked = screen_candidates(session, p.tenant_id, job)
    audit.log(session, actor=p.subject, event="screening.run", tenant_id=p.tenant_id, target=f"job:{job.id}")
    return [_cand_out(c) for c in ranked]


@router.put("/jobs/{job_id}/criteria", response_model=list[CandidateOut])
def set_criteria(job_id: int, body: JobCriteria, reparse: bool = False, p: Principal = Depends(require("jobs:write")),
                 session: Session = Depends(get_session)):
    """Changes what the ranking is based on, then re-ranks. By default only the scoring is recomputed from the
    facts already extracted (instant, no LLM). reparse=true reads every resume again with the new skill list."""
    job = get_owned(session, Job, job_id, p)
    before = {"required_skills": json.loads(job.required_skills), "nice_to_have": json.loads(job.nice_to_have),
              "min_years": job.min_years, "weights": json.loads(job.weights or "{}")}
    job.required_skills, job.nice_to_have = json.dumps(body.required_skills), json.dumps(body.nice_to_have)
    job.min_years, job.weights = body.min_years, body.weights.model_dump_json()
    session.add(job)
    if reparse:
        for c in session.exec(select(Candidate).where(Candidate.job_id == job.id, Candidate.tenant_id == p.tenant_id)):
            c.score = None
            session.add(c)
    session.commit()
    ranked = screen_candidates(session, p.tenant_id, job) if reparse else rescore(session, p.tenant_id, job)
    audit.log(session, actor=p.subject, event="job.criteria_changed", tenant_id=p.tenant_id, target=f"job:{job.id}",
              detail={"before": before, "after": body.model_dump(), "reparse": reparse})
    return [_cand_out(c) for c in ranked]


@router.post("/jobs/{job_id}/criteria/from-text", response_model=CriteriaFromText)
def criteria_from_text(job_id: int, body: CriteriaText, p: Principal = Depends(require("jobs:write")),
                       session: Session = Depends(get_session)):
    """Reads the recruiter's request in their own words and returns the criteria it asks for. Nothing is saved:
    the recruiter checks them and applies them with PUT /jobs/{job_id}/criteria."""
    job = get_owned(session, Job, job_id, p)
    req, nice, _ = criteria(job)
    cands = session.exec(select(Candidate).where(Candidate.job_id == job.id, Candidate.tenant_id == p.tenant_id)).all()
    found = Counter(s.lower() for c in cands for s in json.loads(c.profile_json).get("skills", []))
    crit, fallback = parse_brief(body.text, req + nice + [s for s, _ in found.most_common(60)])
    return CriteriaFromText(criteria=crit, llm_fallback=fallback)


@router.post("/jobs/{job_id}/compare")
def compare(job_id: int, body: CompareRequest, p: Principal = Depends(require("jobs:read")),
            session: Session = Depends(get_session)):
    """Who to choose among the picked candidates, and when another one is the better pick, in plain sentences."""
    job = get_owned(session, Job, job_id, p)
    cands = [get_owned(session, Candidate, cid, p) for cid in dict.fromkeys(body.candidate_ids)]
    if len(cands) < 2 or any(c.job_id != job.id or c.score is None for c in cands):
        raise HTTPException(400, "Pick at least two screened candidates of this job")
    req, nice, _ = criteria(job)
    return advise(cands, {s.lower() for s in req + nice})


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


@router.post("/jobs/{job_id}/learn-weights")
async def learn_weights(job_id: int, file: UploadFile = File(...), p: Principal = Depends(require("jobs:write")),
                        session: Session = Depends(get_session)):
    """Learns the criteria weights from a CSV of past hires and proposes them for approval. The rows are not
    stored: only the aggregate report goes into the approval queue and the audit log."""
    job = get_owned(session, Job, job_id, p)
    req, nice, weights = criteria(job)
    try:
        report = learn(read_csv(await file.read(1_000_001), req, nice, job.min_years), weights)
    except LearnError as e:
        raise HTTPException(422, str(e))
    fairness = report["fairness"]
    audit.log(session, actor=p.subject, event="screening.weights_learned", tenant_id=p.tenant_id,
              target=f"job:{job.id}", detail={k: report[k] for k in ("weights", "learned_from", "accuracy", "fairness")})
    if fairness["checked"] and not fairness["passed"]:
        raise HTTPException(422, {"message": f"Blocked by the bias check: impact ratio {fairness['impact_ratio']} is "
                                             "below 0.8 (four-fifths rule). The weights were not proposed.",
                                  "report": report})
    w = report["weights"]
    action = approvals.propose(
        session, tenant_id=p.tenant_id, type="apply_weights",
        title=f"Use learned weights for {job.title}: required {w['required']}, nice {w['nice']}, "
              f"experience {w['experience']}",
        payload={"job_id": job.id, "weights": w, "report": report},
        rationale=f"Learned from {report['learned_from']} past hires ({report['good_outcomes']} good outcomes); "
                  + ("bias check passed" if fairness["checked"] else "no bias check: no group column"),
        risk_level="low" if fairness["checked"] else "high", created_by=p.subject)
    return {"action_id": action.id, "status": action.status, "report": report}
