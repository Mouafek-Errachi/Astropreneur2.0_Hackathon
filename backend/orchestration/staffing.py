"""Project staffing workflow (docs/specs/project-staffing-gap-analysis.md).

upload documents (file guard) -> extract requirements (LLM, draft version) -> human confirms -> compute gaps (code)
-> internal options first -> human marks a gap "hire externally" -> job description (LLM + checks) -> PENDING
create_job_offer -> a second person approves -> the executor creates a JobOffer that screening can rank against.
"""
import hashlib
import json
import re
import uuid
from collections import defaultdict

import magic
from fastapi import HTTPException
from sqlmodel import Session, select

from backend.agents import jd_writer, project_analyzer
from backend.config import get_settings
from backend.data import storage, vectors
from backend.data.models import (Employee, EmployeeSkill, Project, ProjectAssignment, ProjectDocument,
                                 ProjectSkillRequirement, ProposedAction, Skill, SkillGap, Team, utcnow)
from backend.orchestration import workflow
from backend.security import audit, events
from backend.security.guard import inspect_text
from backend.security.guard.files import FileRejected, extract
from backend.security.guard.injection import risk_level
from backend.security.guard.sanitizer import safe_display_filename
from backend.security.validator import check_job_description

MANIFESTS = {"requirements.txt", "package.json", "pom.xml", "go.mod", "dockerfile", "docker-compose.yml",
             "docker-compose.yaml", "pyproject.toml"}
MAX_MANIFEST_BYTES = 200_000
# Container base images and what they say about the stack.
IMAGES = {"python": "python", "postgres": "sql", "mysql": "sql", "redis": "redis", "node": "javascript",
          "openjdk": "java", "eclipse-temurin": "java", "bitnami/spark": "spark", "apache/airflow": "airflow"}
PRESENCE = {"requirements.txt": "python", "pyproject.toml": "python", "package.json": "javascript", "pom.xml": "java",
            "dockerfile": "docker", "docker-compose.yml": "docker", "docker-compose.yaml": "docker"}


# ------------------------------------------------------------------ taxonomy
def taxonomy(session: Session) -> dict[str, dict]:
    rows = session.exec(select(Skill)).all()
    if rows:
        return {r.name: {"synonyms": json.loads(r.synonyms), "related": json.loads(r.related),
                         "packages": json.loads(r.packages)} for r in rows}
    path = get_settings().data_dir / "skills" / "taxonomy.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def seed_taxonomy(session: Session) -> None:
    path = get_settings().data_dir / "skills" / "taxonomy.json"
    if not path.exists() or session.exec(select(Skill)).first():
        return
    for name, t in json.loads(path.read_text(encoding="utf-8")).items():
        session.add(Skill(name=name, synonyms=json.dumps(t["synonyms"]), related=json.dumps(t["related"]),
                          packages=json.dumps(t["packages"])))
    session.commit()


def normalize(name: str, tax: dict[str, dict]) -> str:
    n = " ".join(name.lower().split())
    for canon, t in tax.items():
        if n == canon or n in t["synonyms"]:
            return canon
    return n


# ------------------------------------------------------------------ projects and documents
def create_project(session: Session, tenant_id: int, *, name: str, summary: str, team_id: str | None, deadline: str,
                   actor: str) -> Project:
    if team_id and not session.get(Team, team_id):
        raise HTTPException(422, "Unknown team")
    p = Project(tenant_id=tenant_id, name=name, summary=summary, team_id=team_id, deadline=deadline)
    session.add(p)
    session.commit()
    session.refresh(p)
    audit.log(session, actor=actor, event="project.created", tenant_id=tenant_id, target=f"project:{p.id}")
    return p


def _manifest_text(data: bytes) -> str:
    """Manifests are read as plain text, never executed. Binary or oversized files are refused."""
    if len(data) > MAX_MANIFEST_BYTES:
        raise FileRejected("manifest larger than 200 KB")
    if b"\x00" in data:
        raise FileRejected("manifest is not a text file")
    mime = magic.from_buffer(data[:4096], mime=True)
    if not (mime.startswith("text/") or mime in ("application/json", "application/xml", "inode/x-empty")):
        raise FileRejected(f"manifest type not allowed: {mime}")
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        raise FileRejected("manifest must be UTF-8 text")


def add_document(session: Session, project: Project, filename: str, data: bytes, actor: str) -> dict:
    name = safe_display_filename(filename)
    is_manifest = name.lower() in MANIFESTS or name.lower().endswith(".tf")
    sha = hashlib.sha256(data).hexdigest()
    if session.exec(select(ProjectDocument).where(ProjectDocument.project_id == project.id,
                                                  ProjectDocument.sha256 == sha)).first():
        return {"filename": name, "accepted": False, "error": "duplicate document for this project"}
    try:
        if is_manifest:
            text, hidden, notes, stored = _manifest_text(data), "", [], ""
        else:
            qpath = storage.quarantine(data)
            try:
                doc = extract(data)
            except FileRejected:
                storage.discard(qpath)
                raise
            text, hidden, notes, stored = doc.text, doc.hidden_text, doc.notes, storage.promote(qpath, doc.mime)
    except FileRejected as e:
        events.record(session, tenant_id=project.tenant_id, source="file", kind="blocked_file", severity="medium",
                      detail={"filename": name, "reason": str(e), "project": str(project.id)})
        return {"filename": name, "accepted": False, "error": str(e)}
    d = ProjectDocument(tenant_id=project.tenant_id, project_id=project.id, kind="manifest" if is_manifest else "charter",
                        original_filename=name, stored_filename=stored, sha256=sha, text=text, hidden_text=hidden,
                        embedding=vectors.dumps(vectors.embed(text[:8000])))
    session.add(d)
    session.flush()
    g = inspect_text(session, text, source="project_document", source_id=str(d.id), tenant_id=project.tenant_id,
                     hidden_text=hidden, sanitizer_notes=notes, already_clean=not is_manifest)
    d.flags, d.risk_level = g.flags_json, g.risk_level
    project.updated_at = utcnow()
    session.add(d)
    session.add(project)
    session.commit()
    audit.log(session, actor=actor, event="project.document_added", tenant_id=project.tenant_id,
              target=f"project:{project.id}", detail={"file": name, "kind": d.kind, "risk": g.risk_level})
    return {"filename": name, "accepted": True, "kind": d.kind, "risk_level": g.risk_level,
            "flags": [f.type for f in g.flags], "hidden_text_found": bool(hidden.strip())}


def manifest_skills(filename: str, text: str, tax: dict[str, dict]) -> list[dict]:
    """Deterministic: dependency names -> skills through the taxonomy's package lists. Nothing is executed."""
    low_name = filename.lower()
    packages: list[str] = []
    if low_name in ("requirements.txt",):
        packages = [re.split(r"[<>=!~\[;\s]", l.strip(), maxsplit=1)[0] for l in text.splitlines()
                    if l.strip() and not l.strip().startswith(("#", "-"))]
    elif low_name == "package.json":
        try:
            pkg = json.loads(text)
            packages = [k for sec in ("dependencies", "devDependencies") for k in (pkg.get(sec) or {})]
        except (json.JSONDecodeError, AttributeError):
            packages = []
    elif low_name == "pom.xml":
        packages = re.findall(r"<artifactId>\s*([\w.\-]+)\s*</artifactId>", text)
    elif low_name in ("go.mod", "pyproject.toml"):
        packages = re.findall(r"([\w.\-/]+)\s*(?:[<>=~^]|v\d)", text)
    elif low_name.startswith("dockerfile") or low_name.startswith("docker-compose"):
        images = re.findall(r"^\s*(?:FROM|image:)\s*([\w./\-]+)", text, re.I | re.M)
        packages = [IMAGES[i.lower()] for i in images if i.lower() in IMAGES]
    by_package = {pk.lower(): canon for canon, t in tax.items() for pk in t["packages"]}
    found: dict[str, str] = {}
    if skill := PRESENCE.get(low_name) or ("terraform" if low_name.endswith(".tf") else None):
        found[skill] = f"{filename} present"
    for pk in packages:
        pkl = pk.lower().strip()
        skill = by_package.get(pkl) or (pkl if pkl in tax else None)
        if skill and skill not in found:
            found[skill] = f"{filename}: {pk}"
    return [{"skill": s, "level": 2, "headcount": 1, "allocation_pct": 50, "evidence": ev[:200], "source": "manifest"}
            for s, ev in found.items()]


# ------------------------------------------------------------------ requirements (versioned)
def current_requirements(session: Session, project: Project) -> list[ProjectSkillRequirement]:
    return list(session.exec(select(ProjectSkillRequirement).where(
        ProjectSkillRequirement.project_id == project.id,
        ProjectSkillRequirement.version == project.requirements_version)).all())


def _write_version(session: Session, project: Project, items: list[dict], *, confirmed: bool, actor: str) -> None:
    project.requirements_version += 1
    project.requirements_confirmed, project.confirmed_by = confirmed, actor if confirmed else ""
    project.updated_at = utcnow()
    for it in items:
        session.add(ProjectSkillRequirement(
            tenant_id=project.tenant_id, project_id=project.id, version=project.requirements_version,
            skill=it["skill"], level=it["level"], headcount=it["headcount"], allocation_pct=it["allocation_pct"],
            evidence=it.get("evidence", "")[:200], source=it.get("source", "human")))
    session.add(project)
    session.commit()


def analyze(session: Session, project: Project, actor: str) -> dict:
    """Extracts a DRAFT version of the requirements from the uploaded documents AND the project's own name and
    summary, so a project without documents still gets requirements. Nothing is used for gaps until a human confirms."""
    tax = taxonomy(session)
    vocab = {k: v["synonyms"] for k, v in tax.items()}
    docs = session.exec(select(ProjectDocument).where(ProjectDocument.project_id == project.id)).all()
    charter = "\n\n".join(d.text for d in docs if d.kind == "charter")  # visible text only, hidden text never sent
    description = f"{project.name}\n{project.summary}".strip()
    needs: dict[str, dict] = {}
    model_flag, fallback = False, False

    def extract(text: str, source: str) -> None:
        nonlocal model_flag, fallback
        extracted, used_fallback = project_analyzer.extract_requirements(text, vocab)
        model_flag, fallback = model_flag or extracted.injection_attempt_observed, fallback or used_fallback
        for n in extracted.skills:
            skill = normalize(n.skill, tax)
            needs.setdefault(skill, {**n.model_dump(), "skill": skill, "source": source})

    # Most detailed source first: charter, then manifests, then the project description fills in what they miss.
    if charter.strip():
        extract(charter, "llm")
    for d in docs:
        if d.kind == "manifest":
            for n in manifest_skills(d.original_filename, d.text, tax):
                needs.setdefault(n["skill"], n)
    if description:
        extract(description, "project")
    _write_version(session, project, list(needs.values()), confirmed=False, actor=actor)
    if model_flag:
        events.record(session, tenant_id=project.tenant_id, source="project_document", source_id=str(project.id),
                      kind="injection", severity="medium", detail={"model_observed_injection": True})
    audit.log(session, actor=actor, event="project.analyzed", tenant_id=project.tenant_id, target=f"project:{project.id}",
              detail={"version": project.requirements_version, "skills": len(needs), "llm_fallback": fallback})
    return {"version": project.requirements_version, "skills": len(needs), "llm_fallback": fallback}


def confirm_requirements(session: Session, project: Project, items: list[dict], actor: str) -> list[SkillGap]:
    """A human edits and confirms the requirements: a new version, then the gaps are computed from it."""
    tax = taxonomy(session)
    merged: dict[str, dict] = {}
    for it in items:
        skill = normalize(it["skill"], tax)
        if skill:
            merged[skill] = {**it, "skill": skill, "source": it.get("source") or "human"}
    _write_version(session, project, list(merged.values()), confirmed=True, actor=actor)
    audit.log(session, actor=actor, event="project.requirements_confirmed", tenant_id=project.tenant_id,
              target=f"project:{project.id}", detail={"version": project.requirements_version, "skills": sorted(merged)})
    return compute_gaps(session, project)


# ------------------------------------------------------------------ gaps (pure code)
def _free_pct(session: Session, tenant_id: int, employee_id: str, project_id: uuid.UUID) -> int:
    """Capacity this employee can give to the project: 100% minus what other projects already take."""
    other = sum(a.allocation_pct for a in session.exec(select(ProjectAssignment).where(
        ProjectAssignment.tenant_id == tenant_id, ProjectAssignment.employee_id == employee_id,
        ProjectAssignment.project_id != project_id)))
    return max(0, 100 - other)


def compute_gaps(session: Session, project: Project) -> list[SkillGap]:
    """Coverage per required skill = validated holders at or above the level, limited by their free capacity.
    Self-declared (unvalidated) skills never count. A person can cover several skills of the same project."""
    if not project.requirements_confirmed:
        raise HTTPException(409, "Confirm the project requirements before computing gaps")
    tax = taxonomy(session)
    tid = project.tenant_id
    active = {e.id: e for e in session.exec(select(Employee).where(Employee.tenant_id == tid, Employee.active == True))}  # noqa: E712
    skills = defaultdict(list)
    for es in session.exec(select(EmployeeSkill).where(EmployeeSkill.tenant_id == tid)):
        if es.employee_id in active:
            skills[es.skill].append(es)
    # Gaps are updated in place (stable ids): pending job descriptions and the UI keep pointing at the same row.
    previous = {g.skill: g for g in session.exec(select(SkillGap).where(SkillGap.project_id == project.id))}
    gaps = []
    for req in current_requirements(session, project):
        needed = round(req.headcount * req.allocation_pct / 100, 2)
        holders = [es for es in skills[req.skill] if es.validated]
        covered = 0.0
        options = []
        for es in sorted(holders, key=lambda e: (-e.level, e.employee_id)):
            free = _free_pct(session, tid, es.employee_id, project.id)
            if es.level >= req.level:
                give = min(free, req.allocation_pct) / 100
                covered += give
                if give < req.allocation_pct / 100:
                    options.append({"employee_id": es.employee_id, "level": es.level, "kind": "rebalance",
                                    "reason": f"has {req.skill} at level {es.level} but only {free}% free"})
            elif es.level == req.level - 1 and free > 0:
                options.append({"employee_id": es.employee_id, "level": es.level, "kind": "upskill",
                                "reason": f"{req.skill} at level {es.level}, one below the level asked"})
        for es in skills[req.skill]:
            if not es.validated and es.level >= req.level:
                options.append({"employee_id": es.employee_id, "level": es.level, "kind": "validate",
                                "reason": f"{req.skill} level {es.level} recorded, not validated by a manager yet"})
        for rel in tax.get(req.skill, {}).get("related", []):
            for es in skills[rel]:
                if es.validated and es.level >= 2 and all(o["employee_id"] != es.employee_id for o in options):
                    options.append({"employee_id": es.employee_id, "level": es.level, "kind": "related",
                                    "reason": f"knows the related skill {rel} (level {es.level})"})
        covered = round(min(covered, needed), 2)
        if covered >= needed:
            continue
        kind = ("missing" if not holders else
                "insufficient_level" if all(h.level < req.level for h in holders) else "insufficient_capacity")
        # human decisions and hiring links survive a recompute: only the numbers and options are refreshed
        gap = previous.pop(req.skill, None) or SkillGap(tenant_id=tid, project_id=project.id, skill=req.skill, level=req.level,
                                                        needed_fte=0, covered_fte=0, gap_fte=0, kind=kind)
        gap.level, gap.needed_fte, gap.covered_fte = req.level, needed, covered
        gap.gap_fte, gap.kind, gap.internal_options = round(needed - covered, 2), kind, json.dumps(options[:8])
        session.add(gap)
        gaps.append(gap)
    for closed in previous.values():  # covered now, or no longer required
        if closed.status in ("jd_proposed", "hiring"):
            closed.gap_fte, closed.covered_fte = 0, closed.needed_fte  # keep the row: a job offer depends on it
            session.add(closed)
        else:
            session.delete(closed)
    session.commit()
    for g in gaps:
        session.refresh(g)
    return gaps


def set_decision(session: Session, gap: SkillGap, decision: str, actor: str, plan: str = "") -> SkillGap:
    if gap.status in ("jd_proposed", "hiring") and decision != "hire_externally":
        raise HTTPException(409, "A job description is already in progress for this gap")
    if decision == "internal" and not plan.strip():
        raise HTTPException(422, "Say how the gap is covered internally (pick an internal option or write a plan)")
    gap.decision, gap.decided_by = decision, actor
    gap.internal_plan = plan.strip()[:300] if decision == "internal" else ""
    session.add(gap)
    session.commit()
    audit.log(session, actor=actor, event="skill_gap.decided", tenant_id=gap.tenant_id, target=f"gap:{gap.id}",
              detail={"skill": gap.skill, "decision": decision, "plan": gap.internal_plan})
    session.refresh(gap)
    return gap


# ------------------------------------------------------------------ job description for a gap
def propose_job_description(session: Session, gap: SkillGap, actor: str) -> ProposedAction:
    if gap.decision != "hire_externally":
        raise HTTPException(409, "Review the internal options first, then mark this gap 'hire externally'")
    if gap.status != "open":
        raise HTTPException(409, f"This gap already has a job description ({gap.status})")
    project = session.get(Project, gap.project_id)
    team = session.get(Team, project.team_id) if project.team_id else None
    others = [r.skill for r in current_requirements(session, project) if r.skill != gap.skill]
    f = jd_writer.facts(project.name, project.summary, gap.skill, gap.level, others,
                        team.display_name if team else "engineering", team.tone_guide if team else "")
    names = [f"{e.first_name} {e.last_initial}." for e in session.exec(select(Employee).where(Employee.tenant_id == gap.tenant_id))]
    names += [e.first_name for e in session.exec(select(Employee).where(Employee.tenant_id == gap.tenant_id))]
    draft, fallback = jd_writer.write_job_description(f)
    flags = check_job_description(draft, {gap.skill}, set(others), names)
    if any(fl.severity == "high" for fl in flags):  # the AI's draft broke a rule: use the checked template instead
        events.record(session, tenant_id=gap.tenant_id, source="output", source_id=str(gap.id), kind="output_violation",
                      severity="high", detail={"job_description_flags": [fl.to_dict() for fl in flags][:10]})
        draft = jd_writer.JobDescriptionDraft.model_validate(jd_writer.template(f))
        flags = check_job_description(draft, {gap.skill}, set(others), names)
    level = risk_level(flags)
    action = workflow.propose(
        session, tenant_id=gap.tenant_id, type="create_job_offer", title=f"Open position: {draft.title} ({project.name})",
        payload={"title": draft.title, "summary": draft.summary, "responsibilities": draft.responsibilities,
                 "body": draft.body, "project_id": str(project.id), "gap_id": str(gap.id),
                 "criteria": {"required_skills": draft.required_skills, "nice_to_have": draft.nice_to_have,
                              "min_years": draft.min_years}},
        rationale=f"{gap.kind.replace('_', ' ')}: {gap.gap_fte:g} FTE of {gap.skill} (level {gap.level}) missing"
                  + (" (template: the AI did not answer)" if fallback else ""),
        input_risk=level, input_flags=[fl.to_dict() for fl in flags], created_by_agent="jd_writer",
        requested_by=actor, source_type="skill_gap", source_id=str(gap.id))
    gap.status, gap.action_id = "jd_proposed", action.id
    session.add(gap)
    session.commit()
    return action


@workflow.on_resume("skill_gap")
def _after_job_offer(session: Session, action: ProposedAction) -> None:
    gap = session.get(SkillGap, uuid.UUID(action.source_id))
    if not gap:
        return
    if action.status == "executed" and (m := re.search(r"job_offer_id=([0-9a-f-]{36})", action.result)):
        gap.status, gap.job_offer_id = "hiring", uuid.UUID(m.group(1))
    elif action.status in ("rejected", "failed", "cancelled"):
        gap.status, gap.action_id = "open", None
    session.add(gap)


# ------------------------------------------------------------------ skill matrix
def matrix(session: Session, tenant_id: int, team_id: str | None, individual: bool) -> dict:
    """Aggregated coverage per skill (validated holders per level). Individual levels only for skills:read."""
    emps = {e.id: e for e in session.exec(select(Employee).where(Employee.tenant_id == tenant_id, Employee.active == True))  # noqa: E712
            if not team_id or e.team_id == team_id}
    rows: dict[str, dict] = {}
    people: dict[str, list] = defaultdict(list)
    for es in session.exec(select(EmployeeSkill).where(EmployeeSkill.tenant_id == tenant_id)):
        if es.employee_id not in emps:
            continue
        r = rows.setdefault(es.skill, {"skill": es.skill, "level_1": 0, "level_2": 0, "level_3": 0, "level_4": 0,
                                       "unvalidated": 0})
        if es.validated:
            r[f"level_{es.level}"] += 1
        else:
            r["unvalidated"] += 1
        if individual:
            e = emps[es.employee_id]
            people[es.skill].append({"employee_id": e.id, "name": f"{e.first_name} {e.last_initial}.",
                                     "team": e.team_id, "level": es.level, "validated": es.validated})
    out = sorted(rows.values(), key=lambda r: r["skill"])
    if individual:
        for r in out:
            r["people"] = people[r["skill"]]
    return {"team": team_id, "individual": individual, "skills": out}


# ------------------------------------------------------------------ employee skills
def seed_employee_skills(session: Session, tenant_id: int) -> None:
    path = get_settings().data_dir / "employees" / "employee_skills.csv"
    if not path.exists() or session.exec(select(EmployeeSkill)).first():
        return
    import csv

    with path.open(encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            if not session.get(Employee, row["employee_id"]):
                continue
            validated = row["validated"].strip().lower() == "true"
            session.add(EmployeeSkill(tenant_id=tenant_id, employee_id=row["employee_id"], skill=row["skill"].lower(),
                                      level=int(row["level"]), source=row["source"], validated=validated,
                                      validated_by="seed" if validated else ""))
    session.commit()


def set_employee_skills(session: Session, tenant_id: int, employee_id: str, items: list[dict],
                        actor: str) -> list[EmployeeSkill]:
    """A manager adds, changes or validates skills. Upsert by skill; unvalidated skills never cover a gap."""
    tax = taxonomy(session)
    existing = {es.skill: es for es in session.exec(select(EmployeeSkill).where(EmployeeSkill.employee_id == employee_id))}
    for it in items:
        skill = normalize(it["skill"], tax)
        es = existing.get(skill) or EmployeeSkill(tenant_id=tenant_id, employee_id=employee_id, skill=skill)
        es.level = int(it["level"])
        es.last_confirmed = utcnow()
        validated = bool(it.get("validated", True))
        es.source, es.validated, es.validated_by = "manager", validated, actor if validated else ""
        session.add(es)
    session.commit()
    audit.log(session, actor=actor, event="employee.skills_updated", tenant_id=tenant_id, target=f"employee:{employee_id}",
              detail={"skills": [i["skill"] for i in items]})
    return list(session.exec(select(EmployeeSkill).where(EmployeeSkill.employee_id == employee_id)))


# ------------------------------------------------------------------ demo projects
def load_demo_projects(session: Session, tenant_id: int, actor: str) -> list[dict]:
    """data/projects/<slug>/: project.json (+ assignments) and its charter and manifests. Requirements stay as a
    draft: confirming them is the human step shown in the demo."""
    out = []
    for folder in sorted(p for p in (get_settings().data_dir / "projects").iterdir() if p.is_dir()):
        spec = json.loads((folder / "project.json").read_text(encoding="utf-8"))
        if session.exec(select(Project).where(Project.tenant_id == tenant_id, Project.name == spec["name"])).first():
            continue
        project = create_project(session, tenant_id, name=spec["name"], summary=spec.get("summary", ""),
                                 team_id=spec.get("team_id"), deadline=spec.get("deadline", ""), actor=actor)
        for a in spec.get("assignments", []):
            session.add(ProjectAssignment(tenant_id=tenant_id, project_id=project.id, employee_id=a["employee_id"],
                                          allocation_pct=a["allocation_pct"]))
        session.commit()
        docs = [add_document(session, project, f.name, f.read_bytes(), actor)
                for f in sorted(folder.iterdir()) if f.is_file() and f.name != "project.json"]
        analyze(session, project, actor)
        out.append({"project": project.name, "documents": docs})
    return out

