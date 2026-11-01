import asyncio
import secrets
import time
import uuid
from dataclasses import dataclass, field


@dataclass
class Session:
    id: str
    owner: str
    job_title: str
    job_description: str
    candidate_id: str | None = None                         # the HR platform's candidate id, if HR gave one
    tenant_id: str | None = None
    language: str = ""                                       # speech-to-text language, "" = detect
    transcript: list[dict] = field(default_factory=list)   # in-memory only until HR confirms (export)
    audio_bytes: dict = field(default_factory=lambda: {"candidate": 0, "hr": 0})
    chat: list[dict] = field(default_factory=list)          # {"role": "user"|"model", "text": str}
    suggested: list[str] = field(default_factory=list)      # questions already offered
    end_prompted: bool = False
    ended: bool = False
    live: object | None = None                              # LiveSession while the audio socket is open
    summary: dict | None = None                             # SummaryContent as dict (HR-edited version)
    notes: list[str] = field(default_factory=list)          # HR's personal notes, kept apart from the summary
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    last_seen: float = field(default_factory=time.monotonic)


_sessions: dict[str, Session] = {}


def create(owner: str, job_title: str, job_description: str,
           candidate_id: str | None = None, tenant_id: str | None = None, language: str = "") -> Session:
    s = Session(uuid.uuid4().hex, owner, job_title, job_description, candidate_id, tenant_id, language)
    _sessions[s.id] = s
    return s


def get(session_id: str, owner: str) -> Session | None:
    s = _sessions.get(session_id)
    if not (s and s.owner == owner):
        return None
    s.last_seen = time.monotonic()
    return s


def purge_stale(ttl_seconds: float) -> int:
    """Drops sessions nobody touched for ttl_seconds and with no open audio socket."""
    cutoff = time.monotonic() - ttl_seconds
    stale = [k for k, s in _sessions.items() if s.last_seen < cutoff and s.live is None]
    for k in stale:
        del _sessions[k]
    return len(stale)


# One-time tickets for the audio WebSocket, so the JWT never goes into a URL
TICKET_TTL = 60.0
_tickets: dict[str, tuple[str, str, float]] = {}   # ticket -> (session id, owner, expiry)


def issue_ticket(s: Session) -> str:
    now = time.monotonic()
    for k in [k for k, v in _tickets.items() if v[2] < now]:
        del _tickets[k]
    t = secrets.token_urlsafe(24)
    _tickets[t] = (s.id, s.owner, now + TICKET_TTL)
    return t


def redeem_ticket(ticket: str, session_id: str) -> Session | None:
    entry = _tickets.pop(ticket, None)
    if not entry or entry[0] != session_id or entry[2] < time.monotonic():
        return None
    return get(session_id, entry[1])
