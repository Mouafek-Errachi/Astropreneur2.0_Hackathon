from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.security import OAuth2PasswordRequestForm
from sqlmodel import Session

from backend.config import get_settings
from backend.core import audit
from backend.core.security import Principal, authenticate_user, create_access_token, get_principal, limiter
from backend.db import get_session
from backend.schemas import Me, Token

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login", response_model=Token)
@limiter.limit(get_settings().login_rate_limit)
def login(request: Request, form: OAuth2PasswordRequestForm = Depends(), session: Session = Depends(get_session)):
    user = authenticate_user(session, form.username, form.password)
    if not user:
        audit.log(session, actor=form.username[:100], event="auth.login_failed")
        raise HTTPException(401, "Invalid credentials")
    audit.log(session, actor=user.email, event="auth.login", tenant_id=user.tenant_id)
    return Token(access_token=create_access_token(user), role=user.role, tenant_id=user.tenant_id, email=user.email)


@router.get("/me", response_model=Me)
def me(p: Principal = Depends(get_principal)):
    return Me(email=p.subject, role=p.role, tenant_id=p.tenant_id, kind=p.kind, scopes=sorted(p.scopes))
