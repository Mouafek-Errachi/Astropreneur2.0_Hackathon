"""AI Firewall visualizer feed (REST + WebSocket), audit log and admin tools."""
import asyncio
import json

from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect
from jose import JWTError, jwt
from sqlmodel import Session, select

from backend.config import get_settings
from backend.data.db import get_session
from backend.data.models import AuditLog, SecurityEvent
from backend.orchestration import seed
from backend.security import audit, events
from backend.security.gateway import ROLE_SCOPES, Principal, require

router = APIRouter(tags=["security"])


@router.get("/security/events")
def list_events(limit: int = 100, p: Principal = Depends(require("security:read")), session: Session = Depends(get_session)):
    q = (select(SecurityEvent).where((SecurityEvent.tenant_id == p.tenant_id) | (SecurityEvent.tenant_id == None))  # noqa: E711
         .order_by(SecurityEvent.id.desc()).limit(min(limit, 500)))
    return [{"id": e.id, "ts": e.ts, "source": e.source, "source_id": e.source_id, "kind": e.kind,
             "severity": e.severity, "detail": json.loads(e.detail)} for e in session.exec(q)]


@router.websocket("/ws/security")
async def ws_security(ws: WebSocket, token: str = ""):
    """Live firewall events. Auth: ?token=<JWT> (browsers cannot set headers on WebSockets)."""
    s = get_settings()
    try:
        data = jwt.decode(token, s.jwt_secret, algorithms=[s.jwt_algorithm])
        assert "security:read" in ROLE_SCOPES.get(data.get("role"), set())
    except (JWTError, AssertionError):
        await ws.close(code=4401)
        return
    await ws.accept()
    q = events.subscribe()
    try:
        while True:
            msg = await q.get()
            if msg.get("tenant_id") in (None, data.get("tid")):
                await ws.send_json(msg)
    except (WebSocketDisconnect, asyncio.CancelledError):
        pass
    finally:
        events.unsubscribe(q)


@router.get("/audit")
def audit_log(limit: int = 200, p: Principal = Depends(require("audit:read")), session: Session = Depends(get_session)):
    q = select(AuditLog).where(AuditLog.tenant_id == p.tenant_id).order_by(AuditLog.id.desc()).limit(min(limit, 1000))
    return [{"id": r.id, "ts": r.ts, "actor": r.actor, "event": r.event, "target": r.target,
             "detail": json.loads(r.detail), "hash": r.hash[:16]} for r in session.exec(q)]


@router.get("/audit/verify")
def audit_verify(p: Principal = Depends(require("audit:read")), session: Session = Depends(get_session)):
    ok, broken = audit.verify_chain(session)
    return {"chain_valid": ok, "first_broken_id": broken}


@router.post("/admin/demo/load")
def load_demo(bulk: bool = False, p: Principal = Depends(require("admin", human_only=True)), session: Session = Depends(get_session)):
    """Loads the sample job, resumes (incl. adversarial ones; bulk=true adds 60 more) and emails from data/."""
    return seed.load_demo(session, p.tenant_id, p.subject, bulk)
