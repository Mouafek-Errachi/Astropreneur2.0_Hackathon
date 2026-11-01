"""Speech-to-text. Deepgram: one live connection per speaker (channels are already separated by the extension).
Without Deepgram: see stt_local.py."""
import asyncio
import json
import logging
from urllib.parse import urlencode

import websockets

from .config import settings

log = logging.getLogger("uvicorn.error")


def provider() -> str:
    """deepgram | whisper | gemini. "auto": Deepgram if it has a key, otherwise the local Whisper model."""
    p = settings.stt_provider.strip().lower()
    if p in ("auto", ""):
        return "deepgram" if settings.deepgram_api_key else "whisper"
    return p


def make_stream(speaker: str, on_utterance, language: str = ""):
    from . import stt_local
    p = provider()
    if p == "deepgram":
        return DeepgramStream(speaker, on_utterance, language)
    fn = stt_local.gemini_transcribe if p == "gemini" else stt_local.whisper_transcribe
    return stt_local.SegmentingStream(speaker, on_utterance, fn, language)


def missing_config() -> str | None:
    p = provider()
    if p == "deepgram" and not settings.deepgram_api_key:
        return "Transcription is not configured (DEEPGRAM_API_KEY missing)."
    if p == "gemini" and not settings.gemini_api_key:
        return "Transcription is not configured (GEMINI_API_KEY missing)."
    if p == "whisper":
        try:
            import faster_whisper  # noqa: F401
        except ImportError:
            return "Transcription is not configured (pip install faster-whisper)."
    return None


async def warm_up():
    """Loads (on first run downloads) the Whisper model at startup, not on the candidate's first answer."""
    if provider() == "whisper":
        from . import stt_local
        await asyncio.to_thread(stt_local._load_whisper)


class DeepgramStream:
    def __init__(self, speaker: str, on_utterance, language: str = ""):
        self.speaker = speaker
        self.language = language
        self._on_utterance = on_utterance      # async (speaker, text)
        self._ws = None
        self._buf: list[str] = []
        self._tasks: list[asyncio.Task] = []

    async def start(self):
        params = {
            "model": settings.deepgram_model,
            "language": self.language or settings.deepgram_language,
            "encoding": "linear16",
            "sample_rate": 16000,
            "channels": 1,
            "smart_format": "true",
            "interim_results": "false",
            "endpointing": settings.deepgram_endpointing_ms,
        }
        self._ws = await websockets.connect(
            "wss://api.deepgram.com/v1/listen?" + urlencode(params),
            additional_headers={"Authorization": f"Token {settings.deepgram_api_key}"},
        )
        self._tasks = [asyncio.create_task(self._recv()), asyncio.create_task(self._keepalive())]

    async def send(self, pcm: bytes):
        if self._ws is None:
            return
        try:
            await self._ws.send(pcm)
        except Exception:
            pass

    async def _keepalive(self):
        try:
            while True:
                await asyncio.sleep(5)
                await self._ws.send(json.dumps({"type": "KeepAlive"}))
        except Exception:
            pass

    async def _flush(self):
        if self._buf:
            text, self._buf = " ".join(self._buf), []
            await self._on_utterance(self.speaker, text)

    async def _recv(self):
        try:
            async for raw in self._ws:
                if isinstance(raw, bytes):
                    continue
                m = json.loads(raw)
                if m.get("type") != "Results":
                    continue
                text = (m["channel"]["alternatives"][0].get("transcript") or "").strip()
                if m.get("is_final") and text:
                    self._buf.append(text)
                if m.get("speech_final"):
                    await self._flush()
        except Exception as e:
            log.warning("Deepgram %s stream ended: %s", self.speaker, e)
        finally:
            await self._flush()

    async def close(self):
        """Ask Deepgram to flush what it still holds, wait briefly for it, then shut down."""
        ws, self._ws = self._ws, None
        if ws is None:
            return
        try:
            await ws.send(json.dumps({"type": "CloseStream"}))
        except Exception:
            pass
        recv = self._tasks[0] if self._tasks else None
        if recv:
            try:
                await asyncio.wait_for(asyncio.shield(recv), timeout=3)
            except Exception:
                pass
        for t in self._tasks:
            t.cancel()
        try:
            await ws.close()
        except Exception:
            pass
