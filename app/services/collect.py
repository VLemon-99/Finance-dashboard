"""Collect research items for a ticker from every switched-on source and store the new ones.

- One failing source never stops the others; it's listed as missing instead (SPEC F4.11).
- The same URL is stored once per ticker. Extra downloads (`enrich`) happen for new items only.
- News headlines that are nearly the same, published close together, become one story
  (SPEC F4.12): the report shows it once with all of its sources.
"""

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from difflib import SequenceMatcher

from sqlmodel import Session, select

from app.config import ReportsConfig
from app.db.models import SourceItemRow
from app.sources.base import Source, SourceError, SourceItem
from app.sources.registry import SourceSet
from app.timeutils import as_utc, utc_now

log = logging.getLogger(__name__)


@dataclass
class CollectResult:
    ticker: str
    new_items: int = 0
    missing: list[str] = field(default_factory=list)  # "Google News: didn't answer in time."


def collect_for_ticker(
    session: Session,
    sources: SourceSet,
    ticker: str,
    since: datetime,
    config: ReportsConfig,
) -> CollectResult:
    result = CollectResult(ticker=ticker)
    company = _company_name(sources, ticker)
    for source in sources.sources:
        try:
            items = source.fetch(ticker, company, since)
        except SourceError as exc:
            result.missing.append(f"{source.label}: {exc}")
            continue
        except Exception:  # a bug or surprise in one source must not stop the report
            log.exception("Source %s failed unexpectedly for %s", source.name, ticker)
            result.missing.append(f"{source.label}: unexpected error (details in the app log).")
            continue
        result.new_items += _store_new(session, source, items, config, result)
    session.commit()
    return result


def _company_name(sources: SourceSet, ticker: str) -> str | None:
    """Company name for news searches. Without it, news is searched by ticker only."""
    if sources.edgar is None:
        return None
    try:
        return sources.edgar.company_name(ticker)
    except Exception as exc:
        log.info("No company name for %s: %s", ticker, exc)
        return None


def _store_new(
    session: Session,
    source: Source,
    items: list[SourceItem],
    config: ReportsConfig,
    result: CollectResult,
) -> int:
    stored = 0
    failed = 0
    seen: set[str] = set()
    for item in items:
        if item.url in seen or _exists(session, item.ticker, item.url):
            continue
        seen.add(item.url)
        try:
            item = source.enrich(item)
        except Exception as exc:
            # Not stored, so the next run tries again.
            if not isinstance(exc, SourceError):
                log.exception("Enriching %s failed unexpectedly", item.url)
            failed += 1
            continue
        row = _row_from_item(item)
        session.add(row)
        session.flush()  # gives the row its id
        row.story_id = _matching_story(session, row, config) or row.id
        stored += 1
    if failed:
        result.missing.append(
            f"{source.label}: {failed} item(s) couldn't be downloaded; will retry next time."
        )
    return stored


def _exists(session: Session, ticker: str, url: str) -> bool:
    query = select(SourceItemRow.id).where(SourceItemRow.ticker == ticker, SourceItemRow.url == url)
    return session.exec(query).first() is not None


def _row_from_item(item: SourceItem) -> SourceItemRow:
    return SourceItemRow(
        ticker=item.ticker,
        source=item.source,
        source_type=item.source_type,
        outlet=item.outlet[:200],
        title=item.title[:1000],
        url=item.url,
        published_at=item.published_at,
        form=item.form,
        text=item.text,
        data=item.data,
        fetched_at=utc_now(),
    )


# --- same story, several outlets (F4.12) --------------------------------------------


def normalize_headline(title: str) -> str:
    """'Apple Posts Record Quarter!' -> 'apple posts record quarter'."""
    return " ".join(re.sub(r"[^\w\s]", " ", title.casefold()).split())


def headline_similarity(a: str, b: str) -> float:
    """0 (nothing alike) to 1 (identical once case and punctuation are ignored)."""
    return SequenceMatcher(None, normalize_headline(a), normalize_headline(b)).ratio()


def _matching_story(session: Session, row: SourceItemRow, config: ReportsConfig) -> int | None:
    """The story id of an earlier news item with a near-identical headline, if any."""
    if row.source_type != "news":
        return None  # filings are never merged: each one is its own event
    window = timedelta(hours=config.duplicate_window_hours)
    published = as_utc(row.published_at)
    candidates = session.exec(
        select(SourceItemRow).where(
            SourceItemRow.ticker == row.ticker,
            SourceItemRow.source_type == "news",
            SourceItemRow.id != row.id,
            SourceItemRow.published_at >= published - window,
            SourceItemRow.published_at <= published + window,
        )
    ).all()
    best_id, best_score = None, config.duplicate_headline_similarity
    for other in candidates:
        score = headline_similarity(row.title, other.title)
        if score >= best_score:
            best_id, best_score = other.story_id, score
    return best_id
