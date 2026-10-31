"""Append-only, hash-chained audit log. Any edit or deletion of a past row breaks the chain."""
import hashlib
import json
from typing import Any, Optional

from sqlmodel import Session, select

from backend.models import AuditLog


def _digest(prev_hash: str, row: AuditLog) -> str:
    material = json.dumps(
        [prev_hash, row.tenant_id, row.ts.isoformat(), row.actor, row.event, row.target, row.detail],
        sort_keys=True,
    )
    return hashlib.sha256(material.encode()).hexdigest()


def log(
    session: Session,
    *,
    actor: str,
    event: str,
    tenant_id: Optional[int] = None,
    target: str = "",
    detail: Optional[dict[str, Any]] = None,
    commit: bool = True,
) -> AuditLog:
    last = session.exec(select(AuditLog).order_by(AuditLog.id.desc()).limit(1)).first()
    prev_hash = last.hash if last else "GENESIS"
    row = AuditLog(
        tenant_id=tenant_id,
        actor=actor,
        event=event,
        target=target,
        detail=json.dumps(detail or {}, default=str)[:8000],
        prev_hash=prev_hash,
    )
    row.hash = _digest(prev_hash, row)
    session.add(row)
    if commit:
        session.commit()
    return row


def verify_chain(session: Session) -> tuple[bool, Optional[int]]:
    """Returns (ok, first_broken_id)."""
    prev = "GENESIS"
    for row in session.exec(select(AuditLog).order_by(AuditLog.id)):
        if row.prev_hash != prev or _digest(prev, row) != row.hash:
            return False, row.id
        prev = row.hash
    return True, None
