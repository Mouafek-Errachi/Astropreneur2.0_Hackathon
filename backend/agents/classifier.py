"""Email Classifier agent: category + urgency + one-line summary."""
from backend.agents.llm import complete_json
from backend.agents.schemas import EmailClassification

TASK = ("Classify this email received by the HR inbox. urgency=high only for time-critical items "
        "(interviews within 48h, deadlines, legal or safety issues). Phishing and spam are low urgency.")

KEYWORDS = [
    ("spam_phishing", ["verify your password", "click here", "lottery", "bank details", "account will be suspended"]),
    ("interview_scheduling", ["interview", "reschedule", "availability", "slot"]),
    ("application", ["application", "resume", "cv", "apply", "position"]),
    ("policy_question", ["policy", "leave", "remote", "expense", "benefit"]),
    ("employee_request", ["payslip", "certificate", "vacation", "day off"]),
    ("candidate_question", ["salary", "status of my", "any news"]),
]


def _mock(subject: str, body: str) -> dict:
    text = f"{subject} {body}".lower()
    category = next((cat for cat, words in KEYWORDS if any(w in text for w in words)), "other")
    urgent = "urgent" in text or category == "interview_scheduling"
    return {"category": category, "urgency": "low" if category == "spam_phishing" else "high" if urgent else "medium",
            "summary": (subject or body[:120])[:400], "needs_reply": category not in ("spam_phishing", "other")}


def classify(subject: str, body: str) -> tuple[EmailClassification, bool]:
    return complete_json(TASK, EmailClassification, lambda: _mock(subject, body),
                         untrusted=f"Subject: {subject}\n\n{body}", source="email")
