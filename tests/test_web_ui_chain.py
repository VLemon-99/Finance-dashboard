"""IMP-13: the option chain on phones (labelled headers, compact columns, sticky strike).

Existing chain tests pin the cells to exactly `<td class="num">…</td>` (13 per row), so the
column names live on the headers and the stylesheet hides columns by position.
"""

import re

import pytest

from app.web.assets import STATIC_DIR
from tests.conftest import OPTION_EXPIRATION

CHAIN_URL = f"/options/AAPL?expiration={OPTION_EXPIRATION.isoformat()}"
COLUMNS = [
    "strike",
    "bid",
    "ask",
    "mid",
    "last",
    "volume",
    "open-interest",
    "iv",
    "delta",
    "gamma",
    "theta",
    "vega",
    "dte",
    "actions",
]
COMPACT = {"strike", "bid", "ask", "mid", "iv", "delta", "actions"}


@pytest.fixture
def web(client, options_clock):
    client.post("/watchlists", data={"name": "Tech"})
    return client


@pytest.fixture(scope="module")
def phone_css():
    css = (STATIC_DIR / "style.css").read_text()
    return "\n".join(re.findall(r"@media \(max-width: 720px\) \{(.*?)\n\}", css, re.S))


def test_imp13_headers_name_their_columns(web):
    html = web.get(CHAIN_URL).text
    head = re.search(r'<table class="stocks chain"[^>]*>\s*<thead>(.*?)</thead>', html, re.S)
    assert re.findall(r'<th\b[^>]*data-col="([^"]+)"', head.group(1)) == COLUMNS


def test_imp13_cells_keep_their_pinned_markup(web):
    html = web.get(CHAIN_URL).text
    row = re.search(r'<tr data-strike="250.0".*?</tr>', html, re.S).group(0)
    assert len(re.findall(r'<td class="num">(.*?)</td>', row, re.S)) == 13


def test_imp13_show_all_columns_switch_is_a_plain_checkbox(web):
    html = web.get(CHAIN_URL).text
    box = re.search(r'<input type="checkbox" id="all-columns"[^>]*>', html)
    assert box and 'class="all-columns"' in box.group(0)
    assert '<label for="all-columns">Show all columns</label>' in html
    # It comes right before the table box, so the stylesheet can follow it (~).
    assert html.index('id="all-columns"') < html.index('<table class="stocks chain"')


def test_imp13_no_switch_without_rows(web, fake_market_data):
    fake_market_data.no_options.add("ZZZ")
    assert 'id="all-columns"' not in web.get("/options/ZZZ").text


def test_imp13_phone_css_hides_only_the_extra_columns_while_unchecked(phone_css):
    hidden = {
        COLUMNS[int(n) - 1]
        for n in re.findall(
            r"\.all-columns:not\(:checked\) ~ \.table-wrap \.chain "
            r":is\(th, td\):nth-child\((\d+)\)",
            phone_css,
        )
    }
    assert hidden == set(COLUMNS) - COMPACT


def test_imp13_strike_column_is_sticky():
    css = (STATIC_DIR / "style.css").read_text()
    assert re.search(r"\.chain :is\(th, td\):first-child \{[^}]*position: sticky;[^}]*left: 0", css)


def test_imp13_show_button_and_actions_stay(web):
    html = web.get(CHAIN_URL).text
    expiration_form = re.search(
        r'<form method="get" action="/options/AAPL" class="inline-form[^"]*">(.*?)</form>',
        html,
        re.S,
    ).group(1)
    assert '<button type="submit">Show</button>' in expiration_form
    row = re.search(r'<tr data-strike="250.0".*?</tr>', html, re.S).group(0)
    assert 'href="/portfolio/trade?symbol=AAPL261120C00250000&amp;side=bought"' in row
    assert '<form method="post" action="/options/AAPL/add">' in row
