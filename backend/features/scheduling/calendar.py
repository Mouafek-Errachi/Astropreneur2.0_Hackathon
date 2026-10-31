import json
from datetime import datetime

from sqlmodel import Session, select

from backend.config import get_settings
from backend.models import CalendarEvent, as_utc


def busy_slots(interviewer: str, session: Session | None = None, tenant_id: int | None = None) -> list[tuple[datetime, datetime]]:
    """Busy blocks from data/calendar.json (times without an offset are UTC) plus interviews already booked."""
    busy = []
    path = get_settings().data_dir / "calendar.json"
    if path.exists():
        data = json.loads(path.read_text(encoding="utf-8"))
        busy = [(as_utc(datetime.fromisoformat(b["start"])), as_utc(datetime.fromisoformat(b["end"])))
                for b in data.get(interviewer, [])]
    if session is not None and tenant_id is not None:
        busy += [(as_utc(e.start), as_utc(e.end)) for e in session.exec(
            select(CalendarEvent).where(CalendarEvent.tenant_id == tenant_id, CalendarEvent.interviewer == interviewer))]
    return busy


def is_free(session: Session, tenant_id: int, interviewer: str, start: datetime, end: datetime) -> bool:
    start, end = as_utc(start), as_utc(end)
    return not any(s < end and start < e for s, e in busy_slots(interviewer, session, tenant_id))


def interviewers() -> list[str]:
    path = get_settings().data_dir / "calendar.json"
    return list(json.loads(path.read_text(encoding="utf-8")).keys()) if path.exists() else []
