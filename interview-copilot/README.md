# Interview Copilot

## Run the backend
    cd backend
    python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scriptsctivate
    pip install -r requirements.txt
    cp .env.example .env      # add GEMINI_API_KEY; keep DEV_AUTH_BYPASS=true for local tests
    uvicorn app.main:app --reload --port 8010
Check http://localhost:8010/health (port 8010, so it runs next to the HR platform on 8000)

## Load the extension
1. chrome://extensions, turn on Developer mode
2. Load unpacked, select the `extension/` folder
3. Open extension Options, set Backend URL (http://localhost:8010). Token can stay empty with DEV_AUTH_BYPASS=true.
4. Open a Google Meet call: click the ✦ bubble, fill the job details, Start. (Candidate consent is collected beforehand, outside the extension.)

## Test audio capture
1. Extension Options: click "Allow microphone" once.
2. Join a Meet call, open the bubble, Start. Then click the extension's toolbar icon (Chrome only allows tab capture right after a click).
3. The dot in the bubble turns green. The server log prints byte counts every ~10 s for candidate and hr.

## Speech-to-text (no Deepgram account needed)
`STT_PROVIDER` in `backend/.env`:
- `auto` (default): Deepgram if `DEEPGRAM_API_KEY` is set, otherwise local Whisper.
- `whisper`: [faster-whisper](https://github.com/SYSTRAN/faster-whisper) on this machine. No account, nothing leaves the server.
  Get the model (`WHISPER_MODEL`, default `small`, ~480 MB) once before the demo, with resume on flaky networks:
  `python download_whisper.py small` (saved in `backend/models/`; without it the first start downloads from Hugging Face).
  Measured on a 12-thread laptop CPU: an answer appears ~1-2 s after the speaker stops.
  Use `base` for a faster download and lower latency, `medium` for better French/Arabic.
- `gemini`: each utterance is sent to Gemini Flash (`GEMINI_API_KEY`). Uses more of your Gemini quota.

With Whisper or Gemini, the server cuts the audio into utterances itself (`STT_SILENCE_MS`, default 700 ms of quiet).
HR picks the **interview language** in the bubble's start form (English, Français, العربية, or detect). Pick it:
auto-detection can mistake accented English for French and then writes a French translation.
`STT_LANGUAGE` is the default when the extension sends none.

Summaries use `GEMINI_MODEL_SUMMARY` (default `gemini-3-flash-preview`: Pro models have no free-tier quota).
If that model is unavailable or out of quota, the live model is used instead.

## Live suggestions
Needs `GEMINI_API_KEY` in `backend/.env` (and speech-to-text, see above).
After each candidate answer (15+ words) a card with an observation and 2-3 follow-up questions appears in the bubble.
If a goodbye phrase is heard and nobody keeps talking for ~15 s, the bubble asks whether to end the interview.

## Real auth
Set DEV_AUTH_BYPASS=false, set JWT_SECRET to match your HR platform, paste a JWT (with `sub`) in Options.

## Live suggestions: HR talking
HR speaking never cancels or hides a pending suggestion. A card is only removed when HR presses Dismiss.

## End of interview, summary, export
1. "End interview" (or "Yes, end it" on the call-end prompt) stops audio and calls `POST /session/{id}/end`.
   The server flushes the last words from Deepgram, then Gemini Pro writes the summary (can take up to ~1 min).
   If it fails, the chat stays open and a "Try again" button re-calls `/end` (it only retries the summary).
2. The bubble shows the summary as editable text (lists: one item per line). HR can edit directly,
   ask the AI to revise (`/summary/revise`, with Undo), and add personal notes. Notes are never sent to the AI
   and are printed under their own "HR notes" heading in the file.
3. "Download it?" saves the edits (`PUT /session/{id}/summary`) and downloads PDF or Word
   (`GET /session/{id}/summary/export?format=pdf|docx`).

PDF uses WeasyPrint when Pango is installed, otherwise (e.g. on Windows) a pure-Python fallback (fpdf2 + HarfBuzz,
also RTL and Arabic shaping, fonts from the system font folder). WeasyPrint needs Pango on the server (Debian/Ubuntu: `apt install libpango-1.0-0 libpangoft2-1.0-0 fonts-noto-core`;
for Hebrew/Arabic also `fonts-noto-ui-core` or the Noto Sans Hebrew/Arabic fonts). Each paragraph's direction (RTL/LTR) is detected from its text.

## Storage, retention, candidate profile
- Nothing is written to the database while the interview runs: transcript, summary and notes stay in memory.
- Downloading the PDF or Word file is HR's confirmation. The server then saves the interview (transcript, the
  HR-edited summary, notes) and the file itself, under the candidate ID typed in the bubble's start form
  (the HR platform's candidate id, optional). Downloading again replaces the file of that format.
- `DATABASE_URL`: SQLite by default; PostgreSQL in production, e.g. `postgresql+psycopg://user:pass@host/db?sslmode=require`
  (Neon works). Tables are created on first use.
- `RETENTION_DAYS` (default 180): confirmed interviews and their files are hidden once expired and deleted by an hourly job.
  `UNCONFIRMED_TTL_HOURS` (default 12): sessions HR never downloaded are dropped from memory.
- Same HR-platform tenant (JWT `tid`) can read a candidate's interviews; only the interviewer can delete one early.

| Endpoint | |
|---|---|
| `GET /candidates/{candidate_id}/interviews` | confirmed interviews for a candidate, with their files |
| `GET /interviews/{id}` | transcript, summary, notes |
| `GET /interviews/{id}/exports/{export_id}` | the saved PDF/Word file |
| `DELETE /interviews/{id}` | delete early (interviewer only) |

## Auth
- JWTs come from the HR platform (ASTROPRENEUR: same `JWT_SECRET`, `sub` = email, `tid` = tenant, `typ` = access).
- The audio WebSocket no longer takes the JWT in its URL: the service worker gets a one-time ticket
  (`POST /session/{id}/audio-ticket`, valid 60 s) and the socket connects with `?ticket=`.
- The extension only has host access to `localhost:8010`. Setting another backend URL in Options asks Chrome for that one origin.
- CORS is closed by default (`CORS_ORIGINS=[]`): the extension calls from its service worker and needs none.

## Tests
    cd backend && pip install -r requirements.txt -r requirements-dev.txt && pytest

## Next steps
- Show the saved interviews on the HR platform's candidate page (it can call `GET /candidates/{id}/interviews`)
- WS reconnect, tab playback, Gemini data-use terms check, localized export headings, multilingual goodbye detection
