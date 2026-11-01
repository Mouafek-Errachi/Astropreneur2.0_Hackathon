# 04 · Workforce analytics: from HR records to dashboard charts

**In one sentence:** the numbers are computed in code from records HR already keeps, filtered by what the viewer's
role may see, and drawn as charts; an optional AI summary may only repeat those numbers.

```mermaid
flowchart TB
  subgraph rec["HR records (no activity tracking)"]
    direction LR
    EMP["Employees and teams"]
    SKL["Employee skills"]
    ASG["Project assignments"]
    GAP["Skill gaps"]
    APP["Applications and<br/>status changes"]
    INT["CV intake log"]
    ACT["Approval queue"]
  end

  subgraph calc["Computed in code"]
    direction LR
    BW["Team capacity<br/>booked · free · over-booked"]
    SD["Skills per team<br/>and one-person skills"]
    GS["Open gaps<br/>by team and kind"]
    PF["Hiring funnel<br/>time in stage"]
    FL["Under-resourced flags<br/>(rules with thresholds)"]
  end

  EMP & ASG --> BW
  EMP & SKL --> SD
  GAP --> GS
  APP & INT & ACT --> PF
  BW & SD & GS --> FL

  BW & SD & GS & PF & FL --> PRIV{"Privacy filter<br/>by role"}
  PRIV -->|"recruiter: team totals,<br/>teams under 3 people hidden"| API["Analytics API"]
  PRIV -->|"manager, admin: also<br/>per-person workload (audited)"| API
  API --> UI["Dashboard charts<br/>tiles · bars · heatmaps · trends<br/>+ data table under each"]

  CRON["n8n daily job<br/>(key can save, never read)"] --> SNAP["Daily snapshot<br/>aggregates only"]
  calc -.-> SNAP
  SNAP --> TR["30-day trends"] --> UI

  API --> AI["AI summary<br/>(optional)"]
  AI --> CHK["Code check: every number<br/>must exist in the metrics"]
  CHK --> UI
```

## How to read it
- **Inputs are HR records only:** skills, allocations, gaps, the hiring pipeline. Nothing measures what employees do.
- **The privacy filter sits before the API.** A recruiter never receives per-person data, and figures from teams with
  fewer than 3 people are hidden from them, because a small team's number points at one person.
- **Trends come from daily snapshots** that store totals only, so history cannot rebuild an individual record.
- **The AI summary cannot invent figures:** a sentence quoting a number that is not in the metrics is dropped.

**In the code:** `backend/orchestration/analytics.py`, `backend/api/analytics.py`, `backend/agents/insights.py`,
dashboard page *Workforce analytics*. Spec: [workforce-telemetry-analytics.md](../specs/workforce-telemetry-analytics.md).
