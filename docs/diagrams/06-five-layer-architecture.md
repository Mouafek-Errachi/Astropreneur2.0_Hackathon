# 06 · Five-layer architecture: what lives in each layer

**In one sentence:** each layer has one job and talks only to its neighbours; security (L5) is not a layer you pass
once but a ring every request and every action goes through.

```mermaid
%%{init: {"flowchart": {"wrappingWidth": 560}, "themeVariables": {"fontSize": "15px"}}}%%
flowchart TB
  L1["<b>L1 · Presentation</b> · dashboard (Streamlit), no business rules<br/>Screening · Inbox · Approvals · Scheduling · Follow-ups · CV sources<br/>Project staffing · Workforce analytics · Ask HR · AI Firewall"]
  L5["<b>L5 · Security</b> · around everything<br/>Gateway: login, API keys, roles, rate limits · Guard: files, hidden text, injections<br/>Output validator · Executor: the only part that sends or changes · Hash-chained audit log"]
  L2["<b>L2 · Orchestration</b> · workflows (FastAPI services + n8n)<br/>CV intake and screening · Inbox and replies · Interview scheduling · Follow-ups<br/>Project staffing · Workforce analytics · HR operations · Company documents"]
  L3["<b>L3 · AI agents</b> · read and draft only (Gemini · Grok · offline mock)<br/>CV reader and ranker · Email classifier · Reply drafter · Bias auditor · Candidate compare<br/>Policy assistant · Project analyzer · Job description writer · Analytics summary"]
  L4[("<b>L4 · Data</b> · SQLite, Postgres-ready<br/>Candidates, jobs, applications · Employees, skills, projects<br/>Pending actions, approvals · Audit log, security events · Files · Snapshots")]

  L1 ==>|"1 · every request passes the gateway"| L5
  L5 ==>|"2 · checked request"| L2
  L2 -->|"3 · cleaned text in, typed calls"| L3
  L3 -->|"4 · JSON back, checked by the validator"| L2
  L2 -->|"5 · proposed actions, records"| L4
  L5 -->|"6 · only approved actions are executed"| L4
```

## How to read it
- **L1 has no business rules.** The dashboard only calls the API, so the same rules apply to n8n and any other client.
- **L2 runs the workflows** and is where a workflow pauses for a human (it creates a pending action) and resumes after
  the decision.
- **L3 agents have no tools.** They receive text and return JSON in a fixed format; they cannot send, save or browse.
- **L4 keeps every row tied to a company (tenant)** and uses random IDs, so records cannot be guessed or mixed.
- **L5 is on both sides of the AI:** input is inspected before the AI reads it, and output is checked before anyone
  acts on it.

**In the code:** `dashboard/`, `backend/orchestration/` + `n8n/`, `backend/agents/`, `backend/data/`, `backend/security/`.
