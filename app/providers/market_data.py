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


class MarketDataError(Exception):
    """The data source failed (down, timed out, rate-limited, bad response)."""


class TickerNotFound(MarketDataError):
    """The data source says this ticker doesn't exist."""


class MarketData(Protocol):
    def get_quote(self, symbol: str) -> QuoteData:
        """Return the latest quote, or raise TickerNotFound / MarketDataError."""
        ...

    def search(self, query: str, max_results: int) -> list[SearchResult]:
        """US-listed stocks/ETFs matching a ticker or company name. Raises MarketDataError."""
        ...
