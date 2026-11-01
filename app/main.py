"""Starts the web app: `uv run uvicorn app.main:app --reload`."""

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from sqlalchemy import Engine
from sqlmodel import Session

from app.config import AppConfig, get_settings, load_app_config
from app.db.session import database_is_set_up, make_engine
from app.providers.market_data import MarketData
from app.providers.yahoo import YahooMarketData
from app.services.users import ensure_default_user
from app.web.routes import router

STATIC_DIR = Path(__file__).parent / "web" / "static"


def create_app(
    engine: Engine | None = None,
    market_data: MarketData | None = None,
    app_config: AppConfig | None = None,
) -> FastAPI:
    """Build the app. Tests pass in a temporary database, fake market data and small limits."""
    settings = get_settings()
    app_config = app_config or load_app_config(settings.app_config_file)
    engine = engine or make_engine(settings.database_url)
    market_data = market_data or YahooMarketData(settings.market_data_timeout_seconds)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if not database_is_set_up(engine):
            raise RuntimeError("The database isn't set up yet. Run: uv run alembic upgrade head")
        with Session(engine) as session:
            ensure_default_user(session, settings.default_user_email)
        yield

    app = FastAPI(title="Finance Dashboard", lifespan=lifespan)
    app.state.engine = engine
    app.state.market_data = market_data
    app.state.config = app_config
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    app.include_router(router)
    return app


app = create_app()
