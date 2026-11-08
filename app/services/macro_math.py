"""The macro dashboard's formulas (SPEC F6.1). Pure functions, each tested with known
answers in tests/test_macro_math.py. A series is a list of (date, value), oldest first.
"""

import bisect
import calendar
from collections.abc import Sequence
from datetime import date

Point = tuple[date, float]
Series = Sequence[Point]

# A value a year (or a month) earlier counts only if it's at most this many days before the
# wanted date, so a gap in the data never compares with something far older.
MATCH_TOLERANCE_DAYS = 10


def months_before(day: date, months: int) -> date:
    """The same day `months` calendar months earlier (Mar 31 minus 1 month -> Feb 28/29)."""
    month_index = day.year * 12 + (day.month - 1) - months
    year, month = divmod(month_index, 12)
    month += 1
    return date(year, month, min(day.day, calendar.monthrange(year, month)[1]))


def value_on_or_before(series: Series, day: date) -> Point | None:
    """The last point on or before `day`, or None."""
    dates = [d for d, _ in series]
    index = bisect.bisect_right(dates, day)
    return series[index - 1] if index else None


def _near_before(series: Series, day: date, tolerance_days: int) -> Point | None:
    found = value_on_or_before(series, day)
    if found is None or (day - found[0]).days > tolerance_days:
        return None
    return found


def yearly_change(series: Series, tolerance_days: int = MATCH_TOLERANCE_DAYS) -> list[Point]:
    """% change from a year earlier, for each point that has one (how inflation is quoted:
    CPI 300.0 a year ago and 309.0 now -> 3.0)."""
    changes = []
    for day, value in series:
        earlier = _near_before(series, months_before(day, 12), tolerance_days)
        if earlier is None or earlier[1] <= 0:
            continue
        changes.append((day, (value / earlier[1] - 1) * 100))
    return changes


def month_earlier(series: Series) -> Point | None:
    """The point to compare the latest value with: the last one on or before the same day a
    month earlier. For a quarterly series, that's the previous quarter (shown with its
    date, so it's never mistaken for a month)."""
    if len(series) < 2:
        return None
    return value_on_or_before(series[:-1], months_before(series[-1][0], 1))


def change(new: float, old: float, as_percent: bool) -> float | None:
    """`new - old`, or the % change for amounts (dollars) where a % reads better."""
    if not as_percent:
        return new - old
    if old == 0:
        return None
    return (new / old - 1) * 100


def earnings_yield(pe: float) -> float:
    """Earnings yield in percent: 1 / P/E. P/E 25 -> 4.0 (%)."""
    if pe <= 0:
        raise ValueError("a P/E must be positive")
    return 100 / pe


def equity_risk_premium(
    pe_readings: Series, treasury: Series, tolerance_days: int = 7
) -> list[Point]:
    """S&P 500 earnings yield minus the 10-year Treasury yield, in percentage points, for each
    day with a P/E reading and a Treasury yield on or up to a week before it.
    P/E 25 (yield 4.00%) and a 4.25% Treasury -> -0.25."""
    premiums = []
    for day, pe in pe_readings:
        if pe <= 0:
            continue
        rate = _near_before(treasury, day, tolerance_days)
        if rate is None:
            continue
        premiums.append((day, earnings_yield(pe) - rate[1]))
    return premiums


def combined_pe(companies: Sequence[tuple[float, float]]) -> float:
    """The P/E of several companies together, like an index: total market value divided by
    total earnings, where each company's earnings are its market value / its P/E.
    (3,000 at 30x and 1,000 at 10x -> earnings 100 + 100 -> 4,000 / 200 = 20.0.)"""
    total_value = sum(cap for cap, _ in companies)
    total_earnings = sum(cap / pe for cap, pe in companies)
    if total_value <= 0 or total_earnings <= 0:
        raise ValueError("needs companies with positive value and earnings")
    return total_value / total_earnings


def within_years(series: Series, years: int) -> list[Point]:
    """The points in the `years` before the latest one (the chart's period)."""
    if not series:
        return []
    start = months_before(series[-1][0], 12 * years)
    return [p for p in series if p[0] >= start]


def thin_to_weekly(series: Series) -> list[Point]:
    """One point per week (the week's last), to keep long daily charts light."""
    kept: dict[tuple[int, int], Point] = {}
    for point in series:
        year, week, _ = point[0].isocalendar()
        kept[(year, week)] = point
    return sorted(kept.values())
