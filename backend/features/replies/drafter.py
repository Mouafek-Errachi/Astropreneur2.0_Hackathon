import json

from backend.core.llm import complete_json
from backend.schemas import DraftReply

TASK = (
    "Draft a short, professional reply from the HR team to the email below. "
    "Do not promise anything about hiring decisions, salaries or dates that is not stated."
)

CANDIDATE_TASK = (
    "Write a short {kind} email from the HR team to this candidate about the {job} position. {guide} "
    "Use only the candidate file below. Address the candidate by first name. Never mention scores, rankings, "
    "other candidates, or anything unrelated to the job (age, gender, origin, health, family, religion). "
    "Do not promise salaries, dates or outcomes. No links. Sign off with 'Best regards, HR Team'."
)

KIND_GUIDE = {
    "acknowledgement": "Confirm that we received the application and that the team is reviewing it.",
    "invitation": "Say that we would like to meet them for an interview and that a recruiter will propose times.",
    "rejection": "Say respectfully that we will not move forward this time, thank them and wish them well. "
                 "Do not give reasons.",
}

CANDIDATE_MOCK = {
    "acknowledgement": ("Your application for {job}",
                        "Hello {first},\n\nThank you for applying for the {job} position. We have received your "
                        "application and our team is reviewing it. We will get back to you as soon as possible.\n\n"
                        "Best regards,\nHR Team"),
    "invitation": ("Interview for {job}",
                   "Hello {first},\n\nThank you for your application for the {job} position. We would like to meet "
                   "you for an interview. A recruiter will contact you shortly with possible times.\n\n"
                   "Best regards,\nHR Team"),
    "rejection": ("Your application for {job}",
                  "Hello {first},\n\nThank you for your interest in the {job} position and for the time you spent "
                  "on your application. After careful consideration, we will not move forward with your "
                  "application this time. We wish you every success in your search.\n\nBest regards,\nHR Team"),
}

REMINDER_TASK = (
    "Write a short, friendly reminder from the HR team to a candidate for the {job} position who has not answered "
    "our last message (sent {days} days ago, quoted below). Ask them to reply when they can. Do not add new "
    "information, dates, links or promises. Sign off with 'Best regards, HR Team'."
)


def draft_reply(sender: str, subject: str, body: str) -> tuple[DraftReply, bool]:
    def mock() -> dict:
        return {
            "subject": f"Re: {subject}"[:200],
            "body": "Hello,\n\nThank you for your message. Our HR team has received it and will get back "
                    "to you shortly.\n\nBest regards,\nHR Team",
        }

    return complete_json(TASK, f"From: {sender}\nSubject: {subject}\n\n{body}", DraftReply, mock)


def first_name(name: str) -> str:
    return (name or "").split()[0] if (name or "").split() else "there"


def draft_candidate_email(kind: str, job: str, profile: dict) -> tuple[DraftReply, bool]:
    """Drafted from the candidate's own file only (their parsed resume), never from other candidates."""
    own_file = {k: profile.get(k) for k in ("name", "current_title", "skills", "years_experience", "summary")}

    def mock() -> dict:
        subject, body = CANDIDATE_MOCK[kind]
        return {"subject": subject.format(job=job), "body": body.format(job=job, first=first_name(profile.get("name")))}

    task = CANDIDATE_TASK.format(kind=kind, job=job, guide=KIND_GUIDE[kind])
    return complete_json(task, json.dumps(own_file, ensure_ascii=False), DraftReply, mock)


def draft_reminder(name: str, job: str, days: int, last_subject: str, last_body: str) -> tuple[DraftReply, bool]:
    def mock() -> dict:
        about = f" about \"{last_subject}\"" if last_subject else ""
        return {
            "subject": f"Reminder: {last_subject}"[:200] if last_subject else f"Following up on your application for {job}",
            "body": f"Hello {first_name(name)},\n\nWe wrote to you {days} days ago{about} regarding the {job} position "
                    "and have not heard back yet. Could you reply when you have a moment?\n\nBest regards,\nHR Team",
        }

    task = REMINDER_TASK.format(job=job, days=days)
    return complete_json(task, f"Subject: {last_subject}\n\n{last_body}", DraftReply, mock)
