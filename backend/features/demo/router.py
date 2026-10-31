"""Loads fictional demo data through the normal pipelines (sanitising, injection scan, dedupe), so the demo
shows the real behaviour at volume. Admin only, and can be turned off with ENABLE_DEMO_DATA=false."""
import json
from datetime import timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, select

from backend.config import get_settings
from backend.core import audit
from backend.core.security import Principal, require
from backend.db import get_session
from backend.features.demo import generator
from backend.features.email_digest.router import store_email
from backend.features.employee_qa.indexer import reindex
from backend.features.screening.intake import intake_resume
from backend.models import Candidate, Email, Job, utcnow
from backend.schemas import DemoDataRequest, EmailIn

router = APIRouter(prefix="/admin", tags=["demo"])


def _job(session: Session, tenant_id: int) -> Job:
    spec = json.loads((get_settings().data_dir / "jobs" / "backend_engineer.json").read_text(encoding="utf-8"))
    job = session.exec(select(Job).where(Job.tenant_id == tenant_id, Job.title == spec["title"])).first()
    if job:
        return job
    job = Job(tenant_id=tenant_id, title=spec["title"], description=spec.get("description", ""),
              required_skills=json.dumps(spec.get("required_skills", [])),
              nice_to_have=json.dumps(spec.get("nice_to_have", [])), min_years=spec.get("min_years", 0))
    session.add(job)
    session.commit()
    session.refresh(job)
    return job


@router.post("/demo-data")
def load_demo_data(body: Optional[DemoDataRequest] = None, p: Principal = Depends(require("admin", human_only=True)),
                   session: Session = Depends(get_session)):
    s = get_settings()
    if not s.enable_demo_data:
        raise HTTPException(404, "Not found")
    body = body or DemoDataRequest()
    job = _job(session, p.tenant_id)

    # Resumes: the hand-written samples (incl. the adversarial one) plus generated ones.
    files = [(f.name, f.read_bytes()) for f in sorted((s.data_dir / "resumes").glob("*.txt"))]
    fake_resumes = generator.resumes(body.resumes, body.seed)
    files += [(r.filename, r.text.encode()) for r in fake_resumes]
    results = [intake_resume(session, p.tenant_id, job, name, data, source="demo", actor=p.subject)
               for name, data in files]

    # Emails: the samples plus generated ones spread over the last 4 days. Some come from candidates.
    candidate_emails = [r.email for r in fake_resumes if not r.adversarial]
    senders = candidate_emails[: max(1, len(candidate_emails) // 2)]
    incoming = [EmailIn(external_id=f"sample-{i}", **e) for i, e in enumerate(
        json.loads((s.data_dir / "emails" / "sample_emails.json").read_text(encoding="utf-8")))]
    incoming += [EmailIn(external_id=e.external_id, sender=e.sender, subject=e.subject, body=e.body,
                         received_at=e.received_at)
                 for e in generator.emails(body.emails, senders, job.title, body.seed)]
    known = set(session.exec(select(Email.external_id).where(Email.tenant_id == p.tenant_id)).all())
    added = [store_email(session, p.tenant_id, e) for e in incoming if e.external_id not in known]

    # Simulated history: we wrote to a few candidates days ago and they never answered, so the
    # follow-up scan has reminders to propose once screening has extracted their email.
    silent = set(candidate_emails[len(senders):][:3])
    contacted = 0
    for c in session.exec(select(Candidate).where(Candidate.tenant_id == p.tenant_id, Candidate.job_id == job.id,
                                                  Candidate.last_contact_at == None)):  # noqa: E711
        if any(e in c.text for e in silent):
            c.last_contact_at = utcnow() - timedelta(days=s.reminder_after_days + 1)
            session.add(c)
            contacted += 1
    session.commit()

    chunks = reindex(session, p.tenant_id)
    summary = {
        "job_id": job.id,
        "resumes": {"accepted": sum(r.accepted for r in results), "rejected": sum(not r.accepted for r in results),
                    "flagged": sum(r.risk_level in ("medium", "high") for r in results)},
        "emails": {"added": len(added), "skipped": len(incoming) - len(added)},
        "policy_chunks": chunks,
        "contacted_candidates": contacted,
    }
    audit.log(session, actor=p.subject, event="demo.loaded", tenant_id=p.tenant_id, detail=summary)
    return summary
