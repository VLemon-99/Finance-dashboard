"""The Yahoo adapter turns yfinance output into QuoteData and sorts errors correctly.

yfinance is replaced with a fake, so nothing goes over the network.
"""

import math

import pytest
from yfinance.exceptions import YFPricesMissingError, YFTzMissingError

from app.providers import yahoo
from app.providers.market_data import MarketDataError, TickerNotFound
from app.providers.yahoo import YahooMarketData, quote_from_history
from tests.conftest import load_yahoo_history


class FakeTicker:
    def __init__(self, history=None, error=None, metadata_error=False):
        self._history, self._error, self._metadata_error = history, error, metadata_error

    def history(self, **kwargs):
        if self._error:
            raise self._error
        return self._history

    @property
    def history_metadata(self):
        if self._metadata_error:
            raise ConnectionError("metadata fetch failed")
        return {"currency": "USD"}


def use_fake_ticker(monkeypatch, **kwargs):
    monkeypatch.setattr(yahoo.yf, "Ticker", lambda symbol: FakeTicker(**kwargs))


def test_parses_recorded_sample(monkeypatch):
    history, _ = load_yahoo_history()
    use_fake_ticker(monkeypatch, history=history)
    quote = YahooMarketData().get_quote("AAPL")
    assert quote.symbol == "AAPL"
    assert quote.last_price == pytest.approx(259.58)
    assert quote.previous_close == pytest.approx(258.04)
    assert quote.currency == "USD"
    assert quote.source == "yahoo"
    assert quote.fetched_at.tzinfo is not None


def test_missing_currency_does_not_spoil_quote(monkeypatch):
    history, _ = load_yahoo_history()
    use_fake_ticker(monkeypatch, history=history, metadata_error=True)
    quote = YahooMarketData().get_quote("AAPL")
    assert quote.last_price == pytest.approx(259.58)
    assert quote.currency is None


@pytest.mark.parametrize(
    "error", [YFTzMissingError("ZZZZ"), YFPricesMissingError("ZZZZ", "(period=5d)")]
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


def test_single_day_has_no_previous_close():
    history, currency = load_yahoo_history()
    quote = quote_from_history("AAPL", history.iloc[-1:], currency)
    assert quote.last_price == pytest.approx(259.58)
    assert quote.previous_close is None
