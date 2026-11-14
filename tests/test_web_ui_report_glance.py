"""IMP-17: the "At a glance" strip of key figures on top of each ticker's report.

The tiles repeat figures the sections below already show (SPEC F4.6, F4.7, F4.8, F4.11);
they're built in Python from the stored report (`app/web/report_view.py`).
"""

import re

from sqlmodel import Session, select

from app.db.models import Report
from app.web.report_view import glance
from tests.test_web_reports import generate
from tests.test_web_watchlists import add, create

TILE_LABELS = ["Sentiment", "Valuation", "Analyst target", "Insiders", "Options flow", "Sources"]


def body(**changes):
    """A small stored report body with every part present."""
    base = {
        "sentiment": {"score": 25, "counts": {"bullish": 2, "neutral": 6, "bearish": 0}},
        "valuation": {
            "rows": [
                {"key": "trailing_pe", "label": "P/E", "now": 30.0, "history": {"vs": 0.2}},
                {"key": "forward_pe", "label": "Forward P/E", "now": 27.0, "history": None},
            ],
            "targets": {"mean": 230.0, "mean_vs": 0.15, "analysts": 40},
        },
        "insider": {
            "available": True,
            "lookback_days": 90,
            "buys": 1,
            "sells": 2,
            "net_value": -3527500.0,
            "trades": [{}, {}, {}],
        },
        "unusual_options": {"count": 3, "calls": 2, "puts": 1},
        "stories": [{}] * 8,
    }
    return {**base, **changes}


def tiles(report_body, missing=()):
    return {t["key"]: t for t in glance(report_body, list(missing))}


def test_six_tiles_in_order_each_pointing_at_a_section():
    result = glance(body(), [])
    assert [t["label"] for t in result] == TILE_LABELS
    assert [t["section"] for t in result] == [
        "sentiment",
        "valuation",
        "valuation",
        "insider",
        "insider",
        "sources",
    ]


def test_sentiment_tile():
    t = tiles(body())["sentiment"]
    assert (t["value"], t["detail"], t["tone"]) == (
        "+25",
        "2 bullish · 6 neutral · 0 bearish",
        "up",
    )
    negative = body(sentiment={"score": -40, "counts": {"bullish": 0, "neutral": 1, "bearish": 3}})
    assert tiles(negative)["sentiment"]["value"] == "−40"
    assert tiles(negative)["sentiment"]["tone"] == "down"
    zero = body(sentiment={"score": 0, "counts": {"bullish": 1, "neutral": 0, "bearish": 1}})
    assert (tiles(zero)["sentiment"]["value"], tiles(zero)["sentiment"]["tone"]) == ("0", "flat")


def test_sentiment_tile_without_a_score():
    none = body(sentiment={"score": None, "counts": {"bullish": 0, "neutral": 0, "bearish": 0}})
    t = tiles(none)["sentiment"]
    assert (t["value"], t["tone"]) == ("n/a", "flat")
    assert t["detail"] == "no stories rated"


def test_valuation_tile_prefers_own_history_then_other_benchmarks():
    t = tiles(body())["valuation"]
    assert (t["value"], t["detail"]) == ("P/E 30.0", "+20% vs own history")
    peers_only = body(
        valuation={"rows": [{"label": "P/E", "now": 12.34, "industry": {"vs": -0.051}}]}
    )
    assert tiles(peers_only)["valuation"]["detail"] == "−5% vs peers"
    assert tiles(peers_only)["valuation"]["value"] == "P/E 12.3"
    alone = body(valuation={"rows": [{"label": "P/E", "now": 12.0}]})
    assert tiles(alone)["valuation"]["detail"] == "no benchmark"


def test_valuation_tile_uses_the_first_multiple_that_has_a_value():
    losing = body(
        valuation={
            "rows": [
                {"label": "P/E", "now": None, "history": None},
                {"label": "Forward P/E", "now": 27.0, "sector": {"vs": -0.1}},
            ]
        }
    )
    t = tiles(losing)["valuation"]
    assert (t["value"], t["detail"]) == ("Forward P/E 27.0", "−10% vs sector")


def test_analyst_tile():
    t = tiles(body())["target"]
    assert (t["value"], t["detail"]) == ("$230.00", "+15% vs price · 40 analysts")
    no_count = body(valuation={"rows": [], "targets": {"mean": 99.5, "mean_vs": -0.02}})
    assert tiles(no_count)["target"]["detail"] == "−2% vs price"


def test_insider_tile_net_sold_and_net_bought():
    t = tiles(body())["insider"]
    assert (t["value"], t["tone"]) == ("Net sold $3.53M", "down")
    assert t["detail"] == "1 buy · 2 sales, last 90 days"
    bought = body(
        insider={
            "available": True,
            "lookback_days": 90,
            "buys": 2,
            "sells": 1,
            "net_value": 12500.0,
            "trades": [{}],
        }
    )
    t = tiles(bought)["insider"]
    assert (t["value"], t["tone"]) == ("Net bought $12.50K", "up")
    assert t["detail"] == "2 buys · 1 sale, last 90 days"


def test_insider_tile_no_trades_and_sec_missing():
    quiet = body(insider={"available": True, "lookback_days": 90, "trades": []})
    assert tiles(quiet)["insider"]["value"] == "No trades"
    assert tiles(quiet)["insider"]["detail"] == "last 90 days"
    off = body(insider={"available": False, "lookback_days": 90, "trades": []})
    assert tiles(off)["insider"]["value"] == "n/a"
    assert tiles(off)["insider"]["detail"] == "SEC filings missing"


def test_options_tile():
    t = tiles(body())["options"]
    assert (t["value"], t["detail"]) == ("3 unusual", "2 calls · 1 put")
    one = body(unusual_options={"count": 1, "calls": 1, "puts": 0})
    assert tiles(one)["options"]["detail"] == "1 call · 0 puts"
    none = body(unusual_options={"count": 0, "calls": 0, "puts": 0})
    assert (tiles(none)["options"]["value"], tiles(none)["options"]["detail"]) == (
        "Nothing unusual",
        "today",
    )


def test_sources_tile_counts_stories_and_missing_sources():
    t = tiles(body())["sources"]
    assert (t["value"], t["detail"], t["tone"]) == ("8", "sources", "flat")
    t = tiles(body(), missing=["Yahoo news: down", "SEC: down"])["sources"]
    assert (t["value"], t["detail"], t["tone"]) == ("8", "sources · 2 missing", "warn")
    one = body(stories=[{}])
    assert tiles(one, missing=["x"])["sources"]["detail"] == "source · 1 missing"


def test_old_or_thin_reports_give_na_tiles_and_never_fail():
    for thin in ({}, {"valuation": None, "sentiment": {}, "insider": {}, "stories": []}):
        result = tiles(thin)
        assert list(result) == ["sentiment", "valuation", "target", "insider", "options", "sources"]
        for key in ("sentiment", "valuation", "target", "options"):
            assert result[key]["value"] == "n/a"
        assert result["sources"]["value"] == "0"
    assert "Unusual options activity" not in str(glance({}, []))


# --- on the page -------------------------------------------------------------------------


def strip(html):
    return html.split('class="glance"')[1].split("</nav>")[0]


def tile_texts(html):
    """label -> the tile's text, tags removed."""
    found = {}
    for tile in re.findall(r'<a class="glance-tile".*?</a>', strip(html), flags=re.S):
        text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", tile)).strip()
        label = next(name for name in TILE_LABELS if text.startswith(name))
        found[label] = text
    return found


def test_strip_sits_before_the_first_section_with_six_tiles(client):
    html = client.get(generate(client, ticker="AAPL")).text
    assert html.index('class="glance"') < html.index("<h3>Major developments</h3>")
    assert strip(html).count('class="glance-tile"') == 6
    assert list(tile_texts(html)) == TILE_LABELS
    assert "Unusual options activity" not in strip(html)


def test_tiles_show_the_same_figures_as_the_sections(client):
    html = client.get(generate(client, ticker="AAPL")).text
    texts = tile_texts(html)
    assert "+25" in texts["Sentiment"] and "2 bullish · 6 neutral · 0 bearish" in texts["Sentiment"]
    assert "P/E 30.0" in texts["Valuation"] and "+20% vs own history" in texts["Valuation"]
    assert "$230.00" in texts["Analyst target"] and "+15% vs price" in texts["Analyst target"]
    assert "Net sold $3.53M" in texts["Insiders"]
    assert "1 buy · 2 sales" in texts["Insiders"]
    assert "Nothing unusual" in texts["Options flow"]
    assert texts["Sources"].startswith("Sources 8 sources")


def test_every_tile_links_to_a_section_of_its_own_report(client):
    url = create(client, "Tech")
    add(client, url, "AAPL")
    add(client, url, "MSFT")
    html = client.get(generate(client, watchlist_id=url.rsplit("/", 1)[1])).text
    reports = html.split('<article class="report"')[1:]
    assert len(reports) == 2
    for article in reports:
        rid = re.match(r' id="report-(\d+)"', article).group(1)
        links = re.findall(r'<a class="glance-tile" href="#([^"]+)"', article)
        assert len(links) == 6
        for link in links:
            assert link.startswith(f"r{rid}-")
            assert f'<section id="{link}"' in article
    ids = re.findall(r'\sid="([^"]+)"', html)
    assert len(ids) == len(set(ids))  # nothing repeated across the two reports


def test_section_ids_are_on_the_sections_and_headings_stay_plain(client):
    html = client.get(generate(client, ticker="AAPL")).text
    for name in ("developments", "rumors", "insider", "valuation", "sentiment", "sources"):
        assert re.search(rf'<section id="r\d+-{name}"', html)
    assert "<h3>Major developments</h3>" in html and "<h3>Sources</h3>" in html


def test_report_without_ai_ratings_shows_na(client, report_services):
    report_services.llm = None
    report_services.llm_problem = "AI summaries are off: set GEMINI_API_KEY in the .env file."
    response = client.get(generate(client, ticker="AAPL"))
    assert response.status_code == 200
    texts = tile_texts(response.text)
    assert texts["Sentiment"].startswith("Sentiment n/a")
    assert "missing" in texts["Sources"]


def test_old_report_without_valuation_or_options_still_shows(client, engine):
    generate(client, ticker="AAPL")
    with Session(engine) as session:
        report = session.exec(select(Report)).first()
        stored = dict(report.body)
        for key in ("institutional", "unusual_options"):
            stored.pop(key)
        stored["valuation"] = None
        report.body = stored
        session.add(report)
        session.commit()
        report_id = report.id
    response = client.get(f"/reports/{report_id}")
    assert response.status_code == 200
    texts = tile_texts(response.text)
    assert texts["Valuation"] == "Valuation n/a not in this report"
    assert texts["Analyst target"] == "Analyst target n/a not in this report"
    assert texts["Options flow"] == "Options flow n/a not in this report"
    assert "Unusual options activity" not in response.text


def test_strip_wraps_two_per_row_on_phones():
    from pathlib import Path

    css = Path("app/web/static/style.css").read_text()
    assert re.search(r"\.glance\s*\{[^}]*display:\s*grid", css)
    assert re.search(
        r"@media \(max-width: 720px\) \{[^@]*\.glance \{[^}]*grid-template-columns: repeat\(2,", css
    )


def test_tiles_use_the_same_wording_as_the_sections_for_missing_parts():
    ai_off = body(sentiment={"score": None, "counts": {}}, ai_model=None)
    assert tiles(ai_off)["sentiment"]["detail"] == "AI ratings off"
    no_chain = body(unusual_options={"count": 0, "expirations": 0})
    assert tiles(no_chain)["options"]["detail"] == "no listed options"
    partial = body()
    partial["insider"] = {**partial["insider"], "available": False}
    assert tiles(partial)["insider"]["detail"].endswith("(may be incomplete)")
    odd = body(unusual_options={"count": 2, "calls": None, "puts": None})
    assert tiles(odd)["options"]["detail"] == "0 calls · 0 puts"


def test_report_without_sec_shows_na_insiders_on_the_page(client, report_services):
    report_services.sources.edgar._user_agent_ok = False
    response = client.get(generate(client, ticker="AAPL"))
    assert response.status_code == 200
    assert tile_texts(response.text)["Insiders"] == "Insiders n/a SEC filings missing"
