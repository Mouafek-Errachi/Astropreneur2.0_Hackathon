"""HR operations workflows ported from n8n: team broadcasts, employee tickets, points and leaderboard."""
import json
import uuid
from collections import defaultdict

from fastapi import HTTPException
from sqlmodel import Session, func, select

from backend.agents import hr_ops, policy_rag
from backend.data.models import (Employee, EmployeeSkill, PointsLedger, PointsRule, ProposedAction, Team, Ticket,
                                 utcnow)
from backend.orchestration import workflow
from backend.security.guard import inspect_text

BADGES = [(200, "Gold"), (100, "Silver"), (50, "Bronze")]
FORMAL_TONE = ("Formal business email: courteous greeting ('Dear colleagues,'), complete sentences, short clear "
               "paragraphs, no slang or abbreviations, exact dates and times, polite closing signed 'HR Team'.")


def propose_broadcast(session: Session, tenant_id: int, team_id: str, notes: str, requested_by: str) -> ProposedAction:
    team = session.get(Team, team_id)
    if not team or team.tenant_id != tenant_id:
        raise HTTPException(422, "UNKNOWN_TEAM")
    g = inspect_text(session, notes, source="broadcast", tenant_id=tenant_id, max_chars=3000)
    draft, fallback = hr_ops.write_broadcast(team.display_name, team.tone_guide, g.clean)
    flags = [f.to_dict() for f in g.flags]
    if draft.injection_attempt_observed:
        flags.append({"type": "model_observed_injection", "severity": "medium", "snippet": ""})
    return workflow.propose(
        session, tenant_id=tenant_id, type="broadcast", title=f"Broadcast to {team.display_name}",
        payload={"to": team.mailing_list, "subject": draft.subject, "body": draft.body, "missing_info": draft.missing_info,
                 "_source_text": g.clean, "_fact_check": True},  # flags invented numbers / links
        rationale="Rewritten for the team tone" + (" (fallback)" if fallback else ""), input_risk=g.risk_level,
        input_flags=flags, created_by_agent="broadcast_writer", requested_by=requested_by, source_type="broadcast")


def _audience(session: Session, tenant_id: int, titles: set[str], teams: set[str], skills: set[str]) -> list[Employee]:
    """Recipients chosen in code: active employees with one of the job titles or in one of the teams, narrowed to
    those with one of the skills if skills are given. The AI never picks an address. Self-declared skills count
    here (unlike staffing coverage): this only addresses an email, and HR sees the list before it is sent."""
    emps = session.exec(select(Employee).where(Employee.tenant_id == tenant_id, Employee.active == True)).all()  # noqa: E712
    if titles or teams:
        emps = [e for e in emps if e.job_title.lower() in titles or e.team_id in teams]
    if skills:
        known = {s.employee_id for s in session.exec(select(EmployeeSkill).where(
            EmployeeSkill.tenant_id == tenant_id, EmployeeSkill.skill.in_(skills)))}
        emps = [e for e in emps if e.id in known]
    return sorted(emps, key=lambda e: (e.first_name, e.id))


def _plural(title: str) -> str:
    return title if title.endswith("s") else f"{title}s"


def propose_group_email(session: Session, tenant_id: int, request: str, requested_by: str) -> ProposedAction:
    """'Send to all backend engineers that the meeting is tomorrow at 18:00': the AI reads who and what, the code
    finds the people, the AI writes the email, and a second person approves it before anyone receives it."""
    g = inspect_text(session, request, source="group_email", tenant_id=tenant_id, max_chars=3000)
    all_emps = session.exec(select(Employee).where(Employee.tenant_id == tenant_id)).all()
    titles = sorted({e.job_title for e in all_emps if e.job_title})
    teams = {t.id: t.display_name for t in session.exec(select(Team).where(Team.tenant_id == tenant_id))}
    skills = sorted({s.skill for s in session.exec(select(EmployeeSkill).where(EmployeeSkill.tenant_id == tenant_id))})
    aud, fallback = hr_ops.read_audience(g.clean, titles, teams, skills)
    # Only values from the lists count: a model cannot widen the audience with a made-up group.
    by_lower = {t.lower(): t for t in titles}
    picked_titles = {t.lower() for t in aud.job_titles if t.lower() in by_lower}
    picked_teams = {t for t in aud.teams if t in teams}
    picked_skills = {s.lower() for s in aud.skills if s.lower() in skills}
    if not (picked_titles or picked_teams or picked_skills):
        raise HTTPException(422, "NO_AUDIENCE: say who should receive it, with a job title, a team or a skill "
                                 f"(job titles: {', '.join(titles)}; teams: {', '.join(teams.values())})")
    people = _audience(session, tenant_id, picked_titles, picked_teams, picked_skills)
    label = " and ".join([_plural(by_lower[t]) for t in sorted(picked_titles)]
                         + [f"the {teams[t]} team" for t in sorted(picked_teams)]) or "Everyone"
    if picked_skills:
        label += f" with {', '.join(sorted(picked_skills))}"
    if not people:
        raise HTTPException(422, f"EMPTY_AUDIENCE: no active employee matches {label}")
    draft, fb2 = hr_ops.write_broadcast(label, FORMAL_TONE, aud.message or g.clean)
    flags = [f.to_dict() for f in g.flags]
    if aud.injection_attempt_observed or draft.injection_attempt_observed:
        flags.append({"type": "model_observed_injection", "severity": "medium", "snippet": ""})
    recipients = [{"id": e.id, "name": f"{e.first_name} {e.last_initial}.", "email": e.email,
                   "job_title": e.job_title, "team": e.team_id} for e in people]
    return workflow.propose(
        session, tenant_id=tenant_id, type="group_email", title=f"Email to {label} ({len(people)})",
        payload={"to": f"{len(people)} people: {label}", "audience": label, "recipients": recipients,
                 "subject": draft.subject, "body": draft.body, "missing_info": draft.missing_info,
                 "_source_text": g.clean, "_fact_check": True},  # flags figures and links not in the request
        rationale="Recipients found by job title, team or skill; AI-written message"
                  + (" (fallback)" if fallback or fb2 else ""),
        input_risk=g.risk_level, input_flags=flags, created_by_agent="group_email_writer", requested_by=requested_by,
        source_type="group_email")


def create_ticket(session: Session, tenant_id: int, employee_id: str, text: str, requested_by: str) -> Ticket:
    emp = session.get(Employee, employee_id)
    if not emp or emp.tenant_id != tenant_id or not emp.active:
        raise HTTPException(404, "Employee not found or inactive")
    g = inspect_text(session, text, source="ticket", tenant_id=tenant_id, max_chars=3000)
    hits = policy_rag.search(session, tenant_id, g.clean, k=3)
    policy_ctx = "\n".join(f"[{policy_rag.label(c)}] {c.title}: {c.content}" for _, c in hits)
    emp_ctx = f"{emp.first_name} {emp.last_initial}., team {emp.team_id}, contract {emp.contract_type}, leave balance {emp.leave_balance_days} days"
    tri, _ = hr_ops.triage_ticket(g.clean, emp_ctx, policy_ctx, policy_rag.label(hits[0][1]) if hits else "")
    ticket = Ticket(tenant_id=tenant_id, employee_id=emp.id, request_text=g.clean, category=tri.category,
                    priority=tri.priority, risk_level=g.risk_level)
    session.add(ticket)
    session.commit()
    action = workflow.propose(
        session, tenant_id=tenant_id, type="send_email", title=f"[{tri.category}] reply to {emp.first_name} {emp.last_initial}.",
        payload={"to": emp.email, "subject": f"Your HR request ({tri.category})", "body": tri.suggested_response,
                 "_source_text": policy_ctx},
        rationale=tri.hr_action_summary, input_risk=g.risk_level, input_flags=[f.to_dict() for f in g.flags],
        created_by_agent="ticket_triage", requested_by=requested_by, source_type="ticket", source_id=str(ticket.id))
    ticket.action_id = action.id
    session.add(ticket)
    session.commit()
    session.refresh(ticket)
    return ticket


@workflow.on_resume("ticket")
def _after_ticket(session: Session, action: ProposedAction) -> None:
    t = session.get(Ticket, uuid.UUID(action.source_id))
    if t:
        t.status = "answered" if action.status == "executed" else "open"
        session.add(t)


def propose_award(session: Session, tenant_id: int, employee_id: str, action_type: str, note: str,
                  idempotency_key: str, requested_by: str, requester_employee_id: str | None) -> ProposedAction:
    if requester_employee_id and requester_employee_id == employee_id:
        raise HTTPException(403, "SELF_AWARD")
    emp, rule = session.get(Employee, employee_id), session.get(PointsRule, action_type)
    if not emp or emp.tenant_id != tenant_id:
        raise HTTPException(404, "Employee not found")
    if not rule:
        raise HTTPException(422, "Unknown action_type")
    if session.exec(select(PointsLedger).where(PointsLedger.idempotency_key == idempotency_key)).first():
        raise HTTPException(409, "DUPLICATE")
    today = utcnow().date()
    awarded_today = sum(1 for l in session.exec(select(PointsLedger).where(
        PointsLedger.employee_id == employee_id, PointsLedger.action_type == action_type)) if l.awarded_on.date() == today)
    if awarded_today >= rule.daily_cap:
        raise HTTPException(429, "DAILY_CAP")
    return workflow.propose(
        session, tenant_id=tenant_id, type="award_points", title=f"{rule.points} pts to {emp.first_name} ({action_type})",
        payload={"employee_id": employee_id, "action_type": action_type, "points": rule.points, "note": note[:200],
                 "idempotency_key": idempotency_key},
        rationale=note, created_by_agent="points", requested_by=requested_by, source_type="points")


def leaderboard(session: Session, tenant_id: int, period: str | None = None) -> list[dict]:
    period = period or utcnow().strftime("%Y-%m")
    totals: dict[str, int] = defaultdict(int)
    for l in session.exec(select(PointsLedger).where(PointsLedger.tenant_id == tenant_id, PointsLedger.period == period)):
        totals[l.employee_id] += l.points
    rows = []
    for emp_id, pts in sorted(totals.items(), key=lambda x: -x[1]):
        emp = session.get(Employee, emp_id)
        badge = next((b for limit, b in BADGES if pts >= limit), "")
        rows.append({"employee": f"{emp.first_name} {emp.last_initial}.", "team": emp.team_id, "points": pts, "badge": badge})
    for i, r in enumerate(rows, 1):
        r["rank"] = i
    return rows
