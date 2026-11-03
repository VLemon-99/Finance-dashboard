"""Virtual portfolio: record Bought/Sold trades with an automatic price (SPEC F3.1, F3.2).

Only trades are stored. Positions, P&L and returns are calculated from them when shown.
Every function takes the user's id and only touches that user's trades.
"""

import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Literal

from sqlmodel import Session, col, select

from app.db.models import Instrument, Transaction
from app.providers.market_data import MarketData
from app.services.options import (
    CONTRACT_SYMBOL_RE,
    contract_instrument,
    load_chain,
    stored_contract,
)
from app.services.options_math import is_expired, mid_price
from app.services.quotes import lookup_quote
from app.tickers import InvalidTicker, normalize_ticker
from app.timeutils import EASTERN, as_utc
from app.web.formatting import short_date

log = logging.getLogger(__name__)

SIDES = ("bought", "sold")
MAX_QUANTITY = 1_000_000
MAX_PRICE = 10_000_000.0
EARLIEST_TRADE_DATE = date(2000, 1, 1)


class TradeError(ValueError):
    """The trade can't be recorded as entered. The message is shown on the page."""


class TradeNotFound(LookupError):
    """No such trade (or it belongs to someone else)."""


@dataclass(frozen=True)
class TradeTarget:
    """The stock or option contract being traded."""

    instrument_id: int
    symbol: str  # ticker, or the contract symbol
    kind: Literal["stock", "option"]
    underlying: str | None = None  # options only
    option_type: str | None = None
    strike: float | None = None
    expiration: date | None = None

    @property
    def size(self) -> int:
        """Shares per unit: 1 for stocks, 100 per option contract."""
        return 100 if self.kind == "option" else 1


@dataclass(frozen=True)
class PriceSuggestion:
    """The price the form fills in (SPEC F3.2). `price` is None when there isn't one."""

    price: float | None
    source: str = ""  # e.g. "mid", "last price", "closing price on Oct 30, 2026"
    underlying_price: float | None = None  # options: the stock price for that time
    note: str | None = None  # why there's no price, or that it's stale


def eastern_date(now: datetime) -> date:
    return as_utc(now).astimezone(EASTERN).date()


# --- what's being traded ---------------------------------------------------------------


def resolve_target(
    session: Session, market_data: MarketData, raw_symbol: str, max_age: timedelta | None
) -> TradeTarget:
    """A stock ticker (checked with the data source) or a contract seen on an option chain."""
    symbol = (raw_symbol or "").strip().upper()
    if CONTRACT_SYMBOL_RE.fullmatch(symbol):
        stored = stored_contract(session, symbol)
        if stored is None:
            raise TradeError(
                "Couldn't find that option contract. Open its option chain, then press "
                "Bought or Sold there."
            )
        instrument = contract_instrument(session, stored)
        session.commit()
        return _target(instrument)

    try:
        symbol = normalize_ticker(symbol)
    except InvalidTicker:
        raise TradeError("Please enter a valid US ticker, e.g. AAPL or BRK-B.") from None
    quote = lookup_quote(session, market_data, symbol, max_age=max_age)
    if quote.status == "not_found":
        raise TradeError(f"Ticker {symbol} not found.")
    if quote.status == "error":
        raise TradeError(
            f"Couldn't check {symbol} with the data source just now. Please try again in a "
            "few minutes."
        )
    instrument = session.exec(select(Instrument).where(Instrument.symbol == symbol)).one()
    return _target(instrument)


def _target(instrument: Instrument) -> TradeTarget:
    if instrument.kind == "option":
        return TradeTarget(
            instrument_id=instrument.id,
            symbol=instrument.symbol,
            kind="option",
            underlying=instrument.underlying,
            option_type=instrument.option_type,
            strike=instrument.strike,
            expiration=instrument.expiration,
        )
    return TradeTarget(instrument_id=instrument.id, symbol=instrument.symbol, kind="stock")


# --- the automatic price (F3.2) -----------------------------------------------------------


def suggest_price(
    session: Session,
    market_data: MarketData,
    target: TradeTarget,
    trade_date: date,
    now: datetime,
    max_age: timedelta | None,
) -> PriceSuggestion:
    """Today: the current mid (or last if there's no bid/ask). Past date: that day's close.
    Never raises: when there's no price, the note asks for one to be entered."""
    today = eastern_date(now)
    if trade_date > today:
        return PriceSuggestion(None, note="The trade date can't be in the future.")
    if target.kind == "option" and trade_date > target.expiration:
        return PriceSuggestion(
            None, note=f"This contract expired on {short_date(target.expiration)}."
        )
    if trade_date == today:
        if target.kind == "option":
            return _current_option_price(session, market_data, target, now, max_age)
        quote = lookup_quote(session, market_data, target.symbol, max_age=max_age)
        if quote.last_price is None:
            return PriceSuggestion(None, note=_ask_for_price(quote.note))
        note = quote.note if quote.status == "stale" else None
        return PriceSuggestion(quote.last_price, "last price", note=note)
    return _closing_prices(market_data, target, trade_date)


def _current_option_price(
    session: Session,
    market_data: MarketData,
    target: TradeTarget,
    now: datetime,
    max_age: timedelta | None,
) -> PriceSuggestion:
    note = None
    if not is_expired(target.expiration, now):
        chain = load_chain(session, market_data, target.underlying, target.expiration, max_age)
        note = chain.note
    row = stored_contract(session, target.symbol)
    if row is None:
        return PriceSuggestion(None, note=_ask_for_price(note))
    mid = mid_price(row.bid, row.ask)
    price, source = (mid, "mid") if mid is not None else (row.last_price, "last price")
    if price is None:
        return PriceSuggestion(
            None, underlying_price=row.underlying_price, note=_ask_for_price(note)
        )
    return PriceSuggestion(price, source, row.underlying_price, note)


def _closing_prices(
    market_data: MarketData, target: TradeTarget, trade_date: date
) -> PriceSuggestion:
    when = short_date(trade_date)
    try:
        close = market_data.get_daily_close(target.symbol, trade_date)
        underlying = (
            market_data.get_daily_close(target.underlying, trade_date)
            if target.kind == "option"
            else None
        )
    except Exception as exc:  # never let a failing source break the form
        log.info("No closing price for %s on %s: %s", target.symbol, trade_date, exc)
        return PriceSuggestion(
            None,
            note=f"Couldn't get the closing price for {when} from the data source just now. "
            "Please enter the price.",
        )
    if close is None:
        return PriceSuggestion(
            None,
            underlying_price=underlying,
            note=f"The data source has no closing price for {when}. Please enter the price.",
        )
    return PriceSuggestion(close, f"closing price on {when}", underlying)


def _ask_for_price(reason: str | None) -> str:
    return " ".join(filter(None, [reason, "No current price is available. Please enter one."]))


# --- recording and deleting trades (F3.1) ------------------------------------------------


def record_trade(
    session: Session,
    user_id: int,
    target: TradeTarget,
    *,
    side: str,
    quantity: int | None,
    price: float | None,
    trade_date: date | None,
    underlying_price: float | None,
    suggested_price: float | None,
    now: datetime,
    reason: str = "trade",
) -> Transaction:
    """Check and store one trade. Sold with no position opens a short position (F3.1)."""
    if side not in SIDES:
        raise TradeError("Please choose Bought or Sold.")
    if quantity is None or not 1 <= quantity <= MAX_QUANTITY:
        unit = "contracts" if target.kind == "option" else "shares"
        raise TradeError(f"Please enter the number of {unit} (a whole number, at least 1).")
    if price is None:
        raise TradeError("Please enter the price.")
    if not 0 <= price <= MAX_PRICE:
        raise TradeError("Please enter a price of 0 or more.")
    if trade_date is None:
        raise TradeError("Please enter the trade date.")
    if trade_date > eastern_date(now):
        raise TradeError("The trade date can't be in the future.")
    if trade_date < EARLIEST_TRADE_DATE:
        raise TradeError(f"The trade date can't be before {short_date(EARLIEST_TRADE_DATE)}.")
    if target.kind == "option":
        if trade_date > target.expiration:
            raise TradeError(f"This contract expired on {short_date(target.expiration)}.")
        if underlying_price is not None and not 0 < underlying_price <= MAX_PRICE:
            raise TradeError("Please enter the stock price as a number above 0.")
        if side == "sold" and target.option_type == "call" and underlying_price is None:
            raise TradeError(
                "Please enter the stock price at the time of the sale. A covered call's "
                "return is based on it (SPEC F3.5)."
            )
    else:
        underlying_price = None

    auto = suggested_price is not None and abs(price - suggested_price) < 0.005
    trade = Transaction(
        user_id=user_id,
        instrument_id=target.instrument_id,
        side=side,
        quantity=quantity,
        price=price,
        trade_date=trade_date,
        price_source="auto" if auto else "manual",
        underlying_price=underlying_price,
        reason=reason,
    )
    session.add(trade)
    session.commit()
    session.refresh(trade)
    return trade


@dataclass(frozen=True)
class TradeRow:
    trade: Transaction
    target: TradeTarget


def list_trades(session: Session, user_id: int) -> list[TradeRow]:
    """All the user's trades, newest trade date first."""
    statement = (
        select(Transaction, Instrument)
        .join(Instrument, col(Instrument.id) == col(Transaction.instrument_id))
        .where(Transaction.user_id == user_id)
        .order_by(col(Transaction.trade_date).desc(), col(Transaction.id).desc())
    )
    return [TradeRow(t, _target(i)) for t, i in session.exec(statement)]


def delete_trade(session: Session, user_id: int, trade_id: int) -> None:
    trade = session.get(Transaction, trade_id)
    if trade is None or trade.user_id != user_id:
        raise TradeNotFound(trade_id)
    session.delete(trade)
    session.commit()
