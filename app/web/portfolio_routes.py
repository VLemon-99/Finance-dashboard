"""Virtual portfolio pages (SPEC F3): the Bought/Sold form, the portfolio and its trades.

Changes are plain form posts followed by a redirect, like the watchlist pages.
"""

from datetime import date
from html import escape
from typing import Annotated

from fastapi import APIRouter, Form, Query, Request
from fastapi.responses import HTMLResponse
from sqlmodel import Session

from app.config import AppConfig
from app.providers.market_data import MarketData
from app.services import portfolio as pf
from app.timeutils import utc_now
from app.web.formatting import short_date
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


async def trade_not_found(request: Request, exc: Exception) -> HTMLResponse:
    return templates.TemplateResponse(request, "not_found.html", {}, status_code=404)


def _parse_date(text: str) -> date | None:
    try:
        return date.fromisoformat(text.strip())
    except ValueError:
        return None


def _parse_number(text: str) -> float | None:
    """'1,234.50' or '$2' -> float. Blank or unreadable -> None."""
    cleaned = text.strip().replace(",", "").replace("$", "")
    try:
        number = float(cleaned)
    except ValueError:
        return None
    return number if number == number and abs(number) != float("inf") else None


def _parse_whole(text: str) -> int | None:
    number = _parse_number(text)
    return int(number) if number is not None and number == int(number) else None


# --- the portfolio ----------------------------------------------------------------------


def _portfolio(
    request: Request,
    session: Session,
    market_data: MarketData,
    user_id: int,
    config: AppConfig,
    *,
    refresh: bool = False,
    error: str | None = None,
    status_code: int = 200,
):
    view = pf.portfolio_view(
        session,
        market_data,
        request.app.state.risk_free_rate,
        user_id,
        now=utc_now(),
        max_age=None if refresh else _refresh_window(config),
        min_iv=config.options.min_implied_volatility_percent / 100,
        max_parallel=config.market_data.max_parallel_fetches,
    )
    return templates.TemplateResponse(
        request,
        "portfolio.html",
        {
            "view": view,
            "trades": pf.list_trades(session, user_id),
            "refresh_minutes": config.market_data.refresh_minutes,
            "error": error,
        },
        status_code=status_code,
    )


@router.get("/portfolio", response_class=HTMLResponse)
def portfolio_page(
    request: Request,
    session: SessionDep,
    market_data: MarketDataDep,
    user_id: UserDep,
    config: ConfigDep,
    refresh: bool = False,
):
    """`?refresh=1` is the Refresh button."""
    return _portfolio(request, session, market_data, user_id, config, refresh=refresh)


@router.post("/portfolio/spreads", response_class=HTMLResponse)
def link_spread(
    request: Request,
    session: SessionDep,
    market_data: MarketDataDep,
    user_id: UserDep,
    config: ConfigDep,
    short_id: Annotated[int, Form()] = 0,
    long_id: Annotated[int, Form()] = 0,
):
    """Link a sold and a bought option position as one credit spread (F3.7)."""
    try:
        pf.link_spread(session, user_id, short_id, long_id, utc_now())
    except pf.TradeError as exc:
        return _portfolio(
            request, session, market_data, user_id, config, error=str(exc), status_code=400
        )
    return _redirect("/portfolio")


@router.post("/portfolio/spreads/{spread_id}/delete")
def unlink_spread(spread_id: int, session: SessionDep, user_id: UserDep):
    pf.unlink_spread(session, user_id, spread_id)
    return _redirect("/portfolio")


@router.post("/portfolio/positions/{instrument_id}/settle", response_class=HTMLResponse)
def settle_expired(
    request: Request,
    instrument_id: int,
    session: SessionDep,
    market_data: MarketDataDep,
    user_id: UserDep,
    config: ConfigDep,
    outcome: Annotated[str, Form(max_length=20)] = "",
):
    """Expired worthless, or assigned / exercised (F3.9)."""
    try:
        pf.settle_expired(session, user_id, instrument_id, outcome, utc_now())
    except pf.TradeError as exc:
        return _portfolio(
            request, session, market_data, user_id, config, error=str(exc), status_code=400
        )
    return _redirect("/portfolio")


@router.post("/portfolio/trades/{trade_id}/delete")
def delete_trade(trade_id: int, session: SessionDep, user_id: UserDep):
    pf.delete_trade(session, user_id, trade_id)
    return _redirect("/portfolio")


# --- the Bought/Sold form (F3.1, F3.2) ----------------------------------------------------


def _trade_form(
    request: Request,
    session: Session,
    market_data: MarketData,
    config: AppConfig,
    *,
    symbol: str,
    side: str,
    trade_date: date | None,
    quantity: str = "1",
    price: str | None = None,
    underlying_price: str | None = None,
    error: str | None = None,
    notice: str | None = None,
    status_code: int = 200,
):
    """The form, with the price filled in for the chosen date (unless one was typed)."""
    now = utc_now()
    max_age = _refresh_window(config)
    try:
        target = pf.resolve_target(session, market_data, symbol, max_age)
    except pf.TradeError as exc:
        return templates.TemplateResponse(
            request, "trade_form.html", {"target": None, "error": str(exc)}, status_code=400
        )
    when = trade_date or pf.eastern_date(now)
    return templates.TemplateResponse(
        request,
        "trade_form.html",
        {
            **_price_fields(session, market_data, target, when, max_age, price, underlying_price),
            "side": side if side in pf.SIDES else "bought",
            "quantity": quantity,
            "error": error,
            "notice": notice,
        },
        status_code=status_code,
    )


def _shown(value: float | None) -> str:
    return "" if value is None else f"{value:.2f}"


def _price_fields(
    session: Session,
    market_data: MarketData,
    target: pf.TradeTarget,
    when: date,
    max_age,
    price: str | None = None,
    underlying_price: str | None = None,
) -> dict:
    """What the price part of the form shows: the suggestion for `when`, unless typed."""
    suggestion = pf.suggest_price(session, market_data, target, when, utc_now(), max_age)
    return {
        "target": target,
        "trade_date": when,
        "today": pf.eastern_date(utc_now()),
        "suggestion": suggestion,
        "price": _shown(suggestion.price) if price is None else price,
        "underlying_price": (
            _shown(suggestion.underlying_price) if underlying_price is None else underlying_price
        ),
    }


@router.get("/portfolio/trade", response_class=HTMLResponse)
def trade_form(
    request: Request,
    session: SessionDep,
    market_data: MarketDataDep,
    config: ConfigDep,
    symbol: Annotated[str, Query(max_length=32)] = "",
    side: Annotated[str, Query(max_length=10)] = "bought",
    date: Annotated[str, Query(max_length=10)] = "",
    quantity: Annotated[str, Query(max_length=12)] = "1",
):
    """`?symbol=AAPL&side=bought`. Changing the date and pressing "Look up price" reloads
    this page with that day's price."""
    return _trade_form(
        request,
        session,
        market_data,
        config,
        symbol=symbol,
        side=side,
        trade_date=_parse_date(date),
        quantity=quantity,
    )


@router.get("/portfolio/trade/price", response_class=HTMLResponse)
def trade_price_fields(
    request: Request,
    session: SessionDep,
    market_data: MarketDataDep,
    config: ConfigDep,
    symbol: Annotated[str, Query(max_length=32)] = "",
    date: Annotated[str, Query(max_length=10)] = "",
):
    """Just the price part of the form for another date; HTMX swaps it in when the trade
    date changes, so the price always belongs to the date shown."""
    max_age = _refresh_window(config)
    try:
        target = pf.resolve_target(session, market_data, symbol, max_age)
    except pf.TradeError as exc:
        return HTMLResponse(f'<p class="note error">{escape(str(exc))}</p>', status_code=400)
    when = _parse_date(date) or pf.eastern_date(utc_now())
    return templates.TemplateResponse(
        request,
        "_trade_price.html",
        _price_fields(session, market_data, target, when, max_age),
    )


@router.post("/portfolio/trades", response_class=HTMLResponse)
def record_trade(
    request: Request,
    session: SessionDep,
    market_data: MarketDataDep,
    user_id: UserDep,
    config: ConfigDep,
    symbol: Annotated[str, Form(max_length=32)] = "",
    side: Annotated[str, Form(max_length=10)] = "",
    date: Annotated[str, Form(max_length=10)] = "",
    quantity: Annotated[str, Form(max_length=12)] = "",
    price: Annotated[str, Form(max_length=20)] = "",
    underlying_price: Annotated[str, Form(max_length=20)] = "",
    suggested_price: Annotated[str, Form(max_length=20)] = "",
    price_date: Annotated[str, Form(max_length=10)] = "",
):
    trade_date = _parse_date(date)
    filled_for = _parse_date(price_date)
    if (
        trade_date is not None
        and filled_for is not None
        and filled_for != trade_date
        and _parse_number(price) is not None
        and _parse_number(price) == _parse_number(suggested_price)
    ):
        # The date was changed after the price was filled in, and the price wasn't typed:
        # it belongs to another day. Show that day's price instead of saving (bug seen
        # 2026-11-03: a trade dated Oct 1 was saved at the Nov 3 price).
        return _trade_form(
            request,
            session,
            market_data,
            config,
            symbol=symbol,
            side=side,
            trade_date=trade_date,
            quantity=quantity,
            notice=(
                f"The price shown was for {short_date(filled_for)}, not "
                f"{short_date(trade_date)}. The price below is for {short_date(trade_date)}: "
                "check it and press Save trade again."
            ),
            status_code=400,
        )
    try:
        target = pf.resolve_target(session, market_data, symbol, _refresh_window(config))
        pf.record_trade(
            session,
            user_id,
            target,
            side=side,
            quantity=_parse_whole(quantity),
            price=_parse_number(price),
            trade_date=trade_date,
            underlying_price=_parse_number(underlying_price),
            suggested_price=_parse_number(suggested_price),
            now=utc_now(),
        )
    except pf.TradeError as exc:
        return _trade_form(
            request,
            session,
            market_data,
            config,
            symbol=symbol,
            side=side,
            trade_date=trade_date,
            quantity=quantity,
            price=price,
            underlying_price=underlying_price,
            error=str(exc),
            status_code=400,
        )
    return _redirect("/portfolio")
