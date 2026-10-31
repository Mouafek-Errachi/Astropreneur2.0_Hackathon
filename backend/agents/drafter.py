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
                   "Please choose a time that suits you using this link: {link}\n\nKind regards,\nHR Team"),
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


def template(kind: str, *, name: str, job: str, link: str = "{link}") -> DraftReply:
    subject, body = TEMPLATES[kind]
    fill = {"name": name or "there", "job": job, "link": link}
    return DraftReply(subject=subject.format(**fill), body=body.format(**fill))
