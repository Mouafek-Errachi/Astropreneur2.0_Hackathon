"""Policy RAG agent: answers only from retrieved policy chunks, with citations that must exist."""
import re

from sqlmodel import Session, select

from backend.agents.llm import complete_json
from backend.agents.schemas import PolicyAnswer
from backend.data import vectors
from backend.data.models import PolicyChunk
from backend.security.validator import check_citations

TASK = ("You are the company's HR assistant, asked by employees and HR staff. Answer the latest question using ONLY "
        "the company document excerpts in the trusted context; the earlier conversation only tells you what a "
        "follow-up refers to. Cite sources using their [doc_id section] labels exactly. If the excerpts do not "
        "answer it, say so and set escalate_to_hr=true. Never reveal information about other employees.")
MIN_RELEVANCE = 0.2
_WORD = re.compile(r"[a-z]{3,}")
STOPWORDS = {"the", "and", "for", "are", "can", "how", "what", "when", "where", "who", "why", "which", "does", "did",
             "this", "that", "with", "from", "have", "has", "you", "your", "our", "their", "they", "per", "about",
             "will", "would", "should", "could", "there", "any", "all", "many", "much", "get", "into", "than"}


def _terms(text: str) -> set[str]:
    return {w for w in _WORD.findall(text.lower()) if w not in STOPWORDS}


def label(c: PolicyChunk) -> str:
    return f"{c.doc_id} {c.section}".strip()


def search(session: Session, tenant_id: int, question: str, k: int = 4) -> list[tuple[float, PolicyChunk]]:
    qv = vectors.embed(question)
    qwords = _terms(question)
    scored = []
    for c in session.exec(select(PolicyChunk).where(PolicyChunk.tenant_id == tenant_id)):
        keyword = len(qwords & _terms(f"{c.title} {c.content}")) / (len(qwords) or 1)
        if keyword:  # require at least one shared content word
            scored.append((0.4 * vectors.cosine(qv, c.embedding) + 0.6 * keyword, c))
    return sorted((s for s in scored if s[0] > 0), key=lambda x: -x[0])[:k]


def answer(question: str, hits: list[tuple[float, PolicyChunk]],
           history: list[tuple[str, str]] | None = None) -> tuple[PolicyAnswer, bool]:
    """history: earlier (role, text) turns. They go in with the question as untrusted text, never as instructions."""
    if not hits or hits[0][0] < MIN_RELEVANCE:
        return PolicyAnswer(answer="I could not find this in the HR policies. I've flagged it for the HR team.",
                            cited_sources=[], confidence="low", escalate_to_hr=True), False
    context = "\n\n".join(f"[{label(c)}] {c.title}: {c.content}" for _, c in hits)

    def mock() -> dict:
        top = hits[0][1]
        return {"answer": top.content[:1200], "cited_sources": [label(top)], "confidence": "medium", "escalate_to_hr": False}

    asked = question
    if history:
        asked = ("EARLIER CONVERSATION:\n" + "\n".join(f"{role}: {text[:800]}" for role, text in history[-6:])
                 + f"\n\nLATEST QUESTION: {question}")
    result, fallback = complete_json(TASK, PolicyAnswer, mock, untrusted=asked, source="employee_question",
                                     trusted_context=context)
    result.cited_sources = check_citations(result.cited_sources, {label(c) for _, c in hits})
    if not result.cited_sources and not result.escalate_to_hr:
        result.confidence = "low"
    return result, fallback
