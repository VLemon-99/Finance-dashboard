"""The macro dashboard's data (SPEC F6): FRED series, ICI's weekly page, the forward P/E
estimate, the settings file, and refreshing. All offline (hand-made samples and fakes)."""

from dataclasses import replace
from datetime import date, datetime, timedelta

import httpx
import pytest
from sqlmodel import select

from app.config import PROJECT_ROOT, ConfigFileError, MacroConfig, load_macro_config
from app.db.models import MacroFetch, MacroObservation
from app.providers.fred import FredClient, FredError, observations_from_fred
from app.providers.ici import IciClient, IciError, weekly_assets_from_page
from app.services import macro
from tests.conftest import (
    FIXTURES,
    MACRO_COMPANIES,
    MACRO_NOW,
    MACRO_SECTORS,
    SOURCE_FIXTURES,
    FakeFetch,
    FakeMacroMarket,
    company,
)

INVENTED_KEY_FOR_TESTS = "abcdefabcdefabcdefabcdefabcdefab"


# --- FRED series ---------------------------------------------------------------------------


def fred_with(handler) -> FredClient:
    return FredClient(INVENTED_KEY_FOR_TESTS, transport=httpx.MockTransport(handler))


def test_fred_series_observations_skip_days_without_a_value():
    seen = []

    def handler(request):
        seen.append(request.url)
        return httpx.Response(200, content=(FIXTURES / "fred_series_T10Y2Y.json").read_bytes())

    points = fred_with(handler).series_observations("T10Y2Y", date(2026, 10, 28))
    assert points[0] == (date(2026, 10, 28), 0.48)
    assert points[-1] == (date(2026, 11, 5), 0.52)
    assert date(2026, 11, 4) not in [d for d, _ in points]  # "." = no value that day
    assert len(points) == 6
    params = seen[0].params
    assert seen[0].path == "/fred/series/observations"
    assert params["series_id"] == "T10Y2Y"
    assert params["observation_start"] == "2026-10-28"
    assert params["file_type"] == "json"
    assert params["api_key"] == INVENTED_KEY_FOR_TESTS


def test_fred_unknown_series_names_it_and_points_to_the_settings():
    def handler(request):
        return httpx.Response(
            400,
            json={"error_code": 400, "error_message": "Bad Request.  The series does not exist."},
        )

    with pytest.raises(FredError) as caught:
        fred_with(handler).series_observations("NOPE123", date(2026, 1, 1))
    assert "NOPE123" in str(caught.value)
    assert "config/macro.yaml" in str(caught.value)
    assert INVENTED_KEY_FOR_TESTS not in str(caught.value)


def test_fred_series_without_a_key():
    with pytest.raises(FredError, match="FRED_API_KEY"):
        FredClient("").series_observations("T10Y2Y", date(2026, 1, 1))


def test_observations_from_fred_rejects_a_surprise():
    with pytest.raises(FredError, match="unexpected"):
        observations_from_fred(b'{"seriess": []}')


# --- ICI's weekly page ---------------------------------------------------------------------

ICI_SAMPLE = (SOURCE_FIXTURES / "ici_mmf_page.html").read_bytes()


def test_ici_weekly_total_from_the_page():
    week = weekly_assets_from_page(ICI_SAMPLE, date(2026, 11, 6))
    assert week.week_ended == date(2026, 11, 5)  # the latest of the two sentences
    assert week.total_billions == pytest.approx(7420.0)
    assert week.change_billions == pytest.approx(33.62)


def test_ici_decrease_and_a_december_week_read_in_january():
    page = (
        b"<p>Total money market fund assets decreased by $1.5 trillion to $6.90 trillion "
        b"for the eight-day period ended Wednesday, December 30, the Investment Company "
        b"Institute reported today.</p>"
    )
    week = weekly_assets_from_page(page, date(2027, 1, 2))
    assert week.week_ended == date(2026, 12, 30)
    assert week.total_billions == pytest.approx(6900.0)
    assert week.change_billions == pytest.approx(-1500.0)


def test_ici_page_changed_or_down():
    with pytest.raises(IciError, match="has changed"):
        weekly_assets_from_page(b"<p>Nothing to see</p>", date(2026, 11, 6))
    fetch = FakeFetch({})
    fetch.down.add("https://www.ici.org/")
    with pytest.raises(IciError, match="Couldn't read ICI's page"):
        IciClient(fetch, "https://www.ici.org/research/stats/mmf").latest(date(2026, 11, 6))


# --- the S&P 500 forward P/E estimate -----------------------------------------------------


def test_forward_pe_of_the_largest_us_companies():
    market = FakeMacroMarket(MACRO_SECTORS, MACRO_COMPANIES)
    estimate = macro.largest_forward_pe(market, count=10, max_requests=200, parallel=1)
    # Known answer (see MACRO_COMPANIES): 14,450 / 579.
    assert estimate.value == pytest.approx(14450 / 579)
    assert round(estimate.value, 2) == 24.96
    assert estimate.largest == (
        "NVDA",
        "GOOGL",
        "META",
        "MSFT",
        "BRK-B",
        "LLY",
        "JPM",
        "V",
        "ORCL",
        "JNJ",
    )
    assert estimate.with_pe == 9  # BRK-B has no forward P/E
    assert "TSM" not in estimate.largest  # based outside the US
    assert estimate.failed == ("BROKEN",)
    assert "Based on 9 of the 10 largest US companies" in estimate.detail
    assert "BROKEN" in estimate.detail


def test_forward_pe_stops_reading_once_no_smaller_company_can_make_the_top():
    market = FakeMacroMarket(MACRO_SECTORS, MACRO_COMPANIES)
    estimate = macro.largest_forward_pe(market, count=10, max_requests=200, parallel=1)
    # GOOG is skipped as a repeat of GOOGL; the small companies deep in each list are
    # never asked for.
    for never in ("T2", "T3", "C2", "H1"):
        assert never not in market.calls
    assert estimate.requests == len(market.calls) < 20


def test_forward_pe_problems():
    market = FakeMacroMarket(MACRO_SECTORS, MACRO_COMPANIES)
    market.down = True
    with pytest.raises(macro.MacroError, match="sector"):
        macro.largest_forward_pe(market, count=10, max_requests=200, parallel=1)
    market = FakeMacroMarket(MACRO_SECTORS, MACRO_COMPANIES)
    with pytest.raises(macro.MacroError, match="max_requests"):
        macro.largest_forward_pe(market, count=10, max_requests=5, parallel=1)
    no_pe = FakeMacroMarket(
        {"technology": ["A", "B", "C", "D", "E", "F"]},
        [company(s, 100 - i, None) for i, s in enumerate("ABCDEF")],
    )
    with pytest.raises(macro.MacroError, match="forward P/Es for only 0"):
        macro.largest_forward_pe(no_pe, count=10, max_requests=200, parallel=1)


# --- config/macro.yaml ---------------------------------------------------------------------


def test_the_macro_settings_file_has_the_agreed_indicators():
    cfg = load_macro_config(PROJECT_ROOT / "config" / "macro.yaml")
    by_series = {i.series: i for i in cfg.indicators if i.source == "fred"}
    # SPEC F6.1, plus VIX and consumer sentiment (owner 2026-11-08) and the extras.
    for series in ("T10Y2Y", "T10Y3M", "VIXCLS", "UMCSENT", "BAMLH0A0HYM2", "UNRATE", "DFF"):
        assert series in by_series
    for series in ("CPIAUCSL", "CPILFESL", "PCEPILFE"):
        assert by_series[series].show == "yearly_change"
    sources = {i.source for i in cfg.indicators}
    assert {"ici", "equity_risk_premium", "largest_forward_pe"} <= sources
    assert cfg.history_years == 5
    assert all(i.label and i.group for i in cfg.indicators)


def test_macro_settings_problems_are_reported(tmp_path):
    path = tmp_path / "macro.yaml"
    path.write_text("indicators:\n  - label: Unemployment\n")  # no series id
    with pytest.raises(ConfigFileError, match="needs its series id"):
        load_macro_config(path)
    path.write_text("indicators:\n  - label: X\n    series: 'BAD ID!'\n")
    with pytest.raises(ConfigFileError, match="isn't a FRED series id"):
        load_macro_config(path)
    path.write_text("indicators:\n  - {label: A, series: X1}\n  - {label: a, series: X2}\n")
    with pytest.raises(ConfigFileError, match="two indicators"):
        load_macro_config(path)


def test_series_ids_are_tidied():
    cfg = MacroConfig.model_validate({"indicators": [{"label": "U", "series": " unrate "}]})
    assert cfg.indicators[0].series == "UNRATE"


# --- refreshing ----------------------------------------------------------------------------


def stored(session, key):
    return session.exec(
        select(MacroObservation.obs_date, MacroObservation.value)
        .where(MacroObservation.series_key == key)
        .order_by(MacroObservation.obs_date)
    ).all()


def test_refresh_saves_every_source(engine, session, macro_setup):
    assert macro.start_refresh(engine, macro_setup, MACRO_NOW) is False  # ran straight away
    assert stored(session, "fred:T10Y2Y")[-1] == (date(2026, 11, 5), 0.55)
    # Six years kept (five for the chart plus one for yearly changes).
    assert stored(session, "fred:T10Y2Y")[0][0] >= date(2020, 11, 6)
    assert stored(session, "ici:mmf_total") == [(date(2026, 11, 5), 7420.0)]
    assert stored(session, "yahoo:SPY:trailing_pe") == [(date(2026, 11, 6), 25.0)]
    [(day, value)] = stored(session, "calc:largest_forward_pe")
    assert day == date(2026, 11, 6) and value == pytest.approx(14450 / 579)
    fetches = {f.series_key: f for f in session.exec(select(MacroFetch))}
    assert all(f.ok for f in fetches.values())
    assert "Based on 9 of the 10" in fetches["calc:largest_forward_pe"].detail


def test_refresh_waits_until_due(engine, macro_setup, fake_fred, macro_market):
    macro.start_refresh(engine, macro_setup, MACRO_NOW)
    calls = (len(fake_fred.series_calls), len(macro_market.calls))
    macro.start_refresh(engine, macro_setup, MACRO_NOW + timedelta(hours=11))
    assert (len(fake_fred.series_calls), len(macro_market.calls)) == calls
    macro.start_refresh(engine, macro_setup, MACRO_NOW + timedelta(hours=12))
    assert len(fake_fred.series_calls) > calls[0]


def test_a_failed_source_keeps_its_figures_and_is_retried_after_15_minutes(
    engine, session, macro_setup, fake_fred
):
    macro.start_refresh(engine, macro_setup, MACRO_NOW)
    fake_fred.down = True
    later = MACRO_NOW + timedelta(hours=12)
    macro.start_refresh(engine, macro_setup, later)
    session.expire_all()
    assert stored(session, "fred:T10Y2Y")[-1] == (date(2026, 11, 5), 0.55)  # kept
    fetch = session.exec(select(MacroFetch).where(MacroFetch.series_key == "fred:T10Y2Y")).one()
    assert not fetch.ok and "didn't answer" in fetch.message
    calls = len(fake_fred.series_calls)
    macro.start_refresh(engine, macro_setup, later + timedelta(minutes=14))
    assert len(fake_fred.series_calls) == calls
    macro.start_refresh(engine, macro_setup, later + timedelta(minutes=15))
    assert len(fake_fred.series_calls) > calls


def test_fred_revisions_replace_the_old_values(engine, session, macro_setup, fake_fred):
    macro.start_refresh(engine, macro_setup, MACRO_NOW)
    fake_fred.series["T10Y2Y"][-1] = (date(2026, 11, 5), 0.60)
    macro.start_refresh(engine, macro_setup, MACRO_NOW + timedelta(hours=12))
    session.expire_all()
    points = stored(session, "fred:T10Y2Y")
    assert points[-1] == (date(2026, 11, 5), 0.60)
    assert len(points) == len({d for d, _ in points})


def test_daily_readings_are_dated_on_the_trading_day(engine, session, macro_setup, macro_market):
    macro.start_refresh(engine, macro_setup, MACRO_NOW)  # Friday
    spy = macro_market.profiles["SPY"]
    macro_market.profiles["SPY"] = replace(spy, multiples=replace(spy.multiples, trailing_pe=26.0))
    saturday = datetime(2026, 11, 7, 15, 0, tzinfo=MACRO_NOW.tzinfo)
    macro.start_refresh(engine, macro_setup, saturday)
    session.expire_all()
    # Saturday's reading is Friday's figure: it replaces Friday's, no Saturday point.
    assert stored(session, "yahoo:SPY:trailing_pe") == [(date(2026, 11, 6), 26.0)]


def test_without_a_fred_key_other_sources_still_refresh(engine, session, macro_setup, fake_fred):
    fake_fred.is_set_up = False
    macro.start_refresh(engine, macro_setup, MACRO_NOW)
    assert fake_fred.series_calls == []
    assert stored(session, "fred:T10Y2Y") == []
    assert stored(session, "ici:mmf_total") == [(date(2026, 11, 5), 7420.0)]


def test_an_unexpected_crash_never_escapes(engine, macro_setup, macro_market, caplog):
    def crash(symbol):
        raise RuntimeError("surprise")

    macro_market.get_profile = crash
    assert macro.start_refresh(engine, macro_setup, MACRO_NOW) is False
    assert "Macro refresh failed unexpectedly" in caplog.text
    assert not macro_setup.running


def test_only_one_background_refresh_at_a_time(engine, macro_setup, monkeypatch):
    started = []
    monkeypatch.setattr(macro, "_start_thread", started.append)  # held, not run yet
    macro_setup.background = True
    assert macro.start_refresh(engine, macro_setup, MACRO_NOW) is True
    assert macro_setup.running
    assert macro.start_refresh(engine, macro_setup, MACRO_NOW) is True
    assert len(started) == 1
    started[0]()  # the job runs and finishes
    assert not macro_setup.running
