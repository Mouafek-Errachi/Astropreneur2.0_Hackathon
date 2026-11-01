from pydantic import BaseModel, Field


class StartSessionRequest(BaseModel):
    job_title: str = Field(default="", max_length=200)
    job_description: str = Field(default="", max_length=20000)
    platform: str = ""
    candidate_id: str | None = Field(default=None, max_length=64, pattern=r"^[A-Za-z0-9_.:-]+$")
    language: str | None = Field(default=None, pattern=r"^[a-z]{0,3}$")   # "en", "fr", "ar"; "" = detect


class StartSessionResponse(BaseModel):
    session_id: str


class AudioTicketResponse(BaseModel):
    ticket: str
    expires_in: int


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=2000)


class ChatResponse(BaseModel):
    reply: str


class SummaryPayload(BaseModel):
    """The summary as exchanged with the bubble (HR can edit every field)."""
    background: str = Field(max_length=4000)
    strengths: list[str] = Field(max_length=30)
    concerns: list[str] = Field(max_length=30)
    notable_answers: list[str] = Field(max_length=30)
    role_fit: str = Field(max_length=4000)


class EndResponse(BaseModel):
    ended: bool
    utterances: int
    summary: SummaryPayload | None = None
    summary_error: str | None = None


class SaveSummaryRequest(BaseModel):
    summary: SummaryPayload
    notes: list[str] = Field(default_factory=list, max_length=50)


class ReviseRequest(BaseModel):
    instruction: str = Field(min_length=1, max_length=1000)
    summary: SummaryPayload | None = None     # the HR's current (edited) text; falls back to the stored one


class SummaryResponse(BaseModel):
    summary: SummaryPayload
    notes: list[str]
    comment: str = ""                          # one short line from the AI after a revision


# Structured output for live suggestions (no defaults: Gemini's schema rejects them)
class Suggestion(BaseModel):
    question: str
    reason: str


class SuggestionSet(BaseModel):
    observation: str
    suggestions: list[Suggestion]


# Structured output for the end-of-call summary (no defaults/limits: Gemini's schema is strict)
class SummaryContent(BaseModel):
    background: str
    strengths: list[str]
    concerns: list[str]
    notable_answers: list[str]
    role_fit: str


class RevisionResult(BaseModel):
    summary: SummaryContent
    comment: str
