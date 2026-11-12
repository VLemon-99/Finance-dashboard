"""F7.2 / F7.1 (story F7-S4): every page works on the signed-in user's own data.

Two users sign in with two separate browsers (test clients). Each sees only their own
watchlists, portfolio, reports and calendar tickers, and can't reach the other's rows by
guessing ids. Without a session cookie the pages keep using the default user.
"""

from datetime import UTC, date, datetime

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.config import ScryptCost
from app.db.models import (
    Instrument,
    Report,
    ReportRun,
    Spread,
    Transaction,
    User,
    Watchlist,
    WatchlistItem,
)
from app.services.users import hash_password

FAST = ScryptCost(n=2**4, r=1, p=1)
PASSWORD = "correct horse battery"  # noqa: S105 (fake test password)
WHEN = datetime(2026, 10, 30, 15, 0, tzinfo=UTC)
EXPIRY = date(2020, 1, 17)


def _option(symbol, ticker, strike):
    return Instrument(
        kind="option",
        symbol=symbol,
        underlying=ticker,
        option_type="put",
        strike=strike,
        expiration=EXPIRY,
    )


def _trade(user_id, instrument_id, side, quantity, price, **extra):
    return Transaction(
        user_id=user_id,
        instrument_id=instrument_id,
        side=side,
        quantity=quantity,
        price=price,
        trade_date=date(2019, 12, 2),
        price_source="manual",
        **extra,
    )


def _plant(engine, email, name, ticker, short_symbol, long_symbol):
    """A user with a password and one of everything: list, item, trades, spread, report."""
    with Session(engine) as s:
        user = User(email=email, password_hash=hash_password(PASSWORD, FAST))
        stock = Instrument(kind="stock", symbol=ticker)
        short_put = _option(short_symbol, ticker, 105.0)
        long_put = _option(long_symbol, ticker, 100.0)
        s.add_all([user, stock, short_put, long_put])
        s.commit()
        watchlist = Watchlist(user_id=user.id, name=f"{name} List")
        s.add(watchlist)
        s.commit()
        item = WatchlistItem(user_id=user.id, watchlist_id=watchlist.id, instrument_id=stock.id)
        stock_trade = _trade(user.id, stock.id, "bought", 10, 150.0)
        short_trade = _trade(user.id, short_put.id, "sold", 1, 2.0, underlying_price=110.0)
        long_trade = _trade(user.id, long_put.id, "bought", 1, 1.0, underlying_price=110.0)
        spread = Spread(
            user_id=user.id, short_instrument_id=short_put.id, long_instrument_id=long_put.id
        )
        run = ReportRun(user_id=user.id, label=f"{name} Run", status="done", total=1, done=1)
        s.add_all([item, stock_trade, short_trade, long_trade, spread, run])
        s.commit()
        report = Report(
            user_id=user.id,
            run_id=run.id,
            ticker=ticker,
            generated_at=WHEN,
            period_start=WHEN,
            period_end=WHEN,
            body={},
        )
        s.add(report)
        s.commit()
        return {
            "email": email,
            "name": name,
            "ticker": ticker,
            "user": user.id,
            "watchlist": watchlist.id,
            "item": item.id,
            "trade": stock_trade.id,
            "short_put": short_put.id,
            "short_symbol": short_symbol,
            "long_put": long_put.id,
            "spread": spread.id,
            "run": run.id,
            "report": report.id,
        }


def _owned_by(engine, user_id):
    tables = [Watchlist, WatchlistItem, Transaction, Spread, ReportRun, Report]
    with Session(engine) as s:
        return {
            table.__name__: sorted(
                (row.model_dump() for row in s.exec(select(table).where(table.user_id == user_id))),
                key=lambda row: row["id"],
            )
            for table in tables
        }


def _sign_in(browser, email):
    response = browser.post(
        "/login", data={"email": email, "password": PASSWORD}, follow_redirects=False
    )
    assert response.status_code == 303
    return browser


@pytest.fixture
def alice(client, engine):
    return _plant(
        engine, "alice@example.com", "Alice", "AAPL", "AAPL200117P00105000", "AAPL200117P00100000"
    )


@pytest.fixture
def bob(client, engine):
    return _plant(
        engine, "bob@example.com", "Bob", "MSFT", "MSFT200117P00105000", "MSFT200117P00100000"
    )


@pytest.fixture
def browsers(client, alice, bob):
    """Alice signed in on `client`, Bob on a second browser of the same app."""
    second = TestClient(client.app)  # the app has started already (via `client`)
    return _sign_in(client, alice["email"]), _sign_in(second, bob["email"])


def test_f7_2_each_user_sees_only_their_own_data(browsers, alice, bob):
    for browser, me, them in ((browsers[0], alice, bob), (browsers[1], bob, alice)):
        home = browser.get("/").text
        assert f"{me['name']} List" in home
        assert f"{them['name']} List" not in home
        assert me["email"] in home  # the menu names who is signed in

        portfolio = browser.get("/portfolio").text
        assert me["short_symbol"] in portfolio
        assert them["short_symbol"] not in portfolio

        history = browser.get("/reports").text
        assert f"{me['name']} Run" in history
        assert f"{them['name']} Run" not in history
        mine = browser.get("/reports", params={"ticker": me["ticker"]}).text
        assert f'/reports/{me["report"]}"' in mine
        theirs = browser.get("/reports", params={"ticker": them["ticker"]}).text
        assert f'/reports/{them["report"]}"' not in theirs

        calendar = browser.get("/calendar").text
        assert f"My tickers: {me['ticker']}." in calendar

        assert browser.get(f"/watchlists/{me['watchlist']}").status_code == 200


def test_f7_2_other_users_ids_are_not_found_and_nothing_changes(browsers, engine, alice, bob):
    for browser, them in ((browsers[0], bob), (browsers[1], alice)):
        before = _owned_by(engine, them["user"])
        wl, item = them["watchlist"], them["item"]
        not_found = [
            browser.get(f"/watchlists/{wl}"),
            browser.get(f"/watchlists/{wl}/search", params={"q": "apple"}),
            browser.post(f"/watchlists/{wl}/rename", data={"name": "Mine now"}),
            browser.post(f"/watchlists/{wl}/delete"),
            browser.post(f"/watchlists/{wl}/items", data={"ticker": "NVDA"}),
            browser.post(f"/watchlists/{wl}/items/{item}/delete"),
            browser.post("/reports/generate", data={"watchlist_id": wl}),
            browser.post(f"/portfolio/trades/{them['trade']}/delete"),
            browser.post(f"/portfolio/spreads/{them['spread']}/delete"),
            browser.get(f"/reports/{them['report']}"),
            browser.get(f"/reports/runs/{them['run']}"),
            browser.get(f"/reports/runs/{them['run']}/progress"),
            browser.post(
                f"/options/{them['ticker']}/add",
                data={"contract": them["short_symbol"], "watchlist_id": wl},
            ),
        ]
        assert [r.status_code for r in not_found] == [404] * len(not_found)
        refused = browser.post(
            f"/portfolio/positions/{them['short_put']}/settle", data={"outcome": "worthless"}
        )
        assert refused.status_code == 400
        assert _owned_by(engine, them["user"]) == before


def test_f7_2_report_started_by_a_user_is_stored_under_them(
    browsers, engine, alice, bob, report_runner
):
    report_runner.hold = True  # just record the run; the report pipeline has its own tests
    alice_browser, bob_browser = browsers
    response = alice_browser.post(
        "/reports/generate", data={"watchlist_id": alice["watchlist"]}, follow_redirects=False
    )
    assert response.status_code == 303
    run_id = int(response.headers["location"].rsplit("/", 1)[1])
    with Session(engine) as s:
        run = s.get(ReportRun, run_id)
        assert run.user_id == alice["user"]
        assert run.label == "Alice List"
    assert alice_browser.get(f"/reports/runs/{run_id}").status_code == 200
    assert bob_browser.get(f"/reports/runs/{run_id}").status_code == 404
    # Alice's running report doesn't hold up Bob's own "Generate now".
    response = bob_browser.post(
        "/reports/generate", data={"watchlist_id": bob["watchlist"]}, follow_redirects=False
    )
    assert response.headers["location"] != f"/reports/runs/{run_id}"
    with Session(engine) as s:
        bobs = s.exec(select(ReportRun).where(ReportRun.user_id == bob["user"])).all()
        assert {r.label for r in bobs} == {"Bob Run", "Bob List"}


def test_f7_2_changes_made_while_signed_in_belong_to_that_user(browsers, engine, alice, bob):
    alice_browser, bob_browser = browsers
    response = alice_browser.post(
        "/watchlists", data={"name": "Alice Second"}, follow_redirects=False
    )
    assert response.status_code == 303
    with Session(engine) as s:
        made = s.exec(select(Watchlist).where(Watchlist.name == "Alice Second")).one()
        assert made.user_id == alice["user"]
    assert "Alice Second" in alice_browser.get("/").text
    assert "Alice Second" not in bob_browser.get("/").text


def test_f7_2_without_a_cookie_pages_use_the_default_user(client, engine, alice):
    anonymous = TestClient(client.app)
    default_id = client.app.state.user_id
    home = anonymous.get("/").text
    assert "Alice List" not in home
    assert 'href="/login"' in home
    anonymous.post("/watchlists", data={"name": "Owner Picks"}, follow_redirects=False)
    with Session(engine) as s:
        made = s.exec(select(Watchlist).where(Watchlist.name == "Owner Picks")).one()
        assert made.user_id == default_id
    _sign_in(client, alice["email"])
    assert "Owner Picks" not in client.get("/").text
    assert "Alice List" in client.get("/").text


def test_f7_2_after_logout_or_with_a_bad_cookie_pages_use_the_default_user(client, engine, alice):
    _sign_in(client, alice["email"])
    assert "Alice List" in client.get("/").text
    client.post("/logout")
    assert "Alice List" not in client.get("/").text
    client.cookies.set("session", "made-up-token")
    page = client.get("/").text
    assert "Alice List" not in page
    assert 'href="/login"' in page
