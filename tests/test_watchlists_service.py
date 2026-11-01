"""Watchlist rules: F1.2 (manage lists), F1.3 (limits), F1.5 (refresh window), F1.6, F1.7."""

from datetime import timedelta

import pytest
from sqlmodel import select

from app.config import WatchlistLimits
from app.db.models import Instrument, Quote, User, Watchlist, WatchlistItem
from app.services import watchlists as wl
from app.services.users import ensure_default_user
from tests.conftest import FAKE_MARKET_CAP

LIMITS = WatchlistLimits(max_watchlists=3, max_items_per_list=2)
WINDOW = timedelta(minutes=15)


@pytest.fixture
def user_id(session):
    return ensure_default_user(session, "owner@localhost").id


@pytest.fixture
def other_user_id(session):
    user = User(email="someone-else@example.com")
    session.add(user)
    session.commit()
    return user.id


@pytest.fixture
def tech(session, user_id):
    return wl.create_watchlist(session, user_id, "Tech", LIMITS)


def add(session, md, user_id, watchlist, ticker, limits=LIMITS):
    return wl.add_ticker(session, md, user_id, watchlist.id, ticker, limits, max_age=WINDOW)


# --- names -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("Tech", "Tech"), ("  Big   tech  ", "Big tech"), ("Div & Co", "Div & Co")],
)
def test_clean_name(raw, expected):
    assert wl.clean_name(raw) == expected


@pytest.mark.parametrize("raw", ["", "   ", "x" * 61, "tab\x00null"])
def test_clean_name_rejects(raw):
    with pytest.raises(wl.WatchlistError):
        wl.clean_name(raw)


# --- F1.2 create / rename / delete ---------------------------------------------------------


def test_create_and_list(session, user_id):
    wl.create_watchlist(session, user_id, "Tech", LIMITS)
    wl.create_watchlist(session, user_id, "Dividends", LIMITS)
    assert [(s.name, s.item_count) for s in wl.list_watchlists(session, user_id)] == [
        ("Tech", 0),
        ("Dividends", 0),
    ]
    assert all(w.user_id == user_id for w in session.exec(select(Watchlist)))


def test_duplicate_name_is_rejected_ignoring_case(session, user_id, tech):
    with pytest.raises(wl.WatchlistError, match="already have a list called"):
        wl.create_watchlist(session, user_id, "  TECH ", LIMITS)
    assert len(session.exec(select(Watchlist)).all()) == 1


def test_rename(session, user_id, tech):
    wl.rename_watchlist(session, user_id, tech.id, "Big tech")
    assert session.get(Watchlist, tech.id).name == "Big tech"


def test_rename_to_same_name_with_other_case_is_allowed(session, user_id, tech):
    wl.rename_watchlist(session, user_id, tech.id, "TECH")
    assert session.get(Watchlist, tech.id).name == "TECH"


def test_rename_to_another_lists_name_is_rejected(session, user_id, tech):
    wl.create_watchlist(session, user_id, "Dividends", LIMITS)
    with pytest.raises(wl.WatchlistError):
        wl.rename_watchlist(session, user_id, tech.id, "dividends")
    assert session.get(Watchlist, tech.id).name == "Tech"


def test_delete_removes_list_and_items_but_keeps_prices(session, fake_market_data, user_id, tech):
    add(session, fake_market_data, user_id, tech, "AAPL")
    wl.delete_watchlist(session, user_id, tech.id)
    assert session.exec(select(Watchlist)).all() == []
    assert session.exec(select(WatchlistItem)).all() == []
    assert len(session.exec(select(Quote)).all()) == 1  # shared market data stays


def test_another_users_list_is_invisible(session, user_id, other_user_id, tech):
    assert wl.list_watchlists(session, other_user_id) == []
    for action in (
        lambda: wl.get_watchlist(session, other_user_id, tech.id),
        lambda: wl.rename_watchlist(session, other_user_id, tech.id, "Mine now"),
        lambda: wl.delete_watchlist(session, other_user_id, tech.id),
    ):
        with pytest.raises(wl.WatchlistNotFound):
            action()
    assert session.get(Watchlist, tech.id).name == "Tech"


# --- F1.2 add / remove items -------------------------------------------------------------


def test_add_ticker_needs_only_the_symbol(session, fake_market_data, user_id, tech):
    quote = add(session, fake_market_data, user_id, tech, "aapl")
    assert quote.symbol == "AAPL"
    item = session.exec(select(WatchlistItem)).one()
    instrument = session.get(Instrument, item.instrument_id)
    assert (instrument.symbol, item.user_id, item.watchlist_id) == ("AAPL", user_id, tech.id)
    assert wl.list_watchlists(session, user_id)[0].item_count == 1


def test_adding_the_same_ticker_twice_is_rejected(session, fake_market_data, user_id, tech):
    add(session, fake_market_data, user_id, tech, "AAPL")
    with pytest.raises(wl.WatchlistError, match="already in this list"):
        add(session, fake_market_data, user_id, tech, "aapl")
    assert len(session.exec(select(WatchlistItem)).all()) == 1


def test_same_ticker_can_be_in_two_lists(session, fake_market_data, user_id, tech):
    other = wl.create_watchlist(session, user_id, "Other", LIMITS)
    add(session, fake_market_data, user_id, tech, "AAPL")
    add(session, fake_market_data, user_id, other, "AAPL")
    assert len(session.exec(select(WatchlistItem)).all()) == 2
    assert len(session.exec(select(Instrument)).all()) == 1


def test_invalid_ticker_is_rejected_without_calling_the_source(
    session, fake_market_data, user_id, tech
):
    with pytest.raises(wl.WatchlistError, match="valid US ticker"):
        add(session, fake_market_data, user_id, tech, "AAPL; DROP TABLE")
    assert fake_market_data.calls == []


def test_remove_item(session, fake_market_data, user_id, tech):
    add(session, fake_market_data, user_id, tech, "AAPL")
    item = session.exec(select(WatchlistItem)).one()
    wl.remove_item(session, user_id, tech.id, item.id)
    assert session.exec(select(WatchlistItem)).all() == []
    wl.remove_item(session, user_id, tech.id, item.id)  # removing twice is harmless


def test_remove_item_from_wrong_list_does_nothing(session, fake_market_data, user_id, tech):
    other = wl.create_watchlist(session, user_id, "Other", LIMITS)
    add(session, fake_market_data, user_id, tech, "AAPL")
    item = session.exec(select(WatchlistItem)).one()
    wl.remove_item(session, user_id, other.id, item.id)
    assert len(session.exec(select(WatchlistItem)).all()) == 1


# --- F1.3 limits -----------------------------------------------------------------


def test_f1_3_list_limit(session, user_id):
    for name in ("A", "B", "C"):
        wl.create_watchlist(session, user_id, name, LIMITS)
    with pytest.raises(wl.WatchlistError, match="3 watchlists, the maximum.*max_watchlists"):
        wl.create_watchlist(session, user_id, "D", LIMITS)
    assert len(session.exec(select(Watchlist)).all()) == 3


def test_f1_3_item_limit_saves_nothing_and_fetches_nothing(
    session, fake_market_data, user_id, tech
):
    add(session, fake_market_data, user_id, tech, "AAPL")
    add(session, fake_market_data, user_id, tech, "MSFT")
    fake_market_data.calls.clear()
    with pytest.raises(wl.WatchlistError, match="2 items, the maximum.*max_items_per_list"):
        add(session, fake_market_data, user_id, tech, "NVDA")
    assert fake_market_data.calls == []
    assert len(session.exec(select(WatchlistItem)).all()) == 2
    assert session.exec(select(Instrument).where(Instrument.symbol == "NVDA")).first() is None


def test_f1_3_raised_limit_allows_more(session, fake_market_data, user_id, tech):
    bigger = WatchlistLimits(max_watchlists=3, max_items_per_list=3)
    for ticker in ("AAPL", "MSFT", "NVDA"):
        add(session, fake_market_data, user_id, tech, ticker, limits=bigger)
    assert len(session.exec(select(WatchlistItem)).all()) == 3


# --- F1.7 unknown ticker / source down while adding ------------------------------------


def test_f1_7_unknown_ticker_is_not_added(session, fake_market_data, user_id, tech):
    fake_market_data.not_found.add("ZZZZ")
    with pytest.raises(wl.WatchlistError, match="Ticker ZZZZ not found"):
        add(session, fake_market_data, user_id, tech, "ZZZZ")
    assert session.exec(select(WatchlistItem)).all() == []
    assert session.exec(select(Instrument)).all() == []


def test_new_ticker_cannot_be_added_while_source_is_down(session, fake_market_data, user_id, tech):
    fake_market_data.mode = "down"
    with pytest.raises(wl.WatchlistError, match="Couldn't check AAPL"):
        add(session, fake_market_data, user_id, tech, "AAPL")
    assert session.exec(select(WatchlistItem)).all() == []


def test_known_ticker_can_be_added_while_source_is_down(session, fake_market_data, user_id, tech):
    other = wl.create_watchlist(session, user_id, "Other", LIMITS)
    add(session, fake_market_data, user_id, other, "AAPL")
    fake_market_data.mode = "down"
    quote = wl.add_ticker(session, fake_market_data, user_id, tech.id, "AAPL", LIMITS, max_age=None)
    assert quote.status == "stale"
    assert len(session.exec(select(WatchlistItem)).all()) == 2


# --- rows, F1.4 / F1.5 / F1.6 ------------------------------------------------------


def test_rows_have_the_full_stock_row_in_added_order(session, fake_market_data, user_id, tech):
    add(session, fake_market_data, user_id, tech, "MSFT")
    add(session, fake_market_data, user_id, tech, "AAPL")
    rows = wl.watchlist_rows(session, fake_market_data, user_id, tech.id, max_age=WINDOW)
    assert [r.quote.symbol for r in rows] == ["MSFT", "AAPL"]
    q = rows[0].quote
    assert q.status == "ok"
    assert (q.day_low, q.day_high) == pytest.approx((257.60, 260.15))
    assert (q.year_low, q.year_high) == pytest.approx((186.20, 271.40))
    assert q.volume == 36_540_100
    assert q.market_cap == pytest.approx(FAKE_MARKET_CAP)
    assert q.next_earnings is not None
    assert q.fetched_at is not None


def test_f1_5_recent_prices_are_reused_and_refresh_refetches(
    session, fake_market_data, user_id, tech
):
    add(session, fake_market_data, user_id, tech, "AAPL")
    fake_market_data.calls.clear()
    wl.watchlist_rows(session, fake_market_data, user_id, tech.id, max_age=WINDOW)
    assert fake_market_data.calls == []  # less than 15 minutes old
    wl.watchlist_rows(session, fake_market_data, user_id, tech.id, max_age=None)  # Refresh
    assert fake_market_data.calls == ["AAPL"]


def test_f1_5_prices_older_than_the_window_are_refetched(session, fake_market_data, user_id, tech):
    add(session, fake_market_data, user_id, tech, "AAPL")
    quote = session.exec(select(Quote)).one()
    quote.fetched_at = quote.fetched_at - timedelta(minutes=16)
    session.add(quote)
    session.commit()
    fake_market_data.calls.clear()
    wl.watchlist_rows(session, fake_market_data, user_id, tech.id, max_age=WINDOW)
    assert fake_market_data.calls == ["AAPL"]


def test_f1_6_one_failing_ticker_does_not_spoil_the_others(
    session, fake_market_data, user_id, tech
):
    add(session, fake_market_data, user_id, tech, "AAPL")
    add(session, fake_market_data, user_id, tech, "MSFT")
    fake_market_data.down_for.add("MSFT")
    rows = wl.watchlist_rows(
        session, fake_market_data, user_id, tech.id, max_age=None, max_parallel=4
    )
    statuses = {r.quote.symbol: r.quote.status for r in rows}
    assert statuses == {"AAPL": "ok", "MSFT": "stale"}
    msft = rows[1].quote
    assert msft.last_price == pytest.approx(259.58)  # last stored value
    assert "did not respond" in msft.note


def test_parallel_refresh_fetches_every_ticker_once(session, fake_market_data, user_id):
    big = WatchlistLimits(max_watchlists=3, max_items_per_list=25)
    watchlist = wl.create_watchlist(session, user_id, "Big", big)
    tickers = [f"T{i}" for i in range(10)]
    for t in tickers:
        add(session, fake_market_data, user_id, watchlist, t, limits=big)
    fake_market_data.calls.clear()
    rows = wl.watchlist_rows(
        session, fake_market_data, user_id, watchlist.id, max_age=None, max_parallel=4
    )
    assert [r.quote.symbol for r in rows] == tickers
    assert sorted(fake_market_data.calls) == sorted(tickers)
    assert all(r.quote.status == "ok" for r in rows)
