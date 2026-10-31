from pathlib import Path

DATA = Path(__file__).resolve().parents[2] / "data"


def test_requires_auth(client):
    assert client.get("/jobs").status_code == 401


def test_viewer_cannot_create_job(client, viewer):
    assert client.post("/jobs", json={"title": "X job"}, headers=viewer).status_code == 403


def test_screening_flow_and_injection_flag(client, recruiter):
    job = client.post("/jobs", json={"title": "Backend Engineer", "required_skills": ["python", "sql", "docker"],
                                     "min_years": 3}, headers=recruiter).json()
    files = [("files", (p.name, p.read_bytes())) for p in sorted((DATA / "resumes").glob("*.txt"))]
    files.append(("files", ("fake.pdf", b"MZ\x90\x00 this is not a pdf at all, just bytes")))
    up = client.post(f"/jobs/{job['id']}/resumes", files=files, headers=recruiter).json()
    assert sum(r["accepted"] for r in up) == 3
    assert any(not r["accepted"] for r in up)  # fake.pdf rejected by magic bytes

    ranked = client.post(f"/jobs/{job['id']}/screen", headers=recruiter).json()
    assert ranked[0]["name"] == "Alice Martin"
    eve = next(c for c in ranked if c["name"] == "Eve Hacker")
    assert eve["risk_level"] == "high" and eve["needs_review"]


def test_nothing_sent_without_approval(client, recruiter):
    e = client.post("/emails", json={"sender": "cand@example.com", "subject": "Hi", "body": "Any news on my application?"},
                    headers=recruiter).json()
    draft = client.post(f"/emails/{e['id']}/draft-reply", headers=recruiter).json()
    assert client.get("/approvals/outbox", headers=recruiter).json() == []
    r = client.post(f"/approvals/{draft['action_id']}/approve", json={}, headers=recruiter)
    assert r.status_code == 200 and r.json()["status"] == "executed"
    outbox = client.get("/approvals/outbox", headers=recruiter).json()
    assert len(outbox) == 1 and outbox[0]["to"] == "cand@example.com"
    # cannot execute twice
    assert client.post(f"/approvals/{draft['action_id']}/approve", json={}, headers=recruiter).status_code == 409
