import json

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session

from backend.config import get_settings
from backend.core import approvals, injection
from backend.core.security import Principal, get_owned, require
from backend.db import get_session
from backend.features.replies.drafter import draft_candidate_email, draft_reply
from backend.models import Candidate, Email, Job
from backend.schemas import CandidateDraftRequest, DraftReply

router = APIRouter(tags=["replies"])

# Emails that announce a decision about a person can only be drafted once a human-approved decision exists.
REQUIRED_STATUS = {"rejection": {"rejected"}, "invitation": {"shortlisted", "interview"}}


def output_risk(base_risk: str, draft: DraftReply) -> str:
    """Scans what the LLM wrote before it reaches the approval queue."""
    allowed = get_settings().allowed_link_domains.split(",")
    out_flags = injection.scan_output(f"{draft.subject}\n{draft.body}", allowed)
    return max([base_risk, injection.risk_level(out_flags)], key=lambda r: injection.SEVERITY_RANK[r])


@router.post("/emails/{email_id}/draft-reply")
def draft_for_email(email_id: int, p: Principal = Depends(require("drafts:create")), session: Session = Depends(get_session)):
    e = get_owned(session, Email, email_id, p)
    draft, fallback = draft_reply(e.sender, e.subject, e.body)
    risk = output_risk(e.risk_level, draft)
    # The recipient is always the original sender: the LLM never chooses who gets mail.
    action = approvals.propose(session, tenant_id=p.tenant_id, type="send_email", title=f"Reply to {e.sender}",
                               payload={"to": e.sender, "subject": draft.subject, "body": draft.body, "email_id": e.id},
                               rationale="Drafted reply" + (" (fallback template)" if fallback else ""),
                               risk_level=risk, created_by=p.subject)
    return {"action_id": action.id, "status": action.status, "draft": draft.model_dump(), "risk_level": risk}


@router.post("/candidates/{cid}/draft-reply")
def draft_for_candidate(cid: int, body: CandidateDraftRequest, p: Principal = Depends(require("drafts:create")),
                        session: Session = Depends(get_session)):
    c = get_owned(session, Candidate, cid, p)
    if not c.email:
        raise HTTPException(409, "No email address found in this candidate's resume")
    needed = REQUIRED_STATUS.get(body.kind)
    if needed and c.status not in needed:
        raise HTTPException(409, f"A {body.kind} email needs an approved decision first (candidate is '{c.status}'). "
                                 "Propose the decision and have it approved.")
    job = session.get(Job, c.job_id)
    draft, fallback = draft_candidate_email(body.kind, job.title, json.loads(c.profile_json))
    risk = output_risk(c.risk_level, draft)
    # The recipient comes from the candidate's own resume (verified at screening), never from the LLM output.
    action = approvals.propose(session, tenant_id=p.tenant_id, type="send_email",
                               title=f"{body.kind.capitalize()} email to {c.name or c.email}",
                               payload={"to": c.email, "subject": draft.subject, "body": draft.body, "candidate_id": c.id},
                               rationale=f"Drafted from the candidate's own file ({body.kind})"
                                         + (" (fallback template)" if fallback else ""),
                               risk_level=risk, created_by=p.subject)
    return {"action_id": action.id, "status": action.status, "draft": draft.model_dump(), "risk_level": risk}
