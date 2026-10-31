"""FastAPI app: L5 gateway middleware + the API routers. See docs/architecture/ for the layer diagrams."""
import asyncio
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from sqlmodel import Session

from backend.api import approvals, auth, hr, inbox, recruiting, scheduling, security
from backend.config import get_settings
from backend.data.db import engine, init_db
from backend.orchestration import seed
from backend.security import events
from backend.security.gateway import limiter

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    if settings.seed_demo:
        with Session(engine) as session:
            seed.seed(session)
    events.bind_loop(asyncio.get_running_loop())
    yield


app = FastAPI(title=settings.app_name, lifespan=lifespan,
              description="The agent executes, the human decides: agents only propose; humans approve in /approvals.")
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in settings.cors_origins.split(",") if o.strip()],
    allow_methods=["GET", "POST"],
    allow_headers=["Authorization", "Content-Type", "X-API-Key", "X-Request-ID"],
)


@app.middleware("http")
async def gateway_headers(request: Request, call_next):
    request_id = request.headers.get("X-Request-ID", "")[:64] or f"req_{uuid.uuid4().hex[:12]}"
    response = await call_next(request)
    response.headers["X-Request-ID"] = request_id
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Cache-Control"] = "no-store"
    return response


@app.get("/health")
def health():
    return {"status": "ok", "llm_provider": settings.llm_provider, "llm_fallback": settings.llm_fallback_provider or None}


for r in (auth.router, recruiting.router, inbox.router, approvals.router, scheduling.router, hr.router, security.router):
    app.include_router(r)
