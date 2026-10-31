"""Project staffing & skill gap routes. Individual skill levels are personal data: only `skills:read` (managers,
admins) and the employee themself see them; everyone else gets team totals."""
import json
import uuid

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlmodel import Session, select

from backend.config import get_settings
from backend.data.db import get_session
from backend.data.models import (Employee, EmployeeSkill, Project, ProjectAssignment, ProjectDocument, SkillGap)
from backend.orchestration import staffing
from backend.schemas import EmployeeSkillsIn, GapDecision, ProjectCreate, RequirementsIn
from backend.security.gateway import Principal, get_owned, require

router = APIRouter(tags=["project staffing"])


def _uuid(v: str) -> uuid.UUID:
    try:
        return uuid.UUID(v)
    except ValueError:
        raise HTTPException(404, "Not found")


def _gap_out(g: SkillGap, p: Principal) -> dict:
    options = json.loads(g.internal_options)
    if "skills:read" not in p.scopes:  # who exactly has which level stays with managers
        options = [{"kind": o["kind"], "reason": o["reason"]} for o in options]
    return {"id": str(g.id), "skill": g.skill, "level": g.level, "kind": g.kind, "needed_fte": g.needed_fte,
            "covered_fte": g.covered_fte, "gap_fte": g.gap_fte, "internal_options": options, "decision": g.decision,
            "internal_plan": g.internal_plan,
            "decided_by": g.decided_by, "status": g.status, "action_id": str(g.action_id) if g.action_id else None,
            "job_offer_id": str(g.job_offer_id) if g.job_offer_id else None}


def _project_out(session: Session, pr: Project, p: Principal, detail: bool = False) -> dict:
    gaps = session.exec(select(SkillGap).where(SkillGap.project_id == pr.id)).all()
    out = {"id": str(pr.id), "name": pr.name, "summary": pr.summary, "team_id": pr.team_id, "deadline": pr.deadline,
           "requirements_version": pr.requirements_version, "requirements_confirmed": pr.requirements_confirmed,
           "confirmed_by": pr.confirmed_by, "gap_count": len(gaps), "gap_fte": round(sum(g.gap_fte for g in gaps), 2)}
    if detail:
        docs = session.exec(select(ProjectDocument).where(ProjectDocument.project_id == pr.id)).all()
        out["documents"] = [{"id": str(d.id), "filename": d.original_filename, "kind": d.kind, "risk_level": d.risk_level,
                             "flags": json.loads(d.flags), "hidden_text": d.hidden_text[:300]} for d in docs]
        out["requirements"] = [{"skill": r.skill, "level": r.level, "headcount": r.headcount,
                                "allocation_pct": r.allocation_pct, "evidence": r.evidence, "source": r.source}
                               for r in staffing.current_requirements(session, pr)]
        out["gaps"] = [_gap_out(g, p) for g in gaps]
        assignments = session.exec(select(ProjectAssignment).where(ProjectAssignment.project_id == pr.id)).all()
        out["assigned_fte"] = round(sum(a.allocation_pct for a in assignments) / 100, 2)
    return out


@router.get("/projects")
def list_projects(p: Principal = Depends(require("projects:read")), session: Session = Depends(get_session)):
    return [_project_out(session, pr, p) for pr in session.exec(select(Project).where(Project.tenant_id == p.tenant_id))]


@router.post("/projects")
def create_project(body: ProjectCreate, p: Principal = Depends(require("projects:write")),
                   session: Session = Depends(get_session)):
    pr = staffing.create_project(session, p.tenant_id, name=body.name, summary=body.summary, team_id=body.team_id,
                                 deadline=body.deadline, actor=p.subject)
    return _project_out(session, pr, p, detail=True)


@router.get("/projects/{project_id}")
def get_project(project_id: str, p: Principal = Depends(require("projects:read")), session: Session = Depends(get_session)):
    return _project_out(session, get_owned(session, Project, _uuid(project_id), p), p, detail=True)


@router.post("/projects/{project_id}/documents")
async def upload_documents(project_id: str, files: list[UploadFile] = File(...),
                           p: Principal = Depends(require("projects:write")), session: Session = Depends(get_session)):
    """Charters (PDF, DOCX, TXT) and dependency manifests. Uploading re-runs the analysis (a new draft version)."""
    pr = get_owned(session, Project, _uuid(project_id), p)
    s = get_settings()
    if len(files) > 20:
        raise HTTPException(413, "At most 20 documents per upload")
    results = [staffing.add_document(session, pr, f.filename or "file", await f.read(s.max_upload_bytes + 1), p.subject)
               for f in files]
    analysis = staffing.analyze(session, pr, p.subject) if any(r["accepted"] for r in results) else None
    return {"documents": results, "analysis": analysis}


@router.post("/projects/{project_id}/analyze")
def analyze(project_id: str, p: Principal = Depends(require("projects:write")), session: Session = Depends(get_session)):
    """Re-extracts the requirements as a new DRAFT version (n8n may call this on a schedule)."""
    return staffing.analyze(session, get_owned(session, Project, _uuid(project_id), p), p.subject)


@router.put("/projects/{project_id}/requirements")
def confirm_requirements(project_id: str, body: RequirementsIn,
                         p: Principal = Depends(require("projects:write", human_only=True)),
                         session: Session = Depends(get_session)):
    """A human edits and confirms the requirements; the gaps are computed from the confirmed version."""
    pr = get_owned(session, Project, _uuid(project_id), p)
    staffing.confirm_requirements(session, pr, [s.model_dump() for s in body.skills], p.subject)
    return _project_out(session, pr, p, detail=True)


@router.get("/projects/{project_id}/gaps")
def gaps(project_id: str, p: Principal = Depends(require("projects:read")), session: Session = Depends(get_session)):
    pr = get_owned(session, Project, _uuid(project_id), p)
    return [_gap_out(g, p) for g in session.exec(select(SkillGap).where(SkillGap.project_id == pr.id))]


@router.post("/projects/{project_id}/gaps/recompute")
def recompute(project_id: str, p: Principal = Depends(require("projects:write")), session: Session = Depends(get_session)):
    pr = get_owned(session, Project, _uuid(project_id), p)
    return [_gap_out(g, p) for g in staffing.compute_gaps(session, pr)]


def _gap(session: Session, project_id: str, gap_id: str, p: Principal) -> SkillGap:
    g = get_owned(session, SkillGap, _uuid(gap_id), p)
    if g.project_id != _uuid(project_id):
        raise HTTPException(404, "SkillGap not found")
    return g


@router.put("/projects/{project_id}/gaps/{gap_id}")
def decide(project_id: str, gap_id: str, body: GapDecision,
           p: Principal = Depends(require("projects:write", human_only=True)), session: Session = Depends(get_session)):
    gap = _gap(session, project_id, gap_id, p)
    return _gap_out(staffing.set_decision(session, gap, body.decision, p.subject, body.plan), p)


@router.post("/projects/{project_id}/gaps/{gap_id}/job-description")
def job_description(project_id: str, gap_id: str, p: Principal = Depends(require("projects:write", human_only=True)),
                    session: Session = Depends(get_session)):
    """Drafts the job description and queues `create_job_offer`; someone else must approve it."""
    a = staffing.propose_job_description(session, _gap(session, project_id, gap_id, p), p.subject)
    return {"action_id": str(a.id), "status": a.status, "risk_level": a.risk_level, "payload": json.loads(a.payload),
            "flags": json.loads(a.risk_flags)}


# ---------------------------------------------------------------- skill matrix and employee skills
@router.get("/skills/matrix")
def skill_matrix(team: str | None = None, p: Principal = Depends(require("projects:read")),
                 session: Session = Depends(get_session)):
    return staffing.matrix(session, p.tenant_id, team, individual="skills:read" in p.scopes)


def _employee(session: Session, employee_id: str, p: Principal) -> Employee:
    emp = session.get(Employee, employee_id)
    if not emp or emp.tenant_id != p.tenant_id:
        raise HTTPException(404, "Employee not found")
    return emp


def _skills_out(session: Session, employee_id: str) -> list[dict]:
    return [{"skill": es.skill, "level": es.level, "validated": es.validated, "source": es.source,
             "last_confirmed": es.last_confirmed}
            for es in session.exec(select(EmployeeSkill).where(EmployeeSkill.employee_id == employee_id))]


@router.get("/employees/{employee_id}/skills")
def employee_skills(employee_id: str, p: Principal = Depends(require()), session: Session = Depends(get_session)):
    if employee_id == "me":
        employee_id = p.employee_id or ""
    emp = _employee(session, employee_id, p)
    if p.employee_id != emp.id and "skills:read" not in p.scopes:
        raise HTTPException(403, "Only the employee and their managers can see individual skill levels")
    return {"employee_id": emp.id, "name": f"{emp.first_name} {emp.last_initial}.", "skills": _skills_out(session, emp.id)}


@router.put("/employees/{employee_id}/skills")
def set_employee_skills(employee_id: str, body: EmployeeSkillsIn, p: Principal = Depends(require(human_only=True)),
                        session: Session = Depends(get_session)):
    """Employees declare their own skills (unvalidated until a manager confirms); managers add and validate."""
    if employee_id == "me":
        employee_id = p.employee_id or ""
    emp = _employee(session, employee_id, p)
    as_manager = "skills:manage" in p.scopes
    if p.employee_id != emp.id and not as_manager:
        raise HTTPException(403, "Only the employee and their managers can change these skills")
    staffing.set_employee_skills(session, p.tenant_id, emp.id, [s.model_dump() for s in body.skills], p.subject,
                                 as_manager=as_manager)
    return {"employee_id": emp.id, "skills": _skills_out(session, emp.id)}
