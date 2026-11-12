"""Starts the web app: `uv run uvicorn app.main:app --reload`."""

import threading
from collections.abc import Callable, Sequence
from contextlib import asynccontextmanager
from urllib.parse import urlencode, urlsplit

from fastapi import FastAPI, Request
from fastapi.responses import PlainTextResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from sqlalchemy import Engine
from sqlmodel import Session
from starlette.concurrency import run_in_threadpool

from app.ai.gemini import GeminiLLM
from app.ai.llm import AIError
from app.config import (
    AccountsConfig,
    AppConfig,
    FomcConfig,
    Settings,
    get_settings,
    load_accounts_config,
    load_app_config,
    load_benchmarks_config,
    load_fomc_config,
    load_institutional_config,
    load_macro_config,
    load_sources_config,
    load_x_config,
)
from app.db.session import database_is_set_up, database_is_up_to_date, make_engine
from app.providers.damodaran import DamodaranClient
from app.providers.fred import FredClient
from app.providers.google_auth import GoogleSignIn
from app.providers.ici import IciClient
from app.providers.market_data import MarketData
from app.providers.sec_13f import ThirteenFClient
from app.providers.yahoo import YahooMarketData
from app.scheduler import start_scheduler
from app.services.calendar import CalendarSetup
from app.services.macro import MacroSetup
from app.services.market_facts import MarketFacts
from app.services.options import RiskFreeRate
from app.services.portfolio import TradeNotFound
from app.services.reports import ReportServices, execute_run, mark_interrupted_runs
from app.services.users import ensure_owner
from app.services.watchlists import WatchlistNotFound
from app.sources.http import HttpFetcher
from app.sources.registry import NEWS_USER_AGENT, build_sources
from app.web.account_routes import SESSION_COOKIE, is_open_path, signed_in
from app.web.account_routes import router as account_router
from app.web.assets import STATIC_DIR
from app.web.calendar_routes import router as calendar_router
from app.web.macro_routes import router as macro_router
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


SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}

# Sent with every response. Other websites can't show the app inside a frame to trick a
# click ("clickjacking"), browsers don't guess file types, and the app's own addresses aren't
# passed on to other sites. Only framing is restricted, so no script or style is blocked.
SECURITY_HEADERS = {
    "X-Frame-Options": "DENY",
    "Content-Security-Policy": "frame-ancestors 'none'",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "same-origin",
}


def is_cross_site(request: Request) -> bool:
    """True when a change request says it was sent from a page on another website.

    Browsers add `Origin` (or at least `Referer`) to form posts and HTMX requests, so a page
    elsewhere can't quietly make the owner's browser delete a watchlist here (cross-site
    request forgery). It's compared with the host the request was sent to, so it works at
    localhost and later when hosted. Requests with neither header (scripts) are allowed.
    """
    source = request.headers.get("origin") or request.headers.get("referer")
    if not source:
        return False
    host = request.headers.get("host", "")
    try:
        return urlsplit(source).netloc.lower() != host.lower()
    except ValueError:
        return True


def build_report_services(
    settings: Settings, app_config: AppConfig, market_data: MarketData, engine: Engine
) -> ReportServices:
    sources_config = load_sources_config(settings.sources_config_file)
    x_config = load_x_config(settings.x_config_file)
    sources = build_sources(settings, sources_config, x_config, engine)
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
    macro: MacroSetup | None = None,
    accounts: AccountsConfig | None = None,
    google: GoogleSignIn | None = None,
) -> FastAPI:
    """Build the app. Tests pass in a temporary database, fake data sources and small limits."""
    settings = get_settings()
    app_config = app_config or load_app_config(settings.app_config_file)
    engine = engine or make_engine(settings.database_url)
    market_data = market_data or YahooMarketData(settings.market_data_timeout_seconds)
    report_services = report_services or build_report_services(
        settings, app_config, market_data, engine
    )
    fred = fred or FredClient(settings.fred_api_key, settings.source_timeout_seconds)
    fomc = fomc or load_fomc_config(settings.fomc_dates_file)
    accounts = accounts or load_accounts_config(settings.accounts_config_file)
    google = google or GoogleSignIn(
        settings.google_client_id, settings.google_client_secret, settings.source_timeout_seconds
    )
    if macro is None:
        macro_config = load_macro_config(settings.macro_config_file)
        macro = MacroSetup(
            fred=fred,
            ici=IciClient(
                HttpFetcher(NEWS_USER_AGENT, settings.source_timeout_seconds),
                macro_config.ici.page_url,
            ),
            market_data=market_data,
            config=macro_config,
            extra_closed_days=app_config.market_data.extra_closed_days,
            parallel=app_config.market_data.max_parallel_fetches,
        )

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
            app.state.user_id = ensure_owner(session, settings.default_user_email).id
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

    def look_up_account(token: str):
        with Session(engine) as session:
            return signed_in(session, token)

    @app.middleware("http")  # runs after find_signed_in_user below (added later = runs first)
    async def require_login(request: Request, call_next):
        """When login is required (online, F7.4), only signed-in users get past the login page."""
        if not accounts.login_required or request.state.account is not None:
            return await call_next(request)
        if is_open_path(request.url.path):
            return await call_next(request)
        if request.headers.get("hx-request"):  # HTMX swaps in parts of a page: reload it all
            return Response(status_code=401, headers={"HX-Redirect": "/login"})
        target = request.url.path + (f"?{request.url.query}" if request.url.query else "")
        return RedirectResponse(f"/login?{urlencode({'next': target})}", status_code=303)

    @app.middleware("http")
    async def find_signed_in_user(request: Request, call_next):
        """Who is signed in (F7.2), for the menu and the pages: `request.state.account`."""
        request.state.account = None
        token = request.cookies.get(SESSION_COOKIE)
        if token and not request.url.path.startswith("/static/"):
            request.state.account = await run_in_threadpool(look_up_account, token)
        return await call_next(request)

    @app.middleware("http")
    async def refuse_cross_site_changes(request: Request, call_next):
        if request.method not in SAFE_METHODS and is_cross_site(request):
            return PlainTextResponse(
                "Refused: this change was sent from another website. "
                "Use the app's own pages to make changes.",
                status_code=403,
            )
        return await call_next(request)

    @app.middleware("http")  # added last, so it runs first and also covers the refusal above
    async def add_security_headers(request: Request, call_next):
        response = await call_next(request)
        for name, value in SECURITY_HEADERS.items():
            response.headers.setdefault(name, value)
        return response

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
    app.state.macro = macro
    app.state.accounts = accounts
    app.state.google = google
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    app.include_router(router)
    app.include_router(report_router)
    app.include_router(option_router)
    app.include_router(portfolio_router)
    app.include_router(calendar_router)
    app.include_router(macro_router)
    app.include_router(account_router)
    app.add_exception_handler(WatchlistNotFound, watchlist_not_found)
    app.add_exception_handler(ReportNotFound, report_not_found)
    app.add_exception_handler(TradeNotFound, trade_not_found)
    return app


app = create_app()
