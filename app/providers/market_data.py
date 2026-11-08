"""The market data interface. Pages and services talk to this, never to Yahoo directly.

To switch data vendors later, write a new class with the same methods (see yahoo.py).
"""

from dataclasses import dataclass
from datetime import date, datetime
from typing import Literal, Protocol


@dataclass(frozen=True)
class QuoteData:
    """One stock's row of market data (SPEC F1.4). Optional fields are None if unavailable."""

    symbol: str
    last_price: float
    previous_close: float | None  # None if the source didn't give one
    currency: str | None
    fetched_at: datetime  # UTC
    source: str  # e.g. "yahoo"
    session_date: date | None = None  # the trading day `last_price` belongs to
    day_low: float | None = None
    day_high: float | None = None
    year_low: float | None = None  # 52-week range
    year_high: float | None = None
    volume: int | None = None  # shares traded in that session
    market_cap: float | None = None
    next_earnings: date | None = None


@dataclass(frozen=True)
class SearchResult:
    symbol: str  # already in our format, e.g. BRK-B
    name: str
    exchange: str  # human-readable, e.g. "NASDAQ"
    kind: Literal["stock", "etf"]


@dataclass(frozen=True)
class OptionContractData:
    """One strike of one option chain, as the source gave it (SPEC F2.2 raw fields)."""

    contract_symbol: str  # OCC-style, e.g. AAPL261120C00150000
    option_type: Literal["call", "put"]
    strike: float
    bid: float | None = None
    ask: float | None = None
    last_price: float | None = None
    volume: int | None = None  # contracts traded today
    open_interest: int | None = None
    implied_volatility: float | None = None  # a fraction: 0.25 = 25%


@dataclass(frozen=True)
class OptionChainData:
    """All calls and puts of one ticker for one expiration date."""

    underlying: str  # the stock's ticker, e.g. AAPL
    expiration: date
    underlying_price: float | None  # the stock price the source sent with the chain
    contracts: tuple[OptionContractData, ...]
    fetched_at: datetime  # UTC
    source: str


EarningsTiming = Literal["before_open", "after_close", "at_time", "unknown"]


@dataclass(frozen=True)
class EarningsData:
    """One scheduled earnings report (SPEC F5.1)."""

    symbol: str
    day: date  # US Eastern calendar day
    starts_at: datetime | None  # UTC, when the source gives a time
    timing: EarningsTiming  # before the open, after the close, at `starts_at`, or unknown


@dataclass(frozen=True)
class Multiples:
    """Valuation multiples (SPEC F4.7). None = the source has none, or it isn't meaningful."""

    trailing_pe: float | None = None  # price / last 12 months' earnings per share
    forward_pe: float | None = None  # price / analysts' expected earnings per share
    ev_ebitda: float | None = None  # enterprise value / EBITDA
    price_to_sales: float | None = None  # market cap / last 12 months' revenue


@dataclass(frozen=True)
class CompanyProfile:
    """A stock's valuation facts, classification and analyst targets (SPEC F4.7)."""

    symbol: str
    name: str | None
    sector: str | None  # e.g. "Consumer Cyclical"
    sector_key: str | None  # e.g. "consumer-cyclical" (used to ask for its largest companies)
    industry: str | None  # e.g. "Internet Retail"
    industry_key: str | None  # e.g. "internet-retail"
    price: float | None
    market_cap: float | None
    shares_outstanding: float | None
    multiples: Multiples
    target_low: float | None = None
    target_mean: float | None = None
    target_high: float | None = None
    analyst_count: int | None = None
    country: str | None = None  # where the company is based, e.g. "United States"


@dataclass(frozen=True)
class AnnualFigures:
    """One fiscal year's figures, and the share price at the year's end (SPEC F4.7 history)."""

    period_end: date
    price: float | None  # closing price on the last trading day up to period_end
    diluted_eps: float | None = None
    revenue: float | None = None
    ebitda: float | None = None
    total_debt: float | None = None
    cash: float | None = None  # cash, equivalents and short-term investments
    shares: float | None = None  # shares outstanding at the year's end


@dataclass(frozen=True)
class GroupMembers:
    """A sector's or industry's largest companies, biggest first."""

    kind: Literal["sector", "industry"]
    key: str  # e.g. "technology", "internet-retail"
    name: str  # e.g. "Technology"
    symbols: tuple[str, ...]


@dataclass(frozen=True)
class HolderData:
    """One institution's stake from its latest 13F report (SPEC F4.8)."""

    name: str
    shares: float | None
    pct_held: float | None  # fraction of the company's shares: 0.07 = 7%
    pct_change: float | None  # change in its share count since its previous report: 0.1 = +10%
    value: float | None  # dollars
    reported: date | None  # the quarter end the stake is as of


@dataclass(frozen=True)
class OwnershipData:
    """Who owns the stock (SPEC F4.8)."""

    symbol: str
    institutions_pct: float | None  # fraction of shares held by institutions
    institutions_count: int | None
    top_institutions: tuple[HolderData, ...]


class MarketDataError(Exception):
    """The data source failed (down, timed out, rate-limited, bad response)."""


class TickerNotFound(MarketDataError):
    """The data source says this ticker doesn't exist."""


class NoOptionChain(MarketDataError):
    """The data source has no options for this ticker, or not for that expiration date."""


class MarketData(Protocol):
    def get_quote(self, symbol: str) -> QuoteData:
        """Return the latest quote, or raise TickerNotFound / MarketDataError."""
        ...

    def search(self, query: str, max_results: int) -> list[SearchResult]:
        """US-listed stocks/ETFs matching a ticker or company name. Raises MarketDataError."""
        ...

    def get_option_expirations(self, symbol: str) -> list[date]:
        """Expiration dates with listed options, earliest first. Empty if the ticker has no
        options. Raises MarketDataError."""
        ...

    def get_option_chain(self, symbol: str, expiration: date) -> OptionChainData:
        """Calls and puts for one expiration. Raises NoOptionChain / MarketDataError."""
        ...

    def get_risk_free_rate(self) -> float:
        """Annual risk-free rate as a fraction (0.04 = 4%), from 13-week US T-bills.
        Raises MarketDataError."""
        ...

    def get_daily_close(self, symbol: str, day: date) -> float | None:
        """Closing price on that day, for a stock or an option contract symbol. None if the
        source has no price for that day (weekend, holiday, no trades). Raises MarketDataError."""
        ...

    def get_earnings(self, symbols: list[str], start: date, end: date) -> list[EarningsData]:
        """Scheduled earnings reports of these stocks between two days (both included).
        Stocks the source has no date for are simply absent. Raises MarketDataError."""
        ...

    def get_profile(self, symbol: str) -> CompanyProfile:
        """Multiples, sector/industry and analyst targets. Raises TickerNotFound /
        MarketDataError."""
        ...

    def get_annual_figures(self, symbol: str) -> list[AnnualFigures]:
        """The fiscal years the source has (usually about 4), oldest first, each with the
        price at the year's end. Raises MarketDataError."""
        ...

    def get_group_members(self, kind: Literal["sector", "industry"], key: str) -> GroupMembers:
        """A sector's or industry's largest US companies, biggest first. Raises
        MarketDataError."""
        ...

    def get_ownership(self, symbol: str) -> OwnershipData:
        """Institutional ownership and the top institutional holders. Raises MarketDataError."""
        ...
