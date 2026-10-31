import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from sqlmodel import Session, select

from backend.config import get_settings
from backend.core.security import hash_password, limiter
from backend.db import engine, init_db
from backend.features.approvals.router import router as approvals_router
from backend.features.auth.router import router as auth_router
from backend.features.demo.router import router as demo_router
from backend.features.email_digest.router import router as email_router
from backend.features.employee_qa.router import router as qa_router
from backend.features.followups.router import router as followups_router
from backend.features.replies.router import router as replies_router
from backend.features.scheduling.router import router as scheduling_router
from backend.features.screening.router import router as screening_router
from backend.models import Tenant, User

settings = get_settings()
if settings.weak_jwt_secret:
    logging.getLogger(__name__).warning("JWT_SECRET is a default value: fine for a local demo, never for a deployment.")


def seed_demo_users() -> None:
    with Session(engine) as session:
        if session.exec(select(Tenant)).first():
            return
        acme = Tenant(name="acme")
        session.add(acme)
        session.commit()
        for email, role, pw in [
            ("admin@acme.test", "admin", settings.demo_admin_password),
            ("recruiter@acme.test", "recruiter", settings.demo_recruiter_password),
            ("viewer@acme.test", "viewer", settings.demo_viewer_password),
        ]:
            session.add(User(tenant_id=acme.id, email=email, role=role, hashed_password=hash_password(pw)))
        session.commit()


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    if settings.seed_users:
        seed_demo_users()
    yield


app = FastAPI(title=settings.app_name, lifespan=lifespan)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.add_middleware(SlowAPIMiddleware)  # applies DEFAULT_RATE_LIMIT to every route without its own limit
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in settings.cors_origins.split(",") if o.strip()],
    allow_methods=["GET", "POST"],
    allow_headers=["Authorization", "Content-Type", "X-API-Key"],
)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    length = request.headers.get("content-length")
    if length and length.isdigit() and int(length) > settings.max_request_bytes:
        response = JSONResponse({"detail": f"Request larger than {settings.max_request_mb} MB"}, status_code=413)
    else:
        response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Cache-Control"] = "no-store"
    return response


@app.get("/health")
def health():
    return {"status": "ok", "llm_mode": settings.llm_mode}


for r in (auth_router, screening_router, email_router, replies_router, approvals_router,
          followups_router, scheduling_router, qa_router, demo_router):
    app.include_router(r)
