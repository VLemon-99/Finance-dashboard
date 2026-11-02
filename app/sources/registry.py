"""Builds the list of switched-on source plug-ins from `config/sources.yaml` and `.env`."""

from dataclasses import dataclass

from app.config import Settings, SourcesConfig
from app.sources.base import Source
from app.sources.edgar import EdgarSource
from app.sources.http import HttpFetcher
from app.sources.news_rss import GoogleNewsSource, YahooNewsSource

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
    return SourceSet(sources=sources, edgar=edgar)
