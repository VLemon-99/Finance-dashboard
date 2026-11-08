"""The macro dashboard (SPEC F6): broad market indicators, each with its latest value, the
change since a month earlier, a 1-5 year chart, its source and the date of its latest figure.

Where the numbers come from (config/macro.yaml):
- FRED series (most cards; any series id, F6.3), replaced in full on each refresh because
  FRED revises past values. Inflation cards turn a price index into the % change from a
  year earlier.
- ICI's weekly money market fund total: one figure per week, saved as it comes.
- The equity risk premium: the S&P 500 index fund's P/E (Yahoo, saved once a day) and
  FRED's 10-year Treasury yield, combined in code (macro_math.py).
- The S&P 500 forward P/E estimate: the largest US companies' forward P/Es combined like an
  index (saved once a day), found from Yahoo's sector lists.

Figures are checked again every `refresh_hours` (15 minutes after a failure) in a
background thread, because the forward P/E needs about 65 Yahoo requests: the page shows
what's saved straight away and never waits for a source. A failing source shows its cards
as "stale" or "missing" with the reason; the other cards are unaffected (like F1.6).
"""

import json
import logging
import math
import threading
from collections.abc import Iterable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Literal

from sqlalchemy import Engine
from sqlmodel import Session, col, delete, select

from app.config import YAHOO_SECTORS, MacroConfig, MacroIndicator, MacroUnit, slug
from app.db.models import MacroFetch, MacroObservation
from app.market_calendar import last_trading_day
from app.providers.fred import SERIES_PAGE_URL, FredClient, FredError
from app.providers.ici import IciClient, IciError
from app.providers.market_data import CompanyProfile, MarketData, MarketDataError
from app.services import macro_math as mm
from app.timeutils import EASTERN, as_utc, format_eastern

log = logging.getLogger(__name__)

ICI_KEY = "ici:mmf_total"
FORWARD_PE_KEY = "calc:largest_forward_pe"
YAHOO_SECTORS_URL = "https://finance.yahoo.com/sectors/"
# Daily charts longer than this many points are thinned to one point per week.
MAX_CHART_POINTS = 400
# A chart built from the app's own daily readings says "fills in over time" until it has
# this many points.
FEW_POINTS = 20


class MacroError(Exception):
    """A macro figure couldn't be worked out. The message is shown on its card."""


def fred_key(series_id: str) -> str:
    return f"fred:{series_id}"


def pe_key(fund: str) -> str:
    return f"yahoo:{fund}:trailing_pe"


def input_keys(indicator: MacroIndicator, config: MacroConfig) -> list[str]:
    """The stored series an indicator is made from."""
    if indicator.source == "fred":
        return [fred_key(indicator.series or "")]
    if indicator.source == "ici":
        return [ICI_KEY]
    if indicator.source == "equity_risk_premium":
        erp = config.equity_risk_premium
        return [pe_key(erp.index_fund), fred_key(erp.treasury_series)]
    return [FORWARD_PE_KEY]


def needed_keys(config: MacroConfig) -> list[str]:
    keys: list[str] = []
    for indicator in config.indicators:
        keys += [k for k in input_keys(indicator, config) if k not in keys]
    return keys


def eastern_day(now: datetime) -> date:
    return as_utc(now).astimezone(EASTERN).date()


# --- the S&P 500 forward P/E estimate ---------------------------------------------------


@dataclass(frozen=True)
class ForwardPeEstimate:
    value: float
    largest: tuple[str, ...]  # the companies picked, biggest first
    with_pe: int  # how many of them have a forward P/E (the rest are left out)
    failed: tuple[str, ...]  # companies Yahoo couldn't give figures for
    requests: int  # company requests made

    @property
    def detail(self) -> str:
        text = (
            f"Based on {self.with_pe} of the {len(self.largest)} largest US companies "
            f"(largest: {', '.join(self.largest[:5])})"
        )
        if self.with_pe < len(self.largest):
            text += "; companies without a forward P/E (expected losses) are left out"
        if self.failed:
            text += f"; Yahoo gave no figures for {', '.join(self.failed[:5])}"
        return text + "."


def largest_forward_pe(
    market_data: MarketData,
    count: int,
    max_requests: int,
    parallel: int,
    sector_keys: Iterable[str] = YAHOO_SECTORS,
) -> ForwardPeEstimate:
    """The forward P/E of the `count` largest US companies together (an estimate of the
    S&P 500's). Raises MacroError.

    Yahoo lists each sector's companies biggest first, but gives market values only per
    company, so companies are read sector by sector, always from the sector whose next
    company could be the biggest (it can't be bigger than the last one read there). Reading
    stops once no sector's next company could make the top `count`: about count + 15
    requests instead of every sector's whole list.
    """
    lists: dict[str, list[str]] = {}
    for key in sector_keys:
        try:
            lists[key] = list(market_data.get_group_members("sector", key).symbols)
        except MarketDataError as exc:
            name = YAHOO_SECTORS.get(key, key)
            raise MacroError(f"Yahoo Finance's list for the {name} sector: {exc}") from exc

    position = dict.fromkeys(lists, 0)
    ceiling = dict.fromkeys(lists, math.inf)  # the most the sector's next company is worth
    picked: dict[str, CompanyProfile] = {}  # by company name: GOOG and GOOGL count once
    asked: set[str] = set()
    failed: list[str] = []
    requests = 0

    def next_symbol(key: str) -> str | None:
        symbols = lists[key]
        while position[key] < len(symbols) and symbols[position[key]] in asked:
            position[key] += 1
        return symbols[position[key]] if position[key] < len(symbols) else None

    with ThreadPoolExecutor(max_workers=parallel) as pool:
        while True:
            values = sorted((p.market_cap or 0.0 for p in picked.values()), reverse=True)
            bar = values[count - 1] if len(values) >= count else 0.0
            open_sectors = sorted(
                (k for k in lists if ceiling[k] > bar and next_symbol(k) is not None),
                key=lambda k: ceiling[k],
                reverse=True,
            )[:parallel]
            if not open_sectors:
                break
            batch = []
            for key in open_sectors:
                symbol = next_symbol(key)
                if symbol is not None:
                    asked.add(symbol)
                    batch.append((key, symbol))
            requests += len(batch)
            if requests > max_requests:
                raise MacroError(
                    f"needed more than {max_requests} Yahoo Finance requests; raise "
                    "largest_forward_pe.max_requests in config/macro.yaml if this repeats."
                )
            for (key, symbol), profile in zip(
                batch,
                pool.map(lambda item: _profile_or_none(market_data, item[1]), batch),
                strict=True,
            ):
                if profile is None or profile.market_cap is None:
                    failed.append(symbol)
                    continue
                ceiling[key] = profile.market_cap
                if profile.country not in (None, "United States"):
                    continue  # e.g. TSMC's US shares: not in the S&P 500
                picked.setdefault((profile.name or symbol).strip().lower(), profile)

    largest = sorted(picked.values(), key=lambda p: p.market_cap or 0.0, reverse=True)[:count]
    with_pe = [
        (p.market_cap, p.multiples.forward_pe)
        for p in largest
        if p.market_cap and p.multiples.forward_pe
    ]
    if len(with_pe) < max(5, len(largest) // 2):
        raise MacroError(
            f"Yahoo Finance gave forward P/Es for only {len(with_pe)} of the "
            f"{len(largest)} largest companies."
        )
    return ForwardPeEstimate(
        value=mm.combined_pe(with_pe),
        largest=tuple(p.symbol for p in largest),
        with_pe=len(with_pe),
        failed=tuple(failed),
        requests=requests,
    )


def _profile_or_none(market_data: MarketData, symbol: str) -> CompanyProfile | None:
    try:
        return market_data.get_profile(symbol)
    except MarketDataError:
        return None


# --- refreshing ---------------------------------------------------------------------------


@dataclass
class MacroSetup:
    """What the macro page needs from outside. Tests pass fakes and `background=False`."""

    fred: FredClient
    ici: IciClient
    market_data: MarketData
    config: MacroConfig
    extra_closed_days: frozenset[date] = frozenset()
    parallel: int = 4
    background: bool = True  # False: refresh before the page is shown (tests)
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False)
    _running: bool = field(default=False, init=False)

    @property
    def running(self) -> bool:
        return self._running


def _is_due(fetch: MacroFetch | None, now: datetime, config: MacroConfig) -> bool:
    if fetch is None:
        return True
    wait = (
        timedelta(hours=config.refresh_hours)
        if fetch.ok
        else timedelta(minutes=config.retry_minutes)
    )
    return as_utc(now) - as_utc(fetch.fetched_at) >= wait


def _fetches(session: Session, keys: Sequence[str]) -> dict[str, MacroFetch]:
    if not keys:
        return {}
    rows = session.exec(select(MacroFetch).where(col(MacroFetch.series_key).in_(list(keys))))
    return {row.series_key: row for row in rows}


def due_keys(session: Session, setup: MacroSetup, now: datetime) -> list[str]:
    """The series to check now. FRED's wait until a FRED key is set up."""
    keys = needed_keys(setup.config)
    if not setup.fred.is_set_up:
        keys = [k for k in keys if not k.startswith("fred:")]
    fetches = _fetches(session, keys)
    return [k for k in keys if _is_due(fetches.get(k), now, setup.config)]


def start_refresh(engine: Engine, setup: MacroSetup, now: datetime) -> bool:
    """Start checking what's due (in the background, unless `setup.background` is False).
    Returns True while a refresh is running."""
    with Session(engine) as session:
        keys = due_keys(session, setup, now)
    if not keys:
        return setup.running
    with setup._lock:
        if setup._running:
            return True
        setup._running = True

    def job() -> None:
        try:
            with Session(engine) as session:
                refresh(session, setup, keys, now)
        except Exception:  # a surprise must never take the app down
            log.exception("Macro refresh failed unexpectedly")
        finally:
            setup._running = False

    if not setup.background:
        job()
        return False
    _start_thread(job)
    return True


def _start_thread(job) -> None:
    threading.Thread(target=job, name="macro-refresh", daemon=True).start()


def refresh(session: Session, setup: MacroSetup, keys: Sequence[str], now: datetime) -> None:
    """Fetch each series in `keys`, saving each as soon as it's done."""
    fetches = _fetches(session, keys)
    for key in keys:
        try:
            detail = _refresh_one(session, setup, key, now)
        except (FredError, IciError, MarketDataError, MacroError) as exc:
            session.rollback()
            _record(session, fetches, key, now, error=str(exc))
        else:
            _record(session, fetches, key, now, detail=detail)
        session.commit()


def _record(
    session: Session,
    fetches: dict[str, MacroFetch],
    key: str,
    now: datetime,
    error: str | None = None,
    detail: str | None = None,
) -> None:
    row = fetches.get(key) or MacroFetch(series_key=key, fetched_at=now)
    row.fetched_at, row.ok = now, error is None
    row.message = error[:300] if error else None
    if error is None:  # a failure keeps the last success's detail
        row.detail = detail[:300] if detail else None
    session.add(row)
    fetches[key] = row


def _refresh_one(session: Session, setup: MacroSetup, key: str, now: datetime) -> str | None:
    today = eastern_day(now)
    config = setup.config
    if key.startswith("fred:"):
        series = key.removeprefix("fred:")
        start = mm.months_before(today, 12 * (config.history_years + 1))
        points = setup.fred.series_observations(series, start)
        if not points:
            raise MacroError(f"FRED has no values for {series} since {start:%b %Y}.")
        session.exec(delete(MacroObservation).where(MacroObservation.series_key == key))
        session.add_all(MacroObservation(series_key=key, obs_date=d, value=v) for d, v in points)
        return None
    if key == ICI_KEY:
        week = setup.ici.latest(today)
        _save_reading(session, key, week.week_ended, week.total_billions)
        return None
    trading_day = last_trading_day(today, setup.extra_closed_days)
    if key == FORWARD_PE_KEY:
        cfg = config.largest_forward_pe
        estimate = largest_forward_pe(
            setup.market_data, cfg.companies, cfg.max_requests, setup.parallel
        )
        _save_reading(session, key, trading_day, estimate.value)
        return estimate.detail
    fund = key.split(":")[1]
    pe = setup.market_data.get_profile(fund).multiples.trailing_pe
    if pe is None:
        raise MacroError(f"Yahoo Finance gave no P/E for {fund}.")
    _save_reading(session, key, trading_day, pe)
    return None


def _save_reading(session: Session, key: str, day: date, value: float) -> None:
    """One value per date: a new reading for the same date replaces the old one."""
    row = session.exec(
        select(MacroObservation).where(
            MacroObservation.series_key == key, MacroObservation.obs_date == day
        )
    ).first()
    if row is None:
        row = MacroObservation(series_key=key, obs_date=day, value=value)
    row.value = value
    session.add(row)


# --- what the page shows ------------------------------------------------------------------

Status = Literal["ok", "stale", "missing"]


@dataclass(frozen=True)
class IndicatorView:
    anchor: str
    label: str
    group: str
    note: str | None
    unit: MacroUnit
    decimals: int
    latest: mm.Point | None
    earlier: mm.Point | None  # what the change is measured from (about a month before)
    change: float | None
    change_is_percent: bool
    chart: list[mm.Point]
    sources: list[tuple[str, str | None]]  # (name, link)
    checked_at: datetime | None  # the last successful check
    status: Status
    problems: list[str]
    detail: str | None
    # Made from readings the app saves itself, still short: the chart "fills in" one
    # reading per this ("week" or "trading day"). None otherwise.
    fills_in_per: str | None

    @property
    def value_text(self) -> str:
        return format_value(self.latest[1], self.unit, self.decimals) if self.latest else "—"

    @property
    def change_text(self) -> str | None:
        if self.change is None or self.earlier is None:
            return None
        return format_change(self.change, self.unit, self.decimals, self.change_is_percent)

    @property
    def chart_json(self) -> str:
        """The chart's dates and values for the page's script (plain JSON, no user input)."""
        return json.dumps(
            {
                "labels": [d.isoformat() for d, _ in self.chart],
                "values": [round(v, self.decimals + 2) for _, v in self.chart],
                "unit": self.unit,
                "decimals": self.decimals,
            }
        )


@dataclass(frozen=True)
class GroupView:
    name: str
    indicators: list[IndicatorView]


def format_value(value: float, unit: MacroUnit, decimals: int) -> str:
    """4.25 percent -> '4.25%'; 7420 billions -> '$7.42 trillion'; a P/E 22.4 -> '22.4x'."""
    if unit == "percent":
        return f"{_shown(value, decimals):,.{decimals}f}%"
    if unit == "ratio":
        return f"{_shown(value, decimals):,.{decimals}f}×"
    if unit == "index":
        return f"{_shown(value, decimals):,.{decimals}f}"
    dollars = value * {"usd_millions": 1e6, "usd_billions": 1e9, "usd_trillions": 1e12}[unit]
    sign = "-" if dollars < 0 else ""
    dollars = abs(dollars)
    for size, word in ((1e12, "trillion"), (1e9, "billion"), (1e6, "million")):
        if dollars >= size:
            return f"{sign}${dollars / size:,.2f} {word}"
    return f"{sign}${dollars:,.0f}"


def format_change(change: float, unit: MacroUnit, decimals: int, as_percent: bool) -> str:
    """'+0.15 points' for percentages, '+1.2%' for dollar amounts, '+0.3' otherwise."""
    if as_percent:
        return f"{_signed(change, 1)}%"
    if unit == "percent":
        return f"{_signed(change, decimals)} points"
    return _signed(change, decimals)


def _shown(value: float, decimals: int) -> float:
    return round(value, decimals) + 0.0  # never "-0.00"


def _signed(value: float, decimals: int) -> str:
    shown = _shown(value, decimals)
    return f"{shown:,.{decimals}f}" if shown == 0 else f"{shown:+,.{decimals}f}"


def _load(session: Session, keys: Sequence[str]) -> dict[str, list[mm.Point]]:
    found: dict[str, list[mm.Point]] = {k: [] for k in keys}
    if not keys:
        return found
    rows = session.exec(
        select(MacroObservation.series_key, MacroObservation.obs_date, MacroObservation.value)
        .where(col(MacroObservation.series_key).in_(list(keys)))
        .order_by(col(MacroObservation.obs_date))
    ).all()
    for key, day, value in rows:
        found[key].append((day, value))
    return found


def _series_for(
    indicator: MacroIndicator, config: MacroConfig, data: dict[str, list[mm.Point]]
) -> list[mm.Point]:
    keys = input_keys(indicator, config)
    if indicator.source == "equity_risk_premium":
        return mm.equity_risk_premium(data[keys[0]], data[keys[1]])
    series = data[keys[0]]
    if indicator.show == "yearly_change":
        series = mm.yearly_change(series)
    return series


def _sources(indicator: MacroIndicator, config: MacroConfig) -> list[tuple[str, str | None]]:
    if indicator.source == "fred":
        series = indicator.series or ""
        return [(f"FRED ({series})", SERIES_PAGE_URL.format(series_id=series))]
    if indicator.source == "ici":
        return [("ICI weekly report", config.ici.page_url)]
    if indicator.source == "equity_risk_premium":
        erp = config.equity_risk_premium
        return [
            (
                f"Yahoo Finance ({erp.index_fund} P/E)",
                f"https://finance.yahoo.com/quote/{erp.index_fund}",
            ),
            (
                f"FRED ({erp.treasury_series})",
                SERIES_PAGE_URL.format(series_id=erp.treasury_series),
            ),
        ]
    return [("Yahoo Finance (company figures)", YAHOO_SECTORS_URL)]


def indicator_views(session: Session, setup: MacroSetup, years: int) -> list[GroupView]:
    """Every card, grouped by section in the order of config/macro.yaml."""
    config = setup.config
    keys = needed_keys(config)
    data = _load(session, keys)
    fetches = _fetches(session, keys)
    groups: dict[str, list[IndicatorView]] = {}
    for indicator in config.indicators:
        inputs = input_keys(indicator, config)
        series = _series_for(indicator, config, data)
        latest = series[-1] if series else None
        earlier = mm.month_earlier(series)
        as_percent = indicator.display_unit.startswith("usd_")
        change = mm.change(latest[1], earlier[1], as_percent) if latest and earlier else None
        chart = mm.within_years(series, years)
        if len(chart) > MAX_CHART_POINTS:
            chart = mm.thin_to_weekly(chart)

        problems = []
        for key in inputs:
            fetch = fetches.get(key)
            if key.startswith("fred:") and not setup.fred.is_set_up:
                problems.append(
                    "FRED isn't set up: add FRED_API_KEY to the .env file (free; see the README)."
                )
            elif fetch is not None and not fetch.ok:
                problems.append(f"{fetch.message} (last tried {format_eastern(fetch.fetched_at)})")
        problems = list(dict.fromkeys(problems))
        status: Status = "missing" if latest is None else ("stale" if problems else "ok")
        successes = [
            as_utc(f.fetched_at) for k in inputs if (f := fetches.get(k)) is not None and f.ok
        ]
        details = [f.detail for k in inputs if (f := fetches.get(k)) is not None and f.detail]
        groups.setdefault(indicator.group, []).append(
            IndicatorView(
                anchor=slug(indicator.label),
                label=indicator.label,
                group=indicator.group,
                note=indicator.note,
                unit=indicator.display_unit,
                decimals=indicator.decimals,
                latest=latest,
                earlier=earlier,
                change=change,
                change_is_percent=as_percent,
                chart=chart,
                sources=_sources(indicator, config),
                checked_at=min(successes) if successes else None,
                status=status,
                problems=problems,
                detail=details[0] if details else None,
                fills_in_per=(
                    None
                    if indicator.source == "fred" or len(series) >= FEW_POINTS
                    else ("week" if indicator.source == "ici" else "trading day")
                ),
            )
        )
    return [GroupView(name, cards) for name, cards in groups.items()]


def run_note(running: bool, first_time: bool) -> str | None:
    if not running:
        return None
    if first_time:
        return (
            "Fetching the figures for the first time (about a minute). This page reloads by itself."
        )
    return "Checking for newer figures in the background. This page reloads by itself."


def never_fetched(session: Session, setup: MacroSetup) -> bool:
    keys = needed_keys(setup.config)
    return not _fetches(session, keys)
