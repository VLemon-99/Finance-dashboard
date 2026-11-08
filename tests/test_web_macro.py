"""The macro page end to end (SPEC F6.1-F6.3). Fake FRED, fake ICI page, fake Yahoo and a
fixed clock (Fri Nov 6, 2026, 10 AM ET); the test indicators are in conftest.TEST_MACRO."""

import html
import json
import re
from datetime import timedelta

import pytest

from app.config import MacroConfig
from app.services import macro
from tests.conftest import MACRO_NOW, TEST_MACRO, Clock


@pytest.fixture
def clock(monkeypatch):
    import app.web.macro_routes

    clock = Clock(MACRO_NOW)
    monkeypatch.setattr(app.web.macro_routes, "utc_now", clock)
    return clock


def card(page: str, anchor: str) -> str:
    match = re.search(
        rf'<article class="macro-card[^"]*" id="{anchor}">(.*?)</article>', page, re.S
    )
    assert match, f"no card {anchor}"
    return match.group(0)


def chart(card_html: str) -> dict:
    match = re.search(r'data-chart="([^"]*)"', card_html)
    assert match, "no chart"
    return json.loads(html.unescape(match.group(1)))


def test_menu_links_to_the_macro_page(client):
    assert '<a href="/macro">Macro</a>' in client.get("/").text


def test_f6_1_latest_value_change_since_last_month_and_chart(client, clock):
    page = client.get("/macro").text
    curve = card(page, "yield-curve-10-year-minus-2-year")
    assert "0.55%" in curve
    assert "+0.15 points" in curve
    assert "Latest figure: Nov 5, 2026 · change since Oct 5, 2026" in curve
    data = chart(curve)
    assert data["labels"][-1] == "2026-11-05" and data["values"][-1] == 0.55
    assert data["labels"][0] < "2021-11-12"  # five years by default
    assert len(data["labels"]) < 400  # thinned to one point per week
    assert data["unit"] == "percent"


def test_f6_1_inflation_is_the_change_from_a_year_earlier(client, clock):
    cpi = card(client.get("/macro").text, "cpi-inflation")
    # Sep 2026 309.0 vs Sep 2025 300.0 = 3.0%; Aug was 2.7%.
    assert "3.0%" in cpi
    assert "+0.3 points" in cpi
    assert "Latest figure: Sep 1, 2026 · change since Aug 1, 2026" in cpi


def test_f6_1_money_market_funds_weekly_and_quarterly(client, clock):
    page = client.get("/macro").text
    weekly = card(page, "money-market-fund-assets-weekly")
    assert "$7.42 trillion" in weekly
    assert "Latest figure: Nov 5, 2026" in weekly
    assert "fills in over time: one reading per week" in weekly
    assert "ICI weekly report" in weekly
    quarterly = card(page, "money-market-fund-assets-quarterly")
    assert "$7.10 trillion" in quarterly
    assert "+1.4%" in quarterly  # 7.1 vs 7.0 trillion
    assert "change since Jan 1, 2026" in quarterly


def test_f6_1_equity_risk_premium_and_forward_pe(client, clock):
    page = client.get("/macro").text
    erp = card(page, "equity-risk-premium")
    # SPY P/E 25 -> earnings yield 4.00%, minus the 4.25% 10-year Treasury yield.
    assert "-0.25%" in erp
    assert "Yahoo Finance (SPY P/E)" in erp and "FRED (DGS10)" in erp
    assert "fills in over time: one reading per trading day" in erp
    pe = card(page, "s-p-500-forward-p-e-estimate")
    assert "25.0×" in pe  # 14,450 / 579 = 24.96
    assert "Based on 9 of the 10 largest US companies" in pe


def test_f6_2_every_indicator_shows_its_source_and_latest_date(client, clock):
    page = client.get("/macro").text
    cards = re.findall(r'<article class="macro-card.*?</article>', page, re.S)
    assert len(cards) == len(TEST_MACRO["indicators"])
    for each in cards:
        assert "Latest figure:" in each
        assert re.search(r"Source: <a href=\"https://", each)
        assert "checked Nov 6, 2026" in each
    assert 'href="https://fred.stlouisfed.org/series/T10Y2Y"' in page


def test_one_year_charts(client, clock):
    data = chart(card(client.get("/macro?years=1").text, "yield-curve-10-year-minus-2-year"))
    assert data["labels"][0] >= "2025-11-05"
    assert data["labels"][-1] == "2026-11-05"
    # Anything else shows the longest chart.
    assert client.get("/macro?years=<script>").status_code == 200


def test_f6_3_an_indicator_added_in_the_settings_appears(client, clock, macro_setup, fake_fred):
    fake_fred.series["UNRATE"] = [
        (MACRO_NOW.date().replace(day=1) - timedelta(days=31), 4.3),
        (MACRO_NOW.date().replace(day=1), 4.4),
    ]
    added = {
        **TEST_MACRO,
        "indicators": TEST_MACRO["indicators"]
        + [{"label": "Unemployment rate", "series": "unrate", "decimals": 1}],
    }
    macro_setup.config = MacroConfig.model_validate(added)
    jobless = card(client.get("/macro").text, "unemployment-rate")
    assert "4.4%" in jobless and "+0.1 points" in jobless
    assert "FRED (UNRATE)" in jobless


def test_without_a_fred_key_fred_cards_are_missing_and_the_rest_work(client, clock, fake_fred):
    fake_fred.is_set_up = False
    page = client.get("/macro").text
    assert "FRED_API_KEY" in page
    curve = card(page, "yield-curve-10-year-minus-2-year")
    assert "missing" in curve.split('"')[1] and "No figures yet." in curve
    assert "$7.42 trillion" in card(page, "money-market-fund-assets-weekly")
    assert "25.0×" in card(page, "s-p-500-forward-p-e-estimate")


def test_a_failing_source_shows_stale_with_the_reason(client, clock, fake_fred, macro_fetch):
    client.get("/macro")
    clock.now = MACRO_NOW + timedelta(hours=13)
    fake_fred.down = True
    macro_fetch.down.add("https://www.ici.org/")
    page = client.get("/macro").text
    curve = card(page, "yield-curve-10-year-minus-2-year")
    assert 'class="macro-card stale"' in curve
    assert "Not up to date: FRED didn't answer in time." in html.unescape(curve)
    assert "0.55%" in curve  # the saved figure is still shown
    weekly = card(page, "money-market-fund-assets-weekly")
    assert "Couldn't read ICI's page" in html.unescape(weekly) and "$7.42 trillion" in weekly
    # The Yahoo-based card is unaffected.
    assert 'class="macro-card ok"' in card(page, "s-p-500-forward-p-e-estimate")


def test_first_visit_while_fetching_in_the_background(client, clock, macro_setup, monkeypatch):
    started = []
    monkeypatch.setattr(macro, "_start_thread", started.append)
    macro_setup.background = True
    page = client.get("/macro").text
    assert "Fetching the figures for the first time" in page
    assert '<meta http-equiv="refresh" content="20">' in page
    assert "No figures yet." in page  # the page didn't wait
    started[0]()  # the background job finishes
    page = client.get("/macro").text
    assert "Fetching" not in page and 'http-equiv="refresh"' not in page
    assert "0.55%" in page


def test_charts_script_is_versioned_and_served(client, clock):
    page = client.get("/macro").text
    for name in ("chart.umd.min.js", "macro_charts.js"):
        match = re.search(rf'/static/{re.escape(name)}\?v=([0-9a-f]{{12}})"', page)
        assert match, name
        assert client.get(f"/static/{name}").status_code == 200
