"""How numbers and dates are shown (known answers)."""

from datetime import date

import pytest

from app.web import formatting as f


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, "—"),
        (950, "950"),
        (36_540_100, "36.54M"),
        (1_250, "1.25K"),
        (412_000_000_000, "412.00B"),
        (3_850_000_000_000, "3.85T"),
    ],
)
def test_compact(value, expected):
    assert f.compact(value) == expected


def test_money_and_signed():
    assert f.money(1234.5) == "1,234.50"
    assert f.money(None) == "—"
    assert f.signed(1.54) == "+1.54"
    assert f.signed(-0.6) == "-0.60"
    assert f.signed(None) == "—"


def test_short_date():
    assert f.short_date(date(2026, 11, 19)) == "Nov 19, 2026"
    assert f.short_date(None) == "—"


def test_session_label_fixes_the_weekend_today_label():
    sunday = date(2026, 11, 1)
    assert f.session_label(date(2026, 11, 1), sunday) == "today"
    assert f.session_label(date(2026, 10, 30), sunday) == "on Fri, Oct 30"
    assert f.session_label(None, sunday) == "on the last trading day"


def test_price_range():
    assert f.price_range(257.6, 260.15) == "257.60 – 260.15"
    assert f.price_range(None, 260.15) == "—"


def test_option_number_formats():
    assert f.number(0.63683, 3) == "0.637"
    assert f.number(-0.017573, 3) == "-0.018"
    assert f.number(None, 3) == "—"
    assert f.whole(15230) == "15,230"
    assert f.whole(None) == "—"
    assert f.percent(0.2834) == "28.3%"
    assert f.percent(None) == "—"
    assert f.strike(260.0) == "260"
    assert f.strike(262.5) == "262.50"
