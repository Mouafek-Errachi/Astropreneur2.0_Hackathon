"""Workforce analytics routes (docs/specs/workforce-telemetry-analytics.md). Read-only: nothing here proposes or
changes anything. `analytics:read` gives aggregates; per-person data also needs `skills:read`."""
import time
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import Field
from sqlmodel import Session

from backend.agents import insights
from backend.data.db import get_session
from backend.orchestration import analytics
from backend.schemas import Strict
from backend.security import audit
from backend.security.gateway import Principal, require

router = APIRouter(prefix="/analytics", tags=["workforce analytics"])
_individual_logged: dict[tuple, float] = {}  # (tenant, user) -> last audit time: one event per hour, not per click


def _individual(p: Principal) -> bool:
    return "skills:read" in p.scopes


def _log_individual(session: Session, p: Principal, view: str) -> None:
    key = (p.tenant_id, p.subject, view)
    if time.monotonic() - _individual_logged.get(key, -3600) >= 3600:
        _individual_logged[key] = time.monotonic()
        audit.log(session, actor=p.subject, event="analytics.individual_viewed", tenant_id=p.tenant_id,
                  detail={"view": view})


@router.get("/teams")
def teams(p: Principal = Depends(require("analytics:read")), session: Session = Depends(get_session)):
    """Capacity, allocation, free time, open gaps and under-resourced flags per team."""
    return analytics.teams_view(session, p.tenant_id, _individual(p))


@router.get("/skills")
def skills(team: str | None = None, p: Principal = Depends(require("analytics:read")),
           session: Session = Depends(get_session)):
    return analytics.skills_view(session, p.tenant_id, _individual(p), team)


@router.get("/skills/single-points")
def single_points(p: Principal = Depends(require("analytics:read")), session: Session = Depends(get_session)):
    if _individual(p):
        _log_individual(session, p, "single_points")
    return analytics.single_points_view(session, p.tenant_id, _individual(p))


@router.get("/gaps")
def gaps(p: Principal = Depends(require("analytics:read")), session: Session = Depends(get_session)):
    return analytics.gap_summary(session, p.tenant_id)


@router.get("/pipeline")
def pipeline(job: str | None = None, p: Principal = Depends(require("analytics:read")),
             session: Session = Depends(get_session)):
    try:
        job_id = uuid.UUID(job) if job else None
    except ValueError:
        raise HTTPException(404, "Job not found")
    return analytics.pipeline(session, p.tenant_id, job_id)


@router.get("/workload")
def workload(team: str | None = None, p: Principal = Depends(require("analytics:read", "skills:read", human_only=True)),
             session: Session = Depends(get_session)):
    """Employees x projects: individual data, managers and admins only, and the viewing is audited."""
    _log_individual(session, p, "workload")
    return analytics.workload(session, p.tenant_id, team)


@router.get("/trends")
def trends(metric: str, team: str | None = None, days: int = Query(default=30, ge=1, le=365),
           p: Principal = Depends(require("analytics:read")), session: Session = Depends(get_session)):
    if metric not in analytics.SNAPSHOT_METRICS:
        raise HTTPException(422, f"metric must be one of {sorted(analytics.SNAPSHOT_METRICS)}")
    return analytics.trends(session, p.tenant_id, metric, team, days, _individual(p))


@router.post("/insights")
def summarise(p: Principal = Depends(require("analytics:read", human_only=True)), session: Session = Depends(get_session)):
    """AI narrative of the aggregates the caller may see. Every number in it is checked against the metrics."""
    teams_ = analytics.teams_view(session, p.tenant_id, _individual(p))
    gaps_ = analytics.gap_summary(session, p.tenant_id)
    pipe = analytics.pipeline(session, p.tenant_id)
    spofs = analytics.single_points_of_failure(session, p.tenant_id)
    metrics = analytics.insight_metrics(teams_, gaps_, pipe, len(spofs))
    return {**insights.summarise(metrics, {t["team"]: t["flags"] for t in teams_}, {t["team"]: t["name"] for t in teams_}),
            "metrics": metrics}


@router.post("/snapshot")
def snapshot(p: Principal = Depends(require("analytics:snapshot")), session: Session = Depends(get_session)):
    """Stores today's aggregates (the n8n daily cron, or an admin). Running it twice the same day replaces the day."""
    return analytics.snapshot(session, p.tenant_id, p.subject)


class ExportNote(Strict):
    view: str = Field(pattern=r"^[a-z_]{2,40}$")


@router.post("/exported")
def exported(body: ExportNote, p: Principal = Depends(require("analytics:read", human_only=True)),
             session: Session = Depends(get_session)):
    """The dashboard reports each CSV download here, so exports appear in the audit log."""
    audit.log(session, actor=p.subject, event="analytics.exported", tenant_id=p.tenant_id, detail={"view": body.view})
    return {"logged": True}
