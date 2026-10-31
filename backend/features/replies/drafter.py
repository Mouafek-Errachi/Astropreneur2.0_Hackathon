from backend.core.llm import complete_json
from backend.schemas import DraftReply

TASK = (
    "Draft a short, professional reply from the HR team to the email below. "
    "Do not promise anything about hiring decisions, salaries or dates that is not stated."
)


def draft_reply(sender: str, subject: str, body: str) -> tuple[DraftReply, bool]:
    def mock() -> dict:
        return {
            "subject": f"Re: {subject}"[:200],
            "body": "Hello,\n\nThank you for your message. Our HR team has received it and will get back "
                    "to you shortly.\n\nBest regards,\nHR Team",
        }

    return complete_json(TASK, f"From: {sender}\nSubject: {subject}\n\n{body}", DraftReply, mock)
