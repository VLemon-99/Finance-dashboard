"""Blogs and YouTube channels (SPEC F4.3, F4.4, F4.5), read from hand-made samples."""

from datetime import UTC, datetime, timedelta

import pytest

from app.config import BlogFeed, SourcesConfig, YouTubeChannel, YouTubeConfig
from app.sources.base import SourceError
from app.sources.blogs import BlogSource
from app.sources.feeds import REUSE_SECONDS, CachedFeed
from app.sources.mentions import company_names, mentions
from app.sources.registry import blog_and_video_sources
from app.sources.youtube import YouTubeSource, at_time, watch_url

CHANNEL = YouTubeChannel(name="Sample Markets", channel_id="UCsampleChannelAAAAAAAAA")
BLOG = BlogFeed(name="Sample Tech Blog", feed="https://blog.example.com/feed/")


@pytest.fixture
def since(sources_now):
    return sources_now - timedelta(days=7)


# --- what counts as a mention ---------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "ticker", "company", "expected"),
    [
        ("Apple earnings: what the numbers say", "AAPL", "Apple", True),
        ("why apple slipped today", "AAPL", "Apple", True),  # any case for the name
        ("Pineapple prices are up", "AAPL", "Apple", False),  # whole words only
        ("Covering $AAPL and $MSFT", "AAPL", None, True),
        ("AAPL results this week", "AAPL", None, True),
        ("aapl results", "AAPL", None, False),  # the ticker must be in capitals
        ("AAPLX is a fund", "AAPL", None, False),
        ("Amazon's AWS grew", "AMZN", "AMAZON COM", True),  # SEC writes Amazon.com this way
        ("Berkshire's BRK.B shares", "BRK-B", None, True),
        ("Turn it ON for AI stocks", "ON", None, False),  # short tickers need the $
        ("Why $ON fell", "ON", None, True),
        ("Ford's quarter", "F", "FORD MOTOR", False),  # "FORD MOTOR" isn't in the text
        ("Ford Motor's quarter", "F", "FORD MOTOR", True),
    ],
)
def test_mentions(text, ticker, company, expected):
    assert mentions(text, ticker, company_names(company)) is expected


def test_extra_names_count_as_mentions():
    names = company_names("Alphabet", ["Google", " "])
    assert names == ["Alphabet", "Google"]
    assert mentions("Google's new search ads", "GOOGL", names)


# --- blogs ----------------------------------------------------------------------------


def test_blog_posts_that_mention_the_ticker(fake_fetch, since):
    items = BlogSource(fake_fetch, BLOG, {}).fetch("AAPL", "Apple", since)
    # The capex post doesn't mention Apple; the October 1 post is too old.
    assert [i.title for i in items] == ["Apple's services flywheel keeps spinning", "Weekly links"]
    first = items[0]
    assert first.source == "blog"
    assert first.source_type == "blog"
    assert first.outlet == "Sample Tech Blog"
    assert first.url == "https://blog.example.com/2026/11/01/apple-services-flywheel/"
    assert first.published_at == datetime(2026, 11, 1, 13, 0, tzinfo=UTC)
    # The full post from the feed, not just the short excerpt.
    assert "App Store, subscriptions" in first.text


def test_blog_extra_names(fake_fetch, since):
    items = BlogSource(fake_fetch, BLOG, {"MSFT": ["Microsoft"]}).fetch("AMZN", None, since)
    assert [i.title for i in items] == ["Notes on cloud capex"]  # "$AMZN" in the summary


def test_blog_down_is_a_named_source_error(fake_fetch, since):
    fake_fetch.down.add("https://blog.example.com/")
    source = BlogSource(fake_fetch, BLOG, {})
    assert source.label == "Blog: Sample Tech Blog"
    with pytest.raises(SourceError):
        source.fetch("AAPL", "Apple", since)


# --- YouTube --------------------------------------------------------------------------


def test_youtube_videos_that_mention_the_ticker(fake_fetch, since):
    source = YouTubeSource(fake_fetch, CHANNEL, YouTubeConfig(), {})
    items = source.fetch("AAPL", "Apple", since)
    # Video 1 names Apple in the title, video 2 has $AAPL in the description; video 3 is
    # about the Fed only; video 4 is older than 7 days.
    assert [i.data["video_id"] for i in items] == ["sampleVid01", "sampleVid02"]
    first = items[0]
    assert first.source == "youtube"
    assert first.source_type == "youtube"
    assert first.outlet == "Sample Markets"
    assert first.url == "https://www.youtube.com/watch?v=sampleVid01"
    assert first.published_at == datetime(2026, 11, 1, 14, 0, tzinfo=UTC)
    assert "line by line" in first.data["description"]
    assert fake_fetch.calls == [
        "https://www.youtube.com/feeds/videos.xml?channel_id=UCsampleChannelAAAAAAAAA"
    ]


def test_youtube_only_recent_videos(fake_fetch, sources_now):
    # Even if the report period is longer, only the last `lookback_days` count (F4.5).
    config = YouTubeConfig(lookback_days=1)
    source = YouTubeSource(fake_fetch, CHANNEL, config, {})
    items = source.fetch("AAPL", "Apple", sources_now - timedelta(days=30))
    assert [i.data["video_id"] for i in items] == ["sampleVid01"]


def test_video_links():
    assert watch_url("sampleVid01") == "https://www.youtube.com/watch?v=sampleVid01"
    assert at_time(watch_url("sampleVid01"), 195).endswith("watch?v=sampleVid01&t=195s")
    assert at_time("https://www.youtube.com/watch?v=sampleVid01", None).endswith("01")
    with pytest.raises(ValueError):
        watch_url("not a video id!")


def test_channel_id_is_checked():
    with pytest.raises(ValueError):
        YouTubeChannel(name="Bad", channel_id="@somechannel")


# --- one download per run ---------------------------------------------------------------


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def test_a_feed_is_downloaded_once_and_reused(fake_fetch):
    clock = FakeClock()
    feed = CachedFeed(fake_fetch, "https://blog.example.com/feed/", "Blog", clock)
    assert len(feed.entries()) == 4
    feed.entries()
    assert len(fake_fetch.calls) == 1
    clock.now += REUSE_SECONDS
    feed.entries()
    assert len(fake_fetch.calls) == 2


def test_a_failed_feed_is_not_retried_for_every_ticker(fake_fetch):
    fake_fetch.down.add("https://blog.example.com/")
    clock = FakeClock()
    feed = CachedFeed(fake_fetch, "https://blog.example.com/feed/", "Blog", clock)
    for _ in range(3):
        with pytest.raises(SourceError):
            feed.entries()
    assert len(fake_fetch.calls) == 1
    clock.now += REUSE_SECONDS
    fake_fetch.down.clear()
    assert len(feed.entries()) == 4


class FlakyFetch:
    """Fails the first `failures` downloads with the given error, then answers normally."""

    def __init__(self, fetch, failures: int, message: str) -> None:
        self.fetch, self.failures, self.message = fetch, failures, message
        self.calls = 0

    def __call__(self, url: str) -> bytes:
        self.calls += 1
        if self.calls <= self.failures:
            raise SourceError(self.message)
        return self.fetch(url)


def test_youtube_feed_is_tried_again_after_a_moment(fake_fetch, since, no_feed_retry_wait):
    # Seen 2026-11-04: a channel's feed answered 500, and worked again later.
    flaky = FlakyFetch(fake_fetch, 1, "The site answered with an error (500).")
    source = YouTubeSource(flaky, CHANNEL, YouTubeConfig(), {})
    assert source.fetch("AAPL", "Apple", since)  # the second try worked
    assert flaky.calls == 2
    assert no_feed_retry_wait == [5.0]  # waited 5 seconds in between
    source.fetch("MSFT", "Microsoft", since)
    assert flaky.calls == 2  # still once per run, not per ticker


def test_youtube_feed_failing_twice_is_reported_once(fake_fetch, since, no_feed_retry_wait):
    flaky = FlakyFetch(fake_fetch, 5, "The site says this page doesn't exist (404).")
    source = YouTubeSource(flaky, CHANNEL, YouTubeConfig(), {})
    for ticker in ("AAPL", "MSFT"):
        with pytest.raises(SourceError, match="404"):
            source.fetch(ticker, None, since)
    assert flaky.calls == 2  # one try plus one retry, shared by both tickers
    assert no_feed_retry_wait == [5.0]


def test_retries_follow_the_settings(fake_fetch, since, no_feed_retry_wait):
    flaky = FlakyFetch(fake_fetch, 5, "The site answered with an error (500).")
    source = YouTubeSource(flaky, CHANNEL, YouTubeConfig(retries=0), {})
    with pytest.raises(SourceError):
        source.fetch("AAPL", None, since)
    assert flaky.calls == 1 and no_feed_retry_wait == []


def test_blogs_are_not_retried(fake_fetch, since, no_feed_retry_wait):
    flaky = FlakyFetch(fake_fetch, 1, "The site answered with an error (500).")
    with pytest.raises(SourceError):
        BlogSource(flaky, BLOG, {}).fetch("AAPL", "Apple", since)
    assert flaky.calls == 1


def test_one_source_per_blog_and_channel(fake_fetch):
    config = SourcesConfig.model_validate(
        {
            "blogs": {"feeds": [BLOG.model_dump()]},
            "youtube": {"channels": [CHANNEL.model_dump()]},
            "also_known_as": {"googl": ["Google"]},
        }
    )
    sources = blog_and_video_sources(fake_fetch, config)
    assert [s.label for s in sources] == ["Blog: Sample Tech Blog", "YouTube: Sample Markets"]
    assert sources[0]._also_known_as == {"GOOGL": ["Google"]}

    off = config.model_copy(
        update={
            "blogs": config.blogs.model_copy(update={"enabled": False}),
            "youtube": config.youtube.model_copy(update={"enabled": False}),
        }
    )
    assert blog_and_video_sources(fake_fetch, off) == []
