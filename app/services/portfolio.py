"""Virtual portfolio: record Bought/Sold trades with an automatic price (SPEC F3.1, F3.2).

Only trades are stored. Positions, P&L and returns are calculated from them when shown.
Every function takes the user's id and only touches that user's trades.
"""

import logging
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta
from typing import Literal

from sqlmodel import Session, col, select

from app.db.models import Instrument, Spread, Transaction
from app.providers.market_data import MarketData
from app.services.options import (
    CONTRACT_SYMBOL_RE,
    ContractQuote,
    ContractView,
    RateView,
    RiskFreeRate,
    contract_instrument,
    contract_quotes,
    load_chain,
    stored_contract,
)
from app.services.options_math import (
    CONTRACT_SIZE,
    annualized_return,
    capital_at_risk_cash_secured_put,
    capital_at_risk_covered_call,
    capital_at_risk_credit_spread,
    capital_at_risk_poor_mans_covered_call,
    days_to_expiry,
    is_expired,
    mid_price,
    position_pnl,
    premium_received,
)
from app.services.positions import ClosedPosition, OpenPosition, TradeInput, build_positions
from app.services.quotes import get_quotes, lookup_quote
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
        if trade_date.weekday() >= 5:  # Saturday or Sunday
            note = (
                f"{when} was a {trade_date:%A}: the market was closed. Please pick a weekday, "
                "or enter the price."
            )
        else:
            note = f"The data source has no closing price for {when}. Please enter the price."
        return PriceSuggestion(None, underlying_price=underlying, note=note)
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


# --- the portfolio page (F3.3, F3.4, F3.5, F3.8) --------------------------------------------

Strategy = Literal["cash-secured put", "covered call", "uncovered call"]


@dataclass(frozen=True)
class OpenRow:
    """One open position as the portfolio page shows it."""

    target: TradeTarget
    position: OpenPosition
    current_price: float | None
    price_label: str  # "last price" | "mid" | "last trade"
    price_status: str  # ok | stale | error | expired
    fetched_at: datetime | None
    pnl: float | None
    pnl_pct: float | None
    note: str | None = None
    # Options only
    contract: ContractView | None = None
    dte: int | None = None
    expiring_today: bool = False
    expired: bool = False
    # Short options only (F3.4, F3.5, F3.8)
    strategy: Strategy | None = None
    premium: float | None = None  # $ received
    capital: float | None = None  # $ at risk
    dte_at_entry: int | None = None
    return_locked: float | None = None  # fraction, using DTE at entry
    return_remaining: float | None = None  # fraction, using DTE left today


@dataclass(frozen=True)
class ClosedRow:
    target: TradeTarget
    position: ClosedPosition


@dataclass(frozen=True)
class SpreadRow:
    """Two linked option positions shown and calculated as one credit spread (F3.7)."""

    spread_id: int
    short: OpenRow
    long: OpenRow
    contracts: int
    net_credit: float  # per share: short entry - long entry
    width: float  # difference between the strikes
    cost_to_close: float | None  # per share now: short price - long price
    pnl: float | None
    pnl_pct: float | None  # of the premium received
    premium: float  # $
    capital: float  # $
    dte: int
    dte_at_entry: int  # from the day the second leg was opened
    return_locked: float | None
    return_remaining: float | None

    @property
    def expiring_today(self) -> bool:
        return self.short.expiring_today

    @property
    def expired(self) -> bool:
        return self.short.expired


@dataclass(frozen=True)
class PmccRow:
    """A LEAP call bought and a shorter-dated call sold against it, as one position (F3.10)."""

    spread_id: int
    short: OpenRow  # the call sold
    long: OpenRow  # the LEAP
    contracts: int
    pnl: float | None
    pnl_pct: float | None  # of the capital at risk (what the LEAP cost)
    premium: float  # $ received for the sold call
    capital: float  # $ paid for the LEAP
    dte: int  # the sold call's
    dte_at_entry: int
    return_locked: float | None
    return_remaining: float | None

    @property
    def expiring_today(self) -> bool:
        return self.short.expiring_today

    @property
    def expired(self) -> bool:
        return self.short.expired


@dataclass(frozen=True)
class GroupItem:
    """One line in a ticker's options section: a single contract, or a linked pair."""

    kind: Literal["single", "credit spread", "poor man's covered call"]
    row: "OpenRow | SpreadRow | PmccRow"

    @property
    def legs(self) -> list[OpenRow]:
        return [self.row] if self.kind == "single" else [self.row.short, self.row.long]


@dataclass(frozen=True)
class TickerGroup:
    """All option positions on one stock, with their totals (owner's layout, 2026-11-04)."""

    ticker: str
    items: list[GroupItem]

    @property
    def contracts(self) -> int:
        return sum(leg.position.quantity for item in self.items for leg in item.legs)

    @property
    def pnl(self) -> float | None:
        """Sum of the positions' P&L; None if any position has no current price."""
        values = [leg.pnl for item in self.items for leg in item.legs]
        return None if any(v is None for v in values) else sum(values)


def group_by_ticker(
    singles: list[OpenRow], spreads: list[SpreadRow], pmccs: list[PmccRow]
) -> list[TickerGroup]:
    """Option positions grouped per stock, tickers A-Z; inside, by expiration then strike
    (a pair sorts by its sold leg)."""
    items = (
        [GroupItem("single", r) for r in singles]
        + [GroupItem("credit spread", r) for r in spreads]
        + [GroupItem("poor man's covered call", r) for r in pmccs]
    )
    by_ticker: dict[str, list[GroupItem]] = {}
    for item in items:
        by_ticker.setdefault(item.legs[0].target.underlying, []).append(item)

    def order(item: GroupItem):
        t = item.legs[0].target
        return (t.expiration, t.option_type, t.strike)

    return [
        TickerGroup(ticker, sorted(group, key=order)) for ticker, group in sorted(by_ticker.items())
    ]


@dataclass(frozen=True)
class PortfolioView:
    stocks: list[OpenRow]
    option_groups: list[TickerGroup]  # every open option position, per stock (F3.3, F3.7, F3.10)
    expired: list[OpenRow]  # expired option positions waiting for their outcome (F3.9)
    closed: list[ClosedRow]
    rate: RateView | None
    has_trades: bool
    link_shorts: list[OpenRow]  # choices for linking a credit spread
    link_longs: list[OpenRow]


@dataclass(frozen=True)
class _Loaded:
    """One user's positions, calculated from their trades."""

    open: list[OpenPosition]
    closed: list[ClosedPosition]
    targets: dict[int, TradeTarget]
    instruments: dict[int, Instrument]
    has_trades: bool

    def open_position(self, instrument_id: int) -> OpenPosition | None:
        return next((p for p in self.open if p.instrument_id == instrument_id), None)


def _load(session: Session, user_id: int) -> _Loaded:
    rows = session.exec(
        select(Transaction, Instrument)
        .join(Instrument, col(Instrument.id) == col(Transaction.instrument_id))
        .where(Transaction.user_id == user_id)
    ).all()
    targets = {instrument.id: _target(instrument) for _, instrument in rows}
    open_positions, closed_positions = build_positions(
        TradeInput(
            id=trade.id,
            instrument_id=trade.instrument_id,
            side=trade.side,
            quantity=trade.quantity,
            price=trade.price,
            trade_date=trade.trade_date,
            size=targets[trade.instrument_id].size,
            underlying_price=trade.underlying_price,
            reason=trade.reason,
        )
        for trade, _ in rows
    )
    return _Loaded(
        open=open_positions,
        closed=closed_positions,
        targets=targets,
        instruments={instrument.id: instrument for _, instrument in rows},
        has_trades=bool(rows),
    )


def portfolio_view(
    session: Session,
    market_data: MarketData,
    rates: RiskFreeRate,
    user_id: int,
    *,
    now: datetime,
    max_age: timedelta | None,
    min_iv: float,
    max_parallel: int = 1,
) -> PortfolioView:
    """Positions calculated from the user's trades, priced with current data."""
    loaded = _load(session, user_id)
    targets = loaded.targets
    stock_positions = [p for p in loaded.open if targets[p.instrument_id].kind == "stock"]
    option_positions = [p for p in loaded.open if targets[p.instrument_id].kind == "option"]
    quotes = get_quotes(
        session,
        market_data,
        [targets[p.instrument_id].symbol for p in stock_positions],
        max_age=max_age,
        max_parallel=max_parallel,
    )
    contract_prices, rate = contract_quotes(
        session,
        market_data,
        rates,
        [loaded.instruments[p.instrument_id] for p in option_positions],
        max_age=max_age,
        min_iv=min_iv,
    )

    # Linked positions first: their short calls don't need shares to be covered.
    active, spread_notes = _active_links(session, user_id, loaded)
    in_spread = {
        i
        for link in active
        for i in (link.spread.short_instrument_id, link.spread.long_instrument_id)
    }

    stocks = [_stock_row(targets[p.instrument_id], p, quotes) for p in stock_positions]
    shares_held = {
        row.target.symbol: row.position.quantity for row in stocks if not row.position.is_short
    }
    covered = _covered_calls(
        [p for p in option_positions if p.instrument_id not in in_spread], targets, shares_held
    )
    today = eastern_date(now)
    option_rows = {
        p.instrument_id: _option_row(
            targets[p.instrument_id],
            p,
            contract_prices[targets[p.instrument_id].symbol],
            today,
            covered,
            in_spread=p.instrument_id in in_spread,
            note=spread_notes.get(p.instrument_id),
        )
        for p in option_positions
    }

    def legs(link: _Link):
        return (
            link.spread.id,
            option_rows[link.spread.short_instrument_id],
            option_rows[link.spread.long_instrument_id],
        )

    spreads = [_spread_row(*legs(link)) for link in active if link.kind == "credit spread"]
    pmccs = [_pmcc_row(*legs(link)) for link in active if link.kind != "credit spread"]
    options = [r for i, r in option_rows.items() if i not in in_spread]
    free = [r for r in options if not r.expired]

    def by_contract(r):
        return (r.target.underlying, r.target.expiration, r.target.option_type, r.target.strike)

    stocks.sort(key=lambda r: r.target.symbol)
    options.sort(key=by_contract)
    spreads.sort(key=lambda r: by_contract(r.short))
    pmccs.sort(key=lambda r: by_contract(r.short))
    closed = sorted(
        (ClosedRow(targets[c.instrument_id], c) for c in loaded.closed),
        key=lambda r: r.position.closed_on,
        reverse=True,
    )
    return PortfolioView(
        stocks=stocks,
        option_groups=group_by_ticker(options, spreads, pmccs),
        expired=sorted((r for r in option_rows.values() if r.expired), key=by_contract),
        closed=closed,
        rate=rate,
        has_trades=loaded.has_trades,
        link_shorts=sorted((r for r in free if r.position.is_short), key=by_contract),
        link_longs=sorted((r for r in free if not r.position.is_short), key=by_contract),
    )


def _stock_row(target: TradeTarget, position: OpenPosition, quotes) -> OpenRow:
    quote = quotes[target.symbol]
    pnl, pct = position_pnl(
        position.direction, position.avg_price, quote.last_price, position.quantity
    )
    return OpenRow(
        target=target,
        position=position,
        current_price=quote.last_price,
        price_label="last price",
        price_status=quote.status,
        fetched_at=quote.fetched_at,
        pnl=pnl,
        pnl_pct=pct,
        note=quote.note,
    )


def _covered_calls(
    positions: list[OpenPosition], targets: dict[int, TradeTarget], shares_held: dict[str, int]
) -> set[int]:
    """Instrument ids of the short calls covered by shares held, oldest first (F3.5).
    A position is covered only if all its contracts are (100 shares each)."""
    left = dict(shares_held)
    covered = set()
    short_calls = sorted(
        (p for p in positions if p.is_short and targets[p.instrument_id].option_type == "call"),
        key=lambda p: (p.opened_on, p.instrument_id),
    )
    for position in short_calls:
        underlying = targets[position.instrument_id].underlying
        needed = position.quantity * CONTRACT_SIZE
        if left.get(underlying, 0) >= needed:
            left[underlying] -= needed
            covered.add(position.instrument_id)
    return covered


def _option_row(
    target: TradeTarget,
    position: OpenPosition,
    quote: ContractQuote,
    today: date,
    covered: set[int],
    *,
    in_spread: bool = False,
    note: str | None = None,
) -> OpenRow:
    view = quote.view
    price, label = None, "mid"
    if view is not None:
        price, label = (
            (view.mid, "mid") if view.mid is not None else (view.last_price, "last trade")
        )
    pnl, pct = position_pnl(
        position.direction, position.avg_price, price, position.quantity, CONTRACT_SIZE
    )
    dte = days_to_expiry(target.expiration, today)
    expired = quote.status == "expired"
    row = OpenRow(
        target=target,
        position=position,
        current_price=price,
        price_label=label,
        price_status=quote.status,
        fetched_at=view.fetched_at if view else None,
        pnl=pnl,
        pnl_pct=pct,
        note=" ".join(filter(None, [note, quote.note])) or None,
        contract=view,
        dte=dte,
        expiring_today=dte == 0 and not expired,
        expired=expired,
    )
    if not position.is_short or in_spread:
        return row

    dte_at_entry = days_to_expiry(target.expiration, position.opened_on)
    premium = premium_received(position.avg_price, position.quantity)
    if target.option_type == "put":
        strategy: Strategy = "cash-secured put"
        capital = capital_at_risk_cash_secured_put(target.strike, premium, position.quantity)
    elif position.instrument_id in covered:
        strategy = "covered call"
        capital = (
            capital_at_risk_covered_call(position.underlying_avg, position.quantity)
            if position.underlying_avg
            else None
        )
    else:
        return replace(row, strategy="uncovered call", premium=premium, dte_at_entry=dte_at_entry)
    return replace(
        row,
        strategy=strategy,
        premium=premium,
        capital=capital,
        dte_at_entry=dte_at_entry,
        return_locked=annualized_return(premium, capital, dte_at_entry) if capital else None,
        return_remaining=annualized_return(premium, capital, dte) if capital else None,
    )


# --- linked positions: credit spreads (F3.7) and poor man's covered calls (F3.10) --------

LinkKind = Literal["credit spread", "poor man's covered call"]


def link_problem(
    short: OpenPosition, long: OpenPosition, targets: dict[int, TradeTarget]
) -> tuple[LinkKind | None, str | None]:
    """What two open option positions can be linked as, or why they can't: (kind, problem).

    Same expiration date -> credit spread. The bought call expiring later -> poor man's
    covered call.
    """
    s, lg = targets[short.instrument_id], targets[long.instrument_id]
    if s.kind != "option" or lg.kind != "option":
        return None, "Both legs must be option contracts."
    if not short.is_short or long.is_short:
        return None, "Pick a contract you sold and a contract you bought."
    if (s.underlying, s.option_type) != (lg.underlying, lg.option_type):
        return None, "Both legs must be on the same stock, and both calls or both puts."
    if short.quantity != long.quantity:
        return None, (
            f"The legs have different numbers of contracts ({short.quantity} sold, "
            f"{long.quantity} bought)."
        )

    if s.expiration == lg.expiration:
        if s.strike == lg.strike:
            return None, "The two legs must have different strikes."
        if short.avg_price - long.avg_price <= 0:
            return None, "These legs were opened for a net debit, not a credit."
        further_out = lg.strike < s.strike if s.option_type == "put" else lg.strike > s.strike
        if not further_out:
            side = "lower" if s.option_type == "put" else "higher"
            return None, (
                f"In a {s.option_type} credit spread, the {s.option_type} you bought has the "
                f"{side} strike."
            )
        return "credit spread", None

    if lg.expiration < s.expiration:
        return None, (
            "The contract you bought must expire on the same date as the one you sold (a "
            "credit spread) or later (a poor man's covered call)."
        )
    if s.option_type != "call":
        return None, (
            "Puts with different expiration dates can't be linked yet (a poor man's "
            "covered put isn't supported)."
        )
    if s.strike <= lg.strike:
        return None, (
            "In a poor man's covered call, the call you sold must have a higher strike than "
            "the call you bought."
        )
    return "poor man's covered call", None


@dataclass(frozen=True)
class _Link:
    spread: Spread
    kind: LinkKind


def _active_links(
    session: Session, user_id: int, loaded: _Loaded
) -> tuple[list[_Link], dict[int, str]]:
    """Links whose legs are both open and still match, plus a note for the legs of links
    that no longer match (they're shown as separate positions). A link with a closed leg
    simply ends: that's normal, e.g. when a poor man's covered call's short call expires."""
    active, notes = [], {}
    for spread in session.exec(select(Spread).where(Spread.user_id == user_id)):
        short = loaded.open_position(spread.short_instrument_id)
        long = loaded.open_position(spread.long_instrument_id)
        if short is None or long is None:
            continue
        kind, problem = link_problem(short, long, loaded.targets)
        if kind is not None:
            active.append(_Link(spread, kind))
            continue
        note = f"Linked, but the legs no longer match: {problem} Shown separately."
        for position in (short, long):
            notes[position.instrument_id] = note
    return active, notes


def _spread_row(spread_id: int, short: OpenRow, long: OpenRow) -> SpreadRow:
    target = short.target
    contracts = short.position.quantity
    net_credit = short.position.avg_price - long.position.avg_price
    premium = premium_received(net_credit, contracts)
    capital = capital_at_risk_credit_spread(
        target.strike, long.target.strike, net_credit, contracts
    )
    opened = max(short.position.opened_on, long.position.opened_on)
    dte_at_entry = days_to_expiry(target.expiration, opened)
    cost = (
        short.current_price - long.current_price
        if short.current_price is not None and long.current_price is not None
        else None
    )
    pnl = short.pnl + long.pnl if short.pnl is not None and long.pnl is not None else None
    return SpreadRow(
        spread_id=spread_id,
        short=short,
        long=long,
        contracts=contracts,
        net_credit=net_credit,
        width=abs(target.strike - long.target.strike),
        cost_to_close=cost,
        pnl=pnl,
        pnl_pct=pnl / premium * 100 if pnl is not None and premium > 0 else None,
        premium=premium,
        capital=capital,
        dte=short.dte,
        dte_at_entry=dte_at_entry,
        return_locked=annualized_return(premium, capital, dte_at_entry),
        return_remaining=annualized_return(premium, capital, short.dte),
    )


def _pmcc_row(spread_id: int, short: OpenRow, long: OpenRow) -> "PmccRow":
    """A poor man's covered call: premium from the sold call, capital = what the bought
    call cost (F3.10). Days to expiration are the sold call's."""
    contracts = short.position.quantity
    premium = premium_received(short.position.avg_price, contracts)
    capital = capital_at_risk_poor_mans_covered_call(long.position.avg_price, contracts)
    opened = max(short.position.opened_on, long.position.opened_on)
    dte_at_entry = days_to_expiry(short.target.expiration, opened)
    pnl = short.pnl + long.pnl if short.pnl is not None and long.pnl is not None else None
    return PmccRow(
        spread_id=spread_id,
        short=short,
        long=long,
        contracts=contracts,
        pnl=pnl,
        pnl_pct=pnl / capital * 100 if pnl is not None and capital > 0 else None,
        premium=premium,
        capital=capital,
        dte=short.dte,
        dte_at_entry=dte_at_entry,
        return_locked=annualized_return(premium, capital, dte_at_entry),
        return_remaining=annualized_return(premium, capital, short.dte),
    )


def link_spread(
    session: Session, user_id: int, short_instrument_id: int, long_instrument_id: int, now: datetime
) -> Spread:
    """Link a sold and a bought option position as one credit spread (F3.7) or poor man's
    covered call (F3.10)."""
    loaded = _load(session, user_id)
    short = loaded.open_position(short_instrument_id)
    long = loaded.open_position(long_instrument_id)
    if short is None or long is None:
        raise TradeError("Pick two open option positions to link.")
    active, _ = _active_links(session, user_id, loaded)
    in_use = {
        i
        for link in active
        for i in (link.spread.short_instrument_id, link.spread.long_instrument_id)
    }
    if short_instrument_id in in_use or long_instrument_id in in_use:
        raise TradeError("One of these positions is already linked. Unlink it first.")
    kind, problem = link_problem(short, long, loaded.targets)
    if problem:
        raise TradeError(problem)
    if is_expired(loaded.targets[short_instrument_id].expiration, now):
        raise TradeError("These contracts have expired.")
    # Old links for these contracts (from positions closed since) are replaced.
    for old in session.exec(
        select(Spread).where(
            Spread.user_id == user_id,
            col(Spread.short_instrument_id).in_([short_instrument_id, long_instrument_id])
            | col(Spread.long_instrument_id).in_([short_instrument_id, long_instrument_id]),
        )
    ):
        session.delete(old)
    session.flush()
    spread = Spread(
        user_id=user_id,
        short_instrument_id=short_instrument_id,
        long_instrument_id=long_instrument_id,
    )
    session.add(spread)
    session.commit()
    return spread


def unlink_spread(session: Session, user_id: int, spread_id: int) -> None:
    spread = session.get(Spread, spread_id)
    if spread is None or spread.user_id != user_id:
        raise TradeNotFound(spread_id)
    session.delete(spread)
    session.commit()


# --- after expiration (F3.9) ------------------------------------------------------------------

OUTCOMES = ("worthless", "assigned")


def settle_expired(
    session: Session, user_id: int, instrument_id: int, outcome: str, now: datetime
) -> None:
    """Record how an expired option position ended.

    - "worthless": the option is closed at $0 on the expiration date.
    - "assigned" (sold) / exercised (bought): the option is closed at $0, and 100 shares
      per contract are bought or sold at the strike on the expiration date. The premium
      stays as the option's profit or loss.
    """
    if outcome not in OUTCOMES:
        raise TradeError("Please choose Expired worthless or Assigned / exercised.")
    loaded = _load(session, user_id)
    position = loaded.open_position(instrument_id)
    target = loaded.targets.get(instrument_id)
    if position is None or target is None or target.kind != "option":
        raise TradeError("That option position isn't open.")
    if not is_expired(target.expiration, now):
        raise TradeError("This contract hasn't expired yet.")

    closing_side = "bought" if position.is_short else "sold"
    if outcome == "worthless":
        reason = "expired"
    else:
        reason = "assigned" if position.is_short else "exercised"
    session.add(
        Transaction(
            user_id=user_id,
            instrument_id=instrument_id,
            side=closing_side,
            quantity=position.quantity,
            price=0.0,
            trade_date=target.expiration,
            price_source="auto",
            reason=reason,
        )
    )
    if outcome == "assigned":
        # Short put assigned / long call exercised -> shares bought; the other two -> sold.
        buys_shares = (target.option_type == "put") == position.is_short
        stock = session.exec(
            select(Instrument).where(Instrument.symbol == target.underlying)
        ).first()
        if stock is None:
            stock = Instrument(kind="stock", symbol=target.underlying)
            session.add(stock)
            session.flush()
        session.add(
            Transaction(
                user_id=user_id,
                instrument_id=stock.id,
                side="bought" if buys_shares else "sold",
                quantity=position.quantity * CONTRACT_SIZE,
                price=target.strike,
                trade_date=target.expiration,
                price_source="auto",
                reason=reason,
            )
        )
    session.commit()
