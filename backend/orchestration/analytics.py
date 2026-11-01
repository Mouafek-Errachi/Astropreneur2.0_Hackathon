"""Workforce analytics (docs/specs/workforce-telemetry-analytics.md).

Every number is computed here, in code, from records HR already keeps: skills, project allocations, gaps, the hiring
pipeline and the approval queue. Nothing here tracks what people do. Each function takes a session and a tenant and
returns plain dicts; privacy (minimum group size, no names without skills:read) is applied here, before the data
leaves the layer. The daily snapshot stores aggregates only.
"""
import random
import statistics
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone

from sqlmodel import Session, delete, select

from backend.config import get_settings
from backend.data.models import (Application, Employee, EmployeeSkill, IntakeLog, JobOffer, MetricSnapshot,
                                 PipelineEvent, Project, ProjectAssignment, ProposedAction, SkillGap, Team, utcnow)
from backend.orchestration.staffing import current_requirements
from backend.security import audit

STATUSES = ["new", "screened", "needs_review", "shortlisted", "interview", "rejected"]
DECISION_BUCKETS = ["undecided", "hire externally", "JD in Approvals", "hiring", "covered internally"]
# Metrics that describe people (hidden for small teams without skills:read) vs. projects (always shown).
PEOPLE_METRICS = {"utilisation", "allocated_fte", "free_fte", "over_allocated"}
SNAPSHOT_METRICS = {"utilisation", "allocated_fte", "free_fte", "people", "gap_fte", "open_gaps", "single_points",
                    "applications", "pending_approvals", "validated_skills"}
HIDDEN = "fewer than 3"


def _aware(dt: datetime) -> datetime:
    """SQLite returns naive datetimes; everything stored is UTC."""
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _min_group() -> int:
    return get_settings().analytics_min_group


def _employees(session: Session, tid: int) -> dict[str, Employee]:
    return {e.id: e for e in session.exec(select(Employee).where(Employee.tenant_id == tid, Employee.active == True))}  # noqa: E712


def _name(e: Employee) -> str:
    return f"{e.first_name} {e.last_initial}."


def _teams(session: Session, tid: int) -> dict[str, str]:
    return {t.id: t.display_name for t in session.exec(select(Team).where(Team.tenant_id == tid))}


def _allocations(session: Session, tid: int) -> dict[str, dict]:
    """employee id -> {project id: allocation %}"""
    out: dict[str, dict] = defaultdict(dict)
    for a in session.exec(select(ProjectAssignment).where(ProjectAssignment.tenant_id == tid)):
        out[a.employee_id][a.project_id] = out[a.employee_id].get(a.project_id, 0) + a.allocation_pct
    return out


# ------------------------------------------------------------------ bandwidth and workload
def team_bandwidth(session: Session, tid: int) -> list[dict]:
    emps, alloc = _employees(session, tid), _allocations(session, tid)
    rows = []
    for team_id, name in sorted(_teams(session, tid).items()):
        members = [e for e in emps.values() if e.team_id == team_id]
        totals = [sum(alloc.get(e.id, {}).values()) for e in members]
        capacity = float(len(members))
        allocated = round(sum(totals) / 100, 2)
        rows.append({"team": team_id, "name": name, "people": len(members), "capacity_fte": capacity,
                     "allocated_fte": allocated, "free_fte": round(sum(max(0, 100 - t) for t in totals) / 100, 2),
                     "utilisation": round(allocated / capacity, 2) if capacity else 0.0,
                     "over_allocated": sum(1 for t in totals if t > 100)})
    return rows


def workload(session: Session, tid: int, team: str | None = None) -> dict:
    """Individual data (callers need skills:read): employees x projects, allocation % per cell, plus the total."""
    emps, alloc = _employees(session, tid), _allocations(session, tid)
    projects = {p.id: p.name for p in session.exec(select(Project).where(Project.tenant_id == tid))}
    rows = []
    for e in sorted(emps.values(), key=lambda e: (e.team_id, e.id)):
        if team and e.team_id != team:
            continue
        cells = {projects[pid]: pct for pid, pct in alloc.get(e.id, {}).items() if pid in projects}
        rows.append({"employee_id": e.id, "name": _name(e), "team": e.team_id, "allocations": cells,
                     "total": sum(cells.values())})
    used = sorted({p for r in rows for p in r["allocations"]})
    return {"projects": used, "rows": rows}


# ------------------------------------------------------------------ skills
def _validated_holders(session: Session, tid: int, emps: dict[str, Employee]) -> list[EmployeeSkill]:
    return [es for es in session.exec(select(EmployeeSkill).where(EmployeeSkill.tenant_id == tid))
            if es.employee_id in emps]


def skill_distribution(session: Session, tid: int, team: str | None = None) -> dict:
    """Validated holders per team x skill x level, coverage depth (level 3+), and the validation backlog.
    Unvalidated entries are counted apart and never added to the holders."""
    emps = _employees(session, tid)
    cells: dict[tuple, dict] = {}
    backlog: dict[str, dict] = {}
    for es in _validated_holders(session, tid, emps):
        t = emps[es.employee_id].team_id
        if team and t != team:
            continue
        c = cells.setdefault((t, es.skill), {"team": t, "skill": es.skill, "level_1": 0, "level_2": 0, "level_3": 0,
                                             "level_4": 0, "holders": 0, "depth": 0, "unvalidated": 0})
        if es.validated:
            c[f"level_{es.level}"] += 1
            c["holders"] += 1
            c["depth"] += es.level >= 3
        else:
            c["unvalidated"] += 1
            b = backlog.setdefault(t, {"team": t, "unvalidated": 0, "oldest": None})
            b["unvalidated"] += 1
            if b["oldest"] is None or es.last_confirmed < b["oldest"]:
                b["oldest"] = es.last_confirmed
    return {"cells": sorted(cells.values(), key=lambda c: (c["team"], c["skill"])),
            "backlog": sorted(backlog.values(), key=lambda b: b["team"])}


def single_points_of_failure(session: Session, tid: int) -> list[dict]:
    """Skills a confirmed project needs that exactly one validated, active person holds at the required level."""
    emps = _employees(session, tid)
    by_skill: dict[str, list[EmployeeSkill]] = defaultdict(list)
    for es in _validated_holders(session, tid, emps):
        if es.validated:
            by_skill[es.skill].append(es)
    found: dict[tuple, dict] = {}
    for p in session.exec(select(Project).where(Project.tenant_id == tid, Project.requirements_confirmed == True)):  # noqa: E712
        for req in current_requirements(session, p):
            holders = [es for es in by_skill[req.skill] if es.level >= req.level]
            if len(holders) == 1:
                e = emps[holders[0].employee_id]
                f = found.setdefault((req.skill, req.level), {
                    "skill": req.skill, "level": req.level, "projects": [], "teams": [],
                    "holder_id": e.id, "holder_name": _name(e), "holder_team": e.team_id})
                f["projects"].append(p.name)
                if p.team_id and p.team_id not in f["teams"]:
                    f["teams"].append(p.team_id)
    return sorted(found.values(), key=lambda f: (f["skill"], f["level"]))


# ------------------------------------------------------------------ gaps
def _bucket(g: SkillGap) -> str:
    if g.decision == "internal":
        return "covered internally"
    if g.status == "hiring":
        return "hiring"
    if g.status == "jd_proposed":
        return "JD in Approvals"
    return "hire externally" if g.decision == "hire_externally" else "undecided"


def gap_summary(session: Session, tid: int) -> dict:
    """Gaps of confirmed projects. A gap covered internally is not open: it is excluded from the open gap FTE."""
    projects = {p.id: p for p in session.exec(select(Project).where(Project.tenant_id == tid,
                                                                    Project.requirements_confirmed == True))}  # noqa: E712
    gaps = [g for g in session.exec(select(SkillGap).where(SkillGap.tenant_id == tid)) if g.project_id in projects]
    open_gaps = [g for g in gaps if g.decision != "internal"]
    by_team: dict[str, dict] = {}
    by_kind: dict[str, dict] = {}
    by_team_kind: dict[tuple, dict] = {}
    skills: Counter = Counter()
    for g in open_gaps:
        team = projects[g.project_id].team_id or ""
        t = by_team.setdefault(team, {"team": team, "gap_fte": 0.0, "gaps": 0})
        t["gap_fte"] = round(t["gap_fte"] + g.gap_fte, 2)
        t["gaps"] += 1
        k = by_kind.setdefault(g.kind, {"kind": g.kind, "gaps": 0, "gap_fte": 0.0})
        k["gaps"] += 1
        k["gap_fte"] = round(k["gap_fte"] + g.gap_fte, 2)
        tk = by_team_kind.setdefault((team, g.kind), {"team": team, "kind": g.kind, "gaps": 0, "gap_fte": 0.0})
        tk["gaps"] += 1
        tk["gap_fte"] = round(tk["gap_fte"] + g.gap_fte, 2)
        skills[g.skill] += g.gap_fte
    decisions = Counter(_bucket(g) for g in gaps)
    jobs = {j.id: j.title for j in session.exec(select(JobOffer).where(JobOffer.tenant_id == tid))}
    to_hire = []
    for g in gaps:
        if g.decision != "hire_externally":
            continue
        apps = len(session.exec(select(Application.id).where(Application.job_id == g.job_offer_id)).all()) if g.job_offer_id else 0
        to_hire.append({"skill": g.skill, "project": projects[g.project_id].name, "status": _bucket(g),
                        "job": jobs.get(g.job_offer_id, ""), "applications": apps})
    return {"open_gap_fte": round(sum(g.gap_fte for g in open_gaps), 2), "open_gaps": len(open_gaps),
            "by_team": sorted(by_team.values(), key=lambda t: -t["gap_fte"]),
            "by_kind": sorted(by_kind.values(), key=lambda k: k["kind"]),
            "by_team_kind": sorted(by_team_kind.values(), key=lambda r: (r["team"], r["kind"])),
            "by_decision": [{"status": b, "gaps": decisions.get(b, 0)} for b in DECISION_BUCKETS],
            "top_skills": [{"skill": s, "gap_fte": round(v, 2)} for s, v in skills.most_common(10)],
            "gap_to_hire": to_hire}


# ------------------------------------------------------------------ hiring pipeline
def pipeline(session: Session, tid: int, job_id=None) -> dict:
    jobs = [j for j in session.exec(select(JobOffer).where(JobOffer.tenant_id == tid)) if not job_id or j.id == job_id]
    funnel = []
    apps: dict = {}
    for j in sorted(jobs, key=lambda j: j.title):
        rows = session.exec(select(Application).where(Application.tenant_id == tid, Application.job_id == j.id)).all()
        apps.update({a.id: a for a in rows})
        counts = Counter(a.status for a in rows)
        funnel.append({"job": j.title, "job_id": str(j.id), "total": len(rows), **{s: counts.get(s, 0) for s in STATUSES}})
    intake: dict[str, dict] = {}
    for log in session.exec(select(IntakeLog).where(IntakeLog.tenant_id == tid)):
        if job_id and log.job_id != job_id:
            continue
        i = intake.setdefault(log.source, {"source": log.source, "accepted": 0, "blocked": 0})
        i["accepted" if log.accepted else "blocked"] += 1
    pending = session.exec(select(ProposedAction).where(ProposedAction.tenant_id == tid,
                                                        ProposedAction.status == "pending")).all()
    now = utcnow()
    queue: dict[str, dict] = {}
    for a in pending:
        q = queue.setdefault(a.type, {"type": a.type, "pending": 0, "oldest_hours": 0.0})
        q["pending"] += 1
        q["oldest_hours"] = max(q["oldest_hours"], round((now - _aware(a.created_at)).total_seconds() / 3600, 1))
    waits: dict[str, list] = defaultdict(list)
    for ev in session.exec(select(PipelineEvent).where(PipelineEvent.tenant_id == tid, PipelineEvent.from_status != "")):
        if (a := apps.get(ev.application_id)) is not None:
            waits[ev.to_status].append((_aware(ev.at) - _aware(a.created_at)).total_seconds() / 86400)
    return {"funnel": funnel, "intake": sorted(intake.values(), key=lambda i: -(i["accepted"] + i["blocked"])),
            "approvals": sorted(queue.values(), key=lambda q: -q["pending"]),
            "pending_approvals": len(pending),
            "oldest_approval_hours": max((q["oldest_hours"] for q in queue.values()), default=0.0),
            "time_in_stage": [{"status": s, "median_days": round(statistics.median(waits[s]), 2), "applications": len(waits[s])}
                              for s in STATUSES if waits.get(s)]}


# ------------------------------------------------------------------ flags and privacy
def team_flags(teams: list[dict], gaps: dict, spofs: list[dict]) -> dict[str, list[str]]:
    """Under-resourced rules (thresholds in config). Each flag says which rule fired."""
    s = get_settings()
    gap_by_team = {g["team"]: g["gap_fte"] for g in gaps["by_team"]}
    flags: dict[str, list[str]] = {t["team"]: [] for t in teams}
    for t in teams:
        f = flags[t["team"]]
        if t["people"] and t["utilisation"] >= s.analytics_utilisation_alert:
            f.append(f"utilisation {round(t['utilisation'] * 100)}% (alert at {round(s.analytics_utilisation_alert * 100)}%)")
        if t["over_allocated"]:
            f.append(f"{t['over_allocated']} person(s) booked over 100%")
        if (fte := gap_by_team.get(t["team"], 0)) >= s.analytics_gap_fte_alert:
            f.append(f"{fte:g} FTE of open skill gaps (alert at {s.analytics_gap_fte_alert:g})")
    single: dict[str, list[str]] = defaultdict(list)
    for sp in spofs:
        for team in sp["teams"]:
            single[team].append(sp["skill"])
    for team, skills in single.items():
        if team in flags:
            flags[team].append(f"depends on one person for {len(skills)} skill(s): {', '.join(sorted(skills))}")
    return flags


def _hide_people(t: dict) -> dict:
    return {**t, **{k: None for k in PEOPLE_METRICS}, "hidden": True}


def teams_view(session: Session, tid: int, individual: bool) -> list[dict]:
    teams, gaps, spofs = team_bandwidth(session, tid), gap_summary(session, tid), single_points_of_failure(session, tid)
    flags = team_flags(teams, gaps, spofs)
    gap_by_team = {g["team"]: g for g in gaps["by_team"]}
    out = []
    for t in teams:
        row = {**t, "gap_fte": gap_by_team.get(t["team"], {}).get("gap_fte", 0.0),
               "open_gaps": gap_by_team.get(t["team"], {}).get("gaps", 0), "flags": flags[t["team"]], "hidden": False}
        if not individual and t["people"] < _min_group():
            # people-based figures and flags would point at one or two people: keep only the project-based ones
            row = _hide_people(row)
            row["flags"] = [f for f in flags[t["team"]] if "skill gaps" in f or "depends on one person" in f]
        out.append(row)
    return out


def skills_view(session: Session, tid: int, individual: bool, team: str | None = None) -> dict:
    dist = skill_distribution(session, tid, team)
    if individual:
        return {**dist, "hidden_teams": []}
    size = Counter(e.team_id for e in _employees(session, tid).values())
    small = {t for t, n in size.items() if n < _min_group()}
    return {"cells": [c for c in dist["cells"] if c["team"] not in small],
            "backlog": [b for b in dist["backlog"] if b["team"] not in small], "hidden_teams": sorted(small)}


def single_points_view(session: Session, tid: int, individual: bool) -> list[dict]:
    rows = single_points_of_failure(session, tid)
    if individual:
        return rows
    return [{k: v for k, v in r.items() if not k.startswith("holder")} for r in rows]


# ------------------------------------------------------------------ snapshots and trends
def _snapshot_values(session: Session, tid: int) -> list[tuple[str, str, float]]:
    teams, gaps = team_bandwidth(session, tid), gap_summary(session, tid)
    gap_by_team = {g["team"]: g for g in gaps["by_team"]}
    rows = []
    for t in teams:
        for m in ("utilisation", "allocated_fte", "free_fte", "people"):
            rows.append((m, t["team"], float(t[m])))
        rows.append(("gap_fte", t["team"], float(gap_by_team.get(t["team"], {}).get("gap_fte", 0.0))))
    pipe = pipeline(session, tid)
    emps = _employees(session, tid)
    rows += [("gap_fte", "", gaps["open_gap_fte"]), ("open_gaps", "", float(gaps["open_gaps"])),
             ("single_points", "", float(len(single_points_of_failure(session, tid)))),
             ("applications", "", float(sum(f["total"] for f in pipe["funnel"]))),
             ("pending_approvals", "", float(pipe["pending_approvals"])),
             ("validated_skills", "", float(sum(1 for es in _validated_holders(session, tid, emps) if es.validated)))]
    return rows


def _write_day(session: Session, tid: int, day: str, rows: list[tuple[str, str, float]]) -> int:
    session.exec(delete(MetricSnapshot).where(MetricSnapshot.tenant_id == tid, MetricSnapshot.day == day))
    for metric, team, value in rows:
        session.add(MetricSnapshot(tenant_id=tid, day=day, metric=metric, team_id=team, value=value))
    return len(rows)


def snapshot(session: Session, tid: int, actor: str) -> dict:
    """Today's aggregates; idempotent (running it twice the same day replaces that day). Also applies retention."""
    day = date.today().isoformat()
    n = _write_day(session, tid, day, _snapshot_values(session, tid))
    cutoff = (date.today() - timedelta(days=get_settings().analytics_retention_days)).isoformat()
    session.exec(delete(MetricSnapshot).where(MetricSnapshot.tenant_id == tid, MetricSnapshot.day < cutoff))
    session.commit()
    audit.log(session, actor=actor, event="analytics.snapshot", tenant_id=tid, detail={"day": day, "rows": n})
    return {"day": day, "rows": n}


def demo_history(session: Session, tid: int, days: int = 30) -> int:
    """Demo only: plausible history for the trend charts, drifting towards today's values. Days that already have a
    snapshot are left alone."""
    current = _snapshot_values(session, tid)
    have = set(session.exec(select(MetricSnapshot.day).where(MetricSnapshot.tenant_id == tid)).all())
    written = 0
    for back in range(days, 0, -1):
        day = (date.today() - timedelta(days=back)).isoformat()
        if day in have:
            continue
        rng = random.Random(f"{tid}-{day}")
        ease = 0.75 + 0.25 * (1 - back / days)  # further back = a bit lower
        rows = []
        for metric, team, value in current:
            v = value if metric == "people" else value * ease * rng.uniform(0.94, 1.06)  # zero stays zero
            if metric in ("open_gaps", "single_points", "applications", "pending_approvals", "validated_skills"):
                v = float(round(v))
            rows.append((metric, team, round(v, 2)))
        written += _write_day(session, tid, day, rows)
    session.commit()
    return written


def trends(session: Session, tid: int, metric: str, team: str | None, days: int, individual: bool) -> list[dict]:
    if metric in PEOPLE_METRICS and not individual and team:
        size = sum(1 for e in _employees(session, tid).values() if e.team_id == team)
        if size < _min_group():
            return []
    since = (date.today() - timedelta(days=days)).isoformat()
    q = select(MetricSnapshot).where(MetricSnapshot.tenant_id == tid, MetricSnapshot.metric == metric,
                                     MetricSnapshot.day >= since)
    q = q.where(MetricSnapshot.team_id == team) if team is not None else q
    rows = session.exec(q.order_by(MetricSnapshot.day)).all()
    if not individual and metric in PEOPLE_METRICS and team is None:
        size = Counter(e.team_id for e in _employees(session, tid).values())
        rows = [r for r in rows if not r.team_id or size.get(r.team_id, 0) >= _min_group()]
    return [{"day": r.day, "team": r.team_id, "value": r.value} for r in rows]


# ------------------------------------------------------------------ input for the insights agent
def insight_metrics(teams: list[dict], gaps: dict, pipe: dict, spof_count: int) -> dict:
    """Flat, aggregated, name-free metrics the insights agent may quote. Hidden figures are left out."""
    m: dict[str, float] = {"company.open_gap_fte": gaps["open_gap_fte"], "company.open_gaps": gaps["open_gaps"],
                           "company.single_points_of_failure": spof_count,
                           "pipeline.pending_approvals": pipe["pending_approvals"],
                           "pipeline.oldest_approval_hours": pipe["oldest_approval_hours"],
                           "pipeline.applications": sum(f["total"] for f in pipe["funnel"])}
    for t in teams:
        k = f"team.{t['name']}"
        m[f"{k}.people"] = t["people"]
        m[f"{k}.gap_fte"] = t["gap_fte"]
        if not t["hidden"]:
            m[f"{k}.utilisation_pct"] = round(t["utilisation"] * 100)
            m[f"{k}.free_fte"] = t["free_fte"]
            m[f"{k}.over_allocated"] = t["over_allocated"]
    for s in gaps["top_skills"][:3]:
        m[f"skill.{s['skill']}.gap_fte"] = s["gap_fte"]
    return m
