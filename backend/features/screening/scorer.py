"""Deterministic scoring from extracted facts, so injected text cannot set the score directly.
The justification is built from the same facts: job-related criteria only, nothing about the person."""
from backend.features.screening.parser import has_term
from backend.schemas import CandidateProfile


def _matches(skill: str, profile_skills: list[str]) -> bool:
    return any(has_term(s, skill) for s in profile_skills)


def _fmt_years(y: float) -> str:
    return f"{y:g} year{'s' if y != 1 else ''}"


def justify(req_hit: list[str], required: list[str], nice_hit: list[str], nice: list[str],
            years: float, min_years: float) -> str:
    parts = []
    if required:
        parts.append(f"Has {len(req_hit)}/{len(required)} required skills" + (f" ({', '.join(req_hit)})" if req_hit else ""))
    missing = [s for s in required if s not in req_hit]
    if missing:
        parts.append(f"missing {', '.join(missing)}")
    if nice:
        parts.append(f"{len(nice_hit)}/{len(nice)} nice-to-have" + (f" ({', '.join(nice_hit)})" if nice_hit else ""))
    if min_years:
        verdict = "meets" if years >= min_years else "below"
        parts.append(f"{_fmt_years(years)} of experience, {verdict} the {_fmt_years(min_years)} asked")
    elif years:
        parts.append(f"{_fmt_years(years)} of experience")
    return ("; ".join(parts) + ".") if parts else "No job criteria defined."


def score(profile: CandidateProfile, required: list[str], nice: list[str], min_years: float) -> dict:
    req_hit = [s for s in required if _matches(s, profile.skills)]
    nice_hit = [s for s in nice if _matches(s, profile.skills)]
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
        "justification": justify(req_hit, required, nice_hit, nice, profile.years_experience, min_years),
    }
