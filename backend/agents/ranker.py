"""Resume Ranker + Justifier agent.

The LLM only EXTRACTS facts (profile) and WRITES a justification. The score itself is computed
deterministically from those facts against the job criteria, so text in a resume cannot set it.
"""
import re

from backend.agents.llm import complete_json
from backend.agents.schemas import CandidateProfile, FitJustification

SKILL_VOCAB = [
    "python", "java", "javascript", "typescript", "sql", "react", "fastapi", "django", "docker",
    "kubernetes", "aws", "azure", "gcp", "machine learning", "pandas", "excel", "recruiting",
    "communication", "leadership", "project management", "git", "linux", "c++", "figma", "n8n",
]

EXTRACT_TASK = "Extract the candidate profile from the resume. Use only facts stated in the resume; leave missing fields empty."
JUSTIFY_TASK = ("Explain in 2-4 short bullet points why this candidate does or does not match the job criteria, "
                "based only on the extracted profile and the score breakdown. Do not change the score.")


def _mock_profile(text: str) -> dict:
    lower = text.lower()
    email = re.search(r"[\w.+-]+@[\w-]+\.[\w.]+", text)
    years = re.search(r"(\d{1,2})\+?\s*years", lower)
    first_line = next((l.strip() for l in text.splitlines() if l.strip()), "")
    return {"name": first_line[:120], "email": email.group(0) if email else "",
            "years_experience": float(years.group(1)) if years else 0,
            "skills": [s for s in SKILL_VOCAB if re.search(rf"(?<![a-z]){re.escape(s)}(?![a-z])", lower)],
            "summary": text[:300]}


def extract_profile(text: str) -> tuple[CandidateProfile, bool]:
    return complete_json(EXTRACT_TASK, CandidateProfile, lambda: _mock_profile(text), untrusted=text, source="resume")


def score(profile: CandidateProfile, criteria: dict) -> dict:
    required = [s.lower() for s in criteria.get("required_skills", [])]
    nice = [s.lower() for s in criteria.get("nice_to_have", [])]
    min_years = float(criteria.get("min_years", 0) or 0)
    skills = {s.lower() for s in profile.skills}
    req_hit = [s for s in required if s in skills]
    nice_hit = [s for s in nice if s in skills]
    req_pct = len(req_hit) / len(required) if required else 1.0
    nice_pct = len(nice_hit) / len(nice) if nice else 0.0
    exp_pct = min(profile.years_experience / min_years, 1.0) if min_years else 1.0
    return {
        "total": round(100 * (0.6 * req_pct + 0.15 * nice_pct + 0.25 * exp_pct), 1),
        "weights": {"required_skills": 0.6, "nice_to_have": 0.15, "experience": 0.25},
        "required_matched": req_hit, "required_missing": [s for s in required if s not in req_hit],
        "nice_matched": nice_hit, "years_experience": profile.years_experience, "min_years": min_years,
    }


def justify(profile: CandidateProfile, breakdown: dict) -> tuple[FitJustification, bool]:
    def mock() -> dict:
        strengths = [f"Has required skill: {s}" for s in breakdown["required_matched"][:4]]
        gaps = [f"Missing required skill: {s}" for s in breakdown["required_missing"][:4]]
        if breakdown["years_experience"] < breakdown["min_years"]:
            gaps.append(f"{breakdown['years_experience']:g} years experience (job asks {breakdown['min_years']:g})")
        return {"strengths": strengths, "gaps": gaps[:6], "summary": f"Match score {breakdown['total']}/100."}

    context = f"PROFILE: {profile.model_dump_json()}\nSCORE BREAKDOWN: {breakdown}"
    return complete_json(JUSTIFY_TASK, FitJustification, mock, trusted_context=context)


def bucket(value: float | None) -> str:
    if value is None:
        return "unscreened"
    return "strong" if value >= 70 else "possible" if value >= 45 else "weak"
