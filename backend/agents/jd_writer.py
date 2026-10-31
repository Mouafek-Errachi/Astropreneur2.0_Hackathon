"""Job Description writer agent: a market-ready job description for ONE skill gap, from trusted facts only
(project summary, the gap skill and level, related skills the project uses, team tone). It never sees employee
data. Its output is checked in code (security/validator.check_job_description) before it can be proposed."""
import json

from backend.agents.llm import complete_json
from backend.agents.schemas import JobDescriptionDraft

LEVEL_TITLE = {1: "Junior", 2: "", 3: "Senior", 4: "Lead"}
LEVEL_YEARS = {1: 0, 2: 2, 3: 4, 4: 7}

TASK = (
    "Write a job description to hire for a skill gap on an internal IT project. Use only the trusted facts. "
    "`required_skills` must be exactly the gap skill; `nice_to_have` only skills from the project's other skills. "
    "Do not mention salary, benefits, bonuses, dates, links or any current employee. Use inclusive, gender-neutral "
    "wording ('you', 'they'); nothing about age, gender, origin, family, health, nationality or being a native "
    "speaker. 4-6 responsibilities. `body`: the full posting in plain text, 120-250 words."
)


def facts(project_name: str, project_summary: str, skill: str, level: int, other_skills: list[str],
          team_name: str, tone: str) -> dict:
    return {"project": project_name, "project_summary": project_summary, "gap_skill": skill, "level": level,
            "seniority": LEVEL_TITLE[level] or "Mid-level", "min_years": LEVEL_YEARS[level],
            "project_other_skills": other_skills, "team": team_name, "tone_guide": tone}


def template(f: dict) -> dict:
    """Deterministic, validator-clean posting (the mock, and the fallback when the AI's draft fails the checks)."""
    skill, seniority = f["gap_skill"], LEVEL_TITLE[f["level"]]
    title = f"{seniority + ' ' if seniority else ''}{skill.title()} Engineer".strip()
    nice = f["project_other_skills"][:4]
    resp = [f"Design, build and maintain the {skill} parts of the {f['project']} project",
            "Review code and share knowledge with the team",
            "Write tests and documentation for what you deliver",
            "Work with product and operations to ship reliable releases"]
    body = (f"{title} - {f['project']}\n\n"
            f"We are looking for an engineer with strong {skill} skills to join the {f['team']} team on the "
            f"{f['project']} project. {f['project_summary']}\n\n"
            "What you will do:\n" + "\n".join(f"- {r}" for r in resp) + "\n\n"
            f"What we are looking for:\n- {f['min_years']}+ years of hands-on experience with {skill}\n"
            + (f"- Nice to have: {', '.join(nice)}\n" if nice else "")
            + "- Clear written communication and a collaborative way of working\n\n"
            "We welcome applications from everyone and assess candidates on job-related skills only.")
    return {"title": title[:100], "summary": f"{title} for the {f['project']} project."[:400], "responsibilities": resp,
            "required_skills": [skill], "nice_to_have": nice, "min_years": f["min_years"], "body": body[:4000]}


def write_job_description(f: dict) -> tuple[JobDescriptionDraft, bool]:
    return complete_json(TASK, JobDescriptionDraft, lambda: template(f), trusted_context=json.dumps(f, ensure_ascii=False))
