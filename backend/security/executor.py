"""L5 Action Executor: the ONLY component that holds SMTP / calendar credentials and the ONLY
code path that sends or records anything.

Flow (docs/architecture/04-hitl-sequence.mmd):
  human approves -> signed Approval(payload_hash) -> verify signature + role + status == pending
  -> execute -> SentEmail / DB change -> hash-chained audit -> resume the workflow hook.
"""
import hashlib
import hmac
import json
import secrets
import smtplib
import uuid
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage as MimeMessage
from email.utils import make_msgid

from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from backend.config import get_settings
from backend.data.models import (Application, Approval, Candidate, JobOffer, PointsLedger, PointsRule, ProposedAction,
                                 SchedulingToken, SentEmail, utcnow)
from backend.agents.drafter import INVITE_WHEN
from backend.security import audit
from backend.security.gateway import Principal
from backend.security.validator import check_outgoing_text

FOUR_EYES_ALWAYS = {"broadcast", "group_email", "create_job_offer"}  # email to many people; opening a position
FOUR_EYES_DECISIONS = {"record_decision", "apply_weights"}  # decisions about people and how they are scored


# ------------------------------------------------------------------ signing
def payload_hash(payload: str) -> str:
    return hashlib.sha256(payload.encode()).hexdigest()


def _sig_material(a: Approval) -> bytes:
    return "|".join([str(a.action_id), a.decision, str(a.reviewer_id), a.payload_hash,
                     a.decided_at.replace(tzinfo=None).isoformat(timespec="seconds")]).encode()


def sign(a: Approval) -> str:
    return hmac.new(get_settings().approval_signing_secret.encode(), _sig_material(a), hashlib.sha256).hexdigest()


def verify(a: Approval, action: ProposedAction) -> bool:
    return (hmac.compare_digest(a.signature, sign(a))
            and a.payload_hash == payload_hash(action.payload)
            and a.action_id == action.id and a.tenant_id == action.tenant_id)


# ------------------------------------------------------------------ decisions
def _decide(session: Session, action: ProposedAction, reviewer: Principal, decision: str, note: str) -> Approval:
    if not reviewer.is_human or reviewer.user_id is None:
        raise HTTPException(403, "Only a human reviewer can decide")
    if action.status != "pending":
        raise HTTPException(409, f"ALREADY_DECIDED: action is {action.status}")
    approval = Approval(tenant_id=action.tenant_id, action_id=action.id, reviewer_id=reviewer.user_id,
                        reviewer_email=reviewer.subject, decision=decision, note=note[:1000],
                        payload_hash=payload_hash(action.payload), decided_at=utcnow().replace(microsecond=0),
                        signature="")
    approval.signature = sign(approval)
    session.add(approval)
    return approval


def _commit_decision(session: Session) -> None:
    try:
        session.commit()
    except IntegrityError:  # another reviewer decided this action at the same moment
        session.rollback()
        raise HTTPException(409, "ALREADY_DECIDED: another reviewer decided this action first")


def _check_slot(session: Session, tenant_id: int, payload: dict, i: int) -> int:
    """The recruiter picks one of the proposed slots at approval time: it must still be free."""
    from backend.orchestration import scheduling  # lazy: orchestration depends on security, not the reverse

    slots = payload.get("slots", [])
    if not 0 <= i < len(slots):
        raise HTTPException(400, f"selected_slot must be between 0 and {len(slots) - 1}")
    if not scheduling.is_free(session, tenant_id, payload["interviewer"], slots[i]["start"], slots[i]["end"]):
        raise HTTPException(409, "This slot is no longer free. Reject this proposal and propose new slots.")
    return i


def approve(session: Session, action: ProposedAction, reviewer: Principal, *, note: str = "",
            edits: dict | None = None, acknowledge_risk: bool = False) -> ProposedAction:
    if action.status != "pending":
        raise HTTPException(409, f"ALREADY_DECIDED: action is {action.status}")
    if action.risk_level == "high" and not acknowledge_risk:
        raise HTTPException(400, "HIGH_RISK: review the flags and set acknowledge_risk=true")
    if action.requested_by == reviewer.subject and (
            action.type in FOUR_EYES_ALWAYS or (action.type in FOUR_EYES_DECISIONS and get_settings().four_eyes_decisions)):
        raise HTTPException(403, "FOUR_EYES: the person who requested this action cannot approve it")
    if edits:
        payload = json.loads(action.payload)
        for k in ("subject", "body"):  # the recipient can never be edited
            if edits.get(k) is not None:
                payload[k] = edits[k]
        if edits.get("selected_slot") is not None and action.type == "schedule_interview":
            payload["selected_slot"] = _check_slot(session, action.tenant_id, payload, int(edits["selected_slot"]))
        if edits.get("excluded_recipients") and action.type == "group_email":  # people can be removed, never added
            kept = [r for r in payload["recipients"] if r["id"] not in set(edits["excluded_recipients"])]
            if not kept:
                raise HTTPException(400, "NO_RECIPIENTS: keep at least one recipient, or reject the email")
            payload["recipients"], payload["to"] = kept, f"{len(kept)} people: {payload['audience']}"
        action.payload = json.dumps(payload)
        audit.log(session, actor=reviewer.subject, event="action.edited_by_human", tenant_id=action.tenant_id,
                  target=f"action:{action.id}", commit=False)
    approval = _decide(session, action, reviewer, "approve", note)
    _commit_decision(session)
    _execute(session, action, approval)
    audit.log(session, actor=reviewer.subject, event="action.approved", tenant_id=action.tenant_id,
              target=f"action:{action.id}", detail={"approval": str(approval.id), "result": action.result})
    session.refresh(action)
    return action


def reject(session: Session, action: ProposedAction, reviewer: Principal, note: str = "") -> ProposedAction:
    _decide(session, action, reviewer, "reject", note)
    action.status = "rejected"
    session.add(action)
    _commit_decision(session)
    audit.log(session, actor=reviewer.subject, event="action.rejected", tenant_id=action.tenant_id,
              target=f"action:{action.id}", detail={"note": note[:200]})
    _resume(session, action)
    session.refresh(action)
    return action


# ------------------------------------------------------------------ execution
def _send_email(session: Session, action: ProposedAction, to: str, subject: str, body: str) -> str:
    s = get_settings()
    message_id = make_msgid(domain=s.smtp_from.split("@")[-1])
    transport = "outbox"
    if s.smtp_host:
        msg = MimeMessage()
        msg["From"], msg["To"], msg["Subject"], msg["Message-ID"] = s.smtp_from, to, subject, message_id
        msg.set_content(body)
        with smtplib.SMTP(s.smtp_host, s.smtp_port, timeout=10) as smtp:
            smtp.send_message(msg)
        transport = "smtp"
    session.add(SentEmail(tenant_id=action.tenant_id, action_id=action.id, to=to, subject=subject, body=body,
                          transport=transport, message_id=message_id))
    # Remember when we last wrote to this person: candidate follow-ups are computed from it.
    for cand in session.exec(select(Candidate).where(Candidate.tenant_id == action.tenant_id, Candidate.email == to.lower())):
        cand.last_contact_at = utcnow()
        session.add(cand)
    return f"email to {to} via {transport} ({message_id})"


def _execute(session: Session, action: ProposedAction, approval: Approval) -> None:
    if not verify(approval, action):  # payload changed after approval, or forged approval
        action.status, action.result = "failed", "approval signature verification failed"
        session.add(action)
        session.commit()
        audit.log(session, actor="executor", event="action.signature_failed", tenant_id=action.tenant_id,
                  target=f"action:{action.id}")
        return
    p = json.loads(action.payload)
    try:
        if action.type in ("send_email", "send_reminder", "broadcast"):
            action.result = _send_email(session, action, p["to"], p["subject"], p["body"])
        elif action.type == "group_email":  # one email each: recipients never see each other's address
            for r in p["recipients"]:
                _send_email(session, action, r["email"], p["subject"], p["body"])
            action.result = f"{len(p['recipients'])} emails sent to {p['audience']}"
        elif action.type == "record_decision":
            app = session.get(Application, uuid.UUID(p["application_id"]))
            cand = session.get(Candidate, app.candidate_id) if app else None
            if not app or app.tenant_id != action.tenant_id:
                raise ValueError("application not found")
            app.status, cand.stage = p["decision"], p["decision"]
            session.add(app)
            session.add(cand)
            action.result = f"application {app.id} -> {p['decision']}"
        elif action.type == "schedule_interview":
            from backend.orchestration import scheduling

            if p.get("selected_slot") is not None:  # the recruiter picked the slot: book it now
                slot = p["slots"][int(p["selected_slot"])]
                when = scheduling.book(session, action.tenant_id, uuid.UUID(p["candidate_id"]), p["interviewer"], slot)
                body = p["body"].replace(INVITE_WHEN, f"Your interview is booked for {when} with {p['interviewer']}. "
                                                      "Reply to this email if this time does not suit you.")
                action.result = f"interview booked {when}; " + _send_email(session, action, p["to"], p["subject"], body)
            else:  # the candidate picks: single-use magic link
                token = SchedulingToken(jti=secrets.token_urlsafe(32), tenant_id=action.tenant_id,
                                        candidate_id=uuid.UUID(p["candidate_id"]), action_id=action.id,
                                        interviewer=p["interviewer"], slots=json.dumps(p["slots"]),
                                        expires_at=utcnow() + timedelta(hours=get_settings().scheduling_token_hours))
                session.add(token)
                link = f"{get_settings().public_base_url}/public/schedule/{token.jti}"
                action.result = _send_email(session, action, p["to"], p["subject"], p["body"].replace("{link}", link))
        elif action.type == "apply_weights":
            job = session.get(JobOffer, uuid.UUID(p["job_id"]))
            if not job or job.tenant_id != action.tenant_id:
                raise ValueError("job not found")
            crit = json.loads(job.criteria)
            crit["weights"] = {k: int(p["weights"][k]) for k in ("required", "nice", "experience")}
            job.criteria = json.dumps(crit)
            session.add(job)
            action.result = f"weights for {job.title} -> {crit['weights']}"
        elif action.type == "create_job_offer":
            from backend.data import vectors

            crit = {"required_skills": [s.lower() for s in p["criteria"]["required_skills"]],
                    "nice_to_have": [s.lower() for s in p["criteria"]["nice_to_have"]],
                    "min_years": float(p["criteria"]["min_years"]), "weights": {"required": 60, "nice": 15, "experience": 25}}
            job = JobOffer(tenant_id=action.tenant_id, title=p["title"], description=p["body"], criteria=json.dumps(crit),
                           embedding=vectors.dumps(vectors.embed(f"{p['title']} {p['body']}")))
            session.add(job)
            session.flush()
            action.result = f"job offer created: {job.title} (job_offer_id={job.id})"  # read by the staffing hook
        elif action.type == "award_points":
            rule = session.get(PointsRule, p["action_type"])
            if not rule:
                raise ValueError("unknown points rule")
            if session.exec(select(PointsLedger).where(PointsLedger.idempotency_key == p["idempotency_key"])).first():
                raise ValueError("duplicate award (idempotency key already used)")
            session.add(PointsLedger(tenant_id=action.tenant_id, employee_id=p["employee_id"], action_type=rule.action_type,
                                     points=rule.points, period=utcnow().strftime("%Y-%m"), awarded_by=approval.reviewer_email,
                                     note=p.get("note", ""), idempotency_key=p["idempotency_key"]))
            action.result = f"{rule.points} points to {p['employee_id']}"
        else:
            raise ValueError(f"unknown action type {action.type}")
        action.status = "executed"
    except Exception as e:
        session.rollback()
        action = session.get(ProposedAction, action.id)
        action.status, action.result = "failed", f"{type(e).__name__}: {e}"[:500]
    action.executed_at = utcnow()
    session.add(action)
    session.commit()
    if action.status == "executed":
        _resume(session, action)


def _resume(session: Session, action: ProposedAction) -> None:
    from backend.orchestration import workflow  # lazy: orchestration depends on security, not the reverse

    workflow.resume(session, action)


def recheck_payload(payload: dict) -> list:
    """Used when an action is proposed: validates any outgoing text in the payload."""
    text = f"{payload.get('subject', '')}\n{payload.get('body', '')}"
    return check_outgoing_text(text, source_text=payload.get("_source_text", ""),
                               fact_check=payload.get("_fact_check", False))
