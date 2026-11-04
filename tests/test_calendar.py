"""The market events calendar (SPEC F5.1, F5.2, F5.4, F5.5): adapters and the calendar
service. Hand-made samples, fake Yahoo and fake FRED; never the network."""

import json
from datetime import UTC, date, datetime, time, timedelta

import httpx
import pytest
import yfinance as yf
from sqlmodel import select

from app.config import AppConfig, CalendarConfig, FomcConfig
from app.db.models import CalendarEvent, Instrument, Quote, Transaction, Watchlist, WatchlistItem
from app.providers import yahoo
from app.providers.fred import FredClient, FredError, dates_from_fred
from app.providers.market_data import EarningsData, MarketDataError
from app.services import calendar as cal
from app.services.users import ensure_default_user
from tests.conftest import FIXTURES, FakeFred

# Wed Nov 4, 2026, 10:00 AM US Eastern (standard time, UTC-5).
NOW = datetime(2026, 11, 4, 15, 0, tzinfo=UTC)
TODAY = date(2026, 11, 4)
CONFIG = CalendarConfig()


# --- Yahoo's earnings calendar ----------------------------------------------------------


def sample_table():
    """The sample run through yfinance's own table-building, as a real answer would be."""
    raw = json.loads((FIXTURES / "yahoo_earnings_calendar.json").read_text())
    calendars = yf.Calendars(start="2026-11-01", end="2027-02-01")
    calendars.calendars["sp_earnings"] = calendars._create_df(raw)
    return calendars._cleanup_df("sp_earnings")


def test_earnings_dates_times_and_timing_from_yahoo():
    found = yahoo.earnings_from_calendar(sample_table(), {"NVDA", "WMT", "SOFI", "ZM"})
    by_symbol = {e.symbol: e for e in found}
    assert set(by_symbol) == {"NVDA", "WMT", "SOFI", "ZM"}  # ZZZZ wasn't asked for
    assert by_symbol["NVDA"] == EarningsData(
        "NVDA", date(2026, 11, 18), datetime(2026, 11, 18, 21, 0, tzinfo=UTC), "after_close"
    )
    assert by_symbol["WMT"].timing == "before_open"
    assert by_symbol["ZM"].timing == "at_time"
    assert by_symbol["ZM"].starts_at == datetime(2026, 11, 23, 22, 0, tzinfo=UTC)
    # "Time not supplied": the date only (05:00 UTC is midnight in New York).
    assert by_symbol["SOFI"] == EarningsData("SOFI", date(2027, 1, 26), None, "unknown")


def test_earnings_query_asks_for_our_tickers_only():
    one = yahoo.earnings_query(["AAPL"], date(2026, 11, 1), date(2027, 2, 1)).to_dict()
    assert one["operands"][-1] == {"operator": "EQ", "operands": ["ticker", "AAPL"]}
    many = yahoo.earnings_query(["AAPL", "MSFT"], date(2026, 11, 1), date(2027, 2, 1)).to_dict()
    assert many["operands"][-1]["operator"] == "OR"
    assert {"operator": "GTE", "operands": ["startdatetime", "2026-11-01"]} in many["operands"]


def test_earnings_requests_in_batches_and_failures(monkeypatch):
    queries = []

    def fake_get_data(self, calendar_type, query, limit=12, offset=0, force=False):
        queries.append(query.to_dict())
        return sample_table()

    monkeypatch.setattr(yf.Calendars, "_get_data", fake_get_data)
    symbols = ["NVDA"] + [f"T{n:03d}" for n in range(29)]  # 30 tickers -> 2 requests
    found = yahoo.YahooMarketData().get_earnings(symbols, date(2026, 11, 1), date(2027, 2, 1))
    assert len(queries) == 2
    assert [e.symbol for e in found] == ["NVDA"]

    def broken(self, *args, **kwargs):
        raise ConnectionError("down")

    monkeypatch.setattr(yf.Calendars, "_get_data", broken)
    with pytest.raises(MarketDataError, match="did not respond properly"):
        yahoo.YahooMarketData().get_earnings(["NVDA"], date(2026, 11, 1), date(2027, 2, 1))


# --- FRED ---------------------------------------------------------------------------------

INVENTED_KEY_FOR_TESTS = "test-only-not-a-real-fred-key"


def fred_with(handler) -> FredClient:
    return FredClient(INVENTED_KEY_FOR_TESTS, transport=httpx.MockTransport(handler))


def test_fred_release_dates_including_scheduled_ones():
    seen = []

    def handler(request):
        seen.append(request.url)
        return httpx.Response(200, content=(FIXTURES / "fred_release_dates_cpi.json").read_bytes())

    days = fred_with(handler).release_dates(10, date(2026, 10, 28), date(2026, 12, 31))
    assert days == [date(2026, 11, 12), date(2026, 12, 10)]  # Oct 15 and Jan 13 outside
    params = seen[0].params
    assert params["release_id"] == "10"
    assert params["include_release_dates_with_no_data"] == "true"  # future dates too
    assert params["api_key"] == INVENTED_KEY_FOR_TESTS and params["file_type"] == "json"


@pytest.mark.parametrize(
    ("response", "message"),
    [
        (httpx.Response(400, text='{"error_message": "api_key is not registered"}'), "key"),
        (httpx.Response(429, text="slow down"), "too often"),
        (httpx.Response(500, text="oops"), r"error \(500\)"),
        (httpx.Response(200, text="not json"), "unexpected"),
    ],
)
def test_fred_errors_are_plain_and_never_show_the_key(response, message):
    with pytest.raises(FredError, match=message) as raised:
        fred_with(lambda request: response).release_dates(10, TODAY, TODAY)
    assert INVENTED_KEY_FOR_TESTS not in str(raised.value)


def test_fred_timeout_and_no_key():
    def timeout(request):
        raise httpx.ConnectTimeout("slow", request=request)

    with pytest.raises(FredError, match="in time"):
        fred_with(timeout).release_dates(10, TODAY, TODAY)
    for key in ("", "  ", "your-fred-key-here"):
        client = FredClient(key)
        assert not client.is_set_up
        with pytest.raises(FredError, match="FRED_API_KEY"):
            client.release_dates(10, TODAY, TODAY)


def test_dates_from_fred():
    body = b'{"release_dates": [{"release_id": 50, "date": "2026-12-04"}, {"date": "2026-11-06"}]}'
    assert dates_from_fred(body) == [date(2026, 11, 6), date(2026, 12, 4)]


# --- my tickers ---------------------------------------------------------------------------


@pytest.fixture
def user_id(session):
    return ensure_default_user(session, "owner@localhost").id


def instrument(session, symbol, **option) -> Instrument:
    row = session.exec(select(Instrument).where(Instrument.symbol == symbol)).first()
    if row is None:
        row = Instrument(symbol=symbol, **option)
        session.add(row)
        session.commit()
    return row


def watch(session, user_id, *symbols, list_name="Tech"):
    watchlist = session.exec(select(Watchlist).where(Watchlist.name == list_name)).first()
    if watchlist is None:
        watchlist = Watchlist(user_id=user_id, name=list_name)
        session.add(watchlist)
        session.commit()
    for symbol in symbols:
        item = instrument(session, symbol)
        session.add(
            WatchlistItem(user_id=user_id, watchlist_id=watchlist.id, instrument_id=item.id)
        )
    session.commit()


def trade(session, user_id, symbol, side="bought", quantity=10, **option):
    item = instrument(session, symbol, **option)
    session.add(
        Transaction(
            user_id=user_id,
            instrument_id=item.id,
            side=side,
            quantity=quantity,
            price=10.0,
            trade_date=date(2026, 10, 1),
            price_source="manual",
        )
    )
    session.commit()


def test_my_tickers_are_watchlists_and_portfolio_holdings(session, user_id):
    watch(session, user_id, "AAPL", "MSFT")
    session.add(
        WatchlistItem(
            user_id=user_id,
            watchlist_id=1,
            instrument_id=instrument(
                session,
                "AMZN261120C00250000",
                kind="option",
                underlying="AMZN",
                option_type="call",
                strike=250.0,
                expiration=date(2026, 11, 20),
            ).id,
        )
    )
    trade(session, user_id, "NBIS")  # held
    trade(session, user_id, "SOFI")  # bought and sold again: not held
    trade(session, user_id, "SOFI", side="sold")
    trade(
        session,
        user_id,
        "TSLA261218P00300000",
        side="sold",
        quantity=1,
        kind="option",
        underlying="TSLA",
        option_type="put",
        strike=300.0,
        expiration=date(2026, 12, 18),
    )
    assert cal.my_tickers(session, user_id) == ["AAPL", "AMZN", "MSFT", "NBIS", "TSLA"]


# --- refreshing ---------------------------------------------------------------------------

EARNINGS = [
    EarningsData(
        "NVDA", date(2026, 11, 18), datetime(2026, 11, 18, 21, 0, tzinfo=UTC), "after_close"
    ),
    EarningsData(
        "WMT", date(2026, 11, 19), datetime(2026, 11, 19, 12, 0, tzinfo=UTC), "before_open"
    ),
]


@pytest.fixture
def market(fake_market_data):
    fake_market_data.earnings = list(EARNINGS)
    return fake_market_data


@pytest.fixture
def fred(fake_fred):
    return fake_fred


def events(
    session, tickers=("NVDA", "WMT"), start=TODAY, end=TODAY + timedelta(days=60), fomc=None
):
    return cal.load_events(session, list(tickers), start, end, CONFIG, fomc or FomcConfig())


def test_refresh_fetches_earnings_in_one_request_and_each_release(session, market, fred):
    cal.refresh_if_due(session, market, fred, ["NVDA", "WMT"], CONFIG, NOW)
    assert market.earnings_calls == [["NVDA", "WMT"]]
    assert sorted(fred.calls) == [10, 50, 53, 54]
    found = events(session)
    nvda = next(e for e in found if e.ticker == "NVDA")
    assert (nvda.day, nvda.time_label, nvda.impact, nvda.source) == (
        date(2026, 11, 18),
        "After the close",
        "high",
        "Yahoo Finance",
    )
    cpi = next(e for e in found if e.title == "CPI (consumer prices)")
    # 8:30 AM Eastern on Nov 12 (standard time) is 13:30 UTC.
    assert cpi.starts_at == datetime(2026, 11, 12, 13, 30, tzinfo=UTC)
    assert cpi.time_label == "8:30 AM ET"
    assert cpi.impact == "high" and cpi.source == "FRED release calendar"
    assert cpi.source_url == "https://fred.stlouisfed.org/release?rid=10"
    gdp = next(e for e in found if e.title == "GDP")
    assert gdp.impact == "medium"


def test_refresh_once_a_day_and_again_soon_after_a_failure(session, market, fred):
    cal.refresh_if_due(session, market, fred, ["NVDA"], CONFIG, NOW)
    cal.refresh_if_due(session, market, fred, ["NVDA"], CONFIG, NOW + timedelta(hours=23))
    assert len(market.earnings_calls) == 1 and len(fred.calls) == 4
    # A new ticker is fetched straight away, on its own.
    cal.refresh_if_due(session, market, fred, ["NVDA", "WMT"], CONFIG, NOW + timedelta(hours=23))
    assert market.earnings_calls[-1] == ["WMT"]

    later = NOW + timedelta(hours=24)
    market.earnings_down = True
    fred.down = True
    cal.refresh_if_due(session, market, fred, ["NVDA", "WMT"], CONFIG, later)
    # The saved dates stay (shown with a note), and it's tried again after 15 minutes.
    assert {e.ticker for e in events(session) if e.kind == "earnings"} == {"NVDA", "WMT"}
    assert any(e.title == "CPI (consumer prices)" for e in events(session))
    notes = cal.notes(
        session,
        ["NVDA", "WMT"],
        CONFIG,
        FomcConfig.model_validate({"meetings": [{"decision": "2026-12-09"}]}),
        fred,
        later,
    )
    # WMT was checked an hour earlier, so only NVDA was due (and failed).
    assert notes[0].startswith("Earnings dates for NVDA: Yahoo Finance did not respond")
    assert any(n.startswith("CPI (consumer prices): FRED didn't answer in time.") for n in notes)
    calls = len(market.earnings_calls)
    cal.refresh_if_due(
        session, market, fred, ["NVDA", "WMT"], CONFIG, later + timedelta(minutes=10)
    )
    assert len(market.earnings_calls) == calls
    market.earnings_down = fred.down = False
    cal.refresh_if_due(
        session, market, fred, ["NVDA", "WMT"], CONFIG, later + timedelta(minutes=15)
    )
    assert len(market.earnings_calls) == calls + 1
    assert not cal.notes(
        session,
        ["NVDA", "WMT"],
        CONFIG,
        FomcConfig.model_validate({"meetings": [{"decision": "2026-12-09"}]}),
        fred,
        later,
    )


def test_moved_dates_replace_the_old_ones(session, market, fred):
    cal.refresh_if_due(session, market, fred, ["NVDA"], CONFIG, NOW)
    market.earnings = [
        EarningsData(
            "NVDA", date(2026, 11, 20), datetime(2026, 11, 20, 21, 0, tzinfo=UTC), "after_close"
        )
    ]
    cal.refresh_if_due(session, market, fred, ["NVDA"], CONFIG, NOW + timedelta(days=1))
    rows = session.exec(select(CalendarEvent).where(CalendarEvent.ticker == "NVDA")).all()
    assert [r.event_date for r in rows] == [date(2026, 11, 20)]


def test_without_a_fred_key_releases_are_missing_with_a_note(session, market):
    no_key = FakeFred()
    no_key.is_set_up = False
    cal.refresh_if_due(session, market, no_key, ["NVDA"], CONFIG, NOW)
    assert no_key.calls == []
    notes = cal.notes(session, ["NVDA"], CONFIG, FomcConfig(), no_key, NOW)
    assert any("set FRED_API_KEY in the .env file" in n for n in notes)


def test_saved_watchlist_earnings_date_is_used_when_yahoo_has_no_entry(session, market, fred):
    aapl = instrument(session, "AAPL")
    session.add(
        Quote(
            instrument_id=aapl.id,
            last_price=200.0,
            previous_close=199.0,
            currency="USD",
            fetched_at=NOW,
            source="yahoo",
            next_earnings=date(2027, 1, 28),
        )
    )
    session.commit()
    cal.refresh_if_due(session, market, fred, ["AAPL", "NVDA"], CONFIG, NOW)
    found = events(session, tickers=["AAPL", "NVDA"], end=date(2027, 2, 28))
    aapl_event = next(e for e in found if e.ticker == "AAPL")
    assert aapl_event.day == date(2027, 1, 28)
    assert aapl_event.time_label == "Time not announced"
    assert aapl_event.source == "Yahoo Finance (date only)"


def test_earnings_of_tickers_no_longer_mine_are_hidden(session, market, fred):
    cal.refresh_if_due(session, market, fred, ["NVDA", "WMT"], CONFIG, NOW)
    assert {e.ticker for e in events(session, tickers=["NVDA"]) if e.ticker} == {"NVDA"}


# --- Fed meetings, filters, past events -------------------------------------------------


def test_fed_decisions_and_minutes():
    fomc = FomcConfig.model_validate(
        {
            "meetings": [
                {"decision": "2026-10-28", "minutes": "2026-11-18"},
                {"decision": "2026-12-09"},
            ]
        }
    )
    found = cal.fomc_events(fomc, CONFIG)
    decision = next(e for e in found if e.day == date(2026, 12, 9))
    assert decision.title == "Fed rate decision (FOMC statement)"
    assert decision.starts_at == datetime(2026, 12, 9, 19, 0, tzinfo=UTC)  # 2:00 PM ET
    assert decision.impact == "high"
    assert decision.source_url.startswith("https://www.federalreserve.gov/")
    minutes = {e.day: e for e in found if "minutes" in e.title}
    assert minutes[date(2026, 11, 18)].title == "Fed meeting minutes (from the Oct 28 meeting)"
    assert minutes[date(2026, 12, 30)].impact == "medium"  # three weeks after Dec 9


def test_fed_dates_running_out_is_flagged(session):
    fomc = FomcConfig.model_validate({"meetings": [{"decision": "2026-10-28"}]})
    notes = cal.notes(session, [], CONFIG, fomc, FakeFred(), NOW)
    assert notes == [
        "Fed meetings: no future meeting is listed in config/fomc_dates.yaml. Add the next "
        "dates from the Fed's calendar page "
        "(https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm) and restart the app."
    ]


def test_filters_my_tickers_and_high_impact(session, market, fred):
    cal.refresh_if_due(session, market, fred, ["NVDA", "WMT"], CONFIG, NOW)
    fomc = FomcConfig.model_validate({"meetings": [{"decision": "2026-12-09"}]})
    found = events(session, fomc=fomc)
    mine = cal.filtered(found, ["NVDA", "WMT"], mine=True, high=False)
    assert {e.kind for e in mine} == {"earnings"}  # economic events hidden
    high = cal.filtered(found, ["NVDA", "WMT"], mine=False, high=True)
    assert {e.title for e in high} == {
        "NVDA earnings",
        "WMT earnings",
        "CPI (consumer prices)",
        "Jobs report (employment situation)",
        "Fed rate decision (FOMC statement)",
    }
    both = cal.filtered(found, ["NVDA"], mine=True, high=True)
    assert [e.title for e in both] == ["NVDA earnings"]


def test_events_are_sorted_and_past_ones_drop_off():
    def event(title, day, at=None, timing=None):
        return cal.EventView("macro", title, day, at, "high", "test", timing=timing)

    jobs = event("Jobs", date(2026, 11, 6), datetime(2026, 11, 6, 13, 30, tzinfo=UTC))
    early = event("Earlier today", TODAY, datetime(2026, 11, 4, 13, 30, tzinfo=UTC))
    today_no_time = event("No time", TODAY)
    yesterday = event("Yesterday", TODAY - timedelta(days=1))
    later_today = event("Later today", TODAY, datetime(2026, 11, 4, 19, 0, tzinfo=UTC))
    ordered = sorted([jobs, today_no_time, later_today, early, yesterday], key=lambda e: e.sort_key)
    assert [e.title for e in ordered] == [
        "Yesterday",
        "Earlier today",
        "Later today",
        "No time",
        "Jobs",
    ]
    assert [e.title for e in cal.upcoming(ordered, NOW)] == ["Later today", "No time", "Jobs"]


def test_time_labels():
    at = datetime(2026, 11, 23, 22, 0, tzinfo=UTC)
    assert cal.EventView("earnings", "x", TODAY, at, "high", "s", timing="at_time").time_label == (
        "5:00 PM ET"
    )
    assert cal.EventView(
        "earnings", "x", TODAY, at, "high", "s", timing="before_open"
    ).time_label == ("Before the open")
    assert cal.EventView(
        "earnings", "x", TODAY, None, "high", "s", timing="unknown"
    ).time_label == ("Time not announced")


def test_daylight_saving_is_handled():
    # 8:30 AM Eastern is 12:30 UTC in summer (daylight time) and 13:30 UTC in winter.
    assert cal.at_eastern(date(2026, 10, 30), time(8, 30)) == datetime(
        2026, 10, 30, 12, 30, tzinfo=UTC
    )
    assert cal.at_eastern(date(2026, 11, 2), time(8, 30)) == datetime(
        2026, 11, 2, 13, 30, tzinfo=UTC
    )


def test_week_start_and_days():
    assert cal.week_start(date(2026, 11, 8)) == date(2026, 11, 2)  # Sunday -> its Monday
    assert cal.week_start(date(2026, 11, 2)) == date(2026, 11, 2)
    groups = cal.by_day([], [date(2026, 11, 2), date(2026, 11, 3)])
    assert [(g.day, g.events) for g in groups] == [(date(2026, 11, 2), []), (date(2026, 11, 3), [])]


def test_settings_have_the_owners_impact_levels():
    config = AppConfig().calendar
    assert config.list_days == 14
    assert (config.earnings_impact, config.fomc_decision_impact, config.fomc_minutes_impact) == (
        "high",
        "high",
        "medium",
    )
    impacts = {r.fred_release_id: r.impact for r in config.macro_releases}
    assert impacts == {10: "high", 50: "high", 54: "medium", 53: "medium"}


def test_saved_date_is_not_shown_next_to_a_calendar_entry_in_another_week(session, market, fred):
    """Seen in the layout check (2026-11-04): AMZN's calendar entry was on Nov 12, and the
    week of Nov 16 still showed the older date saved with the watchlist price (Nov 19)."""
    nvda = instrument(session, "NVDA")
    session.add(
        Quote(
            instrument_id=nvda.id,
            last_price=180.0,
            previous_close=179.0,
            currency="USD",
            fetched_at=NOW,
            source="yahoo",
            next_earnings=date(2026, 11, 25),  # out of date: Yahoo's calendar says Nov 18
        )
    )
    session.commit()
    cal.refresh_if_due(session, market, fred, ["NVDA"], CONFIG, NOW)
    week = events(session, tickers=["NVDA"], start=date(2026, 11, 23), end=date(2026, 11, 29))
    assert [e for e in week if e.kind == "earnings"] == []
