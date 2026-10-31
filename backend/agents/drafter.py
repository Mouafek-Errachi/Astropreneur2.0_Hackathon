"""Reply Drafter agent (candidate-scoped: it only sees the email being answered and, when the
sender is a known candidate, that candidate's own application status)."""
from backend.agents.llm import complete_json
from backend.agents.schemas import DraftReply

TASK = ("Draft a short, professional reply from the HR team to the email. Do not promise anything about "
        "hiring decisions, salaries or dates that is not in the trusted context. Plain text, no links.")

TEMPLATES = {
    "rejection": ("Your application for {job}",
                  "Hello {name},\n\nThank you for your interest in the {job} position and for the time you took to apply. "
                  "After careful review we will not move forward with your application at this time.\n\n"
                  "We wish you the best in your search.\n\nKind regards,\nHR Team"),
    "invitation": ("Interview for {job}",
                   "Hello {name},\n\nThank you for applying to the {job} position. We would like to invite you to an interview. "
                   "{when}\n\nKind regards,\nHR Team"),
    "acknowledgement": ("We received your application for {job}",
                        "Hello {name},\n\nThank you for applying to the {job} position. We have received your application "
                        "and will get back to you after review.\n\nKind regards,\nHR Team"),
    "reminder": ("Following up on your application for {job}",
                 "Hello {name},\n\nWe wanted to give you a quick update: your application for {job} is still being "
                 "reviewed. We will contact you as soon as we have news.\n\nKind regards,\nHR Team"),
}


def draft_reply(sender: str, subject: str, body: str, candidate_context: str = "") -> tuple[DraftReply, bool]:
    def mock() -> dict:
        return {"subject": f"Re: {subject}"[:200],
                "body": "Hello,\n\nThank you for your message. Our HR team has received it and will get back to you "
                        "shortly.\n\nBest regards,\nHR Team"}

    return complete_json(TASK, DraftReply, mock, untrusted=f"From: {sender}\nSubject: {subject}\n\n{body}",
                         source="email", trusted_context=candidate_context)


INVITE_WHEN = "Please choose a time that suits you using this link: {link}"  # replaced by the executor


def template(kind: str, *, name: str, job: str) -> DraftReply:
    subject, body = TEMPLATES[kind]
    fill = {"name": name or "there", "job": job, "when": INVITE_WHEN}
    return DraftReply(subject=subject.format(**fill), body=body.format(**fill))


# ------------------------------------------------------------------ candidate emails (from the candidate's own file)
CANDIDATE_TASK = (
    "Write a short {kind} email from the HR team to this candidate about the {job} position. {guide} "
    "Use only the candidate file. Address the candidate by first name. Never mention scores, rankings, "
    "other candidates, or anything unrelated to the job (age, gender, origin, health, family, religion). "
    "Do not promise salaries, dates or outcomes. No links. Sign off with 'Best regards, HR Team'."
)
KIND_GUIDE = {
    "acknowledgement": "Confirm that we received the application and that the team is reviewing it.",
    "rejection": "Say respectfully that we will not move forward this time, thank them and wish them well. "
                 "Do not give reasons.",
}
REMINDER_TASK = (
    "Write a short, friendly reminder from the HR team to a candidate for the {job} position who has not answered "
    "our last message (sent {days} days ago, quoted). Ask them to reply when they can. Do not add new "
    "information, dates, links or promises. Sign off with 'Best regards, HR Team'."
)


def first_name(name: str) -> str:
    return (name or "").split()[0] if (name or "").split() else "there"


def draft_candidate_email(kind: str, job: str, profile: dict) -> tuple[DraftReply, bool]:
    """Drafted from the candidate's own file only (their parsed resume), never from other candidates."""
    import json

    own_file = {k: profile.get(k) for k in ("name", "current_title", "skills", "years_experience", "summary")}

    def mock() -> dict:
        t = template(kind, name=first_name(profile.get("name", "")), job=job)
        return {"subject": t.subject, "body": t.body}

    return complete_json(CANDIDATE_TASK.format(kind=kind, job=job, guide=KIND_GUIDE[kind]), DraftReply, mock,
                         untrusted=json.dumps(own_file, ensure_ascii=False), source="candidate_file")


def draft_reminder(name: str, job: str, days: int, last_subject: str, last_body: str) -> tuple[DraftReply, bool]:
    def mock() -> dict:
        about = f" about \"{last_subject}\"" if last_subject else ""
        return {
            "subject": f"Reminder: {last_subject}"[:200] if last_subject else f"Following up on your application for {job}",
            "body": f"Hello {first_name(name)},\n\nWe wrote to you {days} days ago{about} regarding the {job} position "
                    "and have not heard back yet. Could you reply when you have a moment?\n\nBest regards,\nHR Team",
        }

    return complete_json(REMINDER_TASK.format(job=job, days=days), DraftReply, mock,
                         untrusted=f"Subject: {last_subject}\n\n{last_body}", source="our_last_email")
