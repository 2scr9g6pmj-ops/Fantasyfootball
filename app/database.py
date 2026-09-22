from pathlib import Path

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import get_settings


class Base(DeclarativeBase):
    pass


def _ensure_sqlite_directory(url: str) -> None:
    prefix = "sqlite:///"
    if url.startswith(prefix) and url != "sqlite:///:memory:":
        Path(url.removeprefix(prefix)).parent.mkdir(parents=True, exist_ok=True)


settings = get_settings()
_ensure_sqlite_directory(settings.database_url)
engine_kwargs = {"connect_args": {"check_same_thread": False}} if settings.database_url.startswith("sqlite") else {}
if settings.database_url == "sqlite:///:memory:":
    engine_kwargs["poolclass"] = StaticPool
engine = create_engine(settings.database_url, **engine_kwargs)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


def ensure_schema_compatibility() -> None:
    """Apply small SQLite compatibility fixes for databases created by older builds."""
    if engine.dialect.name != "sqlite":
        return
    inspector = inspect(engine)
    if "fantasy_rosters" not in inspector.get_table_names():
        return
    columns = {column["name"] for column in inspector.get_columns("fantasy_rosters")}
    if "co_owner_ids" not in columns:
        with engine.begin() as connection:
            connection.execute(
                text("ALTER TABLE fantasy_rosters ADD COLUMN co_owner_ids JSON NOT NULL DEFAULT '[]'")
            )
    league_columns = {column["name"] for column in inspector.get_columns("leagues")}
    if "platform" not in league_columns:
        with engine.begin() as connection:
            connection.execute(
                text("ALTER TABLE leagues ADD COLUMN platform VARCHAR NOT NULL DEFAULT 'sleeper'")
            )


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
