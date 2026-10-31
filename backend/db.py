from collections.abc import Iterator

from sqlalchemy import Engine, inspect, text
from sqlmodel import Session, SQLModel, create_engine

from backend.config import get_settings

_settings = get_settings()
_connect_args = {"check_same_thread": False} if _settings.database_url.startswith("sqlite") else {}
engine = create_engine(_settings.database_url, connect_args=_connect_args, pool_pre_ping=True)


def add_missing_columns(bind: Engine) -> list[str]:
    """Forward-only migration for a prototype without Alembic: adds new (nullable) columns to existing tables."""
    insp = inspect(bind)
    added = []
    with bind.begin() as conn:
        for table in SQLModel.metadata.sorted_tables:
            if not insp.has_table(table.name):
                continue
            existing = {c["name"] for c in insp.get_columns(table.name)}
            for col in table.columns:
                if col.name not in existing:
                    conn.execute(text(f'ALTER TABLE "{table.name}" ADD COLUMN "{col.name}" '
                                      f"{col.type.compile(dialect=bind.dialect)}"))
                    added.append(f"{table.name}.{col.name}")
    return added


def init_db() -> None:
    from backend import models  # noqa: F401  (register tables)

    SQLModel.metadata.create_all(engine)
    add_missing_columns(engine)


def get_session() -> Iterator[Session]:
    with Session(engine) as session:
        yield session
