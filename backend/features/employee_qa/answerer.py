from backend.core.llm import complete_json
from backend.models import PolicyChunk
from backend.schemas import QAAnswer

TASK = (
    "Answer the employee question using ONLY the policy excerpts provided. Cite the source file names. "
    "If the excerpts do not answer it, say so and set escalate_to_hr=true.\n\nQuestion: {q}"
)
MIN_RELEVANCE = 0.25


def answer(question: str, hits: list[tuple[float, PolicyChunk]]) -> tuple[QAAnswer, bool]:
    if not hits or hits[0][0] < MIN_RELEVANCE:
        return QAAnswer(answer="I could not find this in the HR policies. I've flagged it for the HR team.",
                        cited_sources=[], confidence="low", escalate_to_hr=True), False

    context = "\n\n".join(f"[{c.source}] {c.text}" for _, c in hits)

    def mock() -> dict:
        top = hits[0][1]
        return {"answer": top.text[:1200], "cited_sources": [top.source],
                "confidence": "medium", "escalate_to_hr": False}

    return complete_json(TASK.format(q=question), context, QAAnswer, mock)
