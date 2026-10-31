"""One API for resumes from every outside source (n8n, job boards, forms).

A source sends a file (base64), pasted text, or a structured profile. All three go through the same intake as a
dashboard upload: file guard (types, zip bombs, hidden text), Injection Guard, duplicates, then screening.
Integrations use an API key that only holds resumes:upload (created with POST /auth/api-keys)."""
import base64
import binascii
import json
import uuid
from collections import Counter

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlmodel import Session, select

from backend.config import get_settings
from backend.data.db import get_session
from backend.data.models import IntakeLog, JobOffer
from backend.orchestration import ingestion
from backend.schemas import IntakeItem, IntakeRequest
from backend.security import audit
from backend.security.gateway import Principal, get_owned, limiter, require

router = APIRouter(tags=["intake"])


def _payload(item: IntakeItem) -> tuple[bytes, str]:
    """The raw bytes to ingest, and a default file name."""
    ref = item.source_ref or "item"
    if item.profile is not None:
        return json.dumps(item.profile, ensure_ascii=False).encode(), f"{item.source}-{ref}.json"
    if item.text is not None:
        return item.text.encode(), f"{item.source}-{ref}.txt"
    raw = "".join(item.content_base64.split())
    if len(raw) * 3 // 4 > get_settings().max_upload_bytes:
        raise ValueError(f"file larger than {get_settings().max_upload_mb} MB")
    try:
        return base64.b64decode(raw, validate=True), f"{item.source}-{ref}"
    except (binascii.Error, ValueError):
        raise ValueError("content_base64 is not valid base64")


@router.post("/intake/resumes")
@limiter.limit(get_settings().intake_rate_limit)
def intake_resumes(request: Request, body: IntakeRequest, p: Principal = Depends(require("resumes:upload")),
                   session: Session = Depends(get_session)):
    try:
        job_id = uuid.UUID(body.job_id)
    except ValueError:
        raise HTTPException(404, "JobOffer not found")
    job = get_owned(session, JobOffer, job_id, p)
    results = []
    for item in body.items:
        try:
            data, default_name = _payload(item)
        except ValueError as e:
            results.append({"filename": item.filename or item.source, "accepted": False, "error": str(e),
                            "source": item.source, "source_ref": item.source_ref})
            continue
        r = ingestion.ingest_resume(session, p.tenant_id, job, item.filename or default_name, data,
                                    source=item.source, source_ref=item.source_ref, actor=p.subject)
        results.append({**r, "source": item.source, "source_ref": item.source_ref})
    audit.log(session, actor=p.subject, event="resumes.ingested", tenant_id=p.tenant_id, target=f"job:{job.id}",
              detail={"sources": dict(Counter(i.source for i in body.items)),
                      "accepted": sum(r["accepted"] for r in results),
                      "flagged": sum(r.get("risk_level") in ("medium", "high") for r in results)})
    return results


@router.get("/intake/log")
def intake_log(job_id: str | None = None, source: str | None = None, limit: int = 200,
               p: Principal = Depends(require("jobs:read")), session: Session = Depends(get_session)):
    q = select(IntakeLog).where(IntakeLog.tenant_id == p.tenant_id)
    if job_id:
        try:
            q = q.where(IntakeLog.job_id == uuid.UUID(job_id))
        except ValueError:
            return []
    if source:
        q = q.where(IntakeLog.source == source)
    rows = session.exec(q.order_by(IntakeLog.created_at.desc()).limit(min(max(limit, 1), 1000))).all()
    return [{**r.model_dump(exclude={"tenant_id", "flags"}), "id": str(r.id), "job_id": str(r.job_id),
             "candidate_id": str(r.candidate_id) if r.candidate_id else None, "flags": json.loads(r.flags)}
            for r in rows]
