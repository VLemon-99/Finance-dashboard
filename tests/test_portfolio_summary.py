"""IMP-10: the portfolio summary (total open P&L, open positions, capital at risk).

Known answers: AAPL's fake price is 259.58, so long 10 at 250 makes +95.80. SPEC F3.6:
a cash-secured put, strike 100, premium 2.00, 1 contract -> capital 9,800; a credit spread
5 wide for a net credit of 1.00 -> capital 400.
Page clock: Tue Nov 3, 2026; the sample contracts expire Fri Nov 20. Sample mids: 250 put
2.73, 245 put 1.71, 260 call 6.31.
"""

import re
from datetime import UTC, datetime
from html import unescape

import pytest
from sqlmodel import Session, delete

from app.db.models import Quote
from app.services.options_math import capital_at_risk_cash_secured_put, premium_received
from app.services.portfolio_summary import SummaryLine, summarize
from tests.test_web_spreads_expiry import C260, P245, P250, link, trade

# --- the arithmetic ----------------------------------------------------------------------


def test_one_long_stock():
    s = summarize([SummaryLine(pnl=10 * (259.58 - 250))])
    assert (s.positions, s.total_pnl, s.capital_at_risk) == (1, 95.80, 0.0)
    assert (s.unpriced, s.uncovered_calls, s.expired) == (0, 0, 0)


def test_spec_cash_secured_put_plus_the_stock():
    capital = capital_at_risk_cash_secured_put(100, premium_received(2.00, 1), 1)
    assert capital == 9800
    s = summarize([SummaryLine(pnl=95.80), SummaryLine(pnl=50.0, capital=capital)])
    assert (s.positions, s.total_pnl, s.capital_at_risk) == (2, 145.80, 9800.0)


def test_spread_counts_once_and_adds_its_capital():
    s = summarize([SummaryLine(pnl=-2.0, capital=400.0), SummaryLine(pnl=50.0, capital=9800.0)])
    assert (s.positions, s.total_pnl, s.capital_at_risk) == (2, 48.0, 10200.0)


def test_uncovered_call_and_long_option_add_no_capital():
    s = summarize(
        [
            SummaryLine(pnl=-12.0, uncovered_call=True),
            SummaryLine(pnl=30.0),  # a long option
            SummaryLine(pnl=50.0, capital=9800.0),
        ]
    )
    assert (s.positions, s.total_pnl, s.capital_at_risk, s.uncovered_calls) == (3, 68.0, 9800, 1)


def test_unpriced_positions_are_left_out_of_the_total_and_counted():
    s = summarize([SummaryLine(pnl=95.80), SummaryLine(pnl=None)])
    assert (s.positions, s.total_pnl, s.unpriced) == (2, 95.80, 1)


def test_no_priced_position_means_no_total():
    s = summarize([SummaryLine(pnl=None)])
    assert (s.positions, s.total_pnl, s.unpriced) == (1, None, 1)


def test_expired_positions_are_left_out_of_everything():
    s = summarize([SummaryLine(pnl=95.80), SummaryLine(pnl=300.0, capital=24700.0, expired=True)])
    assert (s.positions, s.total_pnl, s.capital_at_risk, s.expired) == (1, 95.80, 0.0, 1)


def test_covered_call_without_capital_is_counted_and_named():
    s = summarize([SummaryLine(pnl=14.0, capital_unknown=True), SummaryLine(pnl=1.0, capital=400)])
    assert (s.positions, s.total_pnl, s.capital_at_risk, s.capital_unknown) == (2, 15.0, 400, 1)
    assert s.notes == [
        "1 covered call has no stock price at sale recorded, so capital at risk leaves it out"
    ]


def test_notes_name_what_is_left_out_with_plurals():
    s = summarize(
        [SummaryLine(pnl=None), SummaryLine(pnl=None), SummaryLine(pnl=1.0, uncovered_call=True)]
        + [SummaryLine(pnl=1.0, expired=True)] * 2
    )
    assert s.notes == [
        "2 positions without a current price aren't included in the P&L",
        "1 uncovered short call isn't included in capital at risk",
        "2 expired positions waiting for their outcome aren't included",
    ]


def test_empty_portfolio():
    s = summarize([])
    assert (s.positions, s.total_pnl, s.capital_at_risk) == (0, None, 0.0)


def test_totals_are_rounded_to_cents():
    s = summarize([SummaryLine(pnl=0.1), SummaryLine(pnl=0.2)])
    assert s.total_pnl == 0.30


# --- on the page ---------------------------------------------------------------------------


@pytest.fixture
def web(client, options_clock):
    client.get("/options/AAPL?expiration=2026-11-20")
    return client


def summary(web, **params):
    html = web.get("/portfolio", params=params).text
    match = re.search(r'<section class="summary"[^>]*>(.*?)</section>', html, re.S)
    assert match, "the summary section"
    text = match.group(1)

    def field(name):
        found = re.search(rf"data-{name}>(.*?)<", text, re.S)
        return found.group(1).strip() if found else None

    notes = unescape(" ".join(re.sub(r"<[^>]+>", " ", text).split()))
    return field("total-pnl"), field("positions"), field("capital"), notes, html


def test_page_long_stock_known_answer(web):
    trade(web, "AAPL", "bought", 10, 250, day="2026-10-29")
    pnl, positions, capital, _, html = summary(web)
    assert (pnl, positions, capital) == ("+95.80", "1", "$0.00")
    # Shown above the positions
    assert html.index('<section class="summary"') < html.index("<h2>Stocks</h2>")


def test_page_cash_secured_put_and_stock(web):
    trade(web, "AAPL", "bought", 10, 250, day="2026-10-29")
    trade(web, P250, "sold", 1, 3.00)  # capital 250 x 100 - 300; P&L (3.00 - 2.73) x 100
    pnl, positions, capital, _, _ = summary(web)
    assert (pnl, positions, capital) == ("+122.80", "2", "$24,700.00")


def test_page_linked_spread_is_one_position(web, engine):
    trade(web, P250, "sold", 1, 2.00, day="2026-10-06")
    trade(web, P245, "bought", 1, 1.00, day="2026-10-06")
    assert summary(web)[1] == "2"
    link(web, engine, P250, P245)
    pnl, positions, capital, _, _ = summary(web)
    # (2.00 - 1.00) - (2.73 - 1.71) = -0.02 per share
    assert (pnl, positions, capital) == ("-2.00", "1", "$400.00")


def test_page_uncovered_call_is_named(web):
    trade(web, C260, "sold", 2, 6.00)
    _, positions, capital, notes, _ = summary(web)
    assert (positions, capital) == ("1", "$0.00")
    assert "1 uncovered short call isn't included in capital at risk" in notes


def test_page_unpriced_position_is_named(web, engine, fake_market_data):
    trade(web, "AAPL", "bought", 10, 250, day="2026-10-29")
    trade(web, "MSFT", "bought", 5, 400, day="2026-10-29")
    with Session(engine) as s:
        s.exec(delete(Quote))
        s.commit()
    fake_market_data.down_for.add("MSFT")
    pnl, positions, _, notes, _ = summary(web, refresh=1)
    assert (pnl, positions) == ("+95.80", "2")
    assert "1 position without a current price isn't included in the P&L" in notes


def test_page_expired_position_is_named(web, options_clock):
    trade(web, "AAPL", "bought", 10, 250, day="2026-10-29")
    trade(web, P250, "sold", 1, 3.00)
    options_clock.now = datetime(2026, 11, 21, 15, 0, tzinfo=UTC)
    pnl, positions, capital, notes, _ = summary(web)
    assert (positions, capital) == ("1", "$0.00")
    assert "1 expired position waiting for its outcome isn't included" in notes


def test_page_without_trades_has_no_summary(web):
    assert '<section class="summary"' not in web.get("/portfolio").text
