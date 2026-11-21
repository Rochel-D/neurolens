from collections.abc import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from .config import get_settings


class Base(DeclarativeBase):
    pass


_settings = get_settings()
engine = create_engine(
    _settings.database_url,
    connect_args={"check_same_thread": False} if _settings.database_url.startswith("sqlite") else {},
    pool_pre_ping=True,
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_db() -> Iterator[Session]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    from . import models  # noqa: F401  (registers tables)

    Base.metadata.create_all(engine)
    _add_missing_columns()


def _add_missing_columns() -> None:
    """Tiny migration so a version-1 database keeps working: add new columns to old tables."""
    from sqlalchemy import inspect, text

    insp = inspect(engine)
    with engine.begin() as conn:
        for table in Base.metadata.sorted_tables:
            if not insp.has_table(table.name):
                continue
            have = {c["name"] for c in insp.get_columns(table.name)}
            for col in table.columns:
                if col.name in have:
                    continue
                ddl_type = col.type.compile(engine.dialect)
                default = col.default.arg if col.default is not None and not callable(col.default.arg) else None
                if isinstance(default, (dict, list)):
                    import json

                    default_sql = "'" + json.dumps(default) + "'"
                elif isinstance(default, bool):
                    default_sql = "1" if default else "0"
                elif isinstance(default, (int, float)):
                    default_sql = str(default)
                elif isinstance(default, str):
                    default_sql = "'" + default.replace("'", "''") + "'"
                else:
                    default_sql = "NULL"
                conn.execute(text(f'ALTER TABLE {table.name} ADD COLUMN {col.name} {ddl_type} DEFAULT {default_sql}'))
