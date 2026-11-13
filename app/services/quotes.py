"""Get stock quotes: reuse recent stored ones, fetch the rest, fall back on failure.

F1.4: a quote carries the full stock row (ranges, volume, market cap, next earnings).
F1.5: stored data younger than the refresh window is reused; Refresh forces a new fetch.
F1.6: if the data source fails, show the last stored values marked "stale" with a note.
F1.7: an unknown ticker shows "not found" and nothing is saved.
"""

import logging
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Literal

from sqlmodel import Session, select

from app.db.models import Instrument, Quote
from app.providers.market_data import MarketData, MarketDataError, QuoteData, TickerNotFound
from app.tickers import normalize_ticker
from app.timeutils import as_utc, utc_now

log = logging.getLogger(__name__)

Status = Literal["ok", "stale", "not_found", "error"]


@dataclass(frozen=True)
class QuoteView:
    """What a page shows for one ticker."""

    symbol: str
    status: Status
    last_price: float | None = None
    change: float | None = None
    change_pct: float | None = None
    currency: str | None = None
    fetched_at: datetime | None = None  # UTC
    session_date: date | None = None
    day_low: float | None = None
    day_high: float | None = None
    year_low: float | None = None
    year_high: float | None = None
    volume: int | None = None
    market_cap: float | None = None
    next_earnings: date | None = None
    note: str | None = None  # error / staleness explanation shown on the page


def day_change(
    last_price: float, previous_close: float | None
) -> tuple[float | None, float | None]:
    """Change on the day in $ and %. None if there's no usable previous close."""
    if previous_close is None or previous_close <= 0:
        return None, None
    change = last_price - previous_close
    return change, change / previous_close * 100


def lookup_quote(
    session: Session,
    market_data: MarketData,
    raw_ticker: str,
    max_age: timedelta | None = None,
) -> QuoteView:
    """One ticker. With `max_age`, a stored quote younger than that is reused."""
    symbol = normalize_ticker(raw_ticker)
    return get_quotes(session, market_data, [symbol], max_age=max_age)[symbol]


def get_quotes(
    session: Session,
    market_data: MarketData,
    symbols: Sequence[str],
    max_age: timedelta | None = None,
    max_parallel: int = 1,
) -> dict[str, QuoteView]:
    """Quotes for already-normalized symbols, keyed by symbol.

    Stored quotes younger than `max_age` are used as they are (None = always fetch).
    The rest are fetched from the source, up to `max_parallel` at a time.
    """
    stored = {symbol: _load_stored(session, symbol) for symbol in dict.fromkeys(symbols)}
    to_fetch = [s for s, quote in stored.items() if not _is_fresh(quote, max_age)]

    fetched: dict[str, QuoteData | Exception] = {}
    if len(to_fetch) == 1 or max_parallel <= 1:
        fetched = {s: _fetch(market_data, s) for s in to_fetch}
    elif to_fetch:
        # Only the outside calls run in parallel; all database work stays on this thread.
        with ThreadPoolExecutor(max_workers=min(max_parallel, len(to_fetch))) as pool:
            results = pool.map(lambda s: _fetch(market_data, s), to_fetch)
            fetched = dict(zip(to_fetch, results, strict=True))

    views: dict[str, QuoteView] = {}
    for symbol, quote in stored.items():
        if symbol in fetched:
            views[symbol] = _resolve(session, symbol, fetched[symbol], quote)
        else:
            views[symbol] = _view_from_stored(symbol, quote, status="ok")
    session.commit()
    return views


def stored_quote_views(
    session: Session, symbols: Sequence[str], max_age: timedelta
) -> dict[str, QuoteView]:
    """Views from stored prices only, never fetching (the home page, IMP-12). A price older
    than `max_age` is marked stale; a stock never fetched has no price (status "error")."""
    wanted = set(symbols)
    if not wanted:
        return {}
    rows = session.exec(
        select(Instrument.symbol, Quote)
        .join(Quote, Quote.instrument_id == Instrument.id)  # type: ignore[arg-type]
        .where(Instrument.symbol.in_(wanted))  # type: ignore[attr-defined]
    ).all()
    stored = dict(rows)
    views = {}
    for symbol in wanted:
        quote = stored.get(symbol)
        if quote is None:
            views[symbol] = QuoteView(symbol=symbol, status="error", note="Not fetched yet.")
        else:
            status = "ok" if _is_fresh(quote, max_age) else "stale"
            views[symbol] = _view_from_stored(symbol, quote, status=status)
    return views


def _is_fresh(quote: Quote | None, max_age: timedelta | None) -> bool:
    if quote is None or max_age is None:
        return False
    return utc_now() - as_utc(quote.fetched_at) < max_age


def _fetch(market_data: MarketData, symbol: str) -> QuoteData | Exception:
    try:
        return market_data.get_quote(symbol)
    except Exception as exc:  # never let a failing source break the page
        if not isinstance(exc, MarketDataError):
            log.exception("Unexpected error from market data provider for %s", symbol)
        return exc


def _resolve(
    session: Session, symbol: str, result: QuoteData | Exception, stored: Quote | None
) -> QuoteView:
    if isinstance(result, QuoteData):
        return _view_from_stored(symbol, _save(session, result), status="ok")
    if isinstance(result, TickerNotFound):
        if stored is None:
            return QuoteView(symbol=symbol, status="not_found", note=f"Ticker {symbol} not found.")
        # We had data for it before, so the source is more likely having trouble.
        note = (
            f"The data source returned nothing for {symbol} just now. It may be temporarily "
            "down, or the ticker may have been delisted."
        )
        return _view_from_stored(symbol, stored, status="stale", note=note)
    reason = (str(result) if isinstance(result, MarketDataError) else "") or (
        "The data source failed."
    )
    if stored is None:
        return QuoteView(
            symbol=symbol, status="error", note=f"{reason} No stored data for {symbol} yet."
        )
    return _view_from_stored(symbol, stored, status="stale", note=f"{reason} Showing stored data.")


def _load_stored(session: Session, symbol: str) -> Quote | None:
    statement = (
        select(Quote)
        .join(Instrument, Quote.instrument_id == Instrument.id)  # type: ignore[arg-type]
        .where(Instrument.symbol == symbol)
    )
    return session.exec(statement).first()


def _view_from_stored(
    symbol: str, stored: Quote, status: Status, note: str | None = None
) -> QuoteView:
    change, change_pct = day_change(stored.last_price, stored.previous_close)
    return QuoteView(
        symbol=symbol,
        status=status,
        last_price=stored.last_price,
        change=change,
        change_pct=change_pct,
        currency=stored.currency,
        fetched_at=as_utc(stored.fetched_at),
        session_date=stored.session_date,
        day_low=stored.day_low,
        day_high=stored.day_high,
        year_low=stored.year_low,
        year_high=stored.year_high,
        volume=stored.volume,
        market_cap=stored.market_cap,
        next_earnings=stored.next_earnings,
        note=note,
    )


_ROW_FIELDS = (
    "last_price",
    "previous_close",
    "currency",
    "source",
    "session_date",
    "day_low",
    "day_high",
    "year_low",
    "year_high",
    "volume",
    "market_cap",
    "next_earnings",
)


def _save(session: Session, data: QuoteData) -> Quote:
    """Store the quote (one row per instrument, overwritten). Committed by the caller."""
    instrument = session.exec(select(Instrument).where(Instrument.symbol == data.symbol)).first()
    if instrument is None:
        instrument = Instrument(kind="stock", symbol=data.symbol)
        session.add(instrument)
        session.flush()
    quote = session.exec(select(Quote).where(Quote.instrument_id == instrument.id)).first()
    if quote is None:
        quote = Quote(
            instrument_id=instrument.id,
            last_price=data.last_price,
            fetched_at=data.fetched_at,
            source=data.source,
        )
    for field in _ROW_FIELDS:
        setattr(quote, field, getattr(data, field))
    quote.fetched_at = as_utc(data.fetched_at)
    session.add(quote)
    session.flush()
    return quote
