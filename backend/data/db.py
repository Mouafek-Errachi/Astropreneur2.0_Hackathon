import logging
import shutil
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path

from sqlalchemy import inspect, text
from sqlmodel import Session, SQLModel, create_engine

from backend.config import get_settings

log = logging.getLogger(__name__)

# Bump when the tables change in a way create_all() cannot apply (no migrations in the prototype).
SCHEMA_VERSION = 7  # v3: project staffing tables, v4: SkillGap.internal_plan, v5: Resume.source, IntakeLog,
#                     v6: Employee.job_title on one branch, PipelineEvent and MetricSnapshot on the other,
#                     v7: both (a v6 database of either kind is upgraded in place, see init_db)

_settings = get_settings()
_connect_args = {"check_same_thread": False} if _settings.database_url.startswith("sqlite") else {}
engine = create_engine(_settings.database_url, connect_args=_connect_args, pool_pre_ping=True)


def _stored_version() -> int:
    insp = inspect(engine)
    if not insp.has_table("schema_meta"):
        return 1 if insp.has_table("user") else SCHEMA_VERSION  # old DB from v1, or empty DB
    with engine.connect() as conn:
        return conn.execute(text("SELECT version FROM schema_meta")).scalar() or 0


def _reset_outdated_sqlite() -> None:
    """A dev SQLite file from an older schema is backed up and replaced, instead of crashing with 500s."""
    if engine.url.get_backend_name() != "sqlite" or not engine.url.database:
        return
    found = _stored_version()
    if found in (6, SCHEMA_VERSION):  # v6 is upgraded in place by init_db
        return
    engine.dispose()
    path = Path(engine.url.database)
    backup = path.with_name(f"{path.stem}.v{found}-backup-{datetime.now():%Y%m%d-%H%M%S}{path.suffix}")
    shutil.move(path, backup)
    log.warning("Database schema v%s is outdated (expected v%s): moved it to %s and created a new one.",
                found, SCHEMA_VERSION, backup.name)


def init_db() -> None:
    from backend.data import models  # noqa: F401  (register tables)

    _reset_outdated_sqlite()
    SQLModel.metadata.create_all(engine)  # also adds the tables a v6 database lacks
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE IF NOT EXISTS schema_meta (version INTEGER NOT NULL)"))
        if conn.execute(text("SELECT COUNT(*) FROM schema_meta")).scalar() == 0:
            conn.execute(text("INSERT INTO schema_meta (version) VALUES (:v)"), {"v": SCHEMA_VERSION})
        elif conn.execute(text("SELECT version FROM schema_meta")).scalar() == 6:
            if "job_title" not in {c["name"] for c in inspect(conn).get_columns("employee")}:
                conn.execute(text("ALTER TABLE employee ADD COLUMN job_title VARCHAR NOT NULL DEFAULT ''"))
            conn.execute(text("UPDATE schema_meta SET version = :v"), {"v": SCHEMA_VERSION})
            log.info("Database schema v6 upgraded in place to v%s.", SCHEMA_VERSION)


def get_session() -> Iterator[Session]:
    with Session(engine) as session:
        yield session
