"""Database connection."""

from collections.abc import Iterator
from pathlib import Path

from fastapi import Request
from sqlalchemy import Engine, inspect
from sqlmodel import Session, create_engine


def make_engine(database_url: str) -> Engine:
    if database_url.startswith("sqlite:///"):
        # Make sure the folder for the database file exists (e.g. data/).
        Path(database_url.removeprefix("sqlite:///")).parent.mkdir(parents=True, exist_ok=True)
    return create_engine(database_url)


def database_is_set_up(engine: Engine) -> bool:
    return inspect(engine).has_table("users")


def database_is_up_to_date(engine: Engine) -> bool:
    """True when every migration has been applied (`uv run alembic upgrade head`)."""
    from alembic.config import Config
    from alembic.runtime.migration import MigrationContext
    from alembic.script import ScriptDirectory

    from app.config import PROJECT_ROOT

    script = ScriptDirectory.from_config(Config(str(PROJECT_ROOT / "alembic.ini")))
    with engine.connect() as connection:
        current = MigrationContext.configure(connection).get_current_heads()
    return set(current) == set(script.get_heads())


def get_session(request: Request) -> Iterator[Session]:
    """FastAPI dependency: one database session per web request."""
    with Session(request.app.state.engine) as session:
        yield session
