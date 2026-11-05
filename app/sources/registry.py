"""Builds the list of switched-on source plug-ins from `config/sources.yaml`,
`config/x.yaml` and `.env`."""

from dataclasses import dataclass, field

from sqlalchemy import Engine

from app.config import Settings, SourcesConfig, XConfig
from app.sources.base import Fetch, LateSource, Source
from app.sources.blogs import BlogSource
from app.sources.edgar import EdgarSource
from app.sources.http import HttpFetcher
from app.sources.news_rss import GoogleNewsSource, YahooNewsSource
from app.sources.x import XAccountsSource, XApi, XLedger, XTickerSearchSource
from app.sources.youtube import YouTubeSource

NEWS_USER_AGENT = "Mozilla/5.0 (compatible; FinanceDashboard/0.1; personal research)"
# Seconds between SEC requests: stays well under the SEC's 10-per-second limit.
SEC_MIN_INTERVAL = 0.15


@dataclass
class SourceSet:
    """The switched-on sources, plus EDGAR's ticker -> company name lookup for news searches."""

    sources: list[Source]
    edgar: EdgarSource | None
    # The downloaders, shared with the funds' 13F reader (same SEC pace) and Damodaran's pages.
    web_fetch: Fetch | None = None
    sec_fetch: Fetch | None = None
    # Run after every ticker is collected (the X $TICKER search), see collect.collect_late.
    late_sources: list[LateSource] = field(default_factory=list)
    # X's spending so far (None when X is off), shown in reports.
    x_ledger: XLedger | None = None

    def source_types(self) -> set[str]:
        """The source types switched on, e.g. {"news", "filing", "x"}."""
        return {s.source_type for s in [*self.sources, *self.late_sources]}


def build_sources(
    settings: Settings,
    config: SourcesConfig,
    x_config: XConfig | None = None,
    engine: Engine | None = None,
) -> SourceSet:
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
    found = SourceSet(sources=sources, edgar=edgar, web_fetch=news_fetch, sec_fetch=sec_fetch)
    if x_config is not None and x_config.enabled and engine is not None:
        api = XApi(settings.x_bearer_token, settings.source_timeout_seconds)
        add_x_sources(found, api, XLedger(engine, x_config), x_config, config)
    return found


def add_x_sources(
    found: SourceSet, api: XApi, ledger: XLedger, x_config: XConfig, config: SourcesConfig
) -> None:
    """X: trusted accounts with the other sources, then the $TICKER search (SPEC F4.3)."""
    aliases = {ticker.upper(): names for ticker, names in config.also_known_as.items()}
    found.x_ledger = ledger
    found.sources.append(XAccountsSource(api, ledger, x_config, aliases))
    if x_config.ticker_search:
        found.late_sources.append(XTickerSearchSource(api, ledger, x_config))


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
