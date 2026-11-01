"""Demo data. Users + HR reference tables are seeded on first start (the HR tables come from the
n8n seed CSVs in n8n/data, so both backends share one dataset). `load_demo()` adds a job,
the sample resumes and the sample emails, so the whole flow can be tested in one click."""
import csv
import json
import re
from datetime import datetime, timezone

from sqlmodel import Session, select

from backend.config import get_settings
from backend.data import vectors
from backend.data.models import (Employee, JobOffer, PointsLedger, PointsRule, PolicyChunk, Team, Tenant, User)
from backend.orchestration import ingestion
from backend.security.gateway import ROLE_SCOPES, hash_password


def _csv(name: str) -> list[dict]:
    path = get_settings().n8n_data_dir / f"{name}.csv"
    if not path.exists():
        return []
    with path.open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def _bool(v: str) -> bool:
    return str(v).strip().lower() == "true"


def seed(session: Session) -> None:
    s = get_settings()
    if session.exec(select(Tenant)).first():
        _deactivate_retired_roles(session)
        return
    tenant = Tenant(name="acme")
    session.add(tenant)
    session.commit()
    tid = tenant.id
    for email, role, pw, emp in [
        ("admin@acme.test", "admin", s.demo_admin_password, None),
        ("recruiter@acme.test", "recruiter", s.demo_recruiter_password, None),
        ("manager@acme.test", "manager", s.demo_manager_password, None),
    ]:  # HR staff only: employees are records, not users
        session.add(User(tenant_id=tid, email=email, role=role, hashed_password=hash_password(pw), employee_id=emp))

    for t in _csv("hr_teams"):
        session.add(Team(id=t["team_id"], tenant_id=tid, display_name=t["display_name"], member_count=int(t["member_count"]),
                         mailing_list=t["mailing_list"], tone_guide=t["tone_guide"]))
    session.commit()
    for e in _csv("hr_employees"):  # portal_token is not imported: employees do not log in here
        session.add(Employee(id=e["employee_id"], tenant_id=tid, first_name=e["first_name"], last_initial=e["last_initial"],
                             team_id=e["team_id"], email=e["email"], leave_balance_days=float(e["leave_balance_days"]),
                             contract_type=e["contract_type"], active=_bool(e["active"])))
    for r in _csv("hr_points_rules"):
        session.add(PointsRule(action_type=r["action_type"], tenant_id=tid, points=int(r["points"]), daily_cap=int(r["daily_cap"])))
    session.commit()
    for l in _csv("hr_points_ledger"):
        session.add(PointsLedger(tenant_id=tid, employee_id=l["employee_id"], action_type=l["action_type"],
                                 points=int(l["points"]), period=l["period"], awarded_by=l["awarded_by"], note=l["note"],
                                 awarded_on=datetime.fromisoformat(l["awarded_on"]).replace(tzinfo=timezone.utc),
                                 idempotency_key=l["idempotency_key"]))
    session.commit()
    index_policies(session, tid)
    from backend.orchestration import staffing  # skill taxonomy + the employees' skill matrix

    staffing.seed_taxonomy(session)
    staffing.seed_employee_skills(session, tid)


def _deactivate_retired_roles(session: Session) -> None:
    """Older databases still hold 'viewer' and 'employee' logins: only the three HR roles may sign in now."""
    for u in session.exec(select(User).where(User.role.not_in(list(ROLE_SCOPES)), User.active == True)):  # noqa: E712
        u.active = False
        session.add(u)
    session.commit()


def index_policies(session: Session, tenant_id: int) -> int:
    for old in session.exec(select(PolicyChunk).where(PolicyChunk.tenant_id == tenant_id)):
        session.delete(old)
    n = 0
    for p in _csv("hr_policy_chunks"):
        session.add(PolicyChunk(tenant_id=tenant_id, doc_id=p["doc_id"], section=p["section"], title=p["title"],
                                content=p["text"], embedding=vectors.dumps(vectors.embed(f"{p['title']} {p['tags']} {p['text']}"))))
        n += 1
    for path in sorted((get_settings().data_dir / "policies").glob("*.md")):
        for i, section in enumerate(re.split(r"\n(?=## )", path.read_text(encoding="utf-8"))):
            lines = section.strip().splitlines()
            if len(lines) < 2:
                continue
            title = lines[0].lstrip("# ").strip()
            body = "\n".join(lines[1:]).strip()
            session.add(PolicyChunk(tenant_id=tenant_id, doc_id=path.stem, section=str(i), title=title, content=body,
                                    embedding=vectors.dumps(vectors.embed(f"{title} {body}"))))
            n += 1
    session.commit()
    return n


def load_demo(session: Session, tenant_id: int, actor: str, bulk: bool = False) -> dict:
    """Creates every job in data/jobs. The hand-written + attack resumes go to the first job; with bulk=True each
    data/resumes/bulk/<job file stem>/ folder goes to its own job (run scripts/generate_demo_data.py first)."""
    data = get_settings().data_dir
    out = {"jobs": 0, "resumes": [], "emails": 0, "projects": []}
    jobs: dict[str, JobOffer] = {}
    for path in sorted((data / "jobs").glob("*.json")):
        spec = json.loads(path.read_text(encoding="utf-8"))
        job = session.exec(select(JobOffer).where(JobOffer.tenant_id == tenant_id, JobOffer.title == spec["title"])).first()
        if not job:
            criteria = {k: spec.get(k, d) for k, d in (("required_skills", []), ("nice_to_have", []), ("min_years", 0))}
            job = JobOffer(tenant_id=tenant_id, title=spec["title"], description=spec.get("description", ""),
                           criteria=json.dumps(criteria),
                           embedding=vectors.dumps(vectors.embed(f"{spec['title']} {spec.get('description', '')}")))
            session.add(job)
            session.commit()
            out["jobs"] += 1
        jobs[path.stem] = job
    if not jobs:
        return out

    batches = [(next(iter(jobs.values())), sorted(f for f in (data / "resumes").iterdir() if f.is_file()))]
    if bulk:
        for slug, job in jobs.items():
            folder = data / "resumes" / "bulk" / slug
            if folder.is_dir():
                batches.append((job, sorted(folder.iterdir())))
    for job, files in batches:
        for f in files:
            if f.is_file():
                out["resumes"].append({**ingestion.ingest_resume(session, tenant_id, job, f.name, f.read_bytes(),
                                                                 source="demo", actor="seed"),
                                       "job": job.title})

    emails_file = data / "emails" / "sample_emails.json"
    if emails_file.exists():
        for i, m in enumerate(json.loads(emails_file.read_text(encoding="utf-8"))):
            received = datetime.fromisoformat(m["received_at"]) if m.get("received_at") else None
            ingestion.ingest_email(session, tenant_id, sender=m["sender"], subject=m.get("subject", ""), body=m["body"],
                                   external_id=m.get("external_id", f"demo-{i}"), received_at=received, actor=actor)
            out["emails"] += 1
    from backend.orchestration import staffing

    out["projects"] = staffing.load_demo_projects(session, tenant_id, actor)
    return out
