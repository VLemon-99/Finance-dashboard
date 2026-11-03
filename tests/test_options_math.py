"""Option math against known answers (SPEC F2.3).

Besides the SPEC case, the examples come from J. Hull, "Options, Futures, and Other
Derivatives" (the standard textbook): the call/put price example and the greeks example
(stock 49, strike 50, 20 weeks, rate 5%, volatility 20%).
"""

import math
from datetime import UTC, date, datetime

import pytest

from app.services.options_math import (
    black_scholes,
    days_to_expiry,
    expires_at,
    is_expired,
    is_in_the_money,
    mid_price,
    nearest_strike,
    normal_cdf,
    years_to_expiry,
)

TOLERANCE = 0.0001


def test_f2_3_spec_case_call_price_and_delta():
    g = black_scholes("call", spot=100, strike=100, years=1, rate=0.05, volatility=0.20)
    assert g.price == pytest.approx(10.4506, abs=TOLERANCE)
    assert g.delta == pytest.approx(0.6368, abs=TOLERANCE)


def test_f2_3_spec_case_other_greeks():
    g = black_scholes("call", spot=100, strike=100, years=1, rate=0.05, volatility=0.20)
    assert g.gamma == pytest.approx(0.018762, abs=1e-6)
    # Theta -6.4140 per year -> per calendar day.
    assert g.theta == pytest.approx(-6.4140 / 365, abs=1e-6)
    # Vega 37.524 per 1.00 of volatility -> per 1 percentage point.
    assert g.vega == pytest.approx(0.37524, abs=1e-5)


def test_f2_3_spec_case_put():
    g = black_scholes("put", spot=100, strike=100, years=1, rate=0.05, volatility=0.20)
    assert g.price == pytest.approx(5.5735, abs=TOLERANCE)
    assert g.delta == pytest.approx(-0.3632, abs=TOLERANCE)
    assert g.theta == pytest.approx(-1.6579 / 365, abs=1e-6)


def test_hull_price_example():
    """Stock 42, strike 40, 6 months, rate 10%, volatility 20% -> call 4.76, put 0.81."""
    call = black_scholes("call", spot=42, strike=40, years=0.5, rate=0.10, volatility=0.20)
    put = black_scholes("put", spot=42, strike=40, years=0.5, rate=0.10, volatility=0.20)
    assert call.price == pytest.approx(4.76, abs=0.005)
    assert put.price == pytest.approx(0.81, abs=0.005)


def test_hull_greeks_example():
    """Stock 49, strike 50, 20 weeks: delta 0.522, gamma 0.066, theta -4.31/yr, vega 12.1."""
    g = black_scholes("call", spot=49, strike=50, years=20 / 52, rate=0.05, volatility=0.20)
    assert g.price == pytest.approx(2.40, abs=0.005)
    assert g.delta == pytest.approx(0.522, abs=0.0005)
    assert g.gamma == pytest.approx(0.066, abs=0.0005)
    assert g.theta * 365 == pytest.approx(-4.31, abs=0.005)
    assert g.vega * 100 == pytest.approx(12.1, abs=0.05)


@pytest.mark.parametrize(
    "spot,strike,years,rate,vol", [(100, 90, 0.25, 0.04, 0.35), (50, 60, 2, 0.01, 0.5)]
)
def test_put_call_parity(spot, strike, years, rate, vol):
    """call - put = stock - strike x discount, and the deltas differ by exactly 1."""
    call = black_scholes("call", spot, strike, years, rate, vol)
    put = black_scholes("put", spot, strike, years, rate, vol)
    assert call.price - put.price == pytest.approx(spot - strike * math.exp(-rate * years))
    assert call.delta - put.delta == pytest.approx(1.0)
    assert call.gamma == pytest.approx(put.gamma)
    assert call.vega == pytest.approx(put.vega)


@pytest.mark.parametrize(
    "spot,strike,years,vol",
    [
        (100, 100, 0, 0.2),
        (100, 100, -0.1, 0.2),
        (100, 100, 1, 0),
        (100, 100, 1, None),
        (0, 100, 1, 0.2),
        (100, 0, 1, 0.2),
        (None, 100, 1, 0.2),
        (100, 100, 1, float("nan")),
    ],
)
def test_no_greeks_when_inputs_unusable(spot, strike, years, vol):
    """Expired, no IV from Yahoo, or a missing price -> no greeks (shown as —), no crash."""
    assert black_scholes("call", spot, strike, years, 0.05, vol) is None


def test_unknown_option_type_is_an_error():
    with pytest.raises(ValueError):
        black_scholes("straddle", 100, 100, 1, 0.05, 0.2)


def test_normal_cdf_known_values():
    assert normal_cdf(0) == pytest.approx(0.5)
    assert normal_cdf(1.96) == pytest.approx(0.975, abs=1e-4)
    assert normal_cdf(-1.0) == pytest.approx(0.158655, abs=1e-6)


# --- time --------------------------------------------------------------------------


def test_dte_counts_calendar_days():
    assert days_to_expiry(date(2026, 11, 20), today=date(2026, 11, 3)) == 17
    assert days_to_expiry(date(2026, 11, 3), today=date(2026, 11, 3)) == 0
    assert days_to_expiry(date(2026, 11, 2), today=date(2026, 11, 3)) == -1


def test_expires_at_4pm_eastern_in_utc():
    # November: Eastern standard time (UTC-5). July: daylight time (UTC-4).
    assert expires_at(date(2026, 11, 20)) == datetime(2026, 11, 20, 21, 0, tzinfo=UTC)
    assert expires_at(date(2026, 7, 17)) == datetime(2026, 7, 17, 20, 0, tzinfo=UTC)


def test_years_to_expiry():
    # 365 days before the close exactly -> 1 year.
    now = datetime(2025, 11, 20, 21, 0, tzinfo=UTC)
    assert years_to_expiry(date(2026, 11, 20), now) == pytest.approx(1.0)
    # Expiration day, 10:00 AM Eastern -> 6 hours left.
    morning = datetime(2026, 11, 20, 15, 0, tzinfo=UTC)
    assert years_to_expiry(date(2026, 11, 20), morning) == pytest.approx(6 / (365 * 24))
    # After the close -> 0, never negative.
    assert years_to_expiry(date(2026, 11, 20), datetime(2026, 11, 21, tzinfo=UTC)) == 0


def test_f2_7_expired_from_4pm_eastern_on_the_date():
    exp = date(2026, 11, 20)
    assert not is_expired(exp, datetime(2026, 11, 20, 20, 59, tzinfo=UTC))  # 3:59 PM ET
    assert is_expired(exp, datetime(2026, 11, 20, 21, 0, tzinfo=UTC))  # 4:00 PM ET
    assert is_expired(exp, datetime(2026, 11, 23, 14, 0, tzinfo=UTC))
    # Times read back from SQLite have no timezone; they're UTC.
    assert not is_expired(exp, datetime(2026, 11, 20, 15, 0))


# --- prices and strikes -------------------------------------------------------------


def test_mid_price():
    assert mid_price(1.20, 1.30) == pytest.approx(1.25)
    assert mid_price(None, 1.30) is None
    assert mid_price(0, 0.05) is None  # no bid: no real market
    assert mid_price(1.30, 1.20) is None  # crossed quote


def test_f2_4_in_the_money():
    assert is_in_the_money("call", strike=95, spot=100)
    assert not is_in_the_money("call", strike=105, spot=100)
    assert is_in_the_money("put", strike=105, spot=100)
    assert not is_in_the_money("put", strike=95, spot=100)
    assert not is_in_the_money("call", strike=100, spot=100)  # at the money
    assert not is_in_the_money("call", strike=95, spot=None)


def test_f2_4_nearest_strike():
    strikes = [95, 97.5, 100, 102.5, 105]
    assert nearest_strike(strikes, 101.0) == 100
    assert nearest_strike(strikes, 101.5) == 102.5
    assert nearest_strike(strikes, 101.25) == 100  # tie -> the lower strike
    assert nearest_strike(strikes, 500) == 105
    assert nearest_strike([], 100) is None
    assert nearest_strike(strikes, None) is None
