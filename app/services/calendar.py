"""The market events calendar (SPEC F5): earnings of my tickers and the main US economic
events, in one list.

Where events come from:
- Earnings (F5.1): Yahoo's earnings calendar, for "my tickers" = every stock in my
  watchlists (and behind their option contracts) plus what I hold in the portfolio (owner
  2026-11-04). If Yahoo has no calendar entry, the next earnings date saved with the
  watchlist prices is used instead (date only).
- CPI, jobs report, PCE, GDP (F5.2): FRED's release calendar (dates), with the usual release
  time from config/settings.yaml.
- Fed rate decisions and minutes (F5.2): config/fomc_dates.yaml (not stored in the database).

Fetched dates are stored (`calendar_events`) and checked again once a day, or 15 minutes
after a failure. A failing source never breaks the page: what's missing or out of date is
listed instead (like SPEC F1.6, F4.11).
"""

import logging
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Literal

from sqlmodel import Session, col, delete, select

from app.config import AppConfig, CalendarConfig, FomcConfig, MacroRelease
from app.db.models import CalendarEvent, CalendarFetch, Instrument, Quote
from app.providers.fred import RELEASE_PAGE_URL, FredClient, FredError
from app.providers.market_data import MarketData, MarketDataError
from app.services import portfolio
from app.services import watchlists as wl
from app.timeutils import EASTERN, as_utc, format_eastern

log = logging.getLogger(__name__)

FED_CALENDAR_URL = "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm"
YAHOO_SOURCE = "Yahoo Finance"
FRED_SOURCE = "FRED release calendar"
FED_SOURCE = "Federal Reserve schedule"
# Earnings are also kept a week back, so the week view still shows this week's past days.
PAST_DAYS_KEPT = 7
# FRED dates are fetched this far ahead.
MACRO_DAYS_AHEAD = 400

EventKind = Literal["earnings", "macro", "fomc"]


@dataclass(frozen=True)
class EventView:
    kind: EventKind
    title: str
    day: date  # US Eastern
    starts_at: datetime | None  # UTC; None when only the day is known
    impact: str  # "high" | "medium"
    source: str
    source_url: str | None = None
    ticker: str | None = None
    timing: str | None = None  # earnings: before_open | after_close | at_time | unknown

    @property
    def time_label(self) -> str:
        """US Eastern time, or 'Before the open' / 'After the close' for earnings (F5.1)."""
        if self.timing == "before_open":
            return "Before the open"
        if self.timing == "after_close":
            return "After the close"
        if self.starts_at is None:
            return "Time not announced"
        local = as_utc(self.starts_at).astimezone(EASTERN)
        return f"{local.hour % 12 or 12}:{local:%M %p} ET"

    def is_past(self, now: datetime) -> bool:
        if self.starts_at is not None:
            return as_utc(self.starts_at) < as_utc(now)
        return self.day < eastern_day(now)

    @property
    def sort_key(self) -> tuple:
        # Within a day: timed events in order, then those whose time isn't known.
        at = as_utc(self.starts_at) if self.starts_at else None
        return (self.day, at is None, at or datetime.min.replace(tzinfo=UTC), self.title)


@dataclass(frozen=True)
class DayGroup:
    day: date
    events: list[EventView]


def eastern_day(now: datetime) -> date:
    return as_utc(now).astimezone(EASTERN).date()


def at_eastern(day: date, clock: time) -> datetime:
    """A US Eastern day and clock time, as UTC (handles daylight saving)."""
    return datetime.combine(day, clock, tzinfo=EASTERN).astimezone(UTC)


def my_tickers(session: Session, user_id: int) -> list[str]:
    """Watchlist stocks (and option underlyings) plus portfolio holdings, A-Z."""
    return sorted(wl.all_tickers(session, user_id) | portfolio.held_tickers(session, user_id))


# --- refreshing ------------------------------------------------------------------------


def _is_due(fetch: CalendarFetch | None, now: datetime, config: CalendarConfig) -> bool:
    if fetch is None:
        return True
    wait = (
        timedelta(hours=config.refresh_hours)
        if fetch.ok
        else timedelta(minutes=config.retry_minutes)
    )
    return as_utc(now) - as_utc(fetch.fetched_at) >= wait


def _fetches(session: Session, keys: Iterable[str]) -> dict[str, CalendarFetch]:
    keys = list(keys)
    if not keys:
        return {}
    rows = session.exec(select(CalendarFetch).where(col(CalendarFetch.source_key).in_(keys)))
    return {row.source_key: row for row in rows}


def _record(
    session: Session,
    fetches: dict[str, CalendarFetch],
    key: str,
    now: datetime,
    error: str | None = None,
) -> None:
    row = fetches.get(key) or CalendarFetch(source_key=key, fetched_at=now)
    row.fetched_at, row.ok, row.message = now, error is None, (error or None)
    if row.message:
        row.message = row.message[:300]
    session.add(row)
    fetches[key] = row


def _replace(session: Session, key: str, events: list[CalendarEvent]) -> None:
    session.exec(delete(CalendarEvent).where(CalendarEvent.source_key == key))
    session.add_all(events)


def refresh_if_due(
    session: Session,
    market_data: MarketData,
    fred: FredClient,
    tickers: Sequence[str],
    config: CalendarConfig,
    now: datetime,
) -> None:
    """Fetch what's due: earnings for my tickers (one request), each FRED release."""
    today = eastern_day(now)
    start = today - timedelta(days=PAST_DAYS_KEPT)
    keys = [f"earnings:{t}" for t in tickers] + [
        f"fred:{r.fred_release_id}" for r in config.macro_releases
    ]
    fetches = _fetches(session, keys)

    due = [t for t in tickers if _is_due(fetches.get(f"earnings:{t}"), now, config)]
    if due:
        end = today + timedelta(days=config.earnings_days_ahead)
        try:
            found = market_data.get_earnings(due, start, end)
        except MarketDataError as exc:
            for ticker in due:
                _record(session, fetches, f"earnings:{ticker}", now, str(exc))
        else:
            for ticker in due:
                key = f"earnings:{ticker}"
                events = [
                    CalendarEvent(
                        kind="earnings",
                        source_key=key,
                        ticker=ticker,
                        title=f"{ticker} earnings",
                        event_date=e.day,
                        starts_at=e.starts_at,
                        timing=e.timing,
                        source=YAHOO_SOURCE,
                        source_url=f"https://finance.yahoo.com/calendar/earnings?symbol={ticker}",
                        fetched_at=now,
                    )
                    for e in found
                    if e.symbol == ticker
                ]
                _replace(session, key, events)
                _record(session, fetches, key, now)

    if fred.is_set_up:
        for release in config.macro_releases:
            key = f"fred:{release.fred_release_id}"
            if not _is_due(fetches.get(key), now, config):
                continue
            try:
                days = fred.release_dates(
                    release.fred_release_id, start, today + timedelta(days=MACRO_DAYS_AHEAD)
                )
            except FredError as exc:
                _record(session, fetches, key, now, str(exc))
                continue
            events = [
                CalendarEvent(
                    kind="macro",
                    source_key=key,
                    title=release.name,
                    event_date=day,
                    starts_at=at_eastern(day, release.time),
                    source=FRED_SOURCE,
                    source_url=RELEASE_PAGE_URL.format(release_id=release.fred_release_id),
                    fetched_at=now,
                )
                for day in days
            ]
            _replace(session, key, events)
            _record(session, fetches, key, now)
    session.commit()


# --- reading ---------------------------------------------------------------------------


def load_events(
    session: Session,
    tickers: Sequence[str],
    start: date,
    end: date,
    config: CalendarConfig,
    fomc: FomcConfig,
) -> list[EventView]:
    """All events from `start` to `end` (US Eastern days, both included), sorted."""
    releases = {f"fred:{r.fred_release_id}": r for r in config.macro_releases}
    events: list[EventView] = []
    stored = session.exec(
        select(CalendarEvent).where(
            CalendarEvent.event_date >= start, CalendarEvent.event_date <= end
        )
    ).all()
    with_calendar_entry = set()
    for row in stored:
        if row.kind == "earnings":
            if row.ticker not in tickers:
                continue  # no longer one of my tickers
            with_calendar_entry.add(row.ticker)
            events.append(_earnings_view(row, config))
        elif row.source_key in releases:  # a release taken out of the settings is hidden
            events.append(_macro_view(row, releases[row.source_key]))
    events += _saved_earnings_dates(session, set(tickers) - with_calendar_entry, start, end, config)
    events += [e for e in fomc_events(fomc, config) if start <= e.day <= end]
    return sorted(events, key=lambda e: e.sort_key)


def _earnings_view(row: CalendarEvent, config: CalendarConfig) -> EventView:
    return EventView(
        kind="earnings",
        title=row.title,
        day=row.event_date,
        starts_at=as_utc(row.starts_at) if row.starts_at else None,
        impact=config.earnings_impact,
        source=row.source,
        source_url=row.source_url,
        ticker=row.ticker,
        timing=row.timing,
    )


def _macro_view(row: CalendarEvent, release: MacroRelease) -> EventView:
    # Name, time and impact from the settings as they are now (edits apply straight away).
    return EventView(
        kind="macro",
        title=release.name,
        day=row.event_date,
        starts_at=at_eastern(row.event_date, release.time),
        impact=release.impact,
        source=row.source,
        source_url=row.source_url,
    )


def _saved_earnings_dates(
    session: Session, tickers: set[str], start: date, end: date, config: CalendarConfig
) -> list[EventView]:
    """The next earnings date saved with the watchlist prices, for tickers Yahoo's earnings
    calendar didn't list (or couldn't be reached for). Date only."""
    if not tickers:
        return []
    rows = session.exec(
        select(Instrument.symbol, Quote.next_earnings)
        .join(Quote, col(Quote.instrument_id) == col(Instrument.id))
        .where(col(Instrument.symbol).in_(sorted(tickers)), col(Quote.next_earnings).is_not(None))
    ).all()
    return [
        EventView(
            kind="earnings",
            title=f"{symbol} earnings",
            day=day,
            starts_at=None,
            impact=config.earnings_impact,
            source=f"{YAHOO_SOURCE} (date only)",
            source_url=f"https://finance.yahoo.com/quote/{symbol}",
            ticker=symbol,
            timing="unknown",
        )
        for symbol, day in rows
        if start <= day <= end
    ]


def fomc_events(fomc: FomcConfig, config: CalendarConfig) -> list[EventView]:
    events = []
    for meeting in fomc.meetings:
        minutes_day = meeting.minutes or meeting.decision + timedelta(weeks=3)
        decided = f"{meeting.decision:%b} {meeting.decision.day}"
        events += [
            EventView(
                kind="fomc",
                title="Fed rate decision (FOMC statement)",
                day=meeting.decision,
                starts_at=at_eastern(meeting.decision, fomc.decision_time),
                impact=config.fomc_decision_impact,
                source=FED_SOURCE,
                source_url=FED_CALENDAR_URL,
            ),
            EventView(
                kind="fomc",
                title=f"Fed meeting minutes (from the {decided} meeting)",
                day=minutes_day,
                starts_at=at_eastern(minutes_day, fomc.minutes_time),
                impact=config.fomc_minutes_impact,
                source=FED_SOURCE,
                source_url=FED_CALENDAR_URL,
            ),
        ]
    return events


def filtered(
    events: Iterable[EventView], tickers: Sequence[str], mine: bool, high: bool
) -> list[EventView]:
    """F5.4: "my tickers only" keeps events about my tickers (so economic events are
    hidden); "high impact only" keeps high-impact events."""
    return [
        e
        for e in events
        if (not mine or (e.ticker is not None and e.ticker in tickers))
        and (not high or e.impact == "high")
    ]


def by_day(events: Iterable[EventView], days: Sequence[date] | None = None) -> list[DayGroup]:
    """Events grouped per day. With `days`, every one of those days is listed, even empty."""
    groups: dict[date, list[EventView]] = {d: [] for d in days or []}
    for event in events:
        groups.setdefault(event.day, []).append(event)
    return [DayGroup(day, groups[day]) for day in sorted(groups)]


def week_start(day: date) -> date:
    """The Monday of that day's week."""
    return day - timedelta(days=day.weekday())


def upcoming(events: Iterable[EventView], now: datetime) -> list[EventView]:
    """Events not yet past (F5.5: past events drop off the default view)."""
    return [e for e in events if not e.is_past(now)]


# --- what's missing or out of date --------------------------------------------------------


def notes(
    session: Session,
    tickers: Sequence[str],
    config: CalendarConfig,
    fomc: FomcConfig,
    fred: FredClient,
    now: datetime,
) -> list[str]:
    """Plain notes for the page: sources that are down, not set up, or have run out."""
    found: list[str] = []
    fetches = _fetches(
        session,
        [f"earnings:{t}" for t in tickers]
        + [f"fred:{r.fred_release_id}" for r in config.macro_releases],
    )
    failed = [t for t in tickers if (f := fetches.get(f"earnings:{t}")) and not f.ok]
    if failed:
        message = fetches[f"earnings:{failed[0]}"].message
        found.append(
            f"Earnings dates for {', '.join(failed)}: {message} Showing the dates saved "
            "earlier, if any."
        )
    if not fred.is_set_up:
        names = ", ".join(r.name for r in config.macro_releases)
        found.append(
            f"Economic releases ({names}) are missing: set FRED_API_KEY in the .env file "
            "(free; see the README)."
        )
    else:
        for release in config.macro_releases:
            fetch = fetches.get(f"fred:{release.fred_release_id}")
            if fetch and not fetch.ok:
                found.append(
                    f"{release.name}: {fetch.message} Showing the dates saved earlier, if any "
                    f"(last tried {format_eastern(fetch.fetched_at)})."
                )
    today = eastern_day(now)
    if not any(m.decision >= today for m in fomc.meetings):
        found.append(
            "Fed meetings: no future meeting is listed in config/fomc_dates.yaml. Add the next "
            f"dates from the Fed's calendar page ({FED_CALENDAR_URL}) and restart the app."
        )
    return found


@dataclass(frozen=True)
class CalendarSetup:
    """What the calendar needs from outside. Tests pass fakes."""

    market_data: MarketData
    fred: FredClient
    fomc: FomcConfig
    config: AppConfig


def prepare(
    session: Session, setup: CalendarSetup, user_id: int, now: datetime
) -> tuple[list[str], list[str]]:
    """Refresh what's due. Returns (my tickers, notes). Never raises for a source problem."""
    tickers = my_tickers(session, user_id)
    try:
        refresh_if_due(session, setup.market_data, setup.fred, tickers, setup.config.calendar, now)
    except Exception:  # a surprise in one source must not break the page
        session.rollback()
        log.exception("Calendar refresh failed unexpectedly")
    found = notes(session, tickers, setup.config.calendar, setup.fomc, setup.fred, now)
    return tickers, found


def next_high_impact(
    session: Session, setup: CalendarSetup, user_id: int, now: datetime
) -> tuple[list[EventView], list[str]]:
    """The next few high-impact events for the home page (F5.6), plus the notes."""
    tickers, found = prepare(session, setup, user_id, now)
    cfg = setup.config.calendar
    today = eastern_day(now)
    horizon = today + timedelta(days=max(cfg.earnings_days_ahead, 120))
    events = load_events(session, tickers, today, horizon, cfg, setup.fomc)
    high = [e for e in upcoming(events, now) if e.impact == "high"]
    return high[: cfg.home_events], found
