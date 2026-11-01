# Feature spec: Workforce Telemetry & Analytics

Project: Secure AI HR System (hackathon MVP). Builds on the project staffing feature
([project-staffing-gap-analysis.md](project-staffing-gap-analysis.md)) and the HR-only roles ([../roles.md](../roles.md)).
Write the architectural and functional specification for this feature, integrated into the existing 5-layer system.
Reuse the existing components named below; do not introduce new frameworks.

## 0. Context and adaptations to the original brief

- **Stack:** FastAPI + SQLModel (SQLite, Postgres-compatible), Streamlit dashboard as a thin client, Python only. The
  brief's Recharts, Chart.js or D3.js are JavaScript libraries and do not fit a Streamlit app. Charts use Streamlit's
  native charts and **Altair** (Vega-Lite), which ships with Streamlit, so there is no new dependency. Altair covers
  bar, stacked bar, line and heatmap charts, tooltips, and click and brush selection.
- **"Department" = `Team`.** The data has 4 teams (`n8n/data/hr_teams.csv`); employees belong to one team
  (`Employee.team_id`). Headcounts come from the real `Employee` rows, not from `Team.member_count`, which is a
  declared number for broadcasts and can disagree with them.
- **"Telemetry" means aggregate workforce and pipeline metrics, never monitoring people.** No activity tracking,
  keystrokes, logins, productivity scores or sentiment. The inputs are records HR already keeps: skills,
  project allocations, open gaps, the hiring pipeline and the approval queue.
- **"Real-time" means live on request, plus history.** At this scale (tens of employees, hundreds of applications)
  every metric is computed from the current tables when the page loads, cached for 60 seconds. A daily snapshot
  stores the same metrics so trends can be drawn. Event-driven recomputation is not needed: the live view is always
  current.
- **"Department heads" are not users.** Only HR staff log in. The HR **manager** role plays that part. Team-scoped
  managers (a manager who sees only their own teams) are listed as roadmap in section 7.
- **Numbers come from code, words from the AI.** The agent only writes a short narrative about metrics that were
  already computed, and every number it quotes is checked against them (as in `agents/compare.py`).

Existing pieces to reuse:
- **Data:** `Employee`, `Team`, `EmployeeSkill` (validated flag, level 1-4), `Skill` (taxonomy), `Project`,
  `ProjectAssignment` (allocation %), `SkillGap` (kind, gap FTE, decision, status), `JobOffer`, `Application`
  (status), `Resume.source`, `ProposedAction` and `Approval`.
- **Logic:** `orchestration/staffing.py` (`matrix`, `_free_pct`, `compute_gaps`), `security/gateway.py`
  (`ROLE_SCOPES`, `ROLE_ACCESS`), the audit log, and the n8n scheduler (as in the follow-ups cron).

## 1. Feature scope and metrics

All metrics are tenant-scoped. Each one is listed with its definition, so that the code, the tests and the
dashboard agree.

### 1.1 Departmental skill telemetry
| Metric | Definition |
|---|---|
| Skill distribution per team | For each team and skill, the number of **validated** holders at each level (1-4). Unvalidated skills are a separate count and never added in. |
| Skill coverage depth | For each team and skill, the number of validated holders at level 3 or higher. |
| Single point of failure | A skill needed by an active project that only **one** validated person in the company holds at the required level. |
| Validation backlog | Unvalidated skill entries per team, and the oldest one by `last_confirmed`. |

### 1.2 Resource bandwidth and workload
| Metric | Definition |
|---|---|
| Team capacity | Active employees x 1.0 FTE. |
| Allocated FTE | Sum of `ProjectAssignment.allocation_pct` / 100 over the team's active employees. |
| Free FTE | Sum over employees of max(0, 100 - their total allocation) / 100. |
| Over-allocated people | Employees whose allocations add up to more than 100 %. |
| Utilisation | Allocated FTE / team capacity. |
| Workload heatmap | Employees (rows) x projects (columns), each cell the allocation %, plus a total column. **Individual data: managers and admins only** (see section 5). |

### 1.3 Skill gaps from active projects
| Metric | Definition |
|---|---|
| Open gap FTE per team | Sum of `SkillGap.gap_fte` over projects of that team whose requirements are confirmed, and whose gaps are not decided as covered internally. |
| Gaps by kind | Count and FTE by `missing`, `insufficient_level` and `insufficient_capacity`. |
| Gap decision status | Gaps that are undecided, covered internally, set to hire externally, with a JD in Approvals, or hiring. |
| Most demanded skills | Skills ranked by total gap FTE across projects (the emerging bottlenecks). |

### 1.4 Hiring pipeline
| Metric | Definition |
|---|---|
| Funnel per job | Applications by status (`new`, `screened`, `needs_review`, `shortlisted`, `interview`, `rejected`), plus hires. |
| Intake by source | Resumes by `Resume.source` (upload, email, n8n collector, demo) and blocked files per source. |
| Time in stage | Median days from an application's creation to each status. **Needs one new table**: `PipelineEvent` (see section 4), because applications only store `created_at` today. |
| Approval queue | Pending `ProposedAction` rows by type, and the age of the oldest one. |
| Gap-to-hire link | For each gap marked hire externally: JD proposed, approved (a `JobOffer` exists), and the number of applications to that offer. |

### 1.5 Under-resourced team flags (rules in code, thresholds in config)
A team is flagged when one of these is true. Each flag shows the rule that triggered it.
- utilisation is at least 90 % (`ANALYTICS_UTILISATION_ALERT=0.9`);
- open gap FTE is at least 1.0 (`ANALYTICS_GAP_FTE_ALERT=1.0`);
- an active project of the team depends on a single point of failure;
- someone in the team is over-allocated.

## 2. Workflow

1. **Live view.** `GET /analytics/*` computes the metrics from the current tables. The results are cached in memory
   for 60 seconds, per tenant and per role level, so a recruiter's cache never holds individual data.
2. **Daily snapshot.** n8n calls `POST /analytics/snapshot` once a day, and an admin can also run it from the page.
   It stores one row per metric, team and day (idempotent: running it twice the same day replaces that day's rows).
   Snapshots only store aggregates, never individual rows.
3. **Insights (optional).** The page can ask for a narrative: the code sends the computed aggregates to the
   insights agent and validates its answer. If validation fails, a fixed template is shown instead.
4. **No actions.** Analytics are read-only. Staffing decisions stay on the Project Staffing page, which an "Open in
   Project staffing" link points to. Nothing here creates a `ProposedAction`.

## 3. Layer integration

| Layer | What to add |
|---|---|
| L1 Presentation: new `dashboard/pages/12_Workforce_Analytics.py`, under "People" in `nav.py` | Four tabs: **Teams** (KPI tiles per team, under-resourced flags, a capacity vs allocated bar chart), **Skills** (a team x skill heatmap of validated holders, coloured by depth and filterable by skill category, plus a single-points-of-failure table), **Gaps** (gap FTE by team and by kind as stacked bars, and most demanded skills), **Pipeline** (funnel per job, intake by source, approval queue age, time-in-stage once events exist). An Altair selection on the team bar filters the other charts. Trend lines come from snapshots and appear once there are 2 or more days. The **workload heatmap** (employee x project) shows only for `skills:read`. Every chart has a "Show data" table under it, so values can be read without colour (accessibility), and a CSV download of the **aggregates** only. |
| L2 Orchestration: new `backend/orchestration/analytics.py` | Pure functions: `team_bandwidth`, `skill_distribution`, `single_points_of_failure`, `gap_summary`, `pipeline_funnel`, `intake_by_source`, `approval_queue`, `team_flags`, `snapshot(tenant)`. Each takes a session and a tenant and returns plain dicts. They reuse `staffing._free_pct` and the `SkillGap` rows rather than recomputing gaps. Minimum group size (section 5) is applied here, before data leaves the layer. |
| L3 Agents: new `backend/agents/insights.py` | `summarise(metrics: dict) -> InsightsDraft` through `complete_json`. The input is **trusted, aggregated** JSON (team names, counts, FTE, skill names): no employee names, no free text from documents or emails. The schema is `headline` (at most 120 chars) and `observations` (at most 5 items of `{text, metric_keys}`). The code checks that each observation's numbers appear in the referenced metrics, and drops any that don't. The mock writes template sentences from the top flags. One call per page request at most, and only on demand ("Summarise"). |
| L4 Data: `backend/data/models.py` | `MetricSnapshot(tenant_id, day, metric, team_id nullable, dims JSON, value float)` with a unique key on (tenant, day, metric, team, dims hash). `PipelineEvent(tenant_id, application_id, from_status, to_status, at, actor)` is written wherever an application's status changes (screening and the executor). Departmental mapping stays `Employee.team_id`, with no new table. Bump `SCHEMA_VERSION`. Retention: snapshots older than `ANALYTICS_RETENTION_DAYS=365` are deleted by the snapshot job. |
| L5 Security | New scopes: `analytics:read` (aggregated views: recruiter, manager, admin) and `analytics:snapshot` (admin, and allowed on machine keys so n8n can trigger the snapshot, but never read). Individual workload and per-person skill cells need the existing `skills:read` (manager, admin). Add a row to `ROLE_ACCESS` and `docs/roles.md`. Audit events: `analytics.individual_viewed` (once per session per page), `analytics.exported`, `analytics.snapshot`. Default rate limit applies. |

## 4. API (FastAPI, new `backend/api/analytics.py`)

| Method | Path | Scope | Returns |
|---|---|---|---|
| GET | `/analytics/teams` | `analytics:read` | Capacity, allocated, free, utilisation, over-allocated count and flags per team |
| GET | `/analytics/skills?team=&category=` | `analytics:read` | Validated holders per team x skill x level, coverage depth, validation backlog |
| GET | `/analytics/skills/single-points` | `analytics:read` | Skills needed by active projects with only one validated holder; the holder's id only with `skills:read` |
| GET | `/analytics/gaps` | `analytics:read` | Gap FTE by team, by kind and by decision status; most demanded skills |
| GET | `/analytics/pipeline?job=` | `analytics:read` | Funnel, intake by source, approval queue, time in stage |
| GET | `/analytics/workload?team=` | `analytics:read` + `skills:read` | Employee x project allocation matrix (individual data) |
| GET | `/analytics/trends?metric=&team=&days=` | `analytics:read` | Daily snapshot series (at most 365 days) |
| POST | `/analytics/insights` | `analytics:read`, human only | AI narrative of the current aggregates, validated |
| POST | `/analytics/snapshot` | `analytics:snapshot` | Stores today's snapshot (n8n daily cron) |

Responses never contain names for recruiters. Where a person must be identified (workload, single point of failure),
the employee id and short name are returned only with `skills:read`, as on the staffing page.

## 5. Privacy rules

- **Minimum group size.** For callers without `skills:read`, any team-level figure computed from fewer than 3 people
  (`ANALYTICS_MIN_GROUP=3`) is replaced by "fewer than 3". Otherwise a skill count of 1 in a small team points at one
  person.
- **No protected attributes.** The system does not store age, gender or origin, and analytics must not infer them.
  Fairness analysis stays in the existing bias auditor and the four-fifths check of the weight learner.
- **Exports are aggregates only.** CSV downloads contain what the role can see on screen, and each export is logged.
- **Snapshots are aggregates only**, so the history cannot be used to rebuild an individual's record after they
  leave.

## 6. Acceptance criteria (tests in `backend/tests/test_analytics.py`, mock LLM)

1. With the demo data, team utilisation, free FTE and over-allocated counts match values computed by hand from
   `ProjectAssignment` (deterministic; the same data always gives the same output).
2. Unvalidated skills are never counted as holders, and they appear in the validation backlog.
3. A skill held by exactly one validated person and required by a confirmed project is listed as a single point of
   failure. Adding a second validated holder removes it.
4. Open gap FTE per team equals the sum of that team's `SkillGap` rows. A gap covered internally is excluded.
5. A recruiter gets aggregates only. `/analytics/workload` returns 403. A group smaller than 3 shows "fewer than 3".
   A manager sees the workload matrix, and an `analytics.individual_viewed` audit event is written.
6. A machine key with `analytics:snapshot` can store a snapshot but gets 403 on every `GET /analytics/*`. Running the
   snapshot twice the same day leaves one set of rows.
7. The insights agent's output that quotes a number not in the metrics loses that observation. With the mock, the
   headline mentions the most severe team flag.
8. A status change on an application writes a `PipelineEvent`, and time in stage is computed from those events.
9. Another tenant's data never appears in any analytics response.

## 7. Out of scope for the MVP, and roadmap

- Out of scope: individual performance or productivity metrics, activity monitoring, attrition prediction, salary
  analytics, and benchmarking against other companies.
- Roadmap: team-scoped managers (a department head who only sees their own teams, through a `User.team_ids` scope
  filter in `get_owned` and the analytics functions); event-driven snapshots on staffing changes; alerts sent to HR
  through the existing approval and email path when a team becomes under-resourced.

## 8. Demo data

The existing demo data already has 4 teams, 12 employees with skills, 4 projects (2 of them with assignments:
emp_001 at 100 % and emp_009 at 80 %), 5 jobs with resumes, and pending approvals. That is too thin for workload
charts, so add:
- assignments on every demo project, spread over at least 3 teams, so most teams show some utilisation;
- one over-allocated employee (allocations summing to 120 %);
- one skill with a single validated holder that an active project needs;
- `scripts/generate_demo_data.py --snapshots 30`, which writes 30 days of plausible history so the trend lines are
  not empty on the first run.
