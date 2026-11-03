"""Building reports (SPEC F4.1, F4.2, F4.6, F4.8, F4.11-F4.14). Fake internet, fake AI."""

from datetime import timedelta

import pytest
from sqlmodel import select

from app.ai.llm import Claim
from app.config import AppConfig, EdgarConfig, NewsConfig, SourcesConfig
from app.db.models import Report, ReportRun, Watchlist
from app.services import reports as reports_service
from app.services.reports import (
    ReportServices,
    execute_run,
    mark_interrupted_runs,
    running_run,
    start_run,
)
from app.services.users import ensure_default_user
from app.sources.edgar import EdgarSource
from app.sources.news_rss import GoogleNewsSource, YahooNewsSource
from app.sources.registry import SourceSet

UA = "Test Person test.person@mail.test"


@pytest.fixture
def user_id(session):
    return ensure_default_user(session, "owner@localhost").id


@pytest.fixture
def services(fake_fetch, fake_llm, sources_now):
    edgar = EdgarSource(fake_fetch, EdgarConfig(), UA)
    news = NewsConfig()
    return ReportServices(
        sources=SourceSet(
            [GoogleNewsSource(fake_fetch, news), YahooNewsSource(fake_fetch, news), edgar], edgar
        ),
        llm=fake_llm,
        llm_problem=None,
        config=AppConfig(),
        sources_config=SourcesConfig(),
    )


def run_for(session, engine, user_id, services, tickers=("AAPL",)) -> ReportRun:
    run = start_run(session, user_id, list(tickers), label="Test")
    execute_run(engine, run.id, list(tickers), services)
    session.expire_all()
    return session.get(ReportRun, run.id)


def latest_report(session, ticker="AAPL") -> Report:
    query = select(Report).where(Report.ticker == ticker).order_by(Report.id.desc())
    return session.exec(query).first()


def test_full_report_for_one_ticker(session, engine, user_id, services, sources_now):
    run = run_for(session, engine, user_id, services)
    assert (run.status, run.done, run.total) == ("done", 1, 1)
    report = latest_report(session)
    body = report.body
    assert report.missing == []
    assert body["company"] == "Apple"
    # F4.14: when it was made and the period covered (first report: the last 7 days).
    assert report.generated_at.replace(tzinfo=None) == sources_now.replace(tzinfo=None)
    assert report.period_end.replace(tzinfo=None) == sources_now.replace(tzinfo=None)
    assert (report.period_end - report.period_start) == timedelta(days=7)
    # Sources: 8 stories (the Reuters/CNBC pair is one story with both sources, F4.12).
    stories = body["stories"]
    assert len(stories) == 8
    pair = next(s for s in stories if len(s["items"]) == 2)
    assert {i["outlet"] for i in pair["items"]} == {"Reuters", "CNBC"}
    assert all(
        {"title", "outlet", "url", "published_at"} <= set(i) for s in stories for i in s["items"]
    )
    assert not any(s["form"] == "4" for s in stories)  # Form 4s are in the insider section


def test_every_statement_cites_a_stored_story(session, engine, user_id, services):
    run_for(session, engine, user_id, services)
    body = latest_report(session).body
    story_refs = {s["ref"] for s in body["stories"]}
    assert body["developments"] and body["rumors"]
    for claim in body["developments"] + body["rumors"]:
        assert claim["refs"] and set(claim["refs"]) <= story_refs
    rumor_titles = [c["text"] for c in body["rumors"]]
    assert rumor_titles == [
        "Rumor: Report: Apple said to be exploring a purchase of an example chip startup."
    ]


def test_uncited_and_wrongly_cited_statements_are_dropped(
    session, engine, user_id, services, fake_llm
):
    fake_llm.extra_claims = [
        Claim("No source at all.", ()),
        Claim("Cites something we never sent.", (999_999,)),
    ]
    run_for(session, engine, user_id, services)
    texts = [c["text"] for c in latest_report(session).body["developments"]]
    assert "No source at all." not in texts
    assert "Cites something we never sent." not in texts
    assert texts  # the properly cited ones remain


def test_sentiment_section(session, engine, user_id, services):
    run_for(session, engine, user_id, services)
    sentiment = latest_report(session).body["sentiment"]
    # Fake AI: 2 bullish stories ("record", "beat"), 6 neutral -> 100 x 2 / 8 = 25.
    assert sentiment["counts"] == {"bullish": 2, "neutral": 6, "bearish": 0}
    assert sentiment["score"] == 25
    assert sentiment["rated"] == 8
    assert sentiment["weights"]["news"] == 1.0 and sentiment["weights"]["reddit"] == 0.3


def test_insider_section(session, engine, user_id, services):
    run_for(session, engine, user_id, services)
    insider = latest_report(session).body["insider"]
    assert insider["lookback_days"] == 90
    assert (insider["buys"], insider["sells"]) == (1, 2)
    assert insider["value_sold"] == pytest.approx(3_757_500.0)
    assert insider["value_bought"] == pytest.approx(230_000.0)
    assert insider["trades"][0]["source"]["url"].endswith("xslF345X05/wk-form4_1730412312.xml")
    assert [h["form"] for h in insider["holder_filings"]] == ["SCHEDULE 13G"]


def test_valuation_is_left_for_a_later_phase(session, engine, user_id, services):
    run_for(session, engine, user_id, services)
    assert latest_report(session).body["valuation"] is None


def test_next_report_covers_the_time_since_the_previous_one(
    session, engine, user_id, services, sources_now, monkeypatch
):
    run_for(session, engine, user_id, services)
    later = sources_now + timedelta(hours=20)
    monkeypatch.setattr(reports_service, "utc_now", lambda: later)
    run_for(session, engine, user_id, services)
    report = latest_report(session)
    assert report.period_start.replace(tzinfo=None) == sources_now.replace(tzinfo=None)
    assert report.body["stories"] == []  # nothing new was published in those 20 hours


def test_failing_source_is_listed_and_report_still_made(
    session, engine, user_id, services, fake_fetch
):
    fake_fetch.down.add("https://news.google.com")
    run_for(session, engine, user_id, services)
    report = latest_report(session)
    assert report.missing == ["Google News: The site didn't answer in time."]
    assert len(report.body["stories"]) == 5  # Yahoo + SEC still there


def test_without_ai_the_report_lists_sources_but_writes_nothing(session, engine, user_id, services):
    services.llm = None
    services.llm_problem = "AI summaries are off: set GEMINI_API_KEY in the .env file."
    run_for(session, engine, user_id, services)
    report = latest_report(session)
    assert report.missing == [
        "AI summaries: AI summaries are off: set GEMINI_API_KEY in the .env file."
    ]
    assert report.body["developments"] == [] and report.body["rumors"] == []
    assert len(report.body["stories"]) == 8
    assert all(s["summary"] is None for s in report.body["stories"])
    assert report.body["sentiment"]["score"] is None


def test_ai_failure_while_writing_is_listed(session, engine, user_id, services, fake_llm):
    def broken(*args, **kwargs):
        from app.ai.llm import AIError

        raise AIError("The AI service didn't respond properly (TimeoutError).")

    fake_llm.write_report = broken
    run_for(session, engine, user_id, services)
    report = latest_report(session)
    assert report.missing == [
        "AI report writing: The AI service didn't respond properly (TimeoutError)."
    ]
    assert report.body["developments"] == []
    assert report.body["sentiment"]["score"] == 25  # item ratings still count


def test_watchlist_names_and_contents_never_reach_the_ai(
    session, engine, user_id, services, fake_llm
):
    session.add(Watchlist(user_id=user_id, name="Secret Strategy List"))
    session.commit()
    run_for(session, engine, user_id, services, tickers=("AAPL", "MSFT"))
    sent = repr(fake_llm.item_calls) + repr(fake_llm.report_calls)
    assert "Secret Strategy" not in sent
    # Each AI call is about one ticker only: no call mentions both tickers' subjects.
    for ticker, _company, _items in fake_llm.item_calls + fake_llm.report_calls:
        assert ticker in ("AAPL", "MSFT")


def test_watchlist_run_summary(session, engine, user_id, services):
    run = run_for(session, engine, user_id, services, tickers=("AAPL", "MSFT"))
    rows = run.summary["tickers"]
    assert [r["ticker"] for r in rows] == ["AAPL", "MSFT"]
    aapl = rows[0]
    assert aapl["score"] == 25
    assert aapl["top"]["refs"]  # the headline statement keeps its citation
    assert aapl["report_id"] == latest_report(session, "AAPL").id
    assert rows[1]["missing"] > 0  # no Yahoo or SEC samples for MSFT


def test_one_ticker_crashing_does_not_lose_the_others(
    session, engine, user_id, services, monkeypatch
):
    real = reports_service.build_ticker_report

    def flaky(session, run, ticker, services):
        if ticker == "MSFT":
            raise RuntimeError("bug")
        return real(session, run, ticker, services)

    monkeypatch.setattr(reports_service, "build_ticker_report", flaky)
    run = run_for(session, engine, user_id, services, tickers=("MSFT", "AAPL"))
    assert run.status == "done" and run.done == 2
    assert run.summary["failed"] == ["MSFT"]
    assert [r["ticker"] for r in run.summary["tickers"]] == ["AAPL"]


def test_running_and_interrupted_runs(session, user_id):
    run = start_run(session, user_id, ["AAPL"], label="AAPL")
    assert running_run(session, user_id).id == run.id
    mark_interrupted_runs(session)
    session.refresh(run)
    assert run.status == "failed" and "stopped" in run.error
    assert running_run(session, user_id) is None


def test_runs_in_a_real_background_thread(session, engine, user_id, services):
    import threading

    from app.main import run_in_background

    run = start_run(session, user_id, ["AAPL"], label="AAPL")
    run_in_background(engine, run.id, ["AAPL"], services)
    thread = next(t for t in threading.enumerate() if t.name == f"report-run-{run.id}")
    thread.join(timeout=30)
    session.expire_all()
    finished = session.get(ReportRun, run.id)
    assert finished.status == "done" and finished.done == 1
    assert latest_report(session).body["stories"]


def test_insider_section_records_whether_sec_was_collected(session, engine, user_id, services):
    run_for(session, engine, user_id, services)
    assert latest_report(session).body["insider"]["available"] is True
    services.sources.edgar._user_agent_ok = False
    run_for(session, engine, user_id, services, tickers=("MSFT",))
    assert latest_report(session, "MSFT").body["insider"]["available"] is False
