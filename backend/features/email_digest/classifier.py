from backend.core.llm import complete_json
from backend.schemas import EmailClassification

TASK = "Classify this HR inbox email: category, priority, a one-sentence summary, and whether it needs a reply."

KEYWORDS = [
    ("spam_phishing", ["verify your password", "click here", "lottery", "bank details"]),
    ("interview_scheduling", ["interview", "reschedule", "availability", "slot"]),
    ("application", ["application", "resume", "cv", "apply"]),
    ("policy_question", ["policy", "leave", "remote", "expense", "benefit"]),
    ("employee_request", ["payslip", "certificate", "vacation", "day off"]),
    ("candidate_question", ["position", "salary", "status of my"]),
]


def _mock(subject: str, body: str) -> dict:
    text = f"{subject} {body}".lower()
    category = next((cat for cat, words in KEYWORDS if any(w in text for w in words)), "other")
    priority = "high" if "urgent" in text or category == "interview_scheduling" else "medium"
    return {
        "category": category,
        "priority": "low" if category == "spam_phishing" else priority,
        "summary": (subject or body[:120])[:400],
        "needs_reply": category not in ("spam_phishing", "other"),
    }


def classify(subject: str, body: str) -> tuple[EmailClassification, bool]:
    return complete_json(TASK, f"Subject: {subject}\n\n{body}", EmailClassification, lambda: _mock(subject, body))
