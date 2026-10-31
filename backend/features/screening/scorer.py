"""Deterministic scoring from extracted facts, so injected text cannot set the score directly."""
from backend.schemas import CandidateProfile


def score(profile: CandidateProfile, required: list[str], nice: list[str], min_years: float) -> dict:
    skills = {s.lower() for s in profile.skills}
    req_hit = [s for s in required if s.lower() in skills]
    nice_hit = [s for s in nice if s.lower() in skills]
    req_pct = len(req_hit) / len(required) if required else 1.0
    nice_pct = len(nice_hit) / len(nice) if nice else 0.0
    exp_pct = min(profile.years_experience / min_years, 1.0) if min_years else 1.0
    total = round(100 * (0.6 * req_pct + 0.15 * nice_pct + 0.25 * exp_pct), 1)
    return {
        "total": total,
        "required_matched": req_hit,
        "required_missing": [s for s in required if s not in req_hit],
        "nice_matched": nice_hit,
        "years_experience": profile.years_experience,
    }
