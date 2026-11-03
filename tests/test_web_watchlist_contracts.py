"""Option contracts in watchlists: add from the chain (F2.5a), rows (F2.6), expiry (F2.7).

The `client` fixture allows 3 lists and 2 items per list. The clock is frozen at
Tue Nov 3, 2026, 10:00 AM Eastern; the sample contracts expire Nov 20.
"""

import re
from datetime import UTC, datetime, timedelta
from html import unescape

import pytest
from sqlmodel import Session, select

from app.db.models import Instrument, OptionQuote, WatchlistItem
from tests.conftest import OPTION_EXPIRATION

CHAIN_URL = f"/options/AAPL?expiration={OPTION_EXPIRATION.isoformat()}"
C260 = "AAPL261120C00260000"
P250 = "AAPL261120P00250000"


@pytest.fixture
def options_client(client, options_clock):
    return client


def create(client, name="Tech"):
    response = client.post("/watchlists", data={"name": name}, follow_redirects=False)
    return int(response.headers["location"].rsplit("/", 1)[1])


def add_contract(client, list_id, contract=C260, option_type="call", ticker="AAPL"):
    return client.post(
        f"/options/{ticker}/add",
        data={
            "contract": contract,
            "watchlist_id": list_id,
            "expiration": OPTION_EXPIRATION.isoformat(),
            "type": option_type,
        },
        follow_redirects=False,
    )


def contract_row(html, symbol=C260):
    return re.search(rf'<tr data-symbol="{symbol}".*?</tr>', html, re.S).group(0)


def cells(row_html):
    return re.findall(r'<td class="num"[^>]*>(.*?)</td>', row_html, re.S)


# --- F2.5a: add from the chain -----------------------------------------------------


def test_f2_5a_add_contract_from_chain(options_client, engine):
    list_id = create(options_client)
    html = options_client.get(CHAIN_URL).text
    assert 'aria-label="Add AAPL Nov 20, 2026 260 call to Tech"' in html

    response = add_contract(options_client, list_id)
    assert response.status_code == 303
    location = response.headers["location"]
    assert location.startswith("/options/AAPL?expiration=2026-11-20&type=call")
    html = options_client.get(location).text
    assert "Added AAPL Nov 20, 2026 260 call to" in html
    assert re.search(rf'value="{C260}"', html) is None  # its Add button is gone...
    assert "In list" in html  # ...replaced by "In list"

    with Session(engine) as session:
        instrument = session.exec(select(Instrument).where(Instrument.symbol == C260)).one()
        assert instrument.kind == "option"
        assert (instrument.underlying, instrument.option_type) == ("AAPL", "call")
        assert (instrument.strike, instrument.expiration) == (260, OPTION_EXPIRATION)


def test_choose_which_list_to_add_to(options_client):
    create(options_client, "Tech")
    second = create(options_client, "Income")
    html = options_client.get(f"{CHAIN_URL}&list={second}").text
    assert f'<option value="{second}" selected>Income</option>' in html
    assert "to Income" in html
    add_contract(options_client, second)
    assert C260 in options_client.get(f"/watchlists/{second}").text


def test_no_watchlists_no_add_buttons(options_client):
    html = options_client.get(CHAIN_URL).text
    assert 'first <a href="/">create a watchlist</a>' in html
    assert "/add" not in html


def test_duplicate_contract_not_added_twice(options_client, engine):
    list_id = create(options_client)
    options_client.get(CHAIN_URL)
    add_contract(options_client, list_id)
    response = add_contract(options_client, list_id)
    assert response.status_code == 400
    assert f"{C260} is already in Tech." in unescape(response.text)
    with Session(engine) as session:
        assert len(session.exec(select(WatchlistItem)).all()) == 1


def test_f1_3_contracts_count_toward_the_item_limit(options_client, engine):
    list_id = create(options_client)
    options_client.get(CHAIN_URL)
    options_client.post(f"/watchlists/{list_id}/items", data={"ticker": "AAPL"})
    assert add_contract(options_client, list_id).status_code == 303
    response = add_contract(options_client, list_id, P250, "put")
    assert response.status_code == 400
    assert "already has 2 items, the maximum" in unescape(response.text)
    with Session(engine) as session:
        assert len(session.exec(select(WatchlistItem)).all()) == 2


@pytest.mark.parametrize("contract", ["AAPL261120C00999000", "NOT A SYMBOL", "", "'; DROP"])
def test_unknown_or_malformed_contract_is_rejected(options_client, engine, contract):
    list_id = create(options_client)
    options_client.get(CHAIN_URL)
    response = add_contract(options_client, list_id, contract)
    assert response.status_code == 400
    assert "Couldn't find that option contract" in unescape(response.text)
    with Session(engine) as session:
        assert session.exec(select(WatchlistItem)).all() == []


def test_add_to_missing_watchlist_is_404(options_client):
    options_client.get(CHAIN_URL)
    assert add_contract(options_client, 999).status_code == 404


def test_add_with_invalid_ticker_in_address(options_client):
    list_id = create(options_client)
    options_client.get(CHAIN_URL)
    assert add_contract(options_client, list_id, ticker="not a ticker").status_code == 400


# --- F2.6: contract rows in the watchlist ----------------------------------------------


def test_f2_6_contract_row_shows_chain_fields_and_stock_price(options_client):
    list_id = create(options_client)
    options_client.get(CHAIN_URL)
    add_contract(options_client, list_id)
    html = options_client.get(f"/watchlists/{list_id}").text
    assert "<h2>Option contracts</h2>" in html
    row = contract_row(html)
    assert "AAPL Nov 20, 2026 260 call" in row
    assert 'href="/options/AAPL?expiration=2026-11-20&amp;type=call"' in row
    values = cells(row)
    # Stock, bid, ask, mid, last, volume, open interest, IV, delta, gamma, theta, vega, DTE
    assert len(values) == 13
    assert values[0] == "259.58"
    assert values[1:4] == ["6.26", "6.36", "6.31"]
    assert values[5:8] == ["4,875", "15,230", "28.0%"]
    assert re.fullmatch(r"0\.5\d\d", values[8])
    assert values[12] == "17"
    assert "1 of 2 items." in html
    assert "1 item<" in options_client.get("/").text


def test_contract_prices_reused_and_refreshed_like_stocks(
    options_client, fake_market_data, options_clock
):
    list_id = create(options_client)
    options_client.get(CHAIN_URL)
    add_contract(options_client, list_id)
    add_contract(options_client, list_id, "AAPL261120C00265000")
    calls_before = len(fake_market_data.chain_calls)
    options_client.get(f"/watchlists/{list_id}")
    assert len(fake_market_data.chain_calls) == calls_before  # fresh: reused
    options_client.get(f"/watchlists/{list_id}?refresh=1")
    # Two contracts from the same chain -> one fetch.
    assert fake_market_data.chain_calls[calls_before:] == [("AAPL", OPTION_EXPIRATION)]


def test_f1_6_contract_rows_stale_when_source_down(options_client, fake_market_data):
    list_id = create(options_client)
    options_client.get(CHAIN_URL)
    add_contract(options_client, list_id)
    fake_market_data.mode = "down"
    response = options_client.get(f"/watchlists/{list_id}?refresh=1")
    assert response.status_code == 200
    row = contract_row(response.text)
    assert 'data-status="stale"' in row and "6.26" in row
    assert "The data source failed for 1 of 1 contracts" in response.text


def test_contract_no_longer_listed_is_stale(options_client, engine, options_clock):
    list_id = create(options_client)
    options_client.get(CHAIN_URL)
    add_contract(options_client, list_id)
    with Session(engine) as session:  # pretend Yahoo dropped this strike since
        row = session.exec(select(OptionQuote).where(OptionQuote.contract_symbol == C260)).one()
        row.fetched_at = options_clock.now - timedelta(days=1)
        session.add(row)
        session.commit()
    html = options_client.get(f"/watchlists/{list_id}").text
    assert 'data-status="stale"' in contract_row(html)
    assert "no longer lists this contract" in html


# --- F2.7: expired contracts -----------------------------------------------------------


def test_f2_7_expired_contract_marked_and_not_refreshed(
    options_client, fake_market_data, options_clock
):
    list_id = create(options_client)
    options_client.get(CHAIN_URL)
    add_contract(options_client, list_id)
    # Friday Nov 20, 2026, 4:00 PM Eastern: the contract has expired.
    options_clock.now = datetime(2026, 11, 20, 21, 0, tzinfo=UTC)
    calls_before = len(fake_market_data.chain_calls)
    html = options_client.get(f"/watchlists/{list_id}?refresh=1").text
    assert fake_market_data.chain_calls[calls_before:] == []  # not refreshed
    row = contract_row(html)
    assert 'data-status="expired"' in row
    assert '<span class="badge plain">expired</span>' in row
    values = cells(row)
    assert values[1] == "6.26"  # last stored prices kept
    assert values[8:12] == ["—"] * 4  # no greeks after expiry
    assert values[12] == "expired"


def test_expired_contract_cannot_be_added(options_client, options_clock):
    list_id = create(options_client)
    options_client.get(CHAIN_URL)
    options_clock.now = datetime(2026, 11, 21, 15, 0, tzinfo=UTC)
    response = add_contract(options_client, list_id)
    assert response.status_code == 400
    assert "has expired" in response.text


def test_remove_contract(options_client, engine):
    list_id = create(options_client)
    options_client.get(CHAIN_URL)
    add_contract(options_client, list_id)
    with Session(engine) as session:
        item_id = session.exec(select(WatchlistItem)).one().id
    options_client.post(f"/watchlists/{list_id}/items/{item_id}/delete")
    assert "Option contracts" not in options_client.get(f"/watchlists/{list_id}").text


# --- reports research stocks only ---------------------------------------------------------


def test_report_for_a_list_skips_contracts(options_client, report_runner):
    list_id = create(options_client)
    options_client.post(f"/watchlists/{list_id}/items", data={"ticker": "AAPL"})
    options_client.get(CHAIN_URL)
    add_contract(options_client, list_id)
    report_runner.hold = True
    options_client.post("/reports/generate", data={"watchlist_id": list_id})
    assert report_runner.started[-1][1] == ["AAPL"]
