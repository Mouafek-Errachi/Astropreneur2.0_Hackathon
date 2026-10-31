"""Resume text -> structured CandidateProfile (LLM, with a heuristic mock)."""
import re

from backend.core.llm import complete_json
from backend.schemas import CandidateProfile

SKILL_VOCAB = [
    "python", "java", "javascript", "typescript", "sql", "react", "fastapi", "django", "docker",
    "kubernetes", "aws", "azure", "gcp", "machine learning", "pandas", "excel", "recruiting",
    "communication", "leadership", "project management", "git", "linux", "c++", "figma",
]

TASK = (
    "Extract the candidate profile from the resume below. Use only facts stated in the resume. "
    "If something is missing, leave it empty."
)


def _mock(text: str) -> dict:
    lower = text.lower()
    email = re.search(r"[\w.+-]+@[\w-]+\.[\w.]+", text)
    years = re.search(r"(\d{1,2})\+?\s*years", lower)
    first_line = next((l.strip() for l in text.splitlines() if l.strip()), "")
    return {
        "name": first_line[:120],
        "email": email.group(0) if email else "",
        "years_experience": float(years.group(1)) if years else 0,
        "skills": [s for s in SKILL_VOCAB if s in lower],
        "summary": text[:300],
    }


def parse_resume(text: str) -> tuple[CandidateProfile, bool]:
    return complete_json(TASK, text, CandidateProfile, lambda: _mock(text))
