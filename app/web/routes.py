"""Web pages.

Changes are plain HTML form posts followed by a redirect, so every page works without
JavaScript. HTMX only makes search and the quote lookup update in place.
"""

from datetime import timedelta
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlmodel import Session

from app.config import AppConfig
from app.db.session import get_session
from app.providers.market_data import MarketData
from app.services import calendar as cal
from app.services import watchlists as wl
from app.services.options import contract_rows
from app.services.quotes import QuoteView, lookup_quote
from app.services.search import SearchOutcome, search_instruments
from app.tickers import InvalidTicker
from app.timeutils import format_eastern, today_eastern, utc_now
from app.web import formatting
from app.web.assets import asset_version

templates = Jinja2Templates(directory=Path(__file__).parent / "templates")
templates.env.filters.update(
    eastern=format_eastern,
    money=formatting.money,
    signed=formatting.signed,
    trend=formatting.trend,
    compact=formatting.compact,
    number=formatting.number,
    whole=formatting.whole,
    percent=formatting.percent,
    multiple=formatting.multiple,
    vs=formatting.vs,
    strike=formatting.strike,
    short_date=formatting.short_date,
    iso_day=formatting.iso_day,
    session_label=lambda d: formatting.session_label(d, today_eastern()),
)
templates.env.globals["price_range"] = formatting.price_range
templates.env.globals["contract_label"] = formatting.contract_label
templates.env.globals["instrument_label"] = formatting.instrument_label
templates.env.globals["asset_version"] = asset_version

router = APIRouter()


def get_market_data(request: Request) -> MarketData:
    return request.app.state.market_data


def get_config(request: Request) -> AppConfig:
    return request.app.state.config


def get_user_id(request: Request) -> int:
    """The signed-in user. For now always the default user (F7.1); login comes in Phase 10."""
    return request.app.state.user_id


SessionDep = Annotated[Session, Depends(get_session)]
MarketDataDep = Annotated[MarketData, Depends(get_market_data)]
ConfigDep = Annotated[AppConfig, Depends(get_config)]
UserDep = Annotated[int, Depends(get_user_id)]


def _refresh_window(config: AppConfig) -> timedelta:
    return timedelta(minutes=config.market_data.refresh_minutes)


def _redirect(url: str) -> RedirectResponse:
    return RedirectResponse(url, status_code=303)  # 303 = "now go and GET this page"


async def watchlist_not_found(request: Request, exc: Exception) -> HTMLResponse:
    return templates.TemplateResponse(request, "not_found.html", {}, status_code=404)


# --- home: list of watchlists ------------------------------------------------


def _home(request, session, user_id, config, error=None, name="", status_code=200):
    # The next high-impact events (SPEC F5.6). Never breaks the page (see services/calendar).
    events, calendar_notes = cal.next_high_impact(
        session, request.app.state.calendar, user_id, utc_now()
    )
    return templates.TemplateResponse(
        request,
        "home.html",
        {
            "events": events,
            "calendar_notes": calendar_notes,
            "watchlists": wl.list_watchlists(session, user_id),
            "limits": config.watchlists,
            "error": error,
            "name": name,
        },
        status_code=status_code,
    )


@router.get("/", response_class=HTMLResponse)
def home(request: Request, session: SessionDep, user_id: UserDep, config: ConfigDep):
    return _home(request, session, user_id, config)


@router.post("/watchlists", response_class=HTMLResponse)
def create_watchlist(
    request: Request,
    session: SessionDep,
    user_id: UserDep,
    config: ConfigDep,
    name: Annotated[str, Form(max_length=200)] = "",
):
    try:
        watchlist = wl.create_watchlist(session, user_id, name, config.watchlists)
    except wl.WatchlistError as exc:
        return _home(request, session, user_id, config, error=str(exc), name=name, status_code=400)
    return _redirect(f"/watchlists/{watchlist.id}")


# --- one watchlist -------------------------------------------------------------


def _watchlist_page(
    request: Request,
    session: Session,
    market_data: MarketData,
    user_id: int,
    config: AppConfig,
    watchlist_id: int,
    *,
    refresh: bool = False,
    search: SearchOutcome | None = None,
    error: str | None = None,
    status_code: int = 200,
):
    watchlist = wl.get_watchlist(session, user_id, watchlist_id)
    rows = wl.watchlist_rows(
        session,
        market_data,
        user_id,
        watchlist_id,
        max_age=None if refresh else _refresh_window(config),
        max_parallel=config.market_data.max_parallel_fetches,
    )
    contracts, rate = contract_rows(
        session,
        market_data,
        request.app.state.risk_free_rate,
        wl.watchlist_contracts(session, user_id, watchlist_id),
        max_age=None if refresh else _refresh_window(config),
        min_iv=config.options.min_implied_volatility_percent / 100,
    )
    return templates.TemplateResponse(
        request,
        "watchlist.html",
        {
            "watchlist": watchlist,
            "rows": rows,
            "contracts": contracts,
            "rate": rate,
            "contract_problems": sum(c.status in ("stale", "error") for c in contracts),
            "problem_count": sum(r.quote.status in ("stale", "error") for r in rows),
            "in_list": {r.quote.symbol for r in rows},
            "search": search or SearchOutcome(query=""),
            "limits": config.watchlists,
            "refresh_minutes": config.market_data.refresh_minutes,
            "error": error,
        },
        status_code=status_code,
    )


@router.get("/watchlists/{watchlist_id}", response_class=HTMLResponse)
def watchlist_page(
    request: Request,
    watchlist_id: int,
    session: SessionDep,
    market_data: MarketDataDep,
    user_id: UserDep,
    config: ConfigDep,
    refresh: bool = False,
    q: str = "",
):
    """`?refresh=1` is the Refresh button. `?q=` searches (used when JavaScript is off)."""
    search = search_instruments(market_data, q, config.search.max_results) if q.strip() else None
    return _watchlist_page(
        request,
        session,
        market_data,
        user_id,
        config,
        watchlist_id,
        refresh=refresh,
        search=search,
    )


@router.get("/watchlists/{watchlist_id}/search", response_class=HTMLResponse)
def watchlist_search(
    request: Request,
    watchlist_id: int,
    session: SessionDep,
    market_data: MarketDataDep,
    user_id: UserDep,
    config: ConfigDep,
    q: str = "",
):
    """Just the search results box; the page swaps it in with HTMX."""
    watchlist = wl.get_watchlist(session, user_id, watchlist_id)
    return templates.TemplateResponse(
        request,
        "_search_results.html",
        {
            "watchlist": watchlist,
            "search": search_instruments(market_data, q, config.search.max_results),
            "in_list": wl.watchlist_symbols(session, user_id, watchlist_id),
        },
    )


@router.post("/watchlists/{watchlist_id}/rename", response_class=HTMLResponse)
def rename_watchlist(
    request: Request,
    watchlist_id: int,
    session: SessionDep,
    market_data: MarketDataDep,
    user_id: UserDep,
    config: ConfigDep,
    name: Annotated[str, Form(max_length=200)] = "",
):
    try:
        wl.rename_watchlist(session, user_id, watchlist_id, name)
    except wl.WatchlistError as exc:
        return _watchlist_page(
            request,
            session,
            market_data,
            user_id,
            config,
            watchlist_id,
            error=str(exc),
            status_code=400,
        )
    return _redirect(f"/watchlists/{watchlist_id}")


@router.post("/watchlists/{watchlist_id}/delete")
def delete_watchlist(watchlist_id: int, session: SessionDep, user_id: UserDep):
    wl.delete_watchlist(session, user_id, watchlist_id)
    return _redirect("/")


@router.post("/watchlists/{watchlist_id}/items", response_class=HTMLResponse)
def add_item(
    request: Request,
    watchlist_id: int,
    session: SessionDep,
    market_data: MarketDataDep,
    user_id: UserDep,
    config: ConfigDep,
    ticker: Annotated[str, Form(max_length=50)] = "",
):
    try:
        wl.add_ticker(
            session,
            market_data,
            user_id,
            watchlist_id,
            ticker,
            config.watchlists,
            max_age=_refresh_window(config),
        )
    except wl.WatchlistError as exc:
        return _watchlist_page(
            request,
            session,
            market_data,
            user_id,
            config,
            watchlist_id,
            error=str(exc),
            status_code=400,
        )
    return _redirect(f"/watchlists/{watchlist_id}")


@router.post("/watchlists/{watchlist_id}/items/{item_id}/delete")
def remove_item(watchlist_id: int, item_id: int, session: SessionDep, user_id: UserDep):
    wl.remove_item(session, user_id, watchlist_id, item_id)
    return _redirect(f"/watchlists/{watchlist_id}")


# --- single-ticker quote lookup (the Phase 1 page) ----------------------------


def _lookup(
    session: Session, market_data: MarketData, config: AppConfig, ticker: str
) -> tuple[QuoteView | None, str | None]:
    """Returns (quote, input_error)."""
    try:
        return lookup_quote(session, market_data, ticker, max_age=_refresh_window(config)), None
    except InvalidTicker:
        return None, "Please enter a valid US ticker, e.g. AAPL or BRK-B."


@router.get("/lookup", response_class=HTMLResponse)
def lookup_page(
    request: Request,
    session: SessionDep,
    market_data: MarketDataDep,
    config: ConfigDep,
    ticker: str = "",
):
    """The quote page. Works without JavaScript too (plain form submit to this page)."""
    quote, input_error = (
        _lookup(session, market_data, config, ticker) if ticker.strip() else (None, None)
    )
    return templates.TemplateResponse(
        request,
        "lookup.html",
        {"ticker": ticker.strip(), "quote": quote, "input_error": input_error},
    )


@router.get("/lookup/result", response_class=HTMLResponse)
def lookup_result(
    request: Request,
    session: SessionDep,
    market_data: MarketDataDep,
    config: ConfigDep,
    ticker: str = "",
):
    """Just the result box; the page swaps it in with HTMX."""
    quote, input_error = _lookup(session, market_data, config, ticker)
    return templates.TemplateResponse(
        request, "_quote_result.html", {"quote": quote, "input_error": input_error}
    )
