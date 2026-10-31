"""One API for resumes from every outside source (n8n, job boards, forms), and the keys those integrations use.

A source sends a file (base64), pasted text, or a structured profile. All three go through the same intake as a
dashboard upload: file checks, extraction, hidden-text detection, injection scan, duplicates, then screening."""
import base64
import binascii
import json
from collections import Counter

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlmodel import Session, select

from backend.config import get_settings
from backend.core import audit
from backend.core.security import (API_KEY_ALLOWED_SCOPES, Principal, generate_api_key, get_owned, limiter,
                                   require)
from backend.db import get_session
from backend.features.screening.intake import intake_resume
from backend.models import ApiKey, IntakeLog, Job
from backend.schemas import ApiKeyCreate, ApiKeyOut, IntakeItem, IntakeLogOut, IntakeRequest, IntakeResult

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


@router.post("/intake/resumes", response_model=list[IntakeResult])
@limiter.limit(get_settings().intake_rate_limit)
def intake_resumes(request: Request, body: IntakeRequest, p: Principal = Depends(require("resumes:upload")),
                   session: Session = Depends(get_session)):
    job = get_owned(session, Job, body.job_id, p)
    results = []
    for item in body.items:
        try:
            data, default_name = _payload(item)
        except ValueError as e:
            results.append(IntakeResult(filename=item.filename or item.source, accepted=False, error=str(e),
                                        source=item.source, source_ref=item.source_ref))
            continue
        r = intake_resume(session, p.tenant_id, job, item.filename or default_name, data, source=item.source,
                          source_ref=item.source_ref, actor=p.subject)
        results.append(IntakeResult(**r.model_dump(), source=item.source, source_ref=item.source_ref))
    audit.log(session, actor=p.subject, event="resumes.ingested", tenant_id=p.tenant_id, target=f"job:{job.id}",
              detail={"sources": dict(Counter(i.source for i in body.items)),
                      "accepted": sum(r.accepted for r in results),
                      "flagged": sum(r.risk_level in ("medium", "high") for r in results)})
    return results


@router.get("/intake/log", response_model=list[IntakeLogOut])
def intake_log(job_id: int | None = None, source: str | None = None, limit: int = 200,
               p: Principal = Depends(require("jobs:read")), session: Session = Depends(get_session)):
    q = select(IntakeLog).where(IntakeLog.tenant_id == p.tenant_id)
    if job_id is not None:
        q = q.where(IntakeLog.job_id == job_id)
    if source:
        q = q.where(IntakeLog.source == source)
    rows = session.exec(q.order_by(IntakeLog.id.desc()).limit(min(max(limit, 1), 1000))).all()
    return [IntakeLogOut(**r.model_dump(exclude={"flags", "tenant_id"}), flags=json.loads(r.flags)) for r in rows]


# ------------------------------------------------------------------ API keys (admin, humans only)
def _key_out(k: ApiKey, full: str | None = None) -> ApiKeyOut:
    return ApiKeyOut(id=k.id, name=k.name, prefix=k.prefix, scopes=[s for s in k.scopes.split(",") if s],
                     active=k.active, created_at=k.created_at, last_used_at=k.last_used_at, key=full)


@router.post("/admin/api-keys", response_model=ApiKeyOut)
def create_api_key(body: ApiKeyCreate, p: Principal = Depends(require("admin")),
                   session: Session = Depends(get_session)):
    """The key is shown once. A key can never approve, administer or read the audit log."""
    if bad := set(body.scopes) - API_KEY_ALLOWED_SCOPES:
        raise HTTPException(400, f"Scopes not allowed for an API key: {', '.join(sorted(bad))}")
    full, prefix, digest = generate_api_key()
    key = ApiKey(tenant_id=p.tenant_id, name=body.name, prefix=prefix, key_hash=digest,
                 scopes=",".join(sorted(set(body.scopes))))
    session.add(key)
    session.commit()
    session.refresh(key)
    audit.log(session, actor=p.subject, event="api_key.created", tenant_id=p.tenant_id, target=f"api_key:{key.id}",
              detail={"name": key.name, "scopes": key.scopes})
    return _key_out(key, full)


@router.get("/admin/api-keys", response_model=list[ApiKeyOut])
def list_api_keys(p: Principal = Depends(require("admin")), session: Session = Depends(get_session)):
    return [_key_out(k) for k in session.exec(select(ApiKey).where(ApiKey.tenant_id == p.tenant_id)).all()]


@router.post("/admin/api-keys/{key_id}/revoke", response_model=ApiKeyOut)
def revoke_api_key(key_id: int, p: Principal = Depends(require("admin")), session: Session = Depends(get_session)):
    key = get_owned(session, ApiKey, key_id, p)
    key.active = False
    session.add(key)
    session.commit()
    session.refresh(key)
    audit.log(session, actor=p.subject, event="api_key.revoked", tenant_id=p.tenant_id, target=f"api_key:{key.id}")
    return _key_out(key)
