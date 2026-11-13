"""IMP-9: the watchlist page as cards on phones (labels per cell, change pill, add form first)."""

import re
from dataclasses import replace

import pytest

from tests.conftest import OPTION_EXPIRATION

C250P = "AAPL261120P00250000"
STOCK_LABELS = [
    "Ticker",
    "Last",
    "Change",
    "Day range",
    "52-week range",
    "Volume",
    "Market cap",
    "Next earnings",
    "Fetched",
    "Actions",
]
CONTRACT_LABELS = [
    "Contract",
    "Stock",
    "Bid",
    "Ask",
    "Mid",
    "Last",
    "Volume",
    "Open int.",
    "IV",
    "Delta",
    "Gamma",
    "Theta",
    "Vega",
    "DTE",
    "Fetched",
    "Actions",
]


@pytest.fixture
def web(client, options_clock):
    client.post("/watchlists", data={"name": "Tech"})
    client.post("/watchlists/1/items", data={"ticker": "AAPL"})
    client.get(f"/options/AAPL?expiration={OPTION_EXPIRATION.isoformat()}")
    client.post(
        "/options/AAPL/add",
        data={
            "contract": C250P,
            "watchlist_id": "1",
            "expiration": OPTION_EXPIRATION.isoformat(),
            "type": "put",
        },
    )
    return client


def row(html, symbol):
    return re.search(rf'<tr data-symbol="{symbol}".*?</tr>', html, re.S).group(0)


def labels(row_html):
    """data-label of each cell, in order; None for a cell without one."""
    tds = re.findall(r"<td\b([^>]*)>", row_html)
    return [(re.search(r'data-label="([^"]*)"', a) or [None, None])[1] for a in tds]


def test_imp9_every_stock_cell_names_its_column(web):
    html = web.get("/watchlists/1").text
    assert labels(row(html, "AAPL")) == STOCK_LABELS
    assert "<thead>" in html.split('data-symbol="AAPL"')[0]  # header row kept


def test_imp9_every_contract_cell_names_its_column(web):
    html = web.get("/watchlists/1").text
    assert labels(row(html, C250P)) == CONTRACT_LABELS


def test_imp9_tables_use_the_card_layout_on_phones(web):
    html = web.get("/watchlists/1").text
    assert html.count('<table class="stocks responsive') == 2


def test_imp9_full_width_note_cells_need_no_label(web, fake_market_data):
    fake_market_data.down_for.add("AAPL")
    html = web.get("/watchlists/1?refresh=1").text
    note = re.search(r'<tr class="row-note">\s*<td colspan="10">', html)
    assert note, "the stale-row note keeps its full-width cell"


def test_imp9_change_has_arrow_and_pill_and_keeps_its_class(web):
    cell = re.search(
        r'data-col="change">(.*?)</td>', row(web.get("/watchlists/1").text, "AAPL"), re.S
    )
    change = cell.group(1)
    assert '<span class="chg"><span class="up">' in change
    assert '<span class="arrow" aria-hidden="true">▲</span> +1.54 (+0.60%)' in change


def change_cell(web, fake_market_data, change):
    """Refresh AAPL so its day change is `change` (None: no previous close); return the cell."""
    real = fake_market_data.get_quote

    def quote(symbol):
        q = real(symbol)
        prev = None if change is None else q.last_price - change
        return replace(q, previous_close=prev)

    fake_market_data.get_quote = quote
    html = web.get("/watchlists/1?refresh=1").text
    return re.search(r'data-col="change">(.*?)</td>', row(html, "AAPL"), re.S).group(1)


@pytest.mark.parametrize(("change", "arrow"), [(-2.5, "▼"), (0.0, None), (0.001, None)])
def test_imp9_arrow_follows_the_shown_change(web, fake_market_data, change, arrow):
    cell = change_cell(web, fake_market_data, change)
    if arrow:
        assert f'aria-hidden="true">{arrow}</span> -2.50' in cell
        assert 'class="down"' in cell
    else:
        assert "arrow" not in cell
        assert 'class="flat"' in cell  # +0.001 shows as 0.00, so it isn't green either


def test_imp9_missing_change_shows_a_dash_without_arrow(web, fake_market_data):
    assert change_cell(web, fake_market_data, None).strip() == "—"


def test_imp9_add_forms_come_before_the_stocks_table(web):
    html = web.get("/watchlists/1").text
    table = html.index('<table class="stocks responsive">')
    assert html.index('id="q"') < table  # search box
    assert html.index('action="/watchlists/1/items"') < table  # add by ticker


def test_imp9_actions_stay_plain_forms(web):
    actions = re.search(
        r'<td class="actions"[^>]*>(.*?)</td>', row(web.get("/watchlists/1").text, "AAPL"), re.S
    )
    html = actions.group(1)
    assert '<form method="post" action="/reports/generate">' in html
    assert re.search(r'<form method="post" action="/watchlists/1/items/\d+/delete">', html)


def test_imp9_contract_remove_stays_a_plain_form(web):
    actions = re.search(
        r'<td class="actions"[^>]*>(.*?)</td>', row(web.get("/watchlists/1").text, C250P), re.S
    ).group(1)
    assert re.search(r'<form method="post" action="/watchlists/1/items/\d+/delete">', actions)


def test_imp9_every_card_shows_when_it_was_fetched(web):
    html = web.get("/watchlists/1").text
    for symbol in ("AAPL", C250P):
        fetched = re.search(r'data-col="fetched">(.*?)</td>', row(html, symbol), re.S).group(1)
        assert "ET" in fetched, symbol
