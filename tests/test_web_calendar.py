"""The calendar page end to end (SPEC F5.1-F5.6). Fake Yahoo, fake FRED, a fixed clock."""

import re
from datetime import UTC, date, datetime

import pytest
from sqlmodel import Session, select

from app.db.models import Instrument, Transaction
from app.providers.market_data import EarningsData
from tests.test_web_watchlists import add, create

# Wed Nov 4, 2026, 10:00 AM US Eastern.
NOW = datetime(2026, 11, 4, 15, 0, tzinfo=UTC)


class Clock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


@pytest.fixture
def clock(monkeypatch):
    import app.web.calendar_routes
    import app.web.routes

    clock = Clock(NOW)
    for module in (app.web.calendar_routes, app.web.routes):
        monkeypatch.setattr(module, "utc_now", clock)
    return clock


@pytest.fixture
def setup(client, engine, fake_market_data, clock):
    """NVDA in a watchlist; a TSLA put in the portfolio. WMT is in neither."""
    fake_market_data.earnings = [
        EarningsData(
            "NVDA", date(2026, 11, 10), datetime(2026, 11, 10, 21, 0, tzinfo=UTC), "after_close"
        ),
        EarningsData(
            "TSLA", date(2026, 11, 12), datetime(2026, 11, 12, 12, 0, tzinfo=UTC), "before_open"
        ),
        EarningsData(
            "WMT", date(2026, 11, 9), datetime(2026, 11, 9, 12, 0, tzinfo=UTC), "before_open"
        ),
    ]
    add(client, create(client, "Tech"), "NVDA")
    with Session(engine) as session:
        put = Instrument(
            symbol="TSLA261218P00300000",
            kind="option",
            underlying="TSLA",
            option_type="put",
            strike=300.0,
            expiration=date(2026, 12, 18),
        )
        session.add(put)
        session.commit()
        session.add(
            Transaction(
                user_id=1,
                instrument_id=put.id,
                side="sold",
                quantity=1,
                price=12.5,
                trade_date=date(2026, 10, 1),
                price_source="manual",
            )
        )
        session.commit()
    return client


def titles(html: str) -> list[str]:
    return [
        re.sub(r"<[^>]+>", "", t).strip()
        for t in re.findall(r'<span class="event-title">(.*?)</span>', html)
    ]


def day_headings(html: str) -> list[str]:
    return [
        re.sub(r"<[^>]+>|today", "", h).strip()
        for h in re.findall(r'<h2 class="day-heading">(.*?)</h2>', html)
    ]


def test_calendar_is_in_the_menu(client):
    assert '<a href="/calendar">Calendar</a>' in client.get("/").text


def test_f5_3_list_shows_the_next_14_days_in_order(setup, fake_market_data):
    html = setup.get("/calendar").text
    assert "Next 14 days" in html
    # Nov 4 to Nov 17: Fed minutes (Nov 18), PCE and GDP (Nov 25) are later.
    assert titles(html) == [
        "Jobs report (employment situation)",
        "NVDA earnings",
        "TSLA earnings",
        "CPI (consumer prices)",
    ]
    assert day_headings(html) == ["Fri, Nov 6, 2026", "Tue, Nov 10, 2026", "Thu, Nov 12, 2026"]
    # F5.1: my tickers = watchlist + portfolio, fetched in one request; WMT isn't mine.
    assert fake_market_data.earnings_calls == [["NVDA", "TSLA"]]
    assert "WMT" not in titles(html)
    assert "My tickers: NVDA, TSLA." in html


def test_f5_5_each_event_has_date_time_and_source(setup):
    html = setup.get("/calendar").text
    nvda = html.split('<a href="/lookup?ticker=NVDA">NVDA earnings</a>')[0].rsplit("<li", 1)[1]
    assert '<span class="event-time">After the close</span>' in nvda
    tsla = html.split("TSLA earnings")[0].rsplit("<li", 1)[1]
    assert "Before the open" in tsla
    cpi = html.split("CPI (consumer prices)")[1].split("</li>")[0]
    assert '<span class="impact high">high</span>' in cpi
    assert (
        'href="https://fred.stlouisfed.org/release?rid=10"' in cpi
        and "FRED release calendar" in cpi
    )
    jobs = html.split("Jobs report (employment situation)")[0].rsplit("<li", 1)[1]
    assert "8:30 AM ET" in jobs
    assert "Times are US Eastern." in html


def test_f5_5_past_events_drop_off_the_list(setup, clock):
    clock.now = datetime(2026, 11, 6, 14, 0, tzinfo=UTC)  # Fri 9:00 AM ET: jobs report out
    assert "Jobs report (employment situation)" not in titles(setup.get("/calendar").text)


def test_f5_4_my_tickers_only_and_high_impact_only(setup):
    mine = setup.get("/calendar?mine=1").text
    assert titles(mine) == ["NVDA earnings", "TSLA earnings"]
    assert 'name="mine" value="1" checked' in mine
    week = "/calendar?view=week&week=2026-11-23"
    assert {"PCE (personal income and outlays)", "GDP"} <= set(titles(setup.get(week).text))
    high = setup.get(week + "&high=1").text
    assert titles(high) == []  # PCE and GDP are medium impact
    assert "Nothing scheduled that matches the filters this week." in high


def test_f5_3_week_view(setup):
    html = setup.get("/calendar?view=week&week=2026-11-25").text  # any day -> its Monday
    assert "Week of Nov 23, 2026" in html
    assert day_headings(html)[0] == "Mon, Nov 23, 2026" and len(day_headings(html)) == 7
    assert "Nothing scheduled." in html  # empty days are listed too
    assert 'href="/calendar?view=week&amp;week=2026-11-16"' in html  # previous week
    assert 'href="/calendar?view=week&amp;week=2026-11-30"' in html  # next week
    assert 'href="/calendar?view=week&amp;week=2026-11-02"' in html  # back to this week
    # Filters are kept when moving between weeks.
    assert (
        'week=2026-11-30&amp;mine=1"'
        in setup.get("/calendar?view=week&week=2026-11-23&mine=1").text
    )


def test_this_week_shows_past_days_greyed(setup):
    html = setup.get("/calendar?view=week").text
    assert "Week of Nov 2, 2026" in html
    assert '<section class="calendar-day past">' in html  # Mon and Tue
    assert '<section class="calendar-day today">' in html


@pytest.mark.parametrize("week", ["nonsense", "2031-01-01"])
def test_bad_week_is_refused_politely(setup, week):
    response = setup.get(f"/calendar?view=week&week={week}")
    assert response.status_code == 400
    assert "showing this week" in response.text
    assert "Week of Nov 2, 2026" in response.text


def test_sources_down_or_not_set_up_still_show_the_page(setup, fake_market_data, fake_fred):
    fake_market_data.earnings_down = True
    fake_fred.is_set_up = False
    response = setup.get("/calendar")
    assert response.status_code == 200
    assert "Earnings dates for NVDA, TSLA: Yahoo Finance did not respond properly" in response.text
    assert "set FRED_API_KEY in the .env file" in response.text
    assert titles(response.text) == []  # Fed minutes are outside the 14 days


def test_fed_meetings_running_out_is_shown(setup, clock):
    clock.now = datetime(2026, 12, 10, 15, 0, tzinfo=UTC)
    html = setup.get("/calendar").text
    assert "no future meeting is listed in config/fomc_dates.yaml" in html
    later = setup.get("/calendar?view=week&week=2026-12-28").text
    assert "Fed meeting minutes (from the Dec 9 meeting)" in titles(later)  # three weeks on


def test_no_tickers_yet(client, clock):
    html = client.get("/calendar").text
    assert "My tickers: none yet" in html
    with Session(client.app.state.engine) as session:
        assert session.exec(select(Instrument)).all() == []


# --- home page (F5.6) --------------------------------------------------------------------


def home_events(html: str) -> list[str]:
    section = html.split('aria-labelledby="coming-up"')[1].split("</section>")[0]
    return titles(section)


def test_f5_6_home_shows_the_next_3_high_impact_events(setup):
    html = setup.get("/").text
    assert "Coming up: high-impact events" in html
    # Jobs (Nov 6), NVDA (Nov 10), TSLA (Nov 12, before the open); CPI the same day is 4th.
    assert home_events(html) == [
        "Jobs report (employment situation)",
        "NVDA earnings",
        "TSLA earnings",
    ]
    assert "Fri, Nov 6, 2026" in html and '<a href="/calendar">Full calendar</a>' in html


def test_home_skips_medium_impact_and_past_events(setup, clock, fake_market_data):
    fake_market_data.earnings = []
    clock.now = datetime(2026, 11, 20, 15, 0, tzinfo=UTC)  # after Nov's CPI and minutes
    # No earnings scheduled; PCE and GDP (Nov 25) are medium impact, so they're skipped.
    assert home_events(setup.get("/").text) == [
        "Jobs report (employment situation)",  # Dec 4
        "Fed rate decision (FOMC statement)",  # Dec 9
        "CPI (consumer prices)",  # Dec 10
    ]


def test_home_still_loads_when_sources_fail(setup, fake_market_data, fake_fred):
    fake_market_data.earnings_down = True
    fake_fred.down = True
    response = setup.get("/")
    assert response.status_code == 200
    assert "some dates may be missing" in response.text
    assert "Watchlists" in response.text


def test_home_with_nothing_yet(client, clock, fake_fred):
    fake_fred.is_set_up = False
    html = client.get("/").text
    # Only the Fed meetings file is left: the Dec 9 decision is high impact.
    assert home_events(html) == ["Fed rate decision (FOMC statement)"]
