"""API request bodies. Unknown fields are rejected (extra="forbid"), as in the n8n field whitelist.
Agent output schemas live in backend/agents/schemas.py."""
import re
from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from backend.agents.schemas import SearchCriteria, SkillNeed

# Syntax check only: EmailStr rejects reserved demo domains such as acme.test.
EMAIL_RE = re.compile(r"^[\w.+-]+@[\w-]+(\.[\w-]+)+$")


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"
    role: str
    email: str


class ApiKeyCreate(Strict):
    name: str = Field(min_length=1, max_length=80)
    scopes: list[str] = Field(min_length=1)


class Weights(Strict):
    """How much each criterion counts. Re-normalised over the criteria the job actually defines."""
    required: int = Field(default=60, ge=0, le=100)
    nice: int = Field(default=15, ge=0, le=100)
    experience: int = Field(default=25, ge=0, le=100)


class JobCriteria(SearchCriteria):
    weights: Weights = Field(default_factory=Weights)


class CriteriaText(Strict):
    text: str = Field(min_length=3, max_length=2000)


class JobCreate(JobCriteria):
    title: str = Field(min_length=2, max_length=150)
    description: str = Field(default="", max_length=8000)


class CompareRequest(Strict):
    application_ids: list[str] = Field(min_length=2, max_length=6)


class EmailIn(Strict):
    sender: str = Field(max_length=254)
    subject: str = Field(default="", max_length=300)
    body: str = Field(max_length=50_000)
    external_id: str = Field(default="", max_length=200)
    received_at: Optional[datetime] = None

    @field_validator("sender")
    @classmethod
    def _valid_sender(cls, v: str) -> str:
        v = v.strip().lower()
        if not EMAIL_RE.match(v):
            raise ValueError("not a valid email address")
        return v


IntakeSource = Literal["email", "linkedin", "drive", "form", "job_board", "other"]


class IntakeItem(Strict):
    """One resume from an outside source: a file (base64), pasted text, or a structured profile. Exactly one."""
    source: IntakeSource
    source_ref: str = Field(default="", max_length=200)
    filename: str = Field(default="", max_length=200)
    content_base64: Optional[str] = Field(default=None, max_length=14_000_000)
    text: Optional[str] = Field(default=None, max_length=50_000)
    profile: Optional[dict] = None

    @model_validator(mode="after")
    def _one_payload(self):
        if sum(x is not None for x in (self.content_base64, self.text, self.profile)) != 1:
            raise ValueError("give exactly one of content_base64, text or profile")
        return self


class IntakeRequest(Strict):
    job_id: str = Field(max_length=64)
    items: list[IntakeItem] = Field(min_length=1, max_length=50)


class DecisionRequest(Strict):
    decision: Literal["shortlisted", "interview", "rejected"]
    note: str = Field(default="", max_length=1000)


class CandidateEmailRequest(Strict):
    kind: Literal["rejection", "acknowledgement"]


class ChatRequest(Strict):
    question: str = Field(min_length=3, max_length=500)


class ApproveRequest(Strict):
    note: str = Field(default="", max_length=1000)
    edited_subject: Optional[str] = Field(default=None, max_length=200)
    edited_body: Optional[str] = Field(default=None, max_length=8000)
    selected_slot: Optional[int] = Field(default=None, ge=0, le=10)  # interviews: book this slot now
    excluded_recipients: list[str] = Field(default_factory=list, max_length=500)  # group emails: employee ids to drop
    acknowledge_risk: bool = False


class RejectRequest(Strict):
    note: str = Field(default="", max_length=1000)


class ScheduleRequest(Strict):
    application_id: str
    interviewer: str = Field(min_length=1, max_length=120)
    duration_minutes: int = Field(default=45, ge=15, le=240)
    days_ahead: int = Field(default=7, ge=1, le=30)


class ChooseSlot(Strict):
    slot_index: int = Field(ge=0, le=10)


class ChatTurn(Strict):
    role: Literal["user", "assistant"]
    text: str = Field(max_length=2500)


class AskRequest(Strict):
    question: str = Field(min_length=3, max_length=1000)
    history: list[ChatTurn] = Field(default_factory=list, max_length=6)  # earlier turns, for follow-up questions


class TicketCreate(Strict):
    request_text: str = Field(min_length=10, max_length=3000)


class BroadcastRequest(Strict):
    target_team: str = Field(pattern=r"^[a-z_]{2,40}$")
    message_prompt: str = Field(min_length=10, max_length=3000)


class GroupEmailRequest(Strict):
    request: str = Field(min_length=10, max_length=3000)  # "send to all backend engineers that ..."


class AwardRequest(Strict):
    employee_id: str = Field(pattern=r"^emp_\d{3,6}$")
    action_type: str = Field(pattern=r"^[a-z_]{2,40}$")
    note: str = Field(default="", max_length=200)
    idempotency_key: str = Field(pattern=r"^[A-Za-z0-9_-]{8,64}$")


class InterviewAssistRequest(Strict):
    job_role: str = Field(min_length=2, max_length=120)
    competencies: list[str] = Field(default_factory=list, max_length=10)
    resume_summary: str = Field(default="", max_length=2000)
    previous_chunk: str = Field(default="", max_length=4000)
    current_chunk: str = Field(min_length=5, max_length=4000)


# ---------------------------------------------------------------- project staffing
class ProjectCreate(Strict):
    name: str = Field(min_length=2, max_length=150)
    summary: str = Field(default="", max_length=2000)
    team_id: Optional[str] = Field(default=None, pattern=r"^[a-z_]{2,40}$")
    deadline: str = Field(default="", max_length=20)


class RequirementsIn(Strict):
    skills: list[SkillNeed] = Field(max_length=40)


class GapDecision(Strict):
    decision: Literal["open", "internal", "hire_externally"]
    plan: str = Field(default="", max_length=300)  # required to cover a gap internally


class EmployeeSkillIn(Strict):
    skill: str = Field(min_length=1, max_length=60)
    level: int = Field(ge=1, le=4)
    validated: bool = True  # only honoured for managers


class EmployeeSkillsIn(Strict):
    skills: list[EmployeeSkillIn] = Field(min_length=1, max_length=40)
