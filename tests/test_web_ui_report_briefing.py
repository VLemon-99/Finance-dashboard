"""IMP-19: the briefing table on top of a whole-watchlist report (SPEC F4.9, F4.11, F7.10).

One row per ticker with the same key figures as that report's "At a glance" tiles, read from
the stored reports when the page is drawn.
"""

import re
from types import SimpleNamespace

from sqlmodel import Session, select

from app.db.models import Report
from app.web.report_view import briefing, glance
from tests.test_web_reports import generate
from tests.test_web_watchlists import add, create

LABELS = [
    "Ticker",
    "Sentiment",
    "Valuation",
    "Analyst target",
    "Insiders",
    "Options flow",
    "Top development",
    "Missing",
]


def stored(ticker, report_id, body, missing=()):
    return SimpleNamespace(ticker=ticker, id=report_id, body=body, missing=list(missing))


FULL = {
    "sentiment": {"score": 25, "counts": {"bullish": 2, "neutral": 6, "bearish": 0}},
    "valuation": {
        "rows": [{"label": "P/E", "now": 30.0, "history": {"vs": 0.2}}],
        "targets": {"mean": 230.0, "mean_vs": 0.15, "analysts": 40},
    },
    "insider": {
        "available": True,
        "lookback_days": 90,
        "buys": 1,
        "sells": 2,
        "net_value": -3527500.0,
        "trades": [{}],
    },
    "unusual_options": {"count": 3, "calls": 2, "puts": 1},
    "developments": [{"text": "First thing.", "refs": [1]}, {"text": "Second.", "refs": [2]}],
    "stories": [{}] * 8,
}


def test_briefing_rows_match_the_glance_tiles_and_keep_the_order():
    rows = briefing([stored("MSFT", 7, FULL), stored("AAPL", 8, FULL, missing=["a", "b"])])
    assert [(r["ticker"], r["report_id"]) for r in rows] == [("MSFT", 7), ("AAPL", 8)]
    tiles = {t["key"]: t for t in glance(FULL, [])}
    row = rows[0]
    for key in ("sentiment", "valuation", "target", "insider", "options"):
        assert row[key]["value"] == tiles[key]["value"]
        assert row[key]["detail"] == tiles[key]["detail"]
        assert row[key]["tone"] == tiles[key]["tone"]
    assert row["top"] == {"text": "First thing.", "refs": [1]}
    assert (row["missing"], rows[1]["missing"]) == (0, 2)
    assert row["mix"]["bullish"] == 25.0  # the small bullish / neutral / bearish bar


def test_briefing_shows_a_dash_for_parts_a_report_lacks():
    rows = briefing([stored("OLD", 1, {"sentiment": {"score": None, "counts": {}}})])
    row = rows[0]
    for key in ("sentiment", "valuation", "target", "insider", "options"):
        assert (row[key]["value"], row[key]["detail"]) == ("—", "")
    assert row["top"] is None and row["mix"] is None
    assert briefing([]) == []


def test_briefing_keeps_real_values_that_are_not_missing():
    quiet = {
        **FULL,
        "insider": {"available": True, "lookback_days": 90, "trades": []},
        "unusual_options": {"count": 0, "expirations": 3},
    }
    row = briefing([stored("AAPL", 1, quiet)])[0]
    assert row["insider"]["value"] == "No trades"
    assert row["options"]["value"] == "Nothing unusual"


# --- on the page -------------------------------------------------------------------------


def watchlist_run(client, *tickers):
    url = create(client, "Tech")
    for ticker in tickers:
        add(client, url, ticker)
    return generate(client, watchlist_id=url.rsplit("/", 1)[1])


def briefing_html(html):
    return html.split("<h2>Summary</h2>")[1].split("</table>")[0]


def rows_of(html):
    return re.findall(r"<tr>\s*<td.*?</tr>", briefing_html(html), flags=re.S)


def cell(row, label):
    found = re.search(rf'<td[^>]*data-label="{label}"[^>]*>(.*?)</td>', row, flags=re.S)
    assert found, label
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", found.group(1))).strip()


def test_briefing_lists_each_tickers_key_figures(client):
    html = client.get(watchlist_run(client, "AAPL", "MSFT")).text
    rows = rows_of(html)
    assert len(rows) == 2
    aapl = rows[0]
    assert cell(aapl, "Ticker") == "AAPL"
    assert cell(aapl, "Sentiment").startswith("+25")
    assert "2 / 6 / 0" in cell(aapl, "Sentiment")
    assert cell(aapl, "Valuation") == "P/E 30.0 +20% vs own history"
    assert cell(aapl, "Analyst target").startswith("$230.00 +15% vs price")
    assert cell(aapl, "Insiders").startswith("Net sold $3.53M")
    assert cell(aapl, "Options flow").startswith("Nothing unusual")
    assert "Development: " in cell(aapl, "Top development")
    assert "[sources]" in cell(aapl, "Top development")
    assert cell(aapl, "Missing") == "—"
    assert cell(rows[1], "Ticker") == "MSFT"
    assert "Ratings are bullish / neutral / bearish story counts." in html


def test_every_cell_is_labelled_and_the_table_becomes_cards_on_phones(client):
    html = client.get(watchlist_run(client, "AAPL", "MSFT")).text
    table = briefing_html(html)
    assert re.search(r'<table class="[^"]*\bresponsive\b[^"]*\bbriefing\b', table)
    for row in rows_of(html):
        assert re.findall(r'<td[^>]*data-label="([^"]+)"', row) == LABELS
        assert row.count("<td") == len(LABELS)
    assert "data-ticker" not in table


def test_ticker_links_to_its_report_and_reports_link_back(client):
    html = client.get(watchlist_run(client, "AAPL", "MSFT")).text
    assert '<div id="summary"' in html
    assert html.index('id="summary"') < html.index("<h2>Summary</h2>")
    for row in rows_of(html):
        rid = re.search(r'href="#report-(\d+)"', row).group(1)
        assert f'<article class="report" id="report-{rid}"' in html
        assert f'href="/reports/{rid}">[sources]</a>' in row
    assert html.count('href="#summary"') == 2  # one per report


def test_single_ticker_pages_have_no_briefing_and_no_back_link(client):
    run_url = generate(client, ticker="AAPL")
    html = client.get(run_url).text
    assert "<h2>Summary</h2>" not in html and 'href="#summary"' not in html
    assert 'href="#summary"' not in client.get("/reports/1").text


def test_report_without_valuation_or_options_shows_dashes(client, engine):
    run_url = watchlist_run(client, "AAPL", "MSFT")
    with Session(engine) as session:
        report = session.exec(select(Report).where(Report.ticker == "AAPL")).first()
        body = dict(report.body)
        for key in ("institutional", "unusual_options"):
            body.pop(key)
        body["valuation"] = None
        report.body = body
        session.add(report)
        session.commit()
    response = client.get(run_url)
    assert response.status_code == 200
    aapl = rows_of(response.text)[0]
    assert cell(aapl, "Valuation") == "—"
    assert cell(aapl, "Analyst target") == "—"
    assert cell(aapl, "Options flow") == "—"
    assert cell(aapl, "Sentiment").startswith("+25")


def test_missing_count_and_missing_parts_show_on_the_page(client):
    html = client.get(watchlist_run(client, "AAPL", "MSFT")).text
    msft = rows_of(html)[1]
    assert re.fullmatch(r"\d+", cell(msft, "Missing"))  # MSFT has no SEC filings in the samples
    assert cell(msft, "Insiders") == "—"
    assert cell(msft, "Analyst target") == "—"


def test_failed_ticker_is_named_and_the_others_are_briefed(client, monkeypatch):
    from app.services import reports as reports_service

    real = reports_service.prepare_ticker

    def flaky(session, run, item, *args):
        if item == "MSFT":
            raise RuntimeError("bug")
        return real(session, run, item, *args)

    monkeypatch.setattr(reports_service, "prepare_ticker", flaky)
    response = client.get(watchlist_run(client, "AAPL", "MSFT"))
    assert response.status_code == 200
    assert "No report could be made for: MSFT" in response.text
    assert [cell(row, "Ticker") for row in rows_of(response.text)] == ["AAPL"]
