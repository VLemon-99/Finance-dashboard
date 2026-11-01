"""Yahoo Finance adapter (via the unofficial `yfinance` library).

This is the only file that imports `yfinance`.

Where each field comes from (see DECISIONS.md, "Phase 2: where the stock row comes from"):
- One year of daily price bars -> price, previous close, day range, volume, 52-week range,
  and the date of the last trading session.
- Share count ("fast info") x price -> market cap.
- Yahoo's company calendar -> next earnings date.
Market cap and earnings are extras: if either fails, the rest of the row is still returned.
"""

import logging
import math
from dataclasses import replace
from datetime import date

import pandas as pd
import yfinance as yf
from yfinance.exceptions import YFPricesMissingError, YFTickerMissingError, YFTzMissingError

from app.providers.market_data import (
    MarketDataError,
    QuoteData,
    SearchResult,
    TickerNotFound,
)
from app.tickers import InvalidTicker, normalize_ticker
from app.timeutils import today_eastern, utc_now

log = logging.getLogger(__name__)

# Make yfinance raise errors instead of quietly returning empty data, so we can tell
# "ticker doesn't exist" apart from "Yahoo is down".
yf.config.debug.hide_exceptions = False

_NOT_FOUND_ERRORS = (YFTickerMissingError, YFTzMissingError, YFPricesMissingError)

# Yahoo's codes for US stock exchanges -> the name we show. OTC/pink sheets are left
# out on purpose: they aren't exchange-listed (SPEC F1.1 says US-listed).
US_EXCHANGES = {
    "NMS": "NASDAQ",
    "NGM": "NASDAQ",
    "NCM": "NASDAQ",
    "NAS": "NASDAQ",
    "NYQ": "NYSE",
    "ASE": "NYSE American",
    "PCX": "NYSE Arca",
    "BTS": "Cboe BZX",
}
_SEARCH_KINDS = {"EQUITY": "stock", "ETF": "etf"}


class YahooMarketData:
    source = "yahoo"

    def __init__(self, timeout_seconds: float = 10.0) -> None:
        self.timeout_seconds = timeout_seconds

    def get_quote(self, symbol: str) -> QuoteData:
        try:
            ticker = yf.Ticker(symbol)
            history = ticker.history(period="1y", interval="1d", timeout=self.timeout_seconds)
        except _NOT_FOUND_ERRORS as exc:
            raise TickerNotFound(symbol) from exc
        except Exception as exc:  # network errors, rate limits, Yahoo changes...
            log.warning("Yahoo quote failed for %s: %s", symbol, exc)
            raise MarketDataError(
                f"Yahoo Finance did not respond properly ({type(exc).__name__})."
            ) from exc

        quote = quote_from_history(symbol, history, _currency(ticker))
        shares = _shares(ticker, symbol)
        return replace(
            quote,
            market_cap=shares * quote.last_price if shares else None,
            next_earnings=next_earnings_date(_calendar(ticker, symbol), today_eastern()),
        )

    def search(self, query: str, max_results: int) -> list[SearchResult]:
        try:
            found = yf.Search(
                query,
                # Ask for extra: some results are foreign listings we filter out.
                max_results=max_results * 3,
                news_count=0,
                lists_count=0,
                include_cb=False,
                recommended=0,
                timeout=self.timeout_seconds,
                raise_errors=True,
            ).quotes
        except Exception as exc:
            log.warning("Yahoo search failed for %r: %s", query, exc)
            raise MarketDataError(
                f"Yahoo Finance search did not respond properly ({type(exc).__name__})."
            ) from exc
        return search_results_from_quotes(found, max_results)


def quote_from_history(symbol: str, history: pd.DataFrame, currency: str | None) -> QuoteData:
    """Turn yfinance's daily price table into a QuoteData (price fields only)."""
    if history is None or history.empty or "Close" not in history:
        raise TickerNotFound(symbol)
    bars = history[history["Close"].notna()]
    if bars.empty:
        raise TickerNotFound(symbol)
    last = bars.iloc[-1]
    return QuoteData(
        symbol=symbol,
        last_price=float(last["Close"]),
        previous_close=float(bars["Close"].iloc[-2]) if len(bars) >= 2 else None,
        currency=currency,
        fetched_at=utc_now(),
        source=YahooMarketData.source,
        session_date=bars.index[-1].date(),
        day_low=_number(last.get("Low")),
        day_high=_number(last.get("High")),
        year_low=_number(history["Low"].min()) if "Low" in history else None,
        year_high=_number(history["High"].max()) if "High" in history else None,
        volume=_whole_number(last.get("Volume")),
    )


def next_earnings_date(calendar: dict | None, today: date) -> date | None:
    """Earliest earnings date that is today or later. Yahoo sometimes gives a range."""
    dates = (calendar or {}).get("Earnings Date") or []
    upcoming = [d for d in dates if isinstance(d, date) and d >= today]
    return min(upcoming) if upcoming else None


def search_results_from_quotes(quotes: list[dict], max_results: int) -> list[SearchResult]:
    """Keep only US-listed stocks and ETFs, in Yahoo's order, without duplicates."""
    results: list[SearchResult] = []
    seen: set[str] = set()
    for q in quotes:
        kind = _SEARCH_KINDS.get(q.get("quoteType", ""))
        exchange = US_EXCHANGES.get(q.get("exchange", ""))
        if kind is None or exchange is None:
            continue
        try:
            symbol = normalize_ticker(q.get("symbol", ""))
        except InvalidTicker:
            continue
        if symbol in seen:
            continue
        seen.add(symbol)
        name = q.get("longname") or q.get("shortname") or symbol
        results.append(SearchResult(symbol=symbol, name=name, exchange=exchange, kind=kind))
        if len(results) >= max_results:
            break
    return results


# --- extras: never let one of these spoil an otherwise good quote ---


def _currency(ticker: yf.Ticker) -> str | None:
    try:
        return (ticker.history_metadata or {}).get("currency")
    except Exception:
        return None


def _shares(ticker: yf.Ticker, symbol: str) -> int | None:
    try:
        shares = ticker.fast_info.shares
        return int(shares) if shares and shares > 0 else None
    except Exception as exc:
        log.info("Yahoo share count unavailable for %s: %s", symbol, exc)
        return None


def _calendar(ticker: yf.Ticker, symbol: str) -> dict | None:
    try:
        return ticker.calendar
    except Exception as exc:
        log.info("Yahoo calendar unavailable for %s: %s", symbol, exc)
        return None


def _number(value: object) -> float | None:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(number) else number


def _whole_number(value: object) -> int | None:
    number = _number(value)
    return int(number) if number is not None else None
