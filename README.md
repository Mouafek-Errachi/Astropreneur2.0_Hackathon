# Secure AI HR System

Hackathon prototype (ASTROPRENEUR 2.0): AI agents that take over daily HR work — resume screening and ranking, inbox triage by urgency, reply drafting, follow-ups, interview scheduling, policy Q&A, employee tickets, team broadcasts and recognition points — while staying secure against the untrusted content they read.

**Golden rule: the agent executes, the human decides.** Agents only *propose* actions. Nothing is sent or recorded until a human approves it; the approval is signed and verified by the executor, the only component that can send mail or change records. Decisions about a candidate, learned weights and broadcasts need a second person (four-eyes rule).

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

`run.py` starts the API and the dashboard together and opens http://localhost:8501 (Ctrl+C stops both; on Windows you can double-click `run.bat`). API docs: http://127.0.0.1:8000/docs

| Account | Role |
|---|---|
| `admin@acme.test` | Everything, plus audit log, API keys and demo data |
| `recruiter@acme.test` | Screens, drafts and approves |
| `viewer@acme.test` | Read only |
| `yasmine.b@hr-demo.test` | Employee: asks HR, opens tickets |

Passwords are in `.env.example`. Log in as admin, tick "bulk" and click **Load demo data**.

## What it does

| Brief | What we built | Where |
|---|---|---|
| Resume screening & ranking | You say who you are looking for in your own words ("Python and SQL, Linux is a plus, 3 years minimum", any language); the AI turns it into criteria and the table re-ranks instantly without re-reading the CVs. Criteria and weights can be edited by hand. The score is computed in code from the extracted facts, with a one-line justification. Compare two or more candidates side by side with a recommendation (choice made in code, sentences by the AI). A bias auditor re-scores each resume with identity masked. Weights can be learned from past hires (four-fifths bias check). | Screening page, `/jobs/{id}/screen`, `/criteria/from-text`, `PUT /criteria`, `/compare`, `/learn-weights` |
| Email summary | Inbox sorted by urgency; digest over a time window, grouped by type and urgency, with a headline. High-risk emails are quarantined. | Inbox page, `GET /emails/digest?hours=24` |
| Automatic replies | Drafts for inbox emails (candidate-scoped), and candidate emails written from the candidate's own file. A rejection needs an approved decision first. | Inbox and Screening pages |
| Candidate follow-ups | Reminder drafts for candidates who have not answered our last message after `REMINDER_AFTER_DAYS`; follow-ups for unanswered inbox emails. A reminder is withdrawn automatically if the candidate writes back. | Follow-ups page, `POST /followups/scan` (n8n cron) |
| Interview scheduling | Free slots from the interviewer calendar. At approval, either book a slot now, or send the candidate a single-use link to pick one. A booked slot is never offered twice. | Scheduling and Approvals pages, `/public/schedule/{token}` |
| Employee questions | Answers from policy documents only, with sources; anything else is escalated. Employees can open tickets; the drafted reply waits for HR. | Ask HR page, `/qa/ask`, `/hr/tickets` |
| HR operations | Interview co-pilot (probing questions when an answer is vague), team broadcasts with a fact check, recognition points and leaderboard. | HR Ops page; the n8n versions on "HR Ops via n8n" |
| Security visibility | Every blocked file, injection, hidden text and quarantine in one place, plus the hash-chained audit log. | AI Firewall page |

## Demo walkthrough (5 minutes)

1. **Screening:** open Backend Engineer and type "I am looking for people with Python and SQL, Linux is a plus, at least 3 years of experience". Show what the AI understood, the re-ranked table, and the attack resumes flagged ⚠️ (their hidden text did not move their score). Open one with 🔎: hidden text shown, bias check, candidate chat.
2. **Compare:** tick the top 3, click Compare: recommendation and side-by-side table.
3. **Four-eyes:** as the recruiter, propose "rejected" for a candidate and try to approve it yourself (refused). Log in as admin and approve it.
4. **Candidate email:** draft the rejection, edit it in the approval queue, approve: it appears in "Sent emails".
5. **Inbox:** classify, show the digest by urgency, and the phishing and injection emails quarantined (AI Firewall lights up).
6. **Scheduling:** propose slots, approve with "let the candidate choose", open the link from the sent email, pick a slot; the link then stops working.
7. **Ask HR:** "How many days can I work remotely?" (answered with a source), then a question outside the policies (escalated).

## Architecture

Five layers (diagrams in [docs/architecture/](docs/architecture/)):

| Layer | Where | What |
|---|---|---|
| L1 Presentation | `dashboard/` | Streamlit thin client (all rules live in the API) |
| L2 Orchestration | `backend/orchestration/`, `n8n/` | Workflows: ingestion, screening (criteria, rescore, compare, weight learning), inbox, scheduling, follow-ups, HR ops. A workflow pauses on a pending action and resumes after the human decision. n8n handles triggers (IMAP, cron, webhooks) and calls the API with a scoped key, see [n8n/FASTAPI_INTEGRATION.md](n8n/FASTAPI_INTEGRATION.md). |
| L3 AI Agents | `backend/agents/` | Classifier, Ranker, Brief (request -> criteria), Compare, Reply Drafter, Policy RAG, Bias Auditor, Candidate Chat, Interview co-pilot, Broadcast writer, Ticket triage. No tools, JSON only. |
| L4 Data | `backend/data/` | SQLModel on SQLite (default) or Postgres, UUID keys, embeddings (pgvector-ready), quarantine + UUID file storage. An outdated dev SQLite file is backed up and recreated automatically. |
| L5 Security | `backend/security/` | Gateway, Injection Guard, Output Validator, Executor, Audit, Security events |

```
backend/
  main.py  config.py  schemas.py
  api/            routes: auth, recruiting, inbox, approvals, scheduling, hr, security
  orchestration/  workflow (propose/resume/cancel), ingestion, screening, learner, inbox, scheduling, followups, hr_ops, seed
  agents/         llm (Gemini pool | Grok | mock), schemas, classifier, ranker, brief, compare, drafter, policy_rag,
                  bias_auditor, candidate_chat, hr_ops
  data/           models, db, storage, vectors
  security/       gateway, guard/ (files, sanitizer, injection), validator, executor, audit, events
  tests/          83 tests: security, guard, human in the loop, pipeline, screening rules, brief, compare, learner, LLM
dashboard/        app.py + pages/
data/             5 jobs, resumes (100 generated + attacks), emails, policies, calendar, past hires (learner sample)
n8n/              n8n workflows + seed tables (shared with FastAPI) + Postman tests
scripts/          generate_demo_data.py
docs/architecture/
```

## LLM providers

`LLM_PROVIDER=gemini|grok|mock`, optional `LLM_FALLBACK_PROVIDER`. Gemini uses a model pool (`GEMINI_MODEL` + `GEMINI_FALLBACK_MODELS`): free-tier quotas are per model, so a rate-limited model cools down for the delay Google asks for while the next one takes over (up to `GEMINI_WAIT_BUDGET_S`). Grok goes through xAI's OpenAI-compatible API. Every answer is validated against a strict Pydantic schema and retried when invalid; if no provider answers, a deterministic rule-based version is used and the row shows `llm_fallback`. Bulk screening and classification run `LLM_CONCURRENCY` calls in parallel. `mock` needs no key and is what the tests use.

## Demo data

`data/` is already generated; `scripts/generate_demo_data.py` regenerates it (`--per-job 30` for 150 resumes).

| Data | What's in it |
|---|---|
| `data/jobs/` | 5 jobs: Backend Engineer, Data Engineer, Frontend Developer, DevOps Engineer, HR Generalist |
| `data/resumes/bulk/<job>/` | 20 resumes per job (PDF, DOCX, TXT): strong, borderline, weak and off-profile candidates |
| `data/resumes/` | 3 hand-written resumes and 10 attack files (white text, 1pt font, hidden and white Word runs, base64, chat-template tokens, zero-width characters, exfiltration, fake PDF, HTML with a hidden span) |
| `data/emails/sample_emails.json` | 43 emails over the last 7 days, incl. phishing and injection attempts |
| `data/policies/` | Expenses, training, onboarding, overtime, travel (leave, payroll, remote work, benefits and conduct come from `n8n/data`) |
| `data/calendar.json` | Busy blocks for 4 interviewers over the next 2 weeks |
| `data/employees/backend_engineer_hires.csv` | Past hires to try the weight learner |
| `n8n/data/` | 12 employees, 4 teams, points rules and ledger, policy sections |

## Security design

| Threat | Defence |
|---|---|
| Prompt injection in resumes and emails | Sanitised (NFKC; zero-width, bidi, tag characters and HTML removed), scanned for override, role-hijack, prompt-leak, score-manipulation, exfiltration and fake-markup patterns (also base64-encoded), fenced in `<untrusted_input>` tags. High-risk email is **quarantined** and never reaches an agent. |
| Hidden text (white or 1pt fonts in PDFs, hidden runs in DOCX) | Extracted separately and **never sent to the LLM**; shown to the human and flagged high risk. |
| Score manipulation | The LLM only extracts facts; the score and justification are computed in code. The compare recommendation is chosen in code and the AI's sentences must name the right person. The bias auditor re-scores with identity masked. |
| Recipients | The LLM never chooses who receives mail: replies go to the original sender, candidate emails to an address found literally in the candidate's own resume. The recipient cannot be edited at approval. |
| Malicious files | Magic-byte type check, size/page/batch/request-size and zip-bomb limits, bounded reads, duplicate (same file or same text) rejection, quarantine then UUID names in a non-public directory. |
| Bad agent output | Strict schemas, a link allow-list, a fact check for invented figures and links (broadcasts), citations that must exist (policy Q&A). |
| Unapproved actions | Every send or record is a `ProposedAction`; a human `Approval` is HMAC-signed over the payload hash and verified by the executor, which runs it once (409 on a second or concurrent decision). High risk needs explicit acknowledgement; four-eyes on candidate decisions, learned weights and broadcasts; a decision email needs the approved decision first. |
| Fairness | Ranking uses required skills, nice-to-have and years of experience only; prompts forbid protected characteristics; learned weights must pass the four-fifths rule; personal columns in the hires CSV are refused. |
| Web and API | JWT for people, hashed and scoped API keys for n8n (never able to approve), RBAC scopes, tenant-scoped queries (404 across tenants), unknown fields rejected, rate limits on every route (stricter on login), CORS, security headers, HMAC-signed webhook with replay protection, ticket identity from the token. Outside `ENVIRONMENT=dev` the API refuses to start with a default `JWT_SECRET`. |
| Tampering and accountability | Hash-chained audit log with a verify endpoint; security events feed the AI Firewall page (REST and `/ws/security`). |

## Learned scoring weights

`POST /jobs/{id}/learn-weights` takes a CSV of past hires (`skills`, `years_experience`, `jira_done`, `performance` out of 100, optional `group`) and learns how much each criterion predicted a good outcome (success = 50% performance + 50% Jira tickets ranked among colleagues; the better half counts as good) with a ridge logistic regression (`backend/orchestration/learner.py`). Only the weights are learned, so every score is still explained the same way. Any other column is refused, at least 30 hires are needed, rows are not stored, and with a `group` column the four-fifths rule must pass or nothing is proposed. The weights go to the approval queue as `apply_weights`; someone other than the proposer approves them, then candidates are re-ranked.

## Tests

```bash
.venv/Scripts/python -m pytest -q
```

## Docker

```bash
docker compose up --build
```

Optional profiles: `--profile postgres` (pgvector), `--profile mail` (MailHog UI on :8025), `--profile n8n` (:5678). See `.env.example`.

## Roadmap

- Chrome extension for the live interview co-pilot (calls `POST /interview/assist`).
- Executor in its own container (the only one with mail credentials), agents in a worker without them.
- LangGraph `interrupt()` + checkpointer for multi-step workflows (`orchestration/workflow.py` already has the propose/resume shape).
- pgvector columns and SQL similarity search on Postgres (`data/vectors.py`); real calendar and SMTP integration.
