"""Recording trades: the Bought/Sold form with an automatic price (F3.1, F3.2, F2.5b).

The clock is frozen at Tue Nov 3, 2026, 10:00 AM Eastern. The stock sample's last trading
days are Thu Oct 29 (close 258.04) and Fri Oct 30 (close 259.58); the fake's current
price is also 259.58. The sample contracts expire Nov 20.
"""

import re
from datetime import date
from html import unescape

import pytest
from sqlmodel import Session, select

from app.db.models import Transaction
from tests.conftest import OPTION_EXPIRATION

CHAIN_URL = f"/options/AAPL?expiration={OPTION_EXPIRATION.isoformat()}"
C260 = "AAPL261120C00260000"
P250 = "AAPL261120P00250000"


@pytest.fixture
def web(client, options_clock):
    return client


def form(web, symbol="AAPL", side="bought", **params):
    return web.get("/portfolio/trade", params={"symbol": symbol, "side": side, **params})


def field(html, name):
    match = re.search(rf'name="{name}" value="([^"]*)"', html)
    return match.group(1) if match else None


def save(web, symbol="AAPL", side="bought", **data):
    data = {"symbol": symbol, "side": side, "date": "2026-11-03", "quantity": "10", **data}
    return web.post("/portfolio/trades", data=data, follow_redirects=False)


def trades(engine):
    with Session(engine) as session:
        return session.exec(select(Transaction)).all()


# --- F3.1: Bought / Sold, date, quantity ---------------------------------------------------


def test_menu_has_portfolio(web):
    assert '<a href="/portfolio">Portfolio</a>' in web.get("/").text
    assert "No trades yet" in web.get("/portfolio").text


def test_f3_1_bought_stock_defaults_to_today(web, engine):
    html = form(web).text
    assert "Record a trade: AAPL" in html
    assert field(html, "date") == "2026-11-03"
    assert 'value="bought" checked' in html
    response = save(web, price="259.58", suggested_price="259.58")
    assert response.status_code == 303
    assert response.headers["location"] == "/portfolio"
    [trade] = trades(engine)
    assert (trade.side, trade.quantity, trade.price) == ("bought", 10, 259.58)
    assert trade.trade_date == date(2026, 11, 3)
    assert trade.price_source == "auto"
    assert trade.underlying_price is None
    assert trade.user_id == 1
    html = web.get("/portfolio").text
    assert f'data-trade="{trade.id}"' in html and "Bought" in html


def test_f3_1_sold_with_no_position_is_recorded(web, engine):
    assert form(web, side="sold").text.count('value="sold" checked') == 1
    assert save(web, side="sold", price="259.58").status_code == 303
    assert trades(engine)[0].side == "sold"


@pytest.mark.parametrize("quantity", ["0", "-5", "1.5", "abc", "", "2000000"])
def test_quantity_must_be_a_whole_number(web, engine, quantity):
    response = save(web, quantity=quantity, price="10")
    assert response.status_code == 400
    assert "number of shares" in response.text
    assert trades(engine) == []


@pytest.mark.parametrize("side", ["", "shorted", "BOUGHT"])
def test_side_must_be_bought_or_sold(web, engine, side):
    assert save(web, side=side, price="10").status_code == 400
    assert trades(engine) == []


def test_future_date_rejected(web, engine):
    response = save(web, date="2026-11-04", price="10")
    assert response.status_code == 400
    assert "can't be in the future" in unescape(response.text)
    assert "can't be in the future" in unescape(form(web, date="2026-11-04").text)
    assert trades(engine) == []


@pytest.mark.parametrize("symbol", ["ZZZZQ", "not a ticker"])
def test_unknown_or_invalid_ticker(web, fake_market_data, symbol):
    fake_market_data.not_found.add("ZZZZQ")
    response = form(web, symbol=symbol)
    assert response.status_code == 400
    assert "not found" in response.text or "valid US ticker" in response.text
    assert "Save trade" not in response.text


def test_unknown_contract_must_come_from_a_chain(web):
    response = form(web, symbol=C260)  # chain never opened, so nothing stored
    assert response.status_code == 400
    assert "Open its option chain" in response.text


# --- F3.2: the automatic price -----------------------------------------------------------


def test_f3_2_today_stock_uses_current_price(web):
    html = form(web).text
    assert field(html, "price") == "259.58"
    assert "Filled in: last price" in html


def test_f3_2_today_option_uses_mid_and_stock_price(web, engine):
    web.get(CHAIN_URL)
    html = form(web, symbol=C260, side="sold").text
    assert "Record a trade: AAPL Nov 20, 2026 260 call" in html
    assert field(html, "price") == "6.31"  # (6.26 + 6.36) / 2
    assert field(html, "underlying_price") == "259.58"
    assert "Filled in: mid" in html
    response = save(
        web,
        C260,
        "sold",
        quantity="2",
        price="6.31",
        underlying_price="259.58",
        suggested_price="6.31",
    )
    assert response.status_code == 303
    [trade] = trades(engine)
    assert (trade.quantity, trade.price, trade.underlying_price) == (2, 6.31, 259.58)


def test_f3_2_option_without_bid_uses_last(web):
    web.get(CHAIN_URL)
    html = form(web, symbol="AAPL261120C00350000").text  # no bid: mid not available
    assert field(html, "price") == "0.01"
    assert "Filled in: last price" in html


def test_f3_2_override_price_is_marked_typed_in(web, engine):
    save(web, price="250", suggested_price="259.58")
    assert trades(engine)[0].price_source == "manual"
    assert "typed in" in web.get("/portfolio").text


def test_f3_2_past_date_uses_that_days_close(web):
    html = form(web, date="2026-10-29").text
    assert field(html, "date") == "2026-10-29"
    assert field(html, "price") == "258.04"
    assert "closing price on Oct 29, 2026" in html


def test_f3_2_past_date_for_an_option(web, fake_market_data):
    web.get(CHAIN_URL)
    fake_market_data.option_closes[(P250, date(2026, 10, 30))] = 3.15
    html = form(web, symbol=P250, date="2026-10-30").text
    assert field(html, "price") == "3.15"
    assert field(html, "underlying_price") == "259.58"  # the stock's close that day


def test_f3_2_no_price_available_asks_for_one(web, engine):
    html = unescape(form(web, date="2026-11-01").text)  # a Sunday
    assert field(html, "price") == ""
    assert "Nov 1, 2026 was a Sunday: the market was closed." in html
    # A weekday without a price (e.g. a holiday, or before the source's history).
    html = unescape(form(web, date="2024-05-01").text)
    assert "has no closing price for May 1, 2024. Please enter the price." in html
    response = save(web, date="2026-11-01", price="")
    assert response.status_code == 400
    assert "Please enter the price." in response.text
    assert trades(engine) == []


def test_f3_2_source_down_asks_for_price(web, fake_market_data):
    fake_market_data.mode = "down"
    web.get("/lookup?ticker=AAPL")  # never stored -> can't check the ticker
    assert form(web).status_code == 400
    fake_market_data.mode = "ok"
    web.get("/lookup?ticker=AAPL")
    fake_market_data.mode = "down"
    html = unescape(form(web, date="2026-10-29").text)
    assert "Couldn't get the closing price" in html and field(html, "price") == ""


def test_typed_values_kept_after_an_error(web):
    response = save(web, quantity="abc", price="123.45")
    assert field(response.text, "price") == "123.45"
    assert field(response.text, "quantity") == "abc"


# --- option trade rules ---------------------------------------------------------------------


def test_sold_call_needs_the_stock_price(web, engine):
    web.get(CHAIN_URL)
    response = save(web, C260, "sold", quantity="1", price="6.31", underlying_price="")
    assert response.status_code == 400
    assert "stock price at the time of the sale" in response.text
    assert trades(engine) == []
    # Puts and bought calls don't need it.
    assert save(web, P250, "sold", quantity="1", price="2.73").status_code == 303
    assert save(web, C260, "bought", quantity="1", price="6.31").status_code == 303


def test_option_trade_after_expiration_rejected(web, engine, options_clock):
    from datetime import UTC, datetime

    web.get(CHAIN_URL)
    options_clock.now = datetime(2026, 11, 23, 15, 0, tzinfo=UTC)
    response = save(web, P250, "sold", date="2026-11-23", quantity="1", price="1")
    assert response.status_code == 400
    assert "expired on Nov 20, 2026" in response.text
    # A trade dated on or before the expiration is still fine.
    assert save(web, P250, "sold", date="2026-11-20", quantity="1", price="1").status_code == 303


# --- deleting trades ---------------------------------------------------------------------------


def test_delete_trade(web, engine):
    save(web, price="259.58")
    [trade] = trades(engine)
    response = web.post(f"/portfolio/trades/{trade.id}/delete", follow_redirects=False)
    assert response.status_code == 303
    assert trades(engine) == []
    assert web.post(f"/portfolio/trades/{trade.id}/delete").status_code == 404


# --- F2.5b: Bought / Sold everywhere ------------------------------------------------------------


def test_f2_5b_bought_sold_links(web):
    chain = web.get(CHAIN_URL).text
    assert f'href="/portfolio/trade?symbol={C260}&amp;side=bought"' in chain
    assert f'href="/portfolio/trade?symbol={C260}&amp;side=sold"' in chain
    url = web.post("/watchlists", data={"name": "Tech"}, follow_redirects=False)
    url = url.headers["location"]
    web.post(f"{url}/items", data={"ticker": "AAPL"})
    web.post(
        "/options/AAPL/add",
        data={
            "contract": C260,
            "watchlist_id": url.rsplit("/", 1)[1],
            "expiration": OPTION_EXPIRATION.isoformat(),
            "type": "call",
        },
    )
    watchlist = web.get(url).text
    assert 'href="/portfolio/trade?symbol=AAPL&amp;side=bought"' in watchlist
    assert f'href="/portfolio/trade?symbol={C260}&amp;side=sold"' in watchlist
    assert (
        'href="/portfolio/trade?symbol=AAPL&amp;side=sold"' in web.get("/lookup?ticker=AAPL").text
    )


# --- the trade date and the filled-in price always match (bug seen 2026-11-03) --------------


def test_changing_the_date_then_saving_does_not_keep_the_old_price(web, engine):
    """The owner changed the date to Oct 1 and pressed Save: the trade was saved at the
    price filled in for today. Now Save shows that day's price instead of saving."""
    html = form(web).text
    assert field(html, "price_date") == "2026-11-03"
    response = save(
        web, date="2026-10-29", price="259.58", suggested_price="259.58", price_date="2026-11-03"
    )
    assert response.status_code == 400
    page = unescape(response.text)
    assert "The price shown was for Nov 3, 2026, not Oct 29, 2026." in page
    assert field(page, "price") == "258.04"  # Oct 29's close
    assert field(page, "price_date") == "2026-10-29"
    assert field(page, "quantity") == "10"  # what was typed is kept
    assert trades(engine) == []
    # Saving again (now the price belongs to the date) works.
    response = save(
        web, date="2026-10-29", price="258.04", suggested_price="258.04", price_date="2026-10-29"
    )
    assert response.status_code == 303
    [trade] = trades(engine)
    assert (trade.price, trade.price_source) == (258.04, "auto")


def test_a_typed_price_is_saved_whatever_date(web, engine):
    response = save(
        web, date="2026-10-29", price="257.00", suggested_price="259.58", price_date="2026-11-03"
    )
    assert response.status_code == 303
    assert (trades(engine)[0].price, trades(engine)[0].price_source) == (257.00, "manual")


def test_price_part_refreshes_for_a_new_date(web):
    """HTMX asks for this when the date field changes."""
    html = web.get("/portfolio/trade/price", params={"symbol": "AAPL", "date": "2026-10-29"}).text
    assert "<html" not in html  # only the price part
    assert field(html, "price") == "258.04"
    assert field(html, "price_date") == "2026-10-29"
    assert "Filled in: closing price on Oct 29, 2026. You can change it." in html


def test_price_part_for_an_option_includes_the_stock_price(web, fake_market_data):
    web.get(CHAIN_URL)
    fake_market_data.option_closes[(P250, date(2026, 10, 30))] = 3.15
    html = web.get("/portfolio/trade/price", params={"symbol": P250, "date": "2026-10-30"}).text
    assert field(html, "price") == "3.15"
    assert field(html, "underlying_price") == "259.58"


def test_date_field_asks_for_the_price_part_when_changed(web):
    html = form(web).text
    assert 'hx-get="/portfolio/trade/price"' in html and 'hx-trigger="change"' in html
    assert "Filled in: last price, today." in html


def test_price_part_with_a_bad_symbol(web):
    response = web.get("/portfolio/trade/price", params={"symbol": "<b>x</b>", "date": ""})
    assert response.status_code == 400
    assert "<b>" not in response.text
