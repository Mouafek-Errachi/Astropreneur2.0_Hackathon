"""Resume intake and screening, shared by the upload endpoint and the demo-data loader."""
import hashlib
import json
from concurrent.futures import ThreadPoolExecutor

from sqlmodel import Session, select

from backend.config import get_settings
from backend.core import injection, storage
from backend.core.ingestion import IngestionError, ingest
from backend.core.sanitizer import safe_display_filename
from backend.features.screening.parser import parse_resume
from backend.features.screening.ranking import rank
from backend.features.screening.scorer import score
from backend.models import Candidate, Job
from backend.schemas import EMAIL_RE, CandidateProfile, UploadResult



def intake_resume(session: Session, tenant_id: int, job: Job, filename: str, data: bytes) -> UploadResult:
    name = safe_display_filename(filename)
    digest = hashlib.sha256(data).hexdigest()
    same = select(Candidate.id).where(Candidate.tenant_id == tenant_id, Candidate.job_id == job.id)
    if (dup := session.exec(same.where(Candidate.sha256 == digest)).first()) is not None:
        return UploadResult(filename=name, accepted=False, error=f"duplicate of candidate #{dup}")
    qpath = storage.quarantine(data)
    try:
        doc = ingest(data)
    except IngestionError as e:
        storage.discard(qpath)
        return UploadResult(filename=name, accepted=False, error=str(e))
    if (dup := session.exec(same.where(Candidate.text == doc.text)).first()) is not None:
        storage.discard(qpath)
        return UploadResult(filename=name, accepted=False, error=f"same content as candidate #{dup}")
    flags = injection.scan(doc.text, sanitizer_notes=doc.notes)
    risk = injection.risk_level(flags)
    cand = Candidate(tenant_id=tenant_id, job_id=job.id, original_filename=name,
                     stored_filename=storage.promote(qpath, doc.mime), mime_type=doc.mime, sha256=digest,
                     text=doc.text, injection_flags=json.dumps([f.to_dict() for f in flags]), risk_level=risk,
                     needs_review=risk in ("medium", "high"))
    session.add(cand)
    session.commit()
    session.refresh(cand)
    return UploadResult(filename=name, accepted=True, candidate_id=cand.id, risk_level=risk,
                        flags=[f.type for f in flags])


def verified_email(email: str, text: str) -> str:
    """Only keep an address that literally appears in the resume: the LLM never invents a recipient."""
    email = (email or "").strip().lower()
    return email if EMAIL_RE.match(email) and email in text.lower() else ""


def criteria(job: Job) -> tuple[list[str], list[str], dict]:
    return json.loads(job.required_skills), json.loads(job.nice_to_have), json.loads(job.weights or "{}")


def _job_candidates(session: Session, tenant_id: int, job: Job) -> list[Candidate]:
    return list(session.exec(select(Candidate).where(Candidate.job_id == job.id, Candidate.tenant_id == tenant_id)).all())


def _save_ranking(session: Session, cands: list[Candidate]) -> list[Candidate]:
    ordered = rank(cands)
    for c in ordered:
        session.add(c)
    session.commit()
    return ordered


def screen_candidates(session: Session, tenant_id: int, job: Job) -> list[Candidate]:
    """Reads new resumes (LLM) and scores them against the job criteria."""
    req, nice, weights = criteria(job)
    cands = _job_candidates(session, tenant_id, job)
    todo = [c for c in cands if c.score is None]
    # LLM calls run in parallel (LLM_CONCURRENCY); the DB is only touched from this thread.
    with ThreadPoolExecutor(max_workers=max(1, get_settings().llm_concurrency)) as pool:
        parsed = list(pool.map(lambda c: parse_resume(c.text, req + nice), todo))
    for c, (profile, fallback) in zip(todo, parsed):
        breakdown = score(profile, req, nice, job.min_years, weights)
        c.name, c.email = profile.name, verified_email(profile.email, c.text)
        c.profile_json, c.score_json, c.score = profile.model_dump_json(), json.dumps(breakdown), breakdown["total"]
        c.llm_fallback, c.status = fallback, "screened" if c.status == "new" else c.status
    return _save_ranking(session, cands)


def rescore(session: Session, tenant_id: int, job: Job) -> list[Candidate]:
    """Re-ranks already-read resumes with the job's current criteria. Pure code, no LLM call: instant."""
    req, nice, weights = criteria(job)
    cands = _job_candidates(session, tenant_id, job)
    for c in cands:
        if c.score is None:
            continue
        breakdown = score(CandidateProfile.model_validate_json(c.profile_json), req, nice, job.min_years, weights)
        c.score_json, c.score = json.dumps(breakdown), breakdown["total"]
    return _save_ranking(session, cands)
