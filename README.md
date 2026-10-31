# Secure AI HR System

Hackathon prototype (ASTROPRENEUR 2.0): AI agents that take over daily HR work — resume screening and ranking, inbox triage by urgency, reply drafting, follow-ups, interview scheduling, policy Q&A, employee tickets, team broadcasts and recognition points — while staying secure against the untrusted content they read.

**Golden rule: the agent executes, the human decides.** Agents only *propose* actions. Nothing is sent or recorded until a human approves it. The approval is signed, then verified by the executor, the only component that can send mail or change records.

## Quick start

```bash
.venv/Scripts/python -m pip install -r requirements.txt
```

```bash
cp .env.example .env
```

```bash
.venv/Scripts/python run.py
```

`run.py` starts the API and the dashboard together and opens http://localhost:8501. Ctrl+C stops both. On Windows you can also double-click `run.bat`.

1. Log in as `admin@acme.test` (passwords are in `.env.example`) and click **Load demo data**. Tick "bulk" for the full set: 5 jobs, 100 generated resumes plus 13 hand-written and attack files, and 43 inbox emails.
2. Log in as `recruiter@acme.test`, go to **Screening**, and click **Run screening**.
3. Queue a decision or an email, then approve it in **Approvals**.
4. Watch **AI Firewall**: it shows the attacks the guard caught.

API docs: http://127.0.0.1:8000/docs

| Account | Role |
|---|---|
| `admin@acme.test` | Everything, plus audit log, API keys and demo data |
| `recruiter@acme.test` | Screens, drafts and approves |
| `viewer@acme.test` | Read only |
| `yasmine.b@hr-demo.test` | Employee: asks HR, opens tickets |

The demo data in `data/` is already generated. To regenerate it (for example with more resumes), run the command below; `--per-job 30` makes 150 resumes:

```bash
.venv/Scripts/python scripts/generate_demo_data.py
```

| Data | What's in it |
|---|---|
| `data/jobs/` | 5 jobs: Backend Engineer, Data Engineer, Frontend Developer, DevOps Engineer, HR Generalist |
| `data/resumes/bulk/<job>/` | 20 resumes per job (PDF, DOCX, TXT): a mix of strong, borderline, weak and off-profile candidates |
| `data/resumes/` | 3 hand-written resumes and 10 attack files: white text, 1pt font, hidden and white Word runs, base64-encoded instructions, chat-template tokens, zero-width characters, data exfiltration, a fake PDF, and HTML with a hidden span |
| `data/emails/sample_emails.json` | 43 emails over the last 7 days: applications, candidate questions, interview requests, employee questions, phishing, injection attempts, newsletters |
| `data/policies/` | Expenses, training, onboarding, overtime and travel. Leave, payroll, remote work, benefits and conduct come from `n8n/data`. |
| `data/calendar.json` | Busy blocks for 4 interviewers over the next 2 weeks |
| `n8n/data/` | 12 employees, 4 teams, points rules and ledger, policy sections |

## Architecture

Five layers (diagrams in [docs/architecture/](docs/architecture/)):

| Layer | Where | What |
|---|---|---|
| L1 Presentation | `dashboard/` | Streamlit thin client: Inbox by urgency, Screening (with candidate chat), Approvals, Scheduling, Follow-ups, Ask HR, HR Ops, AI Firewall |
| L2 Orchestration | `backend/orchestration/`, `n8n/` | Workflows: ingestion, screening, inbox/reply, scheduling (magic links), follow-ups, HR ops. Each one pauses on a pending action and resumes after the human decision. n8n handles triggers (IMAP, cron, webhooks) and calls the API with a scoped key, see [n8n/FASTAPI_INTEGRATION.md](n8n/FASTAPI_INTEGRATION.md). |
| L3 AI Agents | `backend/agents/` | Classifier, Ranker + Justifier, Reply Drafter, Policy RAG, Bias Auditor, Candidate Chat, Interview co-pilot, Broadcast writer, Ticket triage. They have no tools and return JSON only. |
| L4 Data | `backend/data/` | SQLModel on SQLite (default) or Postgres. UUID keys. Embeddings are pgvector-ready. Files are quarantined, then stored under UUID names. |
| L5 Security | `backend/security/` | Gateway, Injection Guard, Output Validator, Executor, Audit, Security events |

```
backend/
  main.py  config.py  schemas.py
  api/            routes: auth, recruiting, inbox, approvals, scheduling, hr, security
  orchestration/  workflow (propose/resume), ingestion, screening, inbox, scheduling, followups, hr_ops, seed
  agents/         llm (Gemini | Grok | mock), schemas, classifier, ranker, drafter, policy_rag, bias_auditor, candidate_chat, hr_ops
  data/           models, db, storage, vectors
  security/       gateway, guard/ (files, sanitizer, injection), validator, executor, audit, events
  tests/          security, guard, human in the loop, end-to-end pipeline
dashboard/        app.py + pages/
data/             sample job, resumes (incl. attacks), emails, interviewer calendar
n8n/              n8n workflows + seed tables (shared with FastAPI) + Postman tests
scripts/          generate_demo_data.py
docs/architecture/
```

The HR reference data (employees, teams, points rules, policy sections) comes from the n8n seed tables in `n8n/data/`, so the n8n workflows and the FastAPI core share one dataset.

## LLM providers

Set `LLM_PROVIDER` to `gemini`, `grok` or `mock`. `LLM_FALLBACK_PROVIDER` is optional. Every output is validated against a strict Pydantic schema and retried when invalid. If the providers fail, the agent falls back to a deterministic version, so a batch of 100 resumes never breaks; those rows show `llm_fallback`. `mock` needs no API key and is what the tests use.

## Security design

| Threat | Defence |
|---|---|
| Prompt injection in resumes and emails | The guard sanitises text (NFKC; zero-width, bidi, tag characters and HTML removed), then scans for override, role-hijack, prompt-leak, score-manipulation, exfiltration and fake-markup patterns, including base64-encoded ones. Untrusted text is fenced in `<untrusted_input>` tags. High-risk email is **quarantined** and never reaches an agent. |
| Hidden text (white or 1pt fonts in PDFs, hidden runs in DOCX) | Extracted separately and **never sent to the LLM**. It is shown to the human and flagged as high risk. |
| Score manipulation | The LLM only extracts facts. The score is computed in code from the job criteria. The bias auditor re-scores with identity masked. |
| Malicious files | Type is checked by magic bytes (not the extension). Size, page-count and zip-bomb limits apply. Files go to quarantine first, then to UUID names in a non-public directory. |
| Bad agent output | Strict schemas, a link allow-list, a fact check for invented figures and links (broadcasts), and citations that must exist (policy Q&A). The agent never chooses an email recipient. |
| Unapproved actions | Every send or record is a `ProposedAction`. A human `Approval` is HMAC-signed over the payload hash; the executor verifies it and runs the action once (409 on a second decision). High risk needs explicit acknowledgement. Broadcasts need a second person (four-eyes). |
| Web and API | JWT for people and hashed, scoped API keys for n8n. Machine keys can never approve. RBAC scopes and tenant-scoped queries (404 across tenants). Unknown fields return 422. Rate limits, CORS, security headers, and an HMAC-signed webhook with replay protection. Ticket identity comes from the token, never the request body. |
| Tampering and accountability | Hash-chained audit log with a verify endpoint. The security event feed powers the AI Firewall page (REST, plus a WebSocket at `/ws/security`). |

## Tests

```bash
.venv/Scripts/python -m pytest -q
```

The tests cover RBAC, API keys, tenant isolation, the signed webhook, and injection and hidden-text detection (PDF and DOCX). They check the human-in-the-loop guarantees: no send without approval, single execution, payload tampering detected, quarantine, and four-eyes. They also screen 80 PDF/DOCX/TXT resumes end to end, and test the scheduling magic link, tickets, points and policy Q&A.

## Docker

```bash
docker compose up --build
```

Optional profiles: `--profile postgres` (pgvector), `--profile mail` (MailHog UI on :8025), `--profile n8n` (:5678). See `.env.example` for the matching settings.

## Roadmap

- Run the executor in its own container (the only one with mail credentials) and the agents in a worker without them.
- Use LangGraph `interrupt()` and a checkpointer for multi-step workflows. `orchestration/workflow.py` already follows the propose/resume shape.
- Use pgvector columns and SQL similarity search on Postgres (`data/vectors.py`).
- Add Redis pub/sub for security events when the API runs as several processes.
