"""Trading days: weekends and NYSE holidays, with known answers from the NYSE's own
published holiday calendars."""

from datetime import date

import pytest

from app.market_calendar import (
    closed_reason,
    easter_sunday,
    is_trading_day,
    last_trading_day,
    nyse_holidays,
)


@pytest.mark.parametrize(
    "year,easter",
    [
        (2000, date(2000, 4, 23)),
        (2019, date(2019, 4, 21)),
        (2025, date(2025, 4, 20)),
        (2026, date(2026, 4, 5)),
        (2027, date(2027, 3, 28)),
        (2038, date(2038, 4, 25)),
    ],
)
def test_easter_sunday(year, easter):
    assert easter_sunday(year) == easter


def test_nyse_holidays_2025():
    assert sorted(nyse_holidays(2025)) == [
        date(2025, 1, 1),
        date(2025, 1, 20),
        date(2025, 2, 17),
        date(2025, 4, 18),
        date(2025, 5, 26),
        date(2025, 6, 19),
        date(2025, 7, 4),
        date(2025, 9, 1),
        date(2025, 11, 27),
        date(2025, 12, 25),
    ]


def test_nyse_holidays_2026():
    # Independence Day falls on a Saturday: observed Friday Jul 3.
    assert sorted(nyse_holidays(2026)) == [
        date(2026, 1, 1),
        date(2026, 1, 19),
        date(2026, 2, 16),
        date(2026, 4, 3),
        date(2026, 5, 25),
        date(2026, 6, 19),
        date(2026, 7, 3),
        date(2026, 9, 7),
        date(2026, 11, 26),
        date(2026, 12, 25),
    ]


def test_nyse_holidays_2027():
    # Juneteenth and Christmas on Saturdays -> the Fridays before; July 4 on a Sunday -> Monday.
    assert sorted(nyse_holidays(2027)) == [
        date(2027, 1, 1),
        date(2027, 1, 18),
        date(2027, 2, 15),
        date(2027, 3, 26),
        date(2027, 5, 31),
        date(2027, 6, 18),
        date(2027, 7, 5),
        date(2027, 9, 6),
        date(2027, 11, 25),
        date(2027, 12, 24),
    ]


def test_new_years_day_on_a_saturday_is_not_moved_to_friday():
    # Jan 1, 2022 was a Saturday; the NYSE stayed open on Friday Dec 31, 2021.
    assert is_trading_day(date(2021, 12, 31))
    assert date(2021, 12, 31) not in nyse_holidays(2021)
    assert date(2022, 1, 1) not in nyse_holidays(2022)


def test_juneteenth_only_from_2022():
    assert is_trading_day(date(2021, 6, 18))  # a Friday: Juneteenth wasn't a holiday yet
    assert date(2022, 6, 20) in nyse_holidays(2022)  # Sunday Jun 19 -> Monday


def test_closed_reasons():
    assert closed_reason(date(2025, 11, 30)) == "a Sunday"  # the owner's date
    assert closed_reason(date(2025, 11, 29)) == "a Saturday"
    assert closed_reason(date(2025, 11, 27)) == "a market holiday (Thanksgiving Day)"
    assert closed_reason(date(2026, 4, 3)) == "a market holiday (Good Friday)"
    assert closed_reason(date(2025, 11, 28)) is None  # day after Thanksgiving: open (early close)
    assert closed_reason(date(2025, 1, 9)) is None
    assert closed_reason(date(2025, 1, 9), {date(2025, 1, 9)}) == "a day the market was closed"


def test_last_trading_day():
    assert last_trading_day(date(2026, 11, 4)) == date(2026, 11, 4)  # a Wednesday
    assert last_trading_day(date(2026, 11, 8)) == date(2026, 11, 6)  # Sunday -> Friday
    assert last_trading_day(date(2026, 4, 5)) == date(2026, 4, 2)  # Easter weekend -> Thursday
    assert last_trading_day(date(2025, 1, 10), {date(2025, 1, 9)}) == date(2025, 1, 10)
    assert last_trading_day(date(2025, 1, 9), {date(2025, 1, 9)}) == date(2025, 1, 8)
