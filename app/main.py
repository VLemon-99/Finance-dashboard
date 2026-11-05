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
from app.config import (
    AppConfig,
    FomcConfig,
    Settings,
    get_settings,
    load_app_config,
    load_benchmarks_config,
    load_fomc_config,
    load_institutional_config,
    load_sources_config,
)
from app.db.session import database_is_set_up, database_is_up_to_date, make_engine
from app.providers.damodaran import DamodaranClient
from app.providers.fred import FredClient
from app.providers.market_data import MarketData
from app.providers.sec_13f import ThirteenFClient
from app.providers.yahoo import YahooMarketData
from app.scheduler import start_scheduler
from app.services.calendar import CalendarSetup
from app.services.market_facts import MarketFacts
from app.services.options import RiskFreeRate
from app.services.portfolio import TradeNotFound
from app.services.reports import ReportServices, execute_run, mark_interrupted_runs
from app.services.users import ensure_default_user
from app.services.watchlists import WatchlistNotFound
from app.sources.registry import build_sources
from app.web.assets import STATIC_DIR
from app.web.calendar_routes import router as calendar_router
from app.web.option_routes import router as option_router
from app.web.portfolio_routes import router as portfolio_router
from app.web.portfolio_routes import trade_not_found
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


def build_report_services(
    settings: Settings, app_config: AppConfig, market_data: MarketData
) -> ReportServices:
    sources_config = load_sources_config(settings.sources_config_file)
    sources = build_sources(settings, sources_config)
    try:
        llm = GeminiLLM(settings.gemini_api_key, app_config.ai.model, app_config.ai.timeout_seconds)
        problem = None
    except AIError as exc:  # no key yet: reports still work, without AI text
        llm, problem = None, str(exc)
    benchmarks = load_benchmarks_config(settings.benchmarks_file)
    facts = MarketFacts(
        market_data=market_data,
        benchmarks=benchmarks,
        damodaran=DamodaranClient(sources.web_fetch, benchmarks.damodaran),
        funds=ThirteenFClient(sources.sec_fetch, settings.sec_user_agent),
        institutional=load_institutional_config(settings.institutional_file),
        max_parallel=app_config.market_data.max_parallel_fetches,
    )
    return ReportServices(
        sources=sources,
        llm=llm,
        llm_problem=problem,
        config=app_config,
        sources_config=sources_config,
        facts=facts,
    )


def create_app(
    engine: Engine | None = None,
    market_data: MarketData | None = None,
    app_config: AppConfig | None = None,
    report_services: ReportServices | None = None,
    report_runner: ReportRunner | None = None,
    fred: FredClient | None = None,
    fomc: FomcConfig | None = None,
) -> FastAPI:
    """Build the app. Tests pass in a temporary database, fake data sources and small limits."""
    settings = get_settings()
    app_config = app_config or load_app_config(settings.app_config_file)
    engine = engine or make_engine(settings.database_url)
    market_data = market_data or YahooMarketData(settings.market_data_timeout_seconds)
    report_services = report_services or build_report_services(settings, app_config, market_data)
    fred = fred or FredClient(settings.fred_api_key, settings.source_timeout_seconds)
    fomc = fomc or load_fomc_config(settings.fomc_dates_file)

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
        app.state.scheduler = start_scheduler(
            engine, app.state.user_id, report_services, app_config.schedule
        )
        try:
            yield
        finally:
            if app.state.scheduler is not None:
                app.state.scheduler.shutdown(wait=False)

    app = FastAPI(title="Finance Dashboard", lifespan=lifespan)
    app.state.engine = engine
    app.state.market_data = market_data
    app.state.config = app_config
    app.state.risk_free_rate = RiskFreeRate(
        app_config.options.fallback_risk_free_rate_percent / 100
    )
    app.state.report_services = report_services
    app.state.report_runner = report_runner or run_in_background
    app.state.scheduler = None  # started at startup if schedule.enabled (app/scheduler.py)
    app.state.calendar = CalendarSetup(
        market_data=market_data, fred=fred, fomc=fomc, config=app_config
    )
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    app.include_router(router)
    app.include_router(report_router)
    app.include_router(option_router)
    app.include_router(portfolio_router)
    app.include_router(calendar_router)
    app.add_exception_handler(WatchlistNotFound, watchlist_not_found)
    app.add_exception_handler(ReportNotFound, report_not_found)
    app.add_exception_handler(TradeNotFound, trade_not_found)
    return app


app = create_app()
