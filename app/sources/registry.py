"""Builds the list of switched-on source plug-ins from `config/sources.yaml` and `.env`."""

from dataclasses import dataclass

from app.config import Settings, SourcesConfig
from app.sources.base import Fetch, Source
from app.sources.blogs import BlogSource
from app.sources.edgar import EdgarSource
from app.sources.http import HttpFetcher
from app.sources.news_rss import GoogleNewsSource, YahooNewsSource
from app.sources.youtube import YouTubeSource

NEWS_USER_AGENT = "Mozilla/5.0 (compatible; FinanceDashboard/0.1; personal research)"
# Seconds between SEC requests: stays well under the SEC's 10-per-second limit.
SEC_MIN_INTERVAL = 0.15


@dataclass
class SourceSet:
    """The switched-on sources, plus EDGAR's ticker -> company name lookup for news searches."""

    sources: list[Source]
    edgar: EdgarSource | None


def build_sources(settings: Settings, config: SourcesConfig) -> SourceSet:
    news_fetch = HttpFetcher(NEWS_USER_AGENT, settings.source_timeout_seconds)
    sec_fetch = HttpFetcher(
        settings.sec_user_agent or NEWS_USER_AGENT,
        settings.source_timeout_seconds,
        min_interval_seconds=SEC_MIN_INTERVAL,
    )
    edgar = EdgarSource(sec_fetch, config.sec_edgar, settings.sec_user_agent)
    sources: list[Source] = []
    if config.news.google_news.enabled:
        sources.append(GoogleNewsSource(news_fetch, config.news))
    if config.news.yahoo_finance.enabled:
        sources.append(YahooNewsSource(news_fetch, config.news))
    if config.sec_edgar.enabled:
        sources.append(edgar)
    sources += blog_and_video_sources(news_fetch, config)
    return SourceSet(sources=sources, edgar=edgar)


def blog_and_video_sources(fetch: Fetch, config: SourcesConfig) -> list[Source]:
    """One source per trusted blog and per YouTube channel, so each failure is named."""
    aliases = {ticker.upper(): names for ticker, names in config.also_known_as.items()}
    sources: list[Source] = []
    if config.blogs.enabled:
        sources += [BlogSource(fetch, blog, aliases) for blog in config.blogs.feeds]
    if config.youtube.enabled:
        sources += [
            YouTubeSource(fetch, channel, config.youtube, aliases)
            for channel in config.youtube.channels
        ]
    return sources
