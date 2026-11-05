"""Report pages end to end (SPEC F4.1, F4.2, F4.9 "Generate now", F4.10, F4.11, F4.14).

Fake internet and fake AI (see conftest). Reports run straight away unless `hold` is set.
"""

import re

import pytest
from sqlmodel import Session, select

from app.db.models import Report, ReportRun
from tests.test_web_watchlists import add, create

SECTIONS = [
    "Major developments",
    "Rumors &amp; speculation",
    "Institutional &amp; insider activity",
    "Valuation snapshot",
    "Sentiment",
    "Sources",
]


def generate(client, **form):
    response = client.post("/reports/generate", data=form, follow_redirects=False)
    assert response.status_code == 303, response.text
    return response.headers["location"]


def test_reports_page_starts_empty_with_generate_forms(client):
    html = client.get("/reports").text
    assert "No reports yet" in html
    assert 'action="/reports/generate"' in html
    assert '<a href="/reports">Reports</a>' in html  # in the top menu


def test_f4_9_generate_now_for_one_ticker(client):
    url = generate(client, ticker="aapl")
    assert re.fullmatch(r"/reports/runs/\d+", url)
    html = client.get(url).text
    assert "Report: AAPL" in html
    assert 'data-ticker="AAPL"' in html


def test_f4_1_sections_in_order(client):
    html = client.get(generate(client, ticker="AAPL")).text
    positions = [html.index(f"<h3>{name}</h3>") for name in SECTIONS]
    assert positions == sorted(positions)


def test_f4_1_rumors_are_labelled_unconfirmed(client):
    html = client.get(generate(client, ticker="AAPL")).text
    rumors = html.split("<h3>Rumors &amp; speculation</h3>")[1].split("<h3>")[0]
    assert (
        '<span class="badge">unconfirmed</span> Rumor: Report: Apple said to be exploring' in rumors
    )


def test_f4_2_statements_link_to_sources_with_title_outlet_date_url(client):
    html = client.get(generate(client, ticker="AAPL")).text
    developments = html.split("<h3>Major developments</h3>")[1].split("<h3>")[0]
    anchors = re.findall(r'href="#(r\d+-s\d+)"', developments)
    assert anchors  # every statement carries at least one citation link
    sources = html.split("<h3>Sources</h3>")[1]
    for anchor in anchors:
        assert f'id="{anchor}"' in sources
    # A cited source shows its title, outlet, date and link.
    assert 'href="https://news.google.com/rss/articles/CBMiSAMPLE0001?oc=5"' in sources
    assert "Reuters, Oct 30, 2026 4:45 PM ET" in sources
    assert "CNBC, Oct 30, 2026 5:02 PM ET" in sources  # same story, second outlet (F4.12)


def test_f4_14_shows_generated_time_and_period(client):
    html = client.get(generate(client, ticker="AAPL")).text
    # Frozen "now": Nov 2, 2026 12:00 UTC = 7:00 AM ET. First report covers 7 days.
    assert "Generated Nov 2, 2026 7:00 AM ET" in html
    assert "covers Oct 26, 2026 8:00 AM ET to Nov 2, 2026 7:00 AM ET" in html


def test_insider_and_sentiment_sections_show_code_numbers(client):
    html = client.get(generate(client, ticker="AAPL")).text
    assert "<strong>1 buy</strong> (1,000 shares, $230,000.00)" in html
    assert "<strong>2 sales</strong> (15,000 shares, $3,757,500.00)" in html
    assert "-$3,527,500.00" in html
    assert ">+25</strong>" in html
    assert "2 bullish · 6 neutral · 0 bearish" in html
    assert "Weights used: filing 1.0 · news 1.0." in html  # switched-on sources only


def test_f4_11_missing_sources_are_shown(client, fake_fetch):
    fake_fetch.down.add("https://news.google.com")
    html = client.get(generate(client, ticker="AAPL")).text
    assert "Missing from this report:" in html
    assert "Google News: The site didn&#39;t answer in time." in html
    assert "<h3>Sources</h3>" in html  # the report is still there


def test_generate_for_a_whole_watchlist_shows_summary_on_top(client):
    url = create(client, "Tech")
    add(client, url, "AAPL")
    add(client, url, "MSFT")
    watchlist_id = url.rsplit("/", 1)[1]
    html = client.get(generate(client, watchlist_id=watchlist_id)).text
    assert "Report: Tech" in html
    summary = html.split("<h2>Summary</h2>")[1]
    assert summary.index('data-ticker="AAPL"') < summary.index('data-ticker="MSFT"')
    assert html.index("<h2>Summary</h2>") < html.index('data-ticker="AAPL"')


def test_watchlist_page_has_generate_buttons(client):
    url = create(client, "Tech")
    add(client, url, "AAPL")
    html = client.get(url).text
    watchlist_id = url.rsplit("/", 1)[1]
    assert f'name="watchlist_id" value="{watchlist_id}"' in html
    assert 'aria-label="Generate report for AAPL"' in html


def test_empty_watchlist_or_bad_ticker_is_refused(client, report_runner):
    url = create(client, "Empty")
    response = client.post("/reports/generate", data={"watchlist_id": url.rsplit("/", 1)[1]})
    assert response.status_code == 400 and "is empty" in response.text
    response = client.post("/reports/generate", data={"ticker": "<script>"})
    assert response.status_code == 400 and "valid US ticker" in response.text
    assert "&lt;script&gt;" not in response.text  # the bad input isn't echoed back
    assert report_runner.started == []


def test_other_watchlist_ids_and_unknown_reports_are_not_found(client):
    assert client.post("/reports/generate", data={"watchlist_id": "999"}).status_code == 404
    assert client.get("/reports/runs/999").status_code == 404
    assert client.get("/reports/999").status_code == 404


def test_progress_while_running_and_one_run_at_a_time(client, report_runner, engine):
    report_runner.hold = True
    url = generate(client, ticker="AAPL")
    html = client.get(url).text
    assert "Working on it…" in html and "0 of 1 ticker done" in html
    assert 'hx-get="/reports/runs/' in html and 'hx-trigger="every 3s"' in html
    # A second click goes to the run that's already going instead of starting another.
    assert generate(client, ticker="MSFT") == url
    assert len(report_runner.started) == 1
    progress = client.get(f"{url}/progress")
    assert "Working on it…" in progress.text
    with Session(engine) as session:
        run = session.exec(select(ReportRun)).one()
        run.status = "done"
        session.commit()
    assert client.get(f"{url}/progress").headers.get("HX-Refresh") == "true"


def test_f4_10_history_by_ticker_and_date(client):
    generate(client, ticker="AAPL")
    html = client.get("/reports", params={"ticker": "aapl"}).text
    assert "AAPL reports" in html
    match = re.search(r'href="(/reports/\d+)"', html)
    assert match
    report_url = match.group(1)
    page = client.get(report_url).text
    assert 'data-ticker="AAPL"' in page and "Earlier AAPL reports" in page
    # By date (US Eastern): the frozen "now" is Nov 2, 2026.
    assert "AAPL</a>" in client.get("/reports", params={"day": "2026-11-02"}).text
    assert "No reports on that date" in client.get("/reports", params={"day": "2026-11-01"}).text
    assert "No MSFT reports" in client.get("/reports", params={"ticker": "MSFT"}).text


@pytest.mark.parametrize("params", [{"day": "yesterday"}, {"ticker": "not a ticker!"}])
def test_history_rejects_bad_filters(client, params):
    response = client.get("/reports", params=params)
    assert response.status_code == 400


def test_reports_belong_to_the_user(client, engine):
    generate(client, ticker="AAPL")
    with Session(engine) as session:
        report = session.exec(select(Report)).one()
        run = session.exec(select(ReportRun)).one()
        assert report.user_id == run.user_id is not None


def test_without_sec_insider_section_says_not_available(client, report_services):
    """Missing data must read as missing, not as "none" (F4.11)."""
    edgar = report_services.sources.edgar
    edgar._user_agent_ok = False  # as if SEC_USER_AGENT weren't set in .env
    html = client.get(generate(client, ticker="AAPL")).text
    insider = html.split("<h3>Institutional &amp; insider activity</h3>")[1].split("<h3>")[0]
    assert "insider trades and 5%+ holder reports are not available" in insider
    assert "no open-market buys or sales" not in insider
    assert "none." not in insider


def test_with_sec_insider_section_has_no_warning(client):
    html = client.get(generate(client, ticker="AAPL")).text
    assert "couldn&#39;t be collected for this report" not in html
    assert "<strong>2 sales</strong>" in html


def test_without_ai_sentiment_says_ratings_are_off(client, report_services):
    report_services.llm = None
    report_services.llm_problem = "AI summaries are off: set GEMINI_API_KEY in the .env file."
    html = client.get(generate(client, ticker="AAPL")).text
    assert "No score: AI ratings are off for this report" in html


def test_progress_says_what_the_run_is_doing(client, report_runner, engine):
    report_runner.hold = True
    url = generate(client, ticker="AAPL")
    with Session(engine) as session:
        run = session.exec(select(ReportRun)).one()
        run.summary = {"stage": "Watching new YouTube videos (up to 10)"}
        session.commit()
    assert "Watching new YouTube videos (up to 10)…" in client.get(f"{url}/progress").text


def with_blog_and_channel(report_services, fake_fetch, **ai):
    from app.config import AppConfig, SourcesConfig
    from app.sources.registry import blog_and_video_sources

    config = SourcesConfig.model_validate(
        {
            "blogs": {
                "feeds": [{"name": "Sample Tech Blog", "feed": "https://blog.example.com/feed/"}]
            },
            "youtube": {
                "channels": [{"name": "Sample Markets", "channel_id": "UCsampleChannelAAAAAAAAA"}]
            },
        }
    )
    report_services.sources.sources = blog_and_video_sources(fake_fetch, config)
    report_services.config = AppConfig.model_validate({"ai": ai})


def test_f4_5_video_points_link_to_their_moment(client, report_services, fake_fetch):
    with_blog_and_channel(report_services, fake_fetch)
    html = client.get(generate(client, ticker="AAPL")).text
    sources = html.split("<h3>Sources</h3>")[1]
    assert '<span class="badge plain">YouTube</span>' in sources
    assert '<span class="badge plain">blog</span>' in sources
    assert (
        '<a href="https://www.youtube.com/watch?v=sampleVid01&amp;t=75s" rel="noopener noreferrer" '
        'target="_blank" class="timestamp" title="Watch from 1:15">1:15</a> First point of:'
    ) in sources
    assert "Second point, time unknown." in sources
    assert "times link to that moment" in sources
    assert "Sample Markets" in sources and "Sample Tech Blog" in sources


def test_video_waiting_for_a_later_run_says_so(client, report_services, fake_fetch):
    with_blog_and_channel(report_services, fake_fetch, max_videos_per_run=0)
    html = client.get(generate(client, ticker="AAPL")).text
    assert "Not watched by the AI yet; it will be summarized in a later report." in html
    assert "2 new video(s) will be watched in a later run" in html


# --- Phase 7: valuation, holders, followed funds, unusual options (F4.7, F4.8) ---------


def section(html, name):
    return html.split(f"<h3>{name}</h3>")[1].split("<h3>")[0]


def test_f4_7_valuation_snapshot_is_compact_with_details_folded(client):
    html = section(client.get(generate(client, ticker="AAPL")).text, "Valuation snapshot")
    # Now, then own history average with "vs", peers, sector, Damodaran: P/E 30 vs 25 (+20%),
    # peers 15 (+100%), sector 40 (−25%), industry average 26 (+15%).
    pe_row = html.split('<th scope="row">P/E</th>')[1].split("</tr>")[0]
    assert "<strong>30.0</strong>" in pe_row
    for shown in ("25.0", "+20%", "15.0", "+100%", "40.0", "−25%", "26.0", "+15%"):
        assert shown in pe_row
    assert "Industry avg: Damodaran, Electronics (Consumer &amp; Office), January 2026." in html
    targets = " ".join(html.split("Analyst targets")[1].split("</p>")[0].split())
    assert targets.startswith('(40): low $180.00 <span class="vs">−10%</span>')
    assert 'average $230.00 <span class="vs">+15%</span>' in targets
    # Peer list and yearly figures are folded away.
    assert '<details class="more"><summary>Peers and yearly figures</summary>' in html
    assert "SONY" in html.split("<details")[1]


def test_amzn_valuation_says_technology_is_the_owners_setting(client):
    html = section(client.get(generate(client, ticker="AMZN")).text, "Valuation snapshot")
    assert "Sector: Technology&#39;s 4 largest (your setting) · " in html


def test_f4_8_holders_funds_and_options_shown(client, fake_market_data):
    from datetime import date

    from app.providers.market_data import OptionContractData

    fake_market_data.extra_contracts = [
        OptionContractData("AAPLX", "call", 300.0, volume=1200, open_interest=100)
    ]
    import app.services.market_facts as mf

    original = mf.today_eastern
    mf.today_eastern = lambda: date(2026, 11, 4)
    try:
        html = section(
            client.get(generate(client, ticker="AAPL")).text,
            "Institutional &amp; insider activity",
        )
    finally:
        mf.today_eastern = original
    assert "Institutions hold <strong>62.3%</strong> of the shares (6,512 institutions" in html
    assert "<summary>Top 3 institutional holders</summary>" in html
    assert "Funds you follow (13F reports for the quarter ending Jun 30, 2026)" in html
    assert "<strong>Berkshire Hathaway</strong>" in html and "Held, unchanged" in html
    assert "Unusual options activity:" in html and "<strong>4 contracts</strong>" in html
    assert "Nov 20, 2026 300 call" in html


def test_reports_made_before_phase_7_still_show(client, engine):
    generate(client, ticker="AAPL")
    with Session(engine) as session:
        report = session.exec(select(Report)).first()
        body = dict(report.body)
        for key in ("institutional", "unusual_options"):
            body.pop(key)
        body["valuation"] = None
        report.body = body
        session.add(report)
        session.commit()
        report_id = report.id
    html = client.get(f"/reports/{report_id}").text
    assert "Not part of reports made before valuations were added." in html
    assert "Unusual options activity" not in html
