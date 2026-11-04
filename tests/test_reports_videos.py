"""YouTube videos and blog posts in reports (SPEC F4.3, F4.5, F4.6). Fake internet, fake AI.

Owner's rules (2026-11-04): about 10 new videos per run, and a video is never watched twice.
"""

from datetime import UTC, datetime, timedelta

import pytest
from sqlmodel import delete, select

from app.config import AppConfig, BlogFeed, EdgarConfig, SourcesConfig, YouTubeChannel
from app.db.models import Report, ReportRun, SourceItemRow
from app.services.reports import ReportServices, execute_run, start_run
from app.services.users import ensure_default_user
from app.services.videos import VideoBudget, timestamp_label, video_text, watch_new_videos
from app.sources.edgar import EdgarSource
from app.sources.registry import SourceSet, blog_and_video_sources
from tests.conftest import TEST_SEC_USER_AGENT

CHANNEL = YouTubeChannel(name="Sample Markets", channel_id="UCsampleChannelAAAAAAAAA")
BLOG = BlogFeed(name="Sample Tech Blog", feed="https://blog.example.com/feed/")
VID1 = "https://www.youtube.com/watch?v=sampleVid01"  # "Apple earnings..." (Nov 1)
VID2 = "https://www.youtube.com/watch?v=sampleVid02"  # "$AAPL, $MSFT and $NVDA" (Oct 31)


@pytest.fixture
def user_id(session):
    return ensure_default_user(session, "owner@localhost").id


def make_services(fake_fetch, fake_llm, **ai) -> ReportServices:
    sources_config = SourcesConfig.model_validate(
        {
            "blogs": {"feeds": [BLOG.model_dump()]},
            "youtube": {"channels": [CHANNEL.model_dump()]},
        }
    )
    edgar = EdgarSource(fake_fetch, EdgarConfig(), TEST_SEC_USER_AGENT)  # company names only
    return ReportServices(
        sources=SourceSet(blog_and_video_sources(fake_fetch, sources_config), edgar),
        llm=fake_llm,
        llm_problem=None,
        config=AppConfig.model_validate({"ai": ai}),
        sources_config=sources_config,
    )


@pytest.fixture
def services(fake_fetch, fake_llm, sources_now):
    return make_services(fake_fetch, fake_llm)


def run_for(session, engine, user_id, services, tickers=("AAPL",)) -> ReportRun:
    run = start_run(session, user_id, list(tickers), label="Test")
    execute_run(engine, run.id, list(tickers), services)
    session.expire_all()
    return session.get(ReportRun, run.id)


def latest_report(session, ticker="AAPL") -> Report:
    query = select(Report).where(Report.ticker == ticker).order_by(Report.id.desc())
    return session.exec(query).first()


def video_rows(session, url=None) -> list[SourceItemRow]:
    query = select(SourceItemRow).where(SourceItemRow.source_type == "youtube")
    if url:
        query = query.where(SourceItemRow.url == url)
    return list(session.exec(query.order_by(SourceItemRow.id)).all())


def test_videos_are_watched_and_shown_with_time_links(session, engine, user_id, services, fake_llm):
    run_for(session, engine, user_id, services)
    report = latest_report(session)
    assert report.missing == []
    assert report.body["ai_complete"] is True
    assert sorted(v.url for v in fake_llm.video_calls) == [VID1, VID2]
    assert fake_llm.video_calls[0].max_seconds == 60 * 60  # ai.max_video_minutes default

    stories = {s["title"]: s for s in report.body["stories"]}
    video = stories["Apple earnings: what the iPhone numbers really say"]
    assert video["kind"] == "video"
    assert video["source_type"] == "youtube"
    assert video["items"][0]["outlet"] == "Sample Markets"
    # 3-5 points, each linked to its moment in the video (F4.5).
    assert video["points"] == [
        {
            "text": "First point of: Apple earnings: what the iPhone numbers really say",
            "time": "1:15",
            "url": VID1 + "&t=75s",
        },
        {"text": "Second point, time unknown.", "time": None, "url": VID1},
        {"text": "Third point, past the hour.", "time": "1:02:05", "url": VID1 + "&t=3725s"},
    ]
    # Then rated for the ticker like any article, from what the video says.
    sent = {i.title: i for _, _, items in fake_llm.item_calls for i in items}
    watched = sent["Apple earnings: what the iPhone numbers really say"]
    assert watched.kind == "video"
    assert "What the video says:\n- [1:15] First point of" in watched.text
    assert "line by line" in watched.text  # the description too
    assert video["sentiment"] and video["summary"]

    blog = stories["Apple's services flywheel keeps spinning"]
    assert blog["kind"] == "blog"
    assert "points" not in blog
    assert sent["Weekly links"].kind == "blog"
    # Sentiment weights by source type (F4.6): blog 0.8, YouTube 0.7.
    assert report.body["sentiment"]["weights"]["youtube"] == 0.7
    assert report.body["sentiment"]["weights"]["blog"] == 0.8


def test_a_video_is_never_watched_twice(session, engine, user_id, services, fake_llm):
    # Video 2 mentions both $AAPL and $MSFT: watched once, used for both.
    run_for(session, engine, user_id, services, tickers=("AAPL", "MSFT"))
    assert sorted(v.url for v in fake_llm.video_calls) == [VID1, VID2]
    rows = video_rows(session, VID2)
    assert sorted(r.ticker for r in rows) == ["AAPL", "MSFT"]
    assert rows[0].data["points"] == rows[1].data["points"]
    msft = latest_report(session, "MSFT")
    videos = [s for s in msft.body["stories"] if s["kind"] == "video"]
    assert [s["title"] for s in videos] == ["Three stocks I'm watching this week"]
    assert videos[0]["points"]

    # A later run, and a later run for another ticker, reuse the kept summaries.
    run_for(session, engine, user_id, services, tickers=("AAPL",))
    # Forget MSFT completely (its items and reports), as if it were newly added.
    for table in (SourceItemRow, Report):
        session.exec(delete(table).where(table.ticker == "MSFT"))
    session.commit()
    run_for(session, engine, user_id, services, tickers=("MSFT",))
    assert len(fake_llm.video_calls) == 2
    stories = latest_report(session, "MSFT").body["stories"]
    assert next(s for s in stories if s["kind"] == "video")["points"]


def test_at_most_the_limit_per_run_newest_first_the_rest_wait(
    session, engine, user_id, fake_fetch, fake_llm, sources_now
):
    services = make_services(fake_fetch, fake_llm, max_videos_per_run=1)
    run_for(session, engine, user_id, services)
    assert [v.url for v in fake_llm.video_calls] == [VID1]  # the newer one
    report = latest_report(session)
    assert report.missing == [
        "YouTube: 1 new video(s) will be watched in a later run (limit: 1 new videos per run, "
        "in config/settings.yaml)."
    ]
    waiting = next(s for s in report.body["stories"] if s["kind"] == "video" and not s["points"])
    assert waiting["title"] == "Three stocks I'm watching this week"
    assert waiting["summary"] is None  # not summarized from its description alone

    # The next run reaches back to the waiting video and watches it.
    run_for(session, engine, user_id, services)
    assert [v.url for v in fake_llm.video_calls] == [VID1, VID2]
    later = latest_report(session)
    assert later.missing == []
    assert later.period_start.replace(tzinfo=UTC) <= datetime(2026, 10, 31, 16, 30, tzinfo=UTC)
    # Reaching back also shows video 1 again (already watched, not watched again).
    videos = {s["title"]: s for s in later.body["stories"] if s["kind"] == "video"}
    assert len(videos) == 2
    assert videos["Three stocks I'm watching this week"]["points"]


def test_a_video_that_cant_be_watched_is_rated_from_its_description(
    session, engine, user_id, services, fake_llm
):
    fake_llm.unwatchable.add(VID2)
    run_for(session, engine, user_id, services)
    story = next(
        s
        for s in latest_report(session).body["stories"]
        if s["title"] == "Three stocks I'm watching this week"
    )
    assert story["points"] == []
    assert "couldn't watch" in story["watch_error"]
    assert story["summary"]  # rated from the title and description instead
    # Not tried again later.
    run_for(session, engine, user_id, services)
    assert sorted(v.url for v in fake_llm.video_calls) == [VID1, VID2]


def test_ai_failing_while_watching_leaves_videos_for_next_time(
    session, engine, user_id, services, fake_llm
):
    fake_llm.video_fail = "The AI service's usage limit was reached; try again later."
    run_for(session, engine, user_id, services)
    report = latest_report(session)
    assert report.missing == [
        "AI video summaries: The AI service's usage limit was reached; try again later."
    ]
    assert report.body["ai_complete"] is False  # the next report covers this period again
    assert all("points" not in r.data for r in video_rows(session))

    fake_llm.video_fail = None
    run_for(session, engine, user_id, services)
    assert sorted(v.url for v in fake_llm.video_calls) == [VID1, VID2]
    assert latest_report(session).missing == []


def test_no_ai_means_no_watching(session, engine, user_id, services, fake_llm):
    services.llm = None
    services.llm_problem = "AI summaries are off: set GEMINI_API_KEY in the .env file."
    run_for(session, engine, user_id, services)
    assert fake_llm.video_calls == []
    stories = latest_report(session).body["stories"]
    assert {s["title"] for s in stories if s["kind"] == "video"} == {
        "Apple earnings: what the iPhone numbers really say",
        "Three stocks I'm watching this week",
    }


def test_a_channel_that_is_down_is_named(session, engine, user_id, services, fake_fetch):
    fake_fetch.down.add("https://www.youtube.com/feeds/")
    run_for(session, engine, user_id, services)
    assert latest_report(session).missing == [
        "YouTube: Sample Markets: The site didn't answer in time."
    ]


# --- watching several at once; a 25-ticker run ------------------------------------------


def add_video(session, ticker: str, number: int, published: datetime) -> SourceItemRow:
    row = SourceItemRow(
        ticker=ticker,
        source="youtube",
        source_type="youtube",
        outlet="Sample Markets",
        title=f"Video {number} about {ticker}",
        url=f"https://www.youtube.com/watch?v=sampleV{number:04d}",
        published_at=published,
        text=f"Video {number}",
        data={"video_id": f"sampleV{number:04d}", "description": "An invented video."},
    )
    session.add(row)
    session.flush()
    row.story_id = row.id
    session.commit()
    return row


def test_25_tickers_30_new_videos(session, fake_llm, sources_now):
    """The owner's largest list (25 stocks): 10 videos watched, 3 at a time (Phase 5 "done
    when": a 25-ticker run in under 10 minutes; the AI's watching is the slow part)."""
    tickers = [f"T{chr(65 + n // 26)}{chr(65 + n % 26)}" for n in range(25)]
    for number in range(30):
        add_video(session, tickers[number % 25], number, sources_now - timedelta(hours=number))
    fake_llm.video_seconds = 0.05
    config = AppConfig().ai
    budget = VideoBudget(config.max_videos_per_run)
    periods = {t: sources_now - timedelta(days=7) for t in tickers}
    notes = watch_new_videos(session, fake_llm, periods, config, budget)

    assert len(fake_llm.video_calls) == 10 and budget.remaining == 0
    assert fake_llm.most_at_once == 3  # ai.parallel_videos
    watched = sorted(int(v.url[-4:]) for v in fake_llm.video_calls)
    assert watched == list(range(10))  # the 10 newest
    assert sum(len(n) for n in notes.values()) == 20  # one "will be watched later" note each


def test_video_helpers():
    assert timestamp_label(0) == "0:00"
    assert timestamp_label(195) == "3:15"
    assert timestamp_label(3725) == "1:02:05"
    text = video_text("Title", "", [{"text": "A point.", "seconds": None}])
    assert text == "Title\n\nWhat the video says:\n- A point."
