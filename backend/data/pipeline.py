"""Application status changes go through here, so every change leaves a PipelineEvent (time-in-stage analytics)."""
from sqlmodel import Session

from backend.data.models import Application, PipelineEvent


def set_status(session: Session, app: Application, to_status: str, actor: str = "") -> None:
    """Changes the status and records the event. The caller commits. No event when the status does not change."""
    if app.status == to_status:
        return
    session.add(PipelineEvent(tenant_id=app.tenant_id, application_id=app.id, from_status=app.status, to_status=to_status,
                              actor=actor[:200]))
    app.status = to_status
    session.add(app)


def created(session: Session, app: Application, actor: str = "") -> None:
    """A new application: the first event goes from "" to its initial status."""
    session.add(app)
    session.add(PipelineEvent(tenant_id=app.tenant_id, application_id=app.id, from_status="", to_status=app.status,
                              actor=actor[:200]))
