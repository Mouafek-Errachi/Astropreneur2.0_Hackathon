"""API request bodies. Unknown fields are rejected (extra="forbid"), as in the n8n field whitelist.
Agent output schemas live in backend/agents/schemas.py."""
from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, EmailStr, Field


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


class JobCreate(Strict):
    title: str = Field(min_length=2, max_length=150)
    description: str = Field(default="", max_length=8000)
    required_skills: list[str] = Field(default_factory=list, max_length=40)
    nice_to_have: list[str] = Field(default_factory=list, max_length=40)
    min_years: float = Field(default=0, ge=0, le=40)


class EmailIn(Strict):
    sender: EmailStr
    subject: str = Field(default="", max_length=300)
    body: str = Field(max_length=50_000)
    external_id: str = Field(default="", max_length=200)
    received_at: Optional[datetime] = None


class DecisionRequest(Strict):
    decision: Literal["shortlisted", "interview", "rejected"]
    note: str = Field(default="", max_length=1000)


class CandidateEmailRequest(Strict):
    kind: Literal["rejection", "acknowledgement", "reminder"]


class ChatRequest(Strict):
    question: str = Field(min_length=3, max_length=500)


class ApproveRequest(Strict):
    note: str = Field(default="", max_length=1000)
    edited_subject: Optional[str] = Field(default=None, max_length=200)
    edited_body: Optional[str] = Field(default=None, max_length=8000)
    selected_slots: Optional[list[dict]] = None
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


class AskRequest(Strict):
    question: str = Field(min_length=3, max_length=1000)


class TicketCreate(Strict):
    request_text: str = Field(min_length=10, max_length=3000)


class BroadcastRequest(Strict):
    target_team: str = Field(pattern=r"^[a-z_]{2,40}$")
    message_prompt: str = Field(min_length=10, max_length=3000)


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
