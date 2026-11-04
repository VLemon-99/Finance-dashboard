"""Credit spreads (F3.7) and expiry outcomes (F3.9) on the portfolio page.

Clock: Tue Nov 3, 2026, 10:00 AM Eastern; the sample contracts expire Fri Nov 20 (17 days).
Sample mids: 250 put 2.73, 245 put 1.71; 265 call 4.06, 270 call 2.43.
"""

import re
from datetime import UTC, datetime
from html import unescape

import pytest
from sqlmodel import Session, select

from app.db.models import Spread, Transaction
from tests.conftest import OPTION_EXPIRATION
from tests.test_web_portfolio import cells, row

CHAIN_URL = f"/options/AAPL?expiration={OPTION_EXPIRATION.isoformat()}"
P250, P245 = "AAPL261120P00250000", "AAPL261120P00245000"
C260, C265, C270 = "AAPL261120C00260000", "AAPL261120C00265000", "AAPL261120C00270000"
AFTER_EXPIRY = datetime(2026, 11, 21, 15, 0, tzinfo=UTC)


@pytest.fixture
def web(client, options_clock):
    client.get(CHAIN_URL)
    return client


def trade(web, symbol, side, quantity, price, day="2026-11-03", underlying="259.58"):
    data = {
        "symbol": symbol,
        "side": side,
        "date": day,
        "quantity": str(quantity),
        "price": str(price),
        "underlying_price": underlying,
    }
    response = web.post("/portfolio/trades", data=data, follow_redirects=False)
    assert response.status_code == 303, response.text


def instrument_id(engine, symbol):
    from app.db.models import Instrument

    with Session(engine) as session:
        return session.exec(select(Instrument).where(Instrument.symbol == symbol)).one().id


def link(web, engine, short, long):
    return web.post(
        "/portfolio/spreads",
        data={"short_id": instrument_id(engine, short), "long_id": instrument_id(engine, long)},
        follow_redirects=False,
    )


def spread_row(html):
    match = re.search(r'<tr [^>]*data-spread="\d+".*?</tr>', html, re.S)
    return match.group(0) if match else None


# --- F3.7: credit spreads ----------------------------------------------------------------


def test_f3_6_f3_7_put_credit_spread_spec_case(web, engine):
    """SPEC F3.6: strikes 5 apart, net credit 1.00, 1 contract, 45 DTE -> capital 400,
    annualized 202.78%. Opened Oct 6, 45 days before the Nov 20 expiration."""
    trade(web, P250, "sold", 1, 2.00, day="2026-10-06")
    trade(web, P245, "bought", 1, 1.00, day="2026-10-06")
    html = web.get("/portfolio").text
    assert "Link a credit spread" in html
    assert link(web, engine, P250, P245).status_code == 303

    html = web.get("/portfolio").text
    sp = spread_row(html)
    assert "250/245 put credit spread" in sp
    assert "net credit 1.00, cost to close 1.02" in sp  # 2.00 - 1.00; 2.73 - 1.71
    values = cells(sp)
    # The pair's line: contracts, -, -, P&L, DTE, -, -, premium, capital, annualized return
    assert values[0] == "1"
    assert values[3] == "-2.00 -2.00%"
    assert values[4] == "17"
    assert values[7:9] == ["100.00", "400.00"]
    # 100 / 400 x 365 / 45 days at entry, then x 365 / 17 days left
    assert values[9] == "202.78% locked-in (45 DTE at entry) 536.76% remaining"
    # Both legs are shown under the pair (not as single contracts), in the AAPL section.
    assert f'<tr data-symbol="{P250}"' not in html and f'<tr data-symbol="{P245}"' not in html
    assert f'<tr data-leg="{P250}"' in html and f'<tr data-leg="{P245}"' in html
    section = html.split('data-ticker="AAPL"')[1].split("</details>")[0]
    assert sp in section


def test_call_credit_spread_needs_no_shares_and_uses_none(web, engine):
    trade(web, "AAPL", "bought", 100, 250, day="2026-10-29")
    trade(web, C265, "sold", 1, 4.06)
    trade(web, C270, "bought", 1, 2.43)
    assert link(web, engine, C265, C270).status_code == 303
    trade(web, C260, "sold", 1, 6.31)  # the 100 shares still cover this one
    html = web.get("/portfolio").text
    assert "265/270 call credit spread" in spread_row(html)
    assert "covered call" in row(html, C260)


@pytest.mark.parametrize(
    "trades,short,long,message",
    [
        (
            [(P250, "sold", 2, 2.73), (P245, "bought", 1, 1.71)],
            P250,
            P245,
            "different numbers of contracts",
        ),
        (
            [(P250, "sold", 1, 1.00), (P245, "bought", 1, 1.71)],
            P250,
            P245,
            "net debit, not a credit",
        ),
        (
            [(P245, "sold", 1, 3.00), (P250, "bought", 1, 1.00)],
            P245,
            P250,
            "the put you bought has the lower strike",
        ),
        (
            [(P250, "sold", 1, 3.00), (C270, "bought", 1, 1.00)],
            P250,
            C270,
            "both calls or both puts",
        ),
        (
            [(P250, "sold", 1, 3.00), (P245, "sold", 1, 1.00)],
            P250,
            P245,
            "a contract you sold and a contract you bought",
        ),
    ],
)
def test_f3_7_link_rules(web, engine, trades, short, long, message):
    for symbol, side, qty, price in trades:
        trade(web, symbol, side, qty, price)
    response = link(web, engine, short, long)
    assert response.status_code == 400
    assert message in unescape(response.text)
    with Session(engine) as session:
        assert session.exec(select(Spread)).all() == []


def test_link_requires_open_positions(web, engine):
    trade(web, P250, "sold", 1, 2.73)
    response = web.post("/portfolio/spreads", data={"short_id": 999, "long_id": 998})
    assert response.status_code == 400
    assert "two open option positions" in response.text


def test_leg_already_in_a_spread(web, engine):
    trade(web, P250, "sold", 1, 3.00)
    trade(web, P245, "bought", 1, 1.00)
    trade(web, "AAPL261120P00255000", "bought", 1, 1.00)
    link(web, engine, P250, P245)
    response = link(web, engine, P250, "AAPL261120P00255000")
    assert response.status_code == 400
    assert "already linked" in response.text


def test_legs_no_longer_matching_are_shown_separately(web, engine):
    trade(web, P250, "sold", 2, 3.00)
    trade(web, P245, "bought", 2, 1.00)
    link(web, engine, P250, P245)
    trade(web, P250, "bought", 1, 2.73)  # bought back one of the two sold puts
    html = unescape(web.get("/portfolio").text)
    assert spread_row(html) is None
    assert "legs no longer match" in html
    assert cells(row(html, P250))[0] == "Short 1 realized so far +27.00"  # (3.00 - 2.73) x 100


def test_unlink(web, engine):
    trade(web, P250, "sold", 1, 3.00)
    trade(web, P245, "bought", 1, 1.00)
    link(web, engine, P250, P245)
    with Session(engine) as session:
        spread_id = session.exec(select(Spread)).one().id
    response = web.post(f"/portfolio/spreads/{spread_id}/delete", follow_redirects=False)
    assert response.status_code == 303
    html = web.get("/portfolio").text
    assert spread_row(html) is None
    assert "cash-secured put" in row(html, P250)
    assert web.post(f"/portfolio/spreads/{spread_id}/delete").status_code == 404


def test_no_link_form_without_both_kinds(web):
    trade(web, P250, "sold", 1, 3.00)
    assert "Link a credit spread" not in web.get("/portfolio").text


# --- F3.9: expiry --------------------------------------------------------------------------


def settle(web, engine, symbol, outcome):
    return web.post(
        f"/portfolio/positions/{instrument_id(engine, symbol)}/settle",
        data={"outcome": outcome},
        follow_redirects=False,
    )


def closed_rows(html):
    return html.split("<h2>Closed positions</h2>")[1]


def test_f3_9_expired_asks_for_the_outcome(web, options_clock):
    trade(web, P250, "sold", 1, 3.00)
    options_clock.now = AFTER_EXPIRY
    html = web.get("/portfolio").text
    assert "Expired: how did these end?" in html
    assert 'aria-label="AAPL Nov 20, 2026 250 put expired worthless"' in html
    assert 'aria-label="AAPL Nov 20, 2026 250 put assigned"' in html
    assert "out of the money" in html  # last stock price 259.58 vs strike 250


def test_f3_9_expired_worthless(web, engine, options_clock):
    trade(web, P250, "sold", 1, 3.00)
    options_clock.now = AFTER_EXPIRY
    assert settle(web, engine, P250, "worthless").status_code == 303
    html = web.get("/portfolio").text
    assert "Expired: how did these end?" not in html
    closed = row(closed_rows(html), P250)
    assert "expired" in closed
    assert cells(closed)[3] == "+300.00"
    with Session(engine) as session:
        closing = session.exec(select(Transaction).where(Transaction.reason == "expired")).one()
        assert (closing.side, closing.price, str(closing.trade_date)) == ("bought", 0, "2026-11-20")


def test_f3_9_short_put_assigned_buys_shares_at_the_strike(web, engine, options_clock):
    trade(web, P250, "sold", 2, 3.00)
    options_clock.now = AFTER_EXPIRY
    assert settle(web, engine, P250, "assigned").status_code == 303
    html = web.get("/portfolio").text
    assert cells(row(closed_rows(html), P250))[3] == "+600.00"  # premium kept
    assert cells(row(html, "AAPL"))[:2] == ["Long 200", "250.00"]


def test_f3_9_covered_call_assigned_sells_the_shares(web, engine, options_clock):
    trade(web, "AAPL", "bought", 100, 250, day="2026-10-29")
    trade(web, C260, "sold", 1, 6.00)
    options_clock.now = AFTER_EXPIRY
    settle(web, engine, C260, "assigned")
    closed = closed_rows(web.get("/portfolio").text)
    assert cells(row(closed, "AAPL"))[3] == "+1,000.00"  # sold at 260, bought at 250
    assert cells(row(closed, C260))[3] == "+600.00"


def test_f3_9_long_call_exercised_buys_shares(web, engine, options_clock):
    trade(web, C260, "bought", 1, 6.00)
    options_clock.now = AFTER_EXPIRY
    html = web.get("/portfolio").text
    assert 'aria-label="AAPL Nov 20, 2026 260 call exercised"' in html
    settle(web, engine, C260, "assigned")
    html = web.get("/portfolio").text
    assert cells(row(html, "AAPL"))[:2] == ["Long 100", "260.00"]
    assert cells(row(closed_rows(html), C260))[3] == "-600.00"


def test_settle_rules(web, engine, options_clock):
    trade(web, P250, "sold", 1, 3.00)
    response = settle(web, engine, P250, "worthless")
    assert response.status_code == 400 and "hasn't expired yet" in unescape(response.text)
    options_clock.now = AFTER_EXPIRY
    assert settle(web, engine, P250, "vanished").status_code == 400
    assert settle(web, engine, P250, "worthless").status_code == 303
    response = settle(web, engine, P250, "worthless")  # already settled
    assert response.status_code == 400 and "isn't open" in unescape(response.text)


def test_expired_spread_legs_each_ask_for_an_outcome(web, engine, options_clock):
    trade(web, P250, "sold", 1, 3.00)
    trade(web, P245, "bought", 1, 1.00)
    link(web, engine, P250, P245)
    options_clock.now = AFTER_EXPIRY
    html = web.get("/portfolio").text
    assert "expired" in spread_row(html)
    assert "250 put expired worthless" in html and "245 put expired worthless" in html
    settle(web, engine, P250, "worthless")
    settle(web, engine, P245, "worthless")
    html = web.get("/portfolio").text
    assert spread_row(html) is None
    assert cells(row(closed_rows(html), P250))[3] == "+300.00"
    assert cells(row(closed_rows(html), P245))[3] == "-100.00"
