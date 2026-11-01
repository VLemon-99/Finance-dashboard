"""Time helpers. Rule: store times in UTC, show them in US Eastern time."""

from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

EASTERN = ZoneInfo("America/New_York")


def utc_now() -> datetime:
    """Current time in UTC, as a timezone-aware datetime."""
    return datetime.now(UTC)


def as_utc(value: datetime) -> datetime:
    """SQLite gives back times without a timezone; they were saved as UTC."""
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def format_eastern(value: datetime) -> str:
    """Show a stored time in US Eastern, e.g. 'Nov 3, 2026 10:15 AM ET'."""
    local = as_utc(value).astimezone(EASTERN)
    hour = local.hour % 12 or 12
    return f"{local:%b} {local.day}, {local.year} {hour}:{local:%M %p} ET"


def today_eastern() -> date:
    """Today's date in US Eastern time (the market's calendar day)."""
    return utc_now().astimezone(EASTERN).date()
