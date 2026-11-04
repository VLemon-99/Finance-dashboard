"""Poor man's covered calls (SPEC F3.10): a LEAP call bought + a shorter-dated call sold.

Clock: Tue Nov 3, 2026, 10:00 AM Eastern. The fake serves the same sample prices for every
expiration date: the 245 call's mid is 16.78, the 265 call's 4.06.
"""

import re
from html import unescape

import pytest

from tests.test_web_portfolio import cells, row
from tests.test_web_spreads_expiry import AFTER_EXPIRY, link, settle, trade

LEAP = "AAPL270115C00245000"  # Jan 15, 2027 245 call
SHORT = "AAPL261120C00265000"  # Nov 20, 2026 265 call (17 days left)
NEXT_SHORT = "AAPL261218C00270000"  # Dec 18, 2026 270 call


@pytest.fixture
def web(client, options_clock):
    for when in ("2026-11-20", "2026-12-18", "2027-01-15"):
        client.get(f"/options/AAPL?expiration={when}")
    return client


def leg(html, symbol):
    match = re.search(rf'<tr data-leg="{symbol}".*?</tr>', html, re.S)
    assert match, f"no leg row for {symbol}"
    return match.group(0)


def pmcc_row(html):
    match = re.search(r'<tr [^>]*data-pmcc="\d+".*?</tr>', html, re.S)
    return match.group(0) if match else None


def test_f3_10_spec_case(web, engine):
    """Bought call 60.00, sold call 3.00, 1 contract, 30 DTE -> capital 6,000, 60.83%.
    Opened Oct 21, 30 days before the sold call's Nov 20 expiration."""
    trade(web, LEAP, "bought", 1, 60.00, day="2026-10-21")
    trade(web, SHORT, "sold", 1, 3.00, day="2026-10-21")
    assert link(web, engine, SHORT, LEAP).status_code == 303

    html = web.get("/portfolio").text
    r = pmcc_row(html)
    assert "265/245 poor man's covered call" in r
    values = cells(r)
    # The pair's line: contracts, -, -, P&L, DTE, -, -, premium, capital, annualized return
    assert values[0] == "1"
    assert values[3] == "-4,428.00 -73.80%"  # (3.00 - 4.06 + 16.78 - 60.00) x 100, of 6,000
    assert values[4] == "17"
    assert values[7:9] == ["300.00", "6,000.00"]
    # 300 / 6,000 x 365 / 30 days at entry; x 365 / 17 days left today
    assert values[9] == "60.83% locked-in (30 DTE at entry) 107.35% remaining"
    # Both legs are listed under the pair, with their own prices; neither stands alone,
    # and the sold call isn't "uncovered".
    assert f'<tr data-symbol="{SHORT}"' not in html and f'<tr data-symbol="{LEAP}"' not in html
    assert cells(leg(html, SHORT))[:3] == ["Short 1", "3.00", "4.06 mid"]
    assert cells(leg(html, LEAP))[:3] == ["Long 1", "60.00", "16.78 mid"]
    assert "uncovered" not in html


def test_shares_stay_free_for_other_covered_calls(web, engine):
    trade(web, "AAPL", "bought", 100, 250, day="2026-10-29")
    trade(web, LEAP, "bought", 1, 60.00)
    trade(web, SHORT, "sold", 1, 3.00)
    link(web, engine, SHORT, LEAP)
    trade(web, "AAPL261120C00270000", "sold", 1, 2.43)  # covered by the 100 shares
    html = web.get("/portfolio").text
    assert pmcc_row(html) is not None
    assert "covered call" in row(html, "AAPL261120C00270000")


@pytest.mark.parametrize(
    "legs,short,long,message",
    [
        (  # the bought call expires first
            [("AAPL261120C00245000", "bought", 1, 16.78), ("AAPL261218C00265000", "sold", 1, 4.06)],
            "AAPL261218C00265000",
            "AAPL261120C00245000",
            "on the same date as the one you sold (a credit spread) or later",
        ),
        (  # the sold call's strike isn't above the LEAP's
            [(LEAP, "bought", 1, 60.00), ("AAPL261120C00245000", "sold", 1, 16.78)],
            "AAPL261120C00245000",
            LEAP,
            "the call you sold must have a higher strike",
        ),
        (  # puts with different dates
            [("AAPL270115P00275000", "bought", 1, 16.33), ("AAPL261120P00250000", "sold", 1, 2.73)],
            "AAPL261120P00250000",
            "AAPL270115P00275000",
            "poor man's covered put isn't supported",
        ),
        (  # different numbers of contracts
            [(LEAP, "bought", 2, 60.00), (SHORT, "sold", 1, 3.00)],
            SHORT,
            LEAP,
            "different numbers of contracts",
        ),
    ],
)
def test_f3_10_link_rules(web, engine, legs, short, long, message):
    for symbol, side, qty, price in legs:
        trade(web, symbol, side, qty, price)
    response = link(web, engine, short, long)
    assert response.status_code == 400
    assert message in unescape(response.text)


def test_rolling_to_the_next_call_on_the_same_leap(web, engine, options_clock):
    trade(web, LEAP, "bought", 1, 60.00, day="2026-10-21")
    trade(web, SHORT, "sold", 1, 3.00, day="2026-10-21")
    link(web, engine, SHORT, LEAP)
    options_clock.now = AFTER_EXPIRY  # the sold call expires Nov 20
    html = web.get("/portfolio").text
    assert "sold call expired" in pmcc_row(html)
    settle(web, engine, SHORT, "worthless")
    html = unescape(web.get("/portfolio").text)
    assert pmcc_row(html) is None
    assert "no longer match" not in html  # a link with a closed leg simply ends
    assert cells(row(html, LEAP))[0] == "Long 1"

    trade(web, NEXT_SHORT, "sold", 1, 2.43, day="2026-11-21")
    assert link(web, engine, NEXT_SHORT, LEAP).status_code == 303
    r = pmcc_row(web.get("/portfolio").text)
    assert "270/245 poor man's covered call" in r
    assert cells(r)[4] == "27"  # Nov 21 -> Dec 18


def test_unlink(web, engine):
    trade(web, LEAP, "bought", 1, 60.00)
    trade(web, SHORT, "sold", 1, 3.00)
    link(web, engine, SHORT, LEAP)
    spread_id = re.search(r'data-pmcc="(\d+)"', web.get("/portfolio").text).group(1)
    web.post(f"/portfolio/spreads/{spread_id}/delete")
    html = web.get("/portfolio").text
    assert pmcc_row(html) is None
    assert "uncovered — no return figure" in row(html, SHORT)
