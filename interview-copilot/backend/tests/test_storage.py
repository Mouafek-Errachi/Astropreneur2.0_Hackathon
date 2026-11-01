from datetime import timedelta

import jwt
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app import db, llm, sessions
from app.config import settings
from app.main import app
from app.models import SummaryContent

SUMMARY = SummaryContent(background="Five years in backend work.", strengths=["Led a migration"], concerns=[],
                         notable_answers=["Scaling"], role_fit="Covers most requirements.")


def token(sub, tid):
    return jwt.encode({"sub": sub, "tid": tid, "typ": "access"}, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def H(sub="ana@acme.test", tid=1):
    return {"Authorization": f"Bearer {token(sub, tid)}"}


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(settings, "dev_auth_bypass", False)
    async def gen(s): return SUMMARY
    monkeypatch.setattr(llm, "generate_summary", gen)
    return TestClient(app)


def interview(client, candidate_id="42", headers=None):
    headers = headers or H()
    sid = client.post("/session/start", json={"job_title": "Backend Engineer", "candidate_id": candidate_id},
                      headers=headers).json()["session_id"]
    s = sessions._sessions[sid]
    s.transcript += [{"speaker": "candidate", "text": "I have five years in backend", "t": 1}]
    client.post(f"/session/{sid}/end", headers=headers)
    client.put(f"/session/{sid}/summary", json={"summary": SUMMARY.model_dump(), "notes": ["Strong"]}, headers=headers)
    return sid


def stored(sid):
    with db.session() as d:
        return d.get(db.Interview, sid)


def test_nothing_stored_until_download_then_saved_to_candidate(client):
    sid = interview(client)
    assert stored(sid) is None                       # ended + edited, but not confirmed yet

    r = client.get(f"/session/{sid}/summary/export", params={"format": "docx"}, headers=H())
    assert r.status_code == 200 and r.headers["x-saved-to-profile"] == "true"
    client.get(f"/session/{sid}/summary/export", params={"format": "docx"}, headers=H())   # again: replaced, not added
    client.get(f"/session/{sid}/summary/export", params={"format": "pdf"}, headers=H())

    lst = client.get("/candidates/42/interviews", headers=H()).json()
    assert [i["id"] for i in lst] == [sid]
    assert sorted(e["format"] for e in lst[0]["exports"]) == ["docx", "pdf"]

    # a teammate in the same tenant sees it and can download the file; the transcript is there
    mate = H("bo@acme.test", 1)
    full = client.get(f"/interviews/{sid}", headers=mate).json()
    assert full["transcript"][0]["text"].startswith("I have") and full["notes"] == ["Strong"]
    ex = lst[0]["exports"][0]
    f = client.get(f"/interviews/{sid}/exports/{ex['id']}", headers=mate)
    assert f.status_code == 200 and f.content[:2] in (b"PK", b"%P")

    # another tenant sees nothing
    other = H("eve@evil.test", 2)
    assert client.get("/candidates/42/interviews", headers=other).json() == []
    assert client.get(f"/interviews/{sid}", headers=other).status_code == 404
    assert client.get(f"/interviews/{sid}/exports/{ex['id']}", headers=other).status_code == 404

    # only the interviewer can delete
    assert client.delete(f"/interviews/{sid}", headers=mate).status_code == 404
    assert client.delete(f"/interviews/{sid}", headers=H()).status_code == 204
    assert stored(sid) is None


def test_retention_purges_expired(client):
    sid = interview(client, "7")
    client.get(f"/session/{sid}/summary/export", params={"format": "docx"}, headers=H())
    with db.session() as d, d.begin():
        d.get(db.Interview, sid).expires_at = db.utcnow() - timedelta(seconds=1)
    assert client.get("/candidates/7/interviews", headers=H()).json() == []   # hidden as soon as expired
    assert db.purge_expired() >= 1
    with db.session() as d:
        assert stored(sid) is None
        assert d.scalars(select(db.ExportFile).where(db.ExportFile.interview_id == sid)).all() == []


def test_unconfirmed_sessions_dropped_from_memory(client):
    sid = interview(client)
    sessions._sessions[sid].last_seen -= 10_000
    assert sessions.purge_stale(3600) >= 1 and sid not in sessions._sessions


def test_bad_candidate_id_and_refresh_token_rejected(client):
    assert client.post("/session/start", json={"candidate_id": "../x"}, headers=H()).status_code == 422
    refresh = jwt.encode({"sub": "a", "typ": "refresh"}, settings.jwt_secret, algorithm=settings.jwt_algorithm)
    assert client.post("/session/start", json={}, headers={"Authorization": f"Bearer {refresh}"}).status_code == 401


def test_audio_socket_needs_one_time_ticket(client, monkeypatch):
    class NoLive:
        def __init__(self, s, ws): pass
        async def start(self): pass
        async def feed(self, *a): pass
        async def close(self): pass
    monkeypatch.setattr("app.main.LiveSession", NoLive)
    sid = client.post("/session/start", json={}, headers=H()).json()["session_id"]

    # the old ?token= is no longer accepted
    with pytest.raises(Exception):
        with client.websocket_connect(f"/session/{sid}/audio?token={token('ana@acme.test', 1)}") as ws:
            ws.send_bytes(b"\x01\x00\x00")
    assert client.post(f"/session/{sid}/audio-ticket", headers=H("eve@evil.test", 2)).status_code == 404

    t = client.post(f"/session/{sid}/audio-ticket", headers=H()).json()["ticket"]
    with client.websocket_connect(f"/session/{sid}/audio?ticket={t}") as ws:
        ws.send_bytes(b"\x01\x00\x00")
    with pytest.raises(Exception):  # single use
        with client.websocket_connect(f"/session/{sid}/audio?ticket={t}") as ws:
            ws.send_bytes(b"\x01\x00\x00")
