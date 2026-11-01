"""Live interview brain: transcript -> follow-up suggestions + call-end detection."""
import asyncio
import logging
import re
import time

from fastapi import WebSocket

from . import llm
from .config import settings
from .sessions import Session
from . import stt

log = logging.getLogger("uvicorn.error")

GOODBYE = re.compile(
    r"\b(good\s?bye|bye|have a (great|good|nice|wonderful|lovely) (day|one|week|weekend|evening)|"
    r"thank(s| you)( so much| very much)? for your time|we'?ll be in touch|we will be in touch|"
    r"talk soon|speak soon|take care)\b",
    re.I,
)


class LiveSession:
    def __init__(self, s: Session, ws: WebSocket):
        self.s = s
        self.ws = ws
        self.streams = {
            "candidate": stt.make_stream("candidate", self.on_utterance, s.language),
            "hr": stt.make_stream("hr", self.on_utterance, s.language),
        }
        self.active = False
        self._cand_words = 0
        self._debounce: asyncio.Task | None = None
        self._end_task: asyncio.Task | None = None
        self._bg: set[asyncio.Task] = set()
        self._busy = False
        self._closed = False
        self._close_task: asyncio.Task | None = None

    async def push(self, payload: dict):
        try:
            await self.ws.send_json(payload)
        except Exception:
            pass

    async def start(self) -> bool:
        problem = stt.missing_config()
        if problem:
            await self.push({"type": "error", "detail": problem})
            return False
        try:
            for st in self.streams.values():
                await st.start()
        except Exception as e:
            log.warning("Speech-to-text start failed: %s", e)
            await self.push({"type": "error", "detail": "Could not connect to the transcription service."})
            return False
        self.active = True
        return True

    async def feed(self, speaker: str, pcm: bytes):
        if self.active:
            await self.streams[speaker].send(pcm)

    # ---- called for every finished utterance ----
    async def on_utterance(self, speaker: str, text: str):
        self.s.transcript.append({"speaker": speaker, "text": text, "t": time.time()})
        words = len(text.split())
        if self._closed:          # late flush while shutting down: keep the text, no more live logic
            return

        # Real conversation resumed: not the end of the call after all
        if words >= 8 and self._end_task:
            self._end_task.cancel()
            self._end_task = None

        if GOODBYE.search(text) and not self.s.end_prompted and not self.s.ended:
            if self._end_task:
                self._end_task.cancel()
            self._end_task = asyncio.create_task(self._end_check())

        if speaker == "candidate":
            self._cand_words += words
            if self._debounce:
                self._debounce.cancel()
            self._debounce = asyncio.create_task(self._wait_then_suggest())
        # HR speaking never cancels a pending suggestion: the HR may be reading
        # one out, or asking something else, and still wants the card to appear.
        # HR can always press Dismiss in the bubble.

    async def _wait_then_suggest(self):
        await asyncio.sleep(settings.suggest_debounce_seconds)
        if self._cand_words < settings.min_answer_words or self._busy:
            return
        task = asyncio.create_task(self._suggest())   # separate task so a new utterance can't cancel the LLM call
        self._bg.add(task)
        task.add_done_callback(self._bg.discard)

    async def _suggest(self):
        self._busy = True
        self._cand_words = 0
        try:
            data = await llm.live_suggestions(self.s)
            if data and data.suggestions:
                self.s.suggested += [q.question for q in data.suggestions]
                await self.push({"type": "suggestions", **data.model_dump()})
        except Exception as e:
            log.warning("Suggestion failed: %s", e)
            await self.push({"type": "error", "detail": "Could not generate suggestions just now."})
        finally:
            self._busy = False

    async def _end_check(self):
        await asyncio.sleep(settings.end_check_seconds)
        if not self.s.end_prompted and not self.s.ended:
            self.s.end_prompted = True
            await self.push({"type": "call_end_suspected"})

    async def close(self):
        """Idempotent. Every caller waits for the same shutdown, so the last words from STT are in
        the transcript before anyone reads it."""
        if self._close_task is None:
            self._closed = True
            self.active = False
            self._close_task = asyncio.create_task(self._shutdown())
        await asyncio.shield(self._close_task)

    async def _shutdown(self):
        for t in (self._debounce, self._end_task, *self._bg):
            if t:
                t.cancel()
        await asyncio.gather(*(st.close() for st in self.streams.values()))
