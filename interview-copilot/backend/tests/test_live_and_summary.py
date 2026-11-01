import asyncio
import io

import pytest
from fastapi.testclient import TestClient

from app import llm, sessions
from app.config import settings
from app.live import LiveSession
from app.main import app
from app.models import RevisionResult, SuggestionSet, Suggestion, SummaryContent


class FakeWS:
    def __init__(self): self.sent = []
    async def send_json(self, p): self.sent.append(p)


def make_live():
    s = sessions.create("u", "Backend Engineer", "Python, Postgres")
    return s, LiveSession(s, FakeWS())


@pytest.mark.asyncio
async def test_hr_speaking_does_not_cancel_pending_suggestion(monkeypatch):
    monkeypatch.setattr(settings, "suggest_debounce_seconds", 0.05)
    calls = []

    async def fake(s):
        calls.append(1)
        return SuggestionSet(observation="", suggestions=[Suggestion(question="How did you scale it?", reason="vague")])
    monkeypatch.setattr(llm, "live_suggestions", fake)

    s, live = make_live()
    await live.on_utterance("candidate", " ".join(["word"] * 20))
    # HR talks right away, for a long time
    await live.on_utterance("hr", "Okay thanks that is really interesting let me think about the next thing")
    await asyncio.sleep(0.3)
    assert calls == [1]
    assert any(m["type"] == "suggestions" for m in live.ws.sent)


@pytest.mark.asyncio
async def test_late_flush_is_kept_in_transcript(monkeypatch):
    s, live = make_live()
    await live.close()
    await live.on_utterance("candidate", "last words after close")
    assert s.transcript[-1]["text"] == "last words after close"
    assert live._debounce is None


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(settings, "dev_auth_bypass", True)
    return TestClient(app)


SUMMARY = SummaryContent(
    background="Five years in backend work.", strengths=["Led a migration"], concerns=["No Kafka experience"],
    notable_answers=["Scaling: moved to read replicas"], role_fit="Covers most requirements.",
)


def start(client):
    sid = client.post("/session/start", json={"job_title": "Backend Engineer", "job_description": "x"}).json()["session_id"]
    s = sessions.get(sid, "dev-user")
    s.transcript += [{"speaker": "hr", "text": "Tell me about yourself", "t": 0},
                     {"speaker": "candidate", "text": "I have five years in backend", "t": 1}]
    return sid, s


def test_end_generates_summary_and_is_idempotent(client, monkeypatch):
    n = []
    async def gen(s): n.append(1); return SUMMARY
    monkeypatch.setattr(llm, "generate_summary", gen)
    sid, _ = start(client)
    r = client.post(f"/session/{sid}/end").json()
    assert r["summary"]["background"].startswith("Five years") and r["utterances"] == 2
    client.post(f"/session/{sid}/end")
    assert len(n) == 1


def test_end_failure_then_retry(client, monkeypatch):
    state = {"fail": True}
    async def gen(s):
        if state["fail"]: raise RuntimeError("boom")
        return SUMMARY
    monkeypatch.setattr(llm, "generate_summary", gen)
    sid, _ = start(client)
    r = client.post(f"/session/{sid}/end").json()
    assert r["ended"] and r["summary"] is None and r["summary_error"]
    state["fail"] = False
    assert client.post(f"/session/{sid}/end").json()["summary"]["role_fit"]


def test_empty_transcript_no_llm_call(client, monkeypatch):
    sid = client.post("/session/start", json={}).json()["session_id"]
    r = client.post(f"/session/{sid}/end").json()
    assert "nothing to summarize" in r["summary"]["background"]


def test_save_revise_notes_export(client, monkeypatch):
    async def gen(s): return SUMMARY
    seen = {}
    async def rev(s, current, instruction):
        seen["current"], seen["instruction"] = current, instruction
        new = SummaryContent(**{**current, "concerns": []})
        return RevisionResult(summary=new, comment="Removed the concern.")
    monkeypatch.setattr(llm, "generate_summary", gen)
    monkeypatch.setattr(llm, "revise_summary", rev)
    sid, s = start(client)
    assert client.put(f"/session/{sid}/summary", json={"summary": SUMMARY.model_dump(), "notes": []}).status_code == 409
    client.post(f"/session/{sid}/end")

    edited = {**SUMMARY.model_dump(), "background": "HR edited this."}
    r = client.put(f"/session/{sid}/summary", json={"summary": edited, "notes": ["  Great energy  ", ""]})
    assert r.json()["notes"] == ["Great energy"]

    # revise uses the HR-edited text, and never touches notes
    r = client.post(f"/session/{sid}/summary/revise", json={"instruction": "drop concerns", "summary": edited}).json()
    assert seen["current"]["background"] == "HR edited this."
    assert r["summary"]["concerns"] == [] and r["comment"] and r["notes"] == ["Great energy"]

    for fmt, magic in (("pdf", b"%PDF"), ("docx", b"PK")):
        resp = client.get(f"/session/{sid}/summary/export", params={"format": fmt})
        assert resp.status_code == 200 and resp.content.startswith(magic)
        assert "attachment" in resp.headers["content-disposition"]
    assert client.get(f"/session/{sid}/summary/export", params={"format": "exe"}).status_code == 422


def test_other_user_cannot_access(client, monkeypatch):
    sid, s = start(client)
    s.owner = "someone-else"
    assert client.post(f"/session/{sid}/end").status_code == 404


def test_export_contents_hebrew_and_notes_separate():
    from docx import Document
    from pypdf import PdfReader
    from app import export
    summ = {"background": "שלוש שנים בפיתוח צד שרת.", "strengths": ["Led a migration", "הובילה מעבר"],
            "concerns": [], "notable_answers": ["x"], "role_fit": "Good."}
    notes = ["My own note"]

    d = Document(io.BytesIO(export.build_docx(summ, notes, "מפתח")))
    paras = d.paragraphs
    he = next(p for p in paras if p.text.startswith("שלוש"))
    en = next(p for p in paras if p.text == "Good.")
    assert he._p.pPr.find("{http://schemas.openxmlformats.org/wordprocessingml/2006/main}bidi") is not None
    assert en._p.pPr is None or en._p.pPr.find("{http://schemas.openxmlformats.org/wordprocessingml/2006/main}bidi") is None
    texts = [p.text for p in paras]
    assert texts.index("HR notes") > texts.index("Role fit")          # notes come after, under their own heading
    assert "My own note" not in "".join(t for t in texts[:texts.index("HR notes")])

    html = export.build_pdf_html(summ, notes, "מפתח")
    assert 'dir="rtl"' in html and "<script" not in html
    assert "&lt;" in export.build_pdf_html({**summ, "background": "<script>alert(1)</script>"}, [], "")
    text = PdfReader(io.BytesIO(export.build_pdf(summ, notes, "Dev"))).pages[0].extract_text()
    assert "HR notes" in text and "My own note" in text


def test_pdf_fallback_without_pango():
    from pypdf import PdfReader
    from app import export
    summ = {"background": "Five years.", "strengths": ["Led a migration"], "concerns": [],
            "notable_answers": ["שלוש שנים"], "role_fit": "Good."}
    text = PdfReader(io.BytesIO(export.build_pdf_fpdf(summ, ["My own note"], "Dev"))).pages[0].extract_text()
    assert "HR notes" in text and "My own note" in text and "Not a hiring recommendation" in text
