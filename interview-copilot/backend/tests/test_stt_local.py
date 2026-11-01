import asyncio

import numpy as np
import pytest

from app import stt, stt_local
from app.config import settings


def tone(seconds, amp=4000):
    t = np.arange(int(16000 * seconds)) / 16000
    return (amp * np.sin(2 * np.pi * 220 * t)).astype(np.int16).tobytes()


def quiet(seconds):
    return (np.random.default_rng(0).normal(0, 30, int(16000 * seconds))).astype(np.int16).tobytes()


async def run(audio, transcribe):
    got = []
    async def cb(speaker, text): got.append((speaker, text))
    s = stt_local.SegmentingStream("candidate", cb, transcribe)
    await s.start()
    for i in range(0, len(audio), 3200):
        await s.send(audio[i:i + 3200])
    await s.close()
    return got


@pytest.mark.asyncio
async def test_cuts_on_silence_keeps_order_and_skips_clicks():
    lengths = []
    async def fake(pcm, language):
        lengths.append(len(pcm) / 32000)
        await asyncio.sleep(0.01 * (3 - len(lengths)))   # first one slowest: order must still hold
        return f"utterance {len(lengths)}"
    audio = quiet(0.5) + tone(1.5) + quiet(1) + tone(0.1) + quiet(1) + tone(2) + quiet(1) + tone(0.8)
    got = await run(audio, fake)
    # the 0.1 s click is dropped; the last utterance (no trailing silence) is flushed by close()
    assert [t for _, t in got] == ["utterance 1", "utterance 2", "utterance 3"]
    assert 1.5 < lengths[0] < 2.6 and lengths[2] >= 0.8


@pytest.mark.asyncio
async def test_long_answer_is_cut_in_pieces(monkeypatch):
    monkeypatch.setattr(settings, "stt_max_utterance_seconds", 5)
    async def fake(pcm, language): return "x"
    assert len(await run(tone(12), fake)) == 3


@pytest.mark.asyncio
async def test_empty_or_failed_transcription_is_skipped():
    async def boom(pcm, language): raise RuntimeError("down")
    async def empty(pcm, language): return "  "
    assert await run(tone(1), boom) == [] and await run(tone(1), empty) == []


def test_provider_choice(monkeypatch):
    monkeypatch.setattr(settings, "stt_provider", "auto")
    monkeypatch.setattr(settings, "deepgram_api_key", "")
    assert stt.provider() == "whisper"
    assert isinstance(stt.make_stream("hr", None), stt_local.SegmentingStream)
    monkeypatch.setattr(settings, "deepgram_api_key", "k")
    assert stt.provider() == "deepgram"
    monkeypatch.setattr(settings, "stt_provider", "gemini")
    monkeypatch.setattr(settings, "gemini_api_key", "")
    assert "GEMINI_API_KEY" in stt.missing_config()


def test_interview_language_reaches_speech_to_text(monkeypatch):
    from fastapi.testclient import TestClient
    from app import sessions
    from app.main import app
    monkeypatch.setattr(settings, "dev_auth_bypass", True)
    monkeypatch.setattr(settings, "stt_language", "")
    c = TestClient(app)
    sid = c.post("/session/start", json={"language": "fr"}).json()["session_id"]
    s = sessions._sessions[sid]
    assert s.language == "fr" and stt.make_stream("candidate", None, s.language).language == "fr"
    assert sessions._sessions[c.post("/session/start", json={}).json()["session_id"]].language == ""
    assert c.post("/session/start", json={"language": "french!"}).status_code == 422
