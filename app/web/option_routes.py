"""Option chain pages (SPEC F2).

`/options/{ticker}` shows one expiration's calls or puts with prices, IV, greeks and DTE.
"""

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Query, Request
from fastapi.responses import HTMLResponse

from app.services.options import ChainView, RiskFreeRate, option_chain
from app.services.quotes import lookup_quote
from app.tickers import InvalidTicker, normalize_ticker
from app.web.routes import (
    ConfigDep,
    MarketDataDep,
    SessionDep,
    _redirect,
    _refresh_window,
    templates,
)

router = APIRouter()


def _parse_date(text: str) -> date | None:
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


@router.get("/options", response_class=HTMLResponse)
def options_start(request: Request, ticker: Annotated[str, Query(max_length=50)] = ""):
    """A box to type a ticker; submitting it opens that ticker's chain."""
    error = None
    if ticker.strip():
        try:
            return _redirect(f"/options/{normalize_ticker(ticker)}")
        except InvalidTicker:
            error = "Please enter a valid US ticker, e.g. AAPL or BRK-B."
    return templates.TemplateResponse(
        request,
        "options_start.html",
        {"ticker": ticker.strip(), "error": error},
        status_code=400 if error else 200,
    )


@router.get("/options/{ticker}", response_class=HTMLResponse)
def option_chain_page(
    request: Request,
    ticker: str,
    session: SessionDep,
    market_data: MarketDataDep,
    config: ConfigDep,
    expiration: Annotated[str, Query(max_length=10)] = "",
    type: Annotated[str, Query(max_length=4)] = "call",
    refresh: bool = False,
):
    """`?expiration=YYYY-MM-DD&type=call|put`. `?refresh=1` is the Refresh button."""
    try:
        symbol = normalize_ticker(ticker)
    except InvalidTicker:
        return templates.TemplateResponse(
            request,
            "options_start.html",
            {"ticker": ticker, "error": "Please enter a valid US ticker, e.g. AAPL or BRK-B."},
            status_code=400,
        )
    option_type = "put" if type == "put" else "call"
    max_age = None if refresh else _refresh_window(config)
    quote = lookup_quote(session, market_data, symbol, max_age=max_age)
    if quote.status == "not_found":
        chain = ChainView(symbol, "no_options", option_type, notes=[f"Ticker {symbol} not found."])
    else:
        rates: RiskFreeRate = request.app.state.risk_free_rate
        chain = option_chain(
            session,
            market_data,
            rates,
            symbol,
            expiration=_parse_date(expiration),
            option_type=option_type,
            max_age=max_age,
            min_iv=config.options.min_implied_volatility_percent / 100,
            stock_price=quote.last_price,
        )
    return templates.TemplateResponse(
        request,
        "option_chain.html",
        {"quote": quote, "chain": chain, "refresh_minutes": config.market_data.refresh_minutes},
    )
