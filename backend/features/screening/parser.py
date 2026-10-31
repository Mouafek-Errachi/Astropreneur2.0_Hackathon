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
    "If something is missing, leave it empty. "
    "The job requires these skills: {skills}. When the resume shows one of them, including a synonym, a version "
    "or a specific technology of that kind (for example PostgreSQL or MySQL for SQL), list it in `skills` with "
    "exactly the spelling given here. List other skills as written in the resume."
)


def term_re(term: str) -> re.Pattern:
    """Whole-word pattern for a lowercase text: 'java' does not match 'javascript', 'c++' matches 'c++ 17'."""
    return re.compile(rf"(?<![\w+#]){re.escape(term.lower())}(?![\w+#])")


def has_term(text: str, term: str) -> bool:
    return term_re(term).search(text.lower()) is not None


def _skills_line(text: str) -> list[str]:
    m = re.search(r"^\s*(?:technical\s+)?skills?\s*:\s*(.+)$", text, re.I | re.M)
    return [s.strip() for s in re.split(r"[,;|•]", m.group(1)) if s.strip()] if m else []


def _mock(text: str, job_skills: list[str]) -> dict:
    email = re.search(r"[\w.+-]+@[\w-]+\.[\w.]+", text)
    years = re.search(r"(\d{1,2})\+?\s*years?\b", text.lower())
    first_line = next((l.strip() for l in text.splitlines() if l.strip()), "")
    vocab = dict.fromkeys([s.lower() for s in job_skills] + SKILL_VOCAB)
    return {
        "name": first_line[:120],
        "email": email.group(0) if email else "",
        "years_experience": float(years.group(1)) if years else 0,
        "skills": _skills_line(text) + [s for s in vocab if has_term(text, s)],
        "summary": text[:300],
    }


def parse_resume(text: str, job_skills: list[str] | None = None) -> tuple[CandidateProfile, bool]:
    job_skills = job_skills or []
    task = TASK.format(skills=", ".join(job_skills) or "none given")
    return complete_json(task, text, CandidateProfile, lambda: _mock(text, job_skills))
