"""Ownership section, condensed (SPEC F4.8, owner 2026-11-14): the headline, the largest
few insider trades and the funds that changed their position are shown; the full lists are
folded away. Sorting and counting are done in Python (`app/web/report_view.py`).
"""

import re

from app.web.report_view import fund_changes, insider_highlights
from tests.test_web_reports import generate


def trade(name, kind, value, plan=False, date="2026-10-30"):
    return {"name": name, "kind": kind, "value": value, "plan_10b5_1": plan, "date": date}


TRADES = [
    trade("Jane", "sell", 754500.0, plan=True),
    trade("Jane", "sell", 3003000.0, plan=True, date="2026-10-29"),
    trade("John", "buy", 230000.0, date="2026-09-12"),
    trade("Ann", "sell", None),
]


def test_highlights_are_the_largest_trades_first():
    h = insider_highlights({"trades": TRADES}, 2)
    assert [t["value"] for t in h["top"]] == [3003000.0, 754500.0]
    assert (h["total"], h["more"], h["people"]) == (4, 2, 3)
    assert (h["sales"], h["planned_sales"]) == (3, 2)


def test_highlights_put_trades_without_a_value_last_and_handle_small_lists():
    h = insider_highlights({"trades": TRADES}, 10)
    assert [t["value"] for t in h["top"]] == [3003000.0, 754500.0, 230000.0, None]
    assert h["more"] == 0
    assert insider_highlights({"trades": TRADES}, 0)["top"] == []
    for empty in ({}, {"trades": []}, None):
        h = insider_highlights(empty, 3)
        assert (h["top"], h["total"], h["more"], h["people"]) == ([], 0, 0, 0)


def test_fund_changes_separates_moves_from_unchanged_positions():
    positions = [
        {"fund": "A", "status": "unchanged"},
        {"fund": "B", "status": "added"},
        {"fund": "C", "status": "sold"},
        {"fund": "D", "status": "held"},
        {"fund": "E", "status": "new"},
        {"fund": "F", "status": "reduced"},
    ]
    result = fund_changes({"positions": positions})
    assert [p["fund"] for p in result["changed"]] == ["B", "C", "E", "F"]
    assert [p["fund"] for p in result["unchanged"]] == ["A", "D"]
    assert fund_changes(None) == {"changed": [], "unchanged": []}
    assert fund_changes({}) == {"changed": [], "unchanged": []}


# --- on the page -------------------------------------------------------------------------


def section(html, name="Institutional &amp; insider activity"):
    return html.split(f"<h3>{name}</h3>")[1].split("</section>")[0]


def visible(html):
    """The section without its folded parts."""
    return re.sub(r"<details.*?</details>", "", html, flags=re.S)


def test_largest_trades_are_shown_and_the_full_table_is_folded(client):
    html = section(client.get(generate(client, ticker="AAPL")).text)
    shown = visible(html)
    items = re.findall(
        r"<li>.*?</li>", shown.split('class="insider-highlights"')[1].split("</ul>")[0], flags=re.S
    )
    assert len(items) == 3
    first = " ".join(re.sub(r"<[^>]+>", " ", items[0]).split())
    assert first.startswith("Sold $3.00M")
    assert "Example Jane Q" in first and "12,000 shares at $250.25" in first
    assert "Oct 29, 2026" in first and "pre-arranged plan" in first
    assert 'rel="noopener noreferrer" target="_blank">Form 4</a>' in items[0]
    # The headline sentence stays; the table moved behind a fold.
    assert "<strong>2 sales</strong>" in shown and "$3,757,500.00" in shown
    assert "<table" not in shown
    assert "<summary>All 3 insider trades</summary>" in html
    assert "<th>Insider</th>" in html.split("All 3 insider trades")[1]


def test_highlight_count_follows_the_setting(client):
    config = client.app.state.config
    client.app.state.config = config.model_copy(
        update={"reports": config.reports.model_copy(update={"insider_highlights": 1})}
    )
    html = section(client.get(generate(client, ticker="AAPL")).text)
    block = visible(html).split('class="insider-highlights"')[1].split("</ul>")[0]
    assert block.count("<li>") == 1
    assert "2 smaller trades are in the full list" in " ".join(visible(html).split())


def test_pre_arranged_sales_are_called_out_plainly(client):
    shown = " ".join(visible(section(client.get(generate(client, ticker="AAPL")).text)).split())
    assert "Both sales were made under pre-arranged plans" in shown


def test_unchanged_funds_are_folded_and_said_so(client):
    html = section(client.get(generate(client, ticker="AAPL")).text)
    shown = " ".join(visible(html).split())
    assert "Funds you follow" in shown
    assert "none changed its position" in shown
    assert "Held, unchanged" not in shown
    assert "<summary>1 fund with an unchanged position</summary>" in html
    assert "<strong>Berkshire Hathaway</strong>" in html and "Held, unchanged" in html


def test_report_without_sec_or_trades_still_shows(client, report_services):
    report_services.sources.edgar._user_agent_ok = False
    response = client.get(generate(client, ticker="AAPL"))
    assert response.status_code == 200
    assert "insider-highlights" not in response.text
