from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.security import OAuth2PasswordRequestForm
from sqlmodel import Session, select

from backend.config import get_settings
from backend.data.db import get_session
from backend.data.models import ApiKey
from backend.schemas import ApiKeyCreate, Token
from backend.security import audit, events
from backend.security.gateway import (API_KEY_ALLOWED_SCOPES, ROLE_ACCESS, ROLE_SUMMARY, Principal, authenticate_user,
                                      create_access_token, generate_api_key, get_owned, get_principal, limiter, require)

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login", response_model=Token)
@limiter.limit(get_settings().login_rate_limit)
def login(request: Request, form: OAuth2PasswordRequestForm = Depends(), session: Session = Depends(get_session)):
    user = authenticate_user(session, form.username, form.password)
    if not user:
        audit.log(session, actor=form.username[:100], event="auth.login_failed")
        events.record(session, tenant_id=None, source="auth", kind="auth_denied", severity="low",
                      detail={"user": form.username[:100]})
        raise HTTPException(401, "Invalid credentials")
    audit.log(session, actor=user.email, event="auth.login", tenant_id=user.tenant_id)
    return Token(access_token=create_access_token(user), role=user.role, email=user.email)


@router.get("/me")
def me(p: Principal = Depends(get_principal)):
    return {"email": p.subject, "role": p.role, "tenant_id": p.tenant_id, "kind": p.kind,
            "role_summary": ROLE_SUMMARY.get(p.role, ""), "scopes": sorted(p.scopes)}


@router.get("/roles")
def roles():
    """The three HR roles and what each can do, in plain words. Public: it describes the product, not a user."""
    return {"roles": [{"role": r, "summary": s} for r, s in ROLE_SUMMARY.items()],
            "access": [{"what": what, "recruiter": rec, "manager": man, "admin": adm} for what, rec, man, adm in ROLE_ACCESS]}


@router.post("/api-keys")
def create_api_key(body: ApiKeyCreate, p: Principal = Depends(require("admin", human_only=True)),
                   session: Session = Depends(get_session)):
    bad = set(body.scopes) - API_KEY_ALLOWED_SCOPES
    if bad:
        raise HTTPException(400, f"Scopes not allowed for machine keys: {sorted(bad)}")
    full, prefix, key_hash = generate_api_key()
    row = ApiKey(tenant_id=p.tenant_id, name=body.name, prefix=prefix, key_hash=key_hash, scopes=",".join(body.scopes))
    session.add(row)
    session.commit()
    audit.log(session, actor=p.subject, event="apikey.created", tenant_id=p.tenant_id, target=f"apikey:{row.name}",
              detail={"scopes": body.scopes})
    return {"id": str(row.id), "name": row.name, "scopes": body.scopes, "key": full,
            "warning": "Store this key now; it is not shown again."}


@router.get("/api-keys")
def list_api_keys(p: Principal = Depends(require("admin")), session: Session = Depends(get_session)):
    return [{"id": str(k.id), "name": k.name, "prefix": k.prefix, "scopes": k.scopes.split(","), "active": k.active,
             "last_used_at": k.last_used_at} for k in session.exec(select(ApiKey).where(ApiKey.tenant_id == p.tenant_id))]


@router.post("/api-keys/{key_id}/revoke")
def revoke_api_key(key_id: str, p: Principal = Depends(require("admin", human_only=True)),
                   session: Session = Depends(get_session)):
    import uuid

    row = get_owned(session, ApiKey, uuid.UUID(key_id), p)
    row.active = False
    session.add(row)
    session.commit()
    audit.log(session, actor=p.subject, event="apikey.revoked", tenant_id=p.tenant_id, target=f"apikey:{row.name}")
    return {"revoked": True}
