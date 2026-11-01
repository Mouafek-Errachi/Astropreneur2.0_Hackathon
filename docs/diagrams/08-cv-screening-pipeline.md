# 08 · CV screening pipeline: from a CV to a hiring decision

**In one sentence:** CVs arrive from several sources, pass the security guard, are read by the AI into facts, scored in
code against what the recruiter asked for, checked for bias, and only a person decides.

```mermaid
flowchart TD
  subgraph src["CV sources"]
    U["Upload in the dashboard"]
    C["n8n collector with an API key:<br/>Gmail attachments, LinkedIn,<br/>job boards, forms, Drive"]
  end
  U & C --> G["Security guard<br/>(see 05)"]
  G -->|"refused"| X["Not stored · logged"]
  G -->|"suspicious"| HR1["Stored, held for review"]
  G -->|"clean"| ST["Stored"]
  HR1 & ST --> AI["AI reads the CV into facts<br/>skills, years, title, education"]

  BRIEF["Recruiter describes the profile<br/>in plain words"] --> CRIT["AI turns it into criteria<br/>required · nice to have · years"]
  CRIT --> EDIT{{"Recruiter can edit criteria<br/>and weights"}}

  AI --> SCORE["Code: score and rank<br/>with a one-line justification"]
  EDIT --> SCORE
  LEARN["Weights learned from past hires<br/>(four-fifths bias check,<br/>manager sign-off)"] -.-> SCORE
  SCORE --> BIAS["Bias auditor<br/>re-scores with identity masked"]
  BIAS --> RANK["Ranking table<br/>flags, hidden text shown"]
  RANK --> CMP["Compare candidates<br/>(choice made in code,<br/>sentences by the AI)"]
  RANK --> DEC{{"Recruiter proposes:<br/>shortlist · interview · reject"}}
  DEC --> APP{{"Manager signs off<br/>(four-eyes)"}}
  APP --> NEXT["Status updated · interview invite<br/>or email drafted for approval"]
```

## How to read it
- **The AI reads; the code scores.** The score comes from the extracted facts and the recruiter's criteria, so
  "rank me first" hidden in a CV has nothing to act on.
- **Bias is checked twice.** Every CV is re-scored with identity details masked. Learned weights must also pass the
  four-fifths rule, so one group is not selected at under 80% of another's rate.
- **Changing criteria re-ranks instantly** without re-reading the CVs.
- **Every status change is recorded,** which feeds the time-in-stage figures in [04 · Analytics](04-workforce-analytics-data-flow.md).

**In the code:** `backend/orchestration/ingestion.py`, `backend/orchestration/screening.py`, `backend/agents/ranker.py`,
`backend/agents/brief.py`, `backend/agents/bias_auditor.py`, `backend/orchestration/learner.py`, dashboard pages
*Screening* and *CV sources*.
