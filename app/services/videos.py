"""Have the AI watch new YouTube videos (SPEC F4.5): once per video, ever.

Owner's rules (2026-11-04):
- A video is never watched twice. Its summary is kept with the stored item; when the same
  video turns up for another ticker, or again in a later run, the kept summary is reused.
- At most `ai.max_videos_per_run` new videos per run (default 10), newest first across the
  whole run. The rest wait for the next run (still within its 7 days, see
  reports.report_period).

Watching takes a while per video, so a few are watched at the same time
(`ai.parallel_videos`). Only the AI calls run in parallel; the database work stays here.

After watching, the item's text becomes the video's points plus its description. The usual
summarize step then rates it for the ticker, like any article (summarize.py).
"""

import logging
from collections import defaultdict
from collections.abc import Mapping
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlmodel import Session, col, select

from app.ai.llm import LLM, AIError, PublicVideo, VideoPoint, VideoUnavailable
from app.config import AIConfig
from app.db.models import SourceItemRow
from app.timeutils import as_utc, format_eastern, utc_now

log = logging.getLogger(__name__)

AI_VIDEO_NOTE = "AI video summaries:"


@dataclass
class VideoBudget:
    """New videos the AI may still watch in this run."""

    remaining: int


def is_video(row: SourceItemRow) -> bool:
    return row.source_type == "youtube"


def is_watched(row: SourceItemRow) -> bool:
    """Watched, or tried and found unwatchable: either way, never sent to the AI again."""
    data = row.data or {}
    return "points" in data or "watch_error" in data


def timestamp_label(seconds: int) -> str:
    """195 -> '3:15'; 3725 -> '1:02:05'."""
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes}:{secs:02d}"


def video_text(title: str, description: str, points: list[dict[str, Any]]) -> str:
    """What the summarize step reads for a watched video."""
    lines = [title, "", "What the video says:"]
    for p in points:
        when = f"[{timestamp_label(p['seconds'])}] " if p.get("seconds") is not None else ""
        lines.append(f"- {when}{p['text']}")
    if description:
        lines += ["", f"Description: {description}"]
    return "\n".join(lines)


def watch_new_videos(
    session: Session,
    llm: LLM,
    periods: Mapping[str, datetime],
    config: AIConfig,
    budget: VideoBudget,
) -> dict[str, list[str]]:
    """Watch the run's new videos. `periods`: ticker -> start of its report period.

    Returns notes for each ticker's "missing" list (videos left for later, AI failures).
    """
    by_url: dict[str, list[SourceItemRow]] = defaultdict(list)
    for row in unwatched_videos(session, periods):
        by_url[row.url].append(row)

    to_watch = []
    for url, rows in by_url.items():
        earlier = _earlier_answer(session, url)
        if earlier is not None:
            _apply(rows, earlier)  # watched before (another ticker, or an earlier run)
        else:
            to_watch.append(url)
    to_watch.sort(key=lambda url: as_utc(by_url[url][0].published_at), reverse=True)

    chosen, waiting = to_watch[: budget.remaining], to_watch[budget.remaining :]
    videos = [_public_video(by_url[url][0], config) for url in chosen]
    outcomes, failure = _watch_all(llm, videos, config.parallel_videos)
    budget.remaining -= len(outcomes)
    for url, outcome in outcomes.items():
        _apply(by_url[url], _answer(outcome, llm.model_name))
    session.commit()

    notes: dict[str, list[str]] = defaultdict(list)
    if failure is not None:
        for ticker in _tickers(by_url, [u for u in chosen if u not in outcomes]):
            notes[ticker].append(f"{AI_VIDEO_NOTE} {failure}")
    left: dict[str, int] = defaultdict(int)
    for url in waiting:
        for row in by_url[url]:
            left[row.ticker] += 1
    for ticker, count in left.items():
        notes[ticker].append(
            f"YouTube: {count} new video(s) will be watched in a later run (limit: "
            f"{config.max_videos_per_run} new videos per run, in config/settings.yaml)."
        )
    return dict(notes)


def unwatched_videos(session: Session, periods: Mapping[str, datetime]) -> list[SourceItemRow]:
    """Videos in these tickers' periods that the AI hasn't watched yet."""
    if not periods:
        return []
    rows = session.exec(
        select(SourceItemRow).where(
            SourceItemRow.source_type == "youtube",
            col(SourceItemRow.ticker).in_(list(periods)),
            col(SourceItemRow.summarized_at).is_(None),
        )
    ).all()
    return [r for r in rows if not is_watched(r) and as_utc(r.published_at) >= periods[r.ticker]]


def _earlier_answer(session: Session, url: str) -> dict[str, Any] | None:
    """The kept answer for this video from any ticker's item, if it was ever watched."""
    rows = session.exec(
        select(SourceItemRow).where(
            SourceItemRow.source_type == "youtube", SourceItemRow.url == url
        )
    ).all()
    for row in rows:
        if is_watched(row):
            keys = ("points", "watch_error", "watched_at", "watched_by")
            return {k: row.data[k] for k in keys if k in row.data}
    return None


def _public_video(row: SourceItemRow, config: AIConfig) -> PublicVideo:
    return PublicVideo(
        url=row.url,
        channel=row.outlet,
        title=row.title,
        published=format_eastern(as_utc(row.published_at)),
        max_seconds=config.max_video_minutes * 60,
    )


def _watch_all(
    llm: LLM, videos: list[PublicVideo], parallel: int
) -> tuple[dict[str, tuple[VideoPoint, ...] | VideoUnavailable], AIError | None]:
    """Watch several videos at once. Stops starting new ones if the AI service fails.

    Returns the answers by URL (points, or why that video can't be watched) and the
    service failure, if any. Videos without an answer stay unwatched for the next run.
    """
    outcomes: dict[str, tuple[VideoPoint, ...] | VideoUnavailable] = {}
    failure: AIError | None = None
    if not videos:
        return outcomes, failure
    with ThreadPoolExecutor(max_workers=min(parallel, len(videos))) as pool:
        futures = {pool.submit(llm.watch_video, video): video.url for video in videos}
        for future in as_completed(futures):
            url = futures[future]
            if future.cancelled():
                continue
            try:
                outcomes[url] = future.result()
            except VideoUnavailable as exc:
                outcomes[url] = exc
            except AIError as exc:
                # Out of credit, bad key, service down: more calls won't help.
                failure = failure or exc
                for other in futures:
                    other.cancel()
            except Exception:
                log.exception("Watching %s failed unexpectedly", url)
    return outcomes, failure


def _answer(outcome: tuple[VideoPoint, ...] | VideoUnavailable, model: str) -> dict[str, Any]:
    stamp = {"watched_at": utc_now().isoformat(), "watched_by": model}
    if isinstance(outcome, VideoUnavailable):
        return {"watch_error": str(outcome), **stamp}
    return {"points": [{"text": p.text, "seconds": p.seconds} for p in outcome], **stamp}


def _apply(rows: list[SourceItemRow], answer: dict[str, Any]) -> None:
    for row in rows:
        row.data = {**(row.data or {}), **answer}  # a new dict, so the change is saved
        if "points" in answer:
            row.text = video_text(row.title, row.data.get("description", ""), answer["points"])


def _tickers(by_url: Mapping[str, list[SourceItemRow]], urls: list[str]) -> list[str]:
    return sorted({row.ticker for url in urls for row in by_url[url]})
