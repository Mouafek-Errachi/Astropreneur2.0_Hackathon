import logging

from google import genai
from google.genai import types

from .config import settings
from .models import RevisionResult, SuggestionSet, SummaryContent
from .prompts import QUESTION_SYSTEM_PROMPT, REVISE_TASK, SUGGESTION_TASK, SUMMARY_SYSTEM_PROMPT, SUMMARY_TASK
from .sessions import Session

log = logging.getLogger("uvicorn.error")

_client = genai.Client(api_key=settings.gemini_api_key) if settings.gemini_api_key else None

_LABEL = {"candidate": "Candidate", "hr": "Interviewer"}


def _context(s: Session) -> str:
    recent = "\n".join(f"{_LABEL[t['speaker']]}: {t['text']}" for t in s.transcript[-40:]) or "(no transcript yet)"
    asked = "\n".join(f"- {q}" for q in s.suggested[-15:]) or "(none)"
    return (
        f"Job title: {s.job_title or 'n/a'}\n"
        f"Job description:\n{s.job_description or 'n/a'}\n\n"
        f"Recent transcript:\n{recent}\n\n"
        f"Questions already suggested:\n{asked}"
    )


async def chat_reply(s: Session, message: str) -> str:
    if _client is None:
        return "(Dev mode: set GEMINI_API_KEY to get real suggestions.)\n1. Tell me about a project you're proud of.\n2. How did you handle a tight deadline?"

    contents = [
        types.Content(role="user", parts=[types.Part(text=_context(s))]),
        types.Content(role="model", parts=[types.Part(text="Understood.")]),
    ]
    for turn in s.chat[-12:]:
        contents.append(types.Content(role=turn["role"], parts=[types.Part(text=turn["text"])]))
    contents.append(types.Content(role="user", parts=[types.Part(text=message)]))

    resp = await _client.aio.models.generate_content(
        model=settings.gemini_model_live,
        contents=contents,
        config=types.GenerateContentConfig(
            system_instruction=QUESTION_SYSTEM_PROMPT,
            temperature=0.6,
            max_output_tokens=400,
        ),
    )
    return (resp.text or "").strip() or "I couldn't come up with a suggestion. Try rephrasing."


async def live_suggestions(s: Session) -> SuggestionSet | None:
    """Called after each candidate answer. Flash model, JSON via response_schema."""
    if _client is None:
        return None
    resp = await _client.aio.models.generate_content(
        model=settings.gemini_model_live,
        contents=_context(s) + "\n\n" + SUGGESTION_TASK,
        config=types.GenerateContentConfig(
            system_instruction=QUESTION_SYSTEM_PROMPT,
            response_mime_type="application/json",
            response_schema=SuggestionSet,
            temperature=0.5,
            max_output_tokens=500,
            thinking_config=types.ThinkingConfig(thinking_budget=0),  # speed over deep reasoning
        ),
    )
    if isinstance(resp.parsed, SuggestionSet):
        return resp.parsed
    return SuggestionSet.model_validate_json(resp.text)


# ---------------- end-of-call summary (Pro model) ----------------

def _full_transcript(s: Session) -> str:
    return "\n".join(f"{_LABEL[t['speaker']]}: {t['text']}" for t in s.transcript)


def _job(s: Session) -> str:
    return f"Job title: {s.job_title or 'n/a'}\nJob description:\n{s.job_description or 'n/a'}"


def _summary_config(schema) -> types.GenerateContentConfig:
    return types.GenerateContentConfig(
        system_instruction=SUMMARY_SYSTEM_PROMPT,
        response_mime_type="application/json",
        response_schema=schema,
        temperature=0.3,
        max_output_tokens=8000,   # thinking tokens count against this on 2.5 models
        thinking_config=types.ThinkingConfig(thinking_budget=1024),
    )


async def _summary_call(contents: str, schema):
    """Summary model first; if it is unavailable or out of quota (404/429), the live model instead."""
    try:
        return await _client.aio.models.generate_content(
            model=settings.gemini_model_summary, contents=contents, config=_summary_config(schema))
    except Exception as e:
        if settings.gemini_model_summary == settings.gemini_model_live or not any(
                c in str(e) for c in ("404", "429", "NOT_FOUND", "RESOURCE_EXHAUSTED")):
            raise
        log.warning("Summary model %s failed (%s); using %s", settings.gemini_model_summary,
                    str(e)[:80], settings.gemini_model_live)
        return await _client.aio.models.generate_content(
            model=settings.gemini_model_live, contents=contents, config=_summary_config(schema))


def _parse(resp, schema):
    if isinstance(resp.parsed, schema):
        return resp.parsed
    return schema.model_validate_json(resp.text)


async def generate_summary(s: Session) -> SummaryContent:
    if not any(t["speaker"] == "candidate" for t in s.transcript):
        return SummaryContent(
            background="No candidate speech was captured, so there is nothing to summarize.",
            strengths=[], concerns=[], notable_answers=[],
            role_fit="Not enough information in the transcript.",
        )
    if _client is None:
        return SummaryContent(
            background="(Dev mode: set GEMINI_API_KEY to get a real summary.)",
            strengths=["Example strength"], concerns=["Example concern"],
            notable_answers=["Example notable answer"], role_fit="Example role fit.",
        )
    resp = await _summary_call(f"{_job(s)}\n\nTranscript:\n{_full_transcript(s)}\n\n{SUMMARY_TASK}", SummaryContent)
    return _parse(resp, SummaryContent)


async def revise_summary(s: Session, current: dict, instruction: str) -> RevisionResult:
    if _client is None:
        return RevisionResult(summary=SummaryContent(**current), comment="(Dev mode: no AI configured, nothing changed.)")
    import json
    resp = await _summary_call(
        f"{_job(s)}\n\nTranscript:\n{_full_transcript(s)}\n\n"
        f"Current summary (JSON):\n{json.dumps(current, ensure_ascii=False)}\n\n"
        f"HR instruction:\n{instruction}\n\n{REVISE_TASK}",
        RevisionResult,
    )
    return _parse(resp, RevisionResult)
