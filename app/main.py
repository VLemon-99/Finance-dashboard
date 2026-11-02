"""Starts the web app: `uv run uvicorn app.main:app --reload`."""

import threading
from collections.abc import Callable, Sequence
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from sqlalchemy import Engine
from sqlmodel import Session

from app.ai.gemini import GeminiLLM
from app.ai.llm import AIError
from app.config import AppConfig, Settings, get_settings, load_app_config, load_sources_config
from app.db.session import database_is_set_up, database_is_up_to_date, make_engine
from app.providers.market_data import MarketData
from app.providers.yahoo import YahooMarketData
from app.services.reports import ReportServices, execute_run, mark_interrupted_runs
from app.services.users import ensure_default_user
from app.services.watchlists import WatchlistNotFound
from app.sources.registry import build_sources
from app.web.assets import STATIC_DIR
from app.web.report_routes import ReportNotFound, report_not_found
from app.web.report_routes import router as report_router
from app.web.routes import router, watchlist_not_found

# Starts a report run: (engine, run id, tickers, services). Tests pass one that runs at once.
ReportRunner = Callable[[Engine, int, Sequence[str], ReportServices], None]


def run_in_background(
    engine: Engine, run_id: int, tickers: Sequence[str], services: ReportServices
) -> None:
    """Reports take minutes, so they run in a background thread; the page shows progress."""
    thread = threading.Thread(
        target=execute_run,
        args=(engine, run_id, list(tickers), services),
        name=f"report-run-{run_id}",
        daemon=True,
    )
    thread.start()


def build_report_services(settings: Settings, app_config: AppConfig) -> ReportServices:
    sources_config = load_sources_config(settings.sources_config_file)
    try:
        llm = GeminiLLM(settings.gemini_api_key, app_config.ai.model, app_config.ai.timeout_seconds)
        problem = None
    except AIError as exc:  # no key yet: reports still work, without AI text
        llm, problem = None, str(exc)
    return ReportServices(
        sources=build_sources(settings, sources_config),
        llm=llm,
        llm_problem=problem,
        config=app_config,
        sources_config=sources_config,
    )


def create_app(
    engine: Engine | None = None,
    market_data: MarketData | None = None,
    app_config: AppConfig | None = None,
    report_services: ReportServices | None = None,
    report_runner: ReportRunner | None = None,
) -> FastAPI:
    """Build the app. Tests pass in a temporary database, fake data sources and small limits."""
    settings = get_settings()
    app_config = app_config or load_app_config(settings.app_config_file)
    engine = engine or make_engine(settings.database_url)
    market_data = market_data or YahooMarketData(settings.market_data_timeout_seconds)
    report_services = report_services or build_report_services(settings, app_config)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if not database_is_set_up(engine):
            raise RuntimeError("The database isn't set up yet. Run: uv run alembic upgrade head")
        if not database_is_up_to_date(engine):
            raise RuntimeError(
                "The database needs updating for this version of the app. "
                "Run: uv run alembic upgrade head"
            )
        with Session(engine) as session:
            app.state.user_id = ensure_default_user(session, settings.default_user_email).id
            mark_interrupted_runs(session)
        yield

    app = FastAPI(title="Finance Dashboard", lifespan=lifespan)
    app.state.engine = engine
    app.state.market_data = market_data
    app.state.config = app_config
    app.state.report_services = report_services
    app.state.report_runner = report_runner or run_in_background
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    app.include_router(router)
    app.include_router(report_router)
    app.add_exception_handler(WatchlistNotFound, watchlist_not_found)
    app.add_exception_handler(ReportNotFound, report_not_found)
    return app


app = create_app()
