from fastapi import APIRouter, Depends
from sqlmodel import Session

from backend.config import get_settings
from backend.core import approvals, injection
from backend.core.security import Principal, get_owned, require
from backend.db import get_session
from backend.features.replies.drafter import draft_reply
from backend.models import Email

router = APIRouter(tags=["replies"])


@router.post("/emails/{email_id}/draft-reply")
def draft_for_email(email_id: int, p: Principal = Depends(require("drafts:create")), session: Session = Depends(get_session)):
    e = get_owned(session, Email, email_id, p)
    draft, fallback = draft_reply(e.sender, e.subject, e.body)
    allowed = get_settings().allowed_link_domains.split(",")
    out_flags = injection.scan_output(draft.body, allowed)
    risk = max([e.risk_level, injection.risk_level(out_flags)], key=lambda r: injection.SEVERITY_RANK[r])
    # The recipient is always the original sender: the LLM never chooses who gets mail.
    action = approvals.propose(session, tenant_id=p.tenant_id, type="send_email", title=f"Reply to {e.sender}",
                               payload={"to": e.sender, "subject": draft.subject, "body": draft.body, "email_id": e.id},
                               rationale="Drafted reply" + (" (fallback template)" if fallback else ""),
                               risk_level=risk)
    return {"action_id": action.id, "status": action.status, "draft": draft.model_dump(), "risk_level": risk}

# TODO: /candidates/{id}/draft-reply (rejection / invitation templates)
