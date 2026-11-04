"""Building reports (SPEC F4.1, F4.2, F4.6, F4.8, F4.11-F4.14). Fake internet, fake AI."""

from datetime import UTC, datetime, timedelta

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


def test_a_report_without_ai_does_not_use_up_the_period(
    session, engine, user_id, services, sources_now, monkeypatch, fake_llm
):
    """Owner, 2026-11-04: the AI failed (402) at 12:05, the next run at 12:12 covered only
    7 minutes, so the news before 12:05 never got AI summaries."""
    fake_llm.fail = "The AI service answered with an error (402)."
    run_for(session, engine, user_id, services)
    failed = latest_report(session)
    assert failed.body["ai_complete"] is False
    assert all(s["summary"] is None for s in failed.body["stories"])

    fake_llm.fail = None
    later = sources_now + timedelta(minutes=7)
    monkeypatch.setattr(reports_service, "utc_now", lambda: later)
    run_for(session, engine, user_id, services)
    report = latest_report(session)
    # The failed report didn't use up the period: the new one reaches back the full 7 days
    # (there's no earlier report with AI), not just the 7 minutes since the failed run.
    assert report.period_start < failed.period_end
    assert report.period_start == later - timedelta(days=7)
    assert report.body["ai_complete"] is True
    assert len(report.body["stories"]) == 8
    assert all(s["summary"] for s in report.body["stories"] if s["source_type"] == "news")
    assert report.body["developments"]


def test_next_report_after_one_with_ai_starts_where_it_ended(
    session, engine, user_id, services, sources_now, monkeypatch, fake_fetch
):
    """A source being down doesn't extend the next period: only the AI matters."""
    fake_fetch.down.add("https://news.google.com")
    run_for(session, engine, user_id, services)
    first = latest_report(session)
    assert first.missing and first.body["ai_complete"] is True
    later = sources_now + timedelta(hours=3)
    monkeypatch.setattr(reports_service, "utc_now", lambda: later)
    run_for(session, engine, user_id, services)
    assert latest_report(session).period_start == first.period_end


def test_reports_made_before_the_flag_are_judged_by_their_notes():
    from app.services.reports import had_ai

    assert had_ai(
        Report(
            user_id=1,
            run_id=1,
            ticker="AAPL",
            period_start=datetime.now(UTC),
            period_end=datetime.now(UTC),
            body={},
            missing=[],
        )
    )
    old_failed = Report(
        user_id=1,
        run_id=1,
        ticker="AAPL",
        period_start=datetime.now(UTC),
        period_end=datetime.now(UTC),
        body={},
        missing=["AI summaries: AI summaries are off: set GEMINI_API_KEY."],
    )
    assert not had_ai(old_failed)
    source_down = Report(
        user_id=1,
        run_id=1,
        ticker="AAPL",
        period_start=datetime.now(UTC),
        period_end=datetime.now(UTC),
        body={},
        missing=["Google News: The site didn't answer in time."],
    )
    assert had_ai(source_down)


def test_never_any_ai_means_the_full_seven_days(
    session, engine, user_id, services, sources_now, monkeypatch
):
    services.llm = None
    services.llm_problem = "AI summaries are off: set GEMINI_API_KEY in the .env file."
    run_for(session, engine, user_id, services)
    later = sources_now + timedelta(hours=1)
    monkeypatch.setattr(reports_service, "utc_now", lambda: later)
    run_for(session, engine, user_id, services)
    report = latest_report(session)
    assert report.period_end - report.period_start == timedelta(days=7)


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


def test_after_a_failed_run_the_period_starts_at_the_last_report_with_ai(session, user_id):
    from app.services.reports import report_period

    t0 = datetime(2026, 11, 2, 12, 0, tzinfo=UTC)
    stored = [
        (t0, []),  # a good report
        (t0 + timedelta(hours=10), ["AI summaries: The AI service answered with an error (402)."]),
        (t0 + timedelta(hours=11), ["AI report writing: The AI service didn't respond properly."]),
    ]
    for end, missing in stored:
        session.add(
            Report(
                user_id=user_id,
                run_id=0,
                ticker="AMZN",
                period_start=end - timedelta(days=1),
                period_end=end,
                body={},
                missing=missing,
            )
        )
    session.commit()
    start, end = report_period(session, user_id, "AMZN", t0 + timedelta(hours=12), max_days=7)
    assert start == t0  # both failed reports are covered again
    # Other tickers and users are unaffected.
    start, _ = report_period(session, user_id, "AAPL", t0 + timedelta(hours=12), max_days=7)
    assert start == t0 + timedelta(hours=12) - timedelta(days=7)


def test_a_run_that_needed_no_ai_counts_as_complete(
    session, engine, user_id, services, sources_now, monkeypatch, fake_llm
):
    """If nothing new needed summarizing, a broken AI didn't cost anything: the period moves on."""
    run_for(session, engine, user_id, services)
    fake_llm.fail = "The AI service answered with an error (402)."
    monkeypatch.setattr(reports_service, "utc_now", lambda: sources_now + timedelta(hours=10))
    run_for(session, engine, user_id, services)
    quiet = latest_report(session)
    assert quiet.missing == [] and quiet.body["ai_complete"] is True
    fake_llm.fail = None
    monkeypatch.setattr(reports_service, "utc_now", lambda: sources_now + timedelta(hours=11))
    run_for(session, engine, user_id, services)
    assert latest_report(session).period_start == quiet.period_end


def test_news_left_without_ai_is_caught_up_even_after_a_quiet_report(
    session, engine, user_id, services, sources_now, monkeypatch, fake_llm
):
    """Owner, 2026-11-04 (after the first fix): 12:05 the AI failed (402), so that news was
    never summarized. 12:12 a quiet report ran with the AI working but nothing new in its 7
    minutes, so it counted as a report "with AI". Every later report started after it, and
    the 12:05 news was never summarized. The news itself must be checked, not just reports."""
    fake_llm.fail = "The AI service answered with an error (402)."
    run_for(session, engine, user_id, services)  # "12:05": AI fails, news stays unsummarized
    failed = latest_report(session)
    assert failed.body["stories"] and all(s["summary"] is None for s in failed.body["stories"])

    # "12:12": a quiet report with no AI problems, as the old code made it.
    session.add(
        Report(
            user_id=user_id,
            run_id=0,
            ticker="AAPL",
            period_start=failed.period_end,
            period_end=failed.period_end + timedelta(minutes=7),
            body={"ai_complete": True, "stories": []},
            missing=[],
        )
    )
    session.commit()

    fake_llm.fail = None
    later = sources_now + timedelta(minutes=23)  # "12:28"
    monkeypatch.setattr(reports_service, "utc_now", lambda: later)
    run_for(session, engine, user_id, services)
    report = latest_report(session)
    assert len(report.body["stories"]) == 8  # the news from the failed run is back...
    news = [s for s in report.body["stories"] if s["source_type"] == "news"]
    assert news and all(s["summary"] for s in news)  # ...with AI summaries
    assert report.body["developments"]
