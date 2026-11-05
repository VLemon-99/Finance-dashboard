"""Yahoo Finance adapter (via the unofficial `yfinance` library).

This is the only file that imports `yfinance`.

Where each field comes from (see DECISIONS.md, "Phase 2: where the stock row comes from"):
- One year of daily price bars -> price, previous close, day range, volume, 52-week range,
  and the date of the last trading session.
- Share count ("fast info") x price -> market cap.
- Yahoo's company calendar -> next earnings date.
Market cap and earnings are extras: if either fails, the rest of the row is still returned.

Earnings calendar (Phase 6): Yahoo's earnings calendar (the same data as its calendar page,
asked for as JSON through yfinance) gives each report's date, time and "before open" /
"after close". All tickers go in one request.

Options (Phase 4): Yahoo's option chain gives bid, ask, last, volume, open interest and IV
per strike, plus the stock price at that moment. Yahoo has no greeks; we calculate them
(app/services/options_math.py). The risk-free rate is the 13-week T-bill yield (^IRX).

Valuation and holders (Phase 7, SPEC F4.7, F4.8):
- The company's "info" -> multiples (trailing/forward P/E, EV/EBITDA, P/S), sector and
  industry, share count and the analysts' low/average/high price targets.
- The annual income statement and balance sheet (about 4 fiscal years) plus the closing
  price at each year's end -> the stock's own valuation history (worked out in
  app/services/valuation.py).
- Yahoo's sector and industry pages -> their largest companies (automatic peers).
- The holders page -> % held by institutions and the top 10 institutions with the change
  in their stakes (from their 13F reports).
"""

import logging
import math
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from typing import Literal

import pandas as pd
import yfinance as yf
from yfinance.calendars import CalendarQuery
from yfinance.exceptions import YFPricesMissingError, YFTickerMissingError, YFTzMissingError

from app.providers.market_data import (
    AnnualFigures,
    CompanyProfile,
    EarningsData,
    GroupMembers,
    HolderData,
    MarketDataError,
    Multiples,
    NoOptionChain,
    OptionChainData,
    OptionContractData,
    OwnershipData,
    QuoteData,
    SearchResult,
    TickerNotFound,
)
from app.tickers import InvalidTicker, normalize_ticker
from app.timeutils import EASTERN, today_eastern, utc_now

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

# Yahoo's 13-week US Treasury bill yield, quoted in percent (4.25 = 4.25%).
RISK_FREE_SYMBOL = "^IRX"


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

        metadata = _metadata(ticker)
        quote = quote_from_history(symbol, history, metadata.get("currency"))
        quote = with_latest_regular_price(quote, metadata)
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

    def get_option_expirations(self, symbol: str) -> list[date]:
        try:
            raw = yf.Ticker(symbol).options
        except Exception as exc:
            log.warning("Yahoo option dates failed for %s: %s", symbol, exc)
            raise MarketDataError(
                f"Yahoo Finance did not respond properly ({type(exc).__name__})."
            ) from exc
        return expirations_from_yahoo(raw)

    def get_option_chain(self, symbol: str, expiration: date) -> OptionChainData:
        try:
            chain = yf.Ticker(symbol).option_chain(expiration.isoformat())
        except ValueError as exc:  # yfinance: "Expiration `...` cannot be found"
            raise NoOptionChain(
                f"Yahoo Finance has no {symbol} options expiring {expiration:%b} "
                f"{expiration.day}, {expiration.year}."
            ) from exc
        except Exception as exc:
            log.warning("Yahoo option chain failed for %s %s: %s", symbol, expiration, exc)
            raise MarketDataError(
                f"Yahoo Finance did not respond properly ({type(exc).__name__})."
            ) from exc
        return chain_from_yahoo(symbol, expiration, chain.calls, chain.puts, chain.underlying)

    def get_daily_close(self, symbol: str, day: date) -> float | None:
        try:
            history = yf.Ticker(symbol).history(
                start=day.isoformat(),
                end=(day + timedelta(days=1)).isoformat(),
                interval="1d",
                timeout=self.timeout_seconds,
            )
        except _NOT_FOUND_ERRORS:
            return None  # no prices for that day (or Yahoo has no history for it)
        except Exception as exc:
            log.warning("Yahoo closing price failed for %s on %s: %s", symbol, day, exc)
            raise MarketDataError(
                f"Yahoo Finance did not respond properly ({type(exc).__name__})."
            ) from exc
        return close_on(history, day)

    def get_risk_free_rate(self) -> float:
        try:
            history = yf.Ticker(RISK_FREE_SYMBOL).history(
                period="5d", interval="1d", timeout=self.timeout_seconds
            )
        except Exception as exc:
            log.warning("Yahoo T-bill rate failed: %s", exc)
            raise MarketDataError(
                f"Yahoo Finance did not respond properly ({type(exc).__name__})."
            ) from exc
        return rate_from_history(history)

    def get_earnings(self, symbols: list[str], start: date, end: date) -> list[EarningsData]:
        results: list[EarningsData] = []
        for first in range(0, len(symbols), EARNINGS_BATCH):
            batch = symbols[first : first + EARNINGS_BATCH]
            try:
                calendars = yf.Calendars(start=start.isoformat(), end=end.isoformat())
                # yfinance's public earnings call can't filter by ticker, so we pass our own
                # query to the same request it makes (pinned yfinance version, see uv.lock).
                table = calendars._get_data(
                    calendar_type="sp_earnings",
                    query=earnings_query(batch, start, end),
                    limit=100,
                    force=True,
                )
            except Exception as exc:
                log.warning("Yahoo earnings calendar failed: %s", exc)
                raise MarketDataError(
                    f"Yahoo Finance did not respond properly ({type(exc).__name__})."
                ) from exc
            results += earnings_from_calendar(table, set(batch))
        return results

    def get_profile(self, symbol: str) -> CompanyProfile:
        try:
            info = yf.Ticker(symbol).info
        except _NOT_FOUND_ERRORS as exc:
            raise TickerNotFound(symbol) from exc
        except Exception as exc:
            log.warning("Yahoo company info failed for %s: %s", symbol, exc)
            raise MarketDataError(
                f"Yahoo Finance did not respond properly ({type(exc).__name__})."
            ) from exc
        return profile_from_info(symbol, info)

    def get_annual_figures(self, symbol: str) -> list[AnnualFigures]:
        try:
            ticker = yf.Ticker(symbol)
            income = ticker.income_stmt
            balance = ticker.balance_sheet
            ends = year_ends(income)
            history = None
            if ends:
                history = ticker.history(
                    start=(min(ends) - timedelta(days=10)).isoformat(),
                    end=(max(ends) + timedelta(days=1)).isoformat(),
                    interval="1d",
                    auto_adjust=False,  # split-adjusted, not dividend-adjusted
                    timeout=self.timeout_seconds,
                )
        except Exception as exc:
            log.warning("Yahoo annual figures failed for %s: %s", symbol, exc)
            raise MarketDataError(
                f"Yahoo Finance did not respond properly ({type(exc).__name__})."
            ) from exc
        return annual_figures_from_yahoo(income, balance, history)

    def get_group_members(self, kind: Literal["sector", "industry"], key: str) -> GroupMembers:
        try:
            group = yf.Sector(key) if kind == "sector" else yf.Industry(key)
            name, top = group.name, group.top_companies
        except Exception as exc:
            log.warning("Yahoo %s page failed for %s: %s", kind, key, exc)
            raise MarketDataError(
                f"Yahoo Finance did not respond properly ({type(exc).__name__})."
            ) from exc
        return group_from_yahoo(kind, key, name, top)

    def get_ownership(self, symbol: str) -> OwnershipData:
        try:
            ticker = yf.Ticker(symbol)
            institutions = ticker.institutional_holders
            breakdown = ticker.major_holders
        except Exception as exc:
            log.warning("Yahoo holders failed for %s: %s", symbol, exc)
            raise MarketDataError(
                f"Yahoo Finance did not respond properly ({type(exc).__name__})."
            ) from exc
        return ownership_from_yahoo(symbol, institutions, breakdown)


# Tickers per earnings-calendar request (Yahoo returns at most 100 rows per request).
EARNINGS_BATCH = 25
# Yahoo's "startdatetimetype": before market open, after market close, time as specified.
_TIMINGS = {"BMO": "before_open", "AMC": "after_close", "TAS": "at_time"}


def earnings_query(symbols: list[str], start: date, end: date) -> CalendarQuery:
    tickers = [CalendarQuery("eq", ["ticker", s]) for s in symbols]
    return CalendarQuery(
        "and",
        [
            CalendarQuery("eq", ["region", "us"]),
            CalendarQuery(
                "or",
                [
                    CalendarQuery("eq", ["eventtype", "EAD"]),
                    CalendarQuery("eq", ["eventtype", "ERA"]),
                ],
            ),
            CalendarQuery("gte", ["startdatetime", start.isoformat()]),
            CalendarQuery("lte", ["startdatetime", end.isoformat()]),
            tickers[0] if len(tickers) == 1 else CalendarQuery("or", tickers),
        ],
    )


def earnings_from_calendar(table: pd.DataFrame | None, wanted: set[str]) -> list[EarningsData]:
    """yfinance's earnings-calendar table (one row per report, indexed by symbol) -> ours."""
    if table is None or table.empty or "Event Start Date" not in table:
        return []
    results = []
    for symbol, row in table.iterrows():
        symbol = str(symbol).upper()
        when = row.get("Event Start Date")
        if symbol not in wanted or when is None or pd.isna(when):
            continue
        when = pd.Timestamp(when)
        when = when.tz_localize("UTC") if when.tzinfo is None else when.tz_convert("UTC")
        starts_at = when.to_pydatetime()
        timing = _TIMINGS.get(str(row.get("Timing") or "").strip().upper(), "unknown")
        results.append(
            EarningsData(
                symbol=symbol,
                day=starts_at.astimezone(EASTERN).date(),
                starts_at=starts_at if timing != "unknown" else None,
                timing=timing,  # type: ignore[arg-type]
            )
        )
    return results


def profile_from_info(symbol: str, info: dict | None) -> CompanyProfile:
    """Yahoo's company "info" -> CompanyProfile. Zero or negative multiples (losses) -> None."""
    info = info or {}
    if not info.get("quoteType") and not info.get("shortName") and not info.get("longName"):
        raise TickerNotFound(symbol)
    price = _positive(info.get("currentPrice")) or _positive(info.get("regularMarketPrice"))
    analysts = _whole_number(info.get("numberOfAnalystOpinions"))
    return CompanyProfile(
        symbol=symbol,
        name=info.get("longName") or info.get("shortName"),
        sector=info.get("sector") or None,
        sector_key=info.get("sectorKey") or None,
        industry=info.get("industry") or None,
        industry_key=info.get("industryKey") or None,
        price=price,
        market_cap=_positive(info.get("marketCap")),
        shares_outstanding=_positive(info.get("sharesOutstanding")),
        multiples=Multiples(
            trailing_pe=_positive(info.get("trailingPE")),
            forward_pe=_positive(info.get("forwardPE")),
            ev_ebitda=_positive(info.get("enterpriseToEbitda")),
            price_to_sales=_positive(info.get("priceToSalesTrailing12Months")),
        ),
        target_low=_positive(info.get("targetLowPrice")),
        target_mean=_positive(info.get("targetMeanPrice")),
        target_high=_positive(info.get("targetHighPrice")),
        analyst_count=analysts if analysts and analysts > 0 else None,
    )


# Row names in yfinance's annual statements, most preferred first.
_EPS_ROWS = ("Diluted EPS",)
_REVENUE_ROWS = ("Total Revenue", "Operating Revenue")
_EBITDA_ROWS = ("EBITDA", "Normalized EBITDA")
_DEBT_ROWS = ("Total Debt",)
_CASH_ROWS = (
    "Cash Cash Equivalents And Short Term Investments",
    "Cash And Cash Equivalents",
)
_SHARE_ROWS = ("Ordinary Shares Number", "Share Issued")


def year_ends(statement: pd.DataFrame | None) -> list[date]:
    """The fiscal year-end dates (the statement's columns)."""
    if statement is None or statement.empty:
        return []
    return sorted({pd.Timestamp(c).date() for c in statement.columns})


def annual_figures_from_yahoo(
    income: pd.DataFrame | None,
    balance: pd.DataFrame | None,
    history: pd.DataFrame | None,
) -> list[AnnualFigures]:
    """yfinance's annual income statement + balance sheet (rows = items, columns = year ends)
    and a daily price history -> one AnnualFigures per fiscal year, oldest first."""
    figures = []
    for end in year_ends(income):
        figures.append(
            AnnualFigures(
                period_end=end,
                price=close_on_or_before(history, end),
                diluted_eps=_cell(income, _EPS_ROWS, end),
                revenue=_cell(income, _REVENUE_ROWS, end),
                ebitda=_cell(income, _EBITDA_ROWS, end),
                total_debt=_cell(balance, _DEBT_ROWS, end),
                cash=_cell(balance, _CASH_ROWS, end),
                shares=_cell(balance, _SHARE_ROWS, end),
            )
        )
    return figures


def close_on_or_before(history: pd.DataFrame | None, day: date) -> float | None:
    """Closing price of the last trading day up to `day` (a year can end on a weekend)."""
    if history is None or history.empty or "Close" not in history:
        return None
    best = None
    for stamp, close in history["Close"].items():
        if stamp.date() <= day:
            price = _positive(close)
            if price:
                best = price
    return best


def _cell(table: pd.DataFrame | None, rows: tuple[str, ...], end: date) -> float | None:
    if table is None or table.empty:
        return None
    columns = [c for c in table.columns if pd.Timestamp(c).date() == end]
    if not columns:
        return None
    for row in rows:
        if row in table.index:
            value = _number(table.at[row, columns[0]])
            if value is not None and math.isfinite(value):
                return value
    return None


def group_from_yahoo(
    kind: Literal["sector", "industry"], key: str, name: str | None, top: pd.DataFrame | None
) -> GroupMembers:
    """yfinance's sector/industry "top companies" table (indexed by symbol, biggest first)."""
    if top is None or top.empty:
        raise MarketDataError(f"Yahoo Finance listed no companies for the {kind} '{key}'.")
    symbols = []
    for raw in top.index:
        try:
            symbol = normalize_ticker(str(raw))
        except InvalidTicker:
            continue  # foreign listings like "7203.T"
        if symbol not in symbols:
            symbols.append(symbol)
    return GroupMembers(kind=kind, key=key, name=name or key, symbols=tuple(symbols))


def ownership_from_yahoo(
    symbol: str, institutions: pd.DataFrame | None, breakdown: pd.DataFrame | None
) -> OwnershipData:
    """yfinance's institutional holders table and holders breakdown -> OwnershipData."""
    holders = []
    if institutions is not None and not institutions.empty:
        for row in institutions.to_dict("records"):
            name = str(row.get("Holder") or "").strip()
            if not name:
                continue
            reported = row.get("Date Reported")
            holders.append(
                HolderData(
                    name=name,
                    shares=_number(row.get("Shares")),
                    pct_held=_number(row.get("pctHeld")),
                    pct_change=_number(row.get("pctChange")),
                    value=_number(row.get("Value")),
                    reported=pd.Timestamp(reported).date()
                    if reported is not None and not pd.isna(reported)
                    else None,
                )
            )
    values: dict[str, object] = {}
    if breakdown is not None and not breakdown.empty and "Value" in breakdown:
        values = breakdown["Value"].to_dict()
    count = _whole_number(values.get("institutionsCount"))
    return OwnershipData(
        symbol=symbol,
        institutions_pct=_number(values.get("institutionsPercentHeld")),
        institutions_count=count if count and count > 0 else None,
        top_institutions=tuple(holders),
    )


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


def with_latest_regular_price(quote: QuoteData, metadata: dict) -> QuoteData:
    """Use Yahoo's latest regular-market price when the daily history lacks that day.

    For a while after the 4 PM close, Yahoo's daily history can be missing today's row, so
    its last row is the previous trading day. Seen 2026-11-03: AMZN showed Monday's close
    (245.63) at 8 PM on Tuesday instead of Tuesday's (about 251). The history's metadata
    always carries the latest regular-session price and its time (after-hours trades are
    not included), so that day is added from it. Returns the quote unchanged otherwise.
    """
    price = _number(metadata.get("regularMarketPrice"))
    stamp = _number(metadata.get("regularMarketTime"))
    if not price or price <= 0 or not stamp:
        return quote
    try:
        day = datetime.fromtimestamp(stamp, tz=UTC).astimezone(EASTERN).date()
    except (OverflowError, OSError, ValueError):
        return quote
    if quote.session_date is not None and day <= quote.session_date:
        return quote  # the history already has that day
    low = _number(metadata.get("regularMarketDayLow"))
    high = _number(metadata.get("regularMarketDayHigh"))
    lows = [v for v in (quote.year_low, low, price) if v is not None]
    highs = [v for v in (quote.year_high, high, price) if v is not None]
    return replace(
        quote,
        last_price=price,
        previous_close=quote.last_price,
        session_date=day,
        day_low=low,
        day_high=high,
        volume=_whole_number(metadata.get("regularMarketVolume")),
        year_low=min(lows),
        year_high=max(highs),
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


def expirations_from_yahoo(raw: object) -> list[date]:
    """Yahoo gives 'YYYY-MM-DD' strings. Unreadable ones are skipped."""
    dates: set[date] = set()
    for text in raw or ():
        try:
            dates.add(date.fromisoformat(str(text)))
        except ValueError:
            log.info("Skipping unreadable option date from Yahoo: %r", text)
    return sorted(dates)


def chain_from_yahoo(
    symbol: str,
    expiration: date,
    calls: pd.DataFrame | None,
    puts: pd.DataFrame | None,
    underlying: dict | None,
) -> OptionChainData:
    """Turn yfinance's two option tables into one OptionChainData, sorted by strike."""
    if (calls is None or calls.empty) and (puts is None or puts.empty):
        raise NoOptionChain(f"Yahoo Finance returned no {symbol} options for {expiration}.")
    contracts = [
        *_contracts_from_table(calls, "call"),
        *_contracts_from_table(puts, "put"),
    ]
    contracts.sort(key=lambda c: (c.option_type, c.strike))
    price = _number((underlying or {}).get("regularMarketPrice"))
    return OptionChainData(
        underlying=symbol,
        expiration=expiration,
        underlying_price=price if price and price > 0 else None,
        contracts=tuple(contracts),
        fetched_at=utc_now(),
        source=YahooMarketData.source,
    )


def close_on(history: pd.DataFrame | None, day: date) -> float | None:
    """The closing price of the bar dated `day`, if there is one."""
    if history is None or history.empty or "Close" not in history:
        return None
    for stamp, close in history["Close"].items():
        if stamp.date() == day:
            price = _number(close)
            return price if price and price > 0 else None
    return None


def rate_from_history(history: pd.DataFrame | None) -> float:
    """Latest ^IRX close (percent) as a fraction. Rejects missing or implausible values."""
    if history is None or history.empty or "Close" not in history:
        raise MarketDataError("Yahoo Finance returned no T-bill rate.")
    closes = history["Close"].dropna()
    if closes.empty:
        raise MarketDataError("Yahoo Finance returned no T-bill rate.")
    percent = float(closes.iloc[-1])
    if not 0 <= percent < 25:
        raise MarketDataError(f"Yahoo Finance returned an implausible T-bill rate ({percent}%).")
    return percent / 100


def _contracts_from_table(table: pd.DataFrame | None, option_type: str) -> list[OptionContractData]:
    if table is None or table.empty:
        return []
    contracts = []
    for row in table.to_dict("records"):
        symbol = row.get("contractSymbol")
        strike = _number(row.get("strike"))
        if not isinstance(symbol, str) or not symbol or strike is None or strike <= 0:
            continue
        contracts.append(
            OptionContractData(
                contract_symbol=symbol,
                option_type=option_type,  # type: ignore[arg-type]
                strike=strike,
                bid=_number(row.get("bid")),
                ask=_number(row.get("ask")),
                last_price=_number(row.get("lastPrice")),
                volume=_whole_number(row.get("volume")),
                open_interest=_whole_number(row.get("openInterest")),
                implied_volatility=_number(row.get("impliedVolatility")),
            )
        )
    return contracts


# --- extras: never let one of these spoil an otherwise good quote ---


def _metadata(ticker: yf.Ticker) -> dict:
    """The price history's metadata (currency, latest regular-market price and time)."""
    try:
        return dict(ticker.history_metadata or {})
    except Exception:
        return {}


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


def _positive(value: object) -> float | None:
    """A finite number above zero, else None (Yahoo sends "Infinity" or 0 for "no value")."""
    number = _number(value)
    return number if number is not None and math.isfinite(number) and number > 0 else None


def _whole_number(value: object) -> int | None:
    number = _number(value)
    return int(number) if number is not None else None
