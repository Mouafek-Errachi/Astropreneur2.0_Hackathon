"""Database tables. Every business table carries tenant_id; queries are always tenant-scoped."""
from datetime import datetime, timezone
from typing import Optional

from sqlmodel import Field, SQLModel


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Tenant(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(index=True, unique=True)


class User(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    tenant_id: int = Field(foreign_key="tenant.id", index=True)
    email: str = Field(index=True, unique=True)
    full_name: str = ""
    hashed_password: str
    role: str = "viewer"  # admin | recruiter | viewer
    active: bool = True


class ApiKey(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    tenant_id: int = Field(foreign_key="tenant.id", index=True)
    name: str
    prefix: str = Field(index=True, unique=True)
    key_hash: str
    scopes: str = ""  # comma separated
    active: bool = True
    created_at: datetime = Field(default_factory=utcnow)
    last_used_at: Optional[datetime] = None


class Job(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    tenant_id: int = Field(foreign_key="tenant.id", index=True)
    title: str
    description: str = ""
    required_skills: str = "[]"  # JSON list
    nice_to_have: str = "[]"  # JSON list
    min_years: float = 0
    created_at: datetime = Field(default_factory=utcnow)


class Candidate(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    tenant_id: int = Field(foreign_key="tenant.id", index=True)
    job_id: int = Field(foreign_key="job.id", index=True)
    original_filename: str
    stored_filename: str
    mime_type: str
    sha256: str = Field(index=True)
    text: str = ""  # sanitized, visible text only
    name: str = ""
    email: str = ""
    profile_json: str = "{}"
    score: Optional[float] = None
    score_json: str = "{}"
    rank: Optional[int] = None
    injection_flags: str = "[]"
    risk_level: str = "none"  # none | low | medium | high
    needs_review: bool = False
    llm_fallback: bool = False
    status: str = "new"  # new | screened | shortlisted | interview | rejected
    created_at: datetime = Field(default_factory=utcnow)
    last_contact_at: Optional[datetime] = None


class Email(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    tenant_id: int = Field(foreign_key="tenant.id", index=True)
    external_id: str = Field(default="", index=True)
    sender: str
    subject: str = ""
    body: str = ""  # sanitized
    received_at: datetime = Field(default_factory=utcnow)
    category: Optional[str] = None
    priority: Optional[str] = None
    summary: str = ""
    needs_reply: bool = False
    injection_flags: str = "[]"
    risk_level: str = "none"
    handled: bool = False


class Action(SQLModel, table=True):
    """Approval queue. The agent only ever creates rows here; humans approve; approvals.py executes."""

    id: Optional[int] = Field(default=None, primary_key=True)
    tenant_id: int = Field(foreign_key="tenant.id", index=True)
    type: str  # send_email | record_decision | schedule_interview | send_reminder
    status: str = Field(default="pending", index=True)  # pending | approved | rejected | executed | failed
    payload: str = "{}"  # JSON
    title: str = ""
    rationale: str = ""
    risk_flags: str = "[]"
    risk_level: str = "none"
    created_by: str = "agent"
    created_at: datetime = Field(default_factory=utcnow)
    decided_by: Optional[str] = None
    decided_at: Optional[datetime] = None
    decision_note: str = ""
    executed_at: Optional[datetime] = None
    result: str = ""


class OutboxMessage(SQLModel, table=True):
    """Simulated outbound mail. Only approvals.execute() writes here."""

    id: Optional[int] = Field(default=None, primary_key=True)
    tenant_id: int = Field(foreign_key="tenant.id", index=True)
    action_id: int = Field(foreign_key="action.id")
    to: str
    subject: str
    body: str
    sent_at: datetime = Field(default_factory=utcnow)


class Followup(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    tenant_id: int = Field(foreign_key="tenant.id", index=True)
    kind: str  # candidate_no_contact | email_unanswered
    ref_type: str  # candidate | email
    ref_id: int
    title: str
    due_at: datetime = Field(default_factory=utcnow)
    status: str = "open"  # open | done
    action_id: Optional[int] = None


class CalendarEvent(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    tenant_id: int = Field(foreign_key="tenant.id", index=True)
    interviewer: str
    candidate_id: Optional[int] = None
    title: str
    start: datetime
    end: datetime
    action_id: Optional[int] = None


class PolicyChunk(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    tenant_id: int = Field(foreign_key="tenant.id", index=True)
    source: str
    heading: str = ""
    chunk_index: int = 0
    text: str


class AuditLog(SQLModel, table=True):
    """Append-only, hash-chained log (tamper evident)."""

    id: Optional[int] = Field(default=None, primary_key=True)
    tenant_id: Optional[int] = Field(default=None, index=True)
    ts: datetime = Field(default_factory=utcnow)
    actor: str
    event: str = Field(index=True)
    target: str = ""
    detail: str = "{}"
    prev_hash: str = ""
    hash: str = ""
