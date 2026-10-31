import json
from datetime import datetime

from backend.config import get_settings


def busy_slots(interviewer: str) -> list[tuple[datetime, datetime]]:
    """Reads data/calendar.json. TODO: merge with approved CalendarEvent rows / real calendar API."""
    path = get_settings().data_dir / "calendar.json"
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    return [(datetime.fromisoformat(b["start"]), datetime.fromisoformat(b["end"]))
            for b in data.get(interviewer, [])]


def interviewers() -> list[str]:
    path = get_settings().data_dir / "calendar.json"
    return list(json.loads(path.read_text(encoding="utf-8")).keys()) if path.exists() else []
