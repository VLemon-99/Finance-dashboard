"""IMP-12: the home page as an at-a-glance dashboard (stored prices only, no fetching)."""

import re
from datetime import timedelta

import pytest
from sqlmodel import Session, delete, select

from app.config import AppConfig
from app.db.models import Quote
from app.timeutils import utc_now
from tests.conftest import OPTION_EXPIRATION

STOCKS = ["AAPL", "MSFT", "NVDA", "AMZN", "GOOG", "META", "TSLA"]


@pytest.fixture
def app_config():
    return AppConfig.model_validate({"watchlists": {"max_watchlists": 5, "max_items_per_list": 10}})


@pytest.fixture
def web(client, options_clock):
    client.post("/watchlists", data={"name": "Tech"})
    return client


def add(web, *tickers, list_id=1):
    for t in tickers:
        web.post(f"/watchlists/{list_id}/items", data={"ticker": t})


def card(html, list_id=1):
    match = re.search(rf'<li class="wl-card" data-watchlist="{list_id}">(.*?)\n  </li>', html, re.S)
    assert match, "no card"
    return match.group(1)


def shown(card_html):
    return re.findall(r'<li data-symbol="([^"]+)">', card_html)


def test_imp12_card_shows_stocks_with_price_and_change(web):
    add(web, "AAPL", "MSFT")
    c = card(web.get("/").text)
    assert shown(c) == ["AAPL", "MSFT"]
    row = re.search(r'<li data-symbol="AAPL">(.*?)</li>', c, re.S).group(1)
    assert '<span class="wl-price">259.58</span>' in row
    assert '<span class="arrow" aria-hidden="true">▲</span> +0.60%' in row
    assert 'class="up"' in row


def test_imp12_at_most_five_stocks_then_more(web):
    add(web, *STOCKS)
    c = card(web.get("/").text)
    assert shown(c) == STOCKS[:5]
    assert "+2 more" in c


def test_imp12_contracts_are_left_out_and_counted_in_more(web):
    add(web, "AAPL")
    web.get(f"/options/AAPL?expiration={OPTION_EXPIRATION.isoformat()}")
    web.post(
        "/options/AAPL/add",
        data={
            "contract": "AAPL261120P00250000",
            "watchlist_id": "1",
            "expiration": OPTION_EXPIRATION.isoformat(),
            "type": "put",
        },
    )
    c = card(web.get("/").text)
    assert shown(c) == ["AAPL"]
    assert "+1 more" in c


def test_imp12_home_fetches_no_quotes_or_chains(web, fake_market_data):
    add(web, "AAPL", "MSFT")
    fake_market_data.calls.clear()
    fake_market_data.chain_calls.clear()
    web.get("/")
    assert fake_market_data.calls == []
    assert fake_market_data.chain_calls == []


def test_imp12_shows_when_prices_were_fetched(web, engine):
    from app.timeutils import format_eastern

    add(web, "AAPL")
    with Session(engine) as s:
        fetched = s.exec(select(Quote)).one().fetched_at
    c = card(web.get("/").text)
    assert f"Prices from {format_eastern(fetched)}" in c


def test_imp12_old_prices_are_marked_stale(web, engine):
    add(web, "AAPL")
    with Session(engine) as s:
        quote = s.exec(select(Quote)).one()
        quote.fetched_at = utc_now() - timedelta(hours=3)
        s.add(quote)
        s.commit()
    assert '<span class="badge">stale</span>' in card(web.get("/").text)


def test_imp12_fresh_prices_are_not_stale(web, engine):
    add(web, "AAPL")
    with Session(engine) as s:
        quote = s.exec(select(Quote)).one()
        quote.fetched_at = utc_now()
        s.add(quote)
        s.commit()
    assert "stale" not in card(web.get("/").text)


def test_imp12_stock_never_fetched_shows_a_dash(web, engine):
    add(web, "AAPL")
    with Session(engine) as s:
        s.exec(delete(Quote))
        s.commit()
    c = card(web.get("/").text)
    row = re.search(r'<li data-symbol="AAPL">(.*?)</li>', c, re.S).group(1)
    assert '<span class="wl-price">—</span>' in row
    assert '<span class="flat">—</span>' in row
    assert "Prices not fetched yet" in c


def test_imp12_empty_list_invites_a_first_stock(web):
    assert 'Empty. <a href="/watchlists/1">Add a stock</a>' in card(web.get("/").text)


def test_imp12_events_still_shown_after_the_lists(web):
    html = web.get("/").text
    assert html.index('class="wl-card"') < html.index('aria-labelledby="coming-up"')


def test_imp12_arrow_follows_the_percentage_shown(web, fake_market_data):
    """A $0.01 move on a $2,600 stock shows as 0.00%: no arrow, not green."""
    from dataclasses import replace

    real = fake_market_data.get_quote

    def quote(symbol):
        q = real(symbol)
        return replace(q, last_price=2600.01, previous_close=2600.00)

    fake_market_data.get_quote = quote
    add(web, "AAPL")
    row = re.search(r'<li data-symbol="AAPL">(.*?)</li>', card(web.get("/").text), re.S).group(1)
    assert "arrow" not in row and 'class="flat"' in row and "0.00%" in row
