# Secure AI HR System

Hackathon prototype (ASTROPRENEUR 2.0): AI agents that take over daily HR tasks — resume screening, inbox triage, reply drafting, follow-ups, interview scheduling, and policy Q&A — while staying secure.

**Golden rule: the agent executes, the human decides.** Agents only *propose* actions. Nothing is sent or recorded until a human approves it in the approval queue (`backend/core/approvals.py` is the only code path that sends or records).

## Status

This is the **basic structure**. Every feature has a working minimal version. `TODO` comments mark the places to improve.

## Stack

- **Backend:** FastAPI, SQLModel (SQLite by default), Pydantic
- **Dashboard:** Streamlit, a thin client that only calls the API
- **LLM:** `anthropic` SDK behind one wrapper (`backend/core/llm.py`). `LLM_MODE=mock` runs everything without an API key.
- **Security:** JWT and bcrypt for users, hashed API keys for integrations, role scopes, tenant scoping, slowapi rate limits, magic-byte file checks, a prompt-injection scanner, and a hash-chained audit log

## Quick start (local)

```bash
python -m venv .venv
```

```bash
.venv/Scripts/pip install -r requirements.txt
```

```bash
cp .env.example .env
```

Then start the backend and the dashboard together with one command (Ctrl+C stops both):

```bash
.venv/Scripts/python run.py
```

On Windows you can also double-click `run.bat`.

- API docs: http://localhost:8000/docs
- Dashboard: http://localhost:8501

Demo users (passwords are in `.env.example`): `admin@acme.test`, `recruiter@acme.test`, `viewer@acme.test`.

## Docker

```bash
docker compose up --build
```

## Tests

```bash
.venv/Scripts/python -m pytest -q
```

## Layout

```
backend/
  main.py, config.py, db.py, models.py, schemas.py
  core/        ingestion, sanitizer, injection, llm, storage, security, approvals, audit
  features/    auth, screening, email_digest, replies, approvals, followups, scheduling, employee_qa
  tests/
dashboard/     app.py + pages/ (Screening, Inbox, Approvals, Followups, Scheduling, Ask HR)
data/          sample resumes (incl. adversarial), jobs, emails, policies, calendar.json
```

## Security design (so far)

| Layer | What is in place |
|---|---|
| AI security | Untrusted text is sanitised (zero-width, bidi, and HTML stripped), scanned for injection patterns, wrapped in `<untrusted_document>` tags, and the LLM output is validated against strict Pydantic schemas. Scores are computed deterministically from the extracted facts, not taken from the LLM. The LLM never chooses email recipients. |
| Human in the loop | Every send or record goes through `Action` (pending), then a human approves, then the action executes once. High-risk actions need an explicit risk acknowledgement. API keys can never approve. |
| System security | Uploads are quarantined, then stored under UUID names in a non-public directory. File types are checked by magic bytes. Size and page limits are enforced. |
| Web security | JWT auth, RBAC scopes, tenant-scoped queries (404 on cross-tenant access), a login rate limit, restricted CORS, and security headers. |

## Roadmap / TODO

- Hidden-text detection in PDF/DOCX (white or tiny fonts, hidden runs) and zip-bomb checks
- Signed inbound email webhook (HMAC with timestamp)
- API key management endpoints and an audit log viewer
- Candidate reply templates (rejection or invitation) and reminder drafts
- Execute `schedule_interview` into calendar events
- Better retrieval for policy Q&A (TF-IDF or embeddings)
- A generator for 50–100 fake resumes, and load tests
