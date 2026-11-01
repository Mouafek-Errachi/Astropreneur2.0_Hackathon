from dataclasses import dataclass

import jwt
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from .config import settings

bearer = HTTPBearer(auto_error=False)


@dataclass(frozen=True)
class Principal:
    user: str                  # JWT `sub` (the HR platform puts the user's email there)
    tenant: str | None = None  # JWT `tid`: interviews are shared within a tenant


def verify_principal(token: str | None) -> Principal:
    """Checks a JWT issued by the HR platform (same JWT_SECRET / JWT_ALGORITHM)."""
    if settings.dev_auth_bypass:
        return Principal("dev-user")
    if not token:
        raise HTTPException(401, "Missing token")
    try:
        claims = jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
    except jwt.PyJWTError:
        raise HTTPException(401, "Invalid or expired token")
    if claims.get("typ", "access") != "access" or not claims.get("sub"):
        raise HTTPException(401, "Invalid or expired token")
    tid = claims.get("tid")
    return Principal(str(claims["sub"]), str(tid) if tid is not None else None)


def verify_token(token: str | None) -> str:
    """Returns the HR user id (JWT `sub`)."""
    return verify_principal(token).user


def current_principal(creds: HTTPAuthorizationCredentials | None = Depends(bearer)) -> Principal:
    return verify_principal(creds.credentials if creds else None)


def current_user(p: Principal = Depends(current_principal)) -> str:
    return p.user
