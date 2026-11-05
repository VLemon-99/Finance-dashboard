"""The source plug-in interface. Each research source (news feeds, SEC EDGAR, blogs, YouTube,
X) is a small class with the same two methods, so the report pipeline can
treat them all alike and one broken source never stops the others (SPEC F4.11).

To add a source: write a class with `name`, `label`, `source_type`, `fetch()` and `enrich()`,
and list it in `app/sources/registry.py`.
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal, Protocol

# Source types decide the sentiment weight (SPEC F4.6).
SourceType = Literal["news", "filing", "blog", "youtube", "x"]


@dataclass(frozen=True)
class SourceItem:
    """One piece of public content about a ticker: an article, a filing, a blog post, a video."""

    source: str  # plug-in name, e.g. "google_news", "sec_edgar"
    source_type: SourceType
    ticker: str
    outlet: str  # who published it, e.g. "Reuters", "SEC EDGAR"
    title: str
    url: str  # unique: the same URL is only stored once
    published_at: datetime  # UTC
    text: str = ""  # public text the AI may read (snippet, or the full filing)
    form: str | None = None  # SEC form type, e.g. "8-K", "4"
    data: dict[str, Any] = field(default_factory=dict)  # structured facts, e.g. Form 4 trades


class SourceError(Exception):
    """A source failed: down, timed out, rate-limited, not set up, or sent something odd.

    The message is shown in the report's "missing sources" list, so keep it plain.
    """


# Downloads one URL and returns the body. Tests pass in a fake that reads recorded samples.
Fetch = Callable[[str], bytes]


class Source(Protocol):
    name: str  # stable id, e.g. "google_news"
    label: str  # shown on pages, e.g. "Google News"
    source_type: SourceType

    def fetch(self, ticker: str, company: str | None, since: datetime) -> list[SourceItem]:
        """Items about `ticker` published at or after `since` (UTC). Raises SourceError."""
        ...

    def enrich(self, item: SourceItem) -> SourceItem:
        """Fill in details that cost extra downloads (e.g. a filing's full text).

        Only called for items we haven't stored before. Raises SourceError.
        """
        ...


class LateSource(Source, Protocol):
    """A source that runs after every ticker of a run is collected (the X $TICKER search),
    so it only uses what the other sources left of a shared budget."""

    def order(self, tickers: list[str]) -> list[str]:
        """The order to go through the run's tickers (e.g. searched longest ago first)."""
        ...
