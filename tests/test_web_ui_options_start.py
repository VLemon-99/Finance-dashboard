"""IMP-15: one-tap ticker chips on the Option chains start page (no fetching)."""

import re

import pytest
from sqlmodel import Session

from app.config import PROJECT_ROOT, AppConfig, load_app_config
from app.db.models import Instrument, User, Watchlist, WatchlistItem
from tests.test_web_spreads_expiry import P250, trade


@pytest.fixture
def app_config():
    return AppConfig.model_validate(
        {
            "watchlists": {"max_watchlists": 5, "max_items_per_list": 10},
            "options": {"max_quick_tickers": 4},
        }
    )


@pytest.fixture
def web(client, options_clock):
    client.post("/watchlists", data={"name": "Tech"})
    return client


def chips(html):
    nav = re.search(r'<nav class="quick-tickers"[^>]*>(.*?)</nav>', html, re.S)
    if nav is None:
        return None
    return re.findall(r'<a class="chip" href="/options/([^"]+)">([^<]+)</a>', nav.group(1))


def test_imp15_cap_comes_from_settings():
    config = load_app_config(PROJECT_ROOT / "config" / "settings.yaml")
    assert config.options.max_quick_tickers == 20
    assert AppConfig().options.max_quick_tickers == 20


def test_imp15_watchlist_and_portfolio_tickers_a_to_z(web):
    for t in ("NVDA", "AAPL"):
        web.post("/watchlists/1/items", data={"ticker": t})
    web.get("/options/MSFT?expiration=2026-11-20")
    trade(web, "MSFT", "bought", 5, 400, day="2026-10-29")
    found = chips(web.get("/options").text)
    assert found == [("AAPL", "AAPL"), ("MSFT", "MSFT"), ("NVDA", "NVDA")]


def test_imp15_stocks_behind_options_count_once(web):
    web.post("/watchlists/1/items", data={"ticker": "AAPL"})
    web.get("/options/AAPL?expiration=2026-11-20")
    trade(web, P250, "sold", 1, 3.00)  # an AAPL option position: AAPL is already a chip
    assert chips(web.get("/options").text) == [("AAPL", "AAPL")]


def test_imp15_above_the_cap_the_first_ones_a_to_z(web):
    for t in ("TSLA", "META", "AAPL", "NVDA", "GOOG", "AMZN"):
        web.post("/watchlists/1/items", data={"ticker": t})
    found = [t for t, _ in chips(web.get("/options").text)]
    assert found == ["AAPL", "AMZN", "GOOG", "META"]  # max_quick_tickers = 4


def test_imp15_closed_positions_dont_count(web):
    web.get("/options/AAPL?expiration=2026-11-20")
    trade(web, "AAPL", "bought", 5, 250, day="2026-10-29")
    trade(web, "AAPL", "sold", 5, 255)
    assert chips(web.get("/options").text) is None


def test_imp15_another_users_tickers_never_appear(web, engine):
    with Session(engine) as s:
        other = User(email="other@example.com")
        s.add(other)
        s.commit()
        theirs = Watchlist(user_id=other.id, name="Theirs")
        stock = Instrument(kind="stock", symbol="QZXW")
        s.add(theirs)
        s.add(stock)
        s.commit()
        s.add(WatchlistItem(user_id=other.id, watchlist_id=theirs.id, instrument_id=stock.id))
        s.commit()
    web.post("/watchlists/1/items", data={"ticker": "AAPL"})
    html = web.get("/options").text
    assert chips(html) == [("AAPL", "AAPL")]
    assert "QZXW" not in html


def test_imp15_start_page_fetches_nothing(web, fake_market_data):
    web.post("/watchlists/1/items", data={"ticker": "AAPL"})
    fake_market_data.calls.clear()
    fake_market_data.chain_calls.clear()
    fake_market_data.expiration_calls.clear()
    web.get("/options")
    assert fake_market_data.calls == []
    assert fake_market_data.chain_calls == []
    assert fake_market_data.expiration_calls == []


def test_imp15_no_tickers_only_the_box_and_a_hint(web):
    html = web.get("/options").text
    assert chips(html) is None
    assert "Show options" in html


def test_imp15_typed_ticker_still_redirects(web):
    response = web.get("/options", params={"ticker": "msft"}, follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/options/MSFT"


def test_imp15_stock_behind_a_watchlist_contract_is_a_chip(web):
    web.get("/options/AAPL?expiration=2026-11-20")
    web.post(
        "/options/AAPL/add",
        data={"contract": P250, "watchlist_id": "1", "expiration": "2026-11-20", "type": "put"},
    )
    assert chips(web.get("/options").text) == [("AAPL", "AAPL")]


def test_imp15_zero_hides_the_chips(
    engine,
    fake_market_data,
    options_clock,
    report_services,
    report_runner,
    fake_fred,
    fomc,
    macro_setup,
):
    """The same fakes as the `client` fixture (no network), with the shortcuts switched off."""
    from fastapi.testclient import TestClient

    from app.main import create_app

    config = AppConfig.model_validate({"options": {"max_quick_tickers": 0}})
    app = create_app(
        engine=engine,
        market_data=fake_market_data,
        app_config=config,
        report_services=report_services,
        report_runner=report_runner,
        fred=fake_fred,
        fomc=fomc,
        macro=macro_setup,
    )
    with TestClient(app) as c:
        c.post("/watchlists", data={"name": "Tech"})
        c.post("/watchlists/1/items", data={"ticker": "AAPL"})
        assert chips(c.get("/options").text) is None
