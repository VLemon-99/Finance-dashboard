"""IMP-14: the quote lookup as a stock page (price, change pill, stats, actions, add to a list)."""

import re

import pytest
from sqlmodel import Session, select

from app.config import AppConfig
from app.db.models import User, Watchlist, WatchlistItem

PAGE = "/lookup?ticker=AAPL"
PARTIAL = "/lookup/result?ticker=AAPL"


@pytest.fixture
def app_config():
    return AppConfig.model_validate({"watchlists": {"max_watchlists": 5, "max_items_per_list": 3}})


@pytest.fixture
def web(client):
    client.post("/watchlists", data={"name": "Tech"})
    client.post("/watchlists", data={"name": "Income"})
    return client


def result(html):
    match = re.search(r'<section class="quote-card[^"]*"[^>]*>(.*?)</section>', html, re.S)
    assert match, "no quote card"
    return match.group(1)


@pytest.mark.parametrize("url", [PAGE, PARTIAL])
def test_imp14_change_is_a_pill_with_an_arrow(web, url):
    card = result(web.get(url).text)
    assert re.search(
        r'<p class="change up">\s*<span class="arrow" aria-hidden="true">▲</span> '
        r"\+1\.54 \(\+0\.60%\) on Fri, Oct 30",
        card,
    )


def test_imp14_stats_grid_has_every_figure(web):
    card = result(web.get(PAGE).text)
    stats = re.search(r'<dl class="facts stats">(.*?)</dl>', card, re.S).group(1)
    for label, value in [
        ("Day range", "257.60 – 260.15"),
        ("52-week range", "186.20 – 271.40"),
        ("Volume", "36.54M"),
        ("Market cap", "$3.85T"),
        ("Next earnings", "Nov 19, 2026"),
    ]:
        assert re.search(rf"<dt>{label}</dt>\s*<dd>{re.escape(value)}</dd>", stats), label
    assert "Fetched " in card


def test_imp14_actions_are_buttons_with_the_same_addresses(web):
    card = result(web.get(PAGE).text)
    actions = re.search(r'<div class="quote-actions">(.*?)</div>', card, re.S).group(1)
    assert 'href="/options/AAPL"' in actions
    assert 'href="/portfolio/trade?symbol=AAPL&amp;side=bought"' in actions
    assert 'href="/portfolio/trade?symbol=AAPL&amp;side=sold"' in actions
    assert actions.count('class="button') == 3


@pytest.mark.parametrize("url", [PAGE, PARTIAL])
def test_imp14_add_to_watchlist_lists_my_lists(web, url):
    card = result(web.get(url).text)
    form = re.search(
        r'<form method="post" action="/lookup/add" class="add-to-list">(.*?)</form>', card, re.S
    )
    assert form, "add-to-watchlist form"
    options = re.findall(r'<option value="(\d+)">([^<]+)</option>', form.group(1))
    assert options == [("1", "Tech"), ("2", "Income")]
    assert '<input type="hidden" name="ticker" value="AAPL">' in form.group(1)


def test_imp14_no_lists_no_form(client):
    assert 'class="add-to-list"' not in client.get(PAGE).text


def test_imp14_adding_puts_the_stock_in_the_list_and_opens_it(web, engine):
    response = web.post(
        "/lookup/add", data={"ticker": "AAPL", "watchlist_id": "2"}, follow_redirects=False
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/watchlists/2"
    assert 'data-symbol="AAPL"' in web.get("/watchlists/2").text


def test_imp14_limits_still_apply(web):
    for t in ("AAPL", "MSFT", "NVDA"):
        web.post("/lookup/add", data={"ticker": t, "watchlist_id": "1"})
    response = web.post("/lookup/add", data={"ticker": "AMZN", "watchlist_id": "1"})
    assert response.status_code == 400
    assert 'data-symbol="AMZN"' not in web.get("/watchlists/1").text


def test_imp14_another_users_list_is_refused(web, engine):
    with Session(engine) as s:
        other = User(email="other@example.com")
        s.add(other)
        s.commit()
        theirs = Watchlist(user_id=other.id, name="Theirs")
        s.add(theirs)
        s.commit()
        their_id = theirs.id
    response = web.post("/lookup/add", data={"ticker": "AAPL", "watchlist_id": str(their_id)})
    assert response.status_code == 404
    with Session(engine) as s:
        assert (
            s.exec(select(WatchlistItem).where(WatchlistItem.watchlist_id == their_id)).all() == []
        )
    assert "Theirs" not in result(web.get(PAGE).text)


def test_imp14_add_from_another_site_is_refused(web):
    response = web.post(
        "/lookup/add",
        data={"ticker": "AAPL", "watchlist_id": "1"},
        headers={"Origin": "https://evil.example"},
    )
    assert response.status_code == 403
    assert 'data-symbol="AAPL"' not in web.get("/watchlists/1").text
