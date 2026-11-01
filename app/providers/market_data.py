"""The market data interface. Pages and services talk to this, never to Yahoo directly.

To switch data vendors later, write a new class with the same methods (see yahoo.py).
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol


@dataclass(frozen=True)
class QuoteData:
    symbol: str
    last_price: float
    previous_close: float | None  # None if the source didn't give one
    currency: str | None
    fetched_at: datetime  # UTC
    source: str  # e.g. "yahoo"


class MarketDataError(Exception):
    """The data source failed (down, timed out, rate-limited, bad response)."""


class TickerNotFound(MarketDataError):
    """The data source says this ticker doesn't exist."""


class MarketData(Protocol):
    def get_quote(self, symbol: str) -> QuoteData:
        """Return the latest quote, or raise TickerNotFound / MarketDataError."""
        ...
