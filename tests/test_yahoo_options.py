"""The Yahoo adapter's option parts: expiration dates, option chains and the T-bill rate.

yfinance is replaced with a fake, so nothing goes over the network.
"""

from collections import namedtuple
from datetime import date

import pandas as pd
import pytest

from app.providers import yahoo
from app.providers.market_data import MarketDataError, NoOptionChain
from app.providers.yahoo import (
    YahooMarketData,
    chain_from_yahoo,
    expirations_from_yahoo,
    rate_from_history,
)
from tests.conftest import OPTION_EXPIRATION, load_yahoo_options

Options = namedtuple("Options", ["calls", "puts", "underlying"])


class FakeOptionTicker:
    def __init__(self, sample=None, error=None, history=None):
        self._sample, self._error, self._history = sample, error, history
        self.asked_for: list[str] = []

    @property
    def options(self):
        if self._error:
            raise self._error
        return tuple(self._sample["expirations"]) if self._sample else ()

    def option_chain(self, when):
        self.asked_for.append(when)
        if self._error:
            raise self._error
        if when not in self._sample["expirations"]:
            raise ValueError(f"Expiration `{when}` cannot be found.")
        return Options(self._sample["calls"], self._sample["puts"], self._sample["underlying"])

    def history(self, **kwargs):
        if self._error:
            raise self._error
        return self._history


def use_fake(monkeypatch, **kwargs) -> FakeOptionTicker:
    ticker = FakeOptionTicker(**kwargs)
    monkeypatch.setattr(yahoo.yf, "Ticker", lambda symbol: ticker)
    return ticker


# --- expiration dates -------------------------------------------------------------


def test_expirations_parsed_sorted_and_bad_ones_skipped(monkeypatch):
    use_fake(monkeypatch, sample=load_yahoo_options())
    dates = YahooMarketData().get_option_expirations("AAPL")
    assert dates == [
        date(2026, 11, 6),
        date(2026, 11, 13),
        date(2026, 11, 20),
        date(2026, 12, 18),
        date(2027, 1, 15),
    ]


def test_no_options_listed_gives_empty_list(monkeypatch):
    use_fake(monkeypatch, sample=None)
    assert YahooMarketData().get_option_expirations("ZZZZQ") == []
    assert expirations_from_yahoo(None) == []


def test_expirations_network_failure_is_market_data_error(monkeypatch):
    use_fake(monkeypatch, error=ConnectionError("reset"))
    with pytest.raises(MarketDataError):
        YahooMarketData().get_option_expirations("AAPL")


# --- option chain -----------------------------------------------------------------


def test_f2_2_chain_fields_from_recorded_sample(monkeypatch):
    fake = use_fake(monkeypatch, sample=load_yahoo_options())
    chain = YahooMarketData().get_option_chain("AAPL", OPTION_EXPIRATION)

    assert fake.asked_for == ["2026-11-20"]
    assert chain.underlying == "AAPL"
    assert chain.expiration == OPTION_EXPIRATION
    assert chain.underlying_price == pytest.approx(259.58)
    assert chain.source == "yahoo"
    calls = [c for c in chain.contracts if c.option_type == "call"]
    puts = [c for c in chain.contracts if c.option_type == "put"]
    assert len(calls) == 8 and len(puts) == 7
    assert [c.strike for c in calls] == sorted(c.strike for c in calls)

    c260 = next(c for c in calls if c.strike == 260)
    assert c260.contract_symbol == "AAPL261120C00260000"
    assert (c260.bid, c260.ask) == (6.26, 6.36)
    assert c260.implied_volatility == pytest.approx(0.28)
    assert (c260.volume, c260.open_interest) == (4875, 15230)
    assert c260.last_price is not None


def test_missing_numbers_become_none_not_nan(monkeypatch):
    use_fake(monkeypatch, sample=load_yahoo_options())
    chain = YahooMarketData().get_option_chain("AAPL", OPTION_EXPIRATION)
    far = next(c for c in chain.contracts if c.strike == 350)
    assert far.volume is None
    assert far.bid == 0.0
    assert far.open_interest == 12


def test_rows_without_symbol_or_strike_are_skipped():
    sample = load_yahoo_options()
    calls = pd.concat(
        [sample["calls"], pd.DataFrame([{"contractSymbol": None, "strike": 100.0}])],
        ignore_index=True,
    )
    puts = sample["puts"].copy()
    puts.loc[0, "strike"] = float("nan")
    chain = chain_from_yahoo("AAPL", OPTION_EXPIRATION, calls, puts, sample["underlying"])
    assert sum(c.option_type == "call" for c in chain.contracts) == 8
    assert sum(c.option_type == "put" for c in chain.contracts) == 6


def test_chain_without_underlying_price():
    sample = load_yahoo_options()
    chain = chain_from_yahoo("AAPL", OPTION_EXPIRATION, sample["calls"], sample["puts"], None)
    assert chain.underlying_price is None


def test_unknown_expiration_is_no_option_chain(monkeypatch):
    use_fake(monkeypatch, sample=load_yahoo_options())
    with pytest.raises(NoOptionChain, match="Nov 21, 2026"):
        YahooMarketData().get_option_chain("AAPL", date(2026, 11, 21))


def test_empty_chain_is_no_option_chain():
    with pytest.raises(NoOptionChain):
        chain_from_yahoo("AAPL", OPTION_EXPIRATION, None, pd.DataFrame(), {})


def test_chain_network_failure_is_market_data_error(monkeypatch):
    use_fake(monkeypatch, error=ConnectionError("reset"))
    with pytest.raises(MarketDataError) as info:
        YahooMarketData().get_option_chain("AAPL", OPTION_EXPIRATION)
    assert not isinstance(info.value, NoOptionChain)


# --- risk-free rate ---------------------------------------------------------------


def _irx(closes):
    return pd.DataFrame({"Close": closes})


def test_rate_from_t_bill_percent():
    assert rate_from_history(_irx([4.31, 4.25])) == pytest.approx(0.0425)
    assert rate_from_history(_irx([4.31, float("nan")])) == pytest.approx(0.0431)


@pytest.mark.parametrize(
    "history", [None, pd.DataFrame(), _irx([float("nan")]), _irx([-1.0]), _irx([40.0])]
)
def test_unusable_rate_is_market_data_error(history):
    with pytest.raises(MarketDataError):
        rate_from_history(history)


def test_rate_via_yfinance(monkeypatch):
    use_fake(monkeypatch, history=_irx([4.1]))
    assert YahooMarketData().get_risk_free_rate() == pytest.approx(0.041)


def test_rate_network_failure_is_market_data_error(monkeypatch):
    use_fake(monkeypatch, error=ConnectionError("reset"))
    with pytest.raises(MarketDataError):
        YahooMarketData().get_risk_free_rate()
