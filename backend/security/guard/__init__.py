"""L5 Injection Guard: every piece of untrusted content passes through `inspect_text` before any
agent sees it. High-risk content is quarantined and raises a security event (firewall visualizer)."""
import json
from dataclasses import dataclass, field

from sqlmodel import Session

from backend.security import events
from backend.security.guard import injection
from backend.security.guard.injection import Flag
from backend.security.guard.sanitizer import sanitize_text


@dataclass
class GuardResult:
    clean: str
    flags: list[Flag] = field(default_factory=list)
    risk_level: str = "none"
    score: float = 0.0

    @property
    def quarantine(self) -> bool:
        return self.risk_level == "high"

    @property
    def flags_json(self) -> str:
        return json.dumps([f.to_dict() for f in self.flags])


def inspect_text(session: Session | None, text: str, *, source: str, source_id: str = "", tenant_id: int | None = None,
                 hidden_text: str = "", sanitizer_notes: list[str] | None = None, max_chars: int = 30_000,
                 already_clean: bool = False) -> GuardResult:
    clean, notes = (text, []) if already_clean else sanitize_text(text, max_chars)
    flags = injection.scan(clean, hidden_text=hidden_text, sanitizer_notes=(sanitizer_notes or []) + notes)
    result = GuardResult(clean, flags, injection.risk_level(flags), injection.injection_score(flags))
    if session is not None and flags:
        events.record(session, tenant_id=tenant_id, source=source, source_id=source_id,
                      kind="hidden_text" if hidden_text.strip() else "injection", severity=result.risk_level,
                      detail={"flags": [f.to_dict() for f in flags][:10], "score": result.score})
    return result
