"""Look up a stock quote: fetch it, save it, and fall back to the stored copy on failure.

F1.6: if the data source fails, show the last stored values marked "stale" with a note.
F1.7: an unknown ticker shows "not found" and nothing is saved.
"""

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from sqlmodel import Session, select

from app.db.models import Instrument, Quote
from app.providers.market_data import MarketData, MarketDataError, QuoteData, TickerNotFound
from app.tickers import normalize_ticker
from app.timeutils import as_utc

log = logging.getLogger(__name__)

Status = Literal["ok", "stale", "not_found", "error"]


@dataclass(frozen=True)
class QuoteView:
    """What the page shows for one ticker."""

    symbol: str
    status: Status
    last_price: float | None = None
    change: float | None = None
    change_pct: float | None = None
    currency: str | None = None
    fetched_at: datetime | None = None  # UTC
    note: str | None = None  # error / staleness explanation shown on the page


def day_change(
    last_price: float, previous_close: float | None
) -> tuple[float | None, float | None]:
    """Change on the day in $ and %. None if there's no usable previous close."""
    if previous_close is None or previous_close <= 0:
        return None, None
    change = last_price - previous_close
    return change, change / previous_close * 100


def lookup_quote(session: Session, market_data: MarketData, raw_ticker: str) -> QuoteView:
    """Fetch a fresh quote and save it; on failure, return the stored one marked stale."""
    symbol = normalize_ticker(raw_ticker)
    try:
        data = market_data.get_quote(symbol)
    except TickerNotFound:
        stored = _load_stored(session, symbol)
        if stored is None:
            return QuoteView(symbol=symbol, status="not_found", note=f"Ticker {symbol} not found.")
        # We had data for it before, so the source is more likely having trouble.
        note = (
            f"The data source returned nothing for {symbol} just now. It may be temporarily "
            "down, or the ticker may have been delisted."
        )
        return _view_from_stored(symbol, stored, status="stale", note=note)
    except Exception as exc:  # never let a failing source break the page
        if not isinstance(exc, MarketDataError):
            log.exception("Unexpected error from market data provider for %s", symbol)
        reason = str(exc) or "The data source failed."
        stored = _load_stored(session, symbol)
        if stored is None:
            return QuoteView(
                symbol=symbol, status="error", note=f"{reason} No stored data for {symbol} yet."
            )
        return _view_from_stored(
            symbol, stored, status="stale", note=f"{reason} Showing stored data."
        )

    _save(session, data)
    change, change_pct = day_change(data.last_price, data.previous_close)
    return QuoteView(
        symbol=symbol,
        status="ok",
        last_price=data.last_price,
        change=change,
        change_pct=change_pct,
        currency=data.currency,
        fetched_at=as_utc(data.fetched_at),
    )


def _load_stored(session: Session, symbol: str) -> Quote | None:
    statement = (
        select(Quote)
        .join(Instrument, Quote.instrument_id == Instrument.id)  # type: ignore[arg-type]
        .where(Instrument.symbol == symbol)
    )
    return session.exec(statement).first()


def _view_from_stored(symbol: str, stored: Quote, status: Status, note: str) -> QuoteView:
    change, change_pct = day_change(stored.last_price, stored.previous_close)
    return QuoteView(
        symbol=symbol,
        status=status,
        last_price=stored.last_price,
        change=change,
        change_pct=change_pct,
        currency=stored.currency,
        fetched_at=as_utc(stored.fetched_at),
        note=note,
    )


def _save(session: Session, data: QuoteData) -> None:
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
    quote.last_price = data.last_price
    quote.previous_close = data.previous_close
    quote.currency = data.currency
    quote.fetched_at = as_utc(data.fetched_at)
    quote.source = data.source
    session.add(quote)
    session.commit()
