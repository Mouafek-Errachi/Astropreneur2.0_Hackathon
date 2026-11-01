"""Which pages exist, who sees them and what each is for. The API still enforces every permission."""
from typing import NamedTuple

from api import can, n8n_configured


class Entry(NamedTuple):
    section: str
    path: str
    title: str
    icon: str  # Material Symbols name
    url: str
    scope: str | None  # the scope that opens the page; None = everyone
    blurb: str


PAGES = [
    Entry("Recruiting", "pages/2_Screening.py", "Screening", "groups", "screening", "jobs:read",
          "Rank CVs against the profile you describe, compare candidates, propose decisions."),
    Entry("Recruiting", "pages/1_Inbox.py", "Inbox", "inbox", "inbox", "emails:read",
          "Emails sorted by urgency, with reply drafts. Suspicious mail is quarantined."),
    Entry("Recruiting", "pages/3_Approvals.py", "Approvals", "fact_check", "approvals", "approvals:read",
          "Review, edit and approve everything the agents propose."),
    Entry("Recruiting", "pages/4_Scheduling.py", "Scheduling", "event", "scheduling", "scheduling:read",
          "Offer interview slots to a candidate."),
    Entry("Recruiting", "pages/5_Followups.py", "Follow-ups", "notifications_active", "follow-ups", "followups:read",
          "Candidates and emails still waiting for an answer."),
    Entry("Recruiting", "pages/11_Sources.py", "CV sources", "cloud_upload", "sources", "jobs:read",
          "Where CVs come from and how each one was checked."),
    Entry("People", "pages/10_Project_Staffing.py", "Project staffing", "account_tree", "staffing", "projects:read",
          "What each project needs, where the team falls short, and how to close the gap."),
    Entry("People", "pages/7_HR_Ops.py", "HR operations", "support_agent", "hr-ops", "hr:read",
          "Tickets, team broadcasts, recognition points and the interview co-pilot."),
    Entry("People", "pages/6_Ask_HR.py", "Ask HR", "forum", "ask-hr", "qa:ask",
          "Answers taken from the company policies, with sources."),
    Entry("Security", "pages/8_AI_Firewall.py", "AI Firewall", "shield", "firewall", "security:read",
          "Every blocked file, injection and quarantine, plus the tamper-proof audit log."),
]
MY_SKILLS = Entry("People", "pages/10_Project_Staffing.py", "My skills", "psychology", "my-skills", None,
                  "Declare your skills; a manager validates them.")
N8N = Entry("People", "pages/9_HR_Ops_via_n8n.py", "HR ops via n8n", "hub", "hr-ops-n8n", "hr:read",
            "The same HR features, served by the n8n workflow backend.")


def visible_pages(me: dict) -> list[Entry]:
    """The pages this role can open, in menu order."""
    shown = [e for e in PAGES if e.scope is None or can(e.scope)]
    if not can("projects:read") and me.get("employee_id"):  # employees only get their own skill profile
        shown.append(MY_SKILLS)
    if n8n_configured() and can(N8N.scope):
        shown.append(N8N)
    return shown
