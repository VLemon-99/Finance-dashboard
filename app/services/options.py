"""Option chains: reuse recent stored prices, fetch the rest, fall back on failure; add greeks.

F2.1: pick an expiration date, switch calls/puts.
F2.2: each strike shows bid, ask, mid, last, volume, open interest, IV, greeks and DTE.
F2.3: greeks are calculated here (via options_math), not taken from the data source.
F2.4: the strike nearest the stock price, and which strikes are in the money.
Same freshness rules as stocks: prices younger than the refresh window are reused, Refresh
always fetches (F1.5), and if the source fails the last stored prices are shown "stale" (F1.6).
"""

import logging
import re
import threading
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Literal

from sqlalchemy import func
from sqlmodel import Session, col, select

from app.db.models import Instrument, OptionQuote
from app.providers.market_data import MarketData, MarketDataError, OptionChainData
from app.services.options_math import (
    Greeks,
    OptionType,
    black_scholes,
    days_to_expiry,
    is_expired,
    is_in_the_money,
    mid_price,
    nearest_strike,
    years_to_expiry,
)
from app.timeutils import EASTERN, as_utc, format_eastern, utc_now

log = logging.getLogger(__name__)

ChainStatus = Literal["ok", "stale", "error"]

# OCC-style option symbol: root, YYMMDD, C or P, strike x 1000 in 8 digits.
CONTRACT_SYMBOL_RE = re.compile(r"^[A-Z][A-Z0-9]{0,5}\d{6}[CP]\d{8}$")


# --- the risk-free rate ---------------------------------------------------------------


@dataclass(frozen=True)
class RateView:
    rate: float  # a fraction: 0.043 = 4.3%
    is_fallback: bool = False  # True = the rate from config/settings.yaml
    note: str | None = None


class RiskFreeRate:
    """Remembers the last 13-week T-bill rate fetched, so it's asked for at most once per
    refresh window. Kept in memory: it's one number and is fetched again after a restart."""

    def __init__(self, fallback: float) -> None:
        self.fallback = fallback
        self._rate: float | None = None
        self._fetched_at: datetime | None = None
        self._lock = threading.Lock()

    def get(self, market_data: MarketData, max_age: timedelta | None) -> RateView:
        with self._lock:
            now = utc_now()
            if (
                self._rate is not None
                and self._fetched_at is not None
                and max_age is not None
                and now - self._fetched_at < max_age
            ):
                return RateView(self._rate)
            try:
                rate = market_data.get_risk_free_rate()
            except Exception as exc:  # never let the rate break the page
                if not isinstance(exc, MarketDataError):
                    log.exception("Unexpected error fetching the risk-free rate")
                if self._rate is not None and self._fetched_at is not None:
                    return RateView(
                        self._rate,
                        note="Couldn't update the 13-week T-bill rate just now; the greeks use "
                        f"the rate fetched {format_eastern(self._fetched_at)}.",
                    )
                return RateView(
                    self.fallback,
                    is_fallback=True,
                    note="Couldn't fetch the 13-week T-bill rate, so the greeks use "
                    f"{self.fallback * 100:.2f}% from config/settings.yaml.",
                )
            self._rate, self._fetched_at = rate, now
            return RateView(rate)


# --- one contract -------------------------------------------------------------------


@dataclass(frozen=True)
class ContractView:
    """What a page shows for one option contract (SPEC F2.2)."""

    contract_symbol: str
    underlying: str
    option_type: OptionType
    strike: float
    expiration: date
    dte: int
    expired: bool
    bid: float | None
    ask: float | None
    mid: float | None
    last_price: float | None
    volume: int | None
    open_interest: int | None
    implied_volatility: float | None  # None when missing or Yahoo's placeholder
    greeks: Greeks | None  # None when expired or there's no usable IV / stock price
    underlying_price: float | None
    in_the_money: bool
    fetched_at: datetime | None  # UTC


def contract_view(
    row: OptionQuote,
    rate: float,
    now: datetime,
    min_iv: float,
    fallback_spot: float | None = None,
) -> ContractView:
    """Add mid, DTE, greeks and in/out of the money to a stored contract.

    The stock price sent with the chain is used for the greeks; `fallback_spot` (e.g. the
    stored stock quote) only if the chain came without one.
    """
    spot = row.underlying_price or fallback_spot
    iv = (
        row.implied_volatility
        if row.implied_volatility and row.implied_volatility >= min_iv
        else None
    )
    expired = is_expired(row.expiration, now)
    option_type: OptionType = "put" if row.option_type == "put" else "call"
    greeks = None
    if not expired and spot is not None and iv is not None:
        years = years_to_expiry(row.expiration, now)
        greeks = black_scholes(option_type, spot, row.strike, years, rate, iv)
    return ContractView(
        contract_symbol=row.contract_symbol,
        underlying=row.underlying,
        option_type=option_type,
        strike=row.strike,
        expiration=row.expiration,
        dte=days_to_expiry(row.expiration, as_utc(now).astimezone(EASTERN).date()),
        expired=expired,
        bid=row.bid,
        ask=row.ask,
        mid=mid_price(row.bid, row.ask),
        last_price=row.last_price,
        volume=row.volume,
        open_interest=row.open_interest,
        implied_volatility=iv,
        greeks=greeks,
        underlying_price=spot,
        in_the_money=is_in_the_money(option_type, row.strike, spot),
        fetched_at=as_utc(row.fetched_at) if row.fetched_at else None,
    )


# --- stored chains ----------------------------------------------------------------------


@dataclass(frozen=True)
class StoredChain:
    status: ChainStatus
    rows: list[OptionQuote] = field(default_factory=list)  # calls then puts, by strike
    fetched_at: datetime | None = None
    note: str | None = None


def load_chain(
    session: Session,
    market_data: MarketData,
    symbol: str,
    expiration: date,
    max_age: timedelta | None,
) -> StoredChain:
    """One expiration's chain. Stored prices younger than `max_age` are reused
    (None = always fetch). If the source fails, the last stored chain is returned 'stale'."""
    latest = _latest_fetch(session, symbol, expiration)
    if latest is not None and max_age is not None and utc_now() - latest < max_age:
        return StoredChain("ok", _stored_rows(session, symbol, expiration, latest), latest)

    try:
        chain = market_data.get_option_chain(symbol, expiration)
    except Exception as exc:  # never let a failing source break the page
        if not isinstance(exc, MarketDataError):
            log.exception("Unexpected error fetching the %s %s option chain", symbol, expiration)
        reason = (str(exc) if isinstance(exc, MarketDataError) else "") or (
            "The data source failed."
        )
        if latest is None:
            return StoredChain("error", note=f"{reason} No stored prices for these options yet.")
        rows = _stored_rows(session, symbol, expiration, latest)
        return StoredChain("stale", rows, latest, note=f"{reason} Showing stored prices.")

    fetched_at = save_chain(session, chain)
    session.commit()
    return StoredChain("ok", _stored_rows(session, symbol, expiration, fetched_at), fetched_at)


def save_chain(session: Session, chain: OptionChainData) -> datetime:
    """Store every contract of the chain (one row per contract, overwritten).
    Returns the fetch time. Committed by the caller."""
    fetched_at = as_utc(chain.fetched_at)
    symbols = [c.contract_symbol for c in chain.contracts]
    existing = {
        row.contract_symbol: row
        for row in session.exec(
            select(OptionQuote).where(col(OptionQuote.contract_symbol).in_(symbols))
        )
    }
    for contract in chain.contracts:
        row = existing.get(contract.contract_symbol) or OptionQuote(
            contract_symbol=contract.contract_symbol,
            underlying=chain.underlying,
            expiration=chain.expiration,
            option_type=contract.option_type,
            strike=contract.strike,
            fetched_at=fetched_at,
            source=chain.source,
        )
        row.underlying = chain.underlying
        row.expiration = chain.expiration
        row.option_type = contract.option_type
        row.strike = contract.strike
        row.bid = contract.bid
        row.ask = contract.ask
        row.last_price = contract.last_price
        row.volume = contract.volume
        row.open_interest = contract.open_interest
        row.implied_volatility = contract.implied_volatility
        row.underlying_price = chain.underlying_price
        row.fetched_at = fetched_at
        row.source = chain.source
        session.add(row)
    session.flush()
    return fetched_at


def _latest_fetch(session: Session, symbol: str, expiration: date) -> datetime | None:
    latest = session.exec(
        select(func.max(OptionQuote.fetched_at)).where(
            OptionQuote.underlying == symbol, OptionQuote.expiration == expiration
        )
    ).one()
    return as_utc(latest) if latest is not None else None


def _stored_rows(
    session: Session, symbol: str, expiration: date, fetched_at: datetime
) -> list[OptionQuote]:
    """The contracts from that fetch (strikes Yahoo has since dropped are left out)."""
    statement = (
        select(OptionQuote)
        .where(
            OptionQuote.underlying == symbol,
            OptionQuote.expiration == expiration,
            OptionQuote.fetched_at == fetched_at,
        )
        .order_by(col(OptionQuote.option_type), col(OptionQuote.strike))
    )
    return list(session.exec(statement))


def stored_contract(session: Session, contract_symbol: str) -> OptionQuote | None:
    """The stored prices of a contract seen on an option chain, or None (unknown or
    malformed symbol). Contracts can only be saved or traded once we've seen them."""
    symbol = (contract_symbol or "").strip().upper()
    if not CONTRACT_SYMBOL_RE.fullmatch(symbol):
        return None
    return session.exec(select(OptionQuote).where(OptionQuote.contract_symbol == symbol)).first()


def contract_instrument(session: Session, stored: OptionQuote) -> Instrument:
    """The `instruments` row for a contract, created if needed. Committed by the caller."""
    instrument = session.exec(
        select(Instrument).where(Instrument.symbol == stored.contract_symbol)
    ).first()
    if instrument is None:
        instrument = Instrument(
            kind="option",
            symbol=stored.contract_symbol,
            underlying=stored.underlying,
            option_type=stored.option_type,
            strike=stored.strike,
            expiration=stored.expiration,
        )
        session.add(instrument)
        session.flush()
    return instrument


def get_expirations(
    session: Session, market_data: MarketData, symbol: str, now: datetime
) -> tuple[list[date], str | None]:
    """(Expiration dates that haven't expired yet, problem note). If the source fails, the
    dates we have stored prices for are used instead."""
    try:
        dates = market_data.get_option_expirations(symbol)
        note = None
    except Exception as exc:
        if not isinstance(exc, MarketDataError):
            log.exception("Unexpected error fetching %s option dates", symbol)
        dates = list(
            session.exec(
                select(OptionQuote.expiration)
                .where(OptionQuote.underlying == symbol)
                .distinct()
                .order_by(col(OptionQuote.expiration))
            )
        )
        note = "Couldn't get the list of expiration dates from the data source just now." + (
            " Showing the dates with stored prices." if dates else ""
        )
    return [d for d in sorted(dates) if not is_expired(d, now)], note


# --- the option chain page ------------------------------------------------------------


@dataclass(frozen=True)
class ChainView:
    """Everything the option chain page shows for one ticker, expiration and side."""

    symbol: str
    status: Literal["ok", "stale", "error", "no_options"]
    option_type: OptionType
    expirations: Sequence[date] = ()
    expiration: date | None = None
    dte: int | None = None
    rows: Sequence[ContractView] = ()
    underlying_price: float | None = None
    nearest_strike: float | None = None
    fetched_at: datetime | None = None  # UTC
    rate: RateView | None = None
    notes: Sequence[str] = ()


def option_chain(
    session: Session,
    market_data: MarketData,
    rates: RiskFreeRate,
    symbol: str,
    *,
    expiration: date | None,
    option_type: OptionType,
    max_age: timedelta | None,
    min_iv: float,
    stock_price: float | None = None,
) -> ChainView:
    """The chain for one expiration (the earliest if none or an unknown one is asked for).

    `min_iv` is a fraction (0.01 = 1%); `stock_price` is used for the greeks only if the
    chain came without a stock price."""
    now = utc_now()
    expirations, dates_note = get_expirations(session, market_data, symbol, now)
    notes = [dates_note] if dates_note else []
    if not expirations:
        if dates_note:
            return ChainView(symbol, "error", option_type, notes=notes)
        return ChainView(
            symbol, "no_options", option_type, notes=[f"No listed options found for {symbol}."]
        )

    chosen = expiration if expiration in expirations else expirations[0]
    dte = days_to_expiry(chosen, now.astimezone(EASTERN).date())
    stored = load_chain(session, market_data, symbol, chosen, max_age)
    if stored.note:
        notes.append(stored.note)
    if stored.status == "error":
        return ChainView(symbol, "error", option_type, expirations, chosen, dte=dte, notes=notes)

    rate = rates.get(market_data, max_age)
    if rate.note:
        notes.append(rate.note)
    spot = next((r.underlying_price for r in stored.rows if r.underlying_price), stock_price)
    rows = [
        contract_view(r, rate.rate, now, min_iv, spot)
        for r in stored.rows
        if r.option_type == option_type
    ]
    return ChainView(
        symbol=symbol,
        status=stored.status,
        option_type=option_type,
        expirations=expirations,
        expiration=chosen,
        dte=dte,
        rows=rows,
        underlying_price=spot,
        nearest_strike=nearest_strike((r.strike for r in rows), spot),
        fetched_at=stored.fetched_at,
        rate=rate,
        notes=notes,
    )


# --- option contracts in a watchlist (F2.6, F2.7) -------------------------------------


@dataclass(frozen=True)
class ContractRow:
    """One option contract in a watchlist."""

    item_id: int
    contract_symbol: str
    underlying: str
    option_type: OptionType
    strike: float
    expiration: date
    status: Literal["ok", "stale", "error", "expired"]
    view: ContractView | None = None  # None if no prices were ever stored
    note: str | None = None


def contract_rows(
    session: Session,
    market_data: MarketData,
    rates: RiskFreeRate,
    items: Sequence[tuple[int, Instrument]],
    *,
    max_age: timedelta | None,
    min_iv: float,
) -> tuple[list[ContractRow], RateView | None]:
    """Rows for a watchlist's contracts, plus the rate used for their greeks.

    Contracts are refreshed through their chain (one fetch per stock and expiration date).
    Expired contracts are not refreshed: they keep their last stored prices (F2.7).
    """
    now = utc_now()
    chains: dict[tuple[str, date], StoredChain] = {}
    for _, instrument in items:
        key = (instrument.underlying or "", instrument.expiration)
        if key not in chains and not is_expired(instrument.expiration, now):
            chains[key] = load_chain(session, market_data, key[0], key[1], max_age)

    rate = rates.get(market_data, max_age) if chains else None
    rows = []
    for item_id, instrument in items:
        stored = session.exec(
            select(OptionQuote).where(OptionQuote.contract_symbol == instrument.symbol)
        ).first()
        row = _contract_row(item_id, instrument)
        view = contract_view(stored, rate.rate if rate else 0.0, now, min_iv) if stored else None
        if is_expired(instrument.expiration, now):
            rows.append(row(status="expired", view=view))
            continue
        chain = chains[(instrument.underlying or "", instrument.expiration)]
        if view is None:
            note = chain.note or "No stored prices for this contract yet."
            rows.append(row(status="error", note=note))
        elif stored.fetched_at is not None and as_utc(stored.fetched_at) != chain.fetched_at:
            note = chain.note or "The data source no longer lists this contract."
            rows.append(row(status="stale", view=view, note=note))
        else:
            rows.append(row(status=chain.status, view=view, note=chain.note))
    return rows, rate


def _contract_row(item_id: int, instrument: Instrument):
    def row(**kwargs) -> ContractRow:
        return ContractRow(
            item_id=item_id,
            contract_symbol=instrument.symbol,
            underlying=instrument.underlying or "",
            option_type="put" if instrument.option_type == "put" else "call",
            strike=instrument.strike or 0.0,
            expiration=instrument.expiration,
            **kwargs,
        )

    return row
