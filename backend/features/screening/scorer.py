"""Deterministic scoring from extracted facts, so injected text cannot set the score directly.
The justification is built from the same facts: job-related criteria only, nothing about the person."""
from backend.features.screening.parser import has_term
from backend.schemas import CandidateProfile

DEFAULT_WEIGHTS = {"required": 60, "nice": 15, "experience": 25}


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


def features(profile: CandidateProfile, required: list[str], nice: list[str], min_years: float) -> dict:
    """Each criterion as a 0-1 fraction; None when the job does not define it. Shared with the weight learner."""
    req_hit = [s for s in required if _matches(s, profile.skills)]
    nice_hit = [s for s in nice if _matches(s, profile.skills)]
    return {
        "required": (len(req_hit) / len(required)) if required else None,
        "nice": (len(nice_hit) / len(nice)) if nice else None,
        "experience": min(profile.years_experience / min_years, 1.0) if min_years else None,
    }


def score(profile: CandidateProfile, required: list[str], nice: list[str], min_years: float,
          weights: dict | None = None) -> dict:
    req_hit = [s for s in required if _matches(s, profile.skills)]
    nice_hit = [s for s in nice if _matches(s, profile.skills)]
    # only the criteria the recruiter defined count; the weights are re-normalised over them
    parts = features(profile, required, nice, min_years)
    w = {**DEFAULT_WEIGHTS, **(weights or {})}
    active = {k: w[k] for k, pct in parts.items() if pct is not None and w[k] > 0}
    total_w = sum(active.values())
    total = round(100 * sum(w * parts[k] for k, w in active.items()) / total_w, 1) if total_w else 0.0
    return {
        "total": total,
        "required_matched": req_hit,
        "required_missing": [s for s in required if s not in req_hit],
        "nice_matched": nice_hit,
        "years_experience": profile.years_experience,
        "weights": {k: round(100 * v / total_w) for k, v in active.items()} if total_w else {},
        "justification": justify(req_hit, required, nice_hit, nice, profile.years_experience, min_years),
    }
