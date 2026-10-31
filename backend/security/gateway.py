"""L5 API gateway: JWT for humans, hashed API keys for integrations (n8n), RBAC scopes,
tenant scoping and rate limits. Every router depends on `require(...)`."""
import hashlib
import hmac
import secrets
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Optional, TypeVar

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from passlib.context import CryptContext
from slowapi import Limiter
from slowapi.util import get_remote_address
from sqlmodel import Session, SQLModel, select

from backend.config import get_settings
from backend.data.db import get_session
from backend.data.models import ApiKey, User, utcnow

settings = get_settings()
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
limiter = Limiter(key_func=get_remote_address, enabled=settings.rate_limit_enabled,
                  default_limits=[settings.default_rate_limit])

# ------------------------------------------------------------------ scopes
EMPLOYEE_SCOPES = {"qa:ask", "tickets:create"}
VIEWER_SCOPES = {"jobs:read", "emails:read", "approvals:read", "followups:read", "scheduling:read",
                 "hr:read", "qa:ask", "security:read"}
RECRUITER_SCOPES = VIEWER_SCOPES | {
    "jobs:write", "resumes:upload", "screening:run", "emails:ingest", "emails:process", "drafts:create",
    "approvals:decide", "followups:manage", "scheduling:manage", "hr:write", "interview:assist",
}
ADMIN_SCOPES = RECRUITER_SCOPES | {"admin", "audit:read", "qa:admin"}
ROLE_SCOPES = {"employee": EMPLOYEE_SCOPES, "viewer": VIEWER_SCOPES, "recruiter": RECRUITER_SCOPES,
               "admin": ADMIN_SCOPES}

# Scopes a machine credential (e.g. the n8n API key) can NEVER hold: a human must decide.
HUMAN_ONLY_SCOPES = {"approvals:decide", "admin", "audit:read", "qa:admin", "tickets:create"}
API_KEY_ALLOWED_SCOPES = ADMIN_SCOPES - HUMAN_ONLY_SCOPES


@dataclass
class Principal:
    kind: str  # user | api_key
    subject: str
    tenant_id: int
    role: str
    scopes: set[str] = field(default_factory=set)
    user_id: Optional[uuid.UUID] = None
    employee_id: Optional[str] = None

    @property
    def is_human(self) -> bool:
        return self.kind == "user"


# ------------------------------------------------------------------ passwords & tokens
def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(password: str, hashed: str) -> bool:
    try:
        return pwd_context.verify(password, hashed)
    except ValueError:
        return False


_DUMMY_HASH = pwd_context.hash("timing-equaliser")


def authenticate_user(session: Session, email: str, password: str) -> Optional[User]:
    user = session.exec(select(User).where(User.email == email.lower().strip())).first()
    if not user:
        verify_password(password, _DUMMY_HASH)  # same timing whether or not the user exists
        return None
    if not user.active or not verify_password(password, user.hashed_password):
        return None
    return user


def create_access_token(user: User) -> str:
    now = datetime.now(timezone.utc)
    payload = {"sub": user.email, "uid": str(user.id), "tid": user.tenant_id, "role": user.role,
               "iat": int(now.timestamp()), "typ": "access",
               "exp": int((now + timedelta(minutes=settings.jwt_expire_minutes)).timestamp())}
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def generate_api_key() -> tuple[str, str, str]:
    """Returns (full_key, prefix, hash). Only the hash is stored; the key is shown once."""
    prefix = secrets.token_hex(4)
    full = f"hrk_{prefix}_{secrets.token_urlsafe(32)}"
    return full, prefix, hash_api_key(full)


def hash_api_key(key: str) -> str:
    # 256-bit random keys: a keyed fast hash is appropriate (no brute-force risk).
    return hmac.new(settings.jwt_secret.encode(), key.encode(), hashlib.sha256).hexdigest()


# ------------------------------------------------------------------ dependencies
_bearer = HTTPBearer(auto_error=False)
_UNAUTH = HTTPException(status.HTTP_401_UNAUTHORIZED, "Not authenticated", headers={"WWW-Authenticate": "Bearer"})


def get_principal(request: Request, creds: Optional[HTTPAuthorizationCredentials] = Depends(_bearer),
                  session: Session = Depends(get_session)) -> Principal:
    if creds and creds.scheme.lower() == "bearer":
        try:
            data = jwt.decode(creds.credentials, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
            uid = uuid.UUID(data.get("uid", ""))
        except (JWTError, ValueError):
            raise _UNAUTH
        user = session.get(User, uid)
        # Re-check the DB so disabled users / role changes apply immediately.
        if data.get("typ") != "access" or not user or not user.active or user.tenant_id != data.get("tid"):
            raise _UNAUTH
        return Principal("user", user.email, user.tenant_id, user.role, set(ROLE_SCOPES.get(user.role, set())),
                         user.id, user.employee_id)
    if api_key := request.headers.get("X-API-Key"):
        parts = api_key.split("_")
        if len(parts) < 3 or parts[0] != "hrk":
            raise _UNAUTH
        row = session.exec(select(ApiKey).where(ApiKey.prefix == parts[1])).first()
        if not row or not row.active or not hmac.compare_digest(row.key_hash, hash_api_key(api_key)):
            raise _UNAUTH
        row.last_used_at = utcnow()
        session.add(row)
        session.commit()
        scopes = {s for s in row.scopes.split(",") if s} & API_KEY_ALLOWED_SCOPES
        return Principal("api_key", f"apikey:{row.name}", row.tenant_id, "integration", scopes)
    raise _UNAUTH


def require(*scopes: str, human_only: bool = False):
    """Dependency factory: the principal must hold every listed scope."""

    def _dep(principal: Principal = Depends(get_principal)) -> Principal:
        if human_only and not principal.is_human:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "This action requires a human user")
        missing = [s for s in scopes if s not in principal.scopes]
        if missing:
            raise HTTPException(status.HTTP_403_FORBIDDEN, f"Missing scope: {', '.join(missing)}")
        return principal

    return _dep


T = TypeVar("T", bound=SQLModel)


def get_owned(session: Session, model: type[T], obj_id, principal: Principal) -> T:
    """Fetch by id; 404 (not 403) for other tenants' rows so existence never leaks."""
    obj = session.get(model, obj_id)
    if obj is None or getattr(obj, "tenant_id", None) != principal.tenant_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"{model.__name__} not found")
    return obj
