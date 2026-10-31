"""API request/response schemas and the strict schemas every LLM output must validate against."""
import re
from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

# Syntax check only: EmailStr rejects reserved demo domains such as acme.test.
EMAIL_RE = re.compile(r"^[\w.+-]+@[\w-]+(\.[\w-]+)+$")


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ---------------------------------------------------------------- LLM outputs
class CandidateProfile(Strict):
    name: str = Field(default="", max_length=120)
    email: str = Field(default="", max_length=200)
    phone: str = Field(default="", max_length=50)
    current_title: str = Field(default="", max_length=150)
    years_experience: float = Field(default=0, ge=0, le=60)
    skills: list[str] = Field(default_factory=list, max_length=80)
    education: str = Field(default="", max_length=200)
    languages: list[str] = Field(default_factory=list, max_length=20)
    summary: str = Field(default="", max_length=800)

    @field_validator("skills", "languages")
    @classmethod
    def _clean_list(cls, v: list[str]) -> list[str]:
        out, seen = [], set()
        for item in v:
            item = str(item).strip()[:60]
            if item and item.lower() not in seen:
                seen.add(item.lower())
                out.append(item)
        return out


class FitAssessment(Strict):
    strengths: list[str] = Field(default_factory=list, max_length=6)
    gaps: list[str] = Field(default_factory=list, max_length=6)
    rationale: str = Field(default="", max_length=900)


EmailCategory = Literal[
    "application",
    "candidate_question",
    "interview_scheduling",
    "employee_request",
    "policy_question",
    "spam_phishing",
    "other",
]


class AlternativeAdvice(Strict):
    candidate_id: int
    sentence: str = Field(max_length=400)


class CompareAdvice(Strict):
    recommendation: str = Field(max_length=600)
    alternatives: list[AlternativeAdvice] = Field(default_factory=list, max_length=5)


class EmailClassification(Strict):
    category: EmailCategory
    priority: Literal["high", "medium", "low"]
    summary: str = Field(max_length=400)
    needs_reply: bool


class DigestSummary(Strict):
    headline: str = Field(max_length=300)
    bullets: list[str] = Field(default_factory=list, max_length=10)


class DraftReply(Strict):
    subject: str = Field(max_length=200)
    body: str = Field(max_length=4000)


class QAAnswer(Strict):
    answer: str = Field(max_length=2500)
    cited_sources: list[str] = Field(default_factory=list, max_length=6)
    confidence: Literal["high", "medium", "low"]
    escalate_to_hr: bool


class SearchCriteria(Strict):
    """What the recruiter asks for: read from their own words by the LLM, or set by hand."""
    required_skills: list[str] = Field(default_factory=list, max_length=40)
    nice_to_have: list[str] = Field(default_factory=list, max_length=40)
    min_years: float = Field(default=0, ge=0, le=40)

    @field_validator("required_skills", "nice_to_have")
    @classmethod
    def _clean_skills(cls, v: list[str]) -> list[str]:
        out = []
        for s in v:
            s = " ".join(str(s).split())
            if len(s) > 60:
                raise ValueError("a skill name is at most 60 characters")
            if s and s.lower() not in [o.lower() for o in out]:
                out.append(s)
        return out


# ---------------------------------------------------------------- API
class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"
    role: str
    tenant_id: int
    email: str


class Me(BaseModel):
    email: str
    role: str
    tenant_id: int
    kind: str
    scopes: list[str]


class ApiKeyCreate(Strict):
    name: str = Field(min_length=1, max_length=80)
    scopes: list[str]


class ApiKeyOut(BaseModel):
    id: int
    name: str
    prefix: str
    scopes: list[str]
    active: bool
    created_at: datetime
    last_used_at: Optional[datetime] = None
    key: Optional[str] = None  # only returned once, at creation


class Weights(Strict):
    """How much each criterion counts. Re-normalised over the criteria the job actually defines."""
    required: int = Field(default=60, ge=0, le=100)
    nice: int = Field(default=15, ge=0, le=100)
    experience: int = Field(default=25, ge=0, le=100)


class JobCriteria(SearchCriteria):
    weights: Weights = Field(default_factory=Weights)


class CriteriaText(Strict):
    text: str = Field(min_length=3, max_length=2000)


class CriteriaFromText(BaseModel):
    criteria: SearchCriteria
    llm_fallback: bool


class JobCreate(JobCriteria):
    title: str = Field(min_length=2, max_length=150)
    description: str = Field(default="", max_length=8000)


class JobOut(BaseModel):
    id: int
    title: str
    description: str
    required_skills: list[str]
    nice_to_have: list[str]
    min_years: float
    weights: dict
    candidate_count: int = 0


class UploadResult(BaseModel):
    filename: str
    accepted: bool
    candidate_id: Optional[int] = None
    error: Optional[str] = None
    risk_level: str = "none"
    flags: list[str] = []


class CandidateOut(BaseModel):
    id: int
    job_id: int
    name: str
    email: str
    original_filename: str
    status: str
    score: Optional[float]
    rank: Optional[int]
    bucket: Optional[str] = None
    risk_level: str
    needs_review: bool
    llm_fallback: bool
    injection_flags: list[dict]
    profile: dict
    score_breakdown: dict
    justification: str = ""


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


class EmailOut(BaseModel):
    id: int
    sender: str
    subject: str
    body: str
    received_at: datetime
    category: Optional[str]
    priority: Optional[str]
    summary: str
    needs_reply: bool
    risk_level: str
    injection_flags: list[dict]
    handled: bool


class CandidateDraftRequest(Strict):
    kind: Literal["rejection", "invitation", "acknowledgement"]


class CompareRequest(Strict):
    candidate_ids: list[int] = Field(min_length=2, max_length=6)


class DecisionRequest(Strict):
    decision: Literal["shortlisted", "interview", "rejected"]
    note: str = Field(default="", max_length=1000)


class ActionOut(BaseModel):
    id: int
    type: str
    status: str
    title: str
    rationale: str
    payload: dict
    risk_level: str
    risk_flags: list[dict]
    created_by: str
    created_at: datetime
    decided_by: Optional[str]
    decided_at: Optional[datetime]
    decision_note: str
    executed_at: Optional[datetime]
    result: str


class ApproveRequest(Strict):
    note: str = Field(default="", max_length=1000)
    edited_subject: Optional[str] = Field(default=None, max_length=200)
    edited_body: Optional[str] = Field(default=None, max_length=8000)
    selected_slot: Optional[int] = Field(default=None, ge=0, le=10)
    acknowledge_risk: bool = False


class RejectRequest(Strict):
    note: str = Field(default="", max_length=1000)


class ScheduleRequest(Strict):
    candidate_id: int
    interviewer: str = Field(min_length=1, max_length=120)
    duration_minutes: int = Field(default=45, ge=15, le=240)
    days_ahead: int = Field(default=7, ge=1, le=30)


class DemoDataRequest(Strict):
    resumes: int = Field(default=60, ge=0, le=500)
    emails: int = Field(default=40, ge=0, le=500)
    seed: int = 7


class AskRequest(Strict):
    question: str = Field(min_length=3, max_length=1000)


class AskResponse(BaseModel):
    answer: str
    citations: list[dict]
    confidence: str
    escalate_to_hr: bool
    flags: list[dict] = []
    llm_fallback: bool = False
