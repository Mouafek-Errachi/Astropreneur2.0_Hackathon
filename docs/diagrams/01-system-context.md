# 01 · System context: who and what connects to HRplus

**In one sentence:** HR staff use HRplus through a dashboard; candidates, emails and project files come in from
outside; every piece of the system sits in one of five layers, with security wrapped around all of them.

```mermaid
flowchart TB
  HR["HR staff<br/>recruiter · manager · admin"]
  CAND["Candidates<br/>CVs and emails"]

  subgraph hrplus["HRplus"]
    direction LR
    L1["L1 Presentation<br/>dashboard, pages per role"]
    L2["L2 Orchestration<br/>workflows and schedules"]
    L3["L3 AI agents<br/>read and draft, never act"]
    L4["L4 Data<br/>records, files, audit log"]
    L2 --> L3
    L2 --> L4
  end

  L5["L5 Security: gateway · guard · human approval · executor<br/>(every request in, every action out)"]

  subgraph services["Outside services"]
    direction LR
    N8N["n8n<br/>Gmail, CV sources,<br/>daily jobs"]
    LLM["AI models<br/>Gemini · Grok"]
    MAIL["Email<br/>Gmail SMTP"]
  end

  COP["Interview Copilot<br/>Chrome extension on Google Meet"]

  HR -->|"log in, review, approve"| L1
  L1 -->|"API calls with login token"| L5
  CAND -->|"apply"| N8N
  N8N -->|"scoped API key,<br/>cannot approve"| L5
  L5 -->|"checked requests"| L2
  L3 <-->|"untrusted text fenced,<br/>JSON back"| LLM
  L5 -->|"only after a signed<br/>human approval"| MAIL
  MAIL -.->|"replies, invites"| CAND
  HR -.->|"live interview help"| COP
```

## How to read it
- **Only HR staff log in.** Recruiter, manager and admin each see different pages ([07 · Roles](07-roles-and-permissions.md)).
  Employees are records in the system, not users.
- **Everything from outside is untrusted:** CVs, candidate emails and uploaded project files go through the security
  layer first ([05 · AI security](05-ai-security-guardrails.md)).
- **The AI only reads and drafts.** It has no access to email or to the database. Sending anything goes through a
  human approval and then the executor, the only component that holds the email credentials
  ([02 · Human approval](02-human-approval-state-machine.md)).
- **n8n is optional.** It watches mailboxes and runs daily jobs, and calls HRplus with an API key that can never approve.
- **The Interview Copilot** is a separate Chrome extension with its own small backend ([09](09-interview-copilot.md)).

**In the code:** `dashboard/` (L1), `backend/orchestration/` and `n8n/` (L2), `backend/agents/` (L3),
`backend/data/` (L4), `backend/security/` (L5), `interview-copilot/`.
