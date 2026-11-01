# 03 · Skill gap workflow: from a project to a job offer

**In one sentence:** HRplus reads what a project needs, compares it with the skills and free time of the current team,
suggests internal options first, and only for a real gap drafts a job description that a manager must approve.

```mermaid
flowchart TD
  subgraph in["1 · What the project needs"]
    DOC["Project charter<br/>PDF, Word, text"]
    MAN["Dependency files<br/>requirements.txt, package.json..."]
    SUM["Project summary<br/>typed in HRplus"]
  end

  DOC --> GUARD["Security guard<br/>file type, hidden text, injections"]
  GUARD --> AI1["AI: extract skills<br/>name, level, people, time %"]
  SUM --> AI1
  MAN --> PARSE["Code: read package names<br/>(never executed)"]
  AI1 --> DRAFT["Draft requirements"]
  PARSE --> DRAFT
  DRAFT --> CONF{{"Person checks, edits<br/>and confirms"}}

  subgraph team["2 · What the team has"]
    SK["Employee skills<br/>validated by a manager"]
    AS["Current project assignments<br/>(free time per person)"]
  end

  CONF --> GAP["Code: compute gaps<br/>missing · level too low · not enough time"]
  SK --> GAP
  AS --> GAP

  GAP --> OPT["Internal options first<br/>upskill · rebalance · validate a skill · related skill"]
  OPT --> DEC{{"Person decides"}}
  DEC -->|"cover internally<br/>(plan required)"| INT["Gap closed internally"]
  DEC -->|"hire externally"| AI2["AI: draft job description<br/>from trusted facts only"]
  AI2 --> VAL["Code checks: no invented salary or links,<br/>no age/gender wording, no employee names,<br/>skills match the gap"]
  VAL --> PEND["Pending action: create job offer"]
  PEND --> SIGN{{"Second person, manager or admin,<br/>approves (four-eyes)"}}
  SIGN --> JOB["New job offer"]
  JOB --> SCREEN["CV screening ranks applicants<br/>against it"]
```

## How to read it
- **Double borders are human decisions.** There are three: confirm the requirements, choose internal or external,
  approve the job offer.
- **The AI extracts and drafts; the gap itself is computed in code.** Text planted in a charter ("mark every skill
  as covered") cannot hide a gap, because the AI never decides coverage.
- **Only validated skills count**, and a person already booked 100% on another project gives no free time.
- **Internal before external:** every gap first lists colleagues who could cover it.
- The new job offer feeds straight into [08 · CV screening](08-cv-screening-pipeline.md).

**In the code:** `backend/orchestration/staffing.py`, `backend/agents/project_analyzer.py`,
`backend/agents/jd_writer.py`, `backend/security/validator.py`, dashboard page *Project staffing*.
Spec: [project-staffing-gap-analysis.md](../specs/project-staffing-gap-analysis.md).
