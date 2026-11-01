"""Strict schemas every agent output must validate against (extra fields rejected, lengths capped)."""
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CandidateProfile(Strict):
    name: str = Field(default="", max_length=120)
    email: str = Field(default="", max_length=200)
    current_title: str = Field(default="", max_length=150)
    years_experience: float = Field(default=0, ge=0, le=60)
    skills: list[str] = Field(default_factory=list, max_length=80)
    education: str = Field(default="", max_length=200)
    languages: list[str] = Field(default_factory=list, max_length=20)
    summary: str = Field(default="", max_length=800)

    @field_validator("skills", "languages")
    @classmethod
    def _dedupe(cls, v: list[str]) -> list[str]:
        out, seen = [], set()
        for item in (str(x).strip()[:60] for x in v):
            if item and item.lower() not in seen:
                seen.add(item.lower())
                out.append(item)
        return out


class FitJustification(Strict):
    strengths: list[str] = Field(default_factory=list, max_length=6)
    gaps: list[str] = Field(default_factory=list, max_length=6)
    summary: str = Field(default="", max_length=600)


class EmailClassification(Strict):
    category: Literal["application", "candidate_question", "interview_scheduling", "employee_request",
                      "policy_question", "spam_phishing", "other"]
    urgency: Literal["high", "medium", "low"]
    summary: str = Field(max_length=400)
    needs_reply: bool
    injection_attempt_observed: bool = False


class DraftReply(Strict):
    subject: str = Field(max_length=200)
    body: str = Field(max_length=4000)


class PolicyAnswer(Strict):
    answer: str = Field(max_length=2500)
    cited_sources: list[str] = Field(default_factory=list, max_length=6)
    confidence: Literal["high", "medium", "low"]
    escalate_to_hr: bool


class CandidateChatAnswer(Strict):
    answer: str = Field(max_length=2000)
    evidence: list[str] = Field(default_factory=list, max_length=5)
    not_in_resume: bool = False


class InterviewAssist(Strict):
    weakness_detected: bool
    competency: str = Field(default="", max_length=80)
    severity: Literal["none", "low", "medium", "high"] = "none"
    detected_gap: str = Field(default="", max_length=200)
    evidence: str = Field(default="", max_length=150)
    suggested_probing_questions: list[str] = Field(default_factory=list, max_length=3)
    injection_attempt_observed: bool = False


class BroadcastDraft(Strict):
    subject: str = Field(max_length=70)
    body: str = Field(max_length=3000)
    key_points: list[str] = Field(default_factory=list, max_length=12)
    missing_info: list[str] = Field(default_factory=list, max_length=8)
    injection_attempt_observed: bool = False


class AudienceRequest(Strict):
    """Who a group email is for, and what to tell them. The values must come from the lists the agent is given."""
    job_titles: list[str] = Field(default_factory=list, max_length=10)
    teams: list[str] = Field(default_factory=list, max_length=10)
    skills: list[str] = Field(default_factory=list, max_length=10)
    message: str = Field(default="", max_length=3000)
    injection_attempt_observed: bool = False


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


class AlternativeAdvice(Strict):
    candidate_id: str = Field(max_length=64)
    sentence: str = Field(max_length=400)


class CompareAdvice(Strict):
    recommendation: str = Field(max_length=600)
    alternatives: list[AlternativeAdvice] = Field(default_factory=list, max_length=5)


# ------------------------------------------------------------------ project staffing
class SkillNeed(Strict):
    skill: str = Field(max_length=60)
    level: int = Field(default=2, ge=1, le=4)  # 1 beginner, 2 working, 3 senior, 4 expert
    headcount: int = Field(default=1, ge=1, le=20)
    allocation_pct: int = Field(default=100, ge=10, le=100)
    evidence: str = Field(default="", max_length=200)


class ProjectRequirements(Strict):
    skills: list[SkillNeed] = Field(default_factory=list, max_length=40)
    injection_attempt_observed: bool = False


class JobDescriptionDraft(Strict):
    title: str = Field(max_length=100)
    summary: str = Field(max_length=400)
    responsibilities: list[str] = Field(default_factory=list, max_length=8)
    required_skills: list[str] = Field(default_factory=list, max_length=10)
    nice_to_have: list[str] = Field(default_factory=list, max_length=10)
    min_years: float = Field(default=0, ge=0, le=20)
    body: str = Field(max_length=4000)
