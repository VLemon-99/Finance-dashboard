"""News feeds: Google News search RSS and Yahoo Finance's headline RSS (SPEC F4.3).

This is the only file that reads news feeds. Company press releases arrive through the
Google News feed (Business Wire, PR Newswire, GlobeNewswire are ordinary outlets there).

Feeds give a headline and, at best, a sentence or two. We store that; we don't fetch the
article pages themselves (many are paywalled, and Google's links are redirects).
"""

import logging
import math
from datetime import datetime
from urllib.parse import quote_plus, urlsplit

import feedparser

from app.config import GoogleNewsConfig, NewsConfig
from app.sources.base import Fetch, SourceError, SourceItem
from app.sources.text import html_to_text, struct_time_to_utc
from app.timeutils import utc_now

log = logging.getLogger(__name__)

GOOGLE_NEWS_URL = "https://news.google.com/rss/search?q={query}&hl=en-US&gl=US&ceid=US:en"
YAHOO_NEWS_URL = "https://feeds.finance.yahoo.com/rss/2.0/headline?s={ticker}&region=US&lang=en-US"

# Yahoo's feed doesn't name the outlet, so we work it out from the link's web address.
_OUTLETS_BY_DOMAIN = {
    "yahoo.com": "Yahoo Finance",
    "reuters.com": "Reuters",
    "bloomberg.com": "Bloomberg",
    "wsj.com": "The Wall Street Journal",
    "ft.com": "Financial Times",
    "cnbc.com": "CNBC",
    "barrons.com": "Barron's",
    "marketwatch.com": "MarketWatch",
    "apnews.com": "Associated Press",
    "nytimes.com": "The New York Times",
    "forbes.com": "Forbes",
    "fortune.com": "Fortune",
    "businessinsider.com": "Business Insider",
    "investors.com": "Investor's Business Daily",
    "fool.com": "The Motley Fool",
    "seekingalpha.com": "Seeking Alpha",
    "morningstar.com": "Morningstar",
    "businesswire.com": "Business Wire",
    "prnewswire.com": "PR Newswire",
    "globenewswire.com": "GlobeNewswire",
}


def _parse_feed(body: bytes, label: str) -> list:
    feed = feedparser.parse(body)
    if feed.get("bozo") and not feed.entries:
        raise SourceError(f"{label} sent something that isn't a readable news feed.")
    return feed.entries


def _keep_outlet(outlet: str, config: NewsConfig) -> bool:
    if not config.trusted_outlets_only:
        return True
    trusted = {name.casefold() for name in config.trusted_outlets}
    return outlet.casefold() in trusted


class GoogleNewsSource:
    name = "google_news"
    label = "Google News"
    source_type = "news"

    def __init__(self, fetch: Fetch, config: NewsConfig) -> None:
        self._fetch = fetch
        self._config = config

    def fetch(self, ticker: str, company: str | None, since: datetime) -> list[SourceItem]:
        url = GOOGLE_NEWS_URL.format(
            query=quote_plus(google_query(self._config.google_news, ticker, company, since))
        )
        items = []
        for entry in _parse_feed(self._fetch(url), self.label):
            published = struct_time_to_utc(entry.get("published_parsed"))
            link = entry.get("link", "")
            if published is None or published < since or not link.startswith("https://"):
                continue
            outlet = (entry.get("source") or {}).get("title") or "Unknown outlet"
            if not _keep_outlet(outlet, self._config):
                continue
            title = _strip_outlet_suffix(html_to_text(entry.get("title", "")), outlet)
            items.append(
                SourceItem(
                    source=self.name,
                    source_type="news",
                    ticker=ticker,
                    outlet=outlet,
                    title=title,
                    url=link,
                    published_at=published,
                    # Google's description only repeats the headline, so the headline is the text.
                    text=title,
                )
            )
        return items

    def enrich(self, item: SourceItem) -> SourceItem:
        return item


class YahooNewsSource:
    name = "yahoo_news"
    label = "Yahoo Finance news"
    source_type = "news"

    def __init__(self, fetch: Fetch, config: NewsConfig) -> None:
        self._fetch = fetch
        self._config = config

    def fetch(self, ticker: str, company: str | None, since: datetime) -> list[SourceItem]:
        items = []
        for entry in _parse_feed(self._fetch(YAHOO_NEWS_URL.format(ticker=ticker)), self.label):
            published = struct_time_to_utc(entry.get("published_parsed"))
            link = entry.get("link", "")
            if published is None or published < since or not link.startswith("https://"):
                continue
            outlet = outlet_from_url(link)
            if not _keep_outlet(outlet, self._config):
                continue
            title = html_to_text(entry.get("title", ""))
            summary = html_to_text(entry.get("summary", ""))
            items.append(
                SourceItem(
                    source=self.name,
                    source_type="news",
                    ticker=ticker,
                    outlet=outlet,
                    title=title,
                    url=link,
                    published_at=published,
                    text=f"{title}\n\n{summary}" if summary and summary != title else title,
                )
            )
        return items

    def enrich(self, item: SourceItem) -> SourceItem:
        return item


def google_query(
    config: GoogleNewsConfig, ticker: str, company: str | None, since: datetime
) -> str:
    """The Google News search text, limited to the days we need (`when:3d`)."""
    template = config.query if company else config.query_without_company
    days = max(1, math.ceil((utc_now() - since).total_seconds() / 86_400))
    return f"{template.format(ticker=ticker, company=company or ticker)} when:{days}d"


def outlet_from_url(url: str) -> str:
    """'https://www.reuters.com/x' -> 'Reuters'; unknown sites -> their address."""
    host = (urlsplit(url).hostname or "").lower().removeprefix("www.")
    for domain, outlet in _OUTLETS_BY_DOMAIN.items():
        if host == domain or host.endswith("." + domain):
            return outlet
    return host or "Unknown outlet"


def _strip_outlet_suffix(title: str, outlet: str) -> str:
    """Google appends ' - Outlet' to every headline."""
    suffix = f" - {outlet}"
    return title[: -len(suffix)].strip() if title.endswith(suffix) else title
