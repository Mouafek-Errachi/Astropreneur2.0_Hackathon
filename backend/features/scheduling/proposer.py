from datetime import datetime, timedelta

from backend.features.scheduling.calendar import busy_slots

WORK_START, WORK_END = 9, 17


def propose_slots(interviewer: str, duration_min: int, days_ahead: int, count: int = 3,
                  now: datetime | None = None) -> list[dict]:
    """Free weekday slots in working hours, on the hour, that don't overlap busy blocks."""
    now = now or datetime.now()
    busy = busy_slots(interviewer)
    dur = timedelta(minutes=duration_min)
    slots = []
    day = (now + timedelta(days=1)).replace(hour=WORK_START, minute=0, second=0, microsecond=0)
    for _ in range(days_ahead):
        if day.weekday() < 5:
            start = day
            while start + dur <= day.replace(hour=WORK_END) and len(slots) < count:
                end = start + dur
                if not any(s < end and start < e for s, e in busy):
                    slots.append({"start": start.isoformat(), "end": end.isoformat()})
                start += timedelta(hours=1)
        if len(slots) >= count:
            break
        day += timedelta(days=1)
    return slots
