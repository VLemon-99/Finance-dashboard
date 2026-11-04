"""The portfolio page: positions, P&L, capital at risk and returns (F3.3, F3.4, F3.5, F3.8).

Clock: Tue Nov 3, 2026, 10:00 AM Eastern. The fake stock price is 259.58; the sample
contracts expire Fri Nov 20 (17 days away). The AAPL Nov 20 250 put has bid 2.68 / ask 2.78
(mid 2.73) and IV 30%; the 260 call has mid 6.31 and IV 28%.
"""

import re
from datetime import UTC, datetime
from html import unescape

import pytest

from tests.conftest import OPTION_EXPIRATION

CHAIN_URL = f"/options/AAPL?expiration={OPTION_EXPIRATION.isoformat()}"
C260 = "AAPL261120C00260000"
C265 = "AAPL261120C00265000"
P250 = "AAPL261120P00250000"


@pytest.fixture
def web(client, options_clock):
    client.get(CHAIN_URL)  # store the sample chain, as the owner would by opening it
    return client


def trade(web, symbol, side, quantity, price, day="2026-11-03", underlying=""):
    response = web.post(
        "/portfolio/trades",
        data={
            "symbol": symbol,
            "side": side,
            "date": day,
            "quantity": str(quantity),
            "price": str(price),
            "underlying_price": str(underlying),
        },
        follow_redirects=False,
    )
    assert response.status_code == 303, response.text
    return response


def row(html, symbol):
    match = re.search(rf'<tr data-symbol="{symbol}".*?</tr>', html, re.S)
    assert match, f"no row for {symbol}"
    return match.group(0)


def cells(row_html):
    """Text of each numeric cell, tags removed and spaces squeezed."""
    raw = re.findall(r'<td class="num"[^>]*>(.*?)</td>', row_html, re.S)
    return [" ".join(re.sub(r"<[^>]+>", " ", c).split()) for c in raw]


def page(web, **params):
    return web.get("/portfolio", params=params).text


# --- F3.3: open positions -----------------------------------------------------------------


def test_f3_3_long_stock_position(web):
    trade(web, "AAPL", "bought", 10, 250, day="2026-10-29")
    values = cells(row(page(web), "AAPL"))
    # Position, avg entry, current, P&L ($ and %)
    assert values[:4] == ["Long 10", "250.00", "259.58", "+95.80 +3.83%"]


def test_f3_3_average_entry_over_several_buys(web):
    trade(web, "AAPL", "bought", 10, 250, day="2026-10-29")
    trade(web, "AAPL", "bought", 30, 254, day="2026-10-30")
    values = cells(row(page(web), "AAPL"))
    assert values[:3] == ["Long 40", "253.00", "259.58"]


def test_f3_1_short_stock_position(web):
    trade(web, "AAPL", "sold", 10, 270)
    assert cells(row(page(web), "AAPL"))[:4] == ["Short 10", "270.00", "259.58", "+104.20 +3.86%"]


def test_f3_3_short_put_with_dte_iv_delta(web):
    trade(web, P250, "sold", 2, 3.00)
    html = page(web)
    values = cells(row(html, P250))
    assert values[0] == "Short 2"
    assert values[1] == "3.00"
    assert values[2] == "2.73 mid"
    assert values[3] == "+54.00 +9.00%"  # (3.00 - 2.73) x 2 x 100; 54 / 600
    assert values[4] == "17"  # DTE
    assert values[5] == "30.0%"  # IV
    assert re.fullmatch(r"-0\.\d{3}", values[6])  # put delta is negative


def test_f3_3_closed_position_shows_realized_pnl(web):
    trade(web, "AAPL", "bought", 10, 250, day="2026-10-29")
    trade(web, "AAPL", "sold", 10, 259.58)
    html = page(web)
    assert "No open stock positions." in html
    closed = row(html.split("<h2>Closed positions</h2>")[1], "AAPL")
    assert cells(closed) == ["Long 10", "250.00", "259.58", "+95.80", "+3.83%"]


def test_partial_close_shows_realized_so_far(web):
    trade(web, "AAPL", "bought", 10, 250, day="2026-10-29")
    trade(web, "AAPL", "sold", 4, 260)
    assert "realized so far +40.00" in row(page(web), "AAPL")


def test_long_option_has_no_return_figures(web):
    trade(web, C260, "bought", 1, 6.00)
    values = cells(row(page(web), C260))
    assert values[0] == "Long 1"
    assert values[7:10] == ["—", "—", "—"]  # premium, capital, returns


# --- F3.4 / F3.5: capital at risk and annualized returns -------------------------------------


def test_f3_4_cash_secured_put_locked_in_and_remaining(web):
    # Sold Oct 30 (21 days before expiry); today 17 days are left.
    trade(web, P250, "sold", 2, 3.00, day="2026-10-30")
    html = page(web)
    r = row(html, P250)
    assert "cash-secured put" in r
    values = cells(r)
    assert values[7] == "600.00"  # premium: 3.00 x 100 x 2
    assert values[8] == "49,400.00"  # 250 x 100 x 2 - 600
    # 600 / 49,400 x 365 / 21 days, then x 365 / 17 days
    assert values[9] == "21.11% locked-in (21 DTE at entry) 26.08% remaining"


def test_f3_5_covered_call_uses_stock_price_at_sale(web):
    trade(web, "AAPL", "bought", 100, 180, day="2026-10-29")  # cost basis is NOT used
    trade(web, C260, "sold", 1, 6.00, underlying=259.58)
    r = row(page(web), C260)
    assert "covered call" in r
    values = cells(r)
    assert values[7:9] == ["600.00", "25,958.00"]  # 259.58 x 100 x 1
    # 600 / 25,958 x 365 / 17 (opened today, so both are the same)
    assert values[9] == "49.63% locked-in (17 DTE at entry) 49.63% remaining"


# --- F3.8: uncovered calls -------------------------------------------------------------------


@pytest.mark.parametrize("shares", [0, 50, 199])
def test_f3_8_short_call_without_enough_shares_is_uncovered(web, shares):
    if shares:
        trade(web, "AAPL", "bought", shares, 250)
    trade(web, C260, "sold", 2, 6.00, underlying=259.58)
    r = row(page(web), C260)
    assert "uncovered — no return figure" in r
    assert "uncovered call" in r
    assert "%" not in "".join(cells(r)[8:])  # no return figure at all


def test_shares_cover_the_oldest_calls_first(web):
    trade(web, "AAPL", "bought", 100, 250, day="2026-10-29")
    trade(web, C265, "sold", 1, 4.00, day="2026-10-30", underlying=259.58)
    trade(web, C260, "sold", 1, 6.00, underlying=259.58)
    html = page(web)
    assert "covered call" in row(html, C265)
    assert "uncovered — no return figure" in row(html, C260)


def test_short_stock_does_not_cover_calls(web):
    trade(web, "AAPL", "sold", 100, 260)
    trade(web, C260, "sold", 1, 6.00, underlying=259.58)
    assert "uncovered — no return figure" in row(page(web), C260)


# --- expiry flags (F3.9, first part) ---------------------------------------------------------


def test_expiring_today_flag(web, options_clock):
    trade(web, P250, "sold", 1, 3.00)
    options_clock.now = datetime(2026, 11, 20, 15, 0, tzinfo=UTC)  # 10 AM ET, expiry day
    r = row(page(web), P250)
    assert "expiring today" in r
    values = cells(r)
    assert values[4] == "0"
    assert values[9].endswith("— remaining")  # none on the last day (would divide by 0)


def test_expired_position_is_marked_and_not_refreshed(web, options_clock, fake_market_data):
    trade(web, P250, "sold", 1, 3.00)
    options_clock.now = datetime(2026, 11, 21, 15, 0, tzinfo=UTC)
    calls = len(fake_market_data.chain_calls)
    r = row(page(web, refresh=1), P250)
    assert fake_market_data.chain_calls[calls:] == []
    assert 'data-status="expired"' in r and "expired" in cells(r)[4]


# --- freshness (F1.5, F1.6) ----------------------------------------------------------------


def test_refresh_fetches_and_failures_show_stale(web, fake_market_data):
    trade(web, "AAPL", "bought", 10, 250)
    trade(web, P250, "sold", 1, 3.00)
    page(web)
    chain_calls, stock_calls = len(fake_market_data.chain_calls), len(fake_market_data.calls)
    page(web)
    assert len(fake_market_data.chain_calls) == chain_calls  # reused
    fake_market_data.mode = "down"
    html = page(web, refresh=1)
    assert len(fake_market_data.calls) > stock_calls
    assert 'data-status="stale"' in row(html, "AAPL")
    assert 'data-status="stale"' in row(html, P250)
    assert "259.58" in row(html, "AAPL")  # last stored price still shown


def test_other_users_trades_are_not_shown(web, engine):
    from sqlmodel import Session, select

    from app.db.models import Transaction, User

    trade(web, "AAPL", "bought", 10, 250)
    with Session(engine) as session:
        other = User(email="someone.else@mail.test")
        session.add(other)
        session.commit()
        mine = session.exec(select(Transaction)).one()
        mine.user_id = other.id
        session.add(mine)
        session.commit()
    html = page(web)
    assert 'data-symbol="AAPL"' not in html
    assert "No open stock positions." in html


# --- options grouped per ticker (owner's layout, 2026-11-04) ----------------------------------


def ticker_sections(html):
    """(ticker, summary text) for each options section, in page order."""
    found = re.findall(
        r'<details class="ticker-group" open data-ticker="([A-Z-]+)">\s*<summary>(.*?)</summary>',
        html,
        re.S,
    )
    return [(t, unescape(" ".join(re.sub(r"<[^>]+>", " ", s).split()))) for t, s in found]


def test_options_grouped_per_ticker_a_to_z_with_totals(web):
    web.get(f"/options/MSFT?expiration={OPTION_EXPIRATION.isoformat()}")
    trade(web, "MSFT261120C00260000", "bought", 1, 6.00)  # mid 6.31 -> +31.00
    trade(web, P250, "sold", 2, 3.00)  # mid 2.73 -> +54.00
    trade(web, C265, "bought", 1, 4.00)  # mid 4.06 -> +6.00
    html = page(web)
    assert ticker_sections(html) == [
        ("AAPL", "AAPL · 3 contracts · P&L +60.00"),
        ("MSFT", "MSFT · 1 contract · P&L +31.00"),
    ]
    aapl = html.split('data-ticker="AAPL"')[1].split("</details>")[0]
    assert P250 in aapl and C265 in aapl and "MSFT" not in aapl
    # Inside a ticker: by expiration, then calls before puts, then strike.
    assert aapl.index(C265) < aapl.index(P250)


def test_linked_pair_counts_in_its_ticker_total(web, engine):
    from tests.test_web_spreads_expiry import link

    trade(web, P250, "sold", 1, 3.00)  # +27.00 at mid 2.73
    trade(web, "AAPL261120P00245000", "bought", 1, 1.00)  # +71.00 at mid 1.71
    link(web, engine, P250, "AAPL261120P00245000")
    assert ticker_sections(page(web)) == [("AAPL", "AAPL · 2 contracts · P&L +98.00")]


def test_ticker_total_is_dash_when_a_price_is_missing(web, fake_market_data):
    trade(web, P250, "sold", 1, 3.00)
    fake_market_data.mode = "down"
    web.get(f"/options/AAPL?expiration={OPTION_EXPIRATION.isoformat()}")
    from sqlmodel import Session, delete

    from app.db.models import OptionQuote

    with Session(web.app.state.engine) as session:
        session.exec(delete(OptionQuote))
        session.commit()
    assert ticker_sections(page(web, refresh=1)) == [("AAPL", "AAPL · 1 contract · P&L —")]
