"""Speech-to-text without Deepgram. The audio is cut into utterances here (energy-based: speech, then
STT_SILENCE_MS of quiet), and each utterance is transcribed by a local Whisper model or by Gemini.
Same interface as stt.DeepgramStream: start(), send(pcm), close()."""
import asyncio
import io
import logging
import wave
from pathlib import Path

import numpy as np

from .config import settings

log = logging.getLogger("uvicorn.error")

SAMPLE_RATE = 16000
FRAME = 320                      # 20 ms of PCM16 samples
LEAD_FRAMES = 15                 # keep ~300 ms before speech starts, so first syllables aren't cut


class SegmentingStream:
    def __init__(self, speaker: str, on_utterance, transcribe, language: str = ""):
        self.speaker = speaker
        self._on_utterance = on_utterance      # async (speaker, text)
        self._transcribe = transcribe          # async (pcm16 bytes, language) -> str
        self.language = language               # "" = detect
        self._pending = b""                    # bytes not yet a full 20 ms frame
        self._seg: list[bytes] = []            # current utterance
        self._lead: list[bytes] = []
        self._speech_frames = 0
        self._silent_frames = 0
        self._noise = 200.0                    # running noise floor (RMS)
        self._queue: asyncio.Queue = asyncio.Queue()
        self._open = False                     # accepting audio
        self._worker: asyncio.Task | None = None

    async def start(self):
        self._open = True
        self._worker = asyncio.create_task(self._work())

    async def send(self, pcm: bytes):
        if not self._open:
            return
        data = self._pending + pcm
        n = len(data) // (FRAME * 2) * FRAME * 2
        self._pending = data[n:]
        silence_frames = settings.stt_silence_ms // 20
        max_frames = int(settings.stt_max_utterance_seconds * 50)
        for i in range(0, n, FRAME * 2):
            raw = data[i:i + FRAME * 2]
            level = float(np.sqrt(np.mean(np.frombuffer(raw, dtype=np.int16).astype(np.float32) ** 2)))
            speech = level > max(settings.stt_min_rms, self._noise * 3)
            if not speech:  # follow the noise floor slowly, on quiet frames only
                self._noise = 0.95 * self._noise + 0.05 * min(level, 3000)
            if self._seg:
                self._seg.append(raw)
                if speech:
                    self._speech_frames += 1
                    self._silent_frames = 0
                else:
                    self._silent_frames += 1
                if self._silent_frames >= silence_frames or len(self._seg) >= max_frames:
                    self._cut()
            elif speech:
                self._seg = self._lead + [raw]
                self._lead, self._speech_frames, self._silent_frames = [], 1, 0
            else:
                self._lead = (self._lead + [raw])[-LEAD_FRAMES:]

    def _cut(self):
        seg, speech = self._seg, self._speech_frames
        self._seg, self._speech_frames, self._silent_frames = [], 0, 0
        if speech * 20 >= settings.stt_min_speech_ms:   # ignore coughs and clicks
            self._queue.put_nowait(b"".join(seg))

    async def _work(self):
        """One utterance at a time, so the transcript keeps the speaking order."""
        while True:
            pcm = await self._queue.get()
            if pcm is None:
                return
            try:
                text = (await self._transcribe(pcm, self.language)).strip()
            except Exception as e:
                log.warning("Transcription (%s) failed: %s", self.speaker, e)
                text = ""
            if text:
                await self._on_utterance(self.speaker, text)

    async def close(self):
        """Transcribes what is still buffered, then stops (waits up to 30 s for the last words)."""
        if not self._open:
            return
        self._open = False
        if self._seg:
            self._cut()
        self._queue.put_nowait(None)
        try:
            await asyncio.wait_for(asyncio.shield(self._worker), timeout=30)
        except Exception:
            self._worker.cancel()


# ---------------------------------------------------------------- local Whisper (faster-whisper)
# No account: runs on this machine's CPU (or GPU). The model is downloaded once on first use.

_whisper = None
_whisper_lock: asyncio.Lock | None = None
# Whisper invents these on noise; keep them out (they would also look like a goodbye)
_HALLUCINATIONS = {"thank you.", "thank you", "thanks for watching!", "thank you for watching.", "you", ".",
                   "bye.", "merci.", "sous-titres réalisés par la communauté d'amara.org"}


def _load_whisper():
    global _whisper
    if _whisper is None:
        from faster_whisper import WhisperModel
        # Prefer a copy fetched by download_whisper.py (models/whisper-<name>); else Hugging Face downloads it
        local = Path(__file__).resolve().parent.parent / "models" / f"whisper-{settings.whisper_model}"
        model = str(local) if (local / "model.bin").is_file() else settings.whisper_model
        log.info("Loading Whisper model %r (a first run without models/ downloads it)...", model)
        _whisper = WhisperModel(model, device=settings.whisper_device,
                                compute_type=settings.whisper_compute_type)
    return _whisper


def _whisper_sync(pcm: bytes, language: str) -> str:
    audio = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
    segments, _ = _load_whisper().transcribe(
        audio, language=language or None, beam_size=1, vad_filter=True,
        condition_on_previous_text=False, without_timestamps=True)
    parts = [s.text.strip() for s in segments if s.no_speech_prob < 0.6 and s.avg_logprob > -1.0]
    text = " ".join(p for p in parts if p)
    return "" if text.lower() in _HALLUCINATIONS else text


async def whisper_transcribe(pcm: bytes, language: str = "") -> str:
    global _whisper_lock
    if _whisper_lock is None:
        _whisper_lock = asyncio.Lock()
    async with _whisper_lock:              # one model: both speakers take turns on the CPU
        return await asyncio.to_thread(_whisper_sync, pcm, language)


# ---------------------------------------------------------------- Gemini (same GEMINI_API_KEY as suggestions)

def _wav(pcm: bytes) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SAMPLE_RATE)
        w.writeframes(pcm)
    return buf.getvalue()


async def gemini_transcribe(pcm: bytes, language: str = "") -> str:
    from google.genai import types
    from . import llm
    if llm._client is None:
        raise RuntimeError("GEMINI_API_KEY missing")
    lang = f" The language is {language}." if language else ""
    resp = await llm._client.aio.models.generate_content(
        model=settings.gemini_model_stt,
        contents=[types.Part.from_bytes(data=_wav(pcm), mime_type="audio/wav"),
                  "Transcribe this speech verbatim, in the language spoken." + lang +
                  " Output only the words said. If there is no clear speech, output nothing."],
        config=types.GenerateContentConfig(temperature=0, max_output_tokens=600,
                                           thinking_config=types.ThinkingConfig(thinking_budget=0)),
    )
    return (resp.text or "").strip()
