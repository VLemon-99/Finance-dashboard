"""Yahoo Finance adapter (via the unofficial `yfinance` library).

This is the only file that imports `yfinance`.
"""

import logging
import math

import pandas as pd
import yfinance as yf
from yfinance.exceptions import YFPricesMissingError, YFTickerMissingError, YFTzMissingError

from app.providers.market_data import MarketDataError, QuoteData, TickerNotFound
from app.timeutils import utc_now

log = logging.getLogger(__name__)

# Make yfinance raise errors instead of quietly returning empty data, so we can tell
# "ticker doesn't exist" apart from "Yahoo is down".
yf.config.debug.hide_exceptions = False

_NOT_FOUND_ERRORS = (YFTickerMissingError, YFTzMissingError, YFPricesMissingError)


class YahooMarketData:
    source = "yahoo"

    def __init__(self, timeout_seconds: float = 10.0) -> None:
        self.timeout_seconds = timeout_seconds

    def get_quote(self, symbol: str) -> QuoteData:
        try:
            ticker = yf.Ticker(symbol)
            # Daily bars for the last few trading days: the last row is today (or the
            # latest session), the one before it gives the previous close.
            history = ticker.history(period="5d", interval="1d", timeout=self.timeout_seconds)
        except _NOT_FOUND_ERRORS as exc:
            raise TickerNotFound(symbol) from exc
        except Exception as exc:  # network errors, rate limits, Yahoo changes...
            log.warning("Yahoo quote failed for %s: %s", symbol, exc)
            raise MarketDataError(
                f"Yahoo Finance did not respond properly ({type(exc).__name__})."
            ) from exc

        return quote_from_history(symbol, history, _currency(ticker))


def quote_from_history(symbol: str, history: pd.DataFrame, currency: str | None) -> QuoteData:
    """Turn yfinance's daily price table into a QuoteData."""
    if history is None or history.empty or "Close" not in history:
        raise TickerNotFound(symbol)
    closes = [float(c) for c in history["Close"].tolist() if not _is_missing(c)]
    if not closes:
        raise TickerNotFound(symbol)
    return QuoteData(
        symbol=symbol,
        last_price=closes[-1],
        previous_close=closes[-2] if len(closes) >= 2 else None,
        currency=currency,
        fetched_at=utc_now(),
        source=YahooMarketData.source,
    )


def _currency(ticker: yf.Ticker) -> str | None:
    # Nice to have only: never let a missing currency spoil an otherwise good quote.
    try:
        return (ticker.history_metadata or {}).get("currency")
    except Exception:
        return None


def _is_missing(value: object) -> bool:
    return value is None or (isinstance(value, float) and math.isnan(value))
