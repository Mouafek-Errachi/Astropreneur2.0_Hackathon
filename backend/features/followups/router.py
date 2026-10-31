from fastapi import APIRouter, Depends
from sqlmodel import Session, select

from backend.core.security import Principal, get_owned, require
from backend.db import get_session
from backend.features.followups.reminders import scan_unanswered
from backend.models import Followup

router = APIRouter(prefix="/followups", tags=["followups"])


@router.post("/scan")
def scan(p: Principal = Depends(require("followups:manage")), session: Session = Depends(get_session)):
    return {"created": len(scan_unanswered(session, p.tenant_id))}


@router.get("")
def list_followups(p: Principal = Depends(require("followups:read")), session: Session = Depends(get_session)):
    return session.exec(select(Followup).where(Followup.tenant_id == p.tenant_id)).all()


@router.post("/{fid}/done")
def mark_done(fid: int, p: Principal = Depends(require("followups:manage")), session: Session = Depends(get_session)):
    f = get_owned(session, Followup, fid, p)
    f.status = "done"
    session.add(f)
    session.commit()
    return f
