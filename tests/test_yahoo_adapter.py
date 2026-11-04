"""The Yahoo adapter turns yfinance output into QuoteData / SearchResult and sorts errors.

yfinance is replaced with a fake, so nothing goes over the network.
"""

import math
from datetime import UTC, date, datetime

import pytest
from yfinance.exceptions import YFPricesMissingError, YFTzMissingError

from app.providers import yahoo
from app.providers.market_data import MarketDataError, TickerNotFound
from app.providers.yahoo import (
    YahooMarketData,
    next_earnings_date,
    quote_from_history,
    search_results_from_quotes,
    with_latest_regular_price,
)
from tests.conftest import load_yahoo_history, load_yahoo_search


class FakeFastInfo:
    def __init__(self, shares, error):
        self._shares, self._error = shares, error

    @property
    def shares(self):
        if self._error:
            raise ConnectionError("share count fetch failed")
        return self._shares


class FakeTicker:
    def __init__(
        self,
        history=None,
        error=None,
        metadata_error=False,
        shares=15_000_000_000,
        shares_error=False,
        calendar=None,
        calendar_error=False,
        metadata=None,
    ):
        self._history, self._error, self._metadata_error = history, error, metadata_error
        self.fast_info = FakeFastInfo(shares, shares_error)
        self._calendar, self._calendar_error = calendar, calendar_error
        self._metadata = metadata or {}

    def history(self, **kwargs):
        if self._error:
            raise self._error
        return self._history

    @property
    def history_metadata(self):
        if self._metadata_error:
            raise ConnectionError("metadata fetch failed")
        return {"currency": "USD", **self._metadata}

    @property
    def calendar(self):
        if self._calendar_error:
            raise ConnectionError("calendar fetch failed")
        return self._calendar


def use_fake_ticker(monkeypatch, **kwargs):
    monkeypatch.setattr(yahoo.yf, "Ticker", lambda symbol: FakeTicker(**kwargs))


@pytest.fixture
def today(monkeypatch):
    """Pin 'today' so the earnings date logic has a known answer."""
    monkeypatch.setattr(yahoo, "today_eastern", lambda: date(2026, 11, 1))


def test_parses_recorded_sample_into_full_row(monkeypatch, today):
    history, _ = load_yahoo_history()
    calendar = {"Earnings Date": [date(2026, 10, 29), date(2027, 1, 28)]}
    use_fake_ticker(monkeypatch, history=history, calendar=calendar)
    quote = YahooMarketData().get_quote("AAPL")
    assert quote.symbol == "AAPL"
    assert quote.last_price == pytest.approx(259.58)
    assert quote.previous_close == pytest.approx(258.04)
    assert quote.session_date == date(2026, 10, 30)
    assert (quote.day_low, quote.day_high) == pytest.approx((257.60, 260.15))
    assert (quote.year_low, quote.year_high) == pytest.approx((186.20, 271.40))
    assert quote.volume == 36_540_100
    assert quote.market_cap == pytest.approx(15_000_000_000 * 259.58)  # shares x price
    assert quote.next_earnings == date(2027, 1, 28)  # the Oct 29 date has passed
    assert quote.currency == "USD"
    assert quote.source == "yahoo"
    assert quote.fetched_at.tzinfo is not None


def test_missing_currency_does_not_spoil_quote(monkeypatch, today):
    history, _ = load_yahoo_history()
    use_fake_ticker(monkeypatch, history=history, metadata_error=True)
    quote = YahooMarketData().get_quote("AAPL")
    assert quote.last_price == pytest.approx(259.58)
    assert quote.currency is None


def test_failing_extras_leave_blanks_but_keep_the_price(monkeypatch, today):
    history, _ = load_yahoo_history()
    use_fake_ticker(monkeypatch, history=history, shares_error=True, calendar_error=True)
    quote = YahooMarketData().get_quote("AAPL")
    assert quote.last_price == pytest.approx(259.58)
    assert quote.market_cap is None
    assert quote.next_earnings is None


def test_etf_without_share_count_has_no_market_cap(monkeypatch, today):
    history, _ = load_yahoo_history()
    use_fake_ticker(monkeypatch, history=history, shares=None, calendar={})
    quote = YahooMarketData().get_quote("SPY")
    assert quote.market_cap is None
    assert quote.next_earnings is None


@pytest.mark.parametrize(
    "error", [YFTzMissingError("ZZZZ"), YFPricesMissingError("ZZZZ", "(period=1y)")]
)
def test_yahoo_unknown_ticker_errors_become_not_found(monkeypatch, error):
    use_fake_ticker(monkeypatch, error=error)
    with pytest.raises(TickerNotFound):
        YahooMarketData().get_quote("ZZZZ")


def test_empty_history_is_not_found(monkeypatch):
    history, _ = load_yahoo_history()
    use_fake_ticker(monkeypatch, history=history.iloc[0:0])
    with pytest.raises(TickerNotFound):
        YahooMarketData().get_quote("ZZZZ")


def test_network_failure_is_market_data_error_not_not_found(monkeypatch):
    use_fake_ticker(monkeypatch, error=ConnectionError("connection reset"))
    with pytest.raises(MarketDataError) as info:
        YahooMarketData().get_quote("AAPL")
    assert not isinstance(info.value, TickerNotFound)


def test_missing_latest_close_uses_last_available():
    history, currency = load_yahoo_history()
    history.iloc[-1, history.columns.get_loc("Close")] = math.nan
    quote = quote_from_history("AAPL", history, currency)
    assert quote.last_price == pytest.approx(258.04)
    assert quote.previous_close == pytest.approx(254.87)
    assert quote.session_date == date(2026, 10, 29)
    assert (quote.day_low, quote.day_high) == pytest.approx((255.00, 258.60))


def test_single_day_has_no_previous_close():
    history, currency = load_yahoo_history()
    quote = quote_from_history("AAPL", history.iloc[-1:], currency)
    assert quote.last_price == pytest.approx(259.58)
    assert quote.previous_close is None
    assert (quote.year_low, quote.year_high) == pytest.approx((257.60, 260.15))


@pytest.mark.parametrize(
    ("calendar", "expected"),
    [
        ({"Earnings Date": [date(2026, 11, 1)]}, date(2026, 11, 1)),  # today counts
        ({"Earnings Date": [date(2026, 11, 20), date(2026, 11, 18)]}, date(2026, 11, 18)),
        ({"Earnings Date": [date(2026, 10, 31)]}, None),  # only a past date
        ({"Earnings Date": []}, None),
        ({}, None),
        (None, None),
    ],
)
def test_next_earnings_date(calendar, expected):
    assert next_earnings_date(calendar, date(2026, 11, 1)) == expected


def test_search_keeps_only_us_listed_stocks_and_etfs():
    results = search_results_from_quotes(load_yahoo_search(), max_results=10)
    assert [(r.symbol, r.exchange, r.kind) for r in results] == [
        ("AMZN", "NASDAQ", "stock"),
        ("AMZY", "NYSE Arca", "etf"),
        ("SAH-B", "NYSE", "stock"),  # share class written our way
    ]
    assert results[0].name == "Amazon.com, Inc."
    assert results[2].name == "Sample Amazonia Holdings"  # no longname -> shortname


def test_search_respects_max_results_and_drops_duplicates():
    quotes = load_yahoo_search()
    results = search_results_from_quotes(quotes + quotes, max_results=2)
    assert [r.symbol for r in results] == ["AMZN", "AMZY"]


def test_search_via_yfinance(monkeypatch):
    seen = {}

    class FakeSearch:
        def __init__(self, query, **kwargs):
            seen["query"], seen["kwargs"] = query, kwargs
            self.quotes = load_yahoo_search()

    monkeypatch.setattr(yahoo.yf, "Search", FakeSearch)
    results = YahooMarketData(timeout_seconds=3).search("amazon", max_results=5)
    assert [r.symbol for r in results] == ["AMZN", "AMZY", "SAH-B"]
    assert seen["query"] == "amazon"
    assert seen["kwargs"]["timeout"] == 3
    assert seen["kwargs"]["news_count"] == 0


def test_search_failure_is_market_data_error(monkeypatch):
    def broken(*args, **kwargs):
        raise ConnectionError("down")

    monkeypatch.setattr(yahoo.yf, "Search", broken)
    with pytest.raises(MarketDataError):
        YahooMarketData().search("amazon", max_results=5)


# --- after the close: Yahoo's history can lack today's row (seen 2026-11-03) -------------

# Mon Nov 2, 2026, 4:00 PM Eastern (the sample history's last row is Fri Oct 30: 259.58).
MONDAY_CLOSE = datetime(2026, 11, 2, 21, 0, tzinfo=UTC).timestamp()
LATEST = {
    "regularMarketPrice": 262.10,
    "regularMarketTime": MONDAY_CLOSE,
    "regularMarketDayLow": 258.50,
    "regularMarketDayHigh": 272.00,
    "regularMarketVolume": 41_000_000,
}


def test_missing_last_day_is_taken_from_the_latest_regular_price(monkeypatch, today):
    history, _ = load_yahoo_history()
    use_fake_ticker(monkeypatch, history=history, metadata=LATEST)
    q = YahooMarketData().get_quote("AMZN")
    assert q.last_price == 262.10
    assert q.previous_close == pytest.approx(259.58)  # Friday's close
    assert q.session_date == date(2026, 11, 2)
    assert (q.day_low, q.day_high, q.volume) == (258.50, 272.00, 41_000_000)
    assert q.year_high == 272.00  # above the history's 52-week high of 271.40
    assert q.year_low == pytest.approx(186.20)
    assert q.market_cap == pytest.approx(15_000_000_000 * 262.10)


def test_latest_price_ignored_when_history_has_that_day(today):
    history, _ = load_yahoo_history()
    quote = quote_from_history("AAPL", history, "USD")
    friday = {**LATEST, "regularMarketTime": datetime(2026, 10, 30, 20, 0, tzinfo=UTC).timestamp()}
    assert with_latest_regular_price(quote, friday) == quote


@pytest.mark.parametrize(
    "metadata",
    [
        {},
        {"regularMarketPrice": 262.10},  # no time
        {"regularMarketTime": MONDAY_CLOSE},  # no price
        {**LATEST, "regularMarketPrice": 0},
        {**LATEST, "regularMarketPrice": "n/a"},
        {**LATEST, "regularMarketTime": 1e20},  # nonsense time
    ],
)
def test_unusable_latest_price_leaves_quote_alone(metadata):
    history, _ = load_yahoo_history()
    quote = quote_from_history("AAPL", history, "USD")
    assert with_latest_regular_price(quote, metadata) == quote
