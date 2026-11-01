"""How numbers and dates look on the pages. Used as Jinja filters (see routes.py)."""

from datetime import date

DASH = "—"  # shown when a value isn't available


def money(value: float | None) -> str:
    return DASH if value is None else f"{value:,.2f}"


def signed(value: float | None, decimals: int = 2) -> str:
    return DASH if value is None else f"{value:+,.{decimals}f}"


def compact(value: float | None) -> str:
    """36_540_100 -> '36.54M', 3.85e12 -> '3.85T'."""
    if value is None:
        return DASH
    for size, suffix in ((1e12, "T"), (1e9, "B"), (1e6, "M"), (1e3, "K")):
        if abs(value) >= size:
            return f"{value / size:.2f}{suffix}"
    return f"{value:,.0f}"


def short_date(value: date | None) -> str:
    """'Nov 19, 2026'."""
    return DASH if value is None else f"{value:%b} {value.day}, {value.year}"


def session_label(session_date: date | None, today: date) -> str:
    """What the day change refers to: 'today', or the last trading day's date."""
    if session_date is None:
        return "on the last trading day"
    if session_date == today:
        return "today"
    return f"on {session_date:%a}, {session_date:%b} {session_date.day}"


def price_range(low: float | None, high: float | None) -> str:
    if low is None or high is None:
        return DASH
    return f"{money(low)} – {money(high)}"
