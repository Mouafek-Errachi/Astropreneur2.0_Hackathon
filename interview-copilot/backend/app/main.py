import asyncio
import logging
import re
from contextlib import asynccontextmanager
from datetime import date

from fastapi import Depends, FastAPI, HTTPException, Query, Response, WebSocket, WebSocketDisconnect
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware

from . import db, export, llm, sessions, stt
from .config import settings
from .live import LiveSession
from .auth import Principal, current_principal, current_user
from .models import (
    AudioTicketResponse, ChatRequest, ChatResponse, EndResponse, ReviseRequest, SaveSummaryRequest,
    StartSessionRequest, StartSessionResponse, SummaryPayload, SummaryResponse,
)

log = logging.getLogger("uvicorn.error")


async def _housekeeping():
    """Retention: deletes expired interviews and drops unconfirmed in-memory sessions, every hour."""
    while True:
        try:
            gone = await run_in_threadpool(db.purge_expired)
            dropped = sessions.purge_stale(settings.unconfirmed_ttl_hours * 3600)
            if gone or dropped:
                log.info("Retention: %d expired interviews deleted, %d unconfirmed sessions dropped", gone, dropped)
        except Exception as e:
            log.warning("Retention job failed: %s", e)
        await asyncio.sleep(3600)


async def _warm_stt():
    log.info("Speech-to-text: %s", stt.provider())
    try:
        await stt.warm_up()
    except Exception as e:
        log.warning("Speech-to-text warm-up failed: %s", e)


@asynccontextmanager
async def lifespan(app: FastAPI):
    task = asyncio.create_task(_housekeeping())
    warm = asyncio.create_task(_warm_stt())
    yield
    warm.cancel()
    task.cancel()


app = FastAPI(title="Interview Copilot", lifespan=lifespan)
# The extension calls from its service worker (host permission), so it needs no CORS entry.
app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins, allow_methods=["GET", "POST", "PUT", "DELETE"],
                   allow_headers=["Authorization", "Content-Type"],
                   expose_headers=["Content-Disposition", "X-Saved-To-Profile"])


@app.get("/health")
async def health():
    return {"ok": True}


@app.post("/session/start", response_model=StartSessionResponse)
async def start(req: StartSessionRequest, p: Principal = Depends(current_principal)):
    language = settings.stt_language if req.language is None else req.language
    s = sessions.create(p.user, req.job_title, req.job_description, req.candidate_id, p.tenant, language)
    return StartSessionResponse(session_id=s.id)


@app.post("/session/{session_id}/chat", response_model=ChatResponse)
async def chat(session_id: str, req: ChatRequest, user: str = Depends(current_user)):
    s = sessions.get(session_id, user)
    if not s:
        raise HTTPException(404, "Session not found")
    reply = await llm.chat_reply(s, req.message)
    s.chat += [{"role": "user", "text": req.message}, {"role": "model", "text": reply}]
    return ChatResponse(reply=reply)


def _own(session_id: str, user: str) -> sessions.Session:
    s = sessions.get(session_id, user)
    if not s:
        raise HTTPException(404, "Session not found")
    return s


@app.post("/session/{session_id}/end", response_model=EndResponse)
async def end(session_id: str, user: str = Depends(current_user)):
    """Stops the live side, flushes the last words, then writes the summary (Pro model).
    Safe to call again: it only retries the summary if there is none yet."""
    s = _own(session_id, user)
    async with s.lock:
        if s.live is not None:
            await s.live.close()
        s.ended = True
        error = None
        if s.summary is None:
            try:
                s.summary = (await llm.generate_summary(s)).model_dump()
            except Exception as e:
                log.warning("Summary failed: %s", e)
                error = "Could not write the summary. Try again."
        return EndResponse(
            ended=True, utterances=len(s.transcript),
            summary=SummaryPayload(**s.summary) if s.summary else None, summary_error=error,
        )


@app.put("/session/{session_id}/summary", response_model=SummaryResponse)
async def save_summary(session_id: str, req: SaveSummaryRequest, user: str = Depends(current_user)):
    """Stores the HR's hand edits and personal notes (notes stay separate from the AI summary)."""
    s = _own(session_id, user)
    if not s.ended:
        raise HTTPException(409, "End the interview first")
    s.summary = req.summary.model_dump()
    s.notes = [n.strip() for n in req.notes if n.strip()]
    return SummaryResponse(summary=req.summary, notes=s.notes)


@app.post("/session/{session_id}/summary/revise", response_model=SummaryResponse)
async def revise(session_id: str, req: ReviseRequest, user: str = Depends(current_user)):
    s = _own(session_id, user)
    current = req.summary.model_dump() if req.summary else s.summary
    if not current:
        raise HTTPException(409, "No summary to revise yet")
    async with s.lock:
        try:
            result = await llm.revise_summary(s, current, req.instruction)
        except Exception as e:
            log.warning("Revise failed: %s", e)
            raise HTTPException(502, "Could not revise the summary. Try again.")
        s.summary = result.summary.model_dump()
    return SummaryResponse(summary=SummaryPayload(**s.summary), notes=s.notes, comment=result.comment)


_MIME = {
    "pdf": "application/pdf",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}


@app.get("/session/{session_id}/summary/export")
async def export_summary(session_id: str, format: str = Query(pattern="^(pdf|docx)$"),
                         user: str = Depends(current_user)):
    s = _own(session_id, user)
    if not s.summary:
        raise HTTPException(409, "No summary to export yet")
    build = export.build_pdf if format == "pdf" else export.build_docx
    try:
        data = await run_in_threadpool(build, s.summary, s.notes, s.job_title)
    except Exception as e:
        log.exception("Export failed: %s", e)
        raise HTTPException(500, "Could not create the file")
    slug = re.sub(r"[^A-Za-z0-9]+", "-", s.job_title).strip("-").lower()
    name = f"interview-summary{'-' + slug if slug else ''}-{date.today().isoformat()}.{format}"
    # Downloading is HR's confirmation: only now is the transcript written to the database,
    # with the file, under the candidate's profile.
    try:
        await run_in_threadpool(db.save_confirmed, s, format, name, _MIME[format], data)
        saved = "true"
    except Exception as e:
        log.exception("Saving the interview failed: %s", e)
        saved = "false"
    return Response(data, media_type=_MIME[format],
                    headers={"Content-Disposition": f'attachment; filename="{name}"', "X-Saved-To-Profile": saved})


# ---------------------------------------------------------------- candidate profile (confirmed interviews)

@app.get("/candidates/{candidate_id}/interviews")
async def candidate_interviews(candidate_id: str, p: Principal = Depends(current_principal)):
    return await run_in_threadpool(db.list_for_candidate, candidate_id, p.user, p.tenant)


@app.get("/interviews/{interview_id}")
async def interview(interview_id: str, p: Principal = Depends(current_principal)):
    r = await run_in_threadpool(db.get_interview, interview_id, p.user, p.tenant)
    if not r:
        raise HTTPException(404, "Interview not found")
    return r


@app.get("/interviews/{interview_id}/exports/{export_id}")
async def interview_file(interview_id: str, export_id: int, p: Principal = Depends(current_principal)):
    f = await run_in_threadpool(db.get_export, interview_id, export_id, p.user, p.tenant)
    if not f:
        raise HTTPException(404, "File not found")
    return Response(f.data, media_type=f.mime, headers={"Content-Disposition": f'attachment; filename="{f.filename}"'})


@app.delete("/interviews/{interview_id}", status_code=204)
async def delete_interview(interview_id: str, user: str = Depends(current_user)):
    if not await run_in_threadpool(db.delete_interview, interview_id, user):
        raise HTTPException(404, "Interview not found")


@app.post("/session/{session_id}/audio-ticket", response_model=AudioTicketResponse)
async def audio_ticket(session_id: str, user: str = Depends(current_user)):
    """One-time, 60 s ticket for the audio WebSocket, so the JWT never goes into a URL."""
    return AudioTicketResponse(ticket=sessions.issue_ticket(_own(session_id, user)), expires_in=int(sessions.TICKET_TTL))


TAG_CANDIDATE = 1


@app.websocket("/session/{session_id}/audio")
async def audio(ws: WebSocket, session_id: str, ticket: str = ""):
    """Frames in: [1 byte tag][PCM16 mono 16 kHz]. 1 = candidate (tab), 2 = HR (mic).
    JSON out: suggestions / call_end_suspected / error. Auth: ?ticket= from POST /session/{id}/audio-ticket."""
    s = sessions.redeem_ticket(ticket, session_id)
    if not s:
        await ws.close(code=4401)
        return
    await ws.accept()
    live = LiveSession(s, ws)
    s.live = live
    await live.start()
    frames = 0
    try:
        while True:
            data = await ws.receive_bytes()
            if len(data) < 2:
                continue
            speaker = "candidate" if data[0] == TAG_CANDIDATE else "hr"
            s.audio_bytes[speaker] += len(data) - 1
            await live.feed(speaker, data[1:])
            frames += 1
            if frames % 100 == 0:  # ~10 s of audio
                log.info("session %s audio bytes: %s, utterances: %d", session_id[:8], s.audio_bytes, len(s.transcript))
    except WebSocketDisconnect:
        pass
    finally:
        await live.close()
        if s.live is live:
            s.live = None
