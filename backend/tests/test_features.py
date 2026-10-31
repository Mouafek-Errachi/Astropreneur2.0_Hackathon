from datetime import datetime, timedelta, timezone
from pathlib import Path

from slowapi.middleware import SlowAPIMiddleware
from sqlmodel import Session

from backend.db import engine
from backend.features.screening.intake import verified_email
from backend.features.screening.parser import has_term
from backend.features.screening.scorer import score
from backend.main import app
from backend.models import Candidate, utcnow
from backend.schemas import CandidateProfile

DATA = Path(__file__).resolve().parents[2] / "data"


def _job(client, headers, title):
    return client.post("/jobs", json={"title": title, "required_skills": ["python", "sql", "docker"],
                                      "nice_to_have": ["aws"], "min_years": 3}, headers=headers).json()


def _screened_candidate(client, headers, title, text):
    job = _job(client, headers, title)
    up = client.post(f"/jobs/{job['id']}/resumes", files=[("files", ("cv.txt", text.encode()))], headers=headers).json()[0]
    assert up["accepted"], up
    client.post(f"/jobs/{job['id']}/screen", headers=headers)
    return job, up["candidate_id"]


def _pending(client, headers, action_type):
    return [a for a in client.get("/approvals", params={"status": "pending"}, headers=headers).json() if a["type"] == action_type]


# ---------------------------------------------------------------- screening
def test_skill_matching_is_whole_word():
    assert has_term("Python 3.11", "python")
    assert has_term("C++ 17", "c++")
    assert not has_term("JavaScript", "java")


def test_score_has_job_related_justification():
    profile = CandidateProfile(name="X", skills=["Python 3", "SQL", "AWS"], years_experience=2)
    b = score(profile, ["python", "sql", "docker"], ["aws"], 3)
    assert b["required_matched"] == ["python", "sql"] and b["required_missing"] == ["docker"]
    assert "2/3 required" in b["justification"] and "missing docker" in b["justification"]
    assert "below the 3 years asked" in b["justification"]


def test_ranking_returns_justification(client, recruiter):
    job, cid = _screened_candidate(client, recruiter, "Justify Job", (DATA / "resumes/alice_martin.txt").read_text())
    cand = client.get(f"/candidates/{cid}", headers=recruiter).json()
    assert cand["justification"].startswith("Has 3/3 required skills")


def test_recipient_must_appear_in_resume():
    assert verified_email("Alice@Example.com", "Contact: alice@example.com") == "alice@example.com"
    assert verified_email("attacker@evil.test", "Contact: alice@example.com") == ""


def test_duplicate_resume_rejected(client, recruiter):
    job = _job(client, recruiter, "Dup Job")
    text = (DATA / "resumes/bob_nguyen.txt").read_bytes()
    files = [("files", ("bob.txt", text)), ("files", ("bob_copy.txt", text))]
    first, second = client.post(f"/jobs/{job['id']}/resumes", files=files, headers=recruiter).json()
    assert first["accepted"] and not second["accepted"] and "duplicate" in second["error"]


def test_upload_batch_limit(client, recruiter):
    job = _job(client, recruiter, "Batch Job")
    files = [("files", (f"f{i}.txt", b"x")) for i in range(121)]
    assert client.post(f"/jobs/{job['id']}/resumes", files=files, headers=recruiter).status_code == 413


# ---------------------------------------------------------------- replies and the human gate
def test_candidate_reply_needs_approved_decision_and_four_eyes(client, recruiter, admin):
    _, cid = _screened_candidate(client, recruiter, "Reply Job", "Carol Diaz\ncarol.diaz@example.com\n"
                                 "5 years experience. Skills: Python, SQL, Docker")
    assert client.post(f"/candidates/{cid}/draft-reply", json={"kind": "rejection"}, headers=recruiter).status_code == 409
    decision = client.post(f"/candidates/{cid}/decision", json={"decision": "rejected"}, headers=recruiter).json()
    # Four-eyes: the recruiter who proposed the decision cannot approve it.
    assert client.post(f"/approvals/{decision['action_id']}/approve", json={}, headers=recruiter).status_code == 403
    assert client.post(f"/approvals/{decision['action_id']}/approve", json={}, headers=admin).status_code == 200

    draft = client.post(f"/candidates/{cid}/draft-reply", json={"kind": "rejection"}, headers=recruiter).json()
    action = next(a for a in _pending(client, recruiter, "send_email") if a["id"] == draft["action_id"])
    assert action["payload"]["to"] == "carol.diaz@example.com"
    assert "Carol" in action["payload"]["body"]


def test_approved_reply_marks_email_handled(client, recruiter):
    e = client.post("/emails", json={"sender": "q@example.com", "subject": "Leave", "body": "How much annual leave do I get?"},
                    headers=recruiter).json()
    draft = client.post(f"/emails/{e['id']}/draft-reply", headers=recruiter).json()
    client.post(f"/approvals/{draft['action_id']}/approve", json={}, headers=recruiter)
    assert next(x for x in client.get("/emails", headers=recruiter).json() if x["id"] == e["id"])["handled"]


# ---------------------------------------------------------------- scheduling
def test_schedule_books_slot_and_sends_invitation(client, recruiter):
    _, cid = _screened_candidate(client, recruiter, "Schedule Job", "Dana Ruiz\ndana.ruiz@example.com\n"
                                 "4 years experience. Skills: Python, Docker")
    assert client.post("/scheduling/propose", json={"candidate_id": cid, "interviewer": "nobody@acme.test"},
                       headers=recruiter).status_code == 404
    s = client.post("/scheduling/propose", json={"candidate_id": cid, "interviewer": "tom.lead@acme.test"},
                    headers=recruiter).json()
    a = client.post(f"/approvals/{s['action_id']}/approve", json={"selected_slot": 1}, headers=recruiter).json()
    assert a["status"] == "executed" and "invitation queued" in a["result"]

    events = client.get("/scheduling/events", headers=recruiter).json()
    picked = datetime.fromisoformat(s["slots"][1]["start"])
    assert any(e["candidate_id"] == cid and datetime.fromisoformat(e["start"]) == picked for e in events)
    invite = client.get("/approvals/outbox", headers=recruiter).json()[-1]
    assert invite["to"] == "dana.ruiz@example.com" and "{slot}" not in invite["body"]
    assert client.get(f"/candidates/{cid}", headers=recruiter).json()["status"] == "interview"

    again = client.post("/scheduling/propose", json={"candidate_id": cid, "interviewer": "tom.lead@acme.test"},
                        headers=recruiter).json()
    assert s["slots"][1] not in again["slots"]  # a booked slot is never offered twice


# ---------------------------------------------------------------- follow-ups
def test_candidate_reminder_then_auto_close_when_they_answer(client, recruiter):
    _, cid = _screened_candidate(client, recruiter, "Reminder Job", "Evan Moss\nevan.moss@example.com\n"
                                 "6 years experience. Skills: Python, SQL")
    with Session(engine) as s:
        c = s.get(Candidate, cid)
        c.last_contact_at = utcnow() - timedelta(days=5)
        s.add(c)
        s.commit()

    assert client.post("/followups/scan", headers=recruiter).json()["candidates"] >= 1
    reminders = [a for a in _pending(client, recruiter, "send_reminder") if a["payload"]["candidate_id"] == cid]
    assert len(reminders) == 1 and reminders[0]["payload"]["to"] == "evan.moss@example.com"
    client.post("/followups/scan", headers=recruiter)
    assert len([a for a in _pending(client, recruiter, "send_reminder") if a["payload"]["candidate_id"] == cid]) == 1

    client.post("/emails", json={"sender": "Evan.Moss@example.com", "subject": "Re: interview", "body": "Sorry, yes!"},
                headers=recruiter)
    cancelled = client.get("/approvals", params={"status": "cancelled"}, headers=recruiter).json()
    assert reminders[0]["id"] in [a["id"] for a in cancelled]
    followup = next(f for f in client.get("/followups", headers=recruiter).json()
                    if f["ref_type"] == "candidate" and f["ref_id"] == cid)
    assert followup["status"] == "done"


# ---------------------------------------------------------------- inbox digest, Q&A, web security
def test_digest_window_and_grouping(client, recruiter):
    old = client.post("/emails", json={"sender": "old@acme.test", "subject": "Remote policy", "body": "Remote work policy?",
                                       "received_at": (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()},
                      headers=recruiter).json()
    client.post("/emails/classify", headers=recruiter)
    day = client.get("/emails/digest", params={"hours": 24}, headers=recruiter).json()
    week = client.get("/emails/digest", params={"hours": 168}, headers=recruiter).json()
    assert old["id"] not in [e["id"] for e in day["needs_reply"]]
    assert old["id"] in [e["id"] for e in week["needs_reply"]]
    assert "last 24h" in day["headline"] and set(day["by_priority"]) == {"high", "medium", "low"}
    assert all({"category", "total", "high", "medium", "low"} <= set(g) for g in week["groups"])


def test_qa_works_without_manual_reindex(client, recruiter):
    r = client.post("/qa/ask", json={"question": "How many days per week can I work remotely?"}, headers=recruiter).json()
    assert not r["escalate_to_hr"] and r["citations"][0]["source"] == "remote_work.md"


def test_rate_limit_middleware_installed():
    assert any(m.cls is SlowAPIMiddleware for m in app.user_middleware)


# ---------------------------------------------------------------- volume
def test_demo_data_at_volume(client, admin, recruiter):
    assert client.post("/admin/demo-data", json={"resumes": 5}, headers=recruiter).status_code == 403
    r = client.post("/admin/demo-data", json={"resumes": 80, "emails": 60}, headers=admin).json()
    assert r["resumes"]["accepted"] == 83 and r["resumes"]["flagged"] >= 6  # 80 generated + 3 samples
    assert r["emails"]["added"] == 65 and r["contacted_candidates"] == 3

    ranked = client.post(f"/jobs/{r['job_id']}/screen", headers=recruiter).json()
    assert len(ranked) == 83 and all(c["score"] is not None and c["justification"] for c in ranked)
    assert [c["rank"] for c in ranked] == list(range(1, 84))
    assert all(c["risk_level"] in ("medium", "high") for c in ranked if c["name"] == "Eve Hacker")
    assert len(client.post("/emails/classify", headers=recruiter).json()) >= 65
    assert client.post("/followups/scan", headers=recruiter).json()["candidates"] >= 3

    again = client.post("/admin/demo-data", json={"resumes": 80, "emails": 60}, headers=admin).json()
    assert again["resumes"]["accepted"] == 0 and again["emails"]["added"] == 0  # reload is idempotent
