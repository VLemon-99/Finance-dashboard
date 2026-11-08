"""The macro dashboard's formulas (SPEC F6.1), each with known answers worked out by hand."""

from datetime import date

import pytest

from app.services import macro_math as mm


def test_months_before_keeps_the_day_or_uses_the_month_end():
    assert mm.months_before(date(2026, 11, 5), 1) == date(2026, 10, 5)
    assert mm.months_before(date(2026, 1, 15), 1) == date(2025, 12, 15)
    assert mm.months_before(date(2026, 3, 31), 1) == date(2026, 2, 28)
    assert mm.months_before(date(2024, 3, 31), 1) == date(2024, 2, 29)  # leap year
    assert mm.months_before(date(2026, 9, 1), 12) == date(2025, 9, 1)
    assert mm.months_before(date(2026, 11, 5), 60) == date(2021, 11, 5)


def test_value_on_or_before():
    series = [(date(2026, 1, 1), 1.0), (date(2026, 2, 1), 2.0), (date(2026, 3, 1), 3.0)]
    assert mm.value_on_or_before(series, date(2026, 2, 1)) == (date(2026, 2, 1), 2.0)
    assert mm.value_on_or_before(series, date(2026, 2, 20)) == (date(2026, 2, 1), 2.0)
    assert mm.value_on_or_before(series, date(2025, 12, 31)) is None


def test_yearly_change_is_how_inflation_is_quoted():
    # CPI 300.0 in Sep 2025 and 309.0 in Sep 2026: prices are 3.0% higher than a year ago.
    series = [(date(2025, 9, 1), 300.0), (date(2026, 8, 1), 306.0), (date(2026, 9, 1), 309.0)]
    assert mm.yearly_change(series) == [(date(2026, 9, 1), pytest.approx(3.0))]


def test_yearly_change_skips_points_without_a_year_earlier_value_close_enough():
    # Aug 2026 has nothing within 10 days before Aug 2025 (the nearest is May 2025).
    series = [(date(2025, 5, 1), 280.0), (date(2026, 8, 1), 308.0)]
    assert mm.yearly_change(series) == []


def test_month_earlier_daily_and_quarterly():
    daily = [(date(2026, 10, 2), 0.38), (date(2026, 10, 5), 0.40), (date(2026, 11, 5), 0.55)]
    assert mm.month_earlier(daily) == (date(2026, 10, 5), 0.40)
    # A weekend a month back: the Friday before.
    daily = [(date(2026, 9, 4), 0.30), (date(2026, 10, 6), 0.55)]
    assert mm.month_earlier(daily) == (date(2026, 9, 4), 0.30)
    # Quarterly: the previous quarter (shown with its own date on the page).
    quarterly = [(date(2026, 1, 1), 7_000_000.0), (date(2026, 4, 1), 7_100_000.0)]
    assert mm.month_earlier(quarterly) == (date(2026, 1, 1), 7_000_000.0)
    assert mm.month_earlier([(date(2026, 4, 1), 1.0)]) is None


def test_change_in_points_or_percent():
    assert mm.change(0.55, 0.40, as_percent=False) == pytest.approx(0.15)
    assert mm.change(7_100_000, 7_000_000, as_percent=True) == pytest.approx(1.428571, abs=1e-6)
    assert mm.change(5.0, 0.0, as_percent=True) is None


def test_earnings_yield():
    assert mm.earnings_yield(25.0) == pytest.approx(4.0)
    assert mm.earnings_yield(20.0) == pytest.approx(5.0)
    with pytest.raises(ValueError):
        mm.earnings_yield(0)


def test_equity_risk_premium_known_answers():
    # P/E 25 -> earnings yield 4.00%; 10-year Treasury 4.25% -> premium -0.25 points.
    # P/E 20 -> 5.00% against 3.50% -> +1.50. The Saturday reading uses Friday's yield.
    pe = [(date(2026, 11, 5), 25.0), (date(2026, 11, 6), 20.0), (date(2026, 11, 7), 20.0)]
    treasury = [(date(2026, 11, 5), 4.25), (date(2026, 11, 6), 3.50)]
    assert mm.equity_risk_premium(pe, treasury) == [
        (date(2026, 11, 5), pytest.approx(-0.25)),
        (date(2026, 11, 6), pytest.approx(1.50)),
        (date(2026, 11, 7), pytest.approx(1.50)),
    ]


def test_equity_risk_premium_needs_a_recent_treasury_yield():
    pe = [(date(2026, 11, 5), 25.0)]
    assert mm.equity_risk_premium(pe, [(date(2026, 10, 1), 4.0)]) == []
    assert mm.equity_risk_premium(pe, []) == []


def test_combined_pe_like_an_index():
    # 3,000 at 30x earns 100; 1,000 at 10x earns 100: 4,000 / 200 = 20.0.
    # 3,000 at 30x and 1,000 at 20x: 4,000 / (100 + 50) = 26.67 (a plain average of the
    # two P/Es would wrongly say 25).
    assert mm.combined_pe([(3000, 30), (1000, 10)]) == pytest.approx(20.0)
    assert mm.combined_pe([(3000, 30), (1000, 20)]) == pytest.approx(26.6667, abs=1e-4)
    with pytest.raises(ValueError):
        mm.combined_pe([])


def test_within_years_and_weekly_thinning():
    series = [(date(2020, 1, 1), 1.0), (date(2025, 11, 5), 2.0), (date(2026, 11, 5), 3.0)]
    assert mm.within_years(series, 1) == series[1:]
    assert mm.within_years(series, 5) == series[1:]
    assert mm.within_years([], 5) == []
    # Mon-Fri of one week, then Monday of the next: the Friday and the Monday are kept.
    week = [(date(2026, 11, d), float(d)) for d in (2, 3, 4, 5, 6, 9)]
    assert mm.thin_to_weekly(week) == [(date(2026, 11, 6), 6.0), (date(2026, 11, 9), 9.0)]
