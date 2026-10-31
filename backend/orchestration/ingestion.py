"""Ingestion workflows: emails (from the API, the dashboard or n8n) and resume files.
Everything passes the Injection Guard; high-risk email is quarantined and never reaches an agent."""
import hashlib
import json
from datetime import datetime, timezone

from sqlmodel import Session, select

from backend.data import storage, vectors
from backend.data.models import Application, Candidate, EmailMessage, JobOffer, Resume, utcnow
from backend.orchestration import followups
from backend.security import audit, events
from backend.security.guard import inspect_text
from backend.security.guard.files import FileRejected, extract
from backend.security.guard.sanitizer import safe_display_filename, sanitize_text


def ingest_email(session: Session, tenant_id: int, *, sender: str, subject: str, body: str, external_id: str = "",
                 received_at: datetime | None = None, actor: str = "system") -> EmailMessage:
    if external_id:
        dup = session.exec(select(EmailMessage).where(EmailMessage.tenant_id == tenant_id,
                                                      EmailMessage.external_id == external_id)).first()
        if dup:
            return dup  # idempotent: n8n may deliver the same message twice
    subject_clean, _ = sanitize_text(subject, 300)
    g = inspect_text(None, f"{subject}\n{body}", source="email")
    received = received_at or utcnow()
    if received.tzinfo is None:
        received = received.replace(tzinfo=timezone.utc)
    cand = session.exec(select(Candidate).where(Candidate.tenant_id == tenant_id, Candidate.email == sender.lower())).first()
    e = EmailMessage(tenant_id=tenant_id, sender=sender.lower(), subject=subject_clean,
                     body=g.clean.split("\n", 1)[-1] if "\n" in g.clean else g.clean, external_id=external_id,
                     received_at=received, flags=g.flags_json, risk_level=g.risk_level, quarantined=g.quarantine,
                     candidate_id=cand.id if cand else None)
    session.add(e)
    session.commit()
    session.refresh(e)
    if g.flags:
        events.record(session, tenant_id=tenant_id, source="email", source_id=str(e.id),
                      kind="quarantined" if g.quarantine else "injection", severity=g.risk_level,
                      detail={"sender": e.sender, "flags": [f.to_dict() for f in g.flags][:10]})
    audit.log(session, actor=actor, event="email.ingested", tenant_id=tenant_id, target=f"email:{e.id}",
              detail={"risk": g.risk_level, "quarantined": g.quarantine})
    if not g.quarantine:
        followups.close_for_sender(session, tenant_id, e.sender)  # a candidate answered: no reminder needed any more
    session.refresh(e)
    return e


def ingest_resume(session: Session, tenant_id: int, job: JobOffer, filename: str, data: bytes) -> dict:
    name = safe_display_filename(filename)
    qpath = storage.quarantine(data)
    try:
        doc = extract(data)
    except FileRejected as e:
        storage.discard(qpath)
        events.record(session, tenant_id=tenant_id, source="file", kind="blocked_file", severity="medium",
                      detail={"filename": name, "reason": str(e)})
        return {"filename": name, "accepted": False, "error": str(e)}
    sha = hashlib.sha256(data).hexdigest()
    same_job = (select(Resume.id).join(Application, Application.resume_id == Resume.id)
                .where(Resume.tenant_id == tenant_id, Application.job_id == job.id))
    if session.exec(same_job.where(Resume.sha256 == sha)).first():
        storage.discard(qpath)
        return {"filename": name, "accepted": False, "error": "duplicate file for this job"}
    if session.exec(same_job.where(Resume.raw_text == doc.text)).first():
        storage.discard(qpath)
        return {"filename": name, "accepted": False, "error": "same content as a resume already uploaded for this job"}

    cand = Candidate(tenant_id=tenant_id)
    session.add(cand)
    session.flush()
    resume = Resume(tenant_id=tenant_id, candidate_id=cand.id, original_filename=name,
                    stored_filename=storage.promote(qpath, doc.mime), mime_type=doc.mime, sha256=sha,
                    raw_text=doc.text, hidden_text=doc.hidden_text, embedding=vectors.dumps(vectors.embed(doc.text)))
    session.add(resume)
    session.flush()
    g = inspect_text(session, doc.text, source="resume", source_id=str(resume.id), tenant_id=tenant_id,
                     hidden_text=doc.hidden_text, sanitizer_notes=doc.notes, already_clean=True)
    resume.flags, resume.risk_level, resume.injection_score = g.flags_json, g.risk_level, g.score
    session.add(resume)
    session.add(Application(tenant_id=tenant_id, candidate_id=cand.id, job_id=job.id, resume_id=resume.id,
                            status="needs_review" if g.risk_level in ("medium", "high") else "new"))
    session.commit()
    return {"filename": name, "accepted": True, "candidate_id": str(cand.id), "risk_level": g.risk_level,
            "flags": [f.type for f in g.flags], "hidden_text_found": bool(doc.hidden_text.strip())}


def job_criteria(job: JobOffer) -> dict:
    return json.loads(job.criteria)
