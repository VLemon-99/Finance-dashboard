"""Option chain service: expirations, reuse/refresh, stale fallback, greeks (F2.1-F2.4).

Uses the fake data source with the recorded option sample and a frozen clock
(Tue Nov 3, 2026, 10:00 AM Eastern).
"""

from datetime import UTC, date, datetime, timedelta

import pytest
from sqlmodel import select

from app.db.models import OptionQuote
from app.services.options import RiskFreeRate, option_chain
from app.services.options_math import black_scholes, years_to_expiry
from tests.conftest import FAKE_RISK_FREE_RATE, OPTION_EXPIRATION

WINDOW = timedelta(minutes=15)
MIN_IV = 0.01


@pytest.fixture
def rates():
    return RiskFreeRate(fallback=0.04)


@pytest.fixture
def chain_for(session, fake_market_data, rates, options_clock):
    def chain_for(symbol="AAPL", expiration=OPTION_EXPIRATION, option_type="call", **kwargs):
        kwargs.setdefault("max_age", WINDOW)
        kwargs.setdefault("min_iv", MIN_IV)
        return option_chain(
            session,
            fake_market_data,
            rates,
            symbol,
            expiration=expiration,
            option_type=option_type,
            **kwargs,
        )

    return chain_for


# --- F2.1: expirations, calls and puts ------------------------------------------------


def test_f2_1_earliest_expiration_by_default(chain_for):
    chain = chain_for(expiration=None)
    assert chain.status == "ok"
    assert chain.expirations[0] == date(2026, 11, 6)
    assert len(chain.expirations) == 5
    assert chain.expiration == date(2026, 11, 6)
    assert chain.dte == 3


def test_f2_1_unknown_expiration_falls_back_to_earliest(chain_for, fake_market_data):
    chain = chain_for(expiration=date(2030, 1, 1))
    assert chain.expiration == date(2026, 11, 6)
    # Only listed dates are ever asked for.
    assert fake_market_data.chain_calls == [("AAPL", date(2026, 11, 6))]


def test_f2_1_calls_and_puts(chain_for):
    calls = chain_for(option_type="call")
    puts = chain_for(option_type="put")
    assert len(calls.rows) == 8 and {r.option_type for r in calls.rows} == {"call"}
    assert len(puts.rows) == 7 and {r.option_type for r in puts.rows} == {"put"}
    assert [r.strike for r in puts.rows] == sorted(r.strike for r in puts.rows)


def test_expired_dates_are_not_offered(chain_for, options_clock):
    # Friday Nov 6, 4:00 PM Eastern: the Nov 6 options have just stopped trading.
    options_clock.now = datetime(2026, 11, 6, 21, 0, tzinfo=UTC)
    chain = chain_for(expiration=None)
    assert date(2026, 11, 6) not in chain.expirations
    assert chain.expiration == date(2026, 11, 13)


# --- F2.2 / F2.3: row fields and greeks -----------------------------------------------


def test_f2_2_row_fields_and_f2_3_greeks(chain_for, options_clock):
    chain = chain_for()
    row = next(r for r in chain.rows if r.strike == 260)
    assert row.contract_symbol == "AAPL261120C00260000"
    assert (row.bid, row.ask) == (6.26, 6.36)
    assert row.mid == pytest.approx(6.31)
    assert (row.volume, row.open_interest) == (4875, 15230)
    assert row.implied_volatility == pytest.approx(0.28)
    assert row.dte == 17
    assert not row.expired
    assert row.underlying_price == pytest.approx(259.58)
    assert chain.rate.rate == FAKE_RISK_FREE_RATE

    years = years_to_expiry(OPTION_EXPIRATION, options_clock.now)
    expected = black_scholes("call", 259.58, 260, years, FAKE_RISK_FREE_RATE, 0.28)
    assert row.greeks == expected
    # Sanity: an at-the-money call has a delta near 0.5 and loses value every day.
    assert 0.45 < row.greeks.delta < 0.55
    assert row.greeks.theta < 0


def test_placeholder_iv_shows_no_greeks(chain_for):
    far = next(r for r in chain_for().rows if r.strike == 350)
    assert far.implied_volatility is None  # Yahoo's 0.001% placeholder
    assert far.greeks is None
    assert far.mid is None  # no bid
    assert far.open_interest == 12  # the rest of the row is still shown


def test_min_iv_comes_from_settings(chain_for):
    rows = chain_for(min_iv=0.30).rows
    assert next(r for r in rows if r.strike == 245).greeks is not None  # IV 31%
    assert next(r for r in rows if r.strike == 260).greeks is None  # IV 28%


# --- F2.4: nearest strike and in the money ----------------------------------------------


def test_f2_4_nearest_strike_and_in_the_money(chain_for):
    calls = chain_for(option_type="call")
    puts = chain_for(option_type="put")
    assert calls.nearest_strike == 260 and puts.nearest_strike == 260
    assert [r.strike for r in calls.rows if r.in_the_money] == [245, 250, 255]
    assert [r.strike for r in puts.rows if r.in_the_money] == [260, 265, 270, 275]


def test_stock_price_used_when_chain_has_none(chain_for, fake_market_data, monkeypatch):
    from tests import conftest

    original = conftest.load_yahoo_options

    def without_price(*args):
        sample = original(*args)
        sample["underlying"] = {}
        return sample

    monkeypatch.setattr(conftest, "load_yahoo_options", without_price)
    chain = chain_for(stock_price=251.0)
    assert chain.underlying_price == 251.0
    assert chain.nearest_strike == 250


# --- F1.5 / F1.6 rules for option prices ------------------------------------------------


def test_prices_reused_within_window_and_refresh_always_fetches(
    chain_for, fake_market_data, options_clock
):
    chain_for()
    chain_for()
    assert len(fake_market_data.chain_calls) == 1
    options_clock.now += timedelta(minutes=16)
    chain_for()
    assert len(fake_market_data.chain_calls) == 2
    chain_for(max_age=None)  # Refresh button
    assert len(fake_market_data.chain_calls) == 3


def test_every_strike_is_stored_once(chain_for, session):
    chain_for()
    chain_for(max_age=None)
    rows = session.exec(select(OptionQuote)).all()
    assert len(rows) == 15
    assert {r.underlying for r in rows} == {"AAPL"}


def test_source_down_shows_stored_prices_as_stale(chain_for, fake_market_data, options_clock):
    first = chain_for()
    options_clock.now += timedelta(minutes=30)
    fake_market_data.mode = "down"
    chain = chain_for()
    assert chain.status == "stale"
    assert [r.strike for r in chain.rows] == [r.strike for r in first.rows]
    assert chain.fetched_at == first.fetched_at
    # The expiration list falls back to the dates with stored prices.
    assert chain.expirations == [OPTION_EXPIRATION]
    assert any("expiration dates" in n for n in chain.notes)
    assert any("Showing stored prices" in n for n in chain.notes)


def test_source_down_with_nothing_stored(chain_for, fake_market_data):
    fake_market_data.mode = "down"
    chain = chain_for()
    assert chain.status == "error"
    assert chain.rows == ()
    assert chain.notes


def test_chain_down_but_dates_ok(chain_for, fake_market_data, monkeypatch):
    def broken(symbol, expiration):
        raise RuntimeError("unexpected")

    monkeypatch.setattr(fake_market_data, "get_option_chain", broken)
    chain = chain_for()
    assert chain.status == "error"
    assert chain.expiration == OPTION_EXPIRATION
    assert "No stored prices" in chain.notes[0]


def test_unexpected_crash_does_not_break(chain_for, fake_market_data):
    fake_market_data.mode = "crash"
    assert chain_for().status == "error"


def test_ticker_without_options(chain_for, fake_market_data):
    fake_market_data.no_options.add("BRK-A")
    chain = chain_for(symbol="BRK-A")
    assert chain.status == "no_options"
    assert chain.notes == ["No listed options found for BRK-A."]


def test_strikes_dropped_by_the_source_are_not_shown(chain_for, session, options_clock):
    chain_for()
    # Pretend an extra strike was stored by an earlier fetch.
    stale_row = OptionQuote(
        contract_symbol="AAPL261120C00999000",
        underlying="AAPL",
        expiration=OPTION_EXPIRATION,
        option_type="call",
        strike=999,
        fetched_at=options_clock.now - timedelta(days=1),
        source="yahoo",
    )
    session.add(stale_row)
    session.commit()
    assert 999 not in [r.strike for r in chain_for().rows]


# --- the risk-free rate ------------------------------------------------------------------


def test_rate_fetched_once_per_window(chain_for, fake_market_data, options_clock):
    chain_for()
    chain_for(option_type="put")
    assert fake_market_data.rate_calls == 1
    options_clock.now += timedelta(minutes=16)
    chain_for()
    assert fake_market_data.rate_calls == 2


def test_rate_falls_back_to_settings_and_says_so(chain_for, fake_market_data, options_clock):
    fake_market_data.rate_down = True
    chain = chain_for()
    assert chain.rate.rate == 0.04 and chain.rate.is_fallback
    assert any("4.00% from config/settings.yaml" in n for n in chain.notes)
    row = next(r for r in chain.rows if r.strike == 260)
    years = years_to_expiry(OPTION_EXPIRATION, options_clock.now)
    assert row.greeks == black_scholes("call", 259.58, 260, years, 0.04, 0.28)


def test_rate_keeps_last_good_value_when_source_fails(chain_for, fake_market_data, options_clock):
    chain_for()
    fake_market_data.rate_down = True
    options_clock.now += timedelta(minutes=16)
    chain = chain_for()
    assert chain.rate.rate == FAKE_RISK_FREE_RATE and not chain.rate.is_fallback
    assert any("Couldn't update the 13-week T-bill rate" in n for n in chain.notes)
