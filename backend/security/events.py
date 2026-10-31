"""Security events for the AI Firewall visualizer: stored in the DB and pushed live to WebSocket
subscribers (in-process; TODO: Redis pub/sub when the API runs as several processes)."""
import asyncio
import json
from typing import Any

from sqlmodel import Session

from backend.data.models import SecurityEvent

_subscribers: set[asyncio.Queue] = set()
_loop: asyncio.AbstractEventLoop | None = None


def bind_loop(loop: asyncio.AbstractEventLoop) -> None:
    global _loop
    _loop = loop


def subscribe() -> asyncio.Queue:
    q: asyncio.Queue = asyncio.Queue(maxsize=100)
    _subscribers.add(q)
    return q


def unsubscribe(q: asyncio.Queue) -> None:
    _subscribers.discard(q)


def _publish(message: dict) -> None:
    for q in list(_subscribers):
        if not q.full():
            q.put_nowait(message)


def record(session: Session, *, tenant_id: int | None, source: str, kind: str, severity: str = "low",
           source_id: str = "", detail: dict[str, Any] | None = None, commit: bool = True) -> SecurityEvent:
    ev = SecurityEvent(tenant_id=tenant_id, source=source, source_id=source_id, kind=kind, severity=severity,
                       detail=json.dumps(detail or {}, default=str)[:4000])
    session.add(ev)
    if commit:
        session.commit()
        session.refresh(ev)
    msg = {"id": ev.id, "tenant_id": tenant_id, "ts": ev.ts.isoformat(), "source": source, "kind": kind,
           "severity": severity, "source_id": source_id}
    if _loop is not None and _subscribers:
        _loop.call_soon_threadsafe(_publish, msg)
    return ev
