"""US stock market trading days: weekends and NYSE holidays are closed.

The regular holidays follow fixed rules, so they're calculated here (no extra library).
Rare one-off closures (e.g. a national day of mourning) are listed in config/settings.yaml
(`market_data.extra_closed_days`) and passed in as `extra_closed`.

Rules (NYSE):
- New Year's Day, Juneteenth (from 2022), Independence Day and Christmas: on a Saturday
  the market closes the Friday before, on a Sunday the Monday after. Exception: New Year's
  Day on a Saturday isn't moved to Friday Dec 31 (the market stays open).
- Martin Luther King Jr. Day (3rd Monday of January), Washington's Birthday (3rd Monday of
  February), Memorial Day (last Monday of May), Labor Day (1st Monday of September),
  Thanksgiving (4th Thursday of November).
- Good Friday (two days before Easter Sunday).
"""

from collections.abc import Collection
from datetime import date, timedelta
from functools import lru_cache

WEEKDAY_NAMES = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")


def easter_sunday(year: int) -> date:
    """Western (Gregorian) Easter Sunday, using the standard 'anonymous' algorithm."""
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    m = (32 + 2 * e + 2 * i - h - k) % 7
    n = (a + 11 * h + 22 * m) // 451
    month, day = divmod(h + m - 7 * n + 114, 31)
    return date(year, month, day + 1)


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    """The n-th given weekday (Monday = 0) of a month; n = -1 means the last one."""
    if n > 0:
        first = date(year, month, 1)
        return first + timedelta(days=(weekday - first.weekday()) % 7 + 7 * (n - 1))
    last = date(year + (month == 12), month % 12 + 1, 1) - timedelta(days=1)
    return last - timedelta(days=(last.weekday() - weekday) % 7)


def _observed(day: date, move_saturday: bool = True) -> date | None:
    """A fixed-date holiday on a weekend is observed on Friday (Saturday) or Monday."""
    if day.weekday() == 5:
        return day - timedelta(days=1) if move_saturday else None
    if day.weekday() == 6:
        return day + timedelta(days=1)
    return day


@lru_cache(maxsize=64)
def nyse_holidays(year: int) -> dict[date, str]:
    """The regular NYSE holidays in a year: {date: name}."""
    days: dict[date | None, str] = {
        _observed(date(year, 1, 1), move_saturday=False): "New Year's Day",
        _nth_weekday(year, 1, 0, 3): "Martin Luther King Jr. Day",
        _nth_weekday(year, 2, 0, 3): "Washington's Birthday",
        easter_sunday(year) - timedelta(days=2): "Good Friday",
        _nth_weekday(year, 5, 0, -1): "Memorial Day",
        _observed(date(year, 7, 4)): "Independence Day",
        _nth_weekday(year, 9, 0, 1): "Labor Day",
        _nth_weekday(year, 11, 3, 4): "Thanksgiving Day",
        _observed(date(year, 12, 25)): "Christmas Day",
    }
    if year >= 2022:
        days[_observed(date(year, 6, 19))] = "Juneteenth"
    return {d: name for d, name in days.items() if d is not None}


def closed_reason(day: date, extra_closed: Collection[date] = ()) -> str | None:
    """Why the market was closed that day ('a Sunday', 'Thanksgiving Day'...), or None
    if it was a trading day."""
    if day.weekday() >= 5:
        return f"a {WEEKDAY_NAMES[day.weekday()]}"
    holiday = nyse_holidays(day.year).get(day)
    if holiday:
        return f"a market holiday ({holiday})"
    if day in extra_closed:
        return "a day the market was closed"
    return None


def is_trading_day(day: date, extra_closed: Collection[date] = ()) -> bool:
    return closed_reason(day, extra_closed) is None


def last_trading_day(on_or_before: date, extra_closed: Collection[date] = ()) -> date:
    """The latest trading day on or before the given date."""
    day = on_or_before
    while not is_trading_day(day, extra_closed):
        day -= timedelta(days=1)
    return day
