"""How numbers and dates look on the pages. Used as Jinja filters (see routes.py)."""

from datetime import date

DASH = "—"  # shown when a value isn't available


def _shown(value: float, decimals: int) -> float:
    """The value as it will be shown. Tiny amounts that round to zero become exactly 0, so
    they never show as "-0.00" (seen on the owner's portfolio, 2026-11-04)."""
    return round(value, decimals) + 0.0  # adding 0.0 turns -0.0 into 0.0


def money(value: float | None) -> str:
    return DASH if value is None else f"{_shown(value, 2):,.2f}"


def signed(value: float | None, decimals: int = 2) -> str:
    """'+1.50', '-0.25'; zero has no sign: '0.00'."""
    if value is None:
        return DASH
    shown = _shown(value, decimals)
    return f"{shown:,.{decimals}f}" if shown == 0 else f"{shown:+,.{decimals}f}"


def trend(value: float | None, decimals: int = 2) -> str:
    """CSS class for a change as shown: 'up', 'down' or 'flat' (so -0.001 isn't red)."""
    if value is None:
        return "flat"
    shown = _shown(value, decimals)
    return "up" if shown > 0 else ("down" if shown < 0 else "flat")


def number(value: float | None, decimals: int = 2) -> str:
    """Plain number with a fixed number of decimals, e.g. greeks: 0.637, -0.018.
    Tiny negatives that round to zero show as 0.000, not -0.000."""
    return DASH if value is None else f"{_shown(value, decimals):,.{decimals}f}"


def whole(value: int | None) -> str:
    """15230 -> '15,230'."""
    return DASH if value is None else f"{value:,}"


def percent(fraction: float | None, decimals: int = 1) -> str:
    """0.2834 -> '28.3%'."""
    return DASH if fraction is None else f"{_shown(fraction * 100, decimals):.{decimals}f}%"


def strike(value: float) -> str:
    """260.0 -> '260', 262.5 -> '262.50'."""
    return f"{value:,.0f}" if value == int(value) else f"{value:,.2f}"


def contract_label(underlying: str, expiration: date, strike_price: float, option_type: str) -> str:
    """'AAPL Nov 20, 2026 260 call'."""
    return f"{underlying} {short_date(expiration)} {strike(strike_price)} {option_type}"


def instrument_label(item) -> str:
    """A stock's ticker, or an option contract as 'AAPL Nov 20, 2026 260 call'. Takes anything
    with kind/symbol (and underlying/expiration/strike/option_type for options)."""
    if getattr(item, "kind", "stock") == "option":
        return contract_label(item.underlying, item.expiration, item.strike, item.option_type)
    return item.symbol


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
