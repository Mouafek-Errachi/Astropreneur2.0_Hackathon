# Secure AI HR System

Hackathon prototype (ASTROPRENEUR 2.0): AI agents that take over daily HR tasks — resume screening, inbox triage, reply drafting, follow-ups, interview scheduling, and policy Q&A — while staying secure.

**Golden rule: the agent executes, the human decides.** Agents only *propose* actions. Nothing is sent or recorded until a human approves it in the approval queue (`backend/core/approvals.py` is the only code path that sends or records). A decision about a candidate needs a second person (four-eyes rule).

## What it does

| Brief | What we built | Where |
|---|---|---|
| Resume screening & ranking | Ranked shortlist per job. Each candidate gets a one-line justification built from job criteria only (required skills, nice-to-have, years of experience). | Screening page, `POST /jobs/{id}/screen` |
| Email summary | Inbox digest for the last 24h (or 48h, 72h, a week), grouped by type and urgency, with the emails that need a human answer and the suspicious ones. | Inbox page, `GET /emails/digest?hours=24` |
| Automatic replies | Drafts for inbox emails, and for candidates (acknowledgement, invitation, rejection) written from the candidate's own file only. Rejection and invitation emails need an approved decision first. | Inbox and Screening pages |
| Candidate follow-ups | Reminder drafts for candidates who have not answered our last message after `REMINDER_AFTER_DAYS`, and follow-ups for inbox emails left unanswered. A reminder is withdrawn automatically if the candidate writes back. | Follow-ups page, `POST /followups/scan` |
| Interview scheduling | Free slots from the interviewer's calendar and interviews already booked. On approval: the chosen slot is booked and the invitation goes out. A booked slot is never offered twice. | Scheduling and Approvals pages |
| Employee questions | Answers from company policy documents only, with sources; anything else is escalated to HR. | Ask HR page, `POST /qa/ask` |
| Extra (n8n + Gemini) | Interview co-pilot, helpdesk tickets, team broadcasts, recognition points. | HR Ops (n8n) page, [n8n/README.md](n8n/README.md) |

## Stack

- **Backend:** FastAPI, SQLModel (SQLite by default), Pydantic
- **Dashboard:** Streamlit, a thin client that only calls the APIs
- **LLM:** `anthropic` SDK behind one wrapper (`backend/core/llm.py`). `LLM_MODE=mock` runs everything without an API key; use `LLM_MODE=live` for the demo. Bulk screening and classification run `LLM_CONCURRENCY` calls in parallel.
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

```bash
.venv/Scripts/uvicorn backend.main:app --reload
```

```bash
.venv/Scripts/streamlit run dashboard/app.py
```

- API docs: http://localhost:8000/docs
- Dashboard: http://localhost:8501

Demo users (passwords are in `.env.example`): `admin@acme.test`, `recruiter@acme.test`, `viewer@acme.test`.

**Demo data:** log in as admin and open "Load demo data" on the home page (or `POST /admin/demo-data`). It creates the Backend Engineer job, about 60 generated resumes (a few with planted prompt injections), about 40 emails over the last 4 days, and indexes the policies. Everything goes through the normal pipelines and every person is fictional. Loading twice adds nothing: duplicates are rejected.

## Demo walkthrough (5 minutes)

1. **Screening:** run screening on Backend Engineer. Show the ranking with its justifications, and the resumes with planted injections flagged ⚠️ for review (their text did not move their score).
2. **Four-eyes:** as the recruiter, propose "rejected" for a candidate and try to approve it yourself (refused). Log in as admin and approve it.
3. **Candidate email:** draft a rejection for that candidate, edit it in the approval queue, approve. It appears in the outbox.
4. **Inbox:** classify, show the 24h digest by type and urgency, and the phishing and injection emails flagged.
5. **Scheduling:** propose slots for a shortlisted candidate, pick one in the approval queue, show the booked interview and the invitation.
6. **Follow-ups:** scan, show the reminder drafts waiting for approval.
7. **Ask HR:** "How many days can I work remotely?" (answered with a source), then a question outside the policies (escalated).

## Docker

```bash
docker compose up --build
```

## Tests

```bash
.venv/Scripts/python -m pytest -q
```

The suite covers the human gate (nothing sent before approval, no double execution, four-eyes), injection flagging, duplicate resumes, scheduling, reminders, the digest window, and an 83-resume / 65-email volume run.

## Layout

```
backend/
  main.py, config.py, db.py, models.py, schemas.py
  core/        ingestion, sanitizer, injection, llm, storage, security, approvals, audit
  features/    auth, screening, email_digest, replies, approvals, followups, scheduling, employee_qa, demo
  tests/
dashboard/     app.py + pages/ (Screening, Inbox, Approvals, Followups, Scheduling, Ask HR, HR Ops n8n)
data/          sample resumes (incl. adversarial), jobs, emails, policies, calendar.json
n8n/           second backend: n8n webhook workflows + Gemini, seed tables, Postman tests
```

The `n8n/` folder holds the extra features as n8n workflows calling Gemini. The dashboard's HR Ops page calls them when `N8N_BASE_URL` and the `N8N_*_KEY` variables are set; the keys stay on the dashboard server. See [n8n/README.md](n8n/README.md) for setup and the attack tests.

## Security design

| Layer | What is in place |
|---|---|
| AI security | Untrusted text is sanitised (zero-width, bidi, and HTML stripped), scanned for injection patterns, wrapped in `<untrusted_document>` tags, and the LLM output is validated against strict Pydantic schemas. Drafts are scanned again before they reach the queue (injection patterns, links outside the allow-list). Scores and justifications are computed by code from the extracted facts, not taken from the LLM. |
| Recipients | The LLM never chooses who receives mail: replies go to the original sender, and candidate emails go to the address found in the candidate's own resume (an extracted address that is not literally in the resume is dropped). |
| Human in the loop | Every send or record goes through `Action` (pending), then a human approves, then the action executes once (409 on a second attempt). Decisions about candidates need a second person. High-risk actions need an explicit risk acknowledgement. API keys can never approve. Emails announcing a decision can only be drafted after that decision is approved. |
| Fairness | Ranking uses required skills, nice-to-have skills and years of experience only. Drafting prompts forbid anything unrelated to the job (age, gender, origin, health, family, religion), and rejection emails give no reasons. |
| System security | Uploads are quarantined, then stored under UUID names in a non-public directory. File types are checked by magic bytes. Size, page, batch and request-size limits are enforced; at most the size limit is read into memory. Duplicate resumes are rejected. |
| Web security | JWT auth, RBAC scopes, tenant-scoped queries (404 on cross-tenant access), rate limits on every route (stricter on login), restricted CORS, and security headers. Outside `ENVIRONMENT=dev` the API refuses to start with a default `JWT_SECRET`. |
| Audit | Hash-chained, append-only log of logins, uploads, screenings, proposals, approvals, rejections and withdrawals. |

## Roadmap / TODO

- Hidden-text detection in PDF/DOCX (white or tiny fonts, hidden runs) and zip-bomb checks
- Signed inbound email webhook (HMAC with timestamp)
- API key management endpoints and an audit log viewer
- Real calendar and SMTP integration (today: `data/calendar.json` and a simulated outbox)
- Better retrieval for policy Q&A (TF-IDF or embeddings)
