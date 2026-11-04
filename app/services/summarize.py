"""Ask the AI to summarize and rate new research items (SPEC F4.6), and save the answers.

- Only one item per story is sent: the story's first item (`story_id == id`). The other
  outlets' copies of the same story are shown as extra sources, not summarized again.
- Form 4 insider filings aren't sent: their trades are read in code (see sources/edgar.py).
- YouTube videos are only sent once the AI has watched them (videos.py); until then they
  stay pending.
- Items are sent in batches to keep the number of AI calls low.
- Only public content leaves the app (SPEC F4.13): see `public_item`.
"""

import logging
from datetime import datetime

from sqlmodel import Session, col, select

from app.ai.llm import LLM, AIError, ItemKind, PublicItem
from app.config import AIConfig
from app.db.models import SourceItemRow
from app.services.videos import is_video, is_watched
from app.sources.edgar import base_form
from app.timeutils import as_utc, format_eastern, utc_now

log = logging.getLogger(__name__)


def is_form4(row: SourceItemRow) -> bool:
    return base_form(row.form or "") == "4"


def pending_items(session: Session, ticker: str, since: datetime) -> list[SourceItemRow]:
    """Story-leading items in the period that haven't been summarized yet."""
    rows = session.exec(
        select(SourceItemRow)
        .where(
            SourceItemRow.ticker == ticker,
            SourceItemRow.published_at >= since,
            col(SourceItemRow.summarized_at).is_(None),
            SourceItemRow.story_id == SourceItemRow.id,
        )
        .order_by(SourceItemRow.published_at)
    ).all()
    return [r for r in rows if not is_form4(r)]


def item_kind(source_type: str) -> ItemKind:
    """How an item is labelled for the AI and on report pages."""
    return {"filing": "filing", "blog": "blog", "youtube": "video"}.get(source_type, "news")


def public_item(row: SourceItemRow, max_chars: int) -> PublicItem:
    """The only fields of a stored item the AI ever sees: public content."""
    return PublicItem(
        ref=row.id,
        kind=item_kind(row.source_type),
        outlet=row.outlet,
        title=row.title,
        published=format_eastern(as_utc(row.published_at)),
        text=row.text[:max_chars],
    )


def batches(rows: list[SourceItemRow], config: AIConfig) -> list[list[SourceItemRow]]:
    """Groups of at most `max_items_per_call` items and about `max_chars_per_call` characters."""
    groups: list[list[SourceItemRow]] = []
    current: list[SourceItemRow] = []
    size = 0
    for row in rows:
        length = min(len(row.text), config.max_chars_per_call) + len(row.title)
        if current and (
            len(current) >= config.max_items_per_call or size + length > config.max_chars_per_call
        ):
            groups.append(current)
            current, size = [], 0
        current.append(row)
        size += length
    if current:
        groups.append(current)
    return groups


def summarize_pending(
    session: Session,
    llm: LLM,
    ticker: str,
    company: str | None,
    since: datetime,
    config: AIConfig,
) -> list[str]:
    """Summarize what's pending. Returns notes for the report's "missing" list (empty if fine)."""
    rows = [r for r in pending_items(session, ticker, since) if not is_video(r) or is_watched(r)]
    by_id = {r.id: r for r in rows}
    left_out = 0
    for group in batches(rows, config):
        items = [public_item(r, config.max_chars_per_call) for r in group]
        try:
            answers = llm.summarize_items(ticker, company, items)
        except AIError as exc:
            # Stop here: if the key is wrong or the limit is reached, more calls won't help.
            # What's left stays pending and is tried again next time.
            session.commit()
            return [f"AI summaries: {exc}"]
        now = utc_now()
        for answer in answers:
            row = by_id[answer.ref]
            row.summary = answer.summary
            row.sentiment = answer.sentiment
            row.sentiment_reason = answer.reason
            row.is_rumor = answer.is_rumor
        for row in group:
            # Items the AI skipped keep an empty summary: they're judged not about the
            # ticker, left out of reports, and not sent again.
            row.summarized_at = now
            row.ai_model = llm.model_name
        left_out += len(group) - len(answers)
    session.commit()
    if left_out:
        log.info(
            "AI skipped %d item(s) for %s (not about the ticker, or unusable)", left_out, ticker
        )
    return []
