"""News feeds (SPEC F4.3, F4.4): Google News and Yahoo Finance, read from recorded samples."""

from datetime import UTC, datetime, timedelta

import pytest

from app.config import GoogleNewsConfig, NewsConfig
from app.sources.base import SourceError
from app.sources.news_rss import (
    GoogleNewsSource,
    YahooNewsSource,
    google_query,
    outlet_from_url,
)


@pytest.fixture
def since(sources_now):
    return sources_now - timedelta(days=7)


def test_google_news_items_within_the_window(fake_fetch, since):
    items = GoogleNewsSource(fake_fetch, NewsConfig()).fetch("AAPL", "Apple", since)
    # The October 12 story is too old and the non-HTTPS link is refused.
    assert [i.outlet for i in items] == ["Reuters", "CNBC", "Business Wire", "Example Rumor Blog"]
    first = items[0]
    assert first.title == "Apple posts record September-quarter revenue as services climb"
    assert first.url == "https://news.google.com/rss/articles/CBMiSAMPLE0001?oc=5"
    assert first.published_at == datetime(2026, 10, 30, 20, 45, tzinfo=UTC)
    assert first.source == "google_news"
    assert first.source_type == "news"
    assert first.ticker == "AAPL"


def test_google_news_search_text(fake_fetch, since):
    GoogleNewsSource(fake_fetch, NewsConfig()).fetch("AAPL", "Apple", since)
    assert "q=%22Apple%22+OR+%22AAPL+stock%22+when%3A7d" in fake_fetch.calls[0]


def test_google_query_without_company_and_short_window(sources_now):
    config = GoogleNewsConfig()
    since = sources_now - timedelta(hours=30)
    assert google_query(config, "SPY", None, since) == '"SPY stock" when:2d'


def test_trusted_outlets_only_drops_other_outlets(fake_fetch, since):
    config = NewsConfig(
        trusted_outlets_only=True, trusted_outlets=["reuters", "CNBC", "Business Wire"]
    )
    items = GoogleNewsSource(fake_fetch, config).fetch("AAPL", "Apple", since)
    assert {i.outlet for i in items} == {"Reuters", "CNBC", "Business Wire"}


def test_yahoo_news_items_and_outlets(fake_fetch, since):
    items = YahooNewsSource(fake_fetch, NewsConfig()).fetch("AAPL", None, since)
    assert [i.outlet for i in items] == ["Yahoo Finance", "The Motley Fool"]
    assert items[0].text.startswith("Apple results beat estimates on services strength\n\n")
    assert items[1].text.endswith("Three things to watch in Apple stock this week.")
    assert items[1].published_at == datetime(2026, 11, 1, 12, 0, tzinfo=UTC)


@pytest.mark.parametrize(
    ("url", "outlet"),
    [
        ("https://finance.yahoo.com/news/x.html", "Yahoo Finance"),
        ("https://www.reuters.com/markets/x", "Reuters"),
        ("https://unknown-site.example.net/a", "unknown-site.example.net"),
    ],
)
def test_outlet_from_url(url, outlet):
    assert outlet_from_url(url) == outlet


def test_feed_that_is_not_a_feed_is_an_error(since):
    def fetch(url: str) -> bytes:
        return b"<html><body>Service unavailable</body"

    with pytest.raises(SourceError, match="readable news feed"):
        YahooNewsSource(fetch, NewsConfig()).fetch("AAPL", None, since)


def test_download_failure_is_a_source_error(fake_fetch, since):
    fake_fetch.down.add("https://news.google.com")
    with pytest.raises(SourceError):
        GoogleNewsSource(fake_fetch, NewsConfig()).fetch("AAPL", "Apple", since)
