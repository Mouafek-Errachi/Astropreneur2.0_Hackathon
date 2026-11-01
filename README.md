# Secure AI HR System

Hackathon prototype (ASTROPRENEUR 2.0): AI agents that take over daily HR work — resume screening and ranking, inbox triage by urgency, reply drafting, follow-ups, interview scheduling, policy Q&A, HR tickets, team broadcasts and recognition points — while staying secure against the untrusted content they read.

**Golden rule: the agent executes, the human decides.** Agents only *propose* actions. Nothing is sent or recorded until a human approves it; the approval is signed and verified by the executor, the only component that can send mail or change records. Decisions about a candidate, job offers, learned weights and broadcasts need a second person, a manager or admin (four-eyes rule).

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

Only HR staff log in; employees are records, not users. Three roles, each adding to the one before
(full access table: [docs/roles.md](docs/roles.md)):

| Account | Role |
|---|---|
| `recruiter@acme.test` | **Recruiter:** runs hiring and HR requests day to day. Proposes; does not sign off. |
| `manager@acme.test` | **Manager:** everything a recruiter does, signs off on job offers, hiring decisions, broadcasts and scoring weights, and sees and validates individual employee skills. |
| `admin@acme.test` | **Admin (IT/security):** everything, plus API keys, the audit log and demo data. |

Passwords are in `.env.example`. Log in as admin, open **Demo data** at the bottom of the Home page, tick the bulk option and click **Load demo data**. The menu shows each role only the pages it can use.

## What it does

| Brief | What we built | Where |
|---|---|---|
| Resume screening & ranking | You say who you are looking for in your own words ("Python and SQL, Linux is a plus, 3 years minimum", any language); the AI turns it into criteria and the table re-ranks instantly without re-reading the CVs. Criteria and weights can be edited by hand. The score is computed in code from the extracted facts, with a one-line justification. Compare two or more candidates side by side with a recommendation (choice made in code, sentences by the AI). A bias auditor re-scores each resume with identity masked. Weights can be learned from past hires (four-fifths bias check). | Screening page, `/jobs/{id}/screen`, `/criteria/from-text`, `PUT /criteria`, `/compare`, `/learn-weights` |
| Email summary | Inbox sorted by urgency; digest over a time window, grouped by type and urgency, with a headline. High-risk emails are quarantined. | Inbox page, `GET /emails/digest?hours=24` |
| Automatic replies | Drafts for inbox emails (candidate-scoped), and candidate emails written from the candidate's own file. A rejection needs an approved decision first. | Inbox and Screening pages |
| Candidate follow-ups | Reminder drafts for candidates who have not answered our last message after `REMINDER_AFTER_DAYS`; follow-ups for unanswered inbox emails. A reminder is withdrawn automatically if the candidate writes back. | Follow-ups page, `POST /followups/scan` (n8n cron) |
| Interview scheduling | Free slots from the interviewer calendar. At approval, either book a slot now, or send the candidate a single-use link to pick one. A booked slot is never offered twice. | Scheduling and Approvals pages, `/public/schedule/{token}` |
| Policy questions and HR requests | Answers from policy documents only, with sources; anything else is escalated. HR files an employee's request as a ticket; the drafted reply to the employee waits for approval. | Ask HR page, `/qa/ask`, `/hr/tickets` |
| HR operations | Interview co-pilot (probing questions when an answer is vague), team broadcasts with a fact check, recognition points and leaderboard. | HR operations page; the n8n versions on "HR ops via n8n" (in the menu once `N8N_BASE_URL` is set) |
| Project staffing & skill gaps | Upload a project charter and dependency manifests; the AI extracts the skills the project needs (a person confirms them); the gaps against the team's validated skills and free capacity are computed in code (missing, level too low, not enough capacity); internal options (upskill, rebalance, validate a skill, related skill) come first; for a gap marked "hire externally" the AI drafts a job description (checked for invented pay, links, exclusionary wording, employee names), and a second person approves it into a job that screening can rank against. Spec: [docs/specs/project-staffing-gap-analysis.md](docs/specs/project-staffing-gap-analysis.md) | Project Staffing page, `/projects`, `/skills/matrix`, `/employees/{id}/skills` |
| Workforce analytics | Team capacity and who is over-booked, skills per team and the ones only one person holds, open gaps by team and kind, the hiring funnel, CVs by source and the approval queue, with 30-day trends and flags for under-resourced teams. All numbers are computed in code; an optional AI summary may only quote them. Recruiters see team totals (teams under 3 people hidden); managers also see the per-person workload, and viewing it is audited. Spec: [docs/specs/workforce-telemetry-analytics.md](docs/specs/workforce-telemetry-analytics.md) | Workforce analytics page, `/analytics/*`, n8n can call `POST /analytics/snapshot` daily |
| Security visibility | Every blocked file, injection, hidden text and quarantine in one place, plus the hash-chained audit log. | AI Firewall page |

## Demo walkthrough (5 minutes)

1. **Screening:** open Backend Engineer and type "I am looking for people with Python and SQL, Linux is a plus, at least 3 years of experience". Show what the AI understood, the re-ranked table, and the attack resumes flagged ⚠️ (their hidden text did not move their score). Open one with 🔎: hidden text shown, bias check, candidate chat.
2. **Compare:** tick the top 3, click Compare: recommendation and side-by-side table.
3. **Four-eyes:** as the recruiter, propose "rejected" for a candidate and try to approve it yourself (refused). Log in as admin and approve it.
4. **Candidate email:** draft the rejection, edit it in the approval queue, approve: it appears in "Sent emails".
5. **Inbox:** click **Sort them now** to classify, show the digest by urgency, and the phishing and injection emails quarantined (AI Firewall lights up).
6. **Scheduling:** propose slots, approve with "let the candidate choose", open the link from the sent email, pick a slot; the link then stops working.
7. **Project staffing:** open "Payments API migration", review the AI's draft requirements and confirm them: gaps appear (Python short on capacity, PostgreSQL level too low, Kubernetes missing). Mark Kubernetes "hire externally", draft the job description, and approve it as the manager: a new job appears on Screening. Show the mobile-app charter flagged for its planted injection, with gaps unchanged.
8. **Ask HR:** "How many days can I work remotely?" (answered with a source), then a question outside the policies (escalated).

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
  api/            routes: auth, recruiting, inbox, approvals, scheduling, hr, security, staffing
  orchestration/  workflow (propose/resume/cancel), ingestion, screening, learner, inbox, scheduling, followups, hr_ops,
                  staffing, seed
  agents/         llm (Gemini pool | Grok | mock), schemas, classifier, ranker, brief, compare, drafter, policy_rag,
                  bias_auditor, candidate_chat, hr_ops, project_analyzer, jd_writer
  data/           models, db, storage, vectors
  security/       gateway, guard/ (files, sanitizer, injection), validator, executor, audit, events
  tests/          91 tests: staffing, security, guard, human in the loop, pipeline, screening rules, brief, compare, learner, LLM
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
| `data/projects/` | 3 projects (payments migration, mobile app, data platform) with charters and manifests; the mobile charter has a planted injection |
| `data/employees/employee_skills.csv`, `data/skills/taxonomy.json` | Skill matrix of the 12 employees (validated and not yet validated) and the skill taxonomy (synonyms, related skills, package names) |
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

## CV intake from every source

`POST /intake/resumes` (header `X-API-Key`) receives CVs from outside tools (`backend/api/intake.py`). Each item is a file (`content_base64`), pasted `text`, or a structured `profile` (job board JSON, JSON Resume), tagged with its `source` (`email`, `drive`, `form`, `job_board`, `linkedin`, `other`) and a `source_ref` (the id in that system). Everything goes through the same ingestion as a dashboard upload, then screening continues as before.

- **Formats:** PDF, Word, OpenDocument, RTF, HTML, text, JSON, and photos or scans (JPG, PNG, WebP), which Gemini transcribes (needs `LLM_PROVIDER=gemini`).
- **Hidden text:** on top of the PDF and Word checks, ODT hidden text, RTF `\v` and hidden HTML are kept away from the agents and scanned by the Injection Guard. A structured profile is turned into text, so every field is scanned.
- **File safety:** types are checked by content, not by name; zip bombs, oversize files and invalid base64 are refused per item.
- **Keys:** an admin creates an upload-only key (scope `resumes:upload`) on the **Sources** page or with `POST /auth/api-keys`. No key can ever approve.
- **Duplicates:** the same `source` + `source_ref`, or the same file, is received once, so n8n can retry safely.
- **Log:** every CV, accepted or refused, is in `GET /intake/log` and on the Sources page.

n8n collects the CVs: import `n8n/workflows/intake-resume-collector.json` (Gmail, Google Drive, an application form, a job-board webhook). `n8n/pitch/` holds five mock-data workflows that show each feature for a demo.

## Testing real email sending

By default approved emails are only recorded (Approvals > Sent emails, `transport: outbox`). To send them over SMTP
to a local catcher (nothing reaches real inboxes; the demo addresses are fictional):

```bash
.venv/Scripts/python -m aiosmtpd -n -l 127.0.0.1:1025
```

Then add `SMTP_HOST=127.0.0.1` and `SMTP_PORT=1025` to `.env`, restart `run.py`, and approve an email: it is printed in
the catcher's terminal and shows `transport: smtp` in Sent emails. With Docker, `docker compose --profile mail` runs
MailHog instead (web inbox on http://localhost:8025).

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
