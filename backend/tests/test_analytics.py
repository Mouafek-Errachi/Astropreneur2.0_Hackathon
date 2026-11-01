"""Workforce analytics: the acceptance criteria of docs/specs/workforce-telemetry-analytics.md (mock LLM).
Other test modules change skills and projects on the same database, so expected values are computed from the rows."""
import uuid
from collections import defaultdict
from datetime import date

from sqlmodel import Session, select

from backend.agents import insights
from backend.agents.schemas import InsightsDraft
from backend.data.db import engine
from backend.data.models import (Employee, MetricSnapshot, PipelineEvent, Project, ProjectAssignment, SkillGap, Tenant,
                                 User)
from backend.security.gateway import hash_password
from backend.tests.conftest import login


def _project(client, headers, name, skills, team="engineering"):
    p = client.post("/projects", json={"name": name, "team_id": team}, headers=headers).json()
    r = client.put(f"/projects/{p['id']}/requirements", json={"skills": skills}, headers=headers)
    assert r.status_code == 200, r.text
    return p["id"], {g["skill"]: g for g in r.json()["gaps"]}


# 1 ---------------------------------------------------------------- bandwidth is computed from the assignments
def test_team_bandwidth_matches_the_assignments(client, manager, demo):
    teams = {t["team"]: t for t in client.get("/analytics/teams", headers=manager).json()}
    with Session(engine) as s:
        tid = s.exec(select(Employee)).first().tenant_id
        emps = {e.id: e for e in s.exec(select(Employee).where(Employee.tenant_id == tid, Employee.active == True))}  # noqa: E712
        total = defaultdict(int)
        for a in s.exec(select(ProjectAssignment).where(ProjectAssignment.tenant_id == tid)):
            total[a.employee_id] += a.allocation_pct
    for team, t in teams.items():
        members = [e for e in emps.values() if e.team_id == team]
        assert t["people"] == len(members)
        assert t["allocated_fte"] == round(sum(total[e.id] for e in members) / 100, 2)
        assert t["free_fte"] == round(sum(max(0, 100 - total[e.id]) for e in members) / 100, 2)
        assert t["over_allocated"] == sum(1 for e in members if total[e.id] > 100)
    assert teams["engineering"]["over_allocated"] >= 1  # emp_011 is booked 120% in the demo data
    assert any("over 100%" in f for f in teams["engineering"]["flags"])
    assert client.get("/analytics/teams", headers=manager).json() == list(teams.values())  # deterministic


# 2 ---------------------------------------------------------------- unvalidated skills never count as holders
def test_unvalidated_skills_are_backlog_not_holders(client, manager):
    r = client.put("/employees/emp_005/skills", json={"skills": [{"skill": "elixir", "level": 3, "validated": False}]},
                   headers=manager)
    assert r.status_code == 200
    sk = client.get("/analytics/skills", params={"team": "sales"}, headers=manager).json()
    cell = next(c for c in sk["cells"] if c["skill"] == "elixir")
    assert cell["holders"] == 0 and cell["depth"] == 0 and cell["unvalidated"] == 1
    assert next(b for b in sk["backlog"] if b["team"] == "sales")["unvalidated"] >= 1


# 3 ---------------------------------------------------------------- single points of failure
def test_single_point_of_failure_appears_and_goes_away(client, recruiter, manager):
    assert client.put("/employees/emp_002/skills", json={"skills": [{"skill": "rust", "level": 3}]},
                      headers=manager).status_code == 200
    _project(client, manager, "Rust rewrite", [{"skill": "rust", "level": 3, "headcount": 1, "allocation_pct": 20}])
    spof = next(s for s in client.get("/analytics/skills/single-points", headers=manager).json() if s["skill"] == "rust")
    assert spof["holder_id"] == "emp_002" and "Rust rewrite" in spof["projects"]
    hidden = next(s for s in client.get("/analytics/skills/single-points", headers=recruiter).json() if s["skill"] == "rust")
    assert not any(k.startswith("holder") for k in hidden)  # recruiters: no names
    assert client.put("/employees/emp_009/skills", json={"skills": [{"skill": "rust", "level": 4}]},
                      headers=manager).status_code == 200
    assert "rust" not in {s["skill"] for s in client.get("/analytics/skills/single-points", headers=manager).json()}


# 4 ---------------------------------------------------------------- gap totals; covered internally is not open
def test_gap_fte_per_team_and_internal_cover(client, manager):
    pid, gaps = _project(client, manager, "Mainframe bridge",
                         [{"skill": "cobol", "level": 2, "headcount": 1, "allocation_pct": 50}], team="operations")
    assert gaps["cobol"]["gap_fte"] == 0.5

    def team_fte(team):
        with Session(engine) as s:
            confirmed = {p.id: p for p in s.exec(select(Project).where(Project.requirements_confirmed == True))}  # noqa: E712
            return round(sum(g.gap_fte for g in s.exec(select(SkillGap)) if g.project_id in confirmed
                             and confirmed[g.project_id].team_id == team and g.decision != "internal"), 2)

    by_team = {g["team"]: g["gap_fte"] for g in client.get("/analytics/gaps", headers=manager).json()["by_team"]}
    assert by_team["operations"] == team_fte("operations") >= 0.5
    url = f"/projects/{pid}/gaps/{gaps['cobol']['id']}"
    assert client.put(url, json={"decision": "internal", "plan": "upskill: training"}, headers=manager).status_code == 200
    after = client.get("/analytics/gaps", headers=manager).json()
    assert {g["team"]: g["gap_fte"] for g in after["by_team"]}.get("operations", 0) == team_fte("operations")
    assert next(d for d in after["by_decision"] if d["status"] == "covered internally")["gaps"] >= 1


# 5 ---------------------------------------------------------------- privacy: aggregates only, small teams hidden
def test_recruiters_get_aggregates_and_small_teams_are_hidden(client, recruiter, manager, admin):
    rec = {t["team"]: t for t in client.get("/analytics/teams", headers=recruiter).json()}
    assert rec["media_team"]["hidden"] and rec["media_team"]["utilisation"] is None  # 2 active people
    assert not rec["engineering"]["hidden"] and rec["engineering"]["utilisation"] is not None
    assert "media_team" in client.get("/analytics/skills", headers=recruiter).json()["hidden_teams"]
    assert client.get("/analytics/workload", headers=recruiter).status_code == 403
    assert client.get("/analytics/trends", params={"metric": "utilisation", "team": "media_team"},
                      headers=recruiter).json() == []

    man = {t["team"]: t for t in client.get("/analytics/teams", headers=manager).json()}
    assert not man["media_team"]["hidden"]
    wl = client.get("/analytics/workload", headers=manager)
    assert wl.status_code == 200 and any(r["total"] > 100 for r in wl.json()["rows"])
    events = client.get("/audit", params={"limit": 500}, headers=admin).json()
    assert any(e["event"] == "analytics.individual_viewed" and e["actor"] == "manager@acme.test" for e in events)


# 6 ---------------------------------------------------------------- machine keys can snapshot, never read
def test_snapshot_key_cannot_read_and_snapshot_is_idempotent(client, admin):
    key = client.post("/auth/api-keys", json={"name": "n8n-analytics", "scopes": ["analytics:snapshot"]},
                      headers=admin).json()["key"]
    h = {"X-API-Key": key}
    first = client.post("/analytics/snapshot", headers=h)
    assert first.status_code == 200, first.text
    assert client.post("/analytics/snapshot", headers=h).json()["rows"] == first.json()["rows"]
    with Session(engine) as s:
        today = s.exec(select(MetricSnapshot).where(MetricSnapshot.day == date.today().isoformat())).all()
    assert len(today) == first.json()["rows"]  # the second run replaced the day, nothing doubled
    for path in ("/analytics/teams", "/analytics/skills", "/analytics/gaps", "/analytics/pipeline"):
        assert client.get(path, headers=h).status_code == 403
    assert client.post("/auth/api-keys", json={"name": "bad", "scopes": ["analytics:read", "skills:read"]},
                       headers=admin).status_code == 400  # skills:read is human-only


# 7 ---------------------------------------------------------------- the AI narrative cannot invent numbers
def test_insights_drop_sentences_with_numbers_not_in_the_metrics(client, manager):
    metrics = {"team.Engineering.utilisation_pct": 88, "company.open_gap_fte": 6.8}
    draft = InsightsDraft(headline="Engineering is at 88% of capacity.", observations=[
        {"text": "Engineering is at 88% of its capacity.", "metric_keys": ["team.Engineering.utilisation_pct"]},
        {"text": "Engineering needs 12 more engineers.", "metric_keys": ["team.Engineering.utilisation_pct"]},
        {"text": "Open gaps total 6.8 FTE.", "metric_keys": ["company.unknown"]}])
    headline, kept, dropped = insights.check(draft, metrics)
    assert headline and [o["text"] for o in kept] == ["Engineering is at 88% of its capacity."] and dropped == 2
    assert insights.check(InsightsDraft(headline="We need 40 hires."), metrics)[0] == ""

    r = client.post("/analytics/insights", headers=manager).json()
    flagged = [t["name"] for t in client.get("/analytics/teams", headers=manager).json() if t["flags"]]
    assert flagged and any(name in r["headline"] for name in flagged) and r["dropped"] == 0


# 8 ---------------------------------------------------------------- status changes leave pipeline events
def test_status_changes_write_pipeline_events(client, recruiter, demo):
    with Session(engine) as s:
        events = s.exec(select(PipelineEvent)).all()
    assert any(e.from_status == "" for e in events)  # creation
    assert any(e.from_status == "new" and e.to_status in ("screened", "needs_review") for e in events)  # screening
    stages = {t["status"] for t in client.get("/analytics/pipeline", headers=recruiter).json()["time_in_stage"]}
    assert stages & {"screened", "needs_review"}


# 9 ---------------------------------------------------------------- tenant isolation
def test_other_tenant_sees_nothing(client, demo):
    with Session(engine) as s:
        t = Tenant(name=f"globex-{uuid.uuid4().hex[:6]}")
        s.add(t)
        s.commit()
        email = f"hr@{t.name}.test"
        s.add(User(tenant_id=t.id, email=email, role="manager", hashed_password=hash_password("Globex#2026!")))
        s.commit()
    other = login(client, email, "Globex#2026!")
    assert client.get("/analytics/teams", headers=other).json() == []
    assert client.get("/analytics/skills", headers=other).json()["cells"] == []
    assert client.get("/analytics/gaps", headers=other).json()["open_gaps"] == 0
    assert client.get("/analytics/pipeline", headers=other).json()["funnel"] == []
    assert client.get("/analytics/workload", headers=other).json()["rows"] == []
