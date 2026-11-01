"""Confirmed interviews. Nothing is written here until HR downloads the summary (that is the confirmation);
until then the transcript lives only in memory (sessions.py). Rows expire after RETENTION_DAYS."""
from datetime import datetime, timedelta, timezone

from sqlalchemy import JSON, DateTime, ForeignKey, LargeBinary, String, Text, create_engine, delete, select
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship, sessionmaker

from .config import settings


class Base(DeclarativeBase):
    pass


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Interview(Base):
    __tablename__ = "interviews"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)          # = session id
    owner: Mapped[str] = mapped_column(String(320), index=True)            # HR user (JWT sub)
    tenant_id: Mapped[str | None] = mapped_column(String(64), index=True)  # HR platform tenant (JWT tid)
    candidate_id: Mapped[str | None] = mapped_column(String(64), index=True)
    job_title: Mapped[str] = mapped_column(Text, default="")
    job_description: Mapped[str] = mapped_column(Text, default="")
    transcript: Mapped[list] = mapped_column(JSON, default=list)
    summary: Mapped[dict | None] = mapped_column(JSON)
    notes: Mapped[list] = mapped_column(JSON, default=list)
    confirmed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    exports: Mapped[list["ExportFile"]] = relationship(back_populates="interview", cascade="all, delete-orphan",
                                                       order_by="ExportFile.id")


class ExportFile(Base):
    __tablename__ = "interview_exports"
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    interview_id: Mapped[str] = mapped_column(ForeignKey("interviews.id", ondelete="CASCADE"), index=True)
    format: Mapped[str] = mapped_column(String(8))
    filename: Mapped[str] = mapped_column(String(255))
    mime: Mapped[str] = mapped_column(String(100))
    data: Mapped[bytes] = mapped_column(LargeBinary)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    interview: Mapped[Interview] = relationship(back_populates="exports")


_Session = None


def session():
    """Engine and tables are created on first use, so tests can point DATABASE_URL elsewhere first."""
    global _Session
    if _Session is None:
        url = settings.database_url
        engine = create_engine(url, pool_pre_ping=True,
                               connect_args={"check_same_thread": False} if url.startswith("sqlite") else {})
        Base.metadata.create_all(engine)
        _Session = sessionmaker(engine, expire_on_commit=False)
    return _Session()


def _aware(d: datetime) -> datetime:
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)   # SQLite drops the tz


def save_confirmed(s, fmt: str, filename: str, mime: str, data: bytes) -> Interview:
    """Writes the interview (transcript, HR-edited summary, notes) and the file to the candidate profile.
    Exporting again updates the row and replaces the file of the same format."""
    now = utcnow()
    with session() as db, db.begin():
        row = db.get(Interview, s.id)
        if row is None:
            row = Interview(id=s.id, owner=s.owner, tenant_id=s.tenant_id, candidate_id=s.candidate_id,
                            confirmed_at=now)
            db.add(row)
        row.job_title, row.job_description = s.job_title, s.job_description
        row.transcript, row.summary, row.notes = list(s.transcript), s.summary, list(s.notes)
        row.expires_at = now + timedelta(days=settings.retention_days)
        row.exports = [e for e in row.exports if e.format != fmt] + [
            ExportFile(format=fmt, filename=filename, mime=mime, data=data)]
    return row


def _visible(stmt, owner: str, tenant_id: str | None):
    """Same tenant on the HR platform sees the team's interviews; without a tenant, only your own."""
    stmt = stmt.where(Interview.expires_at > utcnow())
    return stmt.where(Interview.tenant_id == tenant_id) if tenant_id else stmt.where(Interview.owner == owner)


def _brief(r: Interview) -> dict:
    return {"id": r.id, "candidate_id": r.candidate_id, "job_title": r.job_title, "owner": r.owner,
            "confirmed_at": _aware(r.confirmed_at).isoformat(), "expires_at": _aware(r.expires_at).isoformat(),
            "exports": [{"id": e.id, "format": e.format, "filename": e.filename,
                         "created_at": _aware(e.created_at).isoformat()} for e in r.exports]}


def list_for_candidate(candidate_id: str, owner: str, tenant_id: str | None) -> list[dict]:
    with session() as db:
        stmt = _visible(select(Interview).where(Interview.candidate_id == candidate_id), owner, tenant_id)
        return [_brief(r) for r in db.scalars(stmt.order_by(Interview.confirmed_at.desc()))]


def get_interview(interview_id: str, owner: str, tenant_id: str | None) -> dict | None:
    with session() as db:
        r = db.scalar(_visible(select(Interview).where(Interview.id == interview_id), owner, tenant_id))
        if not r:
            return None
        return {**_brief(r), "job_description": r.job_description, "transcript": r.transcript,
                "summary": r.summary, "notes": r.notes}


def get_export(interview_id: str, export_id: int, owner: str, tenant_id: str | None) -> ExportFile | None:
    with session() as db:
        stmt = _visible(select(ExportFile).join(Interview), owner, tenant_id)
        return db.scalar(stmt.where(ExportFile.id == export_id, ExportFile.interview_id == interview_id))


def delete_interview(interview_id: str, owner: str) -> bool:
    """Only the interviewer who confirmed it can delete it early."""
    with session() as db, db.begin():
        r = db.scalar(select(Interview).where(Interview.id == interview_id, Interview.owner == owner))
        if not r:
            return False
        db.delete(r)
    return True


def purge_expired() -> int:
    with session() as db, db.begin():
        ids = select(Interview.id).where(Interview.expires_at <= utcnow())
        db.execute(delete(ExportFile).where(ExportFile.interview_id.in_(ids)))   # SQLite ignores ON DELETE
        return db.execute(delete(Interview).where(Interview.id.in_(ids))).rowcount
