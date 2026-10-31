"""Workflow primitives (a lightweight stand-in for LangGraph interrupt/resume).

`propose()` is the interrupt: a workflow stops by writing a pending ProposedAction.
`resume()` is called by the executor after the human decision and runs the registered follow-up step.
TODO: swap for LangGraph `interrupt()` + checkpointer if workflows grow multi-step.
"""
import json
from typing import Callable

from sqlmodel import Session

from backend.data.models import ProposedAction
from backend.security import audit, events
from backend.security.executor import recheck_payload
from backend.security.guard.injection import SEVERITY_RANK, risk_level

ResumeHook = Callable[[Session, ProposedAction], None]
_hooks: dict[str, ResumeHook] = {}


def on_resume(source_type: str):
    def deco(fn: ResumeHook) -> ResumeHook:
        _hooks[source_type] = fn
        return fn

    return deco


def resume(session: Session, action: ProposedAction) -> None:
    if hook := _hooks.get(action.source_type):
        hook(session, action)
        session.commit()


def cancel(session: Session, action: ProposedAction, reason: str, actor: str = "system") -> None:
    """Withdraws a pending action that is no longer needed (e.g. the candidate replied). Never sends anything."""
    if action.status != "pending":
        return
    action.status, action.result = "cancelled", reason[:500]
    session.add(action)
    audit.log(session, actor=actor, event="action.cancelled", tenant_id=action.tenant_id,
              target=f"action:{action.id}", detail={"reason": reason}, commit=False)


def propose(session: Session, *, tenant_id: int, type: str, title: str, payload: dict, rationale: str = "",
            input_risk: str = "none", input_flags: list[dict] | None = None, created_by_agent: str = "agent",
            requested_by: str = "", source_type: str = "", source_id: str = "") -> ProposedAction:
    """Output Validator runs here, then the action is stored as PENDING. Nothing executes."""
    out_flags = [f.to_dict() for f in recheck_payload(payload)]
    payload = {k: v for k, v in payload.items() if not k.startswith("_")}
    all_flags = (input_flags or []) + out_flags
    level = max([input_risk, risk_level(out_flags)], key=lambda r: SEVERITY_RANK[r])
    action = ProposedAction(tenant_id=tenant_id, type=type, title=title[:200], payload=json.dumps(payload),
                            rationale=rationale[:1000], risk_level=level, risk_flags=json.dumps(all_flags[:20]),
                            created_by_agent=created_by_agent, requested_by=requested_by,
                            source_type=source_type, source_id=str(source_id))
    session.add(action)
    session.commit()
    session.refresh(action)
    if out_flags:
        events.record(session, tenant_id=tenant_id, source="output", source_id=str(action.id),
                      kind="output_violation", severity=risk_level(out_flags), detail={"flags": out_flags[:10]})
    audit.log(session, actor=created_by_agent, event="action.proposed", tenant_id=tenant_id,
              target=f"action:{action.id}", detail={"type": type, "risk": level})
    session.refresh(action)
    return action
