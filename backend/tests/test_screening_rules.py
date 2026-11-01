"""Screening, replies, scheduling, follow-ups and digest rules (ported from Ghassen's test_features.py)."""
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from slowapi.middleware import SlowAPIMiddleware
from sqlmodel import Session

from backend.agents.ranker import extract_profile, has_term, score
from backend.agents.schemas import CandidateProfile
from backend.data.db import engine
from backend.data.models import Candidate, utcnow
from backend.main import app
from backend.orchestration.screening import verified_email

DATA = Path(__file__).resolve().parents[2] / "data"
CRIT = {"required_skills": ["python", "sql", "docker"], "nice_to_have": ["aws"], "min_years": 3}


def _job(client, headers, title):
    return client.post("/jobs", json={"title": title, **CRIT}, headers=headers).json()


def _screened(client, headers, title, text):
    """Returns (job, application row from the ranking)."""
    job = _job(client, headers, title)
    up = client.post(f"/jobs/{job['id']}/resumes", files=[("files", ("cv.txt", text.encode()))], headers=headers).json()[0]
    assert up["accepted"], up
    ranking = client.post(f"/jobs/{job['id']}/screen", headers=headers).json()["ranking"]
    return job, next(r for r in ranking if r["candidate_id"] == up["candidate_id"])


def _pending(client, headers, action_type):
    return [a for a in client.get("/approvals", params={"status": "pending"}, headers=headers).json() if a["type"] == action_type]


# ---------------------------------------------------------------- screening
def test_skill_matching_is_whole_word():
    assert has_term("Python 3.11", "python")
    assert has_term("C++ 17", "c++")
    assert not has_term("JavaScript", "java")


def test_score_has_job_related_justification():
    profile = CandidateProfile(name="X", skills=["Python 3", "SQL", "AWS"], years_experience=2)
    b = score(profile, CRIT)
    assert b["required_matched"] == ["python", "sql"] and b["required_missing"] == ["docker"]
    assert "2/3 required" in b["justification"] and "missing docker" in b["justification"]
    assert "below the 3 years asked" in b["justification"]


def test_ranking_returns_justification(client, recruiter):
    _, row = _screened(client, recruiter, "Justify Job", (DATA / "resumes/alice_martin.txt").read_text())
    assert row["score_breakdown"]["justification"].startswith("Has 3/3 required skills")


def test_recipient_must_appear_in_resume():
    assert verified_email("Alice@Example.com", "Contact: alice@example.com") == "alice@example.com"
    assert verified_email("attacker@evil.test", "Contact: alice@example.com") == ""


def test_weights_only_count_defined_criteria():
    profile = CandidateProfile(name="X", skills=["Python"], years_experience=10)
    assert score(profile, {"required_skills": ["python"]})["total"] == 100.0  # experience and nice not defined
    only_exp = score(profile, {"required_skills": ["go"], "min_years": 5,
                               "weights": {"required": 0, "nice": 0, "experience": 100}})
    assert only_exp["total"] == 100.0 and only_exp["weights"] == {"experience": 100}
    assert score(profile, {})["total"] == 0.0


def test_mock_parser_reads_the_skills_line():
    profile, _ = extract_profile("Zoe Kim\nSkills: Rust, Terraform; Kafka\n4 years of experience")
    assert {"Rust", "Terraform", "Kafka"} <= set(profile.skills) and profile.years_experience == 4


def test_criteria_change_reranks_without_llm(client, recruiter):
    job = _job(client, recruiter, "Criteria Job")
    files = [("files", (p.name, p.read_bytes())) for p in sorted((DATA / "resumes").glob("*.txt"))]
    client.post(f"/jobs/{job['id']}/resumes", files=files, headers=recruiter)
    first = client.post(f"/jobs/{job['id']}/screen", headers=recruiter).json()["ranking"]
    assert first[0]["name"] == "Alice Martin"

    frontend = {"required_skills": ["JavaScript", "React", "react "], "nice_to_have": [], "min_years": 0,
                "weights": {"required": 100, "nice": 0, "experience": 0}}
    ranked = client.put(f"/jobs/{job['id']}/criteria", json=frontend, headers=recruiter).json()["ranking"]
    assert ranked[0]["name"] == "Bob Nguyen" and ranked[0]["score"] == 100.0
    assert ranked[0]["score_breakdown"]["justification"].startswith("Has 2/2 required skills")
    saved = next(j for j in client.get("/jobs", headers=recruiter).json() if j["id"] == job["id"])
    assert saved["criteria"]["required_skills"] == ["javascript", "react"]
    assert saved["criteria"]["weights"]["required"] == 100

    reread = client.put(f"/jobs/{job['id']}/criteria", params={"reparse": "true"}, json=frontend,
                        headers=recruiter).json()["ranking"]
    assert [c["application_id"] for c in reread] == [c["application_id"] for c in ranked]
    bad = {**frontend, "weights": {"required": 150, "nice": 0, "experience": 0}}
    assert client.put(f"/jobs/{job['id']}/criteria", json=bad, headers=recruiter).status_code == 422


def test_read_only_key_cannot_change_criteria(client, reader, recruiter):
    job = _job(client, recruiter, "Read-only Job")
    assert client.put(f"/jobs/{job['id']}/criteria", json={"required_skills": ["x"]}, headers=reader).status_code == 403


def test_duplicate_resume_rejected(client, recruiter):
    job = _job(client, recruiter, "Dup Job")
    text = (DATA / "resumes/bob_nguyen.txt").read_bytes()
    files = [("files", ("bob.txt", text)), ("files", ("bob_copy.txt", text)), ("files", ("bob2.txt", text + b"\n\n"))]
    first, second, third = client.post(f"/jobs/{job['id']}/resumes", files=files, headers=recruiter).json()
    assert first["accepted"] and not second["accepted"] and "duplicate" in second["error"]
    assert not third["accepted"] and "same content" in third["error"]  # different bytes, same text


def test_upload_batch_limit(client, recruiter):
    job = _job(client, recruiter, "Batch Job")
    files = [("files", (f"f{i}.txt", b"x")) for i in range(121)]
    assert client.post(f"/jobs/{job['id']}/resumes", files=files, headers=recruiter).status_code == 413


# ---------------------------------------------------------------- replies and the human gate
def test_candidate_reply_needs_approved_decision_and_four_eyes(client, recruiter, admin):
    _, row = _screened(client, recruiter, "Reply Job", "Carol Diaz\ncarol.diaz@example.com\n"
                       "5 years experience. Skills: Python, SQL, Docker")
    app_id = row["application_id"]
    assert client.post(f"/applications/{app_id}/email", json={"kind": "rejection"}, headers=recruiter).status_code == 409
    decision = client.post(f"/applications/{app_id}/decision", json={"decision": "rejected"}, headers=recruiter).json()
    # Four-eyes: the recruiter who proposed the decision cannot approve it.
    assert client.post(f"/approvals/{decision['action_id']}/approve", json={}, headers=recruiter).status_code == 403
    assert client.post(f"/approvals/{decision['action_id']}/approve", json={}, headers=admin).status_code == 200

    draft = client.post(f"/applications/{app_id}/email", json={"kind": "rejection"}, headers=recruiter).json()
    action = next(a for a in _pending(client, recruiter, "send_email") if a["id"] == draft["action_id"])
    assert action["payload"]["to"] == "carol.diaz@example.com"
    assert "Carol" in action["payload"]["body"]


def test_approved_reply_marks_email_handled(client, recruiter):
    e = client.post("/emails", json={"sender": "q@acme.test", "subject": "Leave", "body": "How much annual leave do I get?"},
                    headers=recruiter).json()  # .test addresses are accepted (EmailStr rejected them)
    draft = client.post(f"/emails/{e['id']}/draft-reply", headers=recruiter).json()
    client.post(f"/approvals/{draft['action_id']}/approve", json={}, headers=recruiter)
    assert next(x for x in client.get("/emails", headers=recruiter).json() if x["id"] == e["id"])["handled"]


# ---------------------------------------------------------------- scheduling
def test_recruiter_picked_slot_is_booked_and_never_offered_again(client, recruiter):
    _, row = _screened(client, recruiter, "Schedule Job", "Dana Ruiz\ndana.ruiz@example.com\n"
                       "4 years experience. Skills: Python, Docker")
    app_id, cid = row["application_id"], row["candidate_id"]
    assert client.post("/scheduling/propose", json={"application_id": app_id, "interviewer": "nobody@acme.test"},
                       headers=recruiter).status_code == 404
    s = client.post("/scheduling/propose", json={"application_id": app_id, "interviewer": "tom.lead@acme.test"},
                    headers=recruiter).json()
    a = client.post(f"/approvals/{s['action_id']}/approve", json={"selected_slot": 1}, headers=recruiter).json()
    assert a["status"] == "executed" and "interview booked" in a["result"]

    events = client.get("/scheduling/events", headers=recruiter).json()
    picked = datetime.fromisoformat(s["slots"][1]["start"])
    assert any(e["candidate_id"] == cid and datetime.fromisoformat(e["start"]).replace(tzinfo=None) == picked
               for e in events)
    invite = client.get("/approvals/sent", headers=recruiter).json()[0]
    assert invite["to"] == "dana.ruiz@example.com" and "{link}" not in invite["body"] and "booked for" in invite["body"]
    job_id = next(j["id"] for j in client.get("/jobs", headers=recruiter).json() if j["title"] == "Schedule Job")
    after = next(r for r in client.get(f"/jobs/{job_id}/ranking", headers=recruiter).json() if r["candidate_id"] == cid)
    assert after["status"] == "interview" and after["stage"] == "interview"
    again = client.post("/scheduling/propose", json={"application_id": app_id, "interviewer": "tom.lead@acme.test"},
                        headers=recruiter).json()
    assert s["slots"][1] not in again["slots"]  # a booked slot is never offered twice


# ---------------------------------------------------------------- follow-ups
def test_candidate_reminder_then_auto_close_when_they_answer(client, recruiter):
    _, row = _screened(client, recruiter, "Reminder Job", "Evan Moss\nevan.moss@example.com\n"
                       "6 years experience. Skills: Python, SQL")
    cid = row["candidate_id"]
    with Session(engine) as s:
        c = s.get(Candidate, uuid.UUID(cid))
        c.last_contact_at = utcnow() - timedelta(days=5)
        s.add(c)
        s.commit()

    assert client.post("/followups/scan", headers=recruiter).json()["reminders_proposed"] >= 1
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
    assert old["id"] not in [e["id"] for e in day["needs_reply_list"]]
    assert old["id"] in [e["id"] for e in week["needs_reply_list"]]
    assert "last 24h" in day["headline"]
    assert all({"category", "total", "high", "medium", "low"} <= set(g) for g in week["groups"])


def test_qa_works_without_manual_reindex(client, recruiter):
    r = client.post("/qa/ask", json={"question": "How many days per week can I work remotely?"}, headers=recruiter).json()
    assert not r["escalate_to_hr"] and r["sources"][0]["label"].startswith("remote-work")


def test_rate_limit_middleware_installed():
    assert any(m.cls is SlowAPIMiddleware for m in app.user_middleware)
