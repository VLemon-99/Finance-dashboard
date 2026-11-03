"""Option chain pages end to end (F2.1, F2.2, F2.4), with the fake data source."""

import re

import pytest

from tests.conftest import OPTION_EXPIRATION

CHAIN_URL = f"/options/AAPL?expiration={OPTION_EXPIRATION.isoformat()}"


@pytest.fixture
def options_client(client, options_clock):
    return client


def rows(html):
    """(strike, classes) for each chain row, in page order."""
    return [
        (float(strike), classes)
        for strike, classes in re.findall(r'<tr data-strike="([\d.]+)"[^>]*class="([^"]*)"', html)
    ]


def test_menu_and_start_page(options_client):
    html = options_client.get("/").text
    assert '<a href="/options">Options</a>' in html
    assert "Show options" in options_client.get("/options").text


def test_start_page_opens_the_chain(options_client):
    response = options_client.get("/options?ticker=aapl", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/options/AAPL"


@pytest.mark.parametrize("url", ["/options?ticker=$$$", "/options/not a ticker"])
def test_invalid_ticker_is_rejected_before_any_fetch(options_client, fake_market_data, url):
    response = options_client.get(url)
    assert response.status_code == 400
    assert "valid US ticker" in response.text
    assert fake_market_data.calls == [] and fake_market_data.chain_calls == []


def test_f2_1_chain_page_with_expiration_and_calls(options_client):
    html = options_client.get(CHAIN_URL).text
    assert "<h1>AAPL options</h1>" in html
    # Every listed date is offered; the chosen one is selected.
    assert html.count("<option value=") == 5
    assert '<option value="2026-11-20" selected>Nov 20, 2026</option>' in html
    assert 'aria-current="page">Calls</a>' in html
    assert len(rows(html)) == 8
    assert "17 days left" in html


def test_f2_1_switch_to_puts(options_client):
    html = options_client.get(CHAIN_URL + "&type=put").text
    assert 'aria-current="page">Puts</a>' in html
    assert 'data-type="put"' in html
    assert len(rows(html)) == 7


def test_f2_1_default_expiration_is_the_earliest(options_client):
    html = options_client.get("/options/AAPL").text
    assert '<option value="2026-11-06" selected>' in html


def test_f2_2_row_shows_all_fields(options_client):
    html = options_client.get(CHAIN_URL).text
    row = re.search(r'<tr data-strike="260.0".*?</tr>', html, re.S).group(0)
    cells = re.findall(r'<td class="num">(.*?)</td>', row, re.S)
    # Strike, bid, ask, mid, last, volume, open interest, IV, delta, gamma, theta, vega, DTE
    assert len(cells) == 13
    assert cells[1:4] == ["6.26", "6.36", "6.31"]
    assert cells[5:8] == ["4,875", "15,230", "28.0%"]
    assert re.fullmatch(r"0\.5\d\d", cells[8])  # delta near 0.5 at the money
    assert cells[10].startswith("-")  # theta
    assert cells[12] == "17"
    assert "4.30% (13-week T-bill)" in html


def test_f2_2_missing_values_show_a_dash(options_client):
    html = options_client.get(CHAIN_URL).text
    row = re.search(r'<tr data-strike="350.0".*?</tr>', html, re.S).group(0)
    cells = re.findall(r'<td class="num">(.*?)</td>', row, re.S)
    assert cells[3] == "—"  # mid: no bid
    assert cells[5] == "—"  # volume
    assert cells[7:12] == ["—"] * 5  # IV placeholder -> no IV, no greeks


def test_f2_4_nearest_strike_highlighted_and_itm_shaded(options_client):
    calls = rows(options_client.get(CHAIN_URL).text)
    assert [k for k, c in calls if "nearest" in c] == [260]
    assert [k for k, c in calls if c.startswith("itm")] == [245, 250, 255]
    puts = rows(options_client.get(CHAIN_URL + "&type=put").text)
    assert [k for k, c in puts if c.startswith("itm")] == [260, 265, 270, 275]
    assert "≈ stock" in options_client.get(CHAIN_URL).text


def test_refresh_button_fetches_again(options_client, fake_market_data):
    options_client.get(CHAIN_URL)
    options_client.get(CHAIN_URL)
    assert len(fake_market_data.chain_calls) == 1
    options_client.get(CHAIN_URL + "&refresh=1")
    assert len(fake_market_data.chain_calls) == 2


def test_unknown_ticker_says_not_found(options_client, fake_market_data):
    fake_market_data.not_found.add("ZZZZQ")
    response = options_client.get("/options/ZZZZQ")
    assert response.status_code == 200
    assert "Ticker ZZZZQ not found." in response.text
    assert fake_market_data.chain_calls == []


def test_ticker_without_options(options_client, fake_market_data):
    fake_market_data.no_options.add("BRK-A")
    assert "No listed options found for BRK-A." in options_client.get("/options/BRK-A").text


def test_f1_6_source_down_page_still_loads(options_client, fake_market_data, options_clock):
    options_client.get(CHAIN_URL)
    fake_market_data.mode = "down"
    response = options_client.get(CHAIN_URL + "&refresh=1")
    assert response.status_code == 200
    assert len(rows(response.text)) == 8  # stored prices
    assert "stale" in response.text
    assert "Showing stored prices." in response.text


def test_f1_6_source_down_nothing_stored(options_client, fake_market_data):
    fake_market_data.mode = "down"
    response = options_client.get(CHAIN_URL)
    assert response.status_code == 200
    assert "<table" not in response.text
    assert "expiration dates" in response.text


def test_links_from_watchlist_and_lookup(options_client):
    url = options_client.post("/watchlists", data={"name": "Tech"}, follow_redirects=False)
    url = url.headers["location"]
    options_client.post(f"{url}/items", data={"ticker": "AAPL"})
    assert 'href="/options/AAPL"' in options_client.get(url).text
    assert 'href="/options/AAPL"' in options_client.get("/lookup?ticker=AAPL").text
