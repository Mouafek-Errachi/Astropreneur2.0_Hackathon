"""Bias Auditor agent: counterfactual check. Re-scores the resume with identity signals masked
(name, email, phone, photos/links, gendered words, age/dates of birth, nationality). If the score
moves, the ranking depended on something that is not a job-related competency."""
import re

from backend.agents import ranker

MASKS = [
    (re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+"), "[EMAIL]"),
    (re.compile(r"\+?\d[\d\s().-]{7,}\d"), "[PHONE]"),
    (re.compile(r"\b(he|she|him|her|his|hers|mr|mrs|ms|miss)\b", re.I), "[PRONOUN]"),
    (re.compile(r"\b(male|female|married|single|divorced|pregnan\w*|children|kids)\b", re.I), "[PERSONAL]"),
    (re.compile(r"\b(born|date of birth|age|aged)\b[^\n]{0,30}", re.I), "[AGE]"),
    (re.compile(r"\b(nationality|citizen(ship)?|religion|ethnicity)\b[^\n]{0,30}", re.I), "[PROTECTED]"),
    (re.compile(r"https?://\S+"), "[LINK]"),
]


def mask_identity(text: str) -> tuple[str, list[str]]:
    lines = text.splitlines()
    applied = []
    if lines:
        lines[0] = "[CANDIDATE NAME]"  # resumes start with the name
        applied.append("name")
    masked = "\n".join(lines)
    for pattern, token in MASKS:
        if pattern.search(masked):
            applied.append(token.strip("[]").lower())
            masked = pattern.sub(token, masked)
    return masked, applied


def audit(resume_text: str, criteria: dict, original_score: float) -> dict:
    masked, applied = mask_identity(resume_text)
    profile, _ = ranker.extract_profile(masked, criteria.get("required_skills", []) + criteria.get("nice_to_have", []))
    cf = ranker.score(profile, criteria)["total"]
    return {"original_score": original_score, "counterfactual_score": cf,
            "rank_delta": round(cf - original_score, 1), "counterfactuals": {"masked": applied}}
