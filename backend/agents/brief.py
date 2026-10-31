"""Search-brief agent: the recruiter's request in their own words ("python and sql, linux is a plus, 3 years
minimum") -> ranking criteria (LLM, with a rule-based mock). Only skills and experience are kept: the score
never uses anything else. Here the recruiter's text is the untrusted input."""
import re

from backend.agents.llm import complete_json
from backend.agents.ranker import SKILL_VOCAB, term_re
from backend.agents.schemas import SearchCriteria

TASK = (
    "A recruiter describes, in their own words and in any language, who they are looking for. Turn the request "
    "into ranking criteria. required_skills: the skills a candidate must have. nice_to_have: the skills the "
    "recruiter calls a plus, preferred, better or nice to have, ideally, or a bonus. min_years: the minimum years of "
    "experience asked, 0 if none. Write each skill as a short lowercase name, and when it is one of these skills use "
    "exactly this spelling: {known}. Keep only skills and years of experience: leave out anything about the person "
    "(age, gender, origin, family, health, photo), and add no skill the recruiter did not ask for."
)

COMMON_WORDS = {"it", "go", "is", "as", "on", "in", "to", "or", "and", "the", "a", "i"}  # never read as skills
# (pattern, state for the skills that follow, state given to the skills already read in the clause)
MARKERS = [
    (r"\b(?:(?:better|nice|good|great) to (?:have|know)|ideally|preferably|optionally|bonus|a plus if|as a plus)\b",
     "nice", None),
    (r"\b(?:(?:is|are|would be) (?:a )?(?:plus|bonus|nice to have|appreciated|welcome)|preferred|optional)\b",
     None, "nice"),
    (r"\b(?:must|needs?|requires?|looking for|searching for)\b", "required", None),
    (r"\b(?:(?:is|are) )?(?:required|needed|mandatory|essential|a must)\b", "required", "required"),
    (r"[.;!?\n]", "required", None),  # a new sentence starts required again
    (r",", None, None),
]
MARKERS = [(re.compile(p), after, before) for p, after, before in MARKERS]
YEARS = re.compile(r"(\d{1,2}(?:[.,]\d)?)\s*\+?\s*(?:years?|yrs?|ans|années)\b")


def _mock(text: str, known: list[str]) -> dict:
    """Reads left to right: skills are required until a 'nice to have' word, 'X is a plus' makes the skills of its
    clause optional, 'X is required' makes them required."""
    low = text.lower()
    events = [(m.start(), m.end(), s, None) for s in dict.fromkeys(known + SKILL_VOCAB) for m in term_re(s).finditer(low)]
    events += [(m.start(), m.end(), None, (after, before)) for rx, after, before in MARKERS for m in rx.finditer(low)]
    found, state, clause, skill_end, marker_end = {}, "required", [], -1, -1
    for start, end, skill, rule in sorted(events, key=lambda e: (e[0], -e[1])):
        if skill and start >= skill_end:  # 'machine learning' wins over 'learning'
            found[skill], skill_end = state, end
            clause.append(skill)
        elif rule and start >= marker_end:  # 'is nice to have' wins over 'nice to have'
            after, before = rule
            found.update(dict.fromkeys(clause if before else [], before))
            state, clause, marker_end = after or state, [], end
    years = YEARS.search(low)
    return {"required_skills": [s for s, k in found.items() if k == "required"],
            "nice_to_have": [s for s, k in found.items() if k == "nice"],
            "min_years": min(float(years.group(1).replace(",", ".")), 40) if years else 0}


def parse_brief(text: str, known: list[str]) -> tuple[SearchCriteria, bool]:
    """known: the job's current skills and the skills found in its resumes, so the same skill keeps one spelling."""
    known = [k.lower() for k in known if k.lower() not in COMMON_WORDS]
    crit, fallback = complete_json(TASK.format(known=", ".join(dict.fromkeys(known + SKILL_VOCAB))), SearchCriteria,
                                   lambda: _mock(text, known), untrusted=text, source="recruiter_request")
    req = [s.lower() for s in crit.required_skills]
    nice = [s.lower() for s in crit.nice_to_have if s.lower() not in req]
    return SearchCriteria(required_skills=req, nice_to_have=nice, min_years=crit.min_years), fallback
