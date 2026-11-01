"""Ticker / company search (F1.1)."""

import logging
import re
from dataclasses import dataclass, field

from app.providers.market_data import MarketData, SearchResult

log = logging.getLogger(__name__)

MAX_QUERY_LENGTH = 50
# Letters, digits, spaces and the punctuation found in company names ("AT&T", "Berkshire
# Hathaway Inc.", "Moody's"). Anything else is rejected before any outside call.
_QUERY_RE = re.compile(r"^[A-Za-z0-9 .,&'\-]+$")


@dataclass(frozen=True)
class SearchOutcome:
    query: str
    results: list[SearchResult] = field(default_factory=list)
    note: str | None = None  # shown instead of / next to the results


def search_instruments(market_data: MarketData, raw_query: str, max_results: int) -> SearchOutcome:
    query = " ".join((raw_query or "").split())
    if not query:
        return SearchOutcome(query="")
    if len(query) > MAX_QUERY_LENGTH or not _QUERY_RE.fullmatch(query):
        return SearchOutcome(
            query=query,
            note=f"Search with a ticker or company name (letters and numbers, up to "
            f"{MAX_QUERY_LENGTH} characters).",
        )
    try:
        results = market_data.search(query, max_results)
    except Exception as exc:  # a failing search never breaks the page (F1.6)
        log.warning("Search failed for %r: %s", query, exc)
        return SearchOutcome(
            query=query,
            note="Search isn't available right now. You can still add a stock by typing "
            "its ticker below.",
        )
    if not results:
        return SearchOutcome(query=query, note=f'No US-listed stocks or ETFs match "{query}".')
    return SearchOutcome(query=query, results=results)
