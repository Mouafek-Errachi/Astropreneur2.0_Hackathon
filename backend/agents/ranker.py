"""Resume Ranker + Justifier agent.

The LLM only EXTRACTS facts (profile). The score is computed deterministically from those facts against the
job criteria with editable weights, so text in a resume cannot set it. The justification is built from the
same facts: job-related criteria only, nothing about the person.
"""
import re

from backend.agents.llm import complete_json
from backend.agents.schemas import CandidateProfile

SKILL_VOCAB = [
    "python", "java", "javascript", "typescript", "sql", "react", "fastapi", "django", "docker",
    "kubernetes", "aws", "azure", "gcp", "machine learning", "pandas", "excel", "recruiting",
    "communication", "leadership", "project management", "git", "linux", "c++", "figma", "n8n",
]
DEFAULT_WEIGHTS = {"required": 60, "nice": 15, "experience": 25}

EXTRACT_TASK = (
    "Extract the candidate profile from the resume. Use only facts stated in the resume; leave missing fields empty. "
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


def _mock_profile(text: str, job_skills: list[str]) -> dict:
    email = re.search(r"[\w.+-]+@[\w-]+\.[\w.]+", text)
    years = re.search(r"(\d{1,2})\+?\s*years?\b", text.lower())
    first_line = next((l.strip() for l in text.splitlines() if l.strip()), "")
    vocab = dict.fromkeys([s.lower() for s in job_skills] + SKILL_VOCAB)
    return {"name": first_line[:120], "email": email.group(0) if email else "",
            "years_experience": float(years.group(1)) if years else 0,
            "skills": _skills_line(text) + [s for s in vocab if has_term(text, s)], "summary": text[:300]}


def extract_profile(text: str, job_skills: list[str] | None = None) -> tuple[CandidateProfile, bool]:
    job_skills = job_skills or []
    task = EXTRACT_TASK.format(skills=", ".join(job_skills) or "none given")
    return complete_json(task, CandidateProfile, lambda: _mock_profile(text, job_skills), untrusted=text, source="resume")


# ------------------------------------------------------------------ deterministic scoring
def _matches(skill: str, profile_skills: list[str]) -> bool:
    return any(has_term(s, skill) for s in profile_skills)


def _fmt_years(y: float) -> str:
    return f"{y:g} year{'s' if y != 1 else ''}"


def justify(req_hit: list[str], required: list[str], nice_hit: list[str], nice: list[str],
            years: float, min_years: float) -> str:
    parts = []
    if required:
        parts.append(f"Has {len(req_hit)}/{len(required)} required skills" + (f" ({', '.join(req_hit)})" if req_hit else ""))
    if missing := [s for s in required if s not in req_hit]:
        parts.append(f"missing {', '.join(missing)}")
    if nice:
        parts.append(f"{len(nice_hit)}/{len(nice)} nice-to-have" + (f" ({', '.join(nice_hit)})" if nice_hit else ""))
    if min_years:
        verdict = "meets" if years >= min_years else "below"
        parts.append(f"{_fmt_years(years)} of experience, {verdict} the {_fmt_years(min_years)} asked")
    elif years:
        parts.append(f"{_fmt_years(years)} of experience")
    return ("; ".join(parts) + ".") if parts else "No job criteria defined."


def features(profile: CandidateProfile, required: list[str], nice: list[str], min_years: float) -> dict:
    """Each criterion as a 0-1 fraction; None when the job does not define it. Shared with the weight learner."""
    req_hit = [s for s in required if _matches(s, profile.skills)]
    nice_hit = [s for s in nice if _matches(s, profile.skills)]
    return {
        "required": (len(req_hit) / len(required)) if required else None,
        "nice": (len(nice_hit) / len(nice)) if nice else None,
        "experience": min(profile.years_experience / min_years, 1.0) if min_years else None,
    }


def score(profile: CandidateProfile, criteria: dict) -> dict:
    required = [s.lower() for s in criteria.get("required_skills", [])]
    nice = [s.lower() for s in criteria.get("nice_to_have", [])]
    min_years = float(criteria.get("min_years", 0) or 0)
    req_hit = [s for s in required if _matches(s, profile.skills)]
    nice_hit = [s for s in nice if _matches(s, profile.skills)]
    # only the criteria the recruiter defined count; the weights are re-normalised over them
    parts = features(profile, required, nice, min_years)
    w = {**DEFAULT_WEIGHTS, **(criteria.get("weights") or {})}
    active = {k: w[k] for k, pct in parts.items() if pct is not None and w[k] > 0}
    total_w = sum(active.values())
    total = round(100 * sum(wk * parts[k] for k, wk in active.items()) / total_w, 1) if total_w else 0.0
    return {
        "total": total,
        "required_matched": req_hit, "required_missing": [s for s in required if s not in req_hit],
        "nice_matched": nice_hit, "years_experience": profile.years_experience, "min_years": min_years,
        "weights": {k: round(100 * v / total_w) for k, v in active.items()} if total_w else {},
        "justification": justify(req_hit, required, nice_hit, nice, profile.years_experience, min_years),
    }


def strengths_gaps(breakdown: dict) -> dict:
    """Bullet points for the dashboard, from the same facts as the score."""
    strengths = [f"Has required skill: {s}" for s in breakdown["required_matched"][:4]]
    strengths += [f"Nice to have: {s}" for s in breakdown["nice_matched"][:2]]
    gaps = [f"Missing required skill: {s}" for s in breakdown["required_missing"][:4]]
    if breakdown["min_years"] and breakdown["years_experience"] < breakdown["min_years"]:
        gaps.append(f"{_fmt_years(breakdown['years_experience'])} of experience "
                    f"(job asks {_fmt_years(breakdown['min_years'])})")
    return {"strengths": strengths, "gaps": gaps[:6], "summary": breakdown["justification"]}


def bucket(value: float | None) -> str:
    if value is None:
        return "unscreened"
    return "strong" if value >= 70 else "possible" if value >= 45 else "weak"
