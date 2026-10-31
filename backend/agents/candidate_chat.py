"""Candidate Profile Chat agent: a recruiter asks questions about ONE candidate; the agent answers
only from that candidate's resume (candidate-scoped retrieval, no cross-candidate data)."""
import re

from backend.agents.llm import complete_json
from backend.agents.schemas import CandidateChatAnswer

TASK = ("Answer the recruiter's question about this candidate using only the resume. Quote short evidence. "
        "If the resume does not say, set not_in_resume=true. Never speculate about protected characteristics.")


def ask(question: str, resume_text: str, profile_json: str) -> tuple[CandidateChatAnswer, bool]:
    def mock() -> dict:
        words = {w for w in re.findall(r"[a-z]{4,}", question.lower())}
        lines = [l.strip() for l in resume_text.splitlines() if l.strip()]
        hits = [l for l in lines if words & set(re.findall(r"[a-z]{4,}", l.lower()))][:3]
        if not hits:
            return {"answer": "The resume does not mention this.", "evidence": [], "not_in_resume": True}
        return {"answer": " ".join(hits)[:600], "evidence": [h[:200] for h in hits], "not_in_resume": False}

    return complete_json(TASK + f"\n\nRecruiter question: {question}", CandidateChatAnswer, mock,
                         untrusted=resume_text, source="resume", trusted_context=f"EXTRACTED PROFILE: {profile_json}")
