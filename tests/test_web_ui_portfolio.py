"""IMP-10b: portfolio positions readable on phones (every data cell names its column).

The phone card layout itself is CSS (checked with screenshots); these tests check the markup
it relies on, and that the laptop tables keep their fixed column layout.
"""

import re

import pytest

from app.web.assets import STATIC_DIR
from tests.test_web_spreads_expiry import C260, C265, P245, P250, link, trade

TD = re.compile(r"<td\b([^>]*)>(.*?)</td>", re.S)


def unlabelled_cells(row_html):
    """Cells with content but no data-label (full-width colspan cells excepted)."""
    missing = []
    for attrs, inner in TD.findall(row_html):
        if "data-label=" in attrs or not inner.strip():
            continue
        span = re.search(r'colspan="(\d+)"', attrs)
        if span and int(span.group(1)) >= 8:
            continue
        missing.append(re.sub(r"\s+", " ", inner)[:60])
    return missing


def rows(html, table_class):
    table = re.search(rf'<table class="{table_class}">(.*?)</table>', html, re.S).group(1)
    return re.findall(r"<tr\b[^>]*>.*?</tr>", table.split("<tbody>")[1], re.S)


@pytest.fixture
def web(client, options_clock):
    client.get("/options/AAPL?expiration=2026-11-20")
    return client


@pytest.fixture
def full(web, engine):
    """Stocks, a covered call, an uncovered call, a cash-secured put, a long call and a
    linked credit spread: every kind of row the page draws."""
    trade(web, "AAPL", "bought", 100, 250, day="2026-10-29")
    trade(web, C265, "sold", 1, 4.00, day="2026-10-30")
    trade(web, C260, "sold", 1, 6.00)  # no shares left to cover it
    trade(web, P250, "sold", 1, 2.00, day="2026-10-06")
    trade(web, P245, "bought", 1, 1.00, day="2026-10-06")
    link(web, engine, P250, P245)
    return web.get("/portfolio").text


def test_imp10b_stock_cells_are_labelled(full):
    stock_rows = rows(full, "stocks positions responsive")
    assert stock_rows
    for row in stock_rows:
        assert unlabelled_cells(row) == [], row[:80]
    labels = re.findall(r'data-label="([^"]*)"', stock_rows[0])
    assert labels == [
        "Ticker",
        "Position",
        "Avg entry",
        "Current",
        "P&amp;L",
        "Since",
        "Price fetched",
        "Actions",
    ]


def test_imp10b_option_cells_are_labelled(full):
    option_rows = rows(full, "stocks positions options-positions responsive")
    kinds = " ".join(re.match(r"<tr\b[^>]*>", r).group(0) for r in option_rows)
    assert "data-spread=" in kinds and "data-leg=" in kinds and "data-symbol=" in kinds
    for row in option_rows:
        assert unlabelled_cells(row) == [], row[:80]


def test_imp10b_uncovered_call_cell_is_labelled(full):
    row = re.search(rf'<tr data-symbol="{C260}".*?</tr>', full, re.S).group(0)
    assert re.search(
        r'<td class="num" colspan="2" data-label="Return"><span class="uncovered">', row
    )


def test_imp10b_headline_cells_are_marked_for_the_card(full):
    stock = re.search(r'<tr data-symbol="AAPL".*?</tr>', full, re.S).group(0)
    assert 'data-label="Current" data-col="last"' in stock
    assert 'data-label="P&amp;L" data-col="change"' in stock
    pair = re.search(r"<tr [^>]*data-spread=.*?</tr>", full, re.S).group(0)
    assert 'data-label="P&amp;L" data-col="last"' in pair  # a pair's headline is its P&L


def test_imp10b_laptop_tables_keep_their_fixed_columns(full):
    assert full.count('<details class="ticker-group" open') == 1
    group = full.split('<details class="ticker-group"')[1]
    assert "<colgroup>" in group.split("<thead>")[0]
    css = (STATIC_DIR / "style.css").read_text()
    assert re.search(r"(?m)^table\.options-positions \{\s*table-layout:\s*fixed;\s*\}", css)
