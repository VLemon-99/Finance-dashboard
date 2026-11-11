"""One user can never see or change another user's data through the pages (SPEC F7.1;
improvement IMP-3).

Only the default user can sign in for now, but every page already looks rows up by user.
A second user's watchlist, trades, spread and report are planted straight in the database;
the owner's pages must answer "not found" (or refuse) for their ids, change none of them, and
never list them.
"""

from datetime import UTC, date, datetime

import pytest
from sqlmodel import Session, select

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

OTHER_LIST = "Someone Elses Picks"
OTHER_LABEL = "Someone Elses Run"
WHEN = datetime(2026, 10, 30, 15, 0, tzinfo=UTC)


def option(symbol, option_type, strike, expiration):
    return Instrument(
        kind="option",
        symbol=symbol,
        underlying="AAPL",
        option_type=option_type,
        strike=strike,
        expiration=expiration,
    )


def trade(user_id, instrument_id, side, quantity, price, **extra):
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


@pytest.fixture
def other(client, engine):
    """The second user's rows (ids by name). `client` first, so the owner exists already."""
    with Session(engine) as session:
        user = User(email="someone.else@mail.test")
        aapl = Instrument(kind="stock", symbol="AAPL")
        short_put = option("AAPL200117P00105000", "put", 105.0, date(2020, 1, 17))
        long_put = option("AAPL200117P00100000", "put", 100.0, date(2020, 1, 17))
        session.add_all([user, aapl, short_put, long_put])
        session.commit()
        watchlist = Watchlist(user_id=user.id, name=OTHER_LIST)
        session.add(watchlist)
        session.commit()
        item = WatchlistItem(user_id=user.id, watchlist_id=watchlist.id, instrument_id=aapl.id)
        stock_trade = trade(user.id, aapl.id, "bought", 10, 150.0)
        short_trade = trade(user.id, short_put.id, "sold", 1, 2.0, underlying_price=110.0)
        long_trade = trade(user.id, long_put.id, "bought", 1, 1.0, underlying_price=110.0)
        spread = Spread(
            user_id=user.id, short_instrument_id=short_put.id, long_instrument_id=long_put.id
        )
        run = ReportRun(user_id=user.id, label=OTHER_LABEL, status="done", total=1, done=1)
        session.add_all([item, stock_trade, short_trade, long_trade, spread, run])
        session.commit()
        report = Report(
            user_id=user.id,
            run_id=run.id,
            ticker="AAPL",
            generated_at=WHEN,
            period_start=WHEN,
            period_end=WHEN,
            body={},
        )
        session.add(report)
        session.commit()
        return {
            "user": user.id,
            "watchlist": watchlist.id,
            "item": item.id,
            "trade": stock_trade.id,
            "short_put": short_put.id,
            "long_put": long_put.id,
            "spread": spread.id,
            "run": run.id,
            "report": report.id,
        }


def owned_by(engine, user_id):
    """Everything a user owns, as plain values, to compare before and after."""
    tables = [Watchlist, WatchlistItem, Transaction, Spread, ReportRun, Report]
    with Session(engine) as session:
        return {
            table.__name__: sorted(
                (
                    row.model_dump()
                    for row in session.exec(select(table).where(table.user_id == user_id))
                ),
                key=lambda row: row["id"],
            )
            for table in tables
        }


def test_other_users_ids_are_not_found(client, engine, other):
    before = owned_by(engine, other["user"])
    wl, item = other["watchlist"], other["item"]
    not_found = [
        client.get(f"/watchlists/{wl}"),
        client.get(f"/watchlists/{wl}/search", params={"q": "apple"}),
        client.post(f"/watchlists/{wl}/rename", data={"name": "Mine now"}),
        client.post(f"/watchlists/{wl}/delete"),
        client.post(f"/watchlists/{wl}/items", data={"ticker": "MSFT"}),
        client.post(f"/watchlists/{wl}/items/{item}/delete"),
        client.post("/reports/generate", data={"watchlist_id": wl}),
        client.post(f"/portfolio/trades/{other['trade']}/delete"),
        client.post(f"/portfolio/spreads/{other['spread']}/delete"),
        client.get(f"/reports/{other['report']}"),
        client.get(f"/reports/runs/{other['run']}"),
        client.get(f"/reports/runs/{other['run']}/progress"),
        client.post(
            "/options/AAPL/add",
            data={"contract": "AAPL200117P00105000", "watchlist_id": wl},
        ),
    ]
    assert [r.status_code for r in not_found] == [404] * len(not_found)
    assert owned_by(engine, other["user"]) == before


def test_other_users_positions_and_lists_cant_be_used(client, engine, other):
    before = owned_by(engine, other["user"])
    # The owner holds none of these, so they're refused (instruments are shared market
    # data, so "not open" rather than "not found").
    refused = [
        client.post(
            f"/portfolio/positions/{other['short_put']}/settle", data={"outcome": "worthless"}
        ),
        client.post(
            "/portfolio/spreads",
            data={"short_id": other["short_put"], "long_id": other["long_put"]},
        ),
    ]
    assert [r.status_code for r in refused] == [400, 400]
    assert "That option position isn&#39;t open." in refused[0].text
    assert "Pick two open option positions to link." in refused[1].text
    assert owned_by(engine, other["user"]) == before
    with Session(engine) as session:  # and nothing was recorded for the owner either
        owner_rows = session.exec(
            select(Transaction).where(Transaction.user_id != other["user"])
        ).all()
        assert owner_rows == []


def test_other_users_rows_never_listed(client, other):
    home = client.get("/").text
    assert OTHER_LIST not in home and "No watchlists yet" in home
    portfolio = client.get("/portfolio").text
    assert "AAPL200117P00105000" not in portfolio and "150.00" not in portfolio
    history = client.get("/reports").text
    assert OTHER_LABEL not in history
    by_ticker = client.get("/reports", params={"ticker": "AAPL"}).text
    assert f'/reports/{other["report"]}"' not in by_ticker and "No AAPL reports" in by_ticker
    by_day = client.get("/reports", params={"day": "2026-10-30"}).text
    assert OTHER_LABEL not in by_day
    calendar = client.get("/calendar").text
    assert "AAPL" not in calendar  # its tickers aren't the owner's tickers (F5.1)


def test_other_users_item_cant_be_removed_through_my_own_list(client, engine, other):
    before = owned_by(engine, other["user"])
    response = client.post("/watchlists", data={"name": "Mine"}, follow_redirects=False)
    mine = response.headers["location"]
    response = client.post(f"{mine}/items/{other['item']}/delete", follow_redirects=False)
    assert response.status_code == 303  # treated as already gone: nothing to do
    assert owned_by(engine, other["user"]) == before
