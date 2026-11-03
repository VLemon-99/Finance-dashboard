"""Option chain pages (SPEC F2).

`/options/{ticker}` shows one expiration's calls or puts with prices, IV, greeks and DTE,
and lets you add a contract to a watchlist (F2.5a).
"""

from datetime import date
from typing import Annotated
from urllib.parse import urlencode

from fastapi import APIRouter, Form, Query, Request
from fastapi.responses import HTMLResponse
from sqlmodel import Session

from app.config import AppConfig
from app.providers.market_data import MarketData
from app.services import watchlists as wl
from app.services.options import ChainView, RiskFreeRate, option_chain
from app.services.quotes import lookup_quote
from app.tickers import InvalidTicker, normalize_ticker
from app.timeutils import utc_now
from app.web.routes import (
    ConfigDep,
    MarketDataDep,
    SessionDep,
    UserDep,
    _redirect,
    _refresh_window,
    templates,
)

router = APIRouter()

INVALID_TICKER = "Please enter a valid US ticker, e.g. AAPL or BRK-B."


def _parse_date(text: str) -> date | None:
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def _chain_url(symbol: str, expiration: date | None, option_type: str, **extra) -> str:
    params = {"expiration": expiration.isoformat() if expiration else "", "type": option_type}
    params.update({k: v for k, v in extra.items() if v is not None})
    return f"/options/{symbol}?{urlencode(params)}"


@router.get("/options", response_class=HTMLResponse)
def options_start(request: Request, ticker: Annotated[str, Query(max_length=50)] = ""):
    """A box to type a ticker; submitting it opens that ticker's chain."""
    error = None
    if ticker.strip():
        try:
            return _redirect(f"/options/{normalize_ticker(ticker)}")
        except InvalidTicker:
            error = INVALID_TICKER
    return templates.TemplateResponse(
        request,
        "options_start.html",
        {"ticker": ticker.strip(), "error": error},
        status_code=400 if error else 200,
    )


def _chain_page(
    request: Request,
    session: Session,
    market_data: MarketData,
    user_id: int,
    config: AppConfig,
    ticker: str,
    *,
    expiration: date | None,
    option_type: str,
    refresh: bool = False,
    list_id: int | None = None,
    added: str = "",
    error: str | None = None,
    status_code: int = 200,
):
    try:
        symbol = normalize_ticker(ticker)
    except InvalidTicker:
        return templates.TemplateResponse(
            request,
            "options_start.html",
            {"ticker": ticker, "error": INVALID_TICKER},
            status_code=400,
        )
    side = "put" if option_type == "put" else "call"
    max_age = None if refresh else _refresh_window(config)
    quote = lookup_quote(session, market_data, symbol, max_age=max_age)
    if quote.status == "not_found":
        chain = ChainView(symbol, "no_options", side, notes=[f"Ticker {symbol} not found."])
    else:
        rates: RiskFreeRate = request.app.state.risk_free_rate
        chain = option_chain(
            session,
            market_data,
            rates,
            symbol,
            expiration=expiration,
            option_type=side,
            max_age=max_age,
            min_iv=config.options.min_implied_volatility_percent / 100,
            stock_price=quote.last_price,
        )

    # Which watchlist the "Add" buttons add to: the one asked for, else the first.
    watchlists = wl.list_watchlists(session, user_id)
    target = next((w for w in watchlists if w.id == list_id), watchlists[0] if watchlists else None)
    in_list = wl.watchlist_symbols(session, user_id, target.id) if target else set()
    added_row = next((r for r in chain.rows if r.contract_symbol == added), None)
    return templates.TemplateResponse(
        request,
        "option_chain.html",
        {
            "quote": quote,
            "chain": chain,
            "refresh_minutes": config.market_data.refresh_minutes,
            "watchlists": watchlists,
            "target": target,
            "in_list": in_list,
            "added_row": added_row,
            "error": error,
        },
        status_code=status_code,
    )


@router.get("/options/{ticker}", response_class=HTMLResponse)
def option_chain_page(
    request: Request,
    ticker: str,
    session: SessionDep,
    market_data: MarketDataDep,
    user_id: UserDep,
    config: ConfigDep,
    expiration: Annotated[str, Query(max_length=10)] = "",
    type: Annotated[str, Query(max_length=4)] = "call",
    refresh: bool = False,
    list: int | None = None,
    added: Annotated[str, Query(max_length=32)] = "",
):
    """`?expiration=YYYY-MM-DD&type=call|put&list=<watchlist id>`. `?refresh=1` is the
    Refresh button; `?added=` shows a confirmation after adding a contract."""
    return _chain_page(
        request,
        session,
        market_data,
        user_id,
        config,
        ticker,
        expiration=_parse_date(expiration),
        option_type=type,
        refresh=refresh,
        list_id=list,
        added=added,
    )


@router.post("/options/{ticker}/add", response_class=HTMLResponse)
def add_contract(
    request: Request,
    ticker: str,
    session: SessionDep,
    market_data: MarketDataDep,
    user_id: UserDep,
    config: ConfigDep,
    contract: Annotated[str, Form(max_length=32)] = "",
    watchlist_id: Annotated[int, Form()] = 0,
    expiration: Annotated[str, Form(max_length=10)] = "",
    type: Annotated[str, Form(max_length=4)] = "call",
):
    """Add one contract to a watchlist (F2.5a), then go back to the same chain."""
    when = _parse_date(expiration)
    try:
        symbol = normalize_ticker(ticker)
    except InvalidTicker:
        symbol = None
    try:
        if symbol is None:
            raise wl.WatchlistError(INVALID_TICKER)
        wl.add_contract(session, user_id, watchlist_id, contract, config.watchlists, utc_now())
    except wl.WatchlistError as exc:
        return _chain_page(
            request,
            session,
            market_data,
            user_id,
            config,
            ticker,
            expiration=when,
            option_type=type,
            list_id=watchlist_id,
            error=str(exc),
            status_code=400,
        )
    return _redirect(_chain_url(symbol, when, type, list=watchlist_id, added=contract))
