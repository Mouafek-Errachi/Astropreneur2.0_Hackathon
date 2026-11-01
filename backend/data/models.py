"""L4 data model (see docs/architecture/03-classes.mmd).

Every business table carries tenant_id and every query is tenant-scoped. Primary keys are random
UUIDs so IDs cannot be enumerated. JSON columns are stored as text for SQLite/Postgres portability;
embeddings are JSON float lists (pgvector-ready, see data/vectors.py).
"""
import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import UniqueConstraint
from sqlmodel import Field, SQLModel


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _id() -> uuid.UUID:
    return uuid.uuid4()


# ------------------------------------------------------------------ identity
class Tenant(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(index=True, unique=True)


class User(SQLModel, table=True):
    id: uuid.UUID = Field(default_factory=_id, primary_key=True)
    tenant_id: int = Field(foreign_key="tenant.id", index=True)
    email: str = Field(index=True, unique=True)
    hashed_password: str
    role: str = "recruiter"  # recruiter | manager | admin (see security/gateway.py ROLE_SCOPES)
    employee_id: Optional[str] = None  # the HR user's own employee record: no self-awards, no editing own skills
    active: bool = True


class ApiKey(SQLModel, table=True):
    id: uuid.UUID = Field(default_factory=_id, primary_key=True)
    tenant_id: int = Field(foreign_key="tenant.id", index=True)
    name: str
    prefix: str = Field(index=True, unique=True)
    key_hash: str
    scopes: str = ""  # comma separated
    active: bool = True
    created_at: datetime = Field(default_factory=utcnow)
    last_used_at: Optional[datetime] = None


# ------------------------------------------------------------------ recruiting pipeline
class JobOffer(SQLModel, table=True):
    id: uuid.UUID = Field(default_factory=_id, primary_key=True)
    tenant_id: int = Field(foreign_key="tenant.id", index=True)
    title: str
    description: str = ""
    criteria: str = "{}"  # {"required_skills": [], "nice_to_have": [], "min_years": 0}
    embedding: str = "[]"
    created_at: datetime = Field(default_factory=utcnow)


class Candidate(SQLModel, table=True):
    id: uuid.UUID = Field(default_factory=_id, primary_key=True)
    tenant_id: int = Field(foreign_key="tenant.id", index=True)
    name: str = ""
    email: str = Field(default="", index=True)
    stage: str = "new"  # new | screened | shortlisted | interview | rejected | hired
    created_at: datetime = Field(default_factory=utcnow)
    last_contact_at: Optional[datetime] = None


class Resume(SQLModel, table=True):
    id: uuid.UUID = Field(default_factory=_id, primary_key=True)
    tenant_id: int = Field(foreign_key="tenant.id", index=True)
    candidate_id: uuid.UUID = Field(foreign_key="candidate.id", index=True)
    original_filename: str
    stored_filename: str
    mime_type: str
    sha256: str = Field(index=True)
    raw_text: str = ""  # sanitized, visible text only
    hidden_text: str = ""  # text removed by the guard (shown to humans, never to the LLM)
    profile: str = "{}"  # extracted CandidateProfile
    embedding: str = "[]"
    injection_score: float = 0.0  # 0..1
    flags: str = "[]"
    risk_level: str = "none"
    source: str = "upload"  # upload | email | linkedin | drive | form | job_board | other | demo
    source_ref: str = Field(default="", index=True)  # id in the source system (Gmail id, form id...)
    created_at: datetime = Field(default_factory=utcnow)


class IntakeLog(SQLModel, table=True):
    """Every resume received, accepted or refused, with where it came from and who sent it."""

    id: uuid.UUID = Field(default_factory=_id, primary_key=True)
    tenant_id: int = Field(foreign_key="tenant.id", index=True)
    job_id: uuid.UUID = Field(foreign_key="joboffer.id", index=True)
    source: str = "upload"
    source_ref: str = ""
    filename: str = ""
    actor: str = ""
    accepted: bool = False
    candidate_id: Optional[uuid.UUID] = None
    error: str = ""
    risk_level: str = "none"
    flags: str = "[]"  # JSON list of flag types
    created_at: datetime = Field(default_factory=utcnow)


class Application(SQLModel, table=True):
    id: uuid.UUID = Field(default_factory=_id, primary_key=True)
    tenant_id: int = Field(foreign_key="tenant.id", index=True)
    candidate_id: uuid.UUID = Field(foreign_key="candidate.id", index=True)
    job_id: uuid.UUID = Field(foreign_key="joboffer.id", index=True)
    resume_id: uuid.UUID = Field(foreign_key="resume.id")
    match_score: Optional[float] = None
    criteria_justification: str = "{}"
    rank: Optional[int] = None
    status: str = "new"  # new | screened | needs_review | shortlisted | interview | rejected
    llm_fallback: bool = False
    created_at: datetime = Field(default_factory=utcnow)


class FairnessReport(SQLModel, table=True):
    id: uuid.UUID = Field(default_factory=_id, primary_key=True)
    tenant_id: int = Field(foreign_key="tenant.id", index=True)
    application_id: uuid.UUID = Field(foreign_key="application.id", unique=True)
    original_score: float
    counterfactual_score: float
    rank_delta: float
    counterfactuals: str = "{}"
    created_at: datetime = Field(default_factory=utcnow)


class EmailMessage(SQLModel, table=True):
    id: uuid.UUID = Field(default_factory=_id, primary_key=True)
    tenant_id: int = Field(foreign_key="tenant.id", index=True)
    candidate_id: Optional[uuid.UUID] = Field(default=None, foreign_key="candidate.id")
    external_id: str = Field(default="", index=True)
    sender: str
    subject: str = ""
    body: str = ""  # sanitized
    received_at: datetime = Field(default_factory=utcnow)
    category: Optional[str] = None
    urgency: Optional[str] = None  # high | medium | low
    summary: str = ""
    needs_reply: bool = False
    flags: str = "[]"
    risk_level: str = "none"
    quarantined: bool = False  # high-risk mail never reaches the agents
    handled: bool = False


# ------------------------------------------------------------------ human in the loop
class ProposedAction(SQLModel, table=True):
    """The agents only ever write here. Only security/executor.py turns an approved action into effects."""

    id: uuid.UUID = Field(default_factory=_id, primary_key=True)
    tenant_id: int = Field(foreign_key="tenant.id", index=True)
    type: str  # send_email | record_decision | schedule_interview | send_reminder | broadcast | award_points
    status: str = Field(default="pending", index=True)  # pending | rejected | executed | failed
    payload: str = "{}"
    title: str = ""
    rationale: str = ""
    risk_level: str = "none"
    risk_flags: str = "[]"
    created_by_agent: str = "agent"
    requested_by: str = ""  # human who triggered the agent (four-eyes check)
    source_type: str = ""  # email | application | ticket | followup ...
    source_id: str = ""
    created_at: datetime = Field(default_factory=utcnow)
    executed_at: Optional[datetime] = None
    result: str = ""


class Approval(SQLModel, table=True):
    id: uuid.UUID = Field(default_factory=_id, primary_key=True)
    tenant_id: int = Field(foreign_key="tenant.id", index=True)
    action_id: uuid.UUID = Field(foreign_key="proposedaction.id", unique=True)
    reviewer_id: uuid.UUID = Field(foreign_key="user.id")
    reviewer_email: str
    decision: str  # approve | reject
    note: str = ""
    payload_hash: str  # sha256 of the exact payload the human approved
    decided_at: datetime = Field(default_factory=utcnow)
    signature: str  # HMAC over (action, decision, reviewer, payload_hash, decided_at)


class SentEmail(SQLModel, table=True):
    id: uuid.UUID = Field(default_factory=_id, primary_key=True)
    tenant_id: int = Field(foreign_key="tenant.id", index=True)
    action_id: uuid.UUID = Field(foreign_key="proposedaction.id")
    to: str
    subject: str
    body: str
    transport: str = "outbox"  # outbox | smtp
    message_id: str = ""
    sent_at: datetime = Field(default_factory=utcnow)


class SchedulingToken(SQLModel, table=True):
    jti: str = Field(primary_key=True)
    tenant_id: int = Field(foreign_key="tenant.id", index=True)
    candidate_id: uuid.UUID = Field(foreign_key="candidate.id")
    action_id: uuid.UUID = Field(foreign_key="proposedaction.id")
    interviewer: str
    slots: str = "[]"
    expires_at: datetime
    used: bool = False


class CalendarEvent(SQLModel, table=True):
    id: uuid.UUID = Field(default_factory=_id, primary_key=True)
    tenant_id: int = Field(foreign_key="tenant.id", index=True)
    interviewer: str
    candidate_id: Optional[uuid.UUID] = None
    title: str
    start: datetime
    end: datetime


class Followup(SQLModel, table=True):
    id: uuid.UUID = Field(default_factory=_id, primary_key=True)
    tenant_id: int = Field(foreign_key="tenant.id", index=True)
    kind: str  # email_unanswered | candidate_no_contact
    ref_type: str
    ref_id: str
    title: str
    due_at: datetime = Field(default_factory=utcnow)
    status: str = "open"
    action_id: Optional[uuid.UUID] = None


class PolicyChunk(SQLModel, table=True):
    id: uuid.UUID = Field(default_factory=_id, primary_key=True)
    tenant_id: int = Field(foreign_key="tenant.id", index=True)
    doc_id: str
    section: str = ""
    title: str = ""
    region: str = "default"
    lang: str = "en"
    content: str
    embedding: str = "[]"


# ------------------------------------------------------------------ HR operations (ported from n8n/)
class Team(SQLModel, table=True):
    id: str = Field(primary_key=True)  # e.g. "engineering"
    tenant_id: int = Field(foreign_key="tenant.id", index=True)
    display_name: str
    member_count: int = 0
    mailing_list: str
    tone_guide: str = ""


class Employee(SQLModel, table=True):
    id: str = Field(primary_key=True)  # e.g. "emp_001"
    tenant_id: int = Field(foreign_key="tenant.id", index=True)
    first_name: str
    last_initial: str
    team_id: str = Field(foreign_key="team.id")
    email: str
    job_title: str = ""  # used to address a group email ("all backend engineers")
    leave_balance_days: float = 0
    contract_type: str = "permanent"
    active: bool = True


class Ticket(SQLModel, table=True):
    id: uuid.UUID = Field(default_factory=_id, primary_key=True)
    tenant_id: int = Field(foreign_key="tenant.id", index=True)
    employee_id: str = Field(foreign_key="employee.id", index=True)
    request_text: str
    category: str = "General HR"
    priority: str = "normal"
    status: str = "open"  # open | answered | closed
    action_id: Optional[uuid.UUID] = None
    risk_level: str = "none"
    created_at: datetime = Field(default_factory=utcnow)


class PointsRule(SQLModel, table=True):
    action_type: str = Field(primary_key=True)
    tenant_id: int = Field(foreign_key="tenant.id", index=True)
    points: int
    daily_cap: int


class PointsLedger(SQLModel, table=True):
    """Append-only. Points always come from PointsRule, never from the client."""

    id: uuid.UUID = Field(default_factory=_id, primary_key=True)
    tenant_id: int = Field(foreign_key="tenant.id", index=True)
    employee_id: str = Field(foreign_key="employee.id", index=True)
    action_type: str
    points: int
    period: str  # YYYY-MM
    awarded_on: datetime = Field(default_factory=utcnow)
    awarded_by: str
    note: str = ""
    idempotency_key: str = Field(unique=True)


# ------------------------------------------------------------------ project staffing & skill gaps
class Skill(SQLModel, table=True):
    """Shared skill taxonomy: canonical name, synonyms and related skills (data/skills/taxonomy.json)."""

    name: str = Field(primary_key=True)  # canonical, lowercase
    synonyms: str = "[]"
    related: str = "[]"
    packages: str = "[]"  # dependency names in manifests that imply this skill


class EmployeeSkill(SQLModel, table=True):
    id: uuid.UUID = Field(default_factory=_id, primary_key=True)
    tenant_id: int = Field(foreign_key="tenant.id", index=True)
    employee_id: str = Field(foreign_key="employee.id", index=True)
    skill: str = Field(index=True)
    level: int = 2  # 1 beginner, 2 working, 3 senior, 4 expert
    source: str = "self"  # self | manager
    validated: bool = False  # only manager-validated skills count as coverage
    validated_by: str = ""
    last_confirmed: datetime = Field(default_factory=utcnow)


class Project(SQLModel, table=True):
    id: uuid.UUID = Field(default_factory=_id, primary_key=True)
    tenant_id: int = Field(foreign_key="tenant.id", index=True)
    name: str
    summary: str = ""
    team_id: Optional[str] = Field(default=None, foreign_key="team.id")
    deadline: str = ""
    requirements_version: int = 0
    requirements_confirmed: bool = False
    confirmed_by: str = ""
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class ProjectDocument(SQLModel, table=True):
    id: uuid.UUID = Field(default_factory=_id, primary_key=True)
    tenant_id: int = Field(foreign_key="tenant.id", index=True)
    project_id: uuid.UUID = Field(foreign_key="project.id", index=True)
    kind: str  # charter | manifest
    original_filename: str
    stored_filename: str = ""
    sha256: str = ""
    text: str = ""  # sanitized visible text (never executed)
    hidden_text: str = ""
    flags: str = "[]"
    risk_level: str = "none"
    embedding: str = "[]"
    created_at: datetime = Field(default_factory=utcnow)


class ProjectSkillRequirement(SQLModel, table=True):
    """Versioned: every analysis or human edit writes a new version; the project points at the current one."""

    id: uuid.UUID = Field(default_factory=_id, primary_key=True)
    tenant_id: int = Field(foreign_key="tenant.id", index=True)
    project_id: uuid.UUID = Field(foreign_key="project.id", index=True)
    version: int = Field(index=True)
    skill: str
    level: int = 2
    headcount: int = 1
    allocation_pct: int = 100
    evidence: str = ""
    source: str = "llm"  # llm | manifest | human


class ProjectAssignment(SQLModel, table=True):
    id: uuid.UUID = Field(default_factory=_id, primary_key=True)
    tenant_id: int = Field(foreign_key="tenant.id", index=True)
    project_id: uuid.UUID = Field(foreign_key="project.id", index=True)
    employee_id: str = Field(foreign_key="employee.id", index=True)
    allocation_pct: int = 100


class SkillGap(SQLModel, table=True):
    id: uuid.UUID = Field(default_factory=_id, primary_key=True)
    tenant_id: int = Field(foreign_key="tenant.id", index=True)
    project_id: uuid.UUID = Field(foreign_key="project.id", index=True)
    skill: str
    level: int
    needed_fte: float
    covered_fte: float
    gap_fte: float
    kind: str  # missing | insufficient_level | insufficient_capacity
    internal_options: str = "[]"  # [{employee_id, reason, level}]
    decision: str = "open"  # open | internal | hire_externally
    internal_plan: str = ""  # how the gap is covered internally (chosen from the internal options, or written)
    decided_by: str = ""
    status: str = "open"  # open | jd_proposed | hiring | closed
    action_id: Optional[uuid.UUID] = None
    job_offer_id: Optional[uuid.UUID] = None


# ------------------------------------------------------------------ workforce analytics
class PipelineEvent(SQLModel, table=True):
    """One row per application status change (data/pipeline.py), so time in each stage can be measured."""

    id: uuid.UUID = Field(default_factory=_id, primary_key=True)
    tenant_id: int = Field(foreign_key="tenant.id", index=True)
    application_id: uuid.UUID = Field(foreign_key="application.id", index=True)
    from_status: str = ""  # "" when the application is created
    to_status: str
    actor: str = ""
    at: datetime = Field(default_factory=utcnow)


class MetricSnapshot(SQLModel, table=True):
    """Daily aggregates for trend charts. Never individual rows: see docs/specs/workforce-telemetry-analytics.md."""

    __table_args__ = (UniqueConstraint("tenant_id", "day", "metric", "team_id", name="uq_snapshot_day_metric_team"),)
    id: uuid.UUID = Field(default_factory=_id, primary_key=True)
    tenant_id: int = Field(foreign_key="tenant.id", index=True)
    day: str = Field(index=True)  # YYYY-MM-DD
    metric: str = Field(index=True)
    team_id: str = ""  # "" = whole company
    value: float = 0.0


# ------------------------------------------------------------------ L5 observability
class SecurityEvent(SQLModel, table=True):
    """Feed for the AI Firewall visualizer."""

    id: Optional[int] = Field(default=None, primary_key=True)
    tenant_id: Optional[int] = Field(default=None, index=True)
    ts: datetime = Field(default_factory=utcnow)
    source: str  # resume | email | ticket | qa | chat | output | auth | file
    source_id: str = ""
    kind: str  # injection | hidden_text | blocked_file | output_violation | quarantined | auth_denied
    severity: str = "low"
    detail: str = "{}"


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
