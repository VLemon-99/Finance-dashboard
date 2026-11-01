"""Watchlist pages end to end: F1.1-F1.7 through the browser-facing routes.

The `client` fixture uses small limits (3 lists, 2 stocks per list) from conftest.
"""

import re
from datetime import date

import pytest
from sqlmodel import Session, select

from app.db.models import Quote, Watchlist, WatchlistItem
from app.timeutils import format_eastern
from app.web import formatting
from tests.test_web import age_stored_quotes


def create(client, name="Tech"):
    response = client.post("/watchlists", data={"name": name}, follow_redirects=False)
    assert response.status_code == 303
    return response.headers["location"]


def add(client, url, ticker):
    return client.post(f"{url}/items", data={"ticker": ticker}, follow_redirects=False)


def test_home_lists_watchlists_with_counts(client):
    assert "No watchlists yet" in client.get("/").text
    url = create(client, "Tech")
    create(client, "Dividends")
    add(client, url, "AAPL")
    html = client.get("/").text
    assert f'<a href="{url}">Tech</a>' in html
    assert "1 stock<" in html
    assert "Dividends" in html and "0 stocks" in html
    assert "2 of 3 lists used" in html


# --- F1.2 ---------------------------------------------------------------------------


def test_f1_2_create_rename_delete(client, engine):
    url = create(client, "Tech")
    assert client.get(url).status_code == 200
    response = client.post(f"{url}/rename", data={"name": "Big tech"}, follow_redirects=False)
    assert response.status_code == 303
    assert "<h1>Big tech</h1>" in client.get(url).text
    response = client.post(f"{url}/delete", follow_redirects=False)
    assert response.headers["location"] == "/"
    assert client.get(url).status_code == 404
    with Session(engine) as session:
        assert session.exec(select(Watchlist)).all() == []


def test_f1_2_add_and_remove_by_ticker(client, engine):
    url = create(client)
    assert add(client, url, "aapl").status_code == 303
    html = client.get(url).text
    assert 'data-symbol="AAPL"' in html
    item_id = re.search(r"/items/(\d+)/delete", html).group(1)
    client.post(f"{url}/items/{item_id}/delete")
    assert 'data-symbol="AAPL"' not in client.get(url).text
    with Session(engine) as session:
        assert session.exec(select(WatchlistItem)).all() == []


def test_bad_names_are_rejected_with_a_message(client):
    response = client.post("/watchlists", data={"name": "   "})
    assert response.status_code == 400
    assert "Please give the list a name." in response.text
    create(client, "Tech")
    response = client.post("/watchlists", data={"name": "tech"})
    assert "You already have a list called &#34;tech&#34;" in response.text


def test_user_text_is_escaped(client):
    url = create(client, "<script>alert(1)</script>")
    html = client.get(url).text
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html


def test_unknown_watchlist_is_404(client):
    response = client.get("/watchlists/999")
    assert response.status_code == 404
    assert "doesn't exist" in response.text
    assert client.post("/watchlists/999/items", data={"ticker": "AAPL"}).status_code == 404


# --- F1.1 search ------------------------------------------------------------------------


def test_f1_1_search_by_company_name_htmx(client, fake_market_data):
    url = create(client)
    response = client.get(f"{url}/search", params={"q": "amazon"}, headers={"HX-Request": "true"})
    assert response.status_code == 200
    html = response.text
    assert "<html" not in html
    assert "AMZN" in html and "Amazon.com, Inc." in html and "NASDAQ" in html
    assert "AMZY" in html and "ETF" in html
    assert "AMZN.MX" not in html  # foreign listing filtered out
    assert "AMZZ" not in html  # OTC filtered out
    assert 'name="ticker" value="AMZN"' in html  # one-click Add
    assert fake_market_data.searches == ["amazon"]


def test_f1_1_search_without_javascript(client):
    url = create(client)
    html = client.get(url, params={"q": "AMZN"}).text
    assert "<html" in html and "Amazon.com, Inc." in html


def test_f1_1_search_result_already_in_list_has_no_add_button(client):
    url = create(client)
    add(client, url, "AMZN")
    html = client.get(f"{url}/search", params={"q": "amazon"}).text
    assert 'name="ticker" value="AMZN"' not in html
    assert "In this list" in html


def test_f1_1_adding_from_search_result(client):
    url = create(client)
    assert add(client, url, "AMZN").status_code == 303  # what the Add button posts
    assert 'data-symbol="AMZN"' in client.get(url).text


@pytest.mark.parametrize("q", ["<script>", "x" * 51, "a;b"])
def test_f1_1_bad_search_text_is_not_sent(client, fake_market_data, q):
    url = create(client)
    html = client.get(f"{url}/search", params={"q": q}).text
    assert "Search with a ticker or company name" in html
    assert fake_market_data.searches == []


def test_f1_6_search_down_keeps_the_page_working(client, fake_market_data):
    url = create(client)
    fake_market_data.mode = "down"
    response = client.get(url, params={"q": "amazon"})
    assert response.status_code == 200
    assert "Search isn&#39;t available right now" in response.text


# --- F1.3 limits ------------------------------------------------------------------------


def test_f1_3_list_limit_message(client, engine):
    for name in ("A", "B", "C"):
        create(client, name)
    response = client.post("/watchlists", data={"name": "D"})
    assert response.status_code == 400
    assert "You already have 3 watchlists, the maximum." in response.text
    assert "config/settings.yaml" in response.text
    with Session(engine) as session:
        assert len(session.exec(select(Watchlist)).all()) == 3


def test_f1_3_item_limit_message(client, engine):
    url = create(client)
    add(client, url, "AAPL")
    add(client, url, "MSFT")
    response = add(client, url, "NVDA")
    assert response.status_code == 400
    assert "This list already has 2 items, the maximum." in response.text
    with Session(engine) as session:
        assert len(session.exec(select(WatchlistItem)).all()) == 2
    assert "2 of 2 stocks" in response.text


# --- F1.4 full row --------------------------------------------------------------------


def test_f1_4_row_shows_every_field(client, engine):
    url = create(client)
    add(client, url, "AAPL")
    html = client.get(url).text
    row = re.search(r'<tr data-symbol="AAPL".*?</tr>', html, re.S).group(0)
    assert "259.58" in row  # last price
    assert "+1.54 (+0.60%)" in row  # change $ and %
    assert formatting.session_label(date(2026, 10, 30), date(2026, 11, 1)) in row
    assert "257.60 – 260.15" in row  # day range
    assert "186.20 – 271.40" in row  # 52-week range
    assert "36.54M" in row  # volume
    assert "$3.85T" in row  # market cap
    assert "Nov 19, 2026" in row  # next earnings
    with Session(engine) as session:
        fetched = session.exec(select(Quote)).one().fetched_at
    assert format_eastern(fetched) in row  # fetch time
    assert 'class="up"' in row


# --- F1.5 refresh -----------------------------------------------------------------------


def test_f1_5_page_reuses_recent_prices_and_refresh_refetches(client, fake_market_data):
    url = create(client)
    add(client, url, "AAPL")
    fake_market_data.calls.clear()
    html = client.get(url).text
    assert fake_market_data.calls == []
    assert "Refresh prices" in html
    assert "up to 15 minutes old" in html
    client.get(url, params={"refresh": "1"})
    assert fake_market_data.calls == ["AAPL"]


def test_f1_5_prices_older_than_15_minutes_are_refetched(client, engine, fake_market_data):
    url = create(client)
    add(client, url, "AAPL")
    age_stored_quotes(engine)
    fake_market_data.calls.clear()
    client.get(url)
    assert fake_market_data.calls == ["AAPL"]


# --- F1.6 source failure ------------------------------------------------------------------


def test_f1_6_source_down_shows_stale_rows_and_a_note(client, fake_market_data):
    url = create(client)
    add(client, url, "AAPL")
    add(client, url, "MSFT")
    fake_market_data.down_for.add("MSFT")
    response = client.get(url, params={"refresh": "1"})
    assert response.status_code == 200
    html = response.text
    assert 'data-symbol="AAPL" data-status="ok"' in html
    assert 'data-symbol="MSFT" data-status="stale"' in html
    assert ">stale</span>" in html
    assert "The data source failed for 1 of 2 stocks" in html
    assert "MSFT: Yahoo Finance did not respond properly" in html


def test_f1_6_whole_source_down_page_still_loads(client, fake_market_data):
    url = create(client)
    add(client, url, "AAPL")
    fake_market_data.mode = "crash"
    response = client.get(url, params={"refresh": "1"})
    assert response.status_code == 200
    assert 'data-status="stale"' in response.text


# --- F1.7 unknown ticker ------------------------------------------------------------------


def test_f1_7_unknown_ticker_is_not_added(client, engine, fake_market_data):
    url = create(client)
    fake_market_data.not_found.add("ZZZZ")
    response = add(client, url, "ZZZZ")
    assert response.status_code == 400
    assert "Ticker ZZZZ not found, so it wasn&#39;t added." in response.text
    with Session(engine) as session:
        assert session.exec(select(WatchlistItem)).all() == []


def test_invalid_ticker_is_rejected(client, fake_market_data):
    url = create(client)
    response = add(client, url, "<b>bad</b>")
    assert "Please enter a valid US ticker" in response.text
    assert "<b>bad</b>" not in response.text
    assert fake_market_data.calls == []
