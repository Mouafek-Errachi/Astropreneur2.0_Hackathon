from backend.models import Candidate


def bucket(score: float | None) -> str:
    if score is None:
        return "unscreened"
    return "strong" if score >= 70 else "possible" if score >= 45 else "weak"


def rank(candidates: list[Candidate]) -> list[Candidate]:
    ordered = sorted(candidates, key=lambda c: (c.score is None, -(c.score or 0), c.id))
    for i, c in enumerate(ordered, 1):
        c.rank = i if c.score is not None else None
    return ordered
