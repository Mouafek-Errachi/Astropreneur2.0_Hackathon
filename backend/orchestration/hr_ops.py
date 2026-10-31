"""HR operations workflows ported from n8n: team broadcasts, employee tickets, points and leaderboard."""
import json
import uuid
from collections import defaultdict

from fastapi import HTTPException
from sqlmodel import Session, func, select

from backend.agents import hr_ops, policy_rag
from backend.data.models import Employee, PointsLedger, PointsRule, ProposedAction, Team, Ticket, utcnow
from backend.orchestration import workflow
from backend.security.guard import inspect_text

BADGES = [(200, "Gold"), (100, "Silver"), (50, "Bronze")]


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


def create_ticket(session: Session, tenant_id: int, employee_id: str, text: str, requested_by: str) -> Ticket:
    emp = session.get(Employee, employee_id)
    if not emp or emp.tenant_id != tenant_id or not emp.active:
        raise HTTPException(403, "FORBIDDEN")
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
